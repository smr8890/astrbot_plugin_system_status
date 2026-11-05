from __future__ import annotations

import time
from typing import Any

import psutil

from .utils import cfg, human_bytes

PROCESS_STATE_MAP = {
    "running": "running",
    "disk-sleep": "blocked",
    "uninterruptible": "blocked",
    "wake-kill": "blocked",
    "parked": "blocked",
    "sleeping": "sleeping",
}


def _process_state_name(state: str) -> str:
    return PROCESS_STATE_MAP.get(state, "unknown")


def collect_processes_sync(config: Any) -> dict[str, Any] | None:
    show = cfg(config, "processLoad.show", "pro")
    if show is False:
        return None
    if show == "pro":
        # AstrBot has no pro variant; keep the section visible.
        pass

    show_max = cfg(config, "processLoad.showMax", {}) or {}
    show_num = max(1, int(cfg(show_max, "showNum", 6) or 6))
    order = str(cfg(show_max, "order", "mem") or "mem")
    show_cmd = bool(cfg(config, "processLoad.showCmd", False))
    filter_list = set(cfg(config, "processLoad.filterList", []) or [])
    watch_list = [str(item) for item in (cfg(config, "processLoad.list", []) or [])]

    attrs = ["pid", "name", "cmdline", "status", "memory_info", "ppid"]
    processes = []
    for process in psutil.process_iter(attrs):
        try:
            process.cpu_percent(None)
            processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(0.25)

    rows: list[dict[str, Any]] = []
    counts = {"all": 0, "running": 0, "blocked": 0, "sleeping": 0, "unknown": 0}
    for process in processes:
        try:
            info = process.info
            name = str(info.get("name") or "")
            if name in filter_list:
                continue
            cmdline = info.get("cmdline") or []
            command = " ".join(str(part) for part in cmdline if part)
            cpu = float(process.cpu_percent(None) or 0.0)
            memory_info = info.get("memory_info")
            rss = int(getattr(memory_info, "rss", 0) or 0)
            state = _process_state_name(str(info.get("status") or ""))
            counts["all"] += 1
            counts[state] = counts.get(state, 0) + 1
            rows.append(
                {
                    "pid": int(info.get("pid") or 0),
                    "ppid": int(info.get("ppid") or 0),
                    "name": name or f"PID {info.get('pid')}",
                    "command": command or name,
                    "cpu": cpu,
                    "memRss": rss,
                    "state": state,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError):
            continue

    child_counts: dict[int, int] = {}
    for row in rows:
        parent = row.get("ppid", 0)
        if parent:
            child_counts[parent] = child_counts.get(parent, 0) + 1

    def select(order_name: str, limit: int) -> list[dict[str, Any]]:
        key = "memRss" if order_name == "mem" else "cpu"
        return sorted(rows, key=lambda item: item.get(key, 0), reverse=True)[:limit]

    selected: list[dict[str, Any]] = []
    if show_max.get("show", True):
        if order == "cpu":
            selected = select("cpu", show_num)
        elif order == "cpu_mem":
            cpu_count = max(1, (show_num + 1) // 2)
            mem_count = max(1, show_num - cpu_count)
            selected = select("cpu", cpu_count) + ["hr"] + select("mem", mem_count)
        else:
            selected = select("mem", show_num)

    selected_keys = {
        (item["pid"] if isinstance(item, dict) else None) for item in selected
    }
    watched: list[dict[str, Any]] = []
    if watch_list:
        for row in rows:
            if row["pid"] in selected_keys:
                continue
            if any(
                token == row["name"]
                or token == row["command"]
                or token in row["command"]
                for token in watch_list
                if token
            ):
                watched.append(row)
        if watched:
            selected.append("hr")
            selected.extend(watched)

    result_rows: list[dict[str, Any] | str] = []
    for item in selected:
        if item == "hr":
            result_rows.append("hr")
            continue
        children = child_counts.get(item["pid"], 0)
        child_suffix = f"({children})" if children else ""
        display_name = item["command"] if show_cmd else item["name"]
        result_rows.append(
            {
                "name": f"{display_name}{child_suffix}",
                "pid": item["pid"],
                "cpu": f"{item['cpu']:.1f}%",
                "mem": human_bytes(item["memRss"]),
                "state": item["state"],
            }
        )

    return {
        **counts,
        "order": "" if order not in ("cpu", "mem", "cpu_mem") else order,
        "list": result_rows,
    }
