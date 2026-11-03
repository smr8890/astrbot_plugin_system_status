from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from .utils import human_bytes, human_speed

DEFAULT_COLORS = ("#2ec7c9", "#b6a2de", "#84A0DF", "#2EC272", "#F2A65A")
CHART_WINDOW_MS = 60 * 60 * 1000


def _as_points(raw: Any) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if not isinstance(raw, list):
        return points
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            points.append((x, y))
    points.sort(key=lambda item: item[0])
    return points


def _format_axis(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.0f}%"
    if unit == "B":
        return human_bytes(value)
    if unit == "B/s":
        parts = human_speed(value)
        return parts.replace("/s", "")
    return f"{value:.1f}"


def _format_time(ts_ms: float) -> str:
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M")
    except (OSError, ValueError):
        return "--"


def render_line_chart(
    title: str,
    series: list[dict[str, Any]],
    *,
    width: int = 820,
    height: int = 320,
    unit: str = "",
    colors: tuple[str, ...] | list[str] = DEFAULT_COLORS,
    fixed_max: float | None = None,
    compact: bool = False,
    show_points: bool = False,
) -> str:
    prepared: list[dict[str, Any]] = []
    all_points: list[tuple[float, float]] = []
    for index, item in enumerate(series):
        points = _as_points(item.get("points"))
        if not points:
            continue
        prepared.append(
            {
                "name": str(item.get("name") or ""),
                "points": points,
                "color": str(
                    item.get("color")
                    or colors[index % len(colors)]
                    or DEFAULT_COLORS[index % len(DEFAULT_COLORS)]
                ),
            }
        )
        all_points.extend(points)

    if not prepared or len(all_points) < 2:
        return '<div class="chart-empty">暂无足够监控数据</div>'

    # Keep only the latest hour of samples. When less data is available,
    # the axis naturally stretches across the retained time span.
    latest_timestamp = max(point[0] for point in all_points)
    cutoff_timestamp = latest_timestamp - CHART_WINDOW_MS
    filtered: list[dict[str, Any]] = []
    filtered_points: list[tuple[float, float]] = []
    for item in prepared:
        points = [point for point in item["points"] if point[0] >= cutoff_timestamp]
        if not points:
            continue
        filtered.append({**item, "points": points})
        filtered_points.extend(points)
    if not filtered or len(filtered_points) < 2:
        return '<div class="chart-empty">暂无足够监控数据</div>'
    prepared = filtered
    all_points = filtered_points

    x_min = min(point[0] for point in all_points)
    x_max = max(point[0] for point in all_points)
    if x_max <= x_min:
        x_max = x_min + 1000

    y_max = max(point[1] for point in all_points)
    if fixed_max is not None:
        y_max = fixed_max
    else:
        y_max = max(1.0, y_max * 1.12)

    grid_count = 4
    axis_values = [y_max * step / grid_count for step in range(grid_count + 1)]
    axis_labels = [_format_axis(value, unit) for value in axis_values]
    widest_axis_label = max(len(label) for label in axis_labels)
    margin_left = max(
        58 if not compact else 52,
        int(widest_axis_label * 6.4) + 14,
    )
    margin_right = 18
    margin_top = 55 if not compact else 46
    margin_bottom = 30
    plot_width = max(20, width - margin_left - margin_right)
    plot_height = max(20, height - margin_top - margin_bottom)

    def x_coord(value: float) -> float:
        return margin_left + (value - x_min) / (x_max - x_min) * plot_width

    def y_coord(value: float) -> float:
        value = max(0.0, min(y_max, value))
        return margin_top + plot_height - value / y_max * plot_height

    gradient_prefix = f"g-{uuid.uuid4().hex[:8]}"
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="{escape(title)}">',
        "<defs>",
    ]
    for index, item in enumerate(prepared):
        parts.append(
            f'<linearGradient id="{gradient_prefix}-{index}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{item["color"]}" stop-opacity="0.26"/>'
            f'<stop offset="100%" stop-color="{item["color"]}" stop-opacity="0.02"/>'
            "</linearGradient>"
        )
    parts.append("</defs>")
    parts.append(
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="transparent"/>'
    )

    # Title and legend.
    title_size = 20 if not compact else 18
    parts.append(
        f'<text x="{margin_left}" y="25" font-size="{title_size}" font-weight="700" '
        f'fill="#333333" font-family="FZB, Noto Sans CJK SC, sans-serif">{escape(title)}</text>'
    )
    legend_x = width - margin_right
    for index, item in reversed(list(enumerate(prepared))):
        label = escape(item["name"])
        text_width = max(36, len(item["name"]) * (13 if not compact else 12))
        legend_x -= text_width + 22
        parts.append(
            f'<rect x="{legend_x}" y="15" width="10" height="10" rx="2" fill="{item["color"]}"/>'
        )
        parts.append(
            f'<text x="{legend_x + 15}" y="24" font-size="{12 if not compact else 11}" '
            f'fill="#555555" font-family="FZB, Noto Sans CJK SC, sans-serif">{label}</text>'
        )

    # Horizontal grid and y-axis labels.
    for value, label in zip(axis_values, axis_labels):
        y = y_coord(value)
        parts.append(
            f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" '
            f'stroke="#888888" stroke-opacity="0.22" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{margin_left - 8}" y="{y + 4:.2f}" text-anchor="end" font-size="11" '
            f'fill="#666666" font-family="FZB, Noto Sans CJK SC, sans-serif">'
            f"{escape(label)}</text>"
        )

    # X-axis labels.
    for step in range(3):
        ratio = step / 2
        timestamp = x_min + (x_max - x_min) * ratio
        x = x_coord(timestamp)
        anchor = "start" if step == 0 else "end" if step == 2 else "middle"
        parts.append(
            f'<text x="{x:.2f}" y="{height - 9}" text-anchor="{anchor}" font-size="11" '
            f'fill="#666666" font-family="FZB, Noto Sans CJK SC, sans-serif">'
            f"{escape(_format_time(timestamp))}</text>"
        )

    # Series paths.
    peaks: list[tuple[float, float, str, str]] = []
    for index, item in enumerate(prepared):
        coords = [(x_coord(x), y_coord(y)) for x, y in item["points"]]
        max_point = max(item["points"], key=lambda point: point[1])
        peak_label = (
            f"{max_point[1]:.1f}%"
            if unit == "%"
            else _format_axis(max_point[1], unit)
        )
        peaks.append(
            (
                x_coord(max_point[0]),
                y_coord(max_point[1]),
                item["color"],
                peak_label,
            )
        )
        line_path = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in coords)
        base_y = y_coord(0)
        area_path = (
            f"{line_path} L {coords[-1][0]:.2f} {base_y:.2f} "
            f"L {coords[0][0]:.2f} {base_y:.2f} Z"
        )
        parts.append(
            f'<path d="{area_path}" fill="url(#{gradient_prefix}-{index})" stroke="none"/>'
        )
        parts.append(
            f'<path d="{line_path}" fill="none" stroke="{item["color"]}" stroke-width="2.2" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
        if show_points:
            for point_x, point_y in coords:
                parts.append(
                    f'<circle cx="{point_x:.2f}" cy="{point_y:.2f}" r="2.4" '
                    f'fill="{item["color"]}"/>'
                )
        else:
            last_x, last_y = coords[-1]
            parts.append(
                f'<circle cx="{last_x:.2f}" cy="{last_y:.2f}" r="3.2" '
                f'fill="{item["color"]}"/>'
            )

    # Draw one rounded bubble for the peak of each series.
    for marker_x, marker_y, marker_color, marker_label in peaks:
        label_width = max(36.0, len(marker_label) * 6.8 + 16)
        bubble_height = 20.0
        bubble_bottom = marker_y - 8
        bubble_top = bubble_bottom - bubble_height
        bubble_x = min(
            max(marker_x - label_width / 2, margin_left),
            width - margin_right - label_width,
        )
        tail_x = min(max(marker_x, bubble_x + 8), bubble_x + label_width - 8)
        parts.append(
            f'<path d="M {tail_x - 6:.2f} {bubble_bottom - 2:.2f} '
            f'L {marker_x:.2f} {marker_y:.2f} '
            f'L {tail_x + 6:.2f} {bubble_bottom - 2:.2f} Z" '
            f'fill="{marker_color}"/>'
        )
        parts.append(
            f'<rect x="{bubble_x:.2f}" y="{bubble_top:.2f}" '
            f'width="{label_width:.2f}" height="{bubble_height:.2f}" '
            f'rx="10" fill="{marker_color}" fill-opacity="0.95"/>'
        )
        parts.append(
            f'<text x="{bubble_x + label_width / 2:.2f}" '
            f'y="{bubble_top + bubble_height / 2 + 4:.2f}" '
            f'text-anchor="middle" font-size="10.5" font-weight="700" '
            f'fill="#ffffff" font-family="FZB, Noto Sans CJK SC, sans-serif">'
            f"{escape(marker_label)}</text>"
        )

    parts.append("</svg>")
    return "".join(parts)


def build_monitor_charts(
    history: dict[str, list[list[float]]],
    colors: tuple[str, ...] | list[str] = DEFAULT_COLORS,
) -> list[dict[str, str]]:
    palette = tuple(colors) if colors else DEFAULT_COLORS
    return [
        {
            "svg": render_line_chart(
                "CPU",
                [{"name": "CPU", "points": history.get("cpu", []), "color": palette[0]}],
                unit="%",
                colors=palette,
                show_points=True,
            )
        },
        {
            "svg": render_line_chart(
                "RAM",
                [
                    {
                        "name": "RAM",
                        "points": history.get("ram_active", []),
                        "color": palette[1],
                    }
                ],
                unit="B",
                colors=palette,
                show_points=True,
            )
        },
        {
            "svg": render_line_chart(
                "Network",
                [
                    {
                        "name": "上行",
                        "points": history.get("net_up", []),
                        "color": palette[0],
                    },
                    {
                        "name": "下行",
                        "points": history.get("net_down", []),
                        "color": palette[1],
                    },
                ],
                unit="B/s",
                colors=palette,
            )
        },
        {
            "svg": render_line_chart(
                "Disk IO",
                [
                    {
                        "name": "读",
                        "points": history.get("disk_read", []),
                        "color": palette[0],
                    },
                    {
                        "name": "写",
                        "points": history.get("disk_write", []),
                        "color": palette[1],
                    },
                ],
                unit="B/s",
                colors=palette,
            )
        },
    ]


def build_network_sparkline(
    history: dict[str, list[list[float]]],
    colors: tuple[str, ...] | list[str] = DEFAULT_COLORS,
) -> str:
    palette = tuple(colors) if colors else DEFAULT_COLORS
    return render_line_chart(
        "Network",
        [
            {"name": "上行", "points": history.get("net_up", []), "color": palette[0]},
            {"name": "下行", "points": history.get("net_down", []), "color": palette[1]},
        ],
        width=760,
        height=240,
        unit="B/s",
        colors=palette,
        compact=True,
    )
