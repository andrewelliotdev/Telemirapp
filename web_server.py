import os
import glob
import asyncio
import logging
import sys

from aiohttp import web

from settings_manager import settings_manager, APP_DIR, DATA_DIR, VALID_ENVIRONMENTS, env_still_set
from bot_engine import bot_engine, log_broadcaster
from cleaner import transform_text, should_process_message
from runtime_env import apply_runtime_environment, error_detail
from telegram_auth import telegram_login
import auth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("WebServer")

# Sections the generic "Save" button may change. Telegram credentials, the admin password and
# statistics are deliberately excluded: they only change through their own endpoints, so a stale
# browser tab can never overwrite a fresh login or wipe the counters.
EDITABLE_SECTIONS = ("bot", "channels", "customizations")

async def _read_json(request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}

def _result(result: dict) -> web.Response:
    return web.json_response(result, status=200 if result.get("success") else 400)

# ==============================================================================
# Middlewares
# ==============================================================================
@web.middleware
async def error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error on {request.method} {request.path}: {e}", exc_info=settings_manager.is_development())
        if request.path.startswith("/api/"):
            return web.json_response({"success": False, "error": error_detail(e)}, status=500)
        return web.Response(text="Internal server error", status=500)

@web.middleware
async def security_headers_middleware(request, handler):
    response = await handler(request)
    if not isinstance(response, web.WebSocketResponse):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
    return response

# ==============================================================================
# Page & health
# ==============================================================================
async def handle_index(request):
    html_file = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    return web.Response(text="<h1>Admin Dashboard template not found.</h1>", content_type="text/html")

async def handle_healthz(request):
    """Health check endpoint for Render and uptime monitors (public, reveals nothing sensitive)."""
    return web.json_response({"status": "healthy", "service": "TeleMirror Pro"})

# ==============================================================================
# Bot engine controls
# ==============================================================================
def _status_payload() -> dict:
    data = bot_engine.get_status()
    data["login_step"] = telegram_login.step
    return data

async def handle_get_status(request):
    return web.json_response(_status_payload())

async def handle_bot_start(request):
    if not settings_manager.is_telegram_configured():
        return web.json_response({
            "success": False,
            "status": bot_engine.status,
            "error": "Connect a Telegram account first (Settings → Telegram Login).",
            "status_data": _status_payload()
        })
    success = await bot_engine.start()
    return web.json_response({
        "success": success,
        "status": bot_engine.status,
        "error": bot_engine.error_message,
        "status_data": _status_payload()
    })

async def handle_bot_stop(request):
    success = await bot_engine.stop()
    return web.json_response({
        "success": success,
        "status": bot_engine.status,
        "status_data": _status_payload()
    })

async def handle_bot_restart(request):
    if not settings_manager.is_telegram_configured():
        return web.json_response({
            "success": False,
            "status": bot_engine.status,
            "error": "Connect a Telegram account first (Settings → Telegram Login).",
            "status_data": _status_payload()
        })
    success = await bot_engine.restart()
    return web.json_response({
        "success": success,
        "status": bot_engine.status,
        "error": bot_engine.error_message,
        "status_data": _status_payload()
    })

# ==============================================================================
# Settings
# ==============================================================================
async def handle_get_settings(request):
    return web.json_response(settings_manager.get_public())

async def handle_save_settings(request):
    try:
        data = await _read_json(request)
        updates = {}
        for section in EDITABLE_SECTIONS:
            if isinstance(data.get(section), dict):
                updates[section] = data[section]

        environment_changed = False
        app_in = data.get("app")
        if isinstance(app_in, dict) and "environment" in app_in:
            environment = str(app_in["environment"]).strip().lower()
            if environment not in VALID_ENVIRONMENTS:
                return web.json_response(
                    {"success": False, "error": "Environment must be 'production' or 'development'."}, status=400)
            environment_changed = environment != settings_manager.get_environment()
            updates["app"] = {"environment": environment}

        settings_manager.merge_sections(updates)
        if environment_changed:
            apply_runtime_environment()
        logger.info("⚙️ Settings saved.")

        # Apply channel changes to a running engine immediately.
        if bot_engine.status == "running" and bot_engine.client:
            asyncio.create_task(bot_engine._resolve_channels())

        return web.json_response({
            "success": True,
            "message": "Settings saved and applied.",
            "environment": settings_manager.get_environment(),
        })
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        return web.json_response({"success": False, "error": error_detail(e)}, status=400)

async def handle_get_session_string(request):
    session_str = bot_engine.export_session_string()
    if session_str:
        return web.json_response({
            "success": True,
            "session_string": session_str,
            "message": "Copy this SESSION_STRING into your Render Environment Variables."
        })
    return web.json_response({
        "success": False,
        "error": "No logged-in account to export yet. Log in from Settings → Telegram Login first."
    }, status=404)

async def handle_get_render_env(request):
    """Environment variables formatted for the Render dashboard (requires dashboard login)."""
    env_dict = settings_manager.generate_render_env_dict()
    env_lines = [f"{k}={v}" for k, v in env_dict.items() if v]
    return web.json_response({
        "success": True,
        "env_dict": env_dict,
        "env_text": "\n".join(env_lines)
    })

# ==============================================================================
# Telegram account login / logout
# ==============================================================================
async def handle_tg_send_code(request):
    data = await _read_json(request)
    return _result(await telegram_login.send_code(data.get("api_id"), data.get("api_hash"), data.get("phone")))

async def handle_tg_verify_code(request):
    data = await _read_json(request)
    return _result(await telegram_login.verify_code(data.get("code")))

async def handle_tg_verify_password(request):
    data = await _read_json(request)
    return _result(await telegram_login.verify_password(data.get("password")))

async def handle_tg_cancel(request):
    await telegram_login.cancel()
    return web.json_response({"success": True, "step": "idle"})

async def handle_tg_bot_token(request):
    data = await _read_json(request)
    return _result(await telegram_login.connect_bot(data.get("api_id"), data.get("api_hash"), data.get("bot_token")))

async def handle_tg_logout(request):
    data = await _read_json(request)
    await telegram_login.cancel()
    result = await bot_engine.disconnect_account(terminate=bool(data.get("terminate", True)))
    return web.json_response({"success": True, **result, "env_still_set": env_still_set()})

# ==============================================================================
# Reset & logs
# ==============================================================================
def _purge_local_files() -> list:
    """Deletes log files and any leftover Telethon .session files. Returns the file names removed."""
    patterns = ["mirror_bot.log", "*.log", "*.session", "*.session-journal"]
    removed = []
    for directory in {APP_DIR, DATA_DIR}:
        for pattern in patterns:
            for path in glob.glob(os.path.join(directory, pattern)):
                try:
                    os.remove(path)
                    removed.append(os.path.basename(path))
                except OSError:
                    pass
    return sorted(set(removed))

async def handle_reset(request):
    data = await _read_json(request)
    if str(data.get("confirm", "")).strip().upper() != "RESET":
        return web.json_response({"success": False, "error": "Type RESET to confirm."}, status=400)

    await telegram_login.cancel()
    outcome = {"terminated": False, "note": None}
    if data.get("terminate_session", True):
        outcome = await bot_engine.disconnect_account(terminate=True)
    else:
        await bot_engine.stop()
        bot_engine.clear_runtime_state()

    settings_manager.reset_to_fresh(keep_admin=True)
    removed = _purge_local_files()
    log_broadcaster.logs.clear()
    apply_runtime_environment()
    logger.info("🧹 App reset to a fresh state.")
    return web.json_response({
        "success": True,
        "removed_files": removed,
        "env_still_set": env_still_set(),
        **outcome,
    })

async def handle_logs_clear(request):
    log_broadcaster.logs.clear()
    _purge_local_files()
    return web.json_response({"success": True})

# ==============================================================================
# Channel discovery & simulator
# ==============================================================================
async def handle_get_dialogs(request):
    if bot_engine.status != "running":
        return web.json_response({
            "success": False,
            "error": "Bot engine must be running to discover Telegram channels."
        }, status=400)

    dialogs = await bot_engine.fetch_user_dialogs()
    return web.json_response({"success": True, "dialogs": dialogs})

async def handle_preview_transform(request):
    try:
        data = await _read_json(request)
        text = data.get("text", "")
        custom_settings = data.get("customizations", None)

        allowed, reason = should_process_message(text, custom_settings)
        result_text = transform_text(text, custom_settings)

        remove_images = bool(custom_settings.get("remove_images", False)) if custom_settings else False

        return web.json_response({
            "allowed": allowed,
            "filter_reason": reason,
            "original": text,
            "transformed": result_text,
            "remove_images": remove_images
        })
    except Exception as e:
        return web.json_response({"error": error_detail(e)}, status=400)

# ==============================================================================
# WebSocket for Live Logs
# ==============================================================================
async def handle_ws_logs(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    queue = asyncio.Queue(maxsize=150)
    log_broadcaster.subscribe(queue)

    # Send historical logs first
    for log in list(log_broadcaster.logs):
        await ws.send_json(log)

    try:
        while not ws.closed:
            try:
                log_entry = await asyncio.wait_for(queue.get(), timeout=2.0)
                await ws.send_json(log_entry)
            except asyncio.TimeoutError:
                await ws.ping()
    except Exception:
        pass
    finally:
        log_broadcaster.unsubscribe(queue)
        await ws.close()

    return ws

# ==============================================================================
# Application Factory
# ==============================================================================
def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware, security_headers_middleware, auth.auth_middleware])

    # Public: page shell, health check, auth bootstrap
    app.router.add_get("/", handle_index)
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/api/health", handle_healthz)
    app.router.add_get("/api/auth/state", auth.handle_auth_state)
    app.router.add_post("/api/auth/setup", auth.handle_auth_setup)
    app.router.add_post("/api/auth/login", auth.handle_auth_login)

    # Everything below requires a dashboard session
    app.router.add_post("/api/auth/logout", auth.handle_auth_logout)
    app.router.add_post("/api/auth/password", auth.handle_auth_change_password)

    app.router.add_get("/api/status", handle_get_status)
    app.router.add_post("/api/bot/start", handle_bot_start)
    app.router.add_post("/api/bot/stop", handle_bot_stop)
    app.router.add_post("/api/bot/restart", handle_bot_restart)

    app.router.add_get("/api/settings", handle_get_settings)
    app.router.add_post("/api/settings", handle_save_settings)
    app.router.add_get("/api/session-string", handle_get_session_string)
    app.router.add_get("/api/render-env", handle_get_render_env)

    app.router.add_post("/api/telegram/send-code", handle_tg_send_code)
    app.router.add_post("/api/telegram/verify-code", handle_tg_verify_code)
    app.router.add_post("/api/telegram/verify-password", handle_tg_verify_password)
    app.router.add_post("/api/telegram/cancel", handle_tg_cancel)
    app.router.add_post("/api/telegram/bot-token", handle_tg_bot_token)
    app.router.add_post("/api/telegram/logout", handle_tg_logout)

    app.router.add_post("/api/reset", handle_reset)
    app.router.add_post("/api/logs/clear", handle_logs_clear)

    app.router.add_get("/api/dialogs", handle_get_dialogs)
    app.router.add_post("/api/preview", handle_preview_transform)
    app.router.add_get("/ws/logs", handle_ws_logs)

    # Static assets
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.router.add_static("/static/", path=static_dir, name="static")

    return app

async def start_web_server():
    web_cfg = settings_manager.get("web", {})
    host = os.getenv("WEB_HOST", web_cfg.get("host", "0.0.0.0"))
    port = int(os.getenv("PORT", web_cfg.get("port", 8080)))

    apply_runtime_environment()

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"🌐 Web Admin Dashboard running at http://{host}:{port}")
    if not auth.password_is_set():
        logger.info("🔐 No dashboard password yet - the first visitor will be asked to create one.")

    # Auto-start the engine if an account is already connected (quiet no-op on a fresh install)
    if settings_manager.get("bot", {}).get("auto_start", True):
        asyncio.create_task(bot_engine.start())

    # Keep server running
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(start_web_server())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Web Admin server stopped.")
