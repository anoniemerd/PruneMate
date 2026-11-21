# PruneMate

<p align="center">
  <img width="400" height="400" alt="prunemate-logo" src="https://github.com/user-attachments/assets/0785ea56-88f6-4926-9ae1-de736840c378" />
</p>

<h1 align="center">PruneMate</h1>
<p align="center"><em>Docker image & resource cleanup helper, on a schedule!</em></p>

<p align="center">
  <img src="https://img.shields.io/badge/version-2.0.0-purple?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/python-3.12-yellow?style=for-the-badge&logo=python&logoColor=ffffff"/>
  <img src="https://img.shields.io/badge/docker-compose-0db7ed?style=for-the-badge&logo=docker&logoColor=ffffff"/>
  <img src="https://img.shields.io/badge/license-AGPLv3-orange?style=for-the-badge"/>
  <a href="https://hub.docker.com/r/anoniemerd/prunemate">
    <img src="https://img.shields.io/docker/pulls/anoniemerd/prunemate?style=for-the-badge&logo=docker&logoColor=ffffff&label=docker%20pulls"/>
  </a>
  <a href="https://www.buymeacoffee.com/anoniemerd">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"/>
  </a>
  <a href="https://prunemate.org">
    <img src="https://img.shields.io/badge/Visit-Website-0ea5e9?style=for-the-badge&logo=internet-explorer&logoColor=white" alt="Website"/>
  </a>
</p>

PruneMate is a lightweight web app that **keeps your Docker hosts tidy by cleaning up unused resources on a
schedule**: old images, stopped containers, unused networks, volumes and build cache. You choose what to clean
and when, check a preview whenever you like, and get a notification when it's done.

Built with Python (Flask) · Docker SDK · APScheduler · Gunicorn. Runs on amd64 and arm64 (Intel/AMD, Raspberry Pi, Apple Silicon).

> ⚠️ **Disclaimer:** PruneMate uses Docker's own `prune` commands. Removed resources are gone for good. Be
> especially careful with volumes, because they can contain databases. Make sure you understand what will be
> removed before you enable a schedule. **Use at your own risk.**

---

## ✨ Features

**Cleanup**
- 🕐 **Scheduled cleanup** - daily, weekly or monthly at a time you choose, or manual only
- 🧹 **You choose what goes** - containers, images, networks, volumes and build cache (often 10 GB+)
- 🔍 **Preview first** - see per host exactly what will be removed before a manual run
- 🐳 **Multiple hosts** - clean up other Docker machines from the same interface

**Safety**
- 🛡️ **Protection rules** - skip anything with a `prunemate.keep` label, or younger than N hours
- ⚠️ **Volume safeguard** - manual runs only delete volumes after you type `DELETE`
- 🔒 **Secure by default** - optional login with brute-force protection, hardened container, CSRF protection,
  secrets never shown in the browser or logs

**Insight**
- 📈 **Dashboard** - countdown to the next run, result of the last run, all-time totals
- 📊 **Run history** - chart and table of the last runs, with per-host results
- 📜 **Log viewer** - read and filter the PruneMate log right in the web UI

**Integrations**
- 🔔 **Notifications** - Gotify, ntfy, Discord or Telegram, optionally only when something was removed
- 🏠 **Homepage widget** - show your cleanup stats on your [Homepage](https://gethomepage.dev/) dashboard

---

## 📷 Examples

### Dashboard
The main page: next run, last result, run history and all settings.

<p align="center">
  <img width="1246" alt="PruneMate dashboard" src="https://github.com/user-attachments/assets/8afaf51c-0414-4bf1-8851-24f5bfded94a" />
</p>

### Logs
Read what PruneMate is doing right in the web UI: filter lines, choose how many to show and hide the heartbeats.
Passwords and tokens are masked.

<!-- TODO: add the Logs screenshot
<p align="center">
  <img width="1246" alt="PruneMate log viewer" src="SCREENSHOT_URL" />
</p>
-->

<details>
<summary>More screenshots: login, Docker hosts, notifications</summary>

**Login page**
<p align="center"><img width="638" alt="Login page" src="https://github.com/user-attachments/assets/4bcfd33f-dee8-4d8a-8f40-5f6c5689b971" /></p>

**External Docker hosts**
<p align="center"><img width="1194" alt="Docker hosts" src="https://github.com/user-attachments/assets/5a2ea525-fa8d-4b45-8b2d-80ef39a0dfc9" /></p>

**Notification settings**
<p align="center"><img width="1194" alt="Notification settings" src="https://github.com/user-attachments/assets/f30d9063-01ca-4916-8c2c-7d0cecd6ab33" /></p>

</details>

---

## 🚀 Installation

### 1. Create a `docker-compose.yaml`

This setup runs PruneMate together with a [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy).
The proxy only lets through the Docker functions PruneMate needs, so PruneMate never gets full control over
your Docker host.

```yaml
services:
  prunemate:
    image: anoniemerd/prunemate:latest
    container_name: prunemate
    ports:
      - "7676:8080"
    volumes:
      - ./logs:/var/log
      - ./config:/config
    environment:
      - PRUNEMATE_TZ=Europe/Amsterdam   # your timezone
      - PRUNEMATE_TIME_24H=true         # false for 12-hour (AM/PM)
      - PRUNEMATE_DOCKER_HOST=tcp://socket-proxy:2375
      # Recommended: enable the login (see "Turn on the login" below)
      # - PRUNEMATE_AUTH_USER=admin
      # - PRUNEMATE_AUTH_PASSWORD_HASH=your_base64_encoded_hash_here
    depends_on:
      - socket-proxy
    networks:
      - default
      - docker-api
    # Hardening: read-only filesystem, no extra privileges
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    restart: unless-stopped

  socket-proxy:
    image: ghcr.io/tecnativa/docker-socket-proxy:latest
    container_name: prunemate-socket-proxy
    environment:
      - CONTAINERS=1
      - IMAGES=1
      - NETWORKS=1
      - VOLUMES=1
      - BUILD=1   # build cache cleanup
      - SYSTEM=1  # build cache preview
      - POST=1    # needed to prune
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - docker-api
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped

networks:
  docker-api:
    internal: true   # the proxy can't be reached from outside
```

You can also download this file directly:
`curl -O https://raw.githubusercontent.com/anoniemerd/PruneMate/main/docker-compose.yaml`

<details>
<summary>Simpler setup without socket proxy</summary>

```yaml
services:
  prunemate:
    image: anoniemerd/prunemate:latest
    container_name: prunemate
    ports:
      - "7676:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./logs:/var/log
      - ./config:/config
    environment:
      - PRUNEMATE_TZ=Europe/Amsterdam
      - PRUNEMATE_TIME_24H=true
    restart: unless-stopped
```

Mounting the Docker socket directly gives PruneMate full control over Docker (comparable to root access),
so always turn on the login with this setup.

</details>

<details>
<summary>Using <code>docker run</code> instead of Compose</summary>

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

</details>

### 2. Start PruneMate

```bash
docker compose up -d
```

### 3. Open the web UI

Go to `http://<your-server-ip>:7676`.

**What the folders are for:**
- `./config` - your settings, statistics and run history (keep this folder when updating)
- `./logs` - the log file (rotating, max 5 MB per file)

> 💡 With `cap_drop: ALL`, both folders must be writable for root. If PruneMate shows a red
> *"Settings can't be saved"* banner, run `sudo chown -R root:root ./config ./logs`.

---

## 🧭 First steps

1. **Schedule** - pick daily, weekly or monthly and a time. Turn the schedule off if you only want manual runs.
2. **Cleanup options** - switch on what PruneMate may remove (see the table below). Start with images and
   stopped containers; only enable volumes once you've protected your important data.
3. **Save** - the bar at the bottom shows when you have unsaved changes.
4. **Preview & run** - check what would be removed right now, and run it if you like.
5. **Notifications** (optional) - get a message after each run, see [Notifications](#-notifications).

The dashboard at the top then shows when the next run is, how the last run went and how much space you've
saved so far.

### What gets cleaned

| Option | What is removed |
|--------|-----------------|
| Stopped containers | Exited, dead and created containers |
| Unused images | All images not used by any container (not only dangling ones) |
| Unused networks | Custom networks without containers |
| Unused volumes | All volumes not attached to a container, **including named volumes** |
| Build cache | Unused build cache that isn't part of an existing image (like `docker builder prune`) |

---

## 🛡️ Protect important data

Volume cleanup removes **every** volume that isn't attached to a container. That includes the database of a
stack you've stopped temporarily. PruneMate has two ways to prevent accidents:

**Keep label** - anything with the label `prunemate.keep` is never removed (containers, images, networks and
volumes). You can change the label name in the web UI.

```bash
docker volume create --label prunemate.keep postgres_data
```

```yaml
# in a compose file
volumes:
  db_data:
    labels:
      - prunemate.keep
```

**Minimum age** - only remove resources older than a number of hours, so something you just created or stopped
isn't cleaned up right away. This works for containers, images, networks and build cache (Docker has no age
filter for volumes). For images, the age is the build date, not the date you pulled it.

The preview shows how many resources are skipped by these rules. And manual runs only remove volumes after
you type `DELETE` in the preview.

---

## 🔐 Turn on the login

Without a login, anyone who can reach port 7676 can change settings and delete Docker resources. PruneMate
shows a warning banner until you turn it on.

**1. Generate a password hash:**

```bash
docker run --rm anoniemerd/prunemate:latest python prunemate.py --gen-hash "your_password"
```

**2. Add it to your compose file** and run `docker compose up -d` again:

```yaml
    environment:
      - PRUNEMATE_AUTH_USER=admin
      - PRUNEMATE_AUTH_PASSWORD_HASH=<output from the command above>
```

Good to know:
- Avoid `!` and `$` in your password (the shell treats them specially). `# @ % * & ^` are fine.
- After 5 wrong passwords from the same IP, logins are blocked for 5 minutes.
- Tools like Homepage can log in with Basic Auth using your real password.

More options (session length, HTTPS, reverse proxies): [Configuration](docs/configuration.md#authentication).

---

## ⚙️ Common settings

| Variable | Default | What it does |
|----------|---------|--------------|
| `PRUNEMATE_TZ` | `UTC` | Timezone for the schedule, e.g. `Europe/Amsterdam` |
| `PRUNEMATE_TIME_24H` | `true` | `false` for 12-hour (AM/PM) time |
| `PRUNEMATE_AUTH_USER` | `admin` | Login username |
| `PRUNEMATE_AUTH_PASSWORD_HASH` | _(none)_ | Turns on the login |
| `PRUNEMATE_DOCKER_HOST` | `unix:///var/run/docker.sock` | How PruneMate reaches the local Docker, e.g. `tcp://socket-proxy:2375` |
| `PRUNEMATE_API_KEY` | _(none)_ | Requires a key for the stats endpoints (Homepage widget) |
| `PRUNEMATE_COOKIE_SECURE` | `false` | Set to `true` when you serve PruneMate over HTTPS |
| `PRUNEMATE_TRUSTED_PROXIES` | `0` | Set to `1` behind a reverse proxy (Nginx Proxy Manager, Traefik, …) |

All settings: [Configuration](docs/configuration.md#environment-variables).

---

## 🔔 Notifications

Under **Notifications** in the web UI, pick a provider, fill in the fields and click
**Save & send test notification** to check it.

| Provider | What you need |
|----------|---------------|
| [Gotify](https://gotify.net/) | Server URL and an application token |
| [ntfy](https://ntfy.sh/) | Server URL (e.g. `https://ntfy.sh`) and a topic; optionally a token or `user:pass@` in the URL |
| [Discord](https://discord.com/) | A webhook URL (Server settings → Integrations → Webhooks) |
| [Telegram](https://core.telegram.org/bots) | A bot token from @BotFather and your chat ID |

You can choose a priority (low, medium, high) and whether you only want a message when something was actually
removed. Saved tokens are never shown again; leave the field empty to keep them.

Step-by-step per provider: [Notifications](docs/notifications.md).

---

## 🌐 Multiple Docker hosts

PruneMate can clean up other Docker machines as well. Each run covers the local host plus every enabled remote
host, with results per host.

**1.** On the remote machine, run a socket proxy:

```yaml
services:
  dockerproxy:
    image: ghcr.io/tecnativa/docker-socket-proxy:latest
    environment:
      - CONTAINERS=1
      - IMAGES=1
      - NETWORKS=1
      - VOLUMES=1
      - BUILD=1
      - SYSTEM=1
      - POST=1
    ports:
      - "192.168.1.50:2375:2375"   # the machine's LAN IP
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped
```

**2.** In PruneMate, open **Docker hosts → Add host** and enter a name and `tcp://192.168.1.50:2375`.

The status dot turns green when the host is reachable. Port 2375 isn't encrypted, so only use it on a network
you trust. For encrypted connections with TLS, see [Multiple Docker hosts](docs/multi-host.md#option-2-tls-encrypted).

---

## 🏠 Homepage widget

Add this to Homepage's `services.yaml`:

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
        - field: spaceReclaimedHuman
          label: Space Saved
```

<p align="center">
  <img width="658" alt="Homepage widget" src="https://github.com/user-attachments/assets/ae27323a-53d1-4ef3-acfc-a363b7f854de" />
</p>

All available fields and the API key option: [Homepage widget](docs/homepage.md).

---

## 🧠 How it works

1. Every minute, PruneMate checks whether a scheduled run is due.
2. It connects to the local Docker host and every enabled remote host.
3. On each host it runs the cleanup options you selected, skipping protected resources.
4. It adds the results to the all-time statistics and the run history.
5. It sends a notification, if configured.
6. Everything is written to the log (without passwords or tokens).

Manual runs work the same way, but start with a preview that you confirm first.
More detail: [Architecture](ARCHITECTURE.md).

---

## 🩺 Troubleshooting

| Problem | Fix |
|---------|-----|
| Can't open the web UI | Check the container runs (`docker ps`) and port 7676 isn't blocked or already in use |
| *"Settings can't be saved"* banner | Run `sudo chown -R root:root ./config ./logs`, or remove `cap_drop: ALL` |
| Local Docker shows nothing / errors | With the socket proxy, check `PRUNEMATE_DOCKER_HOST=tcp://socket-proxy:2375` |
| Remote host stays red | Check the proxy runs and port 2375 is reachable; the reason is in **Logs** |
| Notifications don't arrive | Use **Save & send test notification** and check **Logs** for the error |
| Schedule runs at the wrong time | Set `PRUNEMATE_TZ` and restart the container |

The **Logs** button in the top right of the web UI shows what PruneMate is doing.
More problems and fixes: [Troubleshooting](docs/troubleshooting.md).

---

## ⬆️ Upgrading from V1.4.0

```bash
docker compose pull && docker compose up -d
```

Your settings, hosts, notification tokens, statistics and login are kept, and earlier runs are imported from
your existing logs into the new run history. A few things to know:

- You'll need to sign in once more.
- Resources with the `prunemate.keep` label are now skipped (you can turn this off).
- Your existing compose file keeps working. Switching to the socket proxy setup above is optional but recommended.
- V1.4.0 wrote some notification tokens to its log file. After the first start of V2.0.0, delete the old
  `./logs/prunemate.log*` files and consider renewing those tokens.

All changes: [Changelog](CHANGELOG.md).

---

## 📚 Documentation

| Topic | |
|-------|---|
| [Configuration](docs/configuration.md) | All settings, login details, security features, files and logs |
| [Notifications](docs/notifications.md) | Step-by-step setup for Gotify, ntfy, Discord and Telegram |
| [Multiple Docker hosts](docs/multi-host.md) | Socket proxy and TLS setup |
| [Homepage widget](docs/homepage.md) | Widget configuration and all available fields |
| [Troubleshooting](docs/troubleshooting.md) | Common problems and fixes |
| [Architecture](ARCHITECTURE.md) | How PruneMate works inside |
| [Changelog](CHANGELOG.md) | What changed per version |

---

## 📬 Support

- 🐛 **Bug reports & feature requests:** [GitHub issues](https://github.com/anoniemerd/PruneMate/issues)
- 💬 **Questions & discussion:** [GitHub discussions](https://github.com/anoniemerd/PruneMate/discussions)
- ⭐ **Like PruneMate?** Give it a star!

## ☕ Support the project

If PruneMate is useful to you, consider buying me a coffee. It helps me spend more time on maintaining and
improving PruneMate! ❤️

<p align="center">
  <a href="https://www.buymeacoffee.com/anoniemerd" target="_blank">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"/>
  </a>
</p>

## 👥 Contributors

- **[@difagume](https://github.com/difagume)** - 🔐 Authentication system (V1.3.0)
- **[@shollyethan](https://github.com/shollyethan)** - 🎨 Logo redesign and the Self-Hosted Dashboard Icons entry
- **[anoniemerd](https://github.com/anoniemerd)** - Creator and maintainer

## 📜 License

PruneMate is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. If you use, modify or
distribute it, you must keep the copyright notice, publish the source of modified versions (also when you run
them as a network service) and license derivative works under AGPL-3.0. See [`LICENSE`](./LICENSE).

**Disclaimer:** PruneMate is provided "as is", without warranty of any kind. The authors are not responsible for
data loss, downtime or any other damage. Always check the preview, keep backups of important data and start
with conservative settings.

© 2026 – PruneMate Project

---

<p align="center">
  <strong>Keep your Docker host clean with PruneMate! 🐳🧹</strong>
</p>
