import os
import json
import logging
import tempfile
import datetime
import calendar
import base64
import urllib.request
import urllib.parse
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from filelock import FileLock, Timeout
from gunicorn.app.base import BaseApplication
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

# optional docker import (best-effort)
try:
    import docker
except Exception:
    docker = None

# Application
app = Flask(__name__)
app.secret_key = os.environ.get("PRUNEMATE_SECRET", "prunemate-secret-key")

# Paths and defaults
CONFIG_PATH = Path(os.environ.get("PRUNEMATE_CONFIG", "/config/config.json"))
# File lock to serialize prune jobs across processes
LOCK_FILE = Path(os.environ.get("PRUNEMATE_LOCK", "/config/prunemate.lock"))
# File to persist the last run key so multiple workers don't double-trigger
LAST_RUN_FILE = Path(os.environ.get("PRUNEMATE_LAST_RUN", "/config/last_run_key"))
LAST_RUN_LOCK = Path(str(LAST_RUN_FILE) + ".lock")
# File to persist all-time statistics
STATS_FILE = Path(os.environ.get("PRUNEMATE_STATS", "/config/stats.json"))

DEFAULT_CONFIG = {
    "frequency": "daily",
    "time": "03:00",
    "day_of_week": "mon",
    "day_of_month": 1,
    "prune_containers": False,
    "prune_images": True,
    "prune_networks": False,
    "prune_volumes": False,
    "prune_build_cache": False,
    "docker_hosts": [],
    "notifications": {
        "provider": "gotify",
        "gotify": {"enabled": False, "url": "", "token": ""},
        "ntfy": {"enabled": False, "url": "", "topic": "", "token": ""},
        "discord": {"enabled": False, "webhook_url": ""},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "pushover": {"enabled": False, "user": "", "token": ""},
        "priority": "medium",
        "only_on_changes": True,
    },
}

config = json.loads(json.dumps(DEFAULT_CONFIG))
# Lock to ensure thread-safe config read/write across workers
import threading
config_lock = threading.RLock()
# In-memory cache (best-effort) for last run; authoritative value is on disk
last_run_key = {"value": None}


def configure_logging():
    """Configure logging with console and rotating file handlers."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)
    try:
        Path("/var/log").mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler("/var/log/prunemate.log", maxBytes=5_000_000, backupCount=3)
        # Note: %(asctime)s uses system local time, not app_timezone
        # Custom log() function handles timezone-aware timestamps
        fh.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(fh)
    except Exception:
        logger.exception("Failed to configure file logging; continuing with console only.")


configure_logging()


# Timezone
tz_name = os.environ.get("PRUNEMATE_TZ", "UTC")
try:
    app_timezone = ZoneInfo(tz_name)
except Exception:
    logging.warning("Invalid timezone '%s', falling back to UTC", tz_name)
    app_timezone = ZoneInfo("UTC")

logging.info("Using timezone: %s", app_timezone)

# Time format (12h or 24h)
use_24h_format = os.environ.get("PRUNEMATE_TIME_24H", "true").lower() in ("true", "1", "yes")
logging.info("Using time format: %s", "24-hour" if use_24h_format else "12-hour")

# Suppress verbose APScheduler job execution logs
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)

# Scheduler
# Background scheduler for minute heartbeat. Jobs are added in __main__.
scheduler = BackgroundScheduler(
    timezone=app_timezone,
    job_defaults={
        "coalesce": False,
        "misfire_grace_time": 300,
    },
)
scheduler.start()


def log(message: str):
    """Log a message with a timezone-aware timestamp."""
    now = datetime.datetime.now(app_timezone)
    timestamp = now.isoformat(timespec="seconds")
    logging.info("[%s] %s", timestamp, message)


def _redact_for_log(obj):
    """Return a deep-copied structure with secrets redacted for safe logging."""
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            if k.lower() in {"token", "api_key", "apikey", "password", "secret", "user"}:
                redacted[k] = "***"
            elif k.lower() == "url" and isinstance(v, str):
                # Redact username:password in URLs
                parsed = urllib.parse.urlparse(v)
                if parsed.username or parsed.password:
                    clean_url = urllib.parse.urlunparse((
                        parsed.scheme,
                        f"***:***@{parsed.hostname}" + (f":{parsed.port}" if parsed.port else ""),
                        parsed.path,
                        parsed.params,
                        parsed.query,
                        parsed.fragment
                    ))
                    redacted[k] = clean_url
                else:
                    redacted[k] = v
            else:
                redacted[k] = _redact_for_log(v)
        return redacted
    if isinstance(obj, list):
        return [_redact_for_log(x) for x in obj]
    return obj


# ---- Cross-process last-run tracking (prevents duplicate scheduled triggers) ----
def _read_last_run_key() -> str | None:
    """Read the last run key from disk in a thread-safe manner."""
    try:
        # Use a short lock to avoid concurrent reads/writes across workers
        with FileLock(str(LAST_RUN_LOCK)):
            if LAST_RUN_FILE.exists():
                return LAST_RUN_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        # Best-effort: on any error, fall back to in-memory state
        pass
    return None


def _write_last_run_key(key: str) -> None:
    """Write the last run key to disk atomically."""
    try:
        with FileLock(str(LAST_RUN_LOCK)):
            parent = LAST_RUN_FILE.parent
            parent.mkdir(parents=True, exist_ok=True)
            tmp = LAST_RUN_FILE.with_suffix(LAST_RUN_FILE.suffix + ".tmp")
            tmp.write_text(key, encoding="utf-8")
            try:
                tmp.chmod(0o600)
            except Exception:
                pass
            tmp.replace(LAST_RUN_FILE)
    except Exception:
        # Non-fatal: fall back to in-memory only
        pass


def _clear_last_run_key() -> None:
    """Clear the last run key from memory and disk."""
    last_run_key["value"] = None
    try:
        with FileLock(str(LAST_RUN_LOCK)):
            if LAST_RUN_FILE.exists():
                LAST_RUN_FILE.unlink()
    except Exception:
        pass


# ---- All-time statistics tracking ----
def load_stats() -> dict:
    """Load cumulative statistics from disk with migration support."""
    # Default stats structure (source of truth for all fields)
    default_stats = {
        "total_space_reclaimed": 0,
        "containers_deleted": 0,
        "images_deleted": 0,
        "networks_deleted": 0,
        "volumes_deleted": 0,
        "build_cache_deleted": 0,
        "prune_runs": 0,
        "first_run": None,
        "last_run": None,
    }
    
    try:
        if STATS_FILE.exists():
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                loaded_stats = json.load(f)
            
            # Merge with defaults to handle missing fields (forward compatibility)
            # This ensures new fields added in future versions get default values
            merged_stats = json.loads(json.dumps(default_stats))
            for key in default_stats:
                if key in loaded_stats:
                    # Type safety: ensure numeric fields are actually numbers
                    if key in {"total_space_reclaimed", "containers_deleted", "images_deleted", 
                              "networks_deleted", "volumes_deleted", "build_cache_deleted", "prune_runs"}:
                        try:
                            merged_stats[key] = int(loaded_stats[key])
                        except (ValueError, TypeError):
                            log(f"Stats field '{key}' has invalid type, using default: 0")
                            merged_stats[key] = 0
                    else:
                        merged_stats[key] = loaded_stats[key]
            
            return merged_stats
    except json.JSONDecodeError as e:
        log(f"Stats file corrupt (invalid JSON): {e}. Using defaults and will overwrite on next save.")
    except Exception as e:
        log(f"Error loading stats from {STATS_FILE}: {e}")
    
    return json.loads(json.dumps(default_stats))


def save_stats(stats: dict) -> None:
    """Atomically save statistics to disk."""
    try:
        parent = STATS_FILE.parent
        parent.mkdir(parents=True, exist_ok=True)
        
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, dir=str(parent), encoding="utf-8") as tmp:
                json.dump(stats, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = Path(tmp.name)
            
            try:
                tmp_path.chmod(0o600)
            except Exception:
                pass
            
            tmp_path.replace(STATS_FILE)
            log(f"Statistics saved to {STATS_FILE}")
        except Exception:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise
    except Exception as e:
        log(f"Error saving statistics: {e}")


def update_stats(containers: int, images: int, networks: int, volumes: int, build_cache: int, space: int) -> None:
    """Update cumulative statistics after a prune run with type safety."""
    stats = load_stats()
    
    # Type-safe increments (load_stats already validates types, but be defensive)
    try:
        # Extra defensive: convert None to 0 before int() to handle null values from old stats
        stats["containers_deleted"] = int(stats.get("containers_deleted") or 0) + int(containers or 0)
        stats["images_deleted"] = int(stats.get("images_deleted") or 0) + int(images or 0)
        stats["networks_deleted"] = int(stats.get("networks_deleted") or 0) + int(networks or 0)
        stats["volumes_deleted"] = int(stats.get("volumes_deleted") or 0) + int(volumes or 0)
        stats["build_cache_deleted"] = int(stats.get("build_cache_deleted") or 0) + int(build_cache or 0)
        stats["total_space_reclaimed"] = int(stats.get("total_space_reclaimed") or 0) + int(space or 0)
        stats["prune_runs"] = int(stats.get("prune_runs") or 0) + 1
    except (ValueError, TypeError) as e:
        log(f"Type error in stats update: {e}. Stats may be incomplete.")
        # Continue with partial update rather than failing completely
    
    now = datetime.datetime.now(app_timezone).isoformat()
    if stats.get("first_run") is None:
        stats["first_run"] = now
    stats["last_run"] = now
    
    save_stats(stats)


def human_bytes(num: int) -> str:
    """Convert bytes to human-readable format (B, KB, MB, GB, TB, PB)."""
    n = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def format_time(time_str: str) -> str:
    """Format time string according to user preference (12h or 24h)."""
    if use_24h_format:
        return time_str
    # Convert 24h to 12h format
    try:
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = parts[1] if len(parts) > 1 else "00"
        
        if hour == 0:
            return f"12:{minute} AM"
        elif hour < 12:
            return f"{hour}:{minute} AM"
        elif hour == 12:
            return f"12:{minute} PM"
        else:
            return f"{hour - 12}:{minute} PM"
    except Exception:
        return time_str


def describe_schedule() -> str:
    """Generate a human-readable description of the current schedule."""
    freq = config.get("frequency", "daily")
    time_str = config.get("time", "03:00")
    formatted_time = format_time(time_str)
    if freq == "daily":
        return f"daily at {formatted_time} ({tz_name})"
    if freq == "weekly":
        day_key = config.get("day_of_week", "mon")
        day_names = {
            "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
            "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
        }
        return f"weekly at {day_names.get(day_key, day_key)} {formatted_time} ({tz_name})"
    if freq == "monthly":
        day_of_month = config.get("day_of_month", 1)
        return f"monthly on day {day_of_month} at {formatted_time} ({tz_name})"
    return f"{freq} at {formatted_time} ({tz_name})"


def validate_time(s: str) -> str:
    """Validate HH:MM time format and clamp to valid 24h range on parse errors."""
    try:
        parts = s.split(":", 1)
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except Exception as e:
        log(f"Invalid time format '{s}': {e}. Falling back to 03:00")
        h, m = 3, 0
    h = max(0, min(23, h))
    m = max(0, min(59, m))
    return f"{h:02d}:{m:02d}"


def _deep_merge(base: dict, override: dict) -> None:
    """Deep merge override dict into base dict, preserving nested structures.
    
    This ensures that nested dicts like notifications.gotify and notifications.ntfy
    are merged individually rather than being completely replaced.
    
    Example:
        base = {"notifications": {"gotify": {...}, "ntfy": {...}}}
        override = {"notifications": {"provider": "ntfy"}}
        Result: base keeps gotify and ntfy sub-dicts, only updates provider field
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            _deep_merge(base[key], value)
        else:
            # Overwrite primitives and lists
            base[key] = value


def effective_config():
    """Return the current effective configuration with relevant fields."""
    freq = config.get("frequency", "daily")
    base = {
        "frequency": freq,
        "time": config.get("time"),
        "prune_containers": config.get("prune_containers"),
        "prune_images": config.get("prune_images"),
        "prune_networks": config.get("prune_networks"),
        "prune_volumes": config.get("prune_volumes"),
        "prune_build_cache": config.get("prune_build_cache"),
        "docker_hosts": config.get("docker_hosts"),
        "notifications": config.get("notifications"),
    }
    if freq == "weekly":
        base["day_of_week"] = config.get("day_of_week")
    elif freq == "monthly":
        base["day_of_month"] = config.get("day_of_month")
    return base


def load_config(silent=False):
    """Load configuration from disk with deep merge. Set silent=True to suppress logging."""
    global config
    with config_lock:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = json.loads(json.dumps(DEFAULT_CONFIG))
            # Deep merge: preserve nested structures like notifications.gotify, notifications.ntfy
            _deep_merge(merged, data)

            # Migrate legacy notification keys into new notifications structure (best-effort)
            # Only migrate if new structure doesn't exist in loaded config
            if "notifications" not in data:
                # Check if we have any legacy notification keys to migrate
                has_gotify_keys = any(k in data for k in ("gotify_enabled", "gotify_url", "gotify_token"))
                has_ntfy_keys = any(k in data for k in ("ntfy_enabled", "ntfy_url", "ntfy_topic", "ntfy_token"))
                has_discord_keys = any(k in data for k in ("discord_enabled", "discord_webhook_url"))
                
                if has_gotify_keys or has_ntfy_keys or has_discord_keys:
                    # Ensure notifications exists with defaults before migration
                    if "notifications" not in merged:
                        merged["notifications"] = json.loads(json.dumps(DEFAULT_CONFIG["notifications"]))
                    
                    # Migrate Gotify settings if present
                    if has_gotify_keys:
                        got = {
                            "enabled": bool(data.get("gotify_enabled")),
                            "url": (data.get("gotify_url") or "").strip(),
                            "token": (data.get("gotify_token") or "").strip(),
                        }
                        merged["notifications"]["gotify"] = got
                    
                    # Migrate ntfy settings if present
                    if has_ntfy_keys:
                        ntf = {
                            "enabled": bool(data.get("ntfy_enabled")),
                            "url": (data.get("ntfy_url") or "").strip(),
                            "topic": (data.get("ntfy_topic") or "").strip(),
                            "token": (data.get("ntfy_token") or "").strip(),
                        }
                        merged["notifications"]["ntfy"] = ntf
                    
                    # Migrate Discord settings if present
                    if has_discord_keys:
                        disc = {
                            "enabled": bool(data.get("discord_enabled")),
                            "webhook_url": (data.get("discord_webhook_url") or "").strip(),
                        }
                        merged["notifications"]["discord"] = disc
                    
                    # Migrate provider selection (default to gotify for backwards compatibility)
                    if has_discord_keys and data.get("discord_enabled"):
                        merged["notifications"]["provider"] = "discord"
                    elif has_ntfy_keys and data.get("ntfy_enabled"):
                        merged["notifications"]["provider"] = "ntfy"
                    elif has_gotify_keys:
                        merged["notifications"]["provider"] = "gotify"
                    
                    # Migrate only_on_changes setting (check both legacy keys, prefer gotify)
                    if "gotify_only_on_changes" in data:
                        merged["notifications"]["only_on_changes"] = bool(data["gotify_only_on_changes"])
                    elif "ntfy_only_on_changes" in data:
                        merged["notifications"]["only_on_changes"] = bool(data["ntfy_only_on_changes"])
            
            # Ensure notifications key exists with all required subkeys
            if "notifications" not in merged:
                merged["notifications"] = json.loads(json.dumps(DEFAULT_CONFIG["notifications"]))
            
            # Ensure all provider subkeys exist (forward compatibility for new providers like telegram/pushover)
            for provider_key in ["gotify", "ntfy", "discord", "telegram", "pushover"]:
                if provider_key not in merged["notifications"]:
                    merged["notifications"][provider_key] = json.loads(json.dumps(DEFAULT_CONFIG["notifications"][provider_key]))
            
            # Migrate numeric priority (1-10) to text priority (low/medium/high)
            priority = merged.get("notifications", {}).get("priority")
            if isinstance(priority, int):
                # Map: 1-3 -> low, 4-7 -> medium, 8-10 -> high
                if priority <= 3:
                    merged["notifications"]["priority"] = "low"
                elif priority <= 7:
                    merged["notifications"]["priority"] = "medium"
                else:
                    merged["notifications"]["priority"] = "high"
            elif not isinstance(priority, str) or priority not in ["low", "medium", "high"]:
                # Invalid or missing priority, set to default
                merged["notifications"]["priority"] = "medium"
            
            # Ensure docker_hosts exists and has valid structure
            if "docker_hosts" not in merged or not isinstance(merged["docker_hosts"], list):
                merged["docker_hosts"] = json.loads(json.dumps(DEFAULT_CONFIG["docker_hosts"]))
            # Clean up: remove Local/unix:// entries that shouldn't be persisted
            merged["docker_hosts"] = [
                h for h in merged["docker_hosts"]
                if h.get("name") != "Local" and "unix://" not in h.get("url", "")
            ]
            # Validate each remaining host entry has required fields
            for host in merged["docker_hosts"]:
                if "name" not in host:
                    host["name"] = "Unnamed"
                if "url" not in host:
                    host["url"] = "tcp://localhost:2375"
                if "enabled" not in host:
                    host["enabled"] = True

            config = merged
            if not silent:
                log(f"Loaded config from {CONFIG_PATH}: {_redact_for_log(effective_config())}")
        except FileNotFoundError:
            if not silent:
                log(f"No config file found at {CONFIG_PATH}, using defaults.")
            config = json.loads(json.dumps(DEFAULT_CONFIG))
        except Exception as e:
            if not silent:
                log(f"Error loading config from {CONFIG_PATH}: {e}. Using defaults.")
            config = json.loads(json.dumps(DEFAULT_CONFIG))


def save_config():
    """Atomic save with fsync and restricted permissions (best-effort)."""
    with config_lock:
        try:
            path = Path(CONFIG_PATH)
            parent = path.parent or Path(".")
            parent.mkdir(parents=True, exist_ok=True)

            # Clean up docker_hosts: remove Local/unix:// entries before saving
            config_to_save = json.loads(json.dumps(config))
            if "docker_hosts" in config_to_save:
                config_to_save["docker_hosts"] = [
                    h for h in config_to_save["docker_hosts"]
                    if h.get("name") != "Local" and "unix://" not in h.get("url", "")
                ]

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile("w", delete=False, dir=str(parent), encoding="utf-8") as tmp:
                    json.dump(config_to_save, tmp, indent=2)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    tmp_path = Path(tmp.name)
                # try to restrict permissions (best-effort)
                try:
                    tmp_path.chmod(0o600)
                except Exception:
                    pass
                # atomic replace
                tmp_path.replace(path)
                log(f"Config saved to {path}: {_redact_for_log(config_to_save)}")
            finally:
                # cleanup leftover temp if any
                if tmp_path and tmp_path.exists() and tmp_path != path:
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
        except Exception as e:
            log(f"Failed to save config to {CONFIG_PATH}: {e}")


def _send_gotify(cfg: dict, title: str, message: str, priority: str = "medium") -> bool:
    """Send a notification via Gotify."""
    if not cfg.get("enabled"):
        log("Gotify disabled; skipping notification.")
        return False
    url = (cfg.get("url") or "").strip()
    token = (cfg.get("token") or "").strip()
    if not url or not token:
        log("Gotify enabled but URL/token missing; skipping.")
        return False
    
    # Map priority: low=2, medium=5, high=8
    priority_map = {"low": 2, "medium": 5, "high": 8}
    gotify_priority = priority_map.get(priority, 2)
    
    endpoint = url.rstrip("/") + "/message?token=" + token
    payload = json.dumps({"title": title, "message": message, "priority": gotify_priority}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            log(f"Gotify notification sent, status={getattr(resp, 'status', '?')}")
            return True
    except Exception as e:
        log(f"Failed to send Gotify notification: {e}")
        return False


def _send_ntfy(cfg: dict, title: str, message: str, priority: str = "medium") -> bool:
    """Send a notification via ntfy."""
    if not cfg.get("enabled"):
        log("ntfy disabled; skipping notification.")
        return False
    url = (cfg.get("url") or "").strip()
    topic = (cfg.get("topic") or "").strip()
    token = (cfg.get("token") or "").strip()
    
    if not url or not topic:
        log("ntfy enabled but URL/topic missing; skipping.")
        return False
    
    # Map priority: low=2, medium=3, high=5 (ntfy range is 1-5)
    priority_map = {"low": 2, "medium": 3, "high": 5}
    ntfy_priority = priority_map.get(priority, 2)
    
    # Parse URL to extract authentication
    parsed = urllib.parse.urlparse(url)
    headers = {"Title": title, "Priority": str(ntfy_priority), "Content-Type": "text/plain"}
    
    # Priority 1: Use explicit token if provided (Bearer token auth)
    if token:
        headers["Authorization"] = f"Bearer {token}"
        endpoint = url.rstrip("/") + "/" + topic.lstrip("/")
    # Priority 2: Check if URL contains username:password (Basic auth)
    elif parsed.username or parsed.password:
        # Reconstruct URL without credentials
        clean_url = urllib.parse.urlunparse((
            parsed.scheme,
            parsed.hostname + (f":{parsed.port}" if parsed.port else ""),
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment
        ))
        # Add Basic Auth header
        username = parsed.username or ""
        password = parsed.password or ""
        credentials = f"{username}:{password}"
        encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded_credentials}"
        endpoint = clean_url.rstrip("/") + "/" + topic.lstrip("/")
    else:
        # No authentication
        endpoint = url.rstrip("/") + "/" + topic.lstrip("/")
    
    payload = message.encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            log(f"ntfy notification sent, status={getattr(resp, 'status', '?')}")
            return True
    except Exception as e:
        log(f"Failed to send ntfy notification: {e}")
        return False


def _send_discord(cfg: dict, title: str, message: str, priority: str = "medium") -> bool:
    """Send a notification via Discord webhook."""
    if not cfg.get("enabled"):
        log("Discord disabled; skipping notification.")
        return False
    webhook_url = (cfg.get("webhook_url") or "").strip()
    if not webhook_url:
        log("Discord enabled but webhook_url missing; skipping.")
        return False
    
    # Validate webhook URL format
    if not webhook_url.startswith("https://discord.com/api/webhooks/") and \
       not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
        log(f"Invalid Discord webhook URL format: {webhook_url[:50]}...")
        return False
    
    # Map priority to Discord color (embed left border color)
    # low: green (info), medium: orange (warning), high: red (critical)
    color_map = {
        "low": 0x2ECC71,     # Green
        "medium": 0xF39C12,  # Orange
        "high": 0xE74C3C,    # Red
    }
    embed_color = color_map.get(priority, 0x2ECC71)  # Default: Green
    
    # Format message for Discord (preserve line breaks)
    payload = {
        "embeds": [{
            "title": title,
            "description": message,
            "color": embed_color,
            "timestamp": datetime.datetime.now(app_timezone).isoformat()
        }]
    }
    
    data = json.dumps(payload).encode("utf-8")
    # Discord requires proper headers including User-Agent
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "PruneMate/1.2.9 (Docker cleanup bot)"
    }
    req = urllib.request.Request(webhook_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log(f"Discord notification sent, status={getattr(resp, 'status', '?')}")
            return True
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        log(f"Discord webhook HTTP error {e.code}: {e.reason}. Body: {error_body[:200]}")
        return False
    except urllib.error.URLError as e:
        log(f"Discord webhook network error: {e.reason}")
        return False
    except Exception as e:
        log(f"Failed to send Discord notification: {e}")
        return False


def _send_telegram(cfg: dict, title: str, message: str, priority: str = "medium") -> bool:
    """Send a notification via Telegram Bot API."""
    if not cfg.get("enabled"):
        log("Telegram disabled; skipping notification.")
        return False
    bot_token = (cfg.get("bot_token") or "").strip()
    chat_id = (cfg.get("chat_id") or "").strip()
    if not bot_token or not chat_id:
        log("Telegram enabled but bot_token/chat_id missing; skipping.")
        return False
    
    # Telegram doesn't have native priority support
    # We use disable_notification for low priority (silent), normal for medium/high
    disable_notification = (priority == "low")
    
    # Format message with title
    full_message = f"<b>{title}</b>\n\n{message}"
    
    # Telegram Bot API endpoint
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    payload = json.dumps({
        "chat_id": chat_id,
        "text": full_message,
        "parse_mode": "HTML",
        "disable_notification": disable_notification
    }).encode("utf-8")
    
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "PruneMate/1.2.9 (Docker cleanup bot)"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                log(f"Telegram notification sent, message_id={result.get('result', {}).get('message_id', '?')}")
                return True
            else:
                log(f"Telegram API returned ok=false: {result}")
                return False
    except Exception as e:
        log(f"Failed to send Telegram notification: {e}")
        return False


def _send_pushover(cfg: dict, title: str, message: str, priority: str = "medium") -> bool:
    """Send a notification via Pushover API."""
    if not cfg.get("enabled"):
        log("Pushover disabled; skipping notification.")
        return False
    user_key = (cfg.get("user") or "").strip()
    api_token = (cfg.get("token") or "").strip()
    if not user_key or not api_token:
        log("Pushover enabled but user/token missing; skipping.")
        return False

    # Map priority: low=-1, medium=0, high=1
    priority_map = {"low": -1, "medium": 0, "high": 1}
    po_priority = priority_map.get(priority, 0)

    endpoint = "https://api.pushover.net/1/messages.json"
    form = {
        "token": api_token,
        "user": user_key,
        "message": message,
        "title": title,
        "priority": str(po_priority),
    }
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = {}
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {}
            status_val = data.get("status")
            request_id = data.get("request")
            receipt = data.get("receipt")
            errors_list = data.get("errors") or []
            if status_val == 1 and not errors_list:
                if receipt:
                    log(f"Pushover notification sent successfully (request={request_id or '?'}, receipt={receipt}).")
                else:
                    log(f"Pushover notification sent successfully (request={request_id or '?'}).")
                return True
            log(f"Pushover API returned status={status_val} errors={errors_list} (request={request_id or '?'}).")
            return False
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
            data = json.loads(body)
            status_val = data.get("status")
            request_id = data.get("request")
            errors_list = data.get("errors") or []
            log(f"Pushover HTTP error {e.code}: status={status_val} errors={errors_list} (request={request_id or '?'}).")
        except Exception:
            log(f"Pushover HTTP error {e.code}: {e.reason}. Body: {body[:200] if body else ''}")
        return False
    except Exception as e:
        log(f"Failed to send Pushover notification: {e}")
        return False


def send_notification(title: str, message: str, priority: str = "medium") -> bool:
    """Send a notification using the configured provider (gotify, ntfy, discord, or telegram)."""
    notcfg = config.get("notifications", DEFAULT_CONFIG["notifications"])
    provider = (notcfg.get("provider") or "gotify").lower()
    if provider == "gotify":
        return _send_gotify(notcfg.get("gotify", {}), title, message, priority)
    if provider == "ntfy":
        return _send_ntfy(notcfg.get("ntfy", {}), title, message, priority)
    if provider == "discord":
        return _send_discord(notcfg.get("discord", {}), title, message, priority)
    if provider == "telegram":
        return _send_telegram(notcfg.get("telegram", {}), title, message, priority)
    if provider == "pushover":
        return _send_pushover(notcfg.get("pushover", {}), title, message, priority)
    log(f"Unknown notification provider '{provider}'; skipping.")
    return False


def create_docker_client(host_url: str):
    """Create a Docker client for the given host URL.
    
    Args:
        host_url: Docker host URL (e.g., 'unix:///var/run/docker.sock', 'tcp://host:2375')
    
    Returns:
        Docker client instance or None on failure
    """
    if docker is None:
        log("Docker SDK not available.")
        return None
    
    try:
        # Support both unix sockets and TCP connections
        if host_url.startswith("unix://"):
            return docker.DockerClient(base_url=host_url)
        elif host_url.startswith("tcp://") or host_url.startswith("http://") or host_url.startswith("https://"):
            return docker.DockerClient(base_url=host_url)
        else:
            # Fallback: try as-is
            return docker.DockerClient(base_url=host_url)
    except Exception as e:
        log(f"Failed to create Docker client for {host_url}: {e}")
        return None


def get_prune_preview() -> dict:
    """Get a preview of what would be pruned without actually pruning.
    
    Returns a dict with preview results per host and aggregate totals.
    """
    load_config(silent=True)
    
    if not any([
        config.get("prune_containers"),
        config.get("prune_images"),
        config.get("prune_networks"),
        config.get("prune_volumes"),
        config.get("prune_build_cache"),
    ]):
        return {"error": "No prune options selected", "hosts": []}
    
    if docker is None:
        return {"error": "Docker SDK not available", "hosts": []}
    
    # Get all hosts (local + external)
    docker_hosts = config.get("docker_hosts", [])
    enabled_external_hosts = [
        h for h in docker_hosts 
        if h.get("enabled", True) and h.get("name") != "Local" and "unix://" not in h.get("url", "")
    ]
    
    all_hosts = [
        {"name": "Local", "url": "unix:///var/run/docker.sock", "enabled": True}
    ] + enabled_external_hosts
    
    preview_results = []
    total_containers = 0
    total_images = 0
    total_networks = 0
    total_volumes = 0
    total_build_cache = 0
    
    for host in all_hosts:
        host_name = host.get("name", "Unnamed")
        host_url = host.get("url", "unix:///var/run/docker.sock")
        
        client = None
        try:
            client = create_docker_client(host_url)
            if client is None:
                preview_results.append({
                    "name": host_name,
                    "url": host_url,
                    "success": False,
                    "error": "Failed to connect",
                    "containers": [],
                    "images": [],
                    "networks": [],
                    "volumes": [],
                    "build_cache": []
                })
                continue
            
            containers_list = []
            images_list = []
            networks_list = []
            volumes_list = []
            build_cache_list = []
            
            # Preview containers (stopped containers)
            if config.get("prune_containers"):
                try:
                    # List all stopped containers (exited, dead, created)
                    # This matches what containers.prune() actually removes
                    all_containers = client.containers.list(all=True)
                    stopped_containers = [c for c in all_containers if c.status in ["exited", "dead", "created"]]
                    containers_list = [
                        {"id": c.short_id, "name": c.name, "status": c.status}
                        for c in stopped_containers
                    ]
                except Exception as e:
                    log(f"[{host_name}] Error listing containers: {e}")
            
            # Preview images (all unused images, matching prune behavior)
            if config.get("prune_images"):
                try:
                    # List all unused images (dangling=False means all unused, not just dangling)
                    # This matches client.images.prune(filters={"dangling": False}) behavior
                    all_images = client.images.list()
                    # Get images in use by containers
                    used_image_ids = set()
                    for container in client.containers.list(all=True):
                        img_id = container.attrs.get("Image")
                        if img_id:
                            used_image_ids.add(img_id)
                    
                    # Filter to unused images only
                    unused_images = [img for img in all_images if img.id not in used_image_ids]
                    images_list = [
                        {
                            "id": img.short_id,
                            "tags": img.tags[:3] if img.tags else ["<none>"],
                            "size": human_bytes(img.attrs.get("Size", 0))
                        }
                        for img in unused_images
                    ]
                except Exception as e:
                    log(f"[{host_name}] Error listing images: {e}")
            
            # Preview networks (unused networks, excluding default ones)
            if config.get("prune_networks"):
                try:
                    networks = client.networks.list()
                    unused_networks = []
                    
                    # Get list of network IDs used by running containers
                    running_network_ids = set()
                    for container in client.containers.list(filters={"status": "running"}):
                        # Get network settings from container
                        network_settings = container.attrs.get("NetworkSettings", {}).get("Networks", {})
                        for net_name, net_info in network_settings.items():
                            if net_info.get("NetworkID"):
                                running_network_ids.add(net_info["NetworkID"])
                    
                    for net in networks:
                        # Skip default networks
                        if net.name in ["bridge", "host", "none"]:
                            continue
                        # Skip networks connected to running containers
                        if net.id in running_network_ids:
                            continue
                        # This network is unused and can be pruned
                        unused_networks.append(net)
                    
                    networks_list = [
                        {"id": net.short_id, "name": net.name}
                        for net in unused_networks
                    ]
                except Exception as e:
                    log(f"[{host_name}] Error listing networks: {e}")
            
            # Preview volumes (unused volumes)
            if config.get("prune_volumes"):
                try:
                    # Get all volumes (can be None in some Docker versions)
                    all_volumes_result = client.volumes.list()
                    all_volumes = all_volumes_result if all_volumes_result else []
                    # Get volumes in use by containers
                    used_volume_names = set()
                    for container in client.containers.list(all=True):
                        for mount in container.attrs.get("Mounts", []):
                            if mount.get("Type") == "volume":
                                used_volume_names.add(mount.get("Name"))
                    
                    unused_volumes = [v for v in all_volumes if v.name not in used_volume_names]
                    volumes_list = [
                        {"name": v.name, "driver": v.attrs.get("Driver", "local")}
                        for v in unused_volumes
                    ]
                except Exception as e:
                    log(f"[{host_name}] Error listing volumes: {e}")
            
            # Preview build cache
            if config.get("prune_build_cache"):
                try:
                    # Get build cache usage via Docker API
                    # We need to do a DRY RUN of the prune operation to see what would be deleted
                    # because client.api.df() may return cached/stale data
                    
                    # Option 1: Use df() but be aware it might show stale data
                    df_result = client.api.df()
                    build_cache_info = df_result.get("BuildCache", [])
                    
                    # Filter to truly reclaimable entries
                    # The Reclaimable field is the most accurate indicator
                    reclaimable_cache = []
                    for c in build_cache_info:
                        # Prioritize Reclaimable field if present (most accurate)
                        if "Reclaimable" in c:
                            if c["Reclaimable"]:
                                reclaimable_cache.append(c)
                        # Fallback: use InUse field
                        elif not c.get("InUse", False):
                            reclaimable_cache.append(c)
                    
                    build_cache_list = [
                        {
                            "id": c.get("ID", "")[:12],  # Short ID like Docker CLI
                            "type": c.get("Type", "unknown"),
                            "size": human_bytes(c.get("Size", 0)),
                            "reclaimable": c.get("Reclaimable", True),
                            "inUse": c.get("InUse", False)
                        }
                        for c in reclaimable_cache
                    ]
                    
                    # Log for debugging
                    if build_cache_list:
                        log(f"[{host_name}] Preview found {len(build_cache_list)} reclaimable build cache entries")
                except Exception as e:
                    log(f"[{host_name}] Error listing build cache: {e}")
            
            total_containers += len(containers_list)
            total_images += len(images_list)
            total_networks += len(networks_list)
            total_volumes += len(volumes_list)
            total_build_cache += len(build_cache_list)
            
            preview_results.append({
                "name": host_name,
                "url": host_url,
                "success": True,
                "containers": containers_list,
                "images": images_list,
                "networks": networks_list,
                "volumes": volumes_list,
                "build_cache": build_cache_list,
                "totals": {
                    "containers": len(containers_list),
                    "images": len(images_list),
                    "networks": len(networks_list),
                    "volumes": len(volumes_list),
                    "build_cache": len(build_cache_list)
                }
            })
            
        except Exception as e:
            log(f"[{host_name}] Error getting preview: {e}")
            preview_results.append({
                "name": host_name,
                "url": host_url,
                "success": False,
                "error": str(e),
                "containers": [],
                "images": [],
                "networks": [],
                "volumes": [],
                "build_cache": []
            })
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
    
    return {
        "hosts": preview_results,
        "totals": {
            "containers": total_containers,
            "images": total_images,
            "networks": total_networks,
            "volumes": total_volumes,
            "build_cache": total_build_cache
        }
    }


def run_prune_job(origin: str = "unknown", wait: bool = False) -> bool:
    """Execute Docker pruning based on current configuration."""
    # Ensure we have the latest config before running prune job
    load_config(silent=True)
    
    lock = FileLock(str(LOCK_FILE))
    acquired = False
    try:
        if wait:
            # Try fast path first
            try:
                lock.acquire(timeout=0)
                acquired = True
                log(f"{origin.capitalize()} trigger: acquired prune lock.")
            except Timeout:
                log(f"{origin.capitalize()} trigger: waiting for any running prune to finish…")
                try:
                    lock.acquire(timeout=300)
                    acquired = True
                except Timeout:
                    log(f"{origin.capitalize()} trigger: waited 300s; skipping run.")
                    return False
        else:
            try:
                lock.acquire(timeout=0)
                acquired = True
            except Timeout:
                log(f"{origin.capitalize()} trigger: prune already in progress; skipping.")
                return False

        log("Starting prune job with configuration:")
        log(str(_redact_for_log(effective_config())))

        if not any([
            config.get("prune_containers"),
            config.get("prune_images"),
            config.get("prune_networks"),
            config.get("prune_volumes"),
            config.get("prune_build_cache"),
        ]):
            log("No prune options selected. Job skipped.")
            return False

        if docker is None:
            log("Docker SDK not available; aborting prune.")
            return False

        # Always include local Docker socket + external hosts from config
        docker_hosts = config.get("docker_hosts", [])
        # Filter out any 'Local' entries from config to prevent duplicates
        enabled_external_hosts = [
            h for h in docker_hosts 
            if h.get("enabled", True) and h.get("name") != "Local" and "unix://" not in h.get("url", "")
        ]
        
        # Build complete host list: local socket first, then external hosts
        all_hosts = [
            {"name": "Local", "url": "unix:///var/run/docker.sock", "enabled": True}
        ] + enabled_external_hosts
        
        log(f"Processing {len(all_hosts)} host(s) (1 local + {len(enabled_external_hosts)} external)...")
        
        # Aggregate totals across all hosts
        total_containers_deleted = 0
        total_images_deleted = 0
        total_networks_deleted = 0
        total_volumes_deleted = 0
        total_build_cache_deleted = 0
        total_space_reclaimed = 0
        
        # Per-host results for detailed reporting
        host_results = []
        
        for host in all_hosts:
            host_name = host.get("name", "Unnamed")
            host_url = host.get("url", "unix:///var/run/docker.sock")
            
            log(f"--- Processing host: {host_name} ({host_url}) ---")
            
            client = None
            try:
                client = create_docker_client(host_url)
                if client is None:
                    log(f"Failed to connect to {host_name}; skipping this host.")
                    host_results.append({
                        "name": host_name,
                        "url": host_url,
                        "success": False,
                        "error": "Failed to connect",
                        "containers": 0,
                        "images": 0,
                        "networks": 0,
                        "volumes": 0,
                        "build_cache": 0,
                        "space": 0,
                    })
                    continue
                
                containers_deleted = images_deleted = networks_deleted = volumes_deleted = build_cache_deleted = 0
                space_reclaimed = 0

                # Containers
                if config.get("prune_containers"):
                    try:
                        log(f"[{host_name}] Pruning containers…")
                        r = client.containers.prune()
                        log(f"[{host_name}] Containers prune result: {r}")
                        containers_deleted = len(r.get("ContainersDeleted") or [])
                        space_reclaimed += int(r.get("SpaceReclaimed") or 0)
                    except Exception as e:
                        log(f"[{host_name}] Error pruning containers: {e}")

                # Images
                if config.get("prune_images"):
                    try:
                        log(f"[{host_name}] Pruning images (all unused)…")
                        r = client.images.prune(filters={"dangling": False})
                        log(f"[{host_name}] Images prune result: {r}")
                        deleted_list = r.get("ImagesDeleted") or []
                        images_deleted = len(deleted_list)
                        space_reclaimed += int(r.get("SpaceReclaimed") or 0)
                    except Exception as e:
                        log(f"[{host_name}] Error pruning images: {e}")

                # Networks
                if config.get("prune_networks"):
                    try:
                        log(f"[{host_name}] Pruning networks…")
                        r = client.networks.prune()
                        log(f"[{host_name}] Networks prune result: {r}")
                        networks_deleted = len(r.get("NetworksDeleted") or [])
                    except Exception as e:
                        log(f"[{host_name}] Error pruning networks: {e}")

                # Volumes
                if config.get("prune_volumes"):
                    try:
                        log(f"[{host_name}] Pruning volumes (all unused, including named)…")
                        r = client.volumes.prune(filters={"all": True})
                        log(f"[{host_name}] Volumes prune result: {r}")
                        # VolumesDeleted is a list of volume names (strings), not dicts
                        volumes_deleted_list = r.get("VolumesDeleted") or []
                        volumes_deleted = len(volumes_deleted_list) if volumes_deleted_list else 0
                        space_reclaimed += int(r.get("SpaceReclaimed") or 0)
                    except Exception as e:
                        log(f"[{host_name}] Error pruning volumes: {e}")

                # Build Cache
                if config.get("prune_build_cache"):
                    try:
                        log(f"[{host_name}] Pruning build cache…")
                        # Use the API client directly for builder prune
                        # Docker API endpoint: POST /build/prune
                        r = client.api.prune_builds()
                        log(f"[{host_name}] Build cache prune result: {r}")
                        # Count the number of cache objects deleted
                        cache_ids_deleted = r.get("CachesDeleted") or []
                        build_cache_deleted = len(cache_ids_deleted) if cache_ids_deleted else 0
                        space_reclaimed += int(r.get("SpaceReclaimed") or 0)
                    except Exception as e:
                        log(f"[{host_name}] Error pruning build cache: {e}")

                log(f"[{host_name}] Prune completed: containers={containers_deleted}, images={images_deleted}, networks={networks_deleted}, volumes={volumes_deleted}, build_cache={build_cache_deleted}, space={human_bytes(space_reclaimed)}")
                
                # Add to totals
                total_containers_deleted += containers_deleted
                total_images_deleted += images_deleted
                total_networks_deleted += networks_deleted
                total_volumes_deleted += volumes_deleted
                total_build_cache_deleted += build_cache_deleted
                total_space_reclaimed += space_reclaimed
                
                # Record host result
                host_results.append({
                    "name": host_name,
                    "url": host_url,
                    "success": True,
                    "containers": containers_deleted,
                    "images": images_deleted,
                    "networks": networks_deleted,
                    "volumes": volumes_deleted,
                    "build_cache": build_cache_deleted,
                    "space": space_reclaimed,
                })
                
            except Exception as e:
                log(f"[{host_name}] Unexpected error during prune: {e}")
                host_results.append({
                    "name": host_name,
                    "url": host_url,
                    "success": False,
                    "error": str(e),
                    "containers": 0,
                    "images": 0,
                    "networks": 0,
                    "volumes": 0,
                    "build_cache": 0,
                    "space": 0,
                })
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass

        log("Prune job finished for all hosts.")

        anything_deleted = any([
            total_containers_deleted, total_images_deleted, total_networks_deleted,
            total_volumes_deleted, total_build_cache_deleted, total_space_reclaimed > 0
        ])

        # Update all-time statistics (always, even if nothing was pruned)
        update_stats(
            containers=total_containers_deleted,
            images=total_images_deleted,
            networks=total_networks_deleted,
            volumes=total_volumes_deleted,
            build_cache=total_build_cache_deleted,
            space=total_space_reclaimed
        )

        # Respect only_on_changes for notifications
        if not anything_deleted and config.get("notifications", {}).get("only_on_changes", True):
            log("Nothing was pruned; skipping notification.")
            return True

        # Build notification summary with per-host breakdown
        summary_lines = [
            f"📅 {describe_schedule()}",
            "",
        ]
        
        # Add per-host details
        if len(all_hosts) > 1:
            summary_lines.append("📊 Per-host results:")
        
        for result in host_results:
            if result.get("success"):
                has_deletions = any([result.get('containers'), result.get('images'), result.get('networks'), result.get('volumes'), result.get('build_cache')])
                
                if has_deletions:
                    summary_lines.append(f"• {result['name']}")
                    if result.get('containers'):
                        summary_lines.append(f"  - 🗑️ {result['containers']} containers")
                    if result.get('images'):
                        summary_lines.append(f"  - 💿 {result['images']} images")
                    if result.get('networks'):
                        summary_lines.append(f"  - 🌐 {result['networks']} networks")
                    if result.get('volumes'):
                        summary_lines.append(f"  - 📦 {result['volumes']} volumes")
                    if result.get('build_cache'):
                        summary_lines.append(f"  - 🏗️ {result['build_cache']} build caches")
                    if result['space']:
                        summary_lines.append(f"  - 💾 {human_bytes(result['space'])} reclaimed")
                else:
                    summary_lines.append(f"• {result['name']}: ✅ Nothing to prune")
            else:
                summary_lines.append(f"• {result['name']}: ❌ {result.get('error', 'Unknown error')}")
        
        if len(all_hosts) > 1:
            summary_lines.append("")
        
        # Add totals
        if len(all_hosts) > 1:
            summary_lines.append("📈 Total across all hosts:")
        if anything_deleted:
            if total_containers_deleted:
                summary_lines.append(f"  - 🗑️ Containers: {total_containers_deleted}")
            if total_images_deleted:
                summary_lines.append(f"  - 💿 Images: {total_images_deleted}")
            if total_networks_deleted:
                summary_lines.append(f"  - 🌐 Networks: {total_networks_deleted}")
            if total_volumes_deleted:
                summary_lines.append(f"  - 📦 Volumes: {total_volumes_deleted}")
            if total_build_cache_deleted:
                summary_lines.append(f"  - 🏗️ Build caches: {total_build_cache_deleted}")
            if total_space_reclaimed:
                summary_lines.append(f"  - 💾 Space reclaimed: {human_bytes(total_space_reclaimed)}")
        else:
            summary_lines.append("✅ Nothing to prune this run")

        message = "\n".join(summary_lines)
        notif_priority = config.get("notifications", {}).get("priority", "medium")
        send_notification("PruneMate run completed", message, priority=notif_priority)
        
        return True
    
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass


def compute_run_key(now: datetime.datetime) -> str:
    """Generate a unique key for the current scheduled run.
    
    Includes the configured time so changing the schedule time allows a new run
    on the same day/week/month.
    """
    freq = config.get("frequency", "daily")
    time_str = config.get("time", "03:00")
    date_str = now.date().isoformat()
    if freq == "daily":
        return f"daily-{date_str}-{time_str}"
    elif freq == "weekly":
        year, week, _ = now.isocalendar()
        return f"weekly-{year}-W{week}-{time_str}"
    elif freq == "monthly":
        return f"monthly-{now.year}-{now.month}-{time_str}"
    else:
        return f"daily-{date_str}-{time_str}"


def check_and_run_scheduled_job():
    """Check if current time matches the configured schedule and trigger prune.
    
    Uses a file-based 'last run key' so the same period is not executed twice
    across multiple processes. If another worker already ran the job for the
    current key, this instance will skip.
    """
    # Always reload config to ensure we have the latest schedule across workers
    load_config(silent=True)
    
    now = datetime.datetime.now(app_timezone)
    freq = config.get("frequency", "daily")
    try:
        time_str = config.get("time", "03:00")
        hour_cfg, minute_cfg = [int(x) for x in time_str.split(":", 1)]
    except Exception:
        hour_cfg, minute_cfg = 3, 0

    hour_now = now.hour
    minute_now = now.minute
    should_run = False

    if freq == "daily":
        if hour_now == hour_cfg and minute_now == minute_cfg:
            should_run = True
    elif freq == "weekly":
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        cfg_dow = day_map.get(config.get("day_of_week", "mon"), 0)
        if now.weekday() == cfg_dow and hour_now == hour_cfg and minute_now == minute_cfg:
            should_run = True
    elif freq == "monthly":
        try:
            dom_cfg = int(config.get("day_of_month", 1))
        except Exception:
            dom_cfg = 1
        dom_cfg = max(1, min(31, dom_cfg))
        # Handle months with fewer days: run on last day if configured day doesn't exist
        _, last_day = calendar.monthrange(now.year, now.month)
        actual_dom = min(dom_cfg, last_day)
        if now.day == actual_dom and hour_now == hour_cfg and minute_now == minute_cfg:
            should_run = True

    if not should_run:
        return

    key = compute_run_key(now)
    # First check in-memory cache
    if last_run_key["value"] == key:
        log(f"Scheduled job skipped: already ran for key '{key}' (in-memory check)")
        return
    # Cross-process read
    disk_key = _read_last_run_key()
    if disk_key == key:
        last_run_key["value"] = key
        log(f"Scheduled job skipped: already ran for key '{key}' (disk check)")
        return

    log(f"Scheduled time reached ({freq}) at {hour_now:02d}:{minute_now:02d}, running prune.")
    # Mark as ran (best-effort) before execution to avoid duplicate triggers
    last_run_key["value"] = key
    _write_last_run_key(key)
    ran = run_prune_job(origin="scheduled", wait=False)
    if not ran:
        # If job skipped due to lock, keep the key set to avoid stampeding
        pass


def heartbeat():
    """Periodic heartbeat function that checks for scheduled jobs."""
    log("Heartbeat: scheduler is alive.")
    check_and_run_scheduled_job()


@app.route("/")
def index():
    """Render the main configuration page."""
    # Reload config to ensure we show the latest settings across workers
    load_config(silent=True)
    return render_template("index.html", config=config, timezone=tz_name, config_path=CONFIG_PATH, use_24h=use_24h_format)


@app.route("/update", methods=["POST"])
def update():
    """Handle configuration updates from the web form."""
    # Reload config to get the latest state before applying changes
    load_config(silent=True)
    old_config = json.loads(json.dumps(config))

    frequency = request.form.get("frequency", "daily")
    
    # Handle both 24h and 12h time formats
    if use_24h_format:
        time_value = request.form.get("time", "03:00")
    else:
        # Convert 12h format (hour, minute, period) to 24h format
        try:
            hour_12 = int(request.form.get("time_hour", "3"))
            minute = int(request.form.get("time_minute", "0"))
            period = request.form.get("time_period", "AM")
            
            # Validate ranges
            hour_12 = max(1, min(12, hour_12))
            minute = max(0, min(59, minute))
            
            # Convert to 24h
            if period == "AM":
                hour_24 = 0 if hour_12 == 12 else hour_12
            else:  # PM
                hour_24 = 12 if hour_12 == 12 else hour_12 + 12
            
            time_value = f"{hour_24:02d}:{minute:02d}"
        except Exception:
            time_value = "03:00"
    
    day_of_week = request.form.get("day_of_week", "mon")
    raw_dom = request.form.get("day_of_month", "1")
    # Sanitize day-of-month (1..31)
    try:
        day_of_month = int(raw_dom)
    except Exception:
        day_of_month = 1
    day_of_month = max(1, min(31, day_of_month))

    prune_containers = "prune_containers" in request.form
    prune_images = "prune_images" in request.form
    prune_networks = "prune_networks" in request.form
    prune_volumes = "prune_volumes" in request.form
    prune_build_cache = "prune_build_cache" in request.form

    provider = request.form.get("notifications_provider", "gotify")
    gotify_enabled = "gotify_enabled" in request.form
    gotify_url = (request.form.get("gotify_url") or "").strip()
    gotify_token = (request.form.get("gotify_token") or "").strip()
    ntfy_enabled = "ntfy_enabled" in request.form
    ntfy_url = (request.form.get("ntfy_url") or "").strip()
    ntfy_topic = (request.form.get("ntfy_topic") or "").strip()
    ntfy_token = (request.form.get("ntfy_token") or "").strip()
    discord_enabled = "discord_enabled" in request.form
    discord_webhook_url = (request.form.get("discord_webhook_url") or "").strip()
    telegram_enabled = "telegram_enabled" in request.form
    telegram_bot_token = (request.form.get("telegram_bot_token") or "").strip()
    telegram_chat_id = (request.form.get("telegram_chat_id") or "").strip()
    pushover_enabled = "pushover_enabled" in request.form
    pushover_user = (request.form.get("pushover_user") or "").strip()
    pushover_token = (request.form.get("pushover_token") or "").strip()
    # Parse notification priority (low/medium/high, default 'medium')
    notification_priority = request.form.get("notification_priority", "medium").strip().lower()
    if notification_priority not in ["low", "medium", "high"]:
        notification_priority = "medium"
    only_on_changes = "notifications_only_on_changes" in request.form

    # Auto-enable selected provider if fields are filled but toggle was forgotten
    if provider == "gotify" and not gotify_enabled and gotify_url and gotify_token:
        gotify_enabled = True
    if provider == "ntfy" and not ntfy_enabled and ntfy_url and ntfy_topic:
        ntfy_enabled = True
    if provider == "discord" and not discord_enabled and discord_webhook_url:
        discord_enabled = True
    if provider == "telegram" and not telegram_enabled and telegram_bot_token and telegram_chat_id:
        telegram_enabled = True
    if provider == "pushover" and not pushover_enabled and pushover_user and pushover_token:
        pushover_enabled = True

    time_value = validate_time(time_value)

    new_values = {
        "frequency": frequency,
        "time": time_value,
        "day_of_week": day_of_week,
        "day_of_month": day_of_month,
        "prune_containers": prune_containers,
        "prune_images": prune_images,
        "prune_networks": prune_networks,
        "prune_volumes": prune_volumes,
        "prune_build_cache": prune_build_cache,
        "notifications": {
            "provider": provider,
            "gotify": {"enabled": gotify_enabled, "url": gotify_url, "token": gotify_token},
            "ntfy": {"enabled": ntfy_enabled, "url": ntfy_url, "topic": ntfy_topic, "token": ntfy_token},
            "discord": {"enabled": discord_enabled, "webhook_url": discord_webhook_url},
            "telegram": {"enabled": telegram_enabled, "bot_token": telegram_bot_token, "chat_id": telegram_chat_id},
            "pushover": {"enabled": pushover_enabled, "user": pushover_user, "token": pushover_token},
            "priority": notification_priority,
            "only_on_changes": only_on_changes,
        },
    }

    schedule_keys = [
        "frequency","time","day_of_week","day_of_month",
        "prune_containers","prune_images","prune_networks","prune_volumes","prune_build_cache"
    ]
    schedule_changed = any(new_values[k] != old_config.get(k) for k in schedule_keys)
    config.update(new_values)
    if schedule_changed:
        # Clear last-run key so the next scheduled window can fire
        _clear_last_run_key()

    save_config()
    flash("Configuration updated.", "success")
    return redirect(url_for("index"))


@app.route("/run-now", methods=["POST"])
def run_now():
    """Trigger an immediate manual prune job."""
    # Reload config to use the latest prune settings
    load_config(silent=True)
    log("Manual run trigger received.")
    ran = run_prune_job(origin="manual", wait=True)
    flash("Prune job executed manually." if ran else "Prune job skipped (busy or timeout).", "info")
    return redirect(url_for("index"))


@app.route("/preview-prune", methods=["POST"])
def preview_prune():
    """Get a preview of what would be pruned without executing."""
    load_config(silent=True)
    
    # Save current prune settings from request body (if provided)
    try:
        data = request.get_json() or {}
        if any(k in data for k in ["prune_containers", "prune_images", "prune_networks", "prune_volumes", "prune_build_cache"]):
            config["prune_containers"] = data.get("prune_containers", False)
            config["prune_images"] = data.get("prune_images", False)
            config["prune_networks"] = data.get("prune_networks", False)
            config["prune_volumes"] = data.get("prune_volumes", False)
            config["prune_build_cache"] = data.get("prune_build_cache", False)
            save_config()
            log("Preview requested with updated prune settings saved.")
    except Exception as e:
        log(f"Error parsing preview request body: {e}")
    
    log("Prune preview requested.")
    preview = get_prune_preview()
    return jsonify(preview)


@app.route("/run-confirmed", methods=["POST"])
def run_confirmed():
    """Execute prune after user has seen and confirmed the preview."""
    load_config(silent=True)
    
    # Ensure latest prune settings are saved (should be saved by preview, but double-check)
    try:
        data = request.get_json() or {}
        if any(k in data for k in ["prune_containers", "prune_images", "prune_networks", "prune_volumes", "prune_build_cache"]):
            config["prune_containers"] = data.get("prune_containers", False)
            config["prune_images"] = data.get("prune_images", False)
            config["prune_networks"] = data.get("prune_networks", False)
            config["prune_volumes"] = data.get("prune_volumes", False)
            config["prune_build_cache"] = data.get("prune_build_cache", False)
            save_config()
            log("Confirmed run with updated prune settings saved.")
    except Exception as e:
        log(f"Error parsing confirmed run request body: {e}")
    
    log("Confirmed manual run trigger received.")
    ran = run_prune_job(origin="manual", wait=True)
    return jsonify({
        "success": ran,
        "message": "Prune job executed successfully." if ran else "Prune job skipped (busy or timeout)."
    })


@app.route("/test-notification", methods=["POST"])
def test_notification():
    """Save configuration and send a test notification."""
    # First save the config with the current form data, then test notification
    load_config(silent=True)
    old_config = json.loads(json.dumps(config))

    frequency = request.form.get("frequency", "daily")
    
    # Handle both 24h and 12h time formats
    if use_24h_format:
        time_value = request.form.get("time", "03:00")
    else:
        # Convert 12h format (hour, minute, period) to 24h format
        try:
            hour_12 = int(request.form.get("time_hour", "3"))
            minute = int(request.form.get("time_minute", "0"))
            period = request.form.get("time_period", "AM")
            
            # Validate ranges
            hour_12 = max(1, min(12, hour_12))
            minute = max(0, min(59, minute))
            
            # Convert to 24h
            if period == "AM":
                hour_24 = 0 if hour_12 == 12 else hour_12
            else:  # PM
                hour_24 = 12 if hour_12 == 12 else hour_12 + 12
            
            time_value = f"{hour_24:02d}:{minute:02d}"
        except Exception:
            time_value = "03:00"
    
    day_of_week = request.form.get("day_of_week", "mon")
    raw_dom = request.form.get("day_of_month", "1")
    try:
        day_of_month = int(raw_dom)
    except Exception:
        day_of_month = 1
    day_of_month = max(1, min(31, day_of_month))

    prune_containers = "prune_containers" in request.form
    prune_images = "prune_images" in request.form
    prune_networks = "prune_networks" in request.form
    prune_volumes = "prune_volumes" in request.form
    prune_build_cache = "prune_build_cache" in request.form

    provider = request.form.get("notifications_provider", "gotify")
    gotify_enabled = "gotify_enabled" in request.form
    gotify_url = (request.form.get("gotify_url") or "").strip()
    gotify_token = (request.form.get("gotify_token") or "").strip()
    ntfy_enabled = "ntfy_enabled" in request.form
    ntfy_url = (request.form.get("ntfy_url") or "").strip()
    ntfy_topic = (request.form.get("ntfy_topic") or "").strip()
    ntfy_token = (request.form.get("ntfy_token") or "").strip()
    discord_enabled = "discord_enabled" in request.form
    discord_webhook_url = (request.form.get("discord_webhook_url") or "").strip()
    telegram_enabled = "telegram_enabled" in request.form
    telegram_bot_token = (request.form.get("telegram_bot_token") or "").strip()
    telegram_chat_id = (request.form.get("telegram_chat_id") or "").strip()
    pushover_enabled = "pushover_enabled" in request.form
    pushover_user = (request.form.get("pushover_user") or "").strip()
    pushover_token = (request.form.get("pushover_token") or "").strip()
    # Parse notification priority (low/medium/high, default 'medium')
    notification_priority = request.form.get("notification_priority", "medium").strip().lower()
    if notification_priority not in ["low", "medium", "high"]:
        notification_priority = "medium"
    only_on_changes = "notifications_only_on_changes" in request.form

    # Auto-enable selected provider if fields are filled but toggle was forgotten
    if provider == "gotify" and not gotify_enabled and gotify_url and gotify_token:
        gotify_enabled = True
    if provider == "ntfy" and not ntfy_enabled and ntfy_url and ntfy_topic:
        ntfy_enabled = True
    if provider == "discord" and not discord_enabled and discord_webhook_url:
        discord_enabled = True
    if provider == "telegram" and not telegram_enabled and telegram_bot_token and telegram_chat_id:
        telegram_enabled = True
    if provider == "pushover" and not pushover_enabled and pushover_user and pushover_token:
        pushover_enabled = True

    time_value = validate_time(time_value)

    new_values = {
        "frequency": frequency,
        "time": time_value,
        "day_of_week": day_of_week,
        "day_of_month": day_of_month,
        "prune_containers": prune_containers,
        "prune_images": prune_images,
        "prune_networks": prune_networks,
        "prune_volumes": prune_volumes,
        "prune_build_cache": prune_build_cache,
        "notifications": {
            "provider": provider,
            "gotify": {"enabled": gotify_enabled, "url": gotify_url, "token": gotify_token},
            "ntfy": {"enabled": ntfy_enabled, "url": ntfy_url, "topic": ntfy_topic, "token": ntfy_token},
            "discord": {"enabled": discord_enabled, "webhook_url": discord_webhook_url},
            "telegram": {"enabled": telegram_enabled, "bot_token": telegram_bot_token, "chat_id": telegram_chat_id},
            "pushover": {"enabled": pushover_enabled, "user": pushover_user, "token": pushover_token},
            "priority": notification_priority,
            "only_on_changes": only_on_changes,
        },
    }

    schedule_keys = [
        "frequency","time","day_of_week","day_of_month",
        "prune_containers","prune_images","prune_networks","prune_volumes","prune_build_cache"
    ]
    schedule_changed = any(new_values[k] != old_config.get(k) for k in schedule_keys)
    config.update(new_values)
    if schedule_changed:
        _clear_last_run_key()

    save_config()
    
    # Test the notification with the saved config
    log("Notification test requested from UI.")
    # Use configured priority for test notification
    test_priority = config.get("notifications", {}).get("priority", "medium")
    ok = send_notification(
        "PruneMate test notification",
        "This is a test message from PruneMate.\n\nIf you see this, your current provider settings are working.",
        priority=test_priority,
    )
    flash("Configuration saved. " + ("Test notification sent." if ok else "Test notification failed (check settings & logs)."), "info")
    return redirect(url_for("index"))


@app.route("/stats")
def stats():
    """Return all-time statistics as JSON."""
    return jsonify(load_stats())


@app.route("/api/stats")
def api_stats():
    """Return formatted statistics for Homepage dashboard widget."""
    stats = load_stats()
    
    # Calculate relative time for last run
    last_run_text = "Never"
    last_run_timestamp = None
    if stats.get("last_run"):
        try:
            last_run_dt = datetime.datetime.fromisoformat(stats["last_run"])
            now = datetime.datetime.now(app_timezone)
            
            # Convert both to timezone-aware if needed
            if last_run_dt.tzinfo is None:
                last_run_dt = last_run_dt.replace(tzinfo=app_timezone)
            
            delta = now - last_run_dt
            
            # Format relative time
            if delta.days > 0:
                last_run_text = f"{delta.days}d ago"
            elif delta.seconds >= 3600:
                hours = delta.seconds // 3600
                last_run_text = f"{hours}h ago"
            elif delta.seconds >= 60:
                minutes = delta.seconds // 60
                last_run_text = f"{minutes}m ago"
            else:
                last_run_text = "Just now"
            
            # Provide timestamp in seconds for format: relativeTime
            last_run_timestamp = int(last_run_dt.timestamp())
        except (ValueError, TypeError, OSError) as e:
            log(f"Error parsing last_run timestamp: {e}")
            last_run_text = "Unknown"
        except Exception as e:
            log(f"Unexpected error in /api/stats timestamp calculation: {e}")
            last_run_text = "Unknown"
    
    return jsonify({
        "pruneRuns": stats.get("prune_runs", 0),
        "containersDeleted": stats.get("containers_deleted", 0),
        "imagesDeleted": stats.get("images_deleted", 0),
        "networksDeleted": stats.get("networks_deleted", 0),
        "volumesDeleted": stats.get("volumes_deleted", 0),
        "buildCacheDeleted": stats.get("build_cache_deleted", 0),
        "spaceReclaimed": stats.get("total_space_reclaimed", 0),
        "spaceReclaimedHuman": human_bytes(stats.get("total_space_reclaimed", 0)),
        "firstRun": stats.get("first_run"),
        "lastRun": stats.get("last_run"),
        "lastRunText": last_run_text,
        "lastRunTimestamp": last_run_timestamp
    })


@app.route("/hosts")
def list_hosts():
    """Return list of Docker hosts as JSON (includes Local socket for display)."""
    load_config(silent=True)
    external_hosts = config.get("docker_hosts", [])
    
    # Include Local host at the beginning for consistency with run_prune_job()
    # Frontend will filter it out for display, but it provides accurate count
    all_hosts = [
        {"name": "Local", "url": "unix:///var/run/docker.sock", "enabled": True}
    ] + external_hosts
    
    return jsonify({"hosts": all_hosts})


@app.route("/hosts/add", methods=["POST"])
def add_host():
    """Add a new Docker host."""
    load_config(silent=True)
    
    name = (request.form.get("name") or "").strip()
    url = (request.form.get("url") or "").strip()
    enabled = "enabled" in request.form
    
    if not name or not url:
        flash("Host name and URL are required.", "warn")
        return redirect(url_for("index"))
    
    # Validate URL format
    valid_protocols = ["tcp://", "http://", "https://"]
    if not any(url.startswith(proto) for proto in valid_protocols):
        flash("URL must start with tcp://, http://, or https://", "warn")
        return redirect(url_for("index"))
    
    new_host = {
        "name": name,
        "url": url,
        "enabled": enabled
    }
    
    if "docker_hosts" not in config:
        config["docker_hosts"] = []
    
    config["docker_hosts"].append(new_host)
    save_config()
    
    flash(f"Docker host '{name}' added successfully.", "info")
    return redirect(url_for("index"))


@app.route("/hosts/<int:index>/update", methods=["POST"])
def update_host(index):
    """Update an existing Docker host."""
    load_config(silent=True)
    
    hosts = config.get("docker_hosts", [])
    if index < 0 or index >= len(hosts):
        flash("Invalid host index.", "warn")
        return redirect(url_for("index"))
    
    name = (request.form.get("name") or "").strip()
    url = (request.form.get("url") or "").strip()
    enabled = "enabled" in request.form
    
    if not name or not url:
        flash("Host name and URL are required.", "warn")
        return redirect(url_for("index"))
    
    # Validate URL format
    valid_protocols = ["tcp://", "http://", "https://"]
    if not any(url.startswith(proto) for proto in valid_protocols):
        flash("URL must start with tcp://, http://, or https://", "warn")
        return redirect(url_for("index"))
    
    hosts[index] = {
        "name": name,
        "url": url,
        "enabled": enabled
    }
    
    config["docker_hosts"] = hosts
    save_config()
    
    flash(f"Docker host '{name}' updated successfully.", "info")
    return redirect(url_for("index"))


@app.route("/hosts/<int:index>/delete", methods=["POST"])
def delete_host(index):
    """Delete a Docker host."""
    load_config(silent=True)
    
    hosts = config.get("docker_hosts", [])
    if index < 0 or index >= len(hosts):
        flash("Invalid host index.", "warn")
        return redirect(url_for("index"))
    
    # Note: It's safe to delete all external hosts - Local is always available at runtime
    deleted_name = hosts[index].get("name", "Unknown")
    del hosts[index]
    
    config["docker_hosts"] = hosts
    save_config()
    
    flash(f"Docker host '{deleted_name}' deleted successfully.", "info")
    return redirect(url_for("index"))


@app.route("/hosts/<int:index>/toggle", methods=["POST"])
def toggle_host(index):
    """Toggle enabled/disabled status of a Docker host."""
    load_config(silent=True)
    
    hosts = config.get("docker_hosts", [])
    if index < 0 or index >= len(hosts):
        return jsonify({"success": False, "error": "Invalid host index"}), 400
    
    hosts[index]["enabled"] = not hosts[index].get("enabled", True)
    config["docker_hosts"] = hosts
    save_config()
    
    status = "enabled" if hosts[index]["enabled"] else "disabled"
    return jsonify({"success": True, "enabled": hosts[index]["enabled"], "message": f"Host {status}"})


class StandaloneApplication(BaseApplication):
    """Custom Gunicorn application for running Flask with specific options."""
    
    def __init__(self, app, options=None):
        """Initialize the application with Flask app and options."""
        self.options = options or {}
        self.application = app
        super().__init__()

    def load_config(self):
        """Load Gunicorn configuration from options dict."""
        for key, value in self.options.items():
            if key in self.cfg.settings and value is not None:
                self.cfg.set(key.lower(), value)

    def load(self):
        """Return the Flask application instance."""
        return self.application


if __name__ == "__main__":
    load_config()
    scheduler.add_job(heartbeat, CronTrigger(second=0), id="heartbeat", max_instances=1, coalesce=True)
    log("Scheduler heartbeat job started (every minute at :00).")
    
    options = {
        "bind": "0.0.0.0:8080",
        "workers": 1,
        "threads": 2,
        "timeout": 120,
        # Disable access logs (172.x request lines)
        "accesslog": None,
        "errorlog": "-",
        "loglevel": "info",
    }
    StandaloneApplication(app, options).run()
