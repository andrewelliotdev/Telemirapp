"""
Interactive Telegram login for the dashboard.

Flow:  API ID + API Hash + phone  ->  login code (OTP)  ->  2FA password (only if enabled)
On success the session is stored (as a Telethon StringSession) and the bot engine reconnects
as that account, so whichever account logs in is the one the app uses.
"""
import re
import asyncio
import logging
from typing import Any, Dict, Optional

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from settings_manager import settings_manager
from bot_engine import bot_engine
from runtime_env import error_detail

logger = logging.getLogger("TelegramAuth")

PENDING_TTL = 600  # seconds a half-finished login is kept before being discarded

def normalize_phone(raw: str) -> str:
    cleaned = re.sub(r"[\s\-().]", "", str(raw or ""))
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    if not re.fullmatch(r"\+\d{7,15}", cleaned):
        raise ValueError("Enter your phone number in international format, e.g. +14155550123.")
    return cleaned

def _delivery_label(sent: Any) -> str:
    name = type(getattr(sent, "type", None)).__name__
    return {
        "SentCodeTypeApp": "your Telegram app",
        "SentCodeTypeSms": "SMS",
        "SentCodeTypeCall": "a phone call",
        "SentCodeTypeFlashCall": "a flash call",
    }.get(name, "Telegram")

def explain_error(exc: Exception) -> str:
    if isinstance(exc, errors.ApiIdInvalidError):
        return "Telegram rejected this API ID / API Hash. Re-check them at my.telegram.org."
    if isinstance(exc, errors.PhoneNumberInvalidError):
        return "That phone number isn't valid. Use international format, e.g. +14155550123."
    if isinstance(exc, errors.PhoneNumberBannedError):
        return "This phone number has been banned by Telegram."
    if isinstance(exc, errors.PhoneNumberUnoccupiedError):
        return "This number isn't registered on Telegram."
    if isinstance(exc, errors.PhoneNumberFloodError):
        return "Too many attempts for this number. Wait a while and try again."
    if isinstance(exc, errors.FloodWaitError):
        return f"Telegram asked you to wait {exc.seconds} seconds before trying again."
    if isinstance(exc, errors.PhoneCodeInvalidError):
        return "That code is incorrect. Check it and try again."
    if isinstance(exc, errors.PhoneCodeExpiredError):
        return "That code has expired. Request a new one."
    if isinstance(exc, errors.PhoneCodeEmptyError):
        return "Enter the login code."
    if isinstance(exc, errors.PasswordHashInvalidError):
        return "Incorrect two-step verification password."
    if isinstance(exc, getattr(errors, "AccessTokenInvalidError", ())):
        return "That bot token is invalid. Copy it again from @BotFather."
    if isinstance(exc, (ConnectionError, OSError, asyncio.TimeoutError)):
        return "Could not reach Telegram. Check the connection and try again."
    logger.error(f"Unexpected Telegram login error: {type(exc).__name__}: {exc}")
    return error_detail(exc, "Telegram login failed. Please try again.")

class TelegramLoginFlow:
    def __init__(self):
        self._client: Optional[TelegramClient] = None
        self._api_id = ""
        self._api_hash = ""
        self._phone = ""
        self._code_hash = ""
        self._needs_password = False
        self._expiry_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ helpers
    @property
    def step(self) -> str:
        if not self._client:
            return "idle"
        return "password" if self._needs_password else "code"

    def _fail(self, message: str, step: Optional[str] = None) -> Dict[str, Any]:
        return {"success": False, "error": message, "step": step or self.step}

    async def _discard_locked(self, cancel_expiry: bool = True):
        if cancel_expiry and self._expiry_task:
            self._expiry_task.cancel()
        self._expiry_task = None
        client, self._client = self._client, None
        self._needs_password = False
        self._code_hash = ""
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    def _schedule_expiry(self):
        if self._expiry_task:
            self._expiry_task.cancel()
        self._expiry_task = asyncio.get_running_loop().create_task(self._expire_later())

    async def _expire_later(self):
        try:
            await asyncio.sleep(PENDING_TTL)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self._client is not None:
                logger.info("⌛ Unfinished Telegram login expired and was discarded.")
            await self._discard_locked(cancel_expiry=False)

    @staticmethod
    def _account_in_use() -> bool:
        tg = settings_manager.get("telegram", {})
        return bool(str(tg.get("session_string", "")).strip() or str(tg.get("bot_token", "")).strip())

    # ------------------------------------------------------------------ public API
    async def cancel(self):
        async with self._lock:
            await self._discard_locked()

    async def send_code(self, api_id: Any, api_hash: Any, phone: Any) -> Dict[str, Any]:
        async with self._lock:
            if self._account_in_use():
                return self._fail("An account is already connected. Disconnect it first to log in with a different one.", "idle")

            stored = settings_manager.get("telegram", {})
            api_id = str(api_id or "").strip() or str(stored.get("api_id", "")).strip()
            api_hash = str(api_hash or "").strip() or str(stored.get("api_hash", "")).strip()
            try:
                if not api_id.isdigit():
                    raise ValueError("API ID must be a number. Get it at my.telegram.org → API development tools.")
                if not re.fullmatch(r"\w{16,64}", api_hash):
                    raise ValueError("API Hash looks invalid. Copy the full hash from my.telegram.org.")
                phone_n = normalize_phone(phone)
            except ValueError as e:
                return self._fail(str(e), "idle")

            await self._discard_locked()
            client = TelegramClient(StringSession(), int(api_id), api_hash,
                                    device_model="TeleMirror Pro", app_version="1.0")
            try:
                await client.connect()
                sent = await client.send_code_request(phone_n)
            except Exception as e:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                return self._fail(explain_error(e), "idle")

            self._client = client
            self._api_id, self._api_hash, self._phone = api_id, api_hash, phone_n
            self._code_hash = sent.phone_code_hash
            self._needs_password = False
            self._schedule_expiry()
            # Remember what was typed so a page refresh keeps the form filled in.
            settings_manager.update_section("telegram", {
                "api_id": api_id, "api_hash": api_hash, "phone_number": phone_n,
            })
            logger.info("📲 Telegram login code requested.")
            return {"success": True, "step": "code", "phone": phone_n, "delivery": _delivery_label(sent)}

    async def verify_code(self, code: Any) -> Dict[str, Any]:
        async with self._lock:
            if not self._client:
                return self._fail("Your login session expired. Request a new code.", "idle")
            digits = re.sub(r"\D", "", str(code or ""))
            if not digits:
                return self._fail("Enter the login code.")
            try:
                await self._client.sign_in(phone=self._phone, code=digits, phone_code_hash=self._code_hash)
            except errors.SessionPasswordNeededError:
                self._needs_password = True
                return {"success": True, "step": "password"}
            except errors.PhoneCodeExpiredError as e:
                await self._discard_locked()
                return self._fail(explain_error(e), "idle")
            except Exception as e:
                return self._fail(explain_error(e))  # stays pending so the code can be retyped
            return await self._finalize_locked()

    async def verify_password(self, password: Any) -> Dict[str, Any]:
        async with self._lock:
            if not self._client or not self._needs_password:
                return self._fail("Your login session expired. Request a new code.", "idle")
            password = str(password or "")
            if not password:
                return self._fail("Enter your two-step verification password.")
            try:
                await self._client.sign_in(password=password)
            except Exception as e:
                return self._fail(explain_error(e))
            return await self._finalize_locked()

    async def _finalize_locked(self) -> Dict[str, Any]:
        client = self._client
        api_id, api_hash, phone = self._api_id, self._api_hash, self._phone
        try:
            me = await client.get_me()
            session_string = client.session.save()
        except Exception as e:
            await self._discard_locked()
            return self._fail(explain_error(e), "idle")
        await self._discard_locked()

        # Make this account the one the app uses.
        await bot_engine.stop()
        bot_engine.clear_runtime_state()
        account = {
            "id": me.id,
            "first_name": me.first_name,
            "username": me.username,
            "phone": getattr(me, "phone", None) or phone.lstrip("+"),
            "is_bot": False,
        }
        settings_manager.update_section("telegram", {
            "api_id": api_id,
            "api_hash": api_hash,
            "phone_number": phone,
            "session_string": session_string,
            "bot_token": "",
            "account": account,
        })
        logger.info(f"✅ Telegram login successful: {me.first_name} (@{me.username or 'NoUsername'})")

        started = await bot_engine.start()
        return {
            "success": True,
            "step": "done",
            "account": account,
            "engine_started": started,
            "engine_status": bot_engine.status,
            "engine_error": bot_engine.error_message,
        }

    async def connect_bot(self, api_id: Any, api_hash: Any, bot_token: Any) -> Dict[str, Any]:
        """Alternative to account login: connect a @BotFather bot token (no OTP needed)."""
        async with self._lock:
            if self._account_in_use():
                return self._fail("An account is already connected. Disconnect it first.", "idle")

            stored = settings_manager.get("telegram", {})
            api_id = str(api_id or "").strip() or str(stored.get("api_id", "")).strip()
            api_hash = str(api_hash or "").strip() or str(stored.get("api_hash", "")).strip()
            token = str(bot_token or "").strip()
            if not api_id.isdigit():
                return self._fail("API ID must be a number. Get it at my.telegram.org.", "idle")
            if not re.fullmatch(r"\w{16,64}", api_hash):
                return self._fail("API Hash looks invalid. Copy the full hash from my.telegram.org.", "idle")
            if not re.fullmatch(r"\d{5,}:[\w-]{20,}", token):
                return self._fail("That doesn't look like a bot token. It should look like 123456789:ABC-def...", "idle")

            client = TelegramClient(StringSession(), int(api_id), api_hash)
            try:
                await client.start(bot_token=token)
                me = await client.get_me()
            except Exception as e:
                return self._fail(explain_error(e), "idle")
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            await bot_engine.stop()
            bot_engine.clear_runtime_state()
            account = {"id": me.id, "first_name": me.first_name, "username": me.username, "phone": None, "is_bot": True}
            settings_manager.update_section("telegram", {
                "api_id": api_id, "api_hash": api_hash, "bot_token": token,
                "session_string": "", "phone_number": "", "account": account,
            })
            logger.info(f"✅ Bot token connected: {me.first_name} (@{me.username or 'NoUsername'})")
            started = await bot_engine.start()
            return {
                "success": True, "step": "done", "account": account,
                "engine_started": started, "engine_status": bot_engine.status,
                "engine_error": bot_engine.error_message,
            }

telegram_login = TelegramLoginFlow()
