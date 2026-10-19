"""Admin authentication for the web dashboard (cookie sessions, PBKDF2 password hashing)."""
import os
import hmac
import time
import hashlib
import secrets
from typing import Dict, List, Optional

from aiohttp import web

from settings_manager import settings_manager

SESSION_COOKIE = "telemirror_session"
SESSION_TTL = 7 * 24 * 3600
MIN_PASSWORD_LENGTH = 8
PBKDF2_ROUNDS = 200_000

# Routes reachable without a dashboard session.
PUBLIC_PATHS = {
    "/", "/healthz", "/api/health",
    "/api/auth/state", "/api/auth/login", "/api/auth/setup",
}

_sessions: Dict[str, float] = {}            # token -> expiry timestamp
_failed_logins: Dict[str, List[float]] = {}  # ip -> failure timestamps
MAX_FAILURES = 5
FAILURE_WINDOW = 300

# ---------------------------------------------------------------------- passwords
def env_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "").strip()

def stored_hash() -> str:
    return settings_manager.get("web", {}).get("admin_password_hash", "") or ""

def password_is_set() -> bool:
    return bool(env_password() or stored_hash())

def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"

def _verify_hash(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = encoded.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False

def verify_password(password: str) -> bool:
    env_pw = env_password()
    if env_pw:  # ADMIN_PASSWORD from the host environment is authoritative when present
        return hmac.compare_digest(password.encode("utf-8"), env_pw.encode("utf-8"))
    encoded = stored_hash()
    return bool(encoded) and _verify_hash(password, encoded)

def set_password(password: str):
    settings_manager.update_section("web", {"admin_password_hash": hash_password(password)})

# ---------------------------------------------------------------------- sessions
def _purge_expired():
    now = time.time()
    for token in [t for t, exp in _sessions.items() if exp < now]:
        _sessions.pop(token, None)

def create_session() -> str:
    _purge_expired()
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL
    return token

def is_authenticated(request: web.Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    expiry = _sessions.get(token)
    if not expiry:
        return False
    if expiry < time.time():
        _sessions.pop(token, None)
        return False
    return True

def drop_session(request: web.Request):
    _sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)

def clear_all_sessions():
    _sessions.clear()

def _client_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() if forwarded else (request.remote or "unknown")

def _is_https(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"

def _rate_limited(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failed_logins.get(ip, []) if now - t < FAILURE_WINDOW]
    _failed_logins[ip] = recent
    return len(recent) >= MAX_FAILURES

def _record_failure(ip: str):
    _failed_logins.setdefault(ip, []).append(time.time())

def _login_response(request: web.Request, payload: Optional[dict] = None) -> web.Response:
    response = web.json_response({"success": True, **(payload or {})})
    response.set_cookie(
        SESSION_COOKIE, create_session(),
        max_age=SESSION_TTL, httponly=True, samesite="Lax",
        secure=_is_https(request), path="/",
    )
    return response

# ---------------------------------------------------------------------- middleware
@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS or path.startswith("/static/"):
        return await handler(request)
    if is_authenticated(request):
        return await handler(request)
    return web.json_response({"success": False, "error": "auth_required"}, status=401)

# ---------------------------------------------------------------------- handlers
async def handle_auth_state(request: web.Request):
    return web.json_response({
        "authenticated": is_authenticated(request),
        "password_set": password_is_set(),
        "setup_required": not password_is_set(),
        "env_managed": bool(env_password()),
    })

async def handle_auth_setup(request: web.Request):
    """First-run: choose the dashboard password (only possible while none exists)."""
    if password_is_set():
        return web.json_response({"success": False, "error": "A password is already set."}, status=409)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid request."}, status=400)
    password = str(data.get("password", ""))
    if len(password) < MIN_PASSWORD_LENGTH:
        return web.json_response(
            {"success": False, "error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters."}, status=400)
    set_password(password)
    return _login_response(request)

async def handle_auth_login(request: web.Request):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return web.json_response(
            {"success": False, "error": "Too many failed attempts. Try again in a few minutes."}, status=429)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid request."}, status=400)
    if not password_is_set():
        return web.json_response({"success": False, "error": "setup_required"}, status=409)
    if not verify_password(str(data.get("password", ""))):
        _record_failure(ip)
        return web.json_response({"success": False, "error": "Incorrect password."}, status=401)
    _failed_logins.pop(ip, None)
    return _login_response(request)

async def handle_auth_logout(request: web.Request):
    drop_session(request)
    response = web.json_response({"success": True})
    response.del_cookie(SESSION_COOKIE, path="/")
    return response

async def handle_auth_change_password(request: web.Request):
    if env_password():
        return web.json_response({
            "success": False,
            "error": "The password is managed by the ADMIN_PASSWORD environment variable. Change it in your host's Environment settings.",
        }, status=409)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid request."}, status=400)
    if not verify_password(str(data.get("current", ""))):
        return web.json_response({"success": False, "error": "Current password is incorrect."}, status=401)
    new_password = str(data.get("new", ""))
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return web.json_response(
            {"success": False, "error": f"New password must be at least {MIN_PASSWORD_LENGTH} characters."}, status=400)
    set_password(new_password)
    clear_all_sessions()
    return _login_response(request)  # keep this browser signed in, sign everyone else out
