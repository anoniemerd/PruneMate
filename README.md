# PruneMate

Docker image & resource cleanup helper, on a schedule!

## Features

- **Scheduled Cleanup**: Configure when and how often to run cleanup
- **Flexible Options**: Choose which Docker resources to prune (containers, images, networks, volumes)
- **Timezone Support**: Run cleanup at the right time in your timezone
- **Notifications**: Get notified via ntfy when cleanup runs
- **Smart Notifications**: Option to only notify when something was actually pruned

## Quick Start

1. Clone this repository
2. Copy and modify `docker-compose.yaml` to your needs
3. Run with Docker Compose:

```bash
docker compose up -d
```

## Configuration

All configuration is done via environment variables in `docker-compose.yaml`.

### Schedule

| Variable | Description | Default | Options |
|----------|-------------|---------|---------|
| `TZ` | Timezone | `Europe/Amsterdam` | Any valid timezone |
| `SCHEDULE_FREQUENCY` | How often to run | `daily` | `hourly`, `daily`, `weekly`, `monthly` |
| `SCHEDULE_TIME` | Time to run (HH:MM) | `03:00` | 24-hour format |
| `SCHEDULE_DAY` | Day of week (for weekly) | `*` | `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`, `*` |
| `RUN_ON_STARTUP` | Run immediately on start | `false` | `true`, `false` |

### Cleanup Options

| Variable | Description | Default |
|----------|-------------|---------|
| `PRUNE_CONTAINERS` | Remove unused containers | `true` |
| `PRUNE_IMAGES` | Remove unused images | `true` |
| `PRUNE_NETWORKS` | Remove unused networks | `true` |
| `PRUNE_VOLUMES` | Remove unused volumes | `false` |

> ⚠️ **Warning**: Be careful with `PRUNE_VOLUMES=true` as this will permanently delete data!

### Notifications

| Variable | Description | Default |
|----------|-------------|---------|
| `NOTIFICATIONS_ENABLED` | Enable notifications | `false` |
| `NOTIFY_ONLY_WHEN_PRUNED` | Only notify when something was pruned | `true` |
| `NTFY_URL` | ntfy server URL | `` |
| `NTFY_TOPIC` | ntfy topic name | `` |
| `NTFY_TOKEN` | ntfy authentication token (optional) | `` |

When `NOTIFY_ONLY_WHEN_PRUNED` is disabled, PruneMate sends a notification after every run.

## Example Configurations

### Daily cleanup at 3 AM

```yaml
environment:
  - TZ=Europe/Amsterdam
  - SCHEDULE_FREQUENCY=daily
  - SCHEDULE_TIME=03:00
```

### Weekly cleanup on Sunday at midnight

```yaml
environment:
  - TZ=America/New_York
  - SCHEDULE_FREQUENCY=weekly
  - SCHEDULE_TIME=00:00
  - SCHEDULE_DAY=sun
```

### Hourly cleanup with ntfy notifications

```yaml
environment:
  - SCHEDULE_FREQUENCY=hourly
  - SCHEDULE_TIME=00:30
  - NOTIFICATIONS_ENABLED=true
  - NTFY_URL=https://ntfy.sh
  - NTFY_TOPIC=my-prunemate-topic
```

## Building

```bash
docker build -t prunemate .
```

## Running without Docker Compose

```bash
docker run -d \
  --name prunemate \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e TZ=Europe/Amsterdam \
  -e SCHEDULE_FREQUENCY=daily \
  -e SCHEDULE_TIME=03:00 \
  prunemate
```

## License

MIT License - see [LICENSE](LICENSE) for details