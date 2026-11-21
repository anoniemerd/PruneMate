# Troubleshooting

[← Back to README](../README.md)

Most problems show up in the log: click **Logs** in the web UI or run `docker logs prunemate`.

| Problem | Fix |
|---------|-----|
| Can't open the web UI | Check that the container runs (`docker ps`) and port 7676 isn't blocked or in use |
| "Settings can't be saved" banner | `./config` isn't writable. Run `sudo chown -R root:root ./config ./logs` or remove `cap_drop: ALL` |
| "Session expired or request token invalid" | Reload the page. After changing `PRUNEMATE_SECRET`, sign in again |
| "Too many failed attempts" | Wait 5 minutes. Behind a reverse proxy, set `PRUNEMATE_TRUSTED_PROXIES=1` |
| Docker permission errors | Make sure `/var/run/docker.sock` exists and Docker is running |
| Local host offline with the socket proxy | Check `PRUNEMATE_DOCKER_HOST=tcp://socket-proxy:2375` and that both containers share the `docker-api` network. The proxy needs `CONTAINERS, IMAGES, NETWORKS, VOLUMES, BUILD, SYSTEM, POST=1` |
| Remote host offline | See [Multiple Docker hosts](multi-host.md#troubleshooting) |
| Notifications don't arrive | Use **Save & send test notification** and check the log for the error. Verify URL, token and topic |
| Wrong time for the schedule | Set `PRUNEMATE_TZ` (e.g. `Europe/Amsterdam`) and restart the container |
| Schedule doesn't run | Check that **Enable automatic schedule** is on; the dashboard shows the next run |
| ARM device (Raspberry Pi, Apple Silicon) | The image supports amd64 and arm64; just pull `anoniemerd/prunemate:latest` |
