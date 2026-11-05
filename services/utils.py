from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from astrbot.core.config import VERSION as ASTRBOT_VERSION
except Exception:  # pragma: no cover - compatibility fallback
    ASTRBOT_VERSION = "4.x"

try:
    from astrbot.core.utils.astrbot_path import (
        get_astrbot_data_path,
        get_astrbot_temp_path,
    )
except Exception:  # pragma: no cover - compatibility fallback

    def get_astrbot_data_path() -> str:
        return str(Path(__file__).resolve().parents[3])

    def get_astrbot_temp_path() -> str:
        return str(Path(__file__).resolve().parents[3] / "temp")


UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def cfg(config: Any, path: str, default: Any = None) -> Any:
    """Read a dotted-path value from an AstrBot config object.

    Args:
        config: AstrBot config object or mapping.
        path: Dotted key path, for example ``monitor.interval``.
        default: Value returned when the path is missing or empty.

    Returns:
        The configured value, or ``default`` when unavailable.
    """
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


def _trim_number(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def bytes_parts(num: float | int | None, *, precision: int = 2) -> dict[str, str]:
    """Return a byte value split into a display size and a unit."""
    if num is None:
        return {"size": "--", "suffix": ""}
    try:
        value = float(num)
    except (TypeError, ValueError):
        return {"size": "--", "suffix": ""}

    value = max(0.0, value)
    index = 0
    while value >= 1024 and index < len(UNITS) - 1:
        value /= 1024
        index += 1

    if index == 0:
        size = str(int(round(value)))
    else:
        size = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    return {"size": size, "suffix": UNITS[index]}


def human_bytes(num: float | int | None, *, precision: int = 2) -> str:
    if num is None:
        return "--"
    parts = bytes_parts(num, precision=precision)
    if not parts["suffix"]:
        return parts["size"]
    return f"{parts['size']} {parts['suffix']}"


def human_speed(num: float | int | None) -> str:
    parts = bytes_parts(num)
    if not parts["suffix"]:
        return parts["size"]
    return f"{parts['size']} {parts['suffix']}/s"


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "--"
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        return "--"

    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}天 {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_datetime(timestamp: float | int | None, fmt: str = "%m/%d %H:%M:%S") -> str:
    if timestamp is None:
        return "--"
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime(fmt)
    except (TypeError, ValueError, OSError):
        return "--"


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def circle_percent(
    fraction: float | None,
    *,
    perimeter: float = 3.14 * 88,
    user_color: str | None = None,
) -> dict[str, object]:
    """Build the values consumed by the yenai SVG circle CSS."""
    value = clamp(float(fraction or 0.0))
    per = perimeter - perimeter * value
    color = user_color or "var(--low-color)"
    if value >= 0.9:
        color = "var(--high-color)"
    elif value >= 0.8:
        color = "var(--medium-color)"
    return {"per": round(per, 2), "color": color}


def disk_circle(fraction: float | None) -> float:
    value = clamp(float(fraction or 0.0))
    perimeter = 3.14 * 54
    return round(perimeter - perimeter * value, 2)


def disk_color(percent: float, user_color: str | None = None) -> str:
    if percent >= 90:
        return "var(--high-color)"
    if percent >= 70:
        return "var(--medium-color)"
    return user_color or "var(--low-color)"


def color_for_latency(delay_ms: float) -> str:
    if delay_ms > 2000:
        return "#F44336"
    if delay_ms > 500:
        return "#d68100"
    return "#188038"


def color_for_status(status: int) -> str:
    if status >= 500:
        return "#9C27B0"
    if status >= 400:
        return "#F44336"
    if status >= 300:
        return "#FF9800"
    if status >= 200:
        return "#188038"
    if status >= 100:
        return "#03A9F4"
    return ""


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp_text(value: object, limit: int = 120) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * clamp(p)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)
