# Multiple Docker hosts

[← Back to README](../README.md)

PruneMate can clean up other Docker hosts too. Every run (scheduled or manual) covers the local host plus all
enabled external hosts, with results per host.

> ⚠️ Never expose a raw Docker socket over the network. Use a socket proxy (below) or TLS.

## Option 1: socket proxy (easiest)

**1. On each remote host**, run [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy):

```yaml
services:
  dockerproxy:
    image: ghcr.io/tecnativa/docker-socket-proxy:latest
    environment:
      - CONTAINERS=1
      - IMAGES=1
      - NETWORKS=1
      - VOLUMES=1
      - BUILD=1    # needed for build cache cleanup
      - SYSTEM=1   # needed for the build cache preview
      - POST=1     # needed to prune
    ports:
      - "192.168.1.50:2375:2375"   # bind to the host's LAN IP
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped
```

Port 2375 has no encryption or password, so only use it on a network you trust.

**2. In PruneMate**, open **Docker hosts → Add host** and enter a name (e.g. `NAS`) and the URL
(e.g. `tcp://192.168.1.50:2375`).

The status dot turns green when the host responds. Use **Preview & run** to see what would be removed per host.

## Option 2: TLS (encrypted)

For Docker daemons that are exposed with TLS (`dockerd --tlsverify`, port 2376):

1. Put the client certificates for each host in its own folder:
   ```
   ./certs/
   └── nas/
       ├── ca.pem     # CA that signed the daemon's certificate
       ├── cert.pem   # client certificate
       └── key.pem    # client key
   ```
2. Mount the folder read-only in PruneMate: `- ./certs:/certs:ro`
3. Add the host with URL `tcp://nas.lan:2376` and **TLS folder** `nas`.

PruneMate checks the server certificate against `ca.pem`, including the host name. Use the same name in the
URL as in the daemon's certificate.

Note: the client certificate gives full access to that Docker host, so keep `key.pem` private.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Connection refused | Check that the proxy is running (`docker ps`) and port 2375 is reachable |
| 403 / permission denied | The proxy needs `POST=1` (and `BUILD=1` for build cache) |
| TLS host offline | Open **Logs** in PruneMate. "Hostname mismatch" means the URL name isn't in the daemon certificate |
| Local host offline with the socket proxy | Check `PRUNEMATE_DOCKER_HOST=tcp://socket-proxy:2375` and that both containers share the `docker-api` network |
