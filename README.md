# PruneMate

<p align="center">
  <img width="400" height="400" alt="prunemate-logo" src="https://github.com/user-attachments/assets/0785ea56-88f6-4926-9ae1-de736840c378" />
</p>

<h1 align="center">PruneMate</h1>
<p align="center"><em>Docker image & resource cleanup helper, on a schedule!</em></p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.3.3-purple?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/python-3.12-yellow?style=for-the-badge&logo=python&logoColor=ffffff"/>
  <img src="https://img.shields.io/badge/docker-compose-0db7ed?style=for-the-badge&logo=docker&logoColor=ffffff"/>
  <img src="https://img.shields.io/badge/license-AGPLv3-orange?style=for-the-badge"/>
  <a href="https://hub.docker.com/r/anoniemerd/prunemate">
    <img src="https://img.shields.io/docker/pulls/anoniemerd/prunemate?style=for-the-badge&logo=docker&logoColor=ffffff&label=docker%20pulls"/>
  </a>
  <a href="https://www.buymeacoffee.com/anoniemerd">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"/>
  <a href="https://prunemate.org">
    <img src="https://img.shields.io/badge/Visit-Website-0ea5e9?style=for-the-badge&logo=internet-explorer&logoColor=white" alt="Website"/>
  </a>
</p>

A sleek, lightweight web interface to **automatically clean up Docker resources** on a schedule. Built with Python (Flask) · Docker SDK · APScheduler · Gunicorn

**Keep your Docker host tidy with scheduled cleanup of unused images, containers, networks, and volumes.**

> ⚠️ **DISCLAIMER**: PruneMate uses Docker's native `prune` commands to delete unused resources. This means it removes containers, images, networks, and volumes that Docker considers "unused" - be careful with volumes as they may contain important data. Ensure you understand what will be pruned before enabling automated schedules. The author is not responsible for any data loss or system issues. **Use at your own risk.**

---

## ✨ Features

- 🕐 **Flexible scheduling** - Daily, Weekly, or Monthly cleanup runs with optional manual-only mode
- 🔀 **Schedule control toggle** - Enable/disable automatic scheduling, so PruneMate only runs manually
- 🔍 **Prune preview** - See exactly what will be deleted before executing manual prune operations
- 🌍 **Timezone aware** - Configure your local timezone
- 🕒 **12/24-hour time format** - Choose your preferred time display
- 🐳 **Multi-host support** - Manage multiple Docker hosts from one interface (requires docker-socket-proxy on remote hosts)
- 🧹 **Selective cleanup** - Choose what to prune: containers, images, networks, volumes, **build cache**
- 🏗️ **Build cache cleanup** - Reclaim significant space by pruning Docker builder cache (often 10GB+)
- 📊 **All-Time Statistics** - Track cumulative space reclaimed and resources deleted across all runs
- 🏠 **Homepage integration** - Display statistics in your Homepage dashboard (works with authentication enabled)
- 🎨 **Modern UI** - Dark theme with smooth animations and responsive design
- 🔒 **Secure authentication** - Optional login protection with password hashing and Basic Auth support
- 🏗️ **Multi-architecture support** - Native amd64 and arm64 Docker images (Intel/AMD, Raspberry Pi, Apple Silicon)
- 🔒 **Safe & controlled** - Manual trigger with preview and detailed logging
- 📈 **Detailed reports** - See exactly what was cleaned and how much space was reclaimed

---

## 📷 Screenshots

### Main Dashboard
The overall look and feel of the PruneMate dashboard

<p align="center">
  <img width="400" height="800" src="https://github.com/user-attachments/assets/845405f1-0787-4323-a592-36177e32a035" />
</p>

### Authentication page
The login page, (when enabled in the docker-compose.yaml environment variables)

<p align="center">
  <img width="400" height="800" src="https://github.com/user-attachments/assets/29ea359c-452e-4e1d-8567-c8fd65b08d4e" /> 
</p>

### External Docker hosts
Add external Docker hosts via [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)

<p align="center">
  <img width="400" height="400" alt="prunemate-cleanup" src="https://github.com/user-attachments/assets/28abdbe4-bd9e-4272-a6fc-24a4a8dc83bb" />
</p>

### Notification Settings
Set up notifications via Gotify, ntfy.sh, Discord, Telegram, or Slack to stay informed about cleanup results.

<p align="center">
  <img width="400" height="400" alt="prunemate-notifications" src="https://github.com/user-attachments/assets/73a06c4d-fffa-40eb-a010-239d7d364004" /> 
</p>

### Prune preview
A brief interface that shows which Docker resources will be pruned during the next cleanup run, either manually triggered or scheduled.

<p align="center">
  <img width="400" height="400" alt="prunemate-preview" src="https://github.com/user-attachments/assets/34fb445d-8956-46e8-84df-b6718db3f556" /> 
</p>


---

## 🚀 Quick Start with Docker Compose

### Prerequisites

- Docker and Docker Compose installed
- Access to Docker socket (`/var/run/docker.sock`)

### Installation

1. **Create a `docker-compose.yaml` file:**

```yaml
services:
  prunemate:
    image: anoniemerd/prunemate:latest  # Supports amd64 and arm64
    container_name: prunemate
    ports:
      - "7676:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./logs:/var/log
      - ./config:/config
    environment:
      - PRUNEMATE_TZ=Europe/Amsterdam # Change this to your desired timezone
      - PRUNEMATE_TIME_24H=true #false for 12-Hour format (AM/PM)
      # Optional: Enable authentication (generate hash with: docker run --rm anoniemerd/prunemate python prunemate.py --gen-hash "password")
      # - PRUNEMATE_AUTH_USER=admin
      # - PRUNEMATE_AUTH_PASSWORD_HASH=your_base64_encoded_hash_here
    restart: unless-stopped
```

2. **Start PruneMate:**

```bash
docker-compose up -d
```

3. **Access the web UI of PruneMate:**

Open your browser and navigate to:

```
http://<your-server-ip>:7676/
```

---

## 🚀 Quick Start with Docker Run

**Using Docker CLI:**

```bash
docker run -d \
  --name prunemate \
  -p 7676:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $(pwd)/logs:/var/log \
  -v $(pwd)/config:/config \
  -e PRUNEMATE_TZ=Europe/Amsterdam \
  -e PRUNEMATE_TIME_24H=true \
  --restart unless-stopped \
  anoniemerd/prunemate:latest
```

**Access the web UI:**

```
http://<your-server-ip>:7676/
```



**Volume explanations:**
- `/var/run/docker.sock` - Required for Docker API access
- `./logs` - Stores application logs (rotating, 5MB max per file)
- `./config` - Stores configuration and state files

---

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PRUNEMATE_TZ` | `UTC` | Timezone for scheduling (e.g., `Europe/Amsterdam`, `America/New_York`) |
| `PRUNEMATE_TIME_24H` | `true` | Time format: `true` for 24-hour, `false` for 12-hour (AM/PM) |
| `PRUNEMATE_CONFIG` | `/config/config.json` | Path to configuration file |
| `PRUNEMATE_AUTH_USER` | `admin` | Username for authentication (optional, only used when auth is enabled) |
| `PRUNEMATE_AUTH_PASSWORD_HASH` | _(none)_ | Base64-encoded password hash (enables authentication when set) |

### 🔐 Authentication (Optional)

PruneMate supports optional password protection for the web interface and API endpoints.

**Key features:**
- 🔒 **Form-based login** - Styled login page matching the app's design
- 🔑 **Secure hashing** - Passwords are hashed using scrypt (industry standard)
- 🌐 **API compatibility** - Basic Auth fallback for external tools (Homepage, Dashy, etc.)
- 🚪 **Logout button** - Convenient session management

**To enable authentication:**

1. **Generate a password hash** using the built-in tool:

```bash
docker run --rm anoniemerd/prunemate:latest python prunemate.py --gen-hash "your_password"
```

This outputs a Base64-encoded hash (safe for YAML, no special characters):
```
c2NyeXB0OjMyNzY4Ojg6MSRvcDdnZFlGR1JmRFp4Y1RjJDBmMzNlYzc4NzExZTI4MzllYjk0MWFiOTZkOGUyZGNjNGRhMzU2NTlmMGI1ZDg0NjhjZTdkMThhODhmNmQ3ZGRhOGU4YzdmMDYxMWZiNzAyYjA0ZGNhNTBjZWMxZjFlYzc3ZjhlNzJhYmM0MmQ3OTQ5NDM2MDUzZWRlZjlhZGY0
```

> **Why Base64?** Raw scrypt hashes contain `$` characters that Docker Compose interprets as environment variables, corrupting the hash. Base64 encoding produces alphanumeric strings that YAML handles safely without escaping.

> **✅ Special characters that work well:**
> - Hash characters: `#`
> - At sign: `@` 
> - Percent: `%`
> - Asterisk: `*`
> - Ampersand: `&`
> - Caret: `^`
>
> **⚠️ Avoid these characters:**
> - Exclamation mark: `!` (bash history expansion)
> - Dollar sign: `$` (variable expansion - even Base64 encoded, can cause issues in some contexts)
>
> **Safe examples:**
> - `MyPassword#123`
> - `Test@secure%pass`
> - `prunemate&admin^2024`
> - `MyPass*Admin#99`

2. **Add to your docker-compose.yaml:**

```yaml
environment:
  - PRUNEMATE_AUTH_USER=admin  # Optional (default: admin)
  - PRUNEMATE_AUTH_PASSWORD_HASH=c2NyeXB0OjMyNzY4Ojg6MSRvcDdnZFlGR1JmRFp4Y1RjJDBmMzNlYzc4...
```

3. **Restart the container:**

```bash
docker-compose up -d
```

**Important notes:**
- Authentication is **opt-in** - only enabled when `PRUNEMATE_AUTH_PASSWORD_HASH` is set
- Without the hash variable, the app runs in open mode (backward compatible)
- For API clients (Homepage, etc.), use Basic Auth with your actual password (not the hash)
- The hash is Base64-encoded to prevent Docker Compose from interpreting `$` characters as variables


### Web Interface Settings

Access the web interface at `http://localhost:7676/` (or your server IP) to configure:

**Schedule Settings:**
- **Frequency:** Daily, Weekly, or Monthly
- **Time:** When to run the cleanup (supports both 12h and 24h format)
- **Day:** Day of week (for weekly) or day of month (for monthly)

**Cleanup Options:**
- ☑️ All unused containers
- ☑️ All unused images  
- ☑️ All unused networks
- ☑️ All unused volumes

**Notification Settings:**
- **Provider:** Gotify, ntfy.sh, Discord, Telegram, or Slack
- **Configuration:** Provider-specific credentials (URL/Token for Gotify, URL/Topic for ntfy, Webhook URL for Discord, Bot Token/Chat ID for Telegram, Webhook URL for Slack)
- **Priority:** Low (silent), Medium, or High priority notifications (provider-dependent)
- **Only notify on changes:** Only send notifications when something was actually cleaned

---

## 🧠 How it works

1. **Scheduler runs** every minute checking if it's time to execute
2. **Loads latest config** from persistent storage
3. **Executes Docker prune** commands for selected resource types
4. **Collects statistics** on what was removed and space reclaimed
5. **Updates all-time statistics** with cumulative data (space, counts, timestamps)
6. **Sends notification** (if configured and enabled)
7. **Logs everything** with timezone-aware timestamps

📊 **[View detailed architecture & flowchart](ARCHITECTURE.md)**

### File Structure

```
/config/
├── config.json          # Your configuration (persistent)
├── stats.json           # All-time statistics (cumulative data)
├── prunemate.lock       # Prevents concurrent runs
└── last_run_key         # Tracks last successful run

/var/log/
└── prunemate.log        # Application logs (rotating, 5MB max)
```

### All-Time Statistics

PruneMate tracks cumulative statistics across all prune runs:

**Metrics tracked:**
- 💾 **Total Space Reclaimed** - Cumulative disk space freed (displayed in MB/GB/TB)
- 📦 **Containers Deleted** - Total count of unused containers removed
- 🖼️ **Images Deleted** - Total count of unused images removed
- 🔗 **Networks Deleted** - Total count of unused networks removed
- 💿 **Volumes Deleted** - Total count of unused volumes removed
- 🔄 **Total Prune Runs** - Number of times prune has executed
- 📅 **First Run** - Timestamp of the very first prune execution
- 🕐 **Last Run** - Timestamp of the most recent prune execution

**Technical details:**
- Statistics persist in `/config/stats.json` using atomic writes with file locking
- Updates occur after every prune run, regardless of whether resources were deleted
- Timestamps are timezone-aware and respect `PRUNEMATE_TZ` setting
- Date/time display in UI follows configured 12h/24h format
- Statistics survive container restarts and updates
- Auto-refresh after manual prune runs via web interface

---

## 🔔 Notification Setup

### Gotify

[Gotify](https://gotify.net/) is a self-hosted notification service.

**Setup steps:**
1. Install and run Gotify server
2. Create a new application in Gotify
3. Copy the application token
4. Configure in PruneMate:
   - **Provider:** Gotify
   - **URL:** `https://your-gotify-server.com`
   - **Token:** Your application token

### ntfy.sh

[ntfy.sh](https://ntfy.sh/) is a simple pub-sub notification service (self-hosted or public).

**Setup steps:**
1. Choose a unique topic name (e.g., `prunemate-alerts`)
2. Configure in PruneMate:
   - **Provider:** ntfy
   - **URL:** `https://ntfy.sh` (or your self-hosted instance, supports `username:password@host` format)
   - **Topic:** Your chosen topic name
   - **Token:** (Optional) Bearer token for authentication

**Authentication options:**
- **Bearer token:** Recommended for API access tokens (higher priority)
- **URL credentials:** Use `https://username:password@ntfy.example.com` format (RFC 3986 compliant)
- **No authentication:** Works with public topics

**Subscribe to notifications:**
- **Web:** Visit `https://ntfy.sh/your-topic`
- **Mobile:** Install the ntfy app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/app/ntfy/id1625396347)) and subscribe to your topic
- **Desktop:** Use ntfy desktop app or web browser

### Discord

[Discord](https://discord.com/) webhooks allow notifications directly to your Discord server.

**Setup steps:**
1. Open your Discord server settings
2. Go to **Integrations** → **Webhooks**
3. Click **New Webhook** or edit existing webhook
4. Copy the **Webhook URL**
5. Configure in PruneMate:
   - **Provider:** Discord
   - **Webhook URL:** `https://discord.com/api/webhooks/...`

**Priority colors:**
- **Low:** Green (informational)
- **Medium:** Orange (warning)
- **High:** Red (critical)

### Telegram

[Telegram Bot API](https://core.telegram.org/bots) enables notifications via Telegram bots.

**Setup steps:**
1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the instructions
3. Give your bot a name (e.g., "PruneMate Notifications")
4. Give your bot a username ending in "bot" (e.g., "prunemate_notif_bot")
5. Copy the **Bot Token** (format: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
6. Get your **Chat ID**:
   - **Easy method:** Message **@userinfobot** or **@getmyid_bot** to get your Chat ID
   - **Alternative:** Message your bot, then visit `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` and find `"chat":{"id":123456789}`
7. Configure in PruneMate:
   - **Provider:** Telegram
   - **Bot Token:** Your bot token from BotFather
   - **Chat ID:** Your numeric chat ID (or `@channelname` for channels)

**Priority behavior:**
- **Low:** Silent notifications (no sound)
- **Medium/High:** Normal notifications with sound

**Advanced usage:**
- **Groups:** Add bot to group, get group Chat ID (starts with `-`)
- **Channels:** Use channel username with `@` (e.g., `@mychannel`) or numeric ID

---

### Slack

[Slack Incoming Webhooks](https://api.slack.com/messaging/webhooks) allow notifications directly to a Slack channel.

**Setup:**
1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create (or choose) an app
2. Enable **Incoming Webhooks** in the app's feature settings
3. Click **Add New Webhook to Workspace**, select your target channel, and click **Allow**
4. Copy the generated webhook URL (starts with `https://hooks.slack.com/services/…`)
5. In PruneMate notifications settings:
   - **Provider:** Slack
   - **Webhook URL:** The URL you copied above

**Priority behavior:**
- **Low:** Green attachment border (`good`)
- **Medium:** Orange/yellow attachment border (`warning`)
- **High:** Red attachment border (`danger`)

---


## 🌐 Multi-Host Setup (Optional)

PruneMate can manage multiple Docker hosts from a single interface. Each prune operation runs across all enabled hosts with aggregated results.

### Security First: Use Docker Socket Proxy

⚠️ **Never expose Docker sockets directly!** Always use [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) to limit API access.

### Quick Setup

**1. Deploy proxy on each remote host:**

```yaml
services:
  dockerproxy:
    image: ghcr.io/tecnativa/docker-socket-proxy:latest
    environment:
      - CONTAINERS=1
      - IMAGES=1
      - NETWORKS=1
      - VOLUMES=1
      - BUILD=1         # REQUIRED FOR BUILD CACHE PRUNE
      - POST=1          # Required for prune operations
    ports:
      - "2375:2375"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped
```

> **⚠️ IMPORTANT:** The `BUILD=1` environment variable is **REQUIRED** to enable Docker build cache pruning. Without it, build cache prune operations will fail with a 403 error.

**2. Add hosts in PruneMate UI:**
- Navigate to **Docker Hosts** section
- Click **Add New Host**
- Enter name (e.g., "NAS") and URL (e.g., `tcp://192.168.1.50:2375`)
- Toggle hosts on/off as needed

**3. Test connection:**
Click **Run now** and check logs for successful connection to all hosts.


### Troubleshooting

- **Connection refused**: Verify proxy is running (`docker ps`) and port 2375 is accessible
- **Permission denied**: Ensure proxy has `POST=1` environment variable
- **Host skipped**: Check URL format starts with `tcp://`, `http://`, or `https://`

---

## 🏠 Homepage Dashboard Integration

PruneMate provides a custom API endpoint at `/api/stats` that returns all-time statistics in a format compatible with [Homepage](https://gethomepage.dev/) dashboard widgets.

<p align="center">
  <img width="400" height="400" alt="prunemate-homepage" src="https://github.com/user-attachments/assets/942169f6-bc16-4cef-8b46-3ac012fe7fec" /> 
</p>

### Setup

Add this configuration to your Homepage `services.yaml`:

```yaml
- PruneMate:
    href: http://<your-server-ip>:7676
    description: Docker Cleanup Automation
    icon: https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp/prunemate.webp
    widget:
      type: customapi
      url: http://<your-server-ip>:7676/api/stats
      mappings:
        - field: pruneRuns
          label: Prune Runs
          format: number
        - field: lastRunText
          label: Last Run
        - field: imagesDeleted
          label: Images Pruned
          format: number
        - field: spaceReclaimedHuman
          label: Space Saved
```

### Available Fields

The `/api/stats` endpoint returns the following fields:

| Field | Type | Description | Homepage Format |
|-------|------|-------------|-----------------|
| `pruneRuns` | number | Total number of prune operations executed | `number` |
| `containersDeleted` | number | Total containers deleted across all runs | `number` |
| `imagesDeleted` | number | Total images deleted across all runs | `number` |
| `networksDeleted` | number | Total networks deleted across all runs | `number` |
| `volumesDeleted` | number | Total volumes deleted across all runs | `number` |
| `buildCacheDeleted` | number | Total build cache entries deleted across all runs | `number` |
| `spaceReclaimed` | number | Total space reclaimed in bytes | `number` |
| `spaceReclaimedHuman` | string | Human-readable space reclaimed (e.g., "2.5 GB") | `text` |
| `lastRunText` | string | Relative time as text (e.g., "2h ago") | `text` |
| `lastRunTimestamp` | number | Unix timestamp in seconds of last run | `number` |
| `lastRun` | string | ISO timestamp of most recent prune run | `date` |
| `firstRun` | string | ISO timestamp of first prune run | `date` |

### Example /api/stats output

```json
{
  "pruneRuns": 42,
  "containersDeleted": 156,
  "imagesDeleted": 89,
  "networksDeleted": 12,
  "volumesDeleted": 7,
  "buildCacheDeleted": 715,
  "spaceReclaimed": 5368709120,
  "spaceReclaimedHuman": "5.00 GB",
  "lastRunText": "2h ago",
  "lastRunTimestamp": 1733454000,
  "lastRun": "2025-12-06T03:00:00+01:00",
  "firstRun": "2025-01-15T03:00:00+01:00"
}
```
---

## 🧠 Troubleshooting

| Problem | Solution |
|---------|----------|
| ❌ Can't access web interface | • Check if port 7676 is available and not blocked by firewall<br>• Verify container is running: `docker ps`<br>• Check logs: `docker logs prunemate` |
| 🏗️ ARM architecture error | • Since V1.3.1+: Image now has native multi-architecture support (amd64 + arm64)<br>• Pull `anoniemerd/prunemate:latest` - it will auto-detect your platform<br>• No local build required anymore!<br>• If running older versions, use `build: .` in docker-compose.yaml |
| ⚙️ Container not starting | • View startup errors: `docker logs prunemate`<br>• Verify Docker socket is accessible<br>• Check if port 7676 is already in use |
| 🔒 Permission denied errors | • Ensure `/var/run/docker.sock` exists and is accessible<br>• On Linux, Docker daemon must be running<br>• User running Docker must have proper permissions |
| 🕐 Wrong timezone in logs/schedule | • Set `PRUNEMATE_TZ` environment variable correctly<br>• Restart container after changing: `docker-compose restart`<br>• Verify timezone in logs matches expected |
| 📧 Notifications not working | • Test notification settings in web interface<br>• Verify notification server URL is accessible<br>• Check token/topic is correct<br>• Review logs for error messages |
| 🗂️ Configuration not persisting | • Ensure `./config` volume is mounted correctly<br>• Check file permissions on host `./config` directory<br>• Verify container has write access |
| 🧹 Cleanup not running on schedule | • Check schedule configuration in web interface<br>• Verify timezone is set correctly<br>• Review logs: "Next scheduled run" messages<br>• Ensure container is running continuously |

---

### Logging

**What the logs contain:**
- ✅ Scheduler heartbeats (every minute)
- 📝 Configuration changes
- 🧹 Prune job executions with results
- 📨 Notification delivery status
- ❌ Error messages and warnings

---

## 📜 Release Notes

## [V1.3.3] - Ferbruary 2026

### Changed  
- 🏗️ **New info button** -  A new information button has been added to the newly launched Prunemate.org website.

📖 **[View full changelog](CHANGELOG.md)**

---

## 📬 Support

Have questions or need help?

- 🐛 **Bug reports:** [Open an issue on GitHub](https://github.com/anoniemerd/PruneMate/issues)
- 💡 **Feature requests:** [Open an issue on GitHub](https://github.com/anoniemerd/PruneMate/issues)
- 💬 **Questions & Discussion:** [Start a discussion on GitHub](https://github.com/anoniemerd/PruneMate/discussions)
- ⭐ **Like PruneMate?** Give it a star!

---

## ☕ Support the Project

If you find PruneMate useful and would like to support the development, consider buying me a coffee!

<p align="center">
  <a href="https://www.buymeacoffee.com/anoniemerd" target="_blank">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"/>
  </a>
</p>

Your support helps me dedicate more time to maintaining and improving PruneMate! ❤️

---

## 👤 Author & License

**Author:** Anoniemerd  
🐙 GitHub: <https://github.com/anoniemerd>  
📦 Repository: <https://github.com/anoniemerd/PruneMate>

---

## 👥 Contributors

I'm grateful for the contributions that make PruneMate better!

### Contributors
- **[@difagume](https://github.com/difagume)** - 🔐 Authentication system implementation (V1.3.0)
- **[@shollyethan](https://github.com/shollyethan)** - 🎨 Logo redesign & added the logo to Self-Hosted Dashboard Icons

### Project maintainer/owner
- **[anoniemerd](https://github.com/anoniemerd)** - Project creator and maintainer


---

### 📜 License — AGPLv3

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

By using, modifying, or distributing this software, you **must**:

- Keep this copyright notice
- Disclose source code of any modified version
- Disclose source code if used to provide a network service
- License any derivative works under **AGPL-3.0**

See the full license text in: [`LICENSE`](./LICENSE)

### ⚠️ Disclaimer

**USE AT YOUR OWN RISK.** PruneMate is provided "as is" without warranty of any kind. The author(s) and contributors are not responsible for:
- Data loss from pruned Docker resources
- Service interruptions or downtime
- System instability or performance issues
- Any damages resulting from the use or misuse of this software

Always:
- ✅ Understand which resources will be deleted
- ✅ Keep backups of important data and configurations
- ✅ Review logs after prune operations
- ✅ Start with conservative settings

© 2026 – PruneMate Project

---

<p align="center">
  <strong>Keep your Docker host clean with PruneMate! 🐳🧹</strong>
</p>
