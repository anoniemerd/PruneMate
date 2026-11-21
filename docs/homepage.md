# Homepage dashboard widget

[← Back to README](../README.md)

PruneMate offers `/api/stats` for a [Homepage](https://gethomepage.dev/) widget (or Dashy and similar tools).

<p align="center">
  <img width="658" height="212" alt="Homepage widget" src="https://github.com/user-attachments/assets/ae27323a-53d1-4ef3-acfc-a363b7f854de" />
</p>

Add this to Homepage's `services.yaml`:

```yaml
- PruneMate:
    href: http://<your-server-ip>:7676
    description: Docker image & resource cleanup helper, on a schedule!
    icon: https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp/prunemate.webp
    widget:
      type: customapi
      url: http://<your-server-ip>:7676/api/stats
      # headers:                    # only when PRUNEMATE_API_KEY is set
      #   X-API-Key: your-api-key
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

The endpoint is public unless you set `PRUNEMATE_API_KEY`. Tools that can't send headers can use
`?api_key=your-api-key` (the key may then appear in proxy logs).

## Available fields

| Field | Type | Description |
|-------|------|-------------|
| `pruneRuns` | number | Total number of runs |
| `containersDeleted` | number | Total containers removed |
| `imagesDeleted` | number | Total images removed |
| `networksDeleted` | number | Total networks removed |
| `volumesDeleted` | number | Total volumes removed |
| `buildCacheDeleted` | number | Total build cache entries removed |
| `spaceReclaimed` | number | Total space reclaimed in bytes |
| `spaceReclaimedHuman` | text | e.g. `2.5 GB` |
| `lastRunText` | text | e.g. `2h ago` |
| `lastRunTimestamp` | number | Unix timestamp of the last run |
| `lastRun` | date | ISO timestamp of the last run |
| `firstRun` | date | ISO timestamp of the first run |
