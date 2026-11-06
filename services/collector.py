from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import psutil

from .db_stats import query_database
from .network import collect_network
from .platforms import PlatformCollector
from .processes import collect_processes_sync
from .resources import collect_disks, collect_resources
from .system_info import collect_other_info, fastfetch_sync
from .utils import ASTRBOT_VERSION, cfg


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
    return "v1.7.0"


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
        self.sampler = sampler
        self.plugin_version = _read_plugin_version(plugin_dir)
        self.platforms = PlatformCollector(
            context=context,
            config=config,
            plugin_dir=plugin_dir,
            identity_cache=identity_cache,
        )

    def export_identity_cache(self) -> dict[str, dict[str, Any]]:
        return self.platforms.export_identity_cache()

    async def collect(self, event: Any) -> dict[str, Any]:
        try:
            db = self.context.get_db()
        except Exception:
            db = None
        snapshot, db_stats = await asyncio.gather(
            self.sampler.get_snapshot(max_age=120),
            query_database(db),
        )

        platforms = await self.platforms.collect(event, db_stats)
        other_info = collect_other_info(self.config, platforms, db_stats)
        visual_data = collect_resources(self.config, snapshot)
        disks = collect_disks(self.config, snapshot)

        network, process_load, fast_fetch = await asyncio.gather(
            collect_network(self.config, snapshot),
            asyncio.to_thread(collect_processes_sync, self.config),
            asyncio.to_thread(fastfetch_sync, self.config, snapshot),
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
                "medium": str(
                    cfg(self.config, "style.progressBarColor.medium", "#ffa500")
                ),
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
            f'Created By AstrBot <span class="version">v{ASTRBOT_VERSION}</span> '
            f"&amp; astrbot_plugin_system_status"
            f'<span class="version">{self.plugin_version}</span>'
            f"<br>Rendered via AstrBot T2I &amp; psutil "
            f'<span class="version">{psutil.__version__}</span>'
        )
