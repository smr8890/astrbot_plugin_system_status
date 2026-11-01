from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

from .services.collector import SystemCollector
from .services.renderer import StatusRenderer
from .services.sampler import ResourceSampler
from .services.svg_chart import build_monitor_charts


@register(
    "astrbot_plugin_system_status",
    "smr8890",
    "系统状态卡片，视觉复刻 yenai-plugin，使用 AstrBot T2I 渲染",
    "1.1.1",
)
class SystemStatusPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).resolve().parent
        self.sampler = ResourceSampler(
            interval=self._conf_int("monitor.interval", 60),
            history_size=self._conf_int("monitor.history_size", 60),
            logger=self.logger,
        )
        self.sampler.set_persist_callback(self._persist_history)
        self.collector: SystemCollector | None = None
        self.renderer: StatusRenderer | None = None
        self._render_lock = asyncio.Lock()

    def _conf(self, path: str, default: Any = None) -> Any:
        current: Any = self.config
        for key in path.split("."):
            if current is None:
                return default
            if hasattr(current, "get"):
                current = current.get(key, default)
            else:
                return default
        return default if current is None else current

    def _conf_int(self, path: str, default: int) -> int:
        try:
            return int(self._conf(path, default) or default)
        except (TypeError, ValueError):
            return default

    async def initialize(self) -> None:
        self.renderer = StatusRenderer(
            star=self,
            config=self.config,
            plugin_dir=self.plugin_dir,
            logger=self.logger,
        )
        identity_cache = await self.get_kv_data("bot_identity_cache", {})
        self.collector = SystemCollector(
            context=self.context,
            config=self.config,
            plugin_dir=self.plugin_dir,
            sampler=self.sampler,
            logger=self.logger,
            identity_cache=identity_cache,
        )
        history = await self.get_kv_data("monitor_history", {})
        self.sampler.load_history(history)
        await self.sampler.start()

    async def terminate(self) -> None:
        await self.sampler.stop()

    async def _persist_history(self, data: dict[str, Any]) -> None:
        await self.put_kv_data("monitor_history", data)

    @filter.command("状态")
    async def status(self, event: AstrMessageEvent):
        """查看 AstrBot 系统状态卡片。"""
        data: dict[str, Any] | None = None
        try:
            if self.collector is None or self.renderer is None:
                yield event.plain_result("状态卡片尚未初始化完成，请稍后再试。").use_t2i(False)
                return

            async with self._render_lock:
                data = await self.collector.collect(event)
                await self.put_kv_data(
                    "bot_identity_cache", self.collector.export_identity_cache()
                )
                path = await self.renderer.render_status(
                    data, umo=getattr(event, "unified_msg_origin", None)
                )
            yield event.chain_result([Image.fromFileSystem(path)])
        except Exception as exc:
            logger.exception("渲染系统状态卡片失败: %s", exc)
            if data:
                yield event.plain_result(self._fallback_text(data)).use_t2i(False)
            else:
                yield event.plain_result(
                    "系统状态卡片渲染失败，请检查 AstrBot T2I 服务是否正常。"
                ).use_t2i(False)

    @filter.command("状态监控")
    async def monitor(self, event: AstrMessageEvent):
        """查看 CPU / RAM / 网络 / 磁盘 IO 监控图表。"""
        try:
            if self.renderer is None or self.collector is None:
                yield event.plain_result("状态监控尚未初始化完成，请稍后再试。").use_t2i(False)
                return

            history = self.sampler.history
            if sum(len(points) for points in history.values()) < 4:
                yield event.plain_result(
                    "监控数据不足，请等待插件采样一段时间后再试。"
                ).use_t2i(False)
                return

            colors = self._conf("chartsCfg.color", ["#2ec7c9", "#b6a2de"]) or [
                "#2ec7c9",
                "#b6a2de",
            ]
            data = {
                "charts": build_monitor_charts(history, colors),
                "style": self.collector._style_config(),
                "copyright": self.collector._copyright(),
            }
            async with self._render_lock:
                path = await self.renderer.render_monitor(
                    data, umo=getattr(event, "unified_msg_origin", None)
                )
            yield event.chain_result([Image.fromFileSystem(path)])
        except Exception as exc:
            logger.exception("渲染系统状态监控失败: %s", exc)
            yield event.plain_result(
                "系统状态监控渲染失败，请检查 AstrBot T2I 服务是否正常。"
            ).use_t2i(False)

    @staticmethod
    def _fallback_text(data: dict[str, Any]) -> str:
        lines = ["AstrBot 系统状态"]
        for resource in data.get("visualData") or []:
            lines.append(f"{resource.get('title')}: {resource.get('inner')}")
        disks = ((data.get("disks") or {}).get("disksSize") or [])
        for disk in disks[:2]:
            lines.append(f"磁盘 {disk.get('mount')}: {disk.get('use')}%")
        network = (data.get("network") or {}).get("speed") or {}
        speed = network.get("speed") or {}
        upload = speed.get("upload") or {}
        download = speed.get("download") or {}
        if upload or download:
            lines.append(
                "网络 ↑ {}{}/s ↓ {}{}/s".format(
                    upload.get("size", "--"),
                    upload.get("suffix", ""),
                    download.get("size", "--"),
                    download.get("suffix", ""),
                )
            )
        other = data.get("otherInfo") or {}
        hostname = (other.get("hostname") or {}).get("main")
        uptime = (other.get("sysTime") or {}).get("main")
        if hostname:
            lines.append(f"主机: {hostname}")
        if uptime:
            lines.append(f"运行: {uptime}")
        return "\n".join(lines)
