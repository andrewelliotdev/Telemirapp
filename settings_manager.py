import os
import copy
import json
import logging
import threading
from typing import Dict, Any, List
from dotenv import load_dotenv

# Load .env if present (local development convenience only)
load_dotenv()

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR lets you point app state at a persistent disk (e.g. a Render Disk mounted at /data).
DATA_DIR = os.getenv("DATA_DIR", "").strip() or APP_DIR
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

VALID_ENVIRONMENTS = ("production", "development")

# Environment variables that only *seed* empty settings (the app's own stored values win).
SEED_ENV_VARS = (
    "API_ID", "API_HASH", "PHONE_NUMBER", "SESSION_STRING", "BOT_TOKEN",
    "SOURCE_CHANNELS", "DESTINATION_CHANNELS",
)

def get_env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")

def get_env_list(key: str) -> List[str]:
    val = os.getenv(key, "")
    if not val:
        return []
    items = []
    for s in val.split(","):
        cleaned = s.strip().strip("\"'")
        if cleaned:
            items.append(cleaned)
    return items

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "").strip() or default)
    except ValueError:
        return default

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        return default

def env_still_set() -> List[str]:
    """Names (never values) of seeding env vars that are still defined in the runtime environment."""
    return [k for k in SEED_ENV_VARS if os.getenv(k, "").strip()]

def _is_set(value: Any) -> bool:
    """True when a stored value carries information (False/0 count as set)."""
    return value not in (None, "", [], {})

def build_defaults(seed_from_env: bool = True) -> Dict[str, Any]:
    """
    Builds a settings tree. With seed_from_env=True, credentials/channels are pre-filled from
    environment variables (first boot). With False the tree is completely blank (fresh app).
    """
    def e(key: str, default: str = "") -> str:
        return os.getenv(key, default).strip() if seed_from_env else default

    environment = os.getenv("APP_ENV", "").strip().lower()
    if environment not in VALID_ENVIRONMENTS:
        environment = "production"

    return {
        "app": {
            "environment": environment,
        },
        "telegram": {
            "api_id": e("API_ID"),
            "api_hash": e("API_HASH"),
            "bot_token": e("BOT_TOKEN"),
            "phone_number": e("PHONE_NUMBER"),
            "session_string": e("SESSION_STRING"),
            "account": {},
        },
        "web": {
            "host": os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0",
            "port": _env_int("PORT", _env_int("WEB_PORT", 8080)),
            "admin_password_hash": "",
        },
        "bot": {
            "copy_mode": os.getenv("COPY_MODE", "clean_copy").strip() or "clean_copy",
            "action_delay": _env_float("ACTION_DELAY", 2.0),
            "mirror_edits": get_env_bool("MIRROR_EDITS", True),
            "auto_start": get_env_bool("AUTO_START", True),
            "remove_images": get_env_bool("REMOVE_IMAGES", False),
        },
        "channels": {
            "sources": get_env_list("SOURCE_CHANNELS") if seed_from_env else [],
            "destinations": get_env_list("DESTINATION_CHANNELS") if seed_from_env else [],
            "mappings": {},
        },
        "customizations": {
            "replace_all_urls": False,
            "global_replacement_url": e("GLOBAL_REPLACEMENT_URL"),
            "url_rules": [],
            "replace_all_usernames": False,
            "global_replacement_username": e("GLOBAL_REPLACEMENT_USERNAME"),
            "username_rules": [],
            "word_rules": [],
            "remove_all_urls": get_env_bool("REMOVE_LINKS", False) if seed_from_env else False,
            "remove_all_usernames": False,
            "remove_images": get_env_bool("REMOVE_IMAGES", False),
            "header": os.getenv("CUSTOM_HEADER", "") if seed_from_env else "",
            "footer": os.getenv("CUSTOM_FOOTER", "") if seed_from_env else "",
            "blacklist_keywords": get_env_list("BLACKLIST_KEYWORDS") if seed_from_env else [],
            "whitelist_keywords": get_env_list("WHITELIST_KEYWORDS") if seed_from_env else [],
        },
        "stats": {
            "total_messages_processed": 0,
            "total_messages_mirrored": 0,
            "total_messages_skipped": 0,
            "last_mirrored_at": None,
        },
    }

# Kept for backwards compatibility with older imports.
DEFAULT_SETTINGS: Dict[str, Any] = build_defaults(seed_from_env=True)

class SettingsManager:
    def __init__(self, filepath: str = SETTINGS_FILE):
        self.filepath = filepath
        self._lock = threading.RLock()
        self._settings = self._load()

    # ------------------------------------------------------------------ load/save
    def _load(self) -> Dict[str, Any]:
        stored: Dict[str, Any] = {}
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        stored = loaded
            except Exception as e:
                logging.error(f"Failed to load settings from {self.filepath}: {e}")

        # Legacy plaintext admin password is never kept.
        if isinstance(stored.get("web"), dict):
            stored["web"].pop("admin_password", None)

        merged = build_defaults(seed_from_env=True)
        for section, values in stored.items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                for key, value in values.items():
                    # Stored value wins when it carries information; otherwise the
                    # env-seeded / default value stays (lets env vars fill empty fields).
                    if _is_set(value) or not _is_set(merged[section].get(key)):
                        merged[section][key] = value
            else:
                merged[section] = values

        if merged["app"].get("environment") not in VALID_ENVIRONMENTS:
            merged["app"]["environment"] = "production"

        self._save(merged)
        return merged

    def _save(self, data: Dict[str, Any]):
        try:
            directory = os.path.dirname(self.filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = f"{self.filepath}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            os.replace(tmp_path, self.filepath)
        except Exception as e:
            logging.error(f"Failed to save settings to {self.filepath}: {e}")

    # ------------------------------------------------------------------ views
    def get_all(self) -> Dict[str, Any]:
        return self._settings

    def get(self, section: str, default=None):
        return self._settings.get(section, default)

    def get_environment(self) -> str:
        env = self._settings.get("app", {}).get("environment", "production")
        return env if env in VALID_ENVIRONMENTS else "production"

    def is_development(self) -> bool:
        return self.get_environment() == "development"

    def get_public(self) -> Dict[str, Any]:
        """Settings safe to send to the browser: no API hash, session, bot token or admin hash."""
        with self._lock:
            data = copy.deepcopy(self._settings)
        tg = data.get("telegram", {})
        data["telegram"] = {
            "api_id": tg.get("api_id", ""),
            "phone_number": tg.get("phone_number", ""),
            "has_api_hash": bool(tg.get("api_hash")),
            "has_session": bool(tg.get("session_string")),
            "has_bot_token": bool(tg.get("bot_token")),
            "account": tg.get("account", {}),
        }
        data.pop("web", None)
        return data

    def is_telegram_configured(self) -> bool:
        tg = self._settings.get("telegram", {})
        return bool(
            str(tg.get("api_id", "")).strip()
            and str(tg.get("api_hash", "")).strip()
            and (str(tg.get("session_string", "")).strip() or str(tg.get("bot_token", "")).strip())
        )

    def generate_render_env_dict(self) -> Dict[str, str]:
        """Key/value pairs formatted for the Render Environment tab (ADMIN_PASSWORD is never exported)."""
        tg = self._settings.get("telegram", {})
        web = self._settings.get("web", {})
        bot = self._settings.get("bot", {})
        ch = self._settings.get("channels", {})
        cust = self._settings.get("customizations", {})

        return {
            "APP_ENV": self.get_environment(),
            "API_ID": str(tg.get("api_id", "")),
            "API_HASH": str(tg.get("api_hash", "")),
            "BOT_TOKEN": str(tg.get("bot_token", "")),
            "PHONE_NUMBER": str(tg.get("phone_number", "")),
            "SESSION_STRING": str(tg.get("session_string", "")),
            "PORT": str(web.get("port", 10000)),
            "COPY_MODE": str(bot.get("copy_mode", "clean_copy")),
            "ACTION_DELAY": str(bot.get("action_delay", "2.0")),
            "MIRROR_EDITS": str(bot.get("mirror_edits", True)).lower(),
            "REMOVE_IMAGES": str(bot.get("remove_images", cust.get("remove_images", False))).lower(),
            "SOURCE_CHANNELS": ",".join(ch.get("sources", [])),
            "DESTINATION_CHANNELS": ",".join(ch.get("destinations", [])),
        }

    # ------------------------------------------------------------------ mutations
    def update_section(self, section: str, new_values: Dict[str, Any]):
        with self._lock:
            if section not in self._settings or not isinstance(self._settings[section], dict):
                self._settings[section] = {}
            self._settings[section].update(new_values)
            self._save(self._settings)

    def merge_sections(self, updates: Dict[str, Dict[str, Any]]):
        """Key-level merge of several sections in a single write."""
        with self._lock:
            for section, values in updates.items():
                if not isinstance(values, dict):
                    continue
                if section not in self._settings or not isinstance(self._settings[section], dict):
                    self._settings[section] = {}
                self._settings[section].update(values)
            self._save(self._settings)

    def update_all(self, new_data: Dict[str, Any]):
        with self._lock:
            self._settings.update(new_data)
            self._save(self._settings)

    def increment_stat(self, stat_name: str, amount: int = 1):
        with self._lock:
            stats = self._settings.setdefault("stats", {})
            stats[stat_name] = stats.get(stat_name, 0) + amount
            self._save(self._settings)

    def set_stat(self, stat_name: str, value: Any):
        with self._lock:
            self._settings.setdefault("stats", {})[stat_name] = value
            self._save(self._settings)

    def reset_to_fresh(self, keep_admin: bool = True) -> Dict[str, Any]:
        """
        Wipes everything stored by the app (account, channels, rules, stats) back to blank defaults.
        Env vars are NOT re-applied until the next restart. The admin password hash is kept by
        default so the dashboard never becomes open to the first visitor after a reset.
        """
        with self._lock:
            admin_hash = self._settings.get("web", {}).get("admin_password_hash", "") if keep_admin else ""
            fresh = build_defaults(seed_from_env=False)
            fresh["web"]["admin_password_hash"] = admin_hash
            self._settings = fresh
            self._save(self._settings)
            return self._settings

settings_manager = SettingsManager()
