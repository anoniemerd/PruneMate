# Changelog

All notable changes to PruneMate will be documented in this file.

## [V2.0.0] - November 2026

### Security
- 🔑 **No more hard-coded session secret** - The Flask secret key used to default to a fixed value, which allowed
  forging a logged-in session cookie. A random key is now generated on first start and stored in `/config/secret_key`
  (or set `PRUNEMATE_SECRET` yourself)
- 🛡️ **CSRF protection** - All state-changing requests (save, run, preview, hosts, test notification, login, logout)
  require a per-session token. Logout is now a POST request
- 🧱 **Content-Security-Policy & security headers** - `script-src 'self'`, `style-src 'self'`, `frame-ancestors 'none'`,
  `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS over HTTPS. All inline scripts and styles were moved to
  `static/`, and the Google Fonts dependency was replaced by self-hosted fonts
- 🧼 **XSS fixes** - The prune preview no longer inserts container/image/network/volume/host names or error messages as HTML
- ⛔ **Login brute-force protection** - Temporary lockout per IP after repeated failed logins (form + Basic Auth),
  constant-time username check, sessions expire (`PRUNEMATE_SESSION_HOURS`), fresh session on login,
  optional `Secure` cookies (`PRUNEMATE_COOKIE_SECURE`) and reverse-proxy support (`PRUNEMATE_TRUSTED_PROXIES`)
- 🙈 **Secrets stay server-side** - Notification tokens and webhook URLs are no longer rendered into the page;
  ntfy URL passwords are masked. Leave a field empty to keep the stored value, or click *Remove*
- 📝 **Log redaction** - The Telegram bot token and Discord webhook URL were written to the log in plain text on every
  config save; all token/secret/webhook fields are now redacted. The log viewer redacts old log lines too
- 🔐 **Gotify token sent as header** (`X-Gotify-Key`) instead of in the URL query string
- 🔑 **Optional API key** for `/stats` and `/api/stats` (`PRUNEMATE_API_KEY`, `X-API-Key` header)
- ✅ **Stricter input validation** - Notification URLs must be http(s) (no `file://`), schedule values are whitelisted,
  host names/URLs are validated, Telegram messages are HTML-escaped, open redirect via `?next=/\evil` fixed
- 🐳 **Least privilege** - New compose file uses a docker-socket-proxy on an internal network, a read-only root
  filesystem, `no-new-privileges` and `cap_drop: ALL`. New `PRUNEMATE_DOCKER_HOST` variable
- 🔒 **TLS for remote hosts** - Mutual TLS with certificate + host name verification (`/certs/<folder>/{ca,cert,key}.pem`)
- 📦 **Pinned dependencies** (`requirements.txt`), Docker `HEALTHCHECK` (`/healthz`), gunicorn control socket disabled

### Added
- 📈 **Dashboard** - Next-run countdown, last-run status (with failed hosts), total reclaimed
- 📊 **Run history** - Bar chart of space reclaimed per run (last 30, with tooltips) and a recent-runs table;
  stored in `/config/history.json` (last 100 runs, per host). On the first start after upgrading, earlier runs
  are reconstructed once from the existing V1.x log files (marked "from V1 logs"; space values are rounded)
- 🏷️ **Protection rules** - Skip resources with a keep label (default `prunemate.keep`) and/or younger than N hours.
  Applied to scheduled runs, manual runs and the preview
- 📜 **Log viewer** in the UI with filter, line count and "hide heartbeats"
- ⚠️ **Volume safety** - Red warning when volume pruning is enabled; manual runs only delete volumes after typing
  `DELETE` in the preview (otherwise volumes are skipped for that run). Scheduled runs are unchanged
- 🩺 **Write check** - Red banner and log error when `/config` is not writable; saves report an error instead of
  silently losing settings
- 🔔 **Warning banner** when authentication is disabled

### Changed
- 🎨 **Redesigned UI** - Same dark theme, SVG icons instead of emojis, sticky save bar with
  unsaved-changes indicator, toasts, native dialogs instead of `alert()/confirm()/prompt()`, collapsible sections
  via `<details>`, improved mobile layout
- ⚡ **No page reloads** - Saving settings and managing hosts happen in the background
- 🌐 Network preview now matches Docker's behavior (networks used by stopped containers are not pruned)
- 🧵 Gunicorn threads increased from 2 to 4 so the UI stays responsive during a manual prune
- 📚 **Shorter README** - The README now covers install, login and data protection; details moved to `docs/`
  (configuration, notifications, multiple hosts, Homepage widget, troubleshooting)

### Fixed
- 🏗️ **Build cache preview** listed cache entries shared with existing images, which `docker builder prune`
  keeps. The preview now matches what is actually removed (bug existed since build cache support)
- 🔒 **Lost config changes** - Concurrent requests (dashboard refresh, heartbeat) could replace the in-memory config
  between "change" and "save", silently dropping a change. All config writes are now atomic
- 🚀 **Startup crash on invalid numbers** - Invalid values such as `PRUNEMATE_SESSION_HOURS=12h` now fall back to the
  default with a warning instead of preventing startup
- 📝 `PRUNEMATE_LOG_FILE` is now honored by the logger as well as the log viewer

### Removed
- `POST /run-now` (unused by the UI; use *Preview & run*)

### Upgrading from V1.4.0
Upgrading is a drop-in image update: `docker compose pull && docker compose up -d`. Your existing
`docker-compose.yaml` keeps working unchanged (direct socket mount is still supported).

**Kept automatically:** schedule, cleanup options, external hosts, notification settings including stored
tokens, all-time statistics (`stats.json`) and the login (same `PRUNEMATE_AUTH_PASSWORD_HASH`).
Downgrading back to V1.4.0 also works; it ignores the new settings.

**Run history:** V1.4.0 only stored totals. On the first V2.0.0 start, PruneMate reads the existing log files
(`./logs/prunemate.log` and its rotated copies) once and rebuilds the per-run history from them: date, manual or
scheduled, per-host counts, failed hosts and space reclaimed (rounded, as logged). Only runs that are still in the
logs (up to ~20 MB) can be imported; the all-time totals are always complete.

**What changes for you:**
- Everyone is logged out once (new session secret, stored in `/config/secret_key`).
- Resources labelled `prunemate.keep` are now skipped by default. Disable this under *Cleanup options → Protection*.
- Scripts that POST to PruneMate (e.g. the removed `/run-now`) need a CSRF token; read-only APIs such as
  `/api/stats` are unchanged and stay public unless you set `PRUNEMATE_API_KEY`.

**Optional:** switch to the new hardened `docker-compose.yaml` (socket proxy, read-only, `cap_drop: ALL`).
With `cap_drop: ALL`, `./config` and `./logs` must be writable for root; if not, PruneMate shows a red banner,
logs an `ERROR` with the fix, and refuses to report unsaved settings as saved.

**Recommended:** V1.4.0 wrote the Telegram bot token and Discord webhook URL in plain text to
`./logs/prunemate.log`. Start V2.0.0 once first (so the run history is imported), then delete the old log files
(`./logs/prunemate.log*`) and consider rotating those tokens.

## [V1.4.0] - November 2026

### Added
- 💓 **Health ping for external hosts** - External Docker hosts are now pinged to check their health
  - Status symbol changes color based on the result of the health check for External Docker hosts
    - Green = online, red = offline, grey = disabled, amber = checking or status unavailable
  - New `/hosts/status` endpoint checks all enabled external hosts in parallel; statuses refresh every 60 seconds
  - Docker connections to remote hosts are now verified with a ping and a 5 second connect timeout, so unreachable hosts wil not run the scheduled prune run

### Changed
- 🔔 **Shorter, cleaner notifications** - Notifications are more compact and less cluttered
  - Removed emojis from notification text for a cleaner look across all providers
  - Per-host results only list the items that were actually pruned
  - Totals across all hosts are skipped when only one host had deletions, avoiding duplicate information
- 🎨 **Improved icons** - Updated icons for external hosts and notifications in the PruneMate interface

## [V1.3.3] - February 2026

### Changed  
- 🏗️ **New info button** -  A new information button has been added to the newly launched Prunemate.org website.

## [V1.3.2] - January 2026

### Fixed
- 🐛 **External Docker hosts edit/delete/toggle bug** - Fixed critical issue preventing users from modifying external hosts
  - Fixed incorrect index calculation in editHost() function that caused edit prompt issues
  - Fixed index mismatch between full hosts array (including Local) and docker_hosts array
  - Now correctly uses displayIndex for API calls to external hosts
  - Confirmed working: add host ✓, edit host ✓, delete host ✓, toggle host ✓
  - No migration needed - fully backward compatible with existing configurations

### Changed  
- 🧹 **Code cleanup** - Improved code quality and removed redundant logic
  - Removed redundant auto_save parameter check in Python
  - Improved DOM cleanup in JavaScript host management functions
  - Added input field clearing after successful host addition
  - Enhanced HTML injection protection with proper escaping in addNewHost()

## [V1.3.1] - December 2025

### Added
- 🔀 **Schedule enable/disable toggle** - New UI toggle to control automatic scheduling
  - "Enable automatic schedule" switch in Schedule section
  - Allows running manual cleanups only without affecting scheduled runs
  - Scheduler still heartbeats every minute but skips execution when disabled
  - Setting persists in config.json and defaults to enabled for existing installations
- 🏗️ **Multi-architecture Docker image support** - Build once, run anywhere
  - Native support for amd64 and arm64 architectures
  - Works seamlessly on Intel/AMD, Raspberry Pi 4/5, Apple Silicon M1/M2/M3, and ARM-based NAS
  - Docker Buildx multi-platform builds for efficient distribution
  - No more local builds required for ARM systems
  - Single docker-compose.yaml works on all architectures

### Fixed
- 🏠 **Homepage widget integration with authentication** - Stats endpoints now work with auth enabled
  - `/stats` and `/api/stats` endpoints accessible without authentication
  - Required for Homepage and Dashy widgets to display statistics when login is enabled
  - Backward compatible: endpoints contain non-sensitive Docker cleanup statistics only
- 📊 **Schedule configuration logging** - Added `schedule_enabled` to effective config output
  - Proper logging of all schedule settings including new toggle

### Changed
- 📦 **Docker Compose default** - Changed from local build to pre-built multi-arch image
  - docker-compose.yaml now uses `image: anoniemerd/prunemate:latest` by default
  - Auto-detects correct architecture (amd64/arm64) at pull time
  - Significantly faster deployment and smaller initial setup

## [V1.3.0] - December 2025

### Added
- 🔐 **Optional authentication system** - Secure password protection for web interface and API
  - Form-based login with styled page matching app design
  - Scrypt password hashing (32768 iterations, industry standard)
  - Base64-encoded hashes to prevent Docker Compose from interpreting `$` as environment variables
  - Built-in hash generator: `docker run --rm anoniemerd/prunemate python prunemate.py --gen-hash "password"`
  - Session management with secure HttpOnly cookies
  - Basic Auth fallback for API clients (Homepage, Dashy, etc.)
  - Logout button in dashboard top-right corner
  - Opt-in design: only enabled when `PRUNEMATE_AUTH_PASSWORD_HASH` is set
  - Backward compatible: runs in open mode without auth variables
  - New environment variables: `PRUNEMATE_AUTH_USER` (default: admin), `PRUNEMATE_AUTH_PASSWORD_HASH`
  - Credit: [@difagume](https://github.com/difagume)
- 🏗️ **Docker build cache pruning support** - New option to clean up Docker builder cache
  - Can reclaim significant disk space
  - Uses Docker API's `/build/prune` endpoint
  - Integrated into preview, statistics, and notifications
  - **⚠️ IMPORTANT: Docker Socket Proxy users MUST add `BUILD=1` environment variable to enable this feature**
  - See README for updated Docker Socket Proxy configuration
- 💬 **Discord notification provider** - Full support for Discord webhook notifications
  - Configure via Webhook URL (from Discord server integrations)
  - Priority-based color coding: Low=Green, Medium=Orange, High=Red
  - Rich embed formatting with timestamps
  - Works alongside Gotify, ntfy, and Telegram providers
- 📱 **Telegram notification provider** - Full support for Telegram Bot notifications
  - Priority support: Low=silent notifications, Medium/High=normal sound
  - HTML formatting for rich message display
  - Works alongside Gotify, ntfy, and Discord providers
- 🎯 **Text-based priority system** - Changed from numeric (1-10) to text-based (Low/Medium/High)
  - More intuitive and user-friendly
  - Default priority changed from "Low" to "Medium"
  - Automatic migration from numeric to text priority on upgrade
  - Provider-specific priority mapping (e.g., Telegram uses disable_notification, Gotify uses numeric values)

### Changed
- ⚙️ **Default notification priority** - Changed from "Low" to "Medium" for better visibility
  - All new configurations default to medium priority
  - Existing configurations with numeric priorities auto-migrate to text equivalents
  - Migration logic: 1-3→Low, 4-7→Medium, 8-10→High

### Fixed
- 🔧 **Notification provider migration** - Added forward compatibility for new providers
  - Discord and Telegram credentials automatically added to existing configs
  - All provider subkeys (gotify, ntfy, discord, telegram) guaranteed to exist
  - Prevents errors when upgrading from older versions

## [V1.2.8] - December 2025

### Added
- 🔍 **Prune preview before manual execution** - See exactly what will be deleted before running manual prune
  - Shows detailed list of containers, images, networks, and volumes to be removed
  - Per-host breakdown for multi-host setups
  - Two-step confirmation process for safer manual pruning
  - Only applies to manual "Run now" executions (scheduled runs remain automatic)
  - Auto-save functionality: checkbox states persist when switching between preview and settings
- 🏠 **Homepage dashboard integration** - New `/api/stats` endpoint for Homepage widget support
  - Returns all-time statistics in customapi-compatible format
  - Includes human-readable space reclaimed field (`spaceReclaimedHuman`)
  - Relative time formatting for last run (`lastRunText` shows "2h ago", "3d ago", etc.)
  - Easy integration with Homepage dashboard services

### Improved
- 🧹 **Image prune behavior** - Now removes ALL unused images (not just dangling)
  - Uses `filters={"dangling": False}` for comprehensive cleanup
  - Matches preview display with actual prune behavior
  - More aggressive space reclamation while maintaining safety
- 📦 **Volume prune behavior** - Explicitly includes named volumes
  - Uses `filters={"all": True}` to remove all unused volumes
  - Named volumes are pruned alongside anonymous volumes
  - Preview accurately shows all volumes to be removed

### Fixed
- 🐛 **Checkbox reading bug** - Preview modal now correctly reads checkbox states
  - Added missing `id` attributes to all four checkboxes
  - JavaScript `getElementById()` now works correctly
  - Fixed "No prune options selected" error when checkboxes were checked
- 🐛 **JavaScript variable scope** - Fixed redeclaration errors in time formatting
  - Resolved Jinja2 template variable conflicts in 12h/24h time display
  - Cleaner variable naming for better maintainability

---

## [V1.2.7] - December 2025

### Added
- 🔐 **ntfy authentication support** - Bearer token and Basic Auth (username:password in URL)
  - Priority system: Bearer token → Basic Auth → unauthenticated
  - RFC 3986 compliant URL parsing for embedded credentials
  - Optional token field in UI for ntfy provider
- 🔒 **Enhanced credential security** - Passwords and tokens masked in all log output
  - URL credentials (username:password) redacted in logs
  - Bearer tokens sanitized in notification logs

### Improved
- 🎨 **Logo enhancement** - Improved SVG logo design (thanks to [@shollyethan](https://github.com/shollyethan)) + added to the Self-Hosted Dashboard Icons on https://selfh.st/icons/
- 📏 Logo size increased from 76×76px to 82×82px for better visibility
- 📱 **Better mobile support** - Enhanced responsive design for smartphone usage
- 🔔 Notification panel height increased to 900px with enhanced scrolling behavior
- 🔧 **Config migration improvements** - Deep merge strategy for nested structures
  - Prevents data loss during v1.2.6 → v1.2.7 upgrades
  - Preserves both gotify and ntfy settings in nested notifications structure
- 📊 **Stats persistence improvements** - Forward-compatible field migration
  - Type-safe increments with defensive programming
  - Graceful handling of corrupt or incomplete stats files
- 🏗️ **Hosts API consistency** - Local socket now included in `/hosts` endpoint response

### Fixed
- 🐛 Config shallow merge bug causing nested key loss during upgrades
  - Replaced `dict.update()` with recursive `_deep_merge()` function
- 🐛 Legacy notification migration incomplete (only migrated gotify, missed ntfy)
- 🐛 Stats field migration missing for new fields in future versions
- 🐛 Stats type safety issues with corrupt JSON files
- 🐛 Notification panel button visibility on smaller screens

---

## [V1.2.6] - November 2025

### Added
- 🐳 **Multi-host support** - Manage multiple Docker hosts from one interface
  - Per-host results in notifications with detailed breakdown for each Docker host
  - Docker hosts management UI (add, edit, enable/disable, delete external hosts)

### Improved
- 🔔 Notification formatting with enhanced layout, consistent emoji usage, and bullet points
- 📬 Notifications now show per-host breakdown for multi-host setups with aggregate totals
- 🎯 Better visual hierarchy in notifications with clear sections and spacing
- 🔧 Code quality improvements and better error handling

### Fixed
- 🐛 Critical checkbox handling bug affecting all prune and notification toggles

---

## [V1.2.5] - November 2025

### Improved
- 🔧 Eliminated duplicate code - moved `_validate_time()` to module level
  - Removed identical function definitions from `/update` and `/test-notification` routes
  - Renamed to `validate_time()` as public module-level function
- 📝 Better log clarity for prune operations
  - Volumes: "Pruning volumes (unused anonymous volumes only)…"
- 🧹 Moved `calendar` import from inline to top-level imports

### Fixed
- 🐛 Monthly schedule bug where jobs never ran in shorter months
  - Jobs configured for day 30-31 now run on last day of shorter months (e.g., Feb 28/29)
  - Uses `calendar.monthrange()` to determine actual last day of each month
- 🐛 Configuration deep copy bug causing shared nested dictionaries
  - All `.copy()` operations replaced with proper deep copy via `json.loads(json.dumps())`
  - Prevents config corruption when modifying nested notification settings
  - Fixed in 4 locations: initialization + 3 in `load_config()`
- 🐛 KeyError in legacy Gotify config migration
  - Now safely checks if notifications dict exists before accessing nested keys
  - Uses `.get()` with fallback values to prevent crashes on old config files

---

## [V1.2.4] - November 2025

### Added
- 📊 **All-Time Statistics dashboard** showing cumulative prune data
  - Total space reclaimed across all runs
  - Counters for containers, images, networks, volumes deleted
  - Total prune runs with first/last run timestamps
  - Statistics persist in `/config/stats.json`

### Improved
- 📝 All functions now have proper Python docstrings for better IDE support
- 🔧 Code quality improvements and better error handling

### Fixed
- 🐛 12-hour time format backend handling in `/update` and `/test-notification` routes
- 🐛 Minute display now shows leading zeros (e.g., "7:04" instead of "7:4")
- 🐛 Time input validation now runs on page load (`initTimeClamp()`)

---

## [V1.2.3] - November 2025

### Added
- 🏗️ ARM64 architecture installation instructions (Apple Silicon, ARM servers, Raspberry Pi)

### Improved
- 📜 License changed from MIT to AGPLv3
- 📝 All functions documented in English for better code maintainability
- 📚 Documentation improvements with Quick Start guide

---

## [V1.2.2] - November 2025

### Added
- ✨ 12/24-hour time format support via `PRUNEMATE_TIME_24H` environment variable
- 🎨 Custom time picker for 12-hour mode (hour 1-12, minutes, AM/PM selector)

### Improved
- 🌍 Timezone handling across all components (logs, scheduling, notifications)
- ⚡ Simplified architecture: reduced from 2 workers to 1 for better reliability
- 📝 Silent config loading to reduce log noise
- 🔧 Input validation with instant clamping and 2-digit limits

### Fixed
- 🐛 Config synchronization issues in multi-worker setup
- 🔒 Thread-safe configuration saving with file locking

---

## [V1.2.1] - November 2025

### Improved
- 🔒 Thread-safe config saving mechanism
- 📊 Logging with timezone-aware timestamps

### Fixed
- 🐛 Scheduler not triggering at configured times
- 🔄 Config reloads before each scheduled check to ensure synchronization

---

## [V1.2.0] - November 2025

### Added
- 🔔 Notification support (Gotify & ntfy.sh)
- 🎯 "Only notify on changes" option
- 📊 Enhanced statistics and detailed cleanup reporting

### Improved
- 🎨 Complete UI redesign with modern dark theme
- 🔘 Improved button animations and hover effects

---

## [V1.1.0] - October 2025

### Added
- 🎉 Initial release
- 🕐 Daily, Weekly, and Monthly scheduling
- 🧹 Selective cleanup options (containers, images, networks, volumes)
- 🌐 Web interface for configuration
- 📁 Persistent configuration and logging
