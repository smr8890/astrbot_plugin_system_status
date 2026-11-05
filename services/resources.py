from __future__ import annotations

import os
from typing import Any

import psutil

from .system_info import cpu_brand, cpu_frequency_ghz, gpu_info
from .utils import (
    bytes_parts,
    cfg,
    circle_percent,
    disk_circle,
    disk_color,
    human_bytes,
)


def collect_resources(config: Any, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    resources = list(
        cfg(config, "systemResources", ["CPU", "RAM", "SWAP", "GPU"]) or []
    )
    low_colors = cfg(
        config,
        "style.progressBarColor.low",
        ["#84A0DF", "#2EC272", "#8070F9"],
    )
    if not isinstance(low_colors, list):
        low_colors = [str(low_colors)]
    color_index = 0

    def user_color() -> str | None:
        nonlocal color_index
        if not low_colors:
            return None
        color = str(low_colors[color_index % len(low_colors)])
        color_index += 1
        return color

    result: list[dict[str, Any]] = []
    for resource in resources:
        title = str(resource).upper()
        if title == "CPU":
            fraction = float(snapshot.get("cpu", 0.0)) / 100
            brand = cpu_brand()
            manufacturer = brand.split()[0] if brand else "unknown"
            cores = psutil.cpu_count(logical=True) or 0
            frequency = cpu_frequency_ghz()
            result.append(
                {
                    "percentage": circle_percent(fraction, user_color=user_color()),
                    "inner": f"{round(fraction * 100)}%",
                    "title": "CPU",
                    "detailed": brand,
                    "info": [
                        f"{manufacturer} {cores}核 {frequency}".strip(),
                    ],
                }
            )
        elif title == "RAM":
            total = float(snapshot.get("ram_total") or 0)
            active = float(snapshot.get("ram_active") or snapshot.get("ram_used") or 0)
            used = float(snapshot.get("ram_used") or 0)
            buffcache = float(snapshot.get("ram_buffers") or 0) + float(
                snapshot.get("ram_cached") or 0
            )
            active_fraction = active / total if total else 0.0
            used_fraction = used / total if total else 0.0
            result.append(
                {
                    "percentage": circle_percent(
                        active_fraction, user_color=user_color()
                    ),
                    "inner": f"{round(active_fraction * 100)}%",
                    "title": "RAM",
                    "detailed": False,
                    "info": [
                        f"{human_bytes(active)} / {human_bytes(total)}",
                        f"缓冲区/缓存 {human_bytes(buffcache)}"
                        if buffcache > 0
                        else "",
                    ],
                    "buffcache": {
                        "percentage": {
                            **circle_percent(used_fraction),
                            "color": "#bcbbbbb0",
                        },
                        "isBuff": buffcache > 0,
                    },
                }
            )
        elif title == "SWAP":
            total = float(snapshot.get("swap_total") or 0)
            used = float(snapshot.get("swap_used") or 0)
            free = float(snapshot.get("swap_free") or 0)
            if total <= 0:
                continue
            fraction = used / total
            result.append(
                {
                    "percentage": circle_percent(fraction, user_color=user_color()),
                    "inner": f"{round(fraction * 100)}%",
                    "title": "SWAP",
                    "detailed": f"Available {human_bytes(free)}",
                    "info": [f"{human_bytes(used)} / {human_bytes(total)}"],
                }
            )
        elif title == "GPU":
            gpu = gpu_info()
            if gpu:
                gpu["percentage"] = circle_percent(
                    float(gpu.get("percentage") or 0), user_color=user_color()
                )
                result.append(gpu)
    return result


def collect_disks(config: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    low_colors = cfg(
        config,
        "style.progressBarColor.low",
        ["#84A0DF", "#2EC272", "#8070F9"],
    )
    if not isinstance(low_colors, list):
        low_colors = [str(low_colors)]

    mounts: list[tuple[str, str]] = []
    try:
        for partition in psutil.disk_partitions(all=False):
            mountpoint = partition.mountpoint
            if mountpoint in ("/etc/hostname", "/etc/hosts", "/etc/resolv.conf"):
                continue
            if mountpoint.startswith("/usr/share/zoneinfo"):
                continue
            mounts.append((mountpoint, partition.fstype))
    except Exception:
        pass
    if os.name != "nt" and not any(mountpoint == "/" for mountpoint, _ in mounts):
        root_fstype = "unknown"
        try:
            root_partition = next(
                (
                    partition
                    for partition in psutil.disk_partitions(all=True)
                    if partition.mountpoint == "/"
                ),
                None,
            )
            if root_partition and root_partition.fstype:
                root_fstype = root_partition.fstype
        except Exception:
            pass
        mounts.insert(0, ("/", root_fstype))
    mounts.sort(key=lambda item: item[0] != "/")

    seen: set[tuple[int, int]] = set()
    disks: list[dict[str, Any]] = []
    color_index = 0
    for mountpoint, fstype in mounts:
        try:
            usage = psutil.disk_usage(mountpoint)
        except Exception:
            continue
        key = (int(usage.total), int(usage.used))
        if key in seen:
            continue
        seen.add(key)
        percent = float(usage.percent)
        color = disk_color(
            percent,
            str(low_colors[color_index % len(low_colors)]) if low_colors else None,
        )
        color_index += 1
        label = mountpoint
        if mountpoint == "/":
            label = "/ (根目录)"
        disks.append(
            {
                "mount": label,
                "used": human_bytes(usage.used),
                "size": human_bytes(usage.total),
                "available": human_bytes(usage.free),
                "use": round(percent),
                "color": color,
                "per": disk_circle(percent / 100),
                "type": fstype or "unknown",
            }
        )

    disks_io = []
    io_list = snapshot.get("disk_io") or []
    if io_list:
        read_sec = sum(float(io.get("rIO_sec") or 0.0) for io in io_list)
        write_sec = sum(float(io.get("wIO_sec") or 0.0) for io in io_list)
        read_parts = bytes_parts(read_sec)
        write_parts = bytes_parts(write_sec)
        disks_io.append(
            {
                "name": "Disk IO",
                "rIO_sec": f"<span>{read_parts['size']} {read_parts['suffix']}</span>",
                "wIO_sec": f"<span>{write_parts['size']} {write_parts['suffix']}</span>",
                "tIO_sec": False,
            }
        )

    return {
        "disksSize": disks or None,
        "disksIo": disks_io or None,
    }
