import json
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any

import docker
import requests


HOMELAB_URL = os.getenv("HOMELAB_URL", "").rstrip("/")
AGENT_TOKEN = os.getenv("HOMELAB_AGENT_TOKEN", "")
AGENT_NAME = os.getenv("HOMELAB_AGENT_NAME") or socket.gethostname()
POLL_SECONDS = max(10, int(os.getenv("HOMELAB_POLL_SECONDS", "30")))
VERIFY_TLS = os.getenv("HOMELAB_VERIFY_TLS", "true").lower() not in {"0", "false", "no"}
DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def docker_cpu_percent(stats: dict[str, Any]) -> float | None:
    try:
        current = stats.get("cpu_stats") or {}
        previous = stats.get("precpu_stats") or {}

        current_usage = (current.get("cpu_usage") or {}).get("total_usage", 0)
        previous_usage = (previous.get("cpu_usage") or {}).get("total_usage", 0)

        current_system = current.get("system_cpu_usage", 0)
        previous_system = previous.get("system_cpu_usage", 0)

        cpu_delta = current_usage - previous_usage
        system_delta = current_system - previous_system
        online_cpus = current.get("online_cpus") or len(
            (current.get("cpu_usage") or {}).get("percpu_usage") or []
        ) or 1

        if cpu_delta > 0 and system_delta > 0:
            return round((cpu_delta / system_delta) * online_cpus * 100, 2)
    except Exception:
        return None

    return None


def safe_attrs(obj: Any) -> dict[str, Any]:
    try:
        return obj.attrs or {}
    except Exception:
        return {}


def collect_containers(client: docker.DockerClient) -> list[dict[str, Any]]:
    containers = []

    for container in client.containers.list(all=True):
        attrs = safe_attrs(container)
        state = attrs.get("State") or {}
        config = attrs.get("Config") or {}
        labels = config.get("Labels") or {}
        host_config = attrs.get("HostConfig") or {}
        mounts = attrs.get("Mounts") or []

        stats = {}
        if state.get("Running"):
            try:
                stats = container.stats(stream=False) or {}
            except Exception:
                stats = {}

        memory_stats = stats.get("memory_stats") or {}
        memory_used = memory_stats.get("usage")
        memory_total = memory_stats.get("limit")

        containers.append(
            {
                "external_id": container.id,
                "name": container.name,
                "kind": "container",
                "status": state.get("Status") or container.status or "unknown",
                "image": config.get("Image"),
                "cpu_percent": docker_cpu_percent(stats),
                "memory_used": memory_used,
                "memory_total": memory_total,
                "storage_used": attrs.get("SizeRw"),
                "storage_total": None,
                "uptime_seconds": None,
                "tags": labels.get("com.docker.compose.project"),
                "metadata": {
                    "short_id": container.short_id,
                    "created": attrs.get("Created"),
                    "ports": attrs.get("NetworkSettings", {}).get("Ports") or {},
                    "mounts": mounts,
                    "restart_policy": host_config.get("RestartPolicy"),
                    "compose_project": labels.get("com.docker.compose.project"),
                    "compose_service": labels.get("com.docker.compose.service"),
                },
            }
        )

    return containers


def collect_images(client: docker.DockerClient) -> list[dict[str, Any]]:
    items = []

    for image in client.images.list():
        attrs = safe_attrs(image)
        tags = image.tags or []
        items.append(
            {
                "external_id": image.id,
                "name": tags[0] if tags else image.short_id,
                "kind": "image",
                "status": None,
                "size_bytes": attrs.get("Size"),
                "metadata": {
                    "tags": tags,
                    "created": attrs.get("Created"),
                    "architecture": attrs.get("Architecture"),
                    "os": attrs.get("Os"),
                },
            }
        )

    return items


def collect_networks(client: docker.DockerClient) -> list[dict[str, Any]]:
    items = []

    for network in client.networks.list():
        attrs = safe_attrs(network)
        items.append(
            {
                "external_id": network.id,
                "name": network.name,
                "kind": "network",
                "status": attrs.get("Scope"),
                "size_bytes": None,
                "metadata": {
                    "driver": attrs.get("Driver"),
                    "internal": attrs.get("Internal"),
                    "attachable": attrs.get("Attachable"),
                    "ipam": attrs.get("IPAM"),
                },
            }
        )

    return items


def collect_volumes(client: docker.DockerClient) -> list[dict[str, Any]]:
    items = []

    for volume in client.volumes.list():
        attrs = safe_attrs(volume)
        usage = attrs.get("UsageData") or {}
        items.append(
            {
                "external_id": volume.name,
                "name": volume.name,
                "kind": "volume",
                "status": attrs.get("Scope"),
                "size_bytes": usage.get("Size"),
                "metadata": {
                    "driver": attrs.get("Driver"),
                    "mountpoint": attrs.get("Mountpoint"),
                    "labels": attrs.get("Labels") or {},
                },
            }
        )

    return items


def collect_compose_projects(containers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}

    for container in containers:
        metadata = container.get("metadata") or {}
        project = metadata.get("compose_project")
        if not project:
            continue

        projects.setdefault(
            project,
            {
                "external_id": project,
                "name": project,
                "kind": "compose",
                "status": "active",
                "size_bytes": None,
                "metadata": {"containers": []},
            },
        )

        projects[project]["metadata"]["containers"].append(container["name"])

    return list(projects.values())


def collect_payload() -> dict[str, Any]:
    client = docker.DockerClient(base_url=DOCKER_HOST)
    info = client.info()
    version = client.version()

    containers = collect_containers(client)
    items = []
    items.extend(collect_images(client))
    items.extend(collect_networks(client))
    items.extend(collect_volumes(client))
    items.extend(collect_compose_projects(containers))

    running = [c for c in containers if c.get("status") == "running"]

    return {
        "agent_name": AGENT_NAME,
        "collected_at": utc_now(),
        "platform": "docker-agent",
        "version": version.get("Version"),
        "host": {
            "name": info.get("Name") or AGENT_NAME,
            "cpu_percent": sum(c.get("cpu_percent") or 0 for c in running),
            "memory_used": sum(c.get("memory_used") or 0 for c in running),
            "memory_total": info.get("MemTotal"),
            "storage_used": None,
            "storage_total": None,
            "metadata": {
                "docker_root_dir": info.get("DockerRootDir"),
                "operating_system": info.get("OperatingSystem"),
                "kernel_version": info.get("KernelVersion"),
                "architecture": info.get("Architecture"),
                "cpus": info.get("NCPU"),
                "server_version": info.get("ServerVersion"),
            },
        },
        "workloads": containers,
        "items": items,
    }


def post_payload(payload: dict[str, Any]) -> None:
    if not HOMELAB_URL:
        raise RuntimeError("HOMELAB_URL is not set")

    if not AGENT_TOKEN:
        raise RuntimeError("HOMELAB_AGENT_TOKEN is not set")

    response = requests.post(
        f"{HOMELAB_URL}/infrastructure/vm-docker-manager/api/agent/checkin",
        headers={
            "Authorization": f"Bearer {AGENT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "HomeLab-Docker-Agent/0.1",
        },
        data=json.dumps(payload),
        timeout=30,
        verify=VERIFY_TLS,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"HomeLab check-in failed: HTTP {response.status_code} {response.text[:500]}")


def main() -> None:
    print(f"HomeLab Docker Agent starting as {AGENT_NAME}")
    print(f"HomeLab URL: {HOMELAB_URL}")
    print(f"Docker host: {DOCKER_HOST}")
    print(f"Poll interval: {POLL_SECONDS}s")

    while True:
        try:
            payload = collect_payload()
            post_payload(payload)
            print(f"{utc_now()} check-in successful: {len(payload['workloads'])} workloads")
        except Exception as exc:
            print(f"{utc_now()} check-in failed: {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
