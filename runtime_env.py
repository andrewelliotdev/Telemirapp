"""Applies the production / development mode to the running process."""
import logging

from settings_manager import settings_manager

def apply_runtime_environment() -> str:
    """
    production  -> INFO logs, quiet third-party libraries, generic API error messages.
    development -> DEBUG app logs, Telethon/aiohttp INFO, detailed API error messages.
    Returns the active environment name.
    """
    env = settings_manager.get_environment()
    dev = env == "development"

    logging.getLogger().setLevel(logging.DEBUG if dev else logging.INFO)
    logging.getLogger("telethon").setLevel(logging.INFO if dev else logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.INFO if dev else logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.INFO if dev else logging.WARNING)

    logging.getLogger("Runtime").info(f"🌍 Environment: {env.upper()}")
    return env

def error_detail(exc: Exception, fallback: str = "Something went wrong. Check the server logs.") -> str:
    """Exception text is only exposed to the browser in development mode."""
    return str(exc) if settings_manager.is_development() else fallback
