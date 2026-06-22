# HomeLab Docker Agent

Read-only Docker inventory and metrics agent for HomeLab.

The agent runs on a Docker host, reads the local Docker socket, and pushes inventory/metrics back to HomeLab over HTTPS.

## Features

- Container inventory
- Image inventory
- Network inventory
- Volume inventory
- Docker Compose project detection
- Basic CPU/RAM stats
- Outbound-only check-in to HomeLab
- No Docker API port exposure required

## Docker run

```bash
docker run -d \
  --name homelab-agent \
  --restart unless-stopped \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e HOMELAB_URL=https://your-homelab-url \
  -e HOMELAB_AGENT_TOKEN=replace-with-generated-token \
  -e HOMELAB_AGENT_NAME=docker-host-01 \
  -e HOMELAB_POLL_SECONDS=30 \
  ghcr.io/antybubbs/homelab-agent:latest
```

## Docker Compose

```bash
docker compose up -d
```

## Environment variables

| Variable | Required | Default | Description |
|---|---:|---|---|
| `HOMELAB_URL` | yes | | Base URL of HomeLab |
| `HOMELAB_AGENT_TOKEN` | yes | | Agent token generated in HomeLab |
| `HOMELAB_AGENT_NAME` | no | hostname | Friendly agent name |
| `HOMELAB_POLL_SECONDS` | no | 30 | Check-in interval |
| `HOMELAB_VERIFY_TLS` | no | true | Verify HTTPS certificates |
| `DOCKER_HOST` | no | unix:///var/run/docker.sock | Docker socket/API endpoint |

## Security notes

This agent mounts the Docker socket read-only, but Docker socket access is still highly sensitive. Run it only on hosts you trust.

The first version is monitoring-only and does not accept remote commands from HomeLab.
