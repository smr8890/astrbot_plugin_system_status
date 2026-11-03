from __future__ import annotations

import asyncio
import os
from functools import lru_cache
import platform as platform_module
import re
import shutil
import socket
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import aiohttp
import psutil

try:
    from astrbot.core.config import VERSION as ASTRBOT_VERSION
except Exception:  # pragma: no cover - compatibility fallback
    ASTRBOT_VERSION = "4.x"

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover
    def get_astrbot_data_path() -> str:
        return str(Path(__file__).resolve().parents[3])


STATUS_ICON_MAP = {
    "running": "11",
    "error": "50",
    "stopped": "60",
    "pending": "70",
}

PROCESS_STATE_MAP = {
    "running": "running",
    "disk-sleep": "blocked",
    "uninterruptible": "blocked",
    "wake-kill": "blocked",
    "parked": "blocked",
    "sleeping": "sleeping",
}


def cfg(config: Any, path: str, default: Any = None) -> Any:
    current: Any = config
    for key in path.split("."):
        if current is None:
            return default
        if hasattr(current, "get"):
            current = current.get(key, default)
        elif isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return default if current is None else current


def _find_db_path() -> Path | None:
    data_dir = Path(get_astrbot_data_path())
    candidates = sorted(
        data_dir.glob("data_v*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _read_plugin_version(plugin_dir: Path) -> str:
    try:
        text = (plugin_dir / "metadata.yaml").read_text(
            encoding="utf-8", errors="ignore"
        )
        match = re.search(r"^version:\s*[\"']?([^\"'\s]+)", text, re.MULTILINE)
        if match:
            version = match.group(1).strip()
            return version if version.startswith("v") else f"v{version}"
    except Exception:
        pass
    return "v1.3.0"


def _query_database(db_path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "messages": {},
        "total_messages": 0,
        "conversations": {},
        "total_conversations": 0,
        "sessions": {},
    }
    if db_path is None or not db_path.exists():
        return result

    try:
        connection = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=3,
        )
    except Exception:
        return result

    try:
        cursor = connection.cursor()
        try:
            rows = cursor.execute(
                "SELECT platform_id, COALESCE(SUM(count), 0) "
                "FROM platform_stats GROUP BY platform_id"
            ).fetchall()
            for platform_id, count in rows:
                result["messages"][str(platform_id)] = int(count or 0)
            result["total_messages"] = sum(result["messages"].values())
        except Exception:
            pass

        try:
            rows = cursor.execute(
                "SELECT platform_id, user_id FROM conversations"
            ).fetchall()
            conversations: dict[str, dict[str, int]] = {}
            for platform_id, user_id in rows:
                key = str(platform_id or "")
                bucket = conversations.setdefault(
                    key, {"total": 0, "group": 0, "private": 0}
                )
                bucket["total"] += 1
                if "GroupMessage" in str(user_id or ""):
                    bucket["group"] += 1
                else:
                    bucket["private"] += 1
            result["conversations"] = conversations
            result["total_conversations"] = sum(
                item["total"] for item in conversations.values()
            )
        except Exception:
            pass

        try:
            rows = cursor.execute(
                "SELECT platform_id, is_group, COUNT(*) "
                "FROM platform_sessions GROUP BY platform_id, is_group"
            ).fetchall()
            sessions: dict[str, dict[str, int]] = {}
            for platform_id, is_group, count in rows:
                key = str(platform_id or "")
                bucket = sessions.setdefault(
                    key, {"total": 0, "group": 0, "private": 0}
                )
                bucket["total"] += int(count or 0)
                if int(is_group or 0):
                    bucket["group"] += int(count or 0)
                else:
                    bucket["private"] += int(count or 0)
            result["sessions"] = sessions
        except Exception:
            pass
    finally:
        try:
            connection.close()
        except Exception:
            pass
    return result


def _count_plugins() -> int:
    plugins_dir = Path(get_astrbot_data_path()) / "plugins"
    if not plugins_dir.is_dir():
        return 0
    count = 0
    for child in plugins_dir.iterdir():
        if not child.is_dir():
            continue
        if (child / "metadata.yaml").exists() or (child / "main.py").exists():
            count += 1
    return count


def _read_os_release() -> str:
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            if line.startswith("PRETTY_NAME="):
                value = line.split("=", 1)[1].strip().strip('"')
                if value:
                    return value
    return platform_module.platform()


def _os_display_name() -> str:
    if os.name == "nt":
        try:
            release, version, _csd, _ptype = platform_module.win32_ver()
            edition = ""
            try:
                edition = platform_module.win32_edition() or ""
            except Exception:
                pass
            name = f"Windows {release or ''}".strip()
            if edition and edition.lower() not in name.lower():
                name += f" {edition}"
            if version:
                name += f" ({version})"
            return name
        except Exception:
            return platform_module.platform()
    return _read_os_release()


def _kernel_display() -> str:
    if os.name == "nt":
        try:
            release, version, _csd, _ptype = platform_module.win32_ver()
        except Exception:
            release, version = platform_module.release(), platform_module.version()
        release = release or platform_module.release()
        version = version or platform_module.version()
        if release and version:
            return f"Windows {release} ({version})"
        return f"Windows {release or version}".strip()
    system = platform_module.system()
    release = platform_module.release()
    return f"{system} {release}".strip()


def _run_shell_version_command(executable: str, args: list[str]) -> str:
    try:
        process = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            errors="ignore",
        )
        return ((process.stdout or "") + "\n" + (process.stderr or "")).strip()
    except Exception:
        return ""


@lru_cache(maxsize=8)
def _shell_version(shell: str) -> str:
    name = Path(shell).name.lower()
    if name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        output = _run_shell_version_command(
            shell,
            ["-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"],
        )
        match = re.search(r"(\d+(?:\.\d+)+)", output)
        return match.group(1) if match else ""
    if name in {"bash", "bash.exe"}:
        output = _run_shell_version_command(shell, ["--version"])
        match = re.search(r"version\s+(\d+(?:\.\d+)+)", output)
        return match.group(1) if match else ""
    if name in {"zsh", "zsh.exe"}:
        output = _run_shell_version_command(shell, ["--version"])
        match = re.search(r"zsh\s+(\d+(?:\.\d+)+)", output)
        return match.group(1) if match else ""
    if name in {"fish", "fish.exe"}:
        output = _run_shell_version_command(shell, ["--version"])
        match = re.search(r"version\s+(\d+(?:\.\d+)+)", output)
        return match.group(1) if match else ""
    return ""


def _terminal_display() -> str:
    shell = os.environ.get("SHELL", "").strip()
    if not shell:
        return ""
    name = Path(shell).name
    lower = name.lower()
    if lower in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        display_name = "PowerShell"
    else:
        display_name = name[:-4] if lower.endswith(".exe") else name
    version = _shell_version(shell)
    return f"{display_name} {version}".strip() if version else display_name

def _cpu_brand() -> str:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform_module.processor() or platform_module.machine() or "CPU"


def _cpu_frequency_ghz() -> str:
    try:
        freq = psutil.cpu_freq()
        if freq and freq.max:
            return f"{freq.max / 1000:.2f}GHz"
        if freq and freq.current:
            return f"{freq.current / 1000:.2f}GHz"
    except Exception:
        pass
    return ""


def _gpu_info() -> dict[str, Any] | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        process = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        line = next(
            (item.strip() for item in process.stdout.splitlines() if item.strip()),
            "",
        )
        if not line:
            return None
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            return None
        name, utilization, memory_used, memory_total, temperature = parts[:5]
        utilization_value = float(utilization)
        used_gb = float(memory_used) / 1024
        total_gb = float(memory_total) / 1024
        vendor = name.split()[0] if name else "GPU"
        temperature_text = f"{temperature}℃" if temperature not in ("", "[N/A]") else ""
        return {
            "percentage": max(0.0, min(1.0, utilization_value / 100)),
            "inner": f"{round(utilization_value)}%",
            "title": "GPU",
            "detailed": name,
            "info": [
                f"{used_gb:.2f} GB / {total_gb:.2f} GB",
                f"{vendor} {temperature_text}".strip(),
            ],
        }
    except Exception:
        return None


def _process_state_name(state: str) -> str:
    return PROCESS_STATE_MAP.get(state, "unknown")


def _collect_processes_sync(config: Any) -> dict[str, Any] | None:
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
                token == row["name"] or token == row["command"] or token in row["command"]
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
                "mem": _human_memory(item["memRss"]),
                "state": item["state"],
            }
        )

    return {
        **counts,
        "order": "" if order not in ("cpu", "mem", "cpu_mem") else order,
        "list": result_rows,
    }


def _human_memory(value: int | float | None) -> str:
    from .utils import human_bytes

    return human_bytes(value)


def _dmi_value(name: str) -> str:
    try:
        value = Path(f"/sys/class/dmi/id/{name}").read_text(
            encoding="utf-8", errors="ignore"
        ).strip()
        return value
    except Exception:
        return ""


def _display_resolution() -> str:
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            width = int(user32.GetSystemMetrics(0))
            height = int(user32.GetSystemMetrics(1))
            if width and height:
                return f"{width}x{height}"
        except Exception:
            pass
    try:
        for path in sorted(Path("/sys/class/drm").glob("card*-*/modes")):
            try:
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line:
                        return line
            except Exception:
                continue
    except Exception:
        pass
    return ""


def _package_count() -> str:
    try:
        process = subprocess.run(
            ["dpkg-query", "-f", ".\n", "-W"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        count = sum(1 for line in process.stdout.splitlines() if line.strip())
        if count > 0:
            return f"{count} (dpkg)"
    except Exception:
        pass
    return ""


def _fastfetch_sync(config: Any, snapshot: dict[str, Any]) -> list[dict[str, Any]] | None:
    show = cfg(config, "showFastFetch", True)
    if show is False:
        return None

    fastfetch = shutil.which("fastfetch")
    if fastfetch:
        config_path = Path(__file__).resolve().parent.parent / "resources" / "state" / "fastfetch" / "config.jsonc"
        try:
            process = subprocess.run(
                [fastfetch, "--config", str(config_path)],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            pattern = re.compile(r"^(.*)\s+\((#[0-9A-Fa-f]{6})\)(.*?): (.*)")
            parsed = []
            for line in process.stdout.splitlines():
                if ":" not in line:
                    continue
                match = pattern.match(line)
                if not match:
                    continue
                parsed.append(
                    {
                        "icon": {"color": match.group(2), "icon": match.group(1).strip()},
                        "key": match.group(3).strip(),
                        "value": match.group(4).strip(),
                    }
                )
            if parsed:
                return parsed
        except Exception:
            pass

    # Match yenai-plugin's fastfetch/neowofetch system information layout.
    icons = {
        "OS": ("\uf17c", "#6670F6"),
        "内核": ("\uf013", "#6640F6"),
        "运行时间": ("\ue641", "#45BC83"),
        "CPU": ("\uf4bc", "#95959B"),
        "内存": ("\U000f046d", "#788760"),
        "包": ("\U000f03d6", "#F4A460"),
        "主机": ("\U000f0322", "#82df0e"),
        "终端": ("\uf489", "#20B2AA"),
        "分辨率": ("\U000f0379", "#FF69B4"),
    }
    items: list[tuple[str, str]] = []

    os_name = _os_display_name()
    if os_name:
        items.append(("OS", os_name))

    host = _dmi_value("product_name") or socket.gethostname()
    if host:
        items.append(("主机", host))

    kernel = _kernel_display()
    if kernel:
        items.append(("内核", kernel))

    items.append(
        ("运行时间", _format_uptime_short(max(0.0, time.time() - psutil.boot_time())))
    )

    packages = _package_count()
    if packages:
        items.append(("包", packages))

    terminal = _terminal_display()
    if terminal:
        items.append(("终端", terminal))

    resolution = _display_resolution()
    if resolution:
        items.append(("分辨率", resolution))

    brand = _cpu_brand()
    cores = psutil.cpu_count(logical=True) or 0
    frequency = _cpu_frequency_ghz().replace("GHz", " GHz")
    cpu_value = f"{brand} ({cores})"
    if frequency:
        cpu_value += f" @ {frequency}"
    if brand:
        items.append(("CPU", cpu_value))

    memory = psutil.virtual_memory()
    items.append(
        (
            "内存",
            f"{_human_memory(memory.used)} / {_human_memory(memory.total)} "
            f"({memory.percent:.0f}%)",
        )
    )

    return [
        {
            "icon": {"color": icons[key][1], "icon": icons[key][0]},
            "key": key,
            "value": value,
        }
        for key, value in items
    ]

def _format_uptime_short(seconds: float) -> str:
    from .utils import format_duration

    return format_duration(seconds)


class SystemCollector:
    def __init__(
        self,
        *,
        context: Any,
        config: Any,
        plugin_dir: Path,
        sampler: Any,
        logger: Any,
        identity_cache: dict[str, Any] | None = None,
    ) -> None:
        self.context = context
        self.config = config
        self.plugin_dir = plugin_dir
        self.sampler = sampler
        self.logger = logger
        self.db_path = _find_db_path()
        self.plugin_version = _read_plugin_version(plugin_dir)
        now = time.time()
        self._identity_cache: dict[str, tuple[float, float, dict[str, Any]]] = {
            str(platform_id): (now, 300.0, dict(value))
            for platform_id, value in (identity_cache or {}).items()
            if isinstance(value, dict)
        }
        self._process_uptime = self._get_process_uptime()

    def _get_process_uptime(self) -> float:
        try:
            return max(0.0, time.time() - psutil.Process().create_time())
        except Exception:
            return 0.0

    def export_identity_cache(self) -> dict[str, dict[str, Any]]:
        return {
            platform_id: dict(entry[2])
            for platform_id, entry in self._identity_cache.items()
            if entry[1] >= 300 and entry[2].get("name")
        }

    async def collect(self, event: Any) -> dict[str, Any]:
        snapshot, db_stats = await asyncio.gather(
            self.sampler.get_snapshot(max_age=120),
            asyncio.to_thread(_query_database, self.db_path),
        )

        platforms = await self._collect_platforms(event, db_stats)
        other_info = self._collect_other_info(platforms, db_stats)
        visual_data = self._collect_resources(snapshot)
        disks = self._collect_disks(snapshot)

        network, process_load, fast_fetch = await asyncio.gather(
            self._collect_network(snapshot),
            asyncio.to_thread(_collect_processes_sync, self.config),
            asyncio.to_thread(_fastfetch_sync, self.config, snapshot),
        )

        chart_colors = tuple(
            cfg(self.config, "chartsCfg.color", ["#2ec7c9", "#b6a2de"]) or []
        )
        network["chart_svg"] = ""
        if cfg(self.config, "chartsCfg.show", True):
            from .svg_chart import build_network_sparkline

            network["chart_svg"] = build_network_sparkline(
                self.sampler.history, chart_colors
            )

        return {
            "BotStatusList": platforms,
            "otherInfo": other_info,
            "visualData": visual_data,
            "disks": disks,
            "network": network,
            "processLoad": process_load,
            "fastFetch": fast_fetch,
            "style": self._style_config(),
            "copyright": self._copyright(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def _collect_platforms(
        self, event: Any, db_stats: dict[str, Any]
    ) -> list[dict[str, Any]]:
        manager = getattr(self.context, "platform_manager", None)
        platforms: list[Any] = []
        if manager is not None:
            try:
                platforms = list(manager.get_insts())
            except Exception:
                platforms = list(getattr(manager, "platform_insts", []) or [])

        current_platform_id = ""
        try:
            current_platform_id = event.get_platform_id()
        except Exception:
            pass

        bots: list[dict[str, Any]] = []
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            identity = await self._platform_identity(platform, meta)
            platform_id = str(getattr(meta, "id", "") or "")
            platform_type = str(getattr(meta, "name", "") or "")
            if platform_type == "webchat" or platform_id == "webchat":
                continue
            display_name = (
                getattr(meta, "adapter_display_name", None)
                or platform_type
                or platform_id
                or "平台"
            )
            if platform_id and platform_id != platform_type:
                platform_label = f"{display_name} · {platform_id}"
            else:
                platform_label = display_name

            conversation = (db_stats.get("conversations") or {}).get(
                platform_id, {"total": 0, "group": 0, "private": 0}
            )
            session = (db_stats.get("sessions") or {}).get(
                platform_id, {"total": 0, "group": 0, "private": 0}
            )
            message_count = int(
                (db_stats.get("messages") or {}).get(platform_type)
                or (db_stats.get("messages") or {}).get(platform_id)
                or 0
            )
            group_count = int(conversation.get("group") or session.get("group") or 0)
            private_count = int(
                conversation.get("private") or session.get("private") or 0
            )
            total_sessions = int(
                conversation.get("total") or session.get("total") or 0
            )

            started_at = getattr(platform, "_started_at", None)
            if started_at is not None:
                try:
                    runtime = max(0.0, time.time() - started_at.timestamp())
                except Exception:
                    runtime = self._process_uptime
            else:
                runtime = self._process_uptime

            status = "pending"
            try:
                status = platform.status.value
            except Exception:
                pass

            bots.append(
                {
                    "avatar": identity.get("avatar")
                    or str(self.plugin_dir / "resources" / "state" / "img" / "default_avatar.jpg"),
                    "nickname": identity.get("name")
                    or f"{display_name} {platform_id}".strip(),
                    "status": STATUS_ICON_MAP.get(status, "11"),
                    "botVersion": f"AstrBot v{ASTRBOT_VERSION}",
                    "platform": platform_label,
                    "botRunTime": _format_uptime_short(runtime),
                    "countContacts": {
                        "friend": self._count_chip(total_sessions, "会话"),
                        "group": self._count_chip(group_count, "群聊"),
                        "groupMember": self._count_chip(private_count, "私聊"),
                    },
                    "messageCount": {
                        "sent": self._count_chip(message_count, "消息"),
                    },
                    "contact_icons": {
                        "friend": "friend",
                        "group": "group",
                        "groupMember": "groupMember",
                    },
                    "message_icons": {"sent": "sent"},
                    "is_current": platform_id == current_platform_id,
                }
            )

        if not bots:
            bots.append(self._fallback_bot(event, db_stats))

        bots.sort(key=lambda item: (not item.get("is_current"), item.get("nickname", "")))
        return bots

    @staticmethod
    def _count_chip(count: int, suffix: str) -> str:
        if not count:
            return ""
        return f'{count} <span class="suffix">{suffix}</span>'

    def _fallback_bot(self, event: Any, db_stats: dict[str, Any]) -> dict[str, Any]:
        try:
            platform_name = event.get_platform_name()
        except Exception:
            platform_name = "AstrBot"
        is_webchat = platform_name == "webchat"
        return {
            "avatar": str(
                self.plugin_dir
                / "resources"
                / "state"
                / "img"
                / ("astrbot.svg" if is_webchat else "default_avatar.jpg")
            ),
            "nickname": "AstrBot" if is_webchat else platform_name,
            "status": "11",
            "botVersion": f"AstrBot v{ASTRBOT_VERSION}",
            "platform": "AstrBot" if is_webchat else platform_name,
            "botRunTime": _format_uptime_short(self._process_uptime),
            "countContacts": {
                "friend": self._count_chip(
                    int(db_stats.get("total_conversations") or 0), "会话"
                )
            },
            "messageCount": {
                "sent": self._count_chip(
                    int(db_stats.get("total_messages") or 0), "消息"
                )
            },
            "contact_icons": {"friend": "friend"},
            "message_icons": {"sent": "sent"},
            "is_current": True,
        }

    async def _platform_identity(self, platform: Any, meta: Any) -> dict[str, Any]:
        platform_id = str(getattr(meta, "id", "") or "")
        cached = self._identity_cache.get(platform_id)
        if cached and time.time() - cached[0] < cached[1]:
            return dict(cached[2])

        platform_type = str(getattr(meta, "name", "") or "")
        name = str(cfg(self.config, "bot.name", "") or "").strip()
        avatar = str(cfg(self.config, "bot.avatar", "") or "").strip()
        identity = {"name": name, "avatar": avatar}
        resolved = False

        try:
            if platform_type == "aiocqhttp":
                client = platform.get_client()
                info = await asyncio.wait_for(
                    client.call_action("get_login_info"), timeout=3
                )
                uid = str((info or {}).get("user_id") or "")
                identity["name"] = identity["name"] or str(
                    (info or {}).get("nickname") or ""
                )
                if not identity["avatar"] and uid:
                    identity["avatar"] = (
                        f"https://q1.qlogo.cn/g?b=qq&s=0&nk={uid}"
                    )
                resolved = bool(identity["name"] or identity["avatar"])
            elif platform_type == "qq_official":
                robot = getattr(getattr(platform, "client", None), "robot", None)
                if robot is not None:
                    identity["name"] = identity["name"] or str(
                        getattr(robot, "name", "") or ""
                    )
                    identity["avatar"] = identity["avatar"] or str(
                        getattr(robot, "avatar", "") or ""
                    )
                    resolved = bool(identity["name"] or identity["avatar"])
            elif platform_type == "telegram":
                client = platform.get_client()
                me = await asyncio.wait_for(client.get_me(), timeout=3)
                identity["name"] = identity["name"] or str(
                    getattr(me, "first_name", "")
                    or getattr(me, "username", "")
                    or ""
                )
                resolved = bool(identity["name"])
            elif platform_type == "discord":
                client = getattr(platform, "client", None)
                user = getattr(client, "user", None)
                if user is not None:
                    identity["name"] = identity["name"] or str(
                        getattr(user, "name", "") or getattr(user, "display_name", "") or ""
                    )
                    avatar_obj = getattr(user, "display_avatar", None) or getattr(
                        user, "avatar", None
                    )
                    if not identity["avatar"] and avatar_obj is not None:
                        identity["avatar"] = str(getattr(avatar_obj, "url", avatar_obj))
                    resolved = bool(identity["name"] or identity["avatar"])
        except Exception:
            pass

        if platform_type == "webchat":
            identity["name"] = identity["name"] or "AstrBot WebUI"
            identity["avatar"] = identity["avatar"] or str(
                self.plugin_dir / "resources" / "state" / "img" / "astrbot.svg"
            )
        if not identity["name"]:
            identity["name"] = str(
                platform_id
                or getattr(meta, "adapter_display_name", None)
                or platform_type
                or "AstrBot"
            )
        ttl = 300.0 if resolved else 5.0
        self._identity_cache[platform_id] = (time.time(), ttl, dict(identity))
        return identity

    def _collect_other_info(
        self, platforms: list[dict[str, Any]], db_stats: dict[str, Any]
    ) -> dict[str, Any]:
        uptime = max(0.0, time.time() - psutil.boot_time())
        boot_time = time.time() - uptime
        hostname_override = str(cfg(self.config, "hostname.override", "") or "").strip()
        plugin_count = _count_plugins()
        return {
            "osInfo": {
                "main": platform_module.system(),
                "secondary": _os_display_name(),
            },
            "hostname": {"main": hostname_override or socket.gethostname()},
            "sysTime": {
                "main": _format_uptime_short(uptime),
                "secondary": time.strftime("%m/%d %H:%M:%S", time.localtime(boot_time)),
            },
            "pluginNum": {
                "main": f"{plugin_count} plugins",
                "secondary": f"{len(platforms)} platforms",
            },
            "messageCount": int(db_stats.get("total_messages") or 0),
            "sessionCount": int(db_stats.get("total_conversations") or 0),
        }

    def _collect_resources(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        from .utils import circle_percent, human_bytes

        resources = list(cfg(self.config, "systemResources", ["CPU", "RAM", "SWAP", "GPU"]) or [])
        low_colors = cfg(
            self.config,
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
                brand = _cpu_brand()
                manufacturer = brand.split()[0] if brand else "unknown"
                cores = psutil.cpu_count(logical=True) or 0
                frequency = _cpu_frequency_ghz()
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
                gpu = _gpu_info()
                if gpu:
                    gpu["percentage"] = circle_percent(
                        float(gpu.get("percentage") or 0), user_color=user_color()
                    )
                    result.append(gpu)
        return result

    def _collect_disks(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        from .utils import (
            bytes_parts,
            disk_circle,
            disk_color,
            human_bytes,
        )

        low_colors = cfg(
            self.config,
            "style.progressBarColor.low",
            ["#84A0DF", "#2EC272", "#8070F9"],
        )
        if not isinstance(low_colors, list):
            low_colors = [str(low_colors)]

        mounts: list[tuple[str, str]] = [("/", "overlay")]
        try:
            for partition in psutil.disk_partitions(all=False):
                mountpoint = partition.mountpoint
                if mountpoint in ("/etc/hostname", "/etc/hosts", "/etc/resolv.conf"):
                    continue
                if mountpoint.startswith("/usr/share/zoneinfo"):
                    continue
                if mountpoint == "/":
                    continue
                mounts.append((mountpoint, partition.fstype))
        except Exception:
            pass

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
                str(low_colors[color_index % len(low_colors)])
                if low_colors
                else None,
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

    async def _collect_network(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        from .utils import bytes_parts

        network_data = snapshot.get("net")
        speed_data = None
        if network_data:
            upload = bytes_parts(network_data.get("tx_sec"))
            download = bytes_parts(network_data.get("rx_sec"))
            traffic_upload = bytes_parts(network_data.get("tx_bytes"))
            traffic_download = bytes_parts(network_data.get("rx_bytes"))
            speed_data = {
                "speed": {"upload": upload, "download": download},
                "traffic": {
                    "upload": traffic_upload,
                    "download": traffic_download,
                },
            }

        ps_test = await self._network_test()
        return {
            "speed": speed_data,
            "psTest": ps_test,
            "chart_svg": "",
        }

    async def _network_test(self) -> list[dict[str, str]] | None:
        from .utils import color_for_latency, color_for_status

        show = cfg(self.config, "psTestSites.show", "pro")
        if show is False:
            return None
        sites = cfg(self.config, "psTestSites.list", []) or []
        if not sites:
            return None
        timeout = float(cfg(self.config, "psTestSites.timeout", 5000) or 5000) / 1000
        concurrency = max(1, int(cfg(self.config, "psTestSites.concurNum", 5) or 5))
        semaphore = asyncio.Semaphore(concurrency)

        async def check(site: dict[str, Any]) -> dict[str, str]:
            name = str(site.get("name") or site.get("url") or "site")
            url = str(site.get("url") or "")
            if not url:
                return {
                    "name": name,
                    "status": "<span style='color:#F44336'>-</span>",
                    "delay": "<span style='color:#F44336'>Error</span>",
                }
            async with semaphore:
                start = time.perf_counter()
                try:
                    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
                    async with aiohttp.ClientSession(
                        timeout=timeout_cfg, trust_env=True
                    ) as session:
                        async with session.get(url, allow_redirects=True) as response:
                            status = response.status
                            await response.read()
                    delay = (time.perf_counter() - start) * 1000
                    return {
                        "name": name,
                        "status": f"<span style='color:{color_for_status(status)}'>{status}</span>",
                        "delay": f"<span style='color:{color_for_latency(delay)}'>{delay:.0f}ms</span>",
                    }
                except asyncio.TimeoutError:
                    return {
                        "name": name,
                        "status": "<span style='color:#F44336'>-</span>",
                        "delay": "<span style='color:#F44336'>Timeout</span>",
                    }
                except Exception:
                    return {
                        "name": name,
                        "status": "<span style='color:#F44336'>-</span>",
                        "delay": "<span style='color:#F44336'>Error</span>",
                    }

        return list(await asyncio.gather(*(check(site) for site in sites)))

    def _style_config(self) -> dict[str, Any]:
        contacts = cfg(
            self.config,
            "style.botInfoColor.contacts",
            ["#F1EDFE", "#FDF1E6", "#E9F4FC"],
        )
        if not isinstance(contacts, list):
            contacts = [str(contacts)]
        low = cfg(
            self.config,
            "style.progressBarColor.low",
            ["#84A0DF", "#2EC272", "#8070F9"],
        )
        if not isinstance(low, list):
            low = [str(low)]
        return {
            "backdrop": "",
            "BotNameColor": str(cfg(self.config, "style.BotNameColor", "#000")),
            "progressBarColor": {
                "high": str(cfg(self.config, "style.progressBarColor.high", "#d73403")),
                "medium": str(cfg(self.config, "style.progressBarColor.medium", "#ffa500")),
                "low": low,
            },
            "botInfoColor": {
                "botVersion": str(
                    cfg(self.config, "style.botInfoColor.botVersion", "#FBE0F3")
                ),
                "platform": str(
                    cfg(self.config, "style.botInfoColor.platform", "#F0EDF2")
                ),
                "contacts": contacts,
            },
        }

    def _copyright(self) -> str:
        return (
            f"Created By AstrBot <span class=\"version\">v{ASTRBOT_VERSION}</span> "
            f"&amp; astrbot_plugin_system_status"
            f"<span class=\"version\">{self.plugin_version}</span>"
            f"<br>Rendered via AstrBot T2I &amp; psutil "
            f"<span class=\"version\">{psutil.__version__}</span>"
        )
