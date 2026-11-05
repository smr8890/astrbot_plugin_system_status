from __future__ import annotations

import os
import platform as platform_module
import re
import shutil
import socket
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import psutil

from .utils import (
    cfg,
    format_duration,
    get_astrbot_data_path,
    human_bytes,
)


def count_plugins() -> int:
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


def os_display_name() -> str:
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


def kernel_display() -> str:
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
            [
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$PSVersionTable.PSVersion.ToString()",
            ],
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


def terminal_display() -> str:
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


def cpu_brand() -> str:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform_module.processor() or platform_module.machine() or "CPU"


def cpu_frequency_ghz() -> str:
    try:
        freq = psutil.cpu_freq()
        if freq and freq.max:
            return f"{freq.max / 1000:.2f}GHz"
        if freq and freq.current:
            return f"{freq.current / 1000:.2f}GHz"
    except Exception:
        pass
    return ""


def gpu_info() -> dict[str, Any] | None:
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


def dmi_value(name: str) -> str:
    try:
        value = (
            Path(f"/sys/class/dmi/id/{name}")
            .read_text(encoding="utf-8", errors="ignore")
            .strip()
        )
        return value
    except Exception:
        return ""


def display_resolution() -> str:
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
                for line in path.read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines():
                    line = line.strip()
                    if line:
                        return line
            except Exception:
                continue
    except Exception:
        pass
    return ""


def package_count() -> str:
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


def fastfetch_sync(
    config: Any, snapshot: dict[str, Any]
) -> list[dict[str, Any]] | None:
    show = cfg(config, "showFastFetch", True)
    if show is False:
        return None

    os_icon = {
        "windows": ("\uf17a", "#0078D4"),
        "darwin": ("\uf179", "#555555"),
    }.get(platform_module.system().lower(), ("\uf17c", "#6670F6"))

    fastfetch = shutil.which("fastfetch")
    if fastfetch:
        config_path = (
            Path(__file__).resolve().parent.parent
            / "resources"
            / "state"
            / "fastfetch"
            / "config.jsonc"
        )
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
                        "icon": {
                            "color": match.group(2),
                            "icon": match.group(1).strip(),
                        },
                        "key": match.group(3).strip(),
                        "value": match.group(4).strip(),
                    }
                )
            if parsed:
                for item in parsed:
                    if item.get("key") == "OS":
                        item["icon"] = {"color": os_icon[1], "icon": os_icon[0]}
                return parsed
        except Exception:
            pass

    # Match yenai-plugin's fastfetch/neowofetch system information layout.
    icons = {
        "OS": os_icon,
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

    os_name = os_display_name()
    if os_name:
        items.append(("OS", os_name))

    host = dmi_value("product_name") or socket.gethostname()
    if host:
        items.append(("主机", host))

    kernel = kernel_display()
    if kernel:
        items.append(("内核", kernel))

    items.append(
        ("运行时间", format_duration(max(0.0, time.time() - psutil.boot_time())))
    )

    packages = package_count()
    if packages:
        items.append(("包", packages))

    terminal = terminal_display()
    if terminal:
        items.append(("终端", terminal))

    resolution = display_resolution()
    if resolution:
        items.append(("分辨率", resolution))

    brand = cpu_brand()
    cores = psutil.cpu_count(logical=True) or 0
    frequency = cpu_frequency_ghz().replace("GHz", " GHz")
    cpu_value = f"{brand} ({cores})"
    if frequency:
        cpu_value += f" @ {frequency}"
    if brand:
        items.append(("CPU", cpu_value))

    memory = psutil.virtual_memory()
    items.append(
        (
            "内存",
            f"{human_bytes(memory.used)} / {human_bytes(memory.total)} "
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


def collect_other_info(
    config: Any, platforms: list[dict[str, Any]], db_stats: dict[str, Any]
) -> dict[str, Any]:
    uptime = max(0.0, time.time() - psutil.boot_time())
    boot_time = time.time() - uptime
    hostname_override = str(cfg(config, "hostname.override", "") or "").strip()
    plugin_count = count_plugins()
    return {
        "osInfo": {
            "main": platform_module.system(),
            "secondary": os_display_name(),
        },
        "hostname": {"main": hostname_override or socket.gethostname()},
        "sysTime": {
            "main": format_duration(uptime),
            "secondary": time.strftime("%m/%d %H:%M:%S", time.localtime(boot_time)),
        },
        "pluginNum": {
            "main": f"{plugin_count} plugins",
            "secondary": f"{len(platforms)} platforms",
        },
        "messageCount": int(db_stats.get("total_messages") or 0),
        "sessionCount": int(db_stats.get("total_conversations") or 0),
    }
