# Configuration

[← Back to README](../README.md)

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PRUNEMATE_TZ` | `UTC` | Timezone for the schedule, e.g. `Europe/Amsterdam` |
| `PRUNEMATE_TIME_24H` | `true` | `false` for 12-hour (AM/PM) time |
| `PRUNEMATE_AUTH_USER` | `admin` | Login username |
| `PRUNEMATE_AUTH_PASSWORD_HASH` | _(none)_ | Base64 password hash; setting it enables the login |
| `PRUNEMATE_DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker endpoint of the "Local" host, e.g. `tcp://socket-proxy:2375` |
| `PRUNEMATE_API_KEY` | _(none)_ | When set, `/stats` and `/api/stats` require this key (`X-API-Key` header or `?api_key=`) |
| `PRUNEMATE_SESSION_HOURS` | `12` | How long a login stays valid |
| `PRUNEMATE_COOKIE_SECURE` | `false` | Set to `true` when PruneMate is served over HTTPS |
| `PRUNEMATE_TRUSTED_PROXIES` | `0` | Number of reverse proxies in front of PruneMate (for the real client IP) |
| `PRUNEMATE_LOGIN_MAX_ATTEMPTS` | `5` | Failed logins per IP (within 5 minutes) before a lockout |
| `PRUNEMATE_LOGIN_LOCKOUT_MINUTES` | `5` | Lockout duration |
| `PRUNEMATE_SECRET` | _(auto)_ | Session signing key. When unset, a random key is created once in `/config/secret_key` |
| `PRUNEMATE_CONFIG` | `/config/config.json` | Path of the configuration file |
| `PRUNEMATE_CERTS_DIR` | `/certs` | Folder with TLS client certificates for remote hosts |
| `PRUNEMATE_LOG_FILE` | `/var/log/prunemate.log` | Log file (also shown in the log viewer) |

Invalid numbers fall back to the default (with a warning in the log).

## Authentication

The login is off until you set `PRUNEMATE_AUTH_PASSWORD_HASH`.

1. Generate a hash:
   ```bash
   docker run --rm anoniemerd/prunemate:latest python prunemate.py --gen-hash "your_password"
   ```
2. Add it to your compose file and restart (`docker compose up -d`):
   ```yaml
   environment:
     - PRUNEMATE_AUTH_USER=admin
     - PRUNEMATE_AUTH_PASSWORD_HASH=c2NyeXB0OjMyNzY4Ojg6MSRvcDdn...
   ```

Good to know:
- The hash is Base64-encoded because raw hashes contain `$`, which Docker Compose would treat as a variable.
- Avoid `!` and `$` in the password itself (shell expansion). Characters like `# @ % * & ^` are fine.
- API tools such as Homepage can use Basic Auth with the real password (not the hash).
- After 5 failed attempts from one IP, logins are blocked for 5 minutes. Behind a reverse proxy, set
  `PRUNEMATE_TRUSTED_PROXIES=1` so the lockout uses the real client IP instead of the proxy's.

## Security features

| Protection | What it does |
|------------|--------------|
| Session secret | Random key generated on first start; no hard-coded default |
| CSRF tokens | Every action (save, run, hosts, logout) requires a per-session token |
| Content-Security-Policy | Only PruneMate's own scripts and styles run; no external CDNs or fonts |
| Security headers | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS over HTTPS |
| Secrets stay on the server | Tokens and webhook URLs are never sent back to the browser; leave a field empty to keep the stored value |
| Log redaction | Secrets are masked in the log; the log viewer also masks them in lines from older versions |
| Brute-force lockout | Temporary lockout after repeated failed logins (form and Basic Auth) |
| Volume safety | Manual runs only delete volumes after you type `DELETE`; otherwise volumes are skipped for that run |
| Write check | Red banner and log error when `/config` isn't writable, instead of silently losing settings |
| Least privilege | Socket proxy, read-only container, `no-new-privileges`, no Linux capabilities |

Without a login, PruneMate shows a warning banner: anyone who can reach the port can delete Docker resources.

### Note on `cap_drop: ALL`

PruneMate runs as root inside the container and writes to `./config` and `./logs`. If those folders belong to
another host user, root without capabilities can't write to them. Fix it with
`sudo chown -R root:root ./config ./logs`, or remove the `cap_drop` lines from your compose file.

## Cleanup options

| Option | What is removed |
|--------|-----------------|
| Stopped containers | Exited, dead and created containers |
| Unused images | All images not used by a container (not only dangling ones) |
| Unused networks | Custom networks without containers |
| Unused volumes | All volumes not attached to a container, **including named volumes** |
| Build cache | Like `docker builder prune`: unused cache not shared with an existing image |

### Protection rules

- **Keep label**: resources with the label `prunemate.keep` (configurable) are never pruned.
  ```bash
  docker volume create --label prunemate.keep postgres_data
  ```
  ```yaml
  volumes:
    db_data:
      labels:
        - prunemate.keep
  ```
- **Minimum age**: only prune resources older than N hours. Works for containers, images, networks and build
  cache (Docker has no age filter for volumes). For images, age means the build date, not the pull date.

## Files

```
/config/
├── config.json      # Your settings
├── stats.json       # All-time totals
├── history.json     # Last 100 runs (dashboard chart)
├── secret_key       # Session signing key (keep private)
├── prunemate.lock   # Prevents concurrent runs
└── last_run_key     # Prevents a scheduled run from running twice

/var/log/
└── prunemate.log    # Log file (rotating, 5 MB x 4 files)
```

## Logs

Click **Logs** in the top right of the web UI (filter, line count, hide heartbeats), or run
`docker logs prunemate`. The log contains heartbeats, setting changes (secrets masked), prune results,
notification status, login attempts and errors.
