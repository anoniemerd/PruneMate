import os
import re
import sys
import json
import functools
import hmac
import html
import time
import secrets
import logging
import tempfile
import datetime
import calendar
import base64
import threading
import urllib.request
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, Response
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from filelock import FileLock, Timeout
from gunicorn.app.base import BaseApplication
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

# optional docker import (best-effort)
try:
    import docker
    import docker.tls
except Exception:
    docker = None

APP_VERSION = "2.0.0"

# Application
app = Flask(__name__)

# Paths and defaults
CONFIG_PATH = Path(os.environ.get("PRUNEMATE_CONFIG", "/config/config.json"))
# Persisted Flask secret key (generated on first start when PRUNEMATE_SECRET is not set)
SECRET_FILE = Path(os.environ.get("PRUNEMATE_SECRET_FILE", str(CONFIG_PATH.parent / "secret_key")))
# Run history (last N runs with per-host results)
HISTORY_FILE = Path(os.environ.get("PRUNEMATE_HISTORY", str(CONFIG_PATH.parent / "history.json")))
HISTORY_LIMIT = 100
# Log file shown in the UI log viewer
LOG_FILE = Path(os.environ.get("PRUNEMATE_LOG_FILE", "/var/log/prunemate.log"))
# Docker endpoint for the "Local" host (set to tcp://socket-proxy:2375 when using a socket proxy)
LOCAL_DOCKER_URL = os.environ.get("PRUNEMATE_DOCKER_HOST", "unix:///var/run/docker.sock").strip() or "unix:///var/run/docker.sock"
# Root directory for TLS client certificates of remote hosts (<root>/<name>/{ca,cert,key}.pem)
CERTS_ROOT = Path(os.environ.get("PRUNEMATE_CERTS_DIR", "/certs"))
# File lock to serialize prune jobs across processes
LOCK_FILE = Path(os.environ.get("PRUNEMATE_LOCK", "/config/prunemate.lock"))
# File to persist the last run key so multiple workers don't double-trigger
LAST_RUN_FILE = Path(os.environ.get("PRUNEMATE_LAST_RUN", "/config/last_run_key"))
LAST_RUN_LOCK = Path(str(LAST_RUN_FILE) + ".lock")
# File to persist all-time statistics
STATS_FILE = Path(os.environ.get("PRUNEMATE_STATS", "/config/stats.json"))

DEFAULT_CONFIG = {
    "schedule_enabled": True,
    "frequency": "daily",
    "time": "03:00",
    "day_of_week": "mon",
    "day_of_month": 1,
    "prune_containers": False,
    "prune_images": True,
    "prune_networks": False,
    "prune_volumes": False,
    "prune_build_cache": False,
    # Exclusions: resources with this label are never pruned (empty string disables)
    "protect_label": "prunemate.keep",
    # Only prune resources older than N hours (0 = no age limit; not supported for volumes)
    "min_age_hours": 0,
    "docker_hosts": [],
    "notifications": {
        "provider": "gotify",
        "gotify": {"enabled": False, "url": "", "token": ""},
        "ntfy": {"enabled": False, "url": "", "topic": "", "token": ""},
        "discord": {"enabled": False, "webhook_url": ""},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "priority": "medium",
        "only_on_changes": True,
    },
}

config = json.loads(json.dumps(DEFAULT_CONFIG))
# Lock to ensure thread-safe config read/write across workers
config_lock = threading.RLock()
# In-memory cache (best-effort) for last run; authoritative value is on disk
last_run_key = {"value": None}

# ---- Early exit for CLI tools before initializing heavy components ----
if len(sys.argv) > 1 and sys.argv[1] == "--gen-hash":
    if len(sys.argv) > 2:
        password = sys.argv[2]
        # Generate hash
        raw_hash = generate_password_hash(password)
        safe_hash = base64.b64encode(raw_hash.encode("utf-8")).decode("utf-8")
        print(safe_hash)
        sys.exit(0)
    else:
        print("Usage: python prunemate.py --gen-hash <password>")
        sys.exit(1)

def configure_logging():
    """Configure logging with console and rotating file handlers."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(str(LOG_FILE), maxBytes=5_000_000, backupCount=3)
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


_SECRET_KEY_NAMES = {"token", "bot_token", "webhook_url", "api_key", "apikey", "password", "secret"}


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return k in _SECRET_KEY_NAMES or k.endswith("_token") or k.endswith("_password") or k.endswith("_secret")


def _redact_for_log(obj):
    """Return a deep-copied structure with secrets redacted for safe logging."""
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            if _is_secret_key(k):
                redacted[k] = "***" if v else ""
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


# ---- Secret key & session hardening ----
def _load_or_create_secret_key() -> str:
    """Return the Flask secret key.

    Priority: PRUNEMATE_SECRET env var -> persisted key file -> newly generated key
    (persisted with 0600 permissions). Never falls back to a hard-coded value.
    """
    env_secret = (os.environ.get("PRUNEMATE_SECRET") or "").strip()
    if env_secret:
        if len(env_secret) < 32:
            logging.warning("PRUNEMATE_SECRET is shorter than 32 characters; consider using a longer random value.")
        return env_secret
    try:
        if SECRET_FILE.exists():
            existing = SECRET_FILE.read_text(encoding="utf-8").strip()
            if len(existing) >= 32:
                return existing
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        new_secret = secrets.token_hex(32)
        tmp = SECRET_FILE.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_secret)
        tmp.replace(SECRET_FILE)
        logging.info("Generated new session secret key at %s", SECRET_FILE)
        return new_secret
    except Exception as e:
        logging.warning("Could not persist secret key (%s); using an ephemeral key. Sessions reset on restart.", e)
        return secrets.token_hex(32)


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Parse an integer env var; invalid values fall back to the default instead of crashing."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logging.warning("Invalid value %s=%r (expected a number); using %d.", name, raw, default)
        return default


app.secret_key = _load_or_create_secret_key()
app.config.update(
    SESSION_COOKIE_NAME="prunemate_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Enable when PruneMate is served over HTTPS (e.g. behind a reverse proxy)
    SESSION_COOKIE_SECURE=_env_bool("PRUNEMATE_COOKIE_SECURE", False),
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=_env_int("PRUNEMATE_SESSION_HOURS", 12, minimum=1)),
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    TEMPLATES_AUTO_RELOAD=False,
)

# Trust X-Forwarded-* headers only when explicitly configured (number of proxies in front)
_trusted_proxies = _env_int("PRUNEMATE_TRUSTED_PROXIES", 0, minimum=0)
if _trusted_proxies > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_trusted_proxies, x_proto=_trusted_proxies, x_host=_trusted_proxies)
    logging.info("Trusting %d reverse proxy hop(s) for X-Forwarded-* headers", _trusted_proxies)


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


# ---- Run history (last N runs) ----
def _atomic_write_json(path: Path, data) -> None:
    """Write JSON atomically with fsync and 0600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        try:
            tmp_path.chmod(0o600)
        except Exception:
            pass
        tmp_path.replace(path)
    finally:
        if tmp_path and tmp_path.exists() and tmp_path != path:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def load_history() -> list:
    """Load run history (oldest first). Returns an empty list on any error."""
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)][-HISTORY_LIMIT:]
    except Exception as e:
        log(f"Error loading history from {HISTORY_FILE}: {e}")
    return []


def append_history(record: dict) -> None:
    """Append a run record to the history file, keeping the last HISTORY_LIMIT entries."""
    try:
        history = load_history()
        history.append(record)
        _atomic_write_json(HISTORY_FILE, history[-HISTORY_LIMIT:])
    except Exception as e:
        log(f"Error saving history: {e}")


# ---- One-time import of run history from V1.x log files ----
_LOG_LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?)\] (.*)$")
_HOST_DONE_RE = re.compile(
    r"^\[(.*)\] Prune completed: containers=(\d+), images=(\d+), networks=(\d+), volumes=(\d+), "
    r"build_cache=(\d+), space=([\d.]+) (B|KB|MB|GB|TB|PB)$"
)
_HOST_START_RE = re.compile(r"^--- Processing host: (.*) \([^()]*\) ---$")
_UNIT_POWER = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4, "PB": 5}


def _parse_runs_from_log_lines(lines) -> list:
    """Reconstruct run records from PruneMate log lines (format used by V1.3+ and V2)."""
    runs = []
    origin = "unknown"
    current = None

    for raw in lines:
        m = _LOG_LINE_RE.match(raw.rstrip("\n"))
        if not m:
            continue
        ts, msg = m.group(1), m.group(2)

        if msg in ("Confirmed manual run trigger received.", "Manual run trigger received."):
            origin = "manual"
        elif msg.startswith("Scheduled time reached"):
            origin = "scheduled"
        elif msg == "Starting prune job with configuration:":
            current = {"start": ts, "origin": origin, "hosts": []}
            origin = "unknown"
        elif current is None:
            continue
        elif msg.startswith(("No prune options selected", "Docker SDK not available")):
            current = None
        elif _HOST_START_RE.match(msg):
            current["host"] = _HOST_START_RE.match(msg).group(1)
        elif msg.startswith("Failed to connect to ") and msg.endswith("; skipping this host."):
            name = msg[len("Failed to connect to "):-len("; skipping this host.")]
            current["hosts"].append({"name": name, "success": False, "error": "Failed to connect",
                                     "containers": 0, "images": 0, "networks": 0, "volumes": 0,
                                     "build_cache": 0, "space": 0})
        elif " Unexpected error during prune: " in msg and msg.startswith("["):
            name = msg[1:msg.index("] Unexpected error during prune: ")]
            current["hosts"].append({"name": name, "success": False, "error": "Unexpected error (see logs)",
                                     "containers": 0, "images": 0, "networks": 0, "volumes": 0,
                                     "build_cache": 0, "space": 0})
        elif _HOST_DONE_RE.match(msg):
            g = _HOST_DONE_RE.match(msg).groups()
            current["hosts"].append({
                "name": g[0], "success": True,
                "containers": int(g[1]), "images": int(g[2]), "networks": int(g[3]),
                "volumes": int(g[4]), "build_cache": int(g[5]),
                "space": int(float(g[6]) * (1024 ** _UNIT_POWER[g[7]])),
            })
        elif msg == "Prune job finished for all hosts.":
            if current["hosts"]:
                totals = {k: sum(h[k] for h in current["hosts"])
                          for k in ("containers", "images", "networks", "volumes", "build_cache", "space")}
                try:
                    duration = (datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                - datetime.datetime.fromisoformat(current["start"].replace("Z", "+00:00"))).total_seconds()
                except ValueError:
                    duration = None
                runs.append({
                    "timestamp": ts,
                    "origin": current["origin"],
                    "duration": round(duration, 1) if duration is not None else None,
                    "success": all(h["success"] for h in current["hosts"]),
                    "totals": totals,
                    "hosts": current["hosts"],
                    "imported": True,  # reconstructed from logs; space is rounded
                })
            current = None
    return runs


def import_history_from_logs() -> int:
    """On the first V2 start, rebuild run history from the (rotated) V1.x log files.

    Runs only when history.json doesn't exist yet. Always writes history.json afterwards
    (possibly empty) so the import never runs twice. Returns the number of imported runs.
    """
    if HISTORY_FILE.exists():
        return 0
    files = [Path(f"{LOG_FILE}.{i}") for i in (3, 2, 1)] + [LOG_FILE]  # oldest first
    runs = []
    for path in files:
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                runs.extend(_parse_runs_from_log_lines(f))
        except Exception as e:
            log(f"History import: could not read {path}: {e}")
    try:
        _atomic_write_json(HISTORY_FILE, runs[-HISTORY_LIMIT:])
    except Exception as e:
        log(f"History import: could not write {HISTORY_FILE}: {e}")
        return 0
    if runs:
        log(f"History import: reconstructed {len(runs)} earlier run(s) from log files "
            f"(kept the last {min(len(runs), HISTORY_LIMIT)}).")
    return min(len(runs), HISTORY_LIMIT)


_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def parse_docker_time(value) -> datetime.datetime | None:
    """Parse Docker timestamps (RFC3339 with nanoseconds, or unix seconds) to aware UTC datetimes."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)
        s = str(value).strip()
        if s.startswith("0001-01-01"):
            return None
        s = _FRACTION_RE.sub(r"\1", s).replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        return None


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


def compute_next_run(now: datetime.datetime | None = None) -> datetime.datetime | None:
    """Return the next scheduled run as an aware datetime, or None when the schedule is disabled."""
    if not config.get("schedule_enabled", True):
        return None
    now = now or datetime.datetime.now(app_timezone)
    try:
        hour, minute = [int(x) for x in str(config.get("time", "03:00")).split(":", 1)]
    except Exception:
        hour, minute = 3, 0
    freq = config.get("frequency", "daily")

    def at(day: datetime.date) -> datetime.datetime:
        return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=app_timezone)

    today = now.date()
    if freq == "weekly":
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        target = day_map.get(config.get("day_of_week", "mon"), 0)
        for offset in range(0, 8):
            candidate = at(today + datetime.timedelta(days=offset))
            if candidate.weekday() == target and candidate > now:
                return candidate
        return None
    if freq == "monthly":
        try:
            dom = max(1, min(31, int(config.get("day_of_month", 1))))
        except Exception:
            dom = 1
        year, month = today.year, today.month
        for _ in range(0, 3):
            last_day = calendar.monthrange(year, month)[1]
            candidate = at(datetime.date(year, month, min(dom, last_day)))
            if candidate > now:
                return candidate
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return None
    # daily
    candidate = at(today)
    if candidate <= now:
        candidate = at(today + datetime.timedelta(days=1))
    return candidate


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


_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_TLS_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _sanitize_label(value) -> str:
    """Return a safe Docker label key or empty string (= protection disabled)."""
    value = (str(value or "")).strip()
    return value if _LABEL_RE.match(value) else ""


def _valid_tls_name(value) -> bool:
    """TLS cert folder names are simple names below CERTS_ROOT (empty = no TLS)."""
    return value in ("", None) or bool(_TLS_NAME_RE.match(str(value)))


def prune_filters(kind: str) -> dict:
    """Build Docker prune filters honoring the protect label and minimum age.

    kind: containers | images | networks | volumes | build_cache
    """
    filters: dict = {}
    label = config.get("protect_label") or ""
    try:
        age = int(config.get("min_age_hours") or 0)
    except (TypeError, ValueError):
        age = 0
    if label and kind in ("containers", "images", "networks", "volumes"):
        filters["label!"] = [label]
    if age > 0 and kind in ("containers", "images", "networks", "build_cache"):
        filters["until"] = f"{age}h"
    return filters


def _is_protected(labels: dict | None) -> bool:
    label = config.get("protect_label") or ""
    return bool(label) and bool(labels) and label in labels


def _old_enough(created) -> bool:
    try:
        age = int(config.get("min_age_hours") or 0)
    except (TypeError, ValueError):
        age = 0
    if age <= 0:
        return True
    dt = parse_docker_time(created)
    if dt is None:
        return True
    return dt <= datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=age)


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
        "schedule_enabled": config.get("schedule_enabled", True),
        "frequency": freq,
        "time": config.get("time"),
        "prune_containers": config.get("prune_containers"),
        "prune_images": config.get("prune_images"),
        "prune_networks": config.get("prune_networks"),
        "prune_volumes": config.get("prune_volumes"),
        "prune_build_cache": config.get("prune_build_cache"),
        "protect_label": config.get("protect_label"),
        "min_age_hours": config.get("min_age_hours"),
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
            
            # Ensure all provider subkeys exist (forward compatibility for new providers like telegram)
            for provider_key in ["gotify", "ntfy", "discord", "telegram"]:
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
                if not _valid_tls_name(host.get("tls", "")):
                    host["tls"] = ""

            # Validate exclusion settings
            merged["protect_label"] = _sanitize_label(merged.get("protect_label", ""))
            try:
                merged["min_age_hours"] = max(0, min(87600, int(merged.get("min_age_hours") or 0)))
            except (TypeError, ValueError):
                merged["min_age_hours"] = 0

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


def config_dir_writable() -> bool:
    """True when PruneMate can create files in the config directory."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=str(CONFIG_PATH.parent), prefix=".write-test-", delete=True):
            pass
        return True
    except Exception:
        return False


CONFIG_WRITE_HELP = (
    f"PruneMate cannot write to {CONFIG_PATH.parent}. Settings, statistics and history will NOT be saved. "
    "Fix: make the host directory writable for root (e.g. 'sudo chown -R root:root ./config ./logs'), "
    "or remove 'cap_drop: ALL' from your compose file."
)
SAVE_FAILED_MESSAGE = f"Could not save: {CONFIG_PATH.parent} is not writable. Check the logs for how to fix this."


def save_config() -> bool:
    """Atomic save with fsync and restricted permissions. Returns True on success."""
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
                return True
            finally:
                # cleanup leftover temp if any
                if tmp_path and tmp_path.exists() and tmp_path != path:
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
        except Exception as e:
            log(f"ERROR: Failed to save config to {CONFIG_PATH}: {e}")
            return False


USER_AGENT = f"PruneMate/{APP_VERSION} (Docker cleanup bot)"


def _is_http_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except Exception:
        return False


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
    
    if not _is_http_url(url):
        log("Gotify URL must start with http:// or https://; skipping.")
        return False
    # Send the token as a header (not in the query string) so it doesn't end up in proxy/access logs
    endpoint = url.rstrip("/") + "/message"
    payload = json.dumps({"title": title, "message": message, "priority": gotify_priority}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json", "X-Gotify-Key": token, "User-Agent": USER_AGENT},
        method="POST",
    )
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
    
    if not _is_http_url(url):
        log("ntfy URL must start with http:// or https://; skipping.")
        return False

    # Parse URL to extract authentication
    parsed = urllib.parse.urlparse(url)
    headers = {"Title": title, "Priority": str(ntfy_priority), "Content-Type": "text/plain", "User-Agent": USER_AGENT}
    
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
        log("Invalid Discord webhook URL format (must start with https://discord.com/api/webhooks/).")
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
        "User-Agent": USER_AGENT
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
    full_message = f"<b>{html.escape(title)}</b>\n\n{html.escape(message)}"
    
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
            "User-Agent": USER_AGENT
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
    log(f"Unknown notification provider '{provider}'; skipping.")
    return False


def _tls_config_for(tls_name: str):
    """Build a TLSConfig from CERTS_ROOT/<tls_name>/{ca,cert,key}.pem (mutual TLS, verified)."""
    if not tls_name:
        return None
    if not _valid_tls_name(tls_name):
        raise ValueError("Invalid TLS folder name")
    # The name regex forbids '/' and leading dots, so the folder is always a direct child
    # of CERTS_ROOT. Symlinked folders (e.g. Kubernetes secret mounts) are allowed.
    folder = CERTS_ROOT / tls_name
    if folder.parent != CERTS_ROOT:
        raise ValueError("TLS folder must be a direct child of the certificates directory")
    ca, cert, key = folder / "ca.pem", folder / "cert.pem", folder / "key.pem"
    missing = [p.name for p in (ca, cert, key) if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing TLS file(s) in {folder}: {', '.join(missing)}")
    return docker.tls.TLSConfig(client_cert=(str(cert), str(key)), ca_cert=str(ca), verify=True)


def _is_local_entry(host: dict) -> bool:
    return host.get("name") == "Local" or "unix://" in (host.get("url") or "")


def local_host_entry() -> dict:
    return {"name": "Local", "url": LOCAL_DOCKER_URL, "enabled": True, "tls": ""}


def get_all_hosts(include_disabled: bool = False) -> list:
    """Return the Local host followed by the configured external hosts."""
    external = [
        h for h in config.get("docker_hosts", [])
        if not _is_local_entry(h) and (include_disabled or h.get("enabled", True))
    ]
    return [local_host_entry()] + external


def create_docker_client(host_url: str, tls_name: str = ""):
    """Create a Docker client for the given host URL.

    Args:
        host_url: Docker host URL (e.g., 'unix:///var/run/docker.sock', 'tcp://host:2375')
        tls_name: optional folder name below CERTS_ROOT with ca.pem/cert.pem/key.pem

    Returns:
        Docker client instance or None on failure
    """
    if docker is None:
        log("Docker SDK not available.")
        return None

    try:
        if host_url.startswith("unix://"):
            return docker.DockerClient(base_url=host_url)
        if host_url.startswith(("tcp://", "http://", "https://")):
            tls = _tls_config_for(tls_name) if tls_name else False
            client = docker.DockerClient(base_url=host_url, timeout=5, tls=tls)
            try:
                if not client.ping():
                    raise ConnectionError("Docker host did not respond to ping")
            except Exception:
                client.close()
                raise
            client.api.timeout = 60
            return client
        log(f"Unsupported Docker host URL scheme: {host_url}")
        return None
    except Exception as e:
        log(f"Failed to create Docker client for {host_url}: {e}")
        return None


def _empty_preview(host_name: str, error: str) -> dict:
    return {
        "name": host_name,
        "success": False,
        "error": error,
        "containers": [],
        "images": [],
        "networks": [],
        "volumes": [],
        "build_cache": [],
        "protected": 0,
    }


def _preview_host(client, host_name: str) -> dict:
    """Collect what would be pruned on one host, honoring label/age exclusions."""
    containers_list, images_list, networks_list, volumes_list, build_cache_list = [], [], [], [], []
    protected = 0

    all_containers = []
    if any(config.get(k) for k in ("prune_containers", "prune_images", "prune_networks", "prune_volumes")):
        try:
            all_containers = client.containers.list(all=True)
        except Exception as e:
            log(f"[{host_name}] Error listing containers: {e}")

    # Containers: stopped (exited, dead, created) - matches containers.prune()
    if config.get("prune_containers"):
        for c in all_containers:
            if c.status not in ("exited", "dead", "created"):
                continue
            if _is_protected(c.labels) or not _old_enough(c.attrs.get("Created")):
                protected += 1
                continue
            containers_list.append({"id": c.short_id, "name": c.name, "status": c.status})

    # Images: all images not used by any container - matches images.prune(dangling=False)
    if config.get("prune_images"):
        try:
            used_image_ids = {c.attrs.get("Image") for c in all_containers if c.attrs.get("Image")}
            for img in client.images.list():
                if img.id in used_image_ids:
                    continue
                if _is_protected(img.labels) or not _old_enough(img.attrs.get("Created")):
                    protected += 1
                    continue
                images_list.append({
                    "id": img.short_id,
                    "tags": img.tags[:3] if img.tags else ["<none>"],
                    "size": human_bytes(img.attrs.get("Size", 0)),
                })
        except Exception as e:
            log(f"[{host_name}] Error listing images: {e}")

    # Networks: custom networks not referenced by any container
    if config.get("prune_networks"):
        try:
            used_network_ids = set()
            for c in all_containers:
                for net_info in (c.attrs.get("NetworkSettings", {}) or {}).get("Networks", {}).values():
                    if net_info.get("NetworkID"):
                        used_network_ids.add(net_info["NetworkID"])
            for net in client.networks.list():
                if net.name in ("bridge", "host", "none", "ingress", "docker_gwbridge") or net.id in used_network_ids:
                    continue
                if _is_protected(net.attrs.get("Labels")) or not _old_enough(net.attrs.get("Created")):
                    protected += 1
                    continue
                networks_list.append({"id": net.short_id, "name": net.name})
        except Exception as e:
            log(f"[{host_name}] Error listing networks: {e}")

    # Volumes: all volumes (incl. named) not mounted by any container
    if config.get("prune_volumes"):
        try:
            used_volume_names = set()
            for c in all_containers:
                for mount in c.attrs.get("Mounts", []) or []:
                    if mount.get("Type") == "volume":
                        used_volume_names.add(mount.get("Name"))
            for v in client.volumes.list() or []:
                if v.name in used_volume_names:
                    continue
                if _is_protected(v.attrs.get("Labels")):
                    protected += 1
                    continue
                volumes_list.append({"name": v.name, "driver": v.attrs.get("Driver", "local")})
        except Exception as e:
            log(f"[{host_name}] Error listing volumes: {e}")

    # Build cache: reclaimable entries from df()
    if config.get("prune_build_cache"):
        try:
            for c in client.api.df().get("BuildCache", []) or []:
                # Match `docker builder prune` (without --all): entries in use or shared with
                # an existing image are kept, so don't list them as "will be pruned".
                if c.get("InUse") or c.get("Shared") or c.get("Reclaimable") is False:
                    continue
                if not _old_enough(c.get("LastUsedAt") or c.get("CreatedAt")):
                    protected += 1
                    continue
                build_cache_list.append({
                    "id": (c.get("ID") or "")[:12],
                    "type": c.get("Type", "unknown"),
                    "size": human_bytes(c.get("Size", 0)),
                })
        except Exception as e:
            log(f"[{host_name}] Error listing build cache: {e}")

    return {
        "name": host_name,
        "success": True,
        "containers": containers_list,
        "images": images_list,
        "networks": networks_list,
        "volumes": volumes_list,
        "build_cache": build_cache_list,
        "protected": protected,
        "totals": {
            "containers": len(containers_list),
            "images": len(images_list),
            "networks": len(networks_list),
            "volumes": len(volumes_list),
            "build_cache": len(build_cache_list),
        },
    }


def _any_prune_option() -> bool:
    return any(config.get(k) for k in (
        "prune_containers", "prune_images", "prune_networks", "prune_volumes", "prune_build_cache"
    ))


def get_prune_preview() -> dict:
    """Get a preview of what would be pruned without actually pruning.

    Returns a dict with preview results per host and aggregate totals.
    """
    load_config(silent=True)

    if not _any_prune_option():
        return {"error": "No prune options selected", "hosts": []}
    if docker is None:
        return {"error": "Docker SDK not available", "hosts": []}

    preview_results = []
    totals = {"containers": 0, "images": 0, "networks": 0, "volumes": 0, "build_cache": 0}
    protected_total = 0

    for host in get_all_hosts():
        host_name = host.get("name", "Unnamed")
        client = None
        try:
            client = create_docker_client(host.get("url", LOCAL_DOCKER_URL), host.get("tls", ""))
            if client is None:
                preview_results.append(_empty_preview(host_name, "Failed to connect"))
                continue
            result = _preview_host(client, host_name)
            for key in totals:
                totals[key] += result["totals"][key]
            protected_total += result["protected"]
            preview_results.append(result)
        except Exception as e:
            log(f"[{host_name}] Error getting preview: {e}")
            preview_results.append(_empty_preview(host_name, "Unexpected error while building preview (see logs)"))
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    return {
        "hosts": preview_results,
        "totals": totals,
        "protected": protected_total,
        "protect_label": config.get("protect_label") or "",
        "min_age_hours": config.get("min_age_hours") or 0,
    }


def _prune_host(client, host_name: str, allow_volumes: bool = True) -> dict:
    """Run the enabled prune operations on one host and return the counts.

    allow_volumes=False skips volume pruning even when enabled (manual run without confirmation).
    """
    result = {"containers": 0, "images": 0, "networks": 0, "volumes": 0, "build_cache": 0, "space": 0}

    if config.get("prune_containers"):
        try:
            log(f"[{host_name}] Pruning containers…")
            r = client.containers.prune(filters=prune_filters("containers") or None)
            log(f"[{host_name}] Containers prune result: {r}")
            result["containers"] = len(r.get("ContainersDeleted") or [])
            result["space"] += int(r.get("SpaceReclaimed") or 0)
        except Exception as e:
            log(f"[{host_name}] Error pruning containers: {e}")

    if config.get("prune_images"):
        try:
            log(f"[{host_name}] Pruning images (all unused)…")
            r = client.images.prune(filters={"dangling": False, **prune_filters("images")})
            log(f"[{host_name}] Images prune result: {r}")
            result["images"] = len(r.get("ImagesDeleted") or [])
            result["space"] += int(r.get("SpaceReclaimed") or 0)
        except Exception as e:
            log(f"[{host_name}] Error pruning images: {e}")

    if config.get("prune_networks"):
        try:
            log(f"[{host_name}] Pruning networks…")
            r = client.networks.prune(filters=prune_filters("networks") or None)
            log(f"[{host_name}] Networks prune result: {r}")
            result["networks"] = len(r.get("NetworksDeleted") or [])
        except Exception as e:
            log(f"[{host_name}] Error pruning networks: {e}")

    if config.get("prune_volumes") and not allow_volumes:
        log(f"[{host_name}] Skipping volumes: manual run without DELETE confirmation.")
    elif config.get("prune_volumes"):
        try:
            log(f"[{host_name}] Pruning volumes (all unused, including named)…")
            r = client.volumes.prune(filters={"all": True, **prune_filters("volumes")})
            log(f"[{host_name}] Volumes prune result: {r}")
            result["volumes"] = len(r.get("VolumesDeleted") or [])
            result["space"] += int(r.get("SpaceReclaimed") or 0)
        except Exception as e:
            log(f"[{host_name}] Error pruning volumes: {e}")

    if config.get("prune_build_cache"):
        try:
            log(f"[{host_name}] Pruning build cache…")
            f = prune_filters("build_cache")
            try:
                r = client.api.prune_builds(filters=f) if f else client.api.prune_builds()
            except TypeError:
                # Older Docker SDKs don't accept filters for builder prune
                r = client.api.prune_builds()
            log(f"[{host_name}] Build cache prune result: {r}")
            result["build_cache"] = len(r.get("CachesDeleted") or [])
            result["space"] += int(r.get("SpaceReclaimed") or 0)
        except Exception as e:
            log(f"[{host_name}] Error pruning build cache: {e}")

    return result


def _build_summary(all_hosts: list, host_results: list, totals: dict, anything_deleted: bool) -> str:
    """Build the notification text with a per-host breakdown."""
    labels = (
        ("containers", "Containers"), ("images", "Images"), ("networks", "Networks"),
        ("volumes", "Volumes"), ("build_cache", "Build caches"),
    )
    lines = [f"Schedule: {describe_schedule()}", ""]
    if len(all_hosts) > 1:
        lines.append("Results by host")

    hosts_with_deletions = sum(
        1 for r in host_results if r.get("success") and any(r.get(k) for k, _ in labels)
    )

    for r in host_results:
        if not r.get("success"):
            lines.append(f"{r['name']}: Error - {r.get('error', 'Unknown error')}")
            continue
        if any(r.get(k) for k, _ in labels):
            lines.append(r["name"])
            for key, label in labels:
                if r.get(key):
                    lines.append(f"  - {label}: {r[key]}")
            if r.get("space"):
                lines.append(f"  - Space reclaimed: {human_bytes(r['space'])}")
        else:
            lines.append(f"{r['name']}: Nothing to prune")

    if len(all_hosts) > 1 and hosts_with_deletions != 1:
        lines.append("")
        lines.append("Totals across all hosts")
        if anything_deleted:
            for key, label in labels:
                if totals.get(key):
                    lines.append(f"  - {label}: {totals[key]}")
            if totals.get("space"):
                lines.append(f"  - Space reclaimed: {human_bytes(totals['space'])}")
        else:
            lines.append("Nothing to prune in this run")
    return "\n".join(lines)


def run_prune_job(origin: str = "unknown", wait: bool = False, allow_volumes: bool = True):
    """Execute Docker pruning based on current configuration.

    Returns the run record (dict, truthy) when the job ran, or False when it was skipped.
    """
    # Ensure we have the latest config before running prune job
    load_config(silent=True)

    lock = FileLock(str(LOCK_FILE))
    acquired = False
    try:
        if wait:
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

        if not _any_prune_option():
            log("No prune options selected. Job skipped.")
            return False
        if docker is None:
            log("Docker SDK not available; aborting prune.")
            return False

        started = time.monotonic()
        all_hosts = get_all_hosts()
        log(f"Processing {len(all_hosts)} host(s) (1 local + {len(all_hosts) - 1} external)...")

        totals = {"containers": 0, "images": 0, "networks": 0, "volumes": 0, "build_cache": 0, "space": 0}
        host_results = []

        for host in all_hosts:
            host_name = host.get("name", "Unnamed")
            host_url = host.get("url", LOCAL_DOCKER_URL)
            log(f"--- Processing host: {host_name} ({host_url}) ---")

            client = None
            try:
                client = create_docker_client(host_url, host.get("tls", ""))
                if client is None:
                    log(f"Failed to connect to {host_name}; skipping this host.")
                    host_results.append({"name": host_name, "success": False, "error": "Failed to connect",
                                         **{k: 0 for k in totals}})
                    continue

                result = _prune_host(client, host_name, allow_volumes=allow_volumes)
                log(f"[{host_name}] Prune completed: containers={result['containers']}, images={result['images']}, "
                    f"networks={result['networks']}, volumes={result['volumes']}, "
                    f"build_cache={result['build_cache']}, space={human_bytes(result['space'])}")
                for key in totals:
                    totals[key] += result[key]
                host_results.append({"name": host_name, "success": True, **result})
            except Exception as e:
                log(f"[{host_name}] Unexpected error during prune: {e}")
                host_results.append({"name": host_name, "success": False, "error": "Unexpected error (see logs)",
                                     **{k: 0 for k in totals}})
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass

        log("Prune job finished for all hosts.")

        anything_deleted = any(totals[k] for k in ("containers", "images", "networks", "volumes", "build_cache")) \
            or totals["space"] > 0

        # Update all-time statistics (always, even if nothing was pruned)
        update_stats(
            containers=totals["containers"],
            images=totals["images"],
            networks=totals["networks"],
            volumes=totals["volumes"],
            build_cache=totals["build_cache"],
            space=totals["space"],
        )

        record = {
            "timestamp": datetime.datetime.now(app_timezone).isoformat(timespec="seconds"),
            "origin": origin,
            "duration": round(time.monotonic() - started, 1),
            "success": all(r.get("success") for r in host_results),
            "totals": totals,
            "hosts": host_results,
        }
        append_history(record)

        # Respect only_on_changes for notifications
        if not anything_deleted and config.get("notifications", {}).get("only_on_changes", True):
            log("Nothing was pruned; skipping notification.")
            return record

        message = _build_summary(all_hosts, host_results, totals, anything_deleted)
        notif_priority = config.get("notifications", {}).get("priority", "medium")
        send_notification("PruneMate run completed", message, priority=notif_priority)

        return record

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
    
    # Check if schedule is enabled
    if not config.get("schedule_enabled", True):
        return
    
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


# ---- Authentication Logic ----
def is_auth_enabled():
    """Check if authentication is enabled via environment variables."""
    # Only enabled if hash is present
    return bool(os.environ.get("PRUNEMATE_AUTH_PASSWORD_HASH"))


def _decoded_password_hash(raw: str) -> str:
    """Accept both Base64-encoded (recommended) and raw Werkzeug hashes."""
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8")
    except Exception:
        return raw


def check_auth(username, password):
    """Verify username and password against environment variables.

    Both the username and the password are always checked so the response time
    doesn't reveal whether the username was correct.
    """
    expected_user = os.environ.get("PRUNEMATE_AUTH_USER", "admin")
    password_hash = os.environ.get("PRUNEMATE_AUTH_PASSWORD_HASH")

    if not password_hash or username is None or password is None:
        return False

    user_ok = hmac.compare_digest(str(username).encode("utf-8"), expected_user.encode("utf-8"))
    try:
        password_ok = check_password_hash(_decoded_password_hash(password_hash), str(password))
    except Exception:
        password_ok = False
    return user_ok and password_ok


class LoginRateLimiter:
    """In-memory sliding-window limiter for failed logins (per client IP)."""

    def __init__(self, max_attempts: int, window_seconds: int, lockout_seconds: int):
        self.max_attempts = max(1, max_attempts)
        self.window = window_seconds
        self.lockout = lockout_seconds
        self._failures: dict[str, deque] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked_for(self, key: str) -> float:
        """Seconds until the key is unlocked (0 when not locked)."""
        with self._lock:
            until = self._locked_until.get(key, 0)
            remaining = until - time.monotonic()
            if remaining <= 0:
                self._locked_until.pop(key, None)
                return 0
            return remaining

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            # Bound memory usage under a flood of distinct IPs
            if len(self._failures) > 10000:
                self._failures.clear()
            q = self._failures.setdefault(key, deque())
            q.append(now)
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout
                q.clear()

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


login_limiter = LoginRateLimiter(
    max_attempts=_env_int("PRUNEMATE_LOGIN_MAX_ATTEMPTS", 5, minimum=1),
    window_seconds=300,
    lockout_seconds=60 * _env_int("PRUNEMATE_LOGIN_LOCKOUT_MINUTES", 5, minimum=1),
)


def _client_ip() -> str:
    return request.remote_addr or "unknown"


def _basic_auth_ok() -> bool:
    """Validate HTTP Basic credentials (API clients), with brute-force protection."""
    auth = request.authorization
    if not auth or auth.type != "basic":
        return False
    ip = _client_ip()
    if login_limiter.locked_for(ip):
        return False
    if check_auth(auth.username, auth.password):
        login_limiter.reset(ip)
        return True
    login_limiter.fail(ip)
    log(f"Failed Basic Auth attempt for user '{auth.username}' from {ip}")
    return False


def _api_key_state():
    """None when no API key is configured; otherwise True/False for the presented key."""
    expected = (os.environ.get("PRUNEMATE_API_KEY") or "").strip()
    if not expected:
        return None
    sent = request.headers.get("X-API-Key") or request.args.get("api_key") or ""
    return bool(sent) and hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8"))


def request_wants_json():
    """Check if client wants JSON response."""
    best = request.accept_mimetypes.best_match(['application/json', 'text/html'])
    return best == 'application/json' and request.accept_mimetypes[best] > request.accept_mimetypes['text/html']


def _is_fetch() -> bool:
    """Requests made by the PruneMate UI via fetch() carry this header."""
    return request.headers.get("X-Requested-With") == "fetch"


def _wants_json() -> bool:
    return _is_fetch() or request.is_json or request_wants_json()


def _respond(success: bool, message: str, status: int = 200, **extra):
    """JSON for the UI (fetch), flash + redirect for classic form posts."""
    if _wants_json():
        return jsonify({"success": success, "message": message, **extra}), status
    flash(message, "info" if success else "warn")
    return redirect(url_for("index"))


def _safe_next(url: str | None) -> str:
    """Only allow relative, same-origin redirect targets."""
    if not url:
        return url_for("index")
    candidate = url.replace("\\", "/").strip()
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/") or candidate.startswith("//"):
        return url_for("index")
    return candidate


PUBLIC_ENDPOINTS = {"static", "login", "healthz"}
STATS_ENDPOINTS = {"stats", "api_stats"}


@app.before_request
def require_auth():
    """Intercept requests and ensure user is authenticated."""
    endpoint = request.endpoint

    if endpoint in PUBLIC_ENDPOINTS:
        return

    # Stats endpoints (Homepage widget): open unless PRUNEMATE_API_KEY is configured
    if endpoint in STATS_ENDPOINTS:
        key_state = _api_key_state()
        if key_state is None or key_state or session.get("logged_in"):
            return
        if is_auth_enabled() and request.authorization and _basic_auth_ok():
            return
        return jsonify({"error": "Invalid or missing API key"}), 401

    if not is_auth_enabled():
        return

    if session.get("logged_in"):
        return

    # API/CLI Clients (Basic Auth)
    if request.authorization and _basic_auth_ok():
        return

    # UI fetch() calls: plain 401 (no WWW-Authenticate, so the browser doesn't pop up a dialog)
    if _is_fetch():
        return jsonify({"success": False, "message": "Session expired. Please sign in again.", "login": url_for("login")}), 401

    ua = request.user_agent.string.lower()
    is_browser = any(x in ua for x in ['mozilla', 'chrome', 'safari', 'edge']) and 'curl' not in ua and 'python' not in ua

    if not is_browser or request_wants_json() or request.path.startswith('/api/'):
        return Response(
            'Could not verify your access level for that URL.\n'
            'You have to login with proper credentials', 401,
            {'WWW-Authenticate': 'Basic realm="PruneMate Login"'}
        )

    # For Browsers: Redirect to login page
    next_path = request.full_path if request.method == "GET" and request.path != "/" else None
    return redirect(url_for("login", next=next_path) if next_path else url_for("login"))


# ---- CSRF protection ----
def csrf_token() -> str:
    """Return (and lazily create) the per-session CSRF token."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    """Reject state-changing requests without a valid CSRF token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    expected = session.get("_csrf_token")
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    if expected and sent and hmac.compare_digest(str(expected), str(sent)):
        return
    log(f"Rejected {request.method} {request.path} from {_client_ip()}: missing or invalid CSRF token")
    message = "Your session expired or the request token was invalid. Reload the page and try again."
    if _wants_json():
        return jsonify({"success": False, "message": message}), 400
    flash(message, "warn")
    return redirect(url_for("login") if request.endpoint == "login" else url_for("index"))


# ---- Security headers ----
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


@app.after_request
def set_security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"
    if request.is_secure:
        # Browsers ignore (and warn about) COOP on plain-HTTP origins
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


@app.route("/healthz")
def healthz():
    """Lightweight liveness probe for Docker HEALTHCHECK."""
    return Response("ok", mimetype="text/plain")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Handle login page and authentication."""
    if not is_auth_enabled():
        return redirect(url_for("index"))
    if session.get('logged_in'):
        return redirect(url_for('index'))

    status = 200
    if request.method == "POST":
        ip = _client_ip()
        remaining = login_limiter.locked_for(ip)
        if remaining:
            minutes = max(1, int((remaining + 59) // 60))
            flash(f"Too many failed attempts. Try again in {minutes} minute(s).", "error")
            status = 429
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if check_auth(username, password):
                login_limiter.reset(ip)
                next_url = _safe_next(request.args.get("next"))
                # Fresh session on login (new CSRF token, no leftovers)
                session.clear()
                session.permanent = True
                session['logged_in'] = True
                session['user'] = username
                session['_csrf_token'] = secrets.token_urlsafe(32)
                log(f"Successful login for user '{username}' from {ip}")
                return redirect(next_url)
            login_limiter.fail(ip)
            log(f"Failed login attempt for user '{username}' from {ip}")
            flash("Invalid credentials", "error")
            status = 401

    return render_template("login.html", app_version=APP_VERSION), status


@app.route("/logout", methods=["POST"])
def logout():
    """Clear session and redirect to login."""
    session.clear()
    return redirect(url_for("login"))


# ---- Config form helpers ----
SECRET_FIELDS = {
    "gotify_token": ("gotify", "token"),
    "ntfy_token": ("ntfy", "token"),
    "discord_webhook_url": ("discord", "webhook_url"),
    "telegram_bot_token": ("telegram", "bot_token"),
}
VALID_FREQUENCIES = {"daily", "weekly", "monthly"}
VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
VALID_PROVIDERS = {"gotify", "ntfy", "discord", "telegram"}
MASK = "********"


def _mask_url_password(url: str) -> str:
    """Hide the password part of user:pass@host URLs for display."""
    try:
        parsed = urllib.parse.urlparse(url or "")
        if not parsed.password:
            return url or ""
        netloc = f"{parsed.username or ''}:{MASK}@{parsed.hostname or ''}" + (f":{parsed.port}" if parsed.port else "")
        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return url or ""


def _secret_from_form(field: str, old_value: str) -> str:
    """Empty secret fields keep the stored value; '<field>_clear=1' removes it."""
    if request.form.get(f"{field}_clear") == "1":
        return ""
    new = (request.form.get(field) or "").strip()
    return new if new else (old_value or "")


def _parse_time_from_form() -> str:
    if use_24h_format:
        return validate_time(request.form.get("time", "03:00"))
    try:
        hour_12 = max(1, min(12, int(request.form.get("time_hour", "3"))))
        minute = max(0, min(59, int(request.form.get("time_minute", "0"))))
        period = request.form.get("time_period", "AM")
        if period == "AM":
            hour_24 = 0 if hour_12 == 12 else hour_12
        else:
            hour_24 = 12 if hour_12 == 12 else hour_12 + 12
        return f"{hour_24:02d}:{minute:02d}"
    except Exception:
        return "03:00"


def _config_from_form(old: dict) -> tuple[dict, list]:
    """Parse and validate the main settings form. Returns (new_values, errors)."""
    errors = []
    old_notif = old.get("notifications", {})

    frequency = request.form.get("frequency", "daily")
    if frequency not in VALID_FREQUENCIES:
        frequency = "daily"
    day_of_week = request.form.get("day_of_week", "mon")
    if day_of_week not in VALID_DAYS:
        day_of_week = "mon"
    try:
        day_of_month = max(1, min(31, int(request.form.get("day_of_month", "1"))))
    except Exception:
        day_of_month = 1

    # Exclusions
    protect_label = ""
    if "protect_label_enabled" in request.form:
        raw_label = (request.form.get("protect_label") or "").strip() or "prunemate.keep"
        protect_label = _sanitize_label(raw_label)
        if not protect_label:
            errors.append("Protect label may only contain letters, digits, '.', '_', '-' and '/'.")
    try:
        min_age_hours = max(0, min(87600, int(request.form.get("min_age_hours") or 0)))
    except (TypeError, ValueError):
        min_age_hours = 0

    provider = request.form.get("notifications_provider", "gotify")
    if provider not in VALID_PROVIDERS:
        provider = "gotify"

    def old_val(provider_key, key):
        return (old_notif.get(provider_key) or {}).get(key, "")

    gotify_url = (request.form.get("gotify_url") or "").strip()
    ntfy_url = (request.form.get("ntfy_url") or "").strip()
    old_ntfy_url = old_val("ntfy", "url")
    if old_ntfy_url and ntfy_url == _mask_url_password(old_ntfy_url):
        ntfy_url = old_ntfy_url  # unchanged masked value -> keep stored credentials

    for label, url in (("Gotify URL", gotify_url), ("ntfy URL", ntfy_url)):
        if url and not _is_http_url(url):
            errors.append(f"{label} must start with http:// or https://")

    secrets_new = {field: _secret_from_form(field, old_val(*path)) for field, path in SECRET_FIELDS.items()}
    discord_webhook_url = secrets_new["discord_webhook_url"]
    if discord_webhook_url and not discord_webhook_url.startswith(
            ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        errors.append("Discord webhook URL must start with https://discord.com/api/webhooks/")

    gotify_enabled = "gotify_enabled" in request.form
    ntfy_enabled = "ntfy_enabled" in request.form
    ntfy_topic = (request.form.get("ntfy_topic") or "").strip()
    discord_enabled = "discord_enabled" in request.form
    telegram_enabled = "telegram_enabled" in request.form
    telegram_chat_id = (request.form.get("telegram_chat_id") or "").strip()

    # Auto-enable selected provider if fields are filled but toggle was forgotten
    if provider == "gotify" and not gotify_enabled and gotify_url and secrets_new["gotify_token"]:
        gotify_enabled = True
    if provider == "ntfy" and not ntfy_enabled and ntfy_url and ntfy_topic:
        ntfy_enabled = True
    if provider == "discord" and not discord_enabled and discord_webhook_url:
        discord_enabled = True
    if provider == "telegram" and not telegram_enabled and secrets_new["telegram_bot_token"] and telegram_chat_id:
        telegram_enabled = True

    notification_priority = (request.form.get("notification_priority") or "medium").strip().lower()
    if notification_priority not in ("low", "medium", "high"):
        notification_priority = "medium"

    new_values = {
        "schedule_enabled": "schedule_enabled" in request.form,
        "frequency": frequency,
        "time": _parse_time_from_form(),
        "day_of_week": day_of_week,
        "day_of_month": day_of_month,
        "prune_containers": "prune_containers" in request.form,
        "prune_images": "prune_images" in request.form,
        "prune_networks": "prune_networks" in request.form,
        "prune_volumes": "prune_volumes" in request.form,
        "prune_build_cache": "prune_build_cache" in request.form,
        "protect_label": protect_label,
        "min_age_hours": min_age_hours,
        "notifications": {
            "provider": provider,
            "gotify": {"enabled": gotify_enabled, "url": gotify_url, "token": secrets_new["gotify_token"]},
            "ntfy": {"enabled": ntfy_enabled, "url": ntfy_url, "topic": ntfy_topic, "token": secrets_new["ntfy_token"]},
            "discord": {"enabled": discord_enabled, "webhook_url": discord_webhook_url},
            "telegram": {"enabled": telegram_enabled, "bot_token": secrets_new["telegram_bot_token"], "chat_id": telegram_chat_id},
            "priority": notification_priority,
            "only_on_changes": "notifications_only_on_changes" in request.form,
        },
    }
    return new_values, errors


def with_config_lock(func):
    """Run a read-modify-write of the config atomically w.r.t. other threads.

    load_config() replaces the global config object; without the lock another request
    (dashboard poll, heartbeat) could swap it between update and save, losing changes.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with config_lock:
            return func(*args, **kwargs)
    return wrapper


@with_config_lock
def _save_form_config() -> list:
    """Apply the submitted settings form. Returns a list of validation errors (empty on success)."""
    load_config(silent=True)
    old_config = json.loads(json.dumps(config))
    new_values, errors = _config_from_form(old_config)
    if errors:
        return errors

    schedule_keys = [
        "schedule_enabled", "frequency", "time", "day_of_week", "day_of_month",
        "prune_containers", "prune_images", "prune_networks", "prune_volumes", "prune_build_cache",
    ]
    schedule_changed = any(new_values[k] != old_config.get(k) for k in schedule_keys)
    config.update(new_values)
    if schedule_changed:
        # Clear last-run key so the next scheduled window can fire
        _clear_last_run_key()
    if not save_config():
        return [SAVE_FAILED_MESSAGE]
    return []


def _schedule_payload() -> dict:
    next_run = compute_next_run()
    return {
        "next_run": next_run.isoformat() if next_run else None,
        "schedule_text": describe_schedule(),
        "schedule_enabled": bool(config.get("schedule_enabled", True)),
    }


@app.route("/")
def index():
    """Render the main configuration page."""
    # Reload config to ensure we show the latest settings across workers
    load_config(silent=True)
    notif = config.get("notifications", {})
    secret_state = {field: bool((notif.get(p) or {}).get(k)) for field, (p, k) in SECRET_FIELDS.items()}
    return render_template(
        "index.html",
        config=config,
        timezone=tz_name,
        use_24h=use_24h_format,
        app_version=APP_VERSION,
        auth_enabled=is_auth_enabled(),
        secret_state=secret_state,
        ntfy_url_display=_mask_url_password((notif.get("ntfy") or {}).get("url", "")),
        config_writable=config_dir_writable(),
        config_dir=str(CONFIG_PATH.parent),
        current_year=datetime.datetime.now(app_timezone).year,
    )


@app.route("/update", methods=["POST"])
def update():
    """Handle configuration updates from the web form."""
    errors = _save_form_config()
    if errors:
        return _respond(False, " ".join(errors), 400)
    return _respond(True, "Configuration saved.", **_schedule_payload())


PRUNE_KEYS = ("prune_containers", "prune_images", "prune_networks", "prune_volumes", "prune_build_cache")


def _apply_prune_selection(data: dict, context: str) -> None:
    """Persist the prune toggles sent by the UI (atomic w.r.t. other config writers)."""
    with config_lock:
        load_config(silent=True)
        if any(k in data for k in PRUNE_KEYS):
            for key in PRUNE_KEYS:
                config[key] = bool(data.get(key, False))
            save_config()
            log(f"{context} with updated prune settings saved.")


@app.route("/preview-prune", methods=["POST"])
def preview_prune():
    """Get a preview of what would be pruned without executing."""
    _apply_prune_selection(request.get_json(silent=True) or {}, "Preview requested")
    log("Prune preview requested.")
    return jsonify(get_prune_preview())


@app.route("/run-confirmed", methods=["POST"])
def run_confirmed():
    """Execute prune after user has seen and confirmed the preview."""
    data = request.get_json(silent=True) or {}
    _apply_prune_selection(data, "Confirmed run")

    # Volume pruning deletes named volumes (databases!): a manual run only touches volumes
    # when the user typed DELETE in the preview. Otherwise volumes are skipped for this run.
    allow_volumes = str(data.get("confirm_volumes") or "") == "DELETE"
    if config.get("prune_volumes") and not allow_volumes:
        if not any(config.get(k) for k in PRUNE_KEYS if k != "prune_volumes"):
            return jsonify({"success": False, "message": "Volume pruning requires typing DELETE to confirm."}), 400
        log("Volumes are enabled but not confirmed; this manual run skips volumes.")

    log("Confirmed manual run trigger received.")
    record = run_prune_job(origin="manual", wait=True, allow_volumes=allow_volumes)
    return jsonify({
        "success": bool(record),
        "message": "Prune job executed successfully." if record else "Prune job skipped (busy, nothing selected or timeout).",
        "run": record or None,
    })


@app.route("/test-notification", methods=["POST"])
def test_notification():
    """Save configuration and send a test notification."""
    errors = _save_form_config()
    if errors:
        return _respond(False, " ".join(errors), 400)

    log("Notification test requested from UI.")
    test_priority = config.get("notifications", {}).get("priority", "medium")
    ok = send_notification(
        "PruneMate test notification",
        "This is a test message from PruneMate.\n\nIf you see this, your current provider settings are working.",
        priority=test_priority,
    )
    return _respond(
        ok,
        "Configuration saved. " + ("Test notification sent." if ok else "Test notification failed (check settings & logs)."),
        200,
        **_schedule_payload(),
    )


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


@app.route("/api/dashboard")
def api_dashboard():
    """Everything the UI dashboard needs in one call (requires normal auth)."""
    load_config(silent=True)
    return jsonify({
        "stats": load_stats(),
        "history": load_history()[-30:],
        "now": datetime.datetime.now(app_timezone).isoformat(timespec="seconds"),
        **_schedule_payload(),
    })


_LOG_REDACTIONS = [
    # 'bot_token': '...' / "webhook_url": "..." in logged config dicts
    (re.compile(r"""(['"](?:\w*token|webhook_url|password|secret|api_key)['"]\s*:\s*['"])[^'"]+(['"])""", re.IGNORECASE),
     r"\1***\2"),
    # Discord webhook URLs anywhere in a line (e.g. V1.x error messages)
    (re.compile(r"(https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/)\S+", re.IGNORECASE), r"\1***"),
    # Telegram bot tokens (api.telegram.org/bot<id>:<secret>)
    (re.compile(r"\bbot(\d+):[A-Za-z0-9_-]{20,}"), r"bot\1:***"),
    # user:password@host in URLs
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@", re.IGNORECASE), r"\1***@"),
]


def redact_log_line(line: str) -> str:
    """Mask secrets in a log line (also for lines written by older PruneMate versions)."""
    for pattern, replacement in _LOG_REDACTIONS:
        line = pattern.sub(replacement, line)
    return line


def _tail_file(path: Path, lines: int, max_bytes: int = 1024 * 1024) -> list:
    """Return the last `lines` lines of a file without reading it completely."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read().decode("utf-8", errors="replace")
    result = data.splitlines()
    if size > max_bytes and result:
        result = result[1:]  # first line is probably partial
    return result[-lines:]


@app.route("/api/logs")
def api_logs():
    """Return the last lines of the PruneMate log (secrets redacted)."""
    try:
        lines = max(50, min(2000, int(request.args.get("lines", 300))))
    except (TypeError, ValueError):
        lines = 300
    if not LOG_FILE.exists():
        return jsonify({"lines": [], "message": f"No log file found at {LOG_FILE}"})
    try:
        tail = [redact_log_line(line) for line in _tail_file(LOG_FILE, lines)]
        return jsonify({"lines": tail})
    except Exception as e:
        log(f"Error reading log file: {e}")
        return jsonify({"lines": [], "message": "Could not read log file"}), 500


# ---- Docker hosts ----
def _public_host(host: dict) -> dict:
    return {
        "name": host.get("name", ""),
        "url": host.get("url", ""),
        "enabled": bool(host.get("enabled", True)),
        "tls": host.get("tls", "") or "",
    }


def _host_from_form() -> tuple[dict | None, str | None]:
    """Validate host fields from a form submission. Returns (host, error)."""
    name = (request.form.get("name") or "").strip()
    url = (request.form.get("url") or "").strip()
    tls_name = (request.form.get("tls") or "").strip()
    enabled = request.form.get("enabled", "on") == "on"

    if not name or not url:
        return None, "Host name and URL are required."
    if len(name) > 64:
        return None, "Host name is too long (max 64 characters)."
    if name.lower() == "local":
        return None, "The name 'Local' is reserved for the local Docker host."
    if len(url) > 512 or not url.startswith(("tcp://", "http://", "https://")):
        return None, "URL must start with tcp://, http://, or https://"
    try:
        if not urllib.parse.urlparse(url).hostname:
            return None, "URL must contain a host name or IP address."
    except ValueError:
        return None, "URL is not valid."
    if not _valid_tls_name(tls_name):
        return None, "TLS folder may only contain letters, digits, '.', '_' and '-'."
    return {"name": name, "url": url, "enabled": enabled, "tls": tls_name}, None


@app.route("/hosts")
def list_hosts():
    """Return list of Docker hosts as JSON (includes Local for display)."""
    load_config(silent=True)
    external_hosts = [_public_host(h) for h in config.get("docker_hosts", [])]
    return jsonify({"hosts": [_public_host(local_host_entry())] + external_hosts})


@app.route("/hosts/status")
def get_host_status():
    """Check enabled external Docker hosts and return their connection status."""
    load_config(silent=True)
    configured_hosts = config.get("docker_hosts", [])
    external_hosts = [
        (index, host) for index, host in enumerate(configured_hosts)
        if not _is_local_entry(host)
    ]

    def check_host(index, host):
        if not host.get("enabled", True):
            return {"index": index, "status": "disabled"}

        host_url = (host.get("url") or "").strip()
        if not host_url.startswith(("tcp://", "http://", "https://")):
            return {"index": index, "status": "offline"}

        client = create_docker_client(host_url, host.get("tls", ""))
        if client is None:
            return {"index": index, "status": "offline"}

        try:
            return {"index": index, "status": "online"}
        finally:
            client.close()

    if not external_hosts:
        return jsonify({"statuses": []})

    with ThreadPoolExecutor(max_workers=min(8, len(external_hosts))) as executor:
        statuses = list(executor.map(
            check_host,
            (index for index, _ in external_hosts),
            (host for _, host in external_hosts),
        ))

    return jsonify({"statuses": statuses})


@app.route("/hosts/add", methods=["POST"])
@with_config_lock
def add_host():
    """Add a new Docker host."""
    load_config(silent=True)
    new_host, error = _host_from_form()
    if error:
        return _respond(False, error, 400)

    config.setdefault("docker_hosts", []).append(new_host)
    if not save_config():
        return _respond(False, SAVE_FAILED_MESSAGE, 500)
    return _respond(True, f"Docker host '{new_host['name']}' added.")


@app.route("/hosts/<int:index>/update", methods=["POST"])
@with_config_lock
def update_host(index):
    """Update an existing Docker host."""
    load_config(silent=True)
    hosts = config.get("docker_hosts", [])
    if index < 0 or index >= len(hosts):
        return _respond(False, "Invalid host index.", 400)

    updated, error = _host_from_form()
    if error:
        return _respond(False, error, 400)

    hosts[index] = updated
    config["docker_hosts"] = hosts
    if not save_config():
        return _respond(False, SAVE_FAILED_MESSAGE, 500)
    return _respond(True, f"Docker host '{updated['name']}' updated.")


@app.route("/hosts/<int:index>/delete", methods=["POST"])
@with_config_lock
def delete_host(index):
    """Delete a Docker host."""
    load_config(silent=True)
    hosts = config.get("docker_hosts", [])
    if index < 0 or index >= len(hosts):
        return _respond(False, "Invalid host index.", 400)

    # Note: It's safe to delete all external hosts - Local is always available at runtime
    deleted_name = hosts[index].get("name", "Unknown")
    del hosts[index]
    config["docker_hosts"] = hosts
    if not save_config():
        return _respond(False, SAVE_FAILED_MESSAGE, 500)
    return _respond(True, f"Docker host '{deleted_name}' deleted.")


@app.route("/hosts/<int:index>/toggle", methods=["POST"])
@with_config_lock
def toggle_host(index):
    """Toggle enabled/disabled status of a Docker host."""
    load_config(silent=True)
    hosts = config.get("docker_hosts", [])
    if index < 0 or index >= len(hosts):
        return jsonify({"success": False, "message": "Invalid host index"}), 400

    hosts[index]["enabled"] = not hosts[index].get("enabled", True)
    config["docker_hosts"] = hosts
    if not save_config():
        return jsonify({"success": False, "message": SAVE_FAILED_MESSAGE}), 500

    status = "enabled" if hosts[index]["enabled"] else "disabled"
    return jsonify({"success": True, "enabled": hosts[index]["enabled"], "message": f"Host '{hosts[index].get('name')}' {status}."})


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
    # Upgrading from V1.x: rebuild the run history from existing log files (one-time)
    import_history_from_logs()
    scheduler.add_job(heartbeat, CronTrigger(second=0), id="heartbeat", max_instances=1, coalesce=True)
    log("Scheduler heartbeat job started (every minute at :00).")
    log(f"PruneMate v{APP_VERSION} - local Docker endpoint: {LOCAL_DOCKER_URL}")
    if not config_dir_writable():
        log("ERROR: " + CONFIG_WRITE_HELP)
    if not is_auth_enabled():
        log("WARNING: Authentication is disabled. Anyone who can reach this port can change settings and run prunes. "
            "Set PRUNEMATE_AUTH_PASSWORD_HASH to enable login.")
    if not os.environ.get("PRUNEMATE_API_KEY"):
        log("Note: /stats and /api/stats are public. Set PRUNEMATE_API_KEY to require a key (X-API-Key header).")

    options = {
        "bind": "0.0.0.0:8080",
        "workers": 1,
        "threads": 4,
        "timeout": 120,
        # Disable access logs (172.x request lines)
        "accesslog": None,
        "errorlog": "-",
        "loglevel": "info",
        # Gunicorn >= 25.1 opens a runtime control socket by default; not needed (and fails on read-only FS)
        "control_socket_disable": True,
    }
    StandaloneApplication(app, options).run()
