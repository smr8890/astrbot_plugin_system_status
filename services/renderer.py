from __future__ import annotations

import asyncio
import base64
import json
import random
from pathlib import Path
from typing import Any

from .image_utils import (
    download_image_as_data_uri,
    path_to_data_uri,
    resize_image_bytes,
    to_data_uri,
)


class StatusRenderer:
    def __init__(self, *, star: Any, config: Any, plugin_dir: Path, logger: Any) -> None:
        self.star = star
        self.config = config
        self.plugin_dir = plugin_dir
        self.logger = logger

        state_dir = plugin_dir / "resources" / "state"
        self.icon_dir = state_dir / "icon"
        self.bg_dir = state_dir / "img" / "bg"
        self.default_avatar_path = state_dir / "img" / "default_avatar.jpg"
        self.index_template = (state_dir / "index.html").read_text(encoding="utf-8")
        self.monitor_template = (state_dir / "monitor.html").read_text(encoding="utf-8")
        self.common_css = self._read_css(plugin_dir / "resources" / "common" / "common.css")
        self.index_css = self._read_css(state_dir / "css" / "index.css")
        self.style_js = (state_dir / "js" / "style.js").read_text(encoding="utf-8")
        self.icons = self._load_icons()
        self.font_fzb = self._load_font(plugin_dir / "resources" / "common" / "font" / "FZB.woff")
        self.font_nzbz = self._load_font(plugin_dir / "resources" / "common" / "font" / "NZBZ.woff")
        self.font_maple = self._load_font(
            plugin_dir / "resources" / "common" / "font" / "MapleMono-NF-icons.woff2"
        )
        self._backdrop_cache: tuple[float, str] | None = None
        self._avatar_cache: dict[str, tuple[float, str]] = {}
        self._default_avatar_uri = self._load_default_avatar()

    @staticmethod
    def _read_css(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        return text.replace('@charset "UTF-8";', "").replace("@charset 'UTF-8';", "")

    def _load_icons(self) -> dict[str, str]:
        icons: dict[str, str] = {}
        if not self.icon_dir.is_dir():
            return icons
        for path in self.icon_dir.glob("*.png"):
            icons[path.stem] = path_to_data_uri(path)
        return icons

    @staticmethod
    def _load_font(path: Path) -> str:
        try:
            data = path.read_bytes()
            return f"data:font/woff;base64,{base64.b64encode(data).decode()}"
        except Exception:
            return ""

    def _load_default_avatar(self) -> str:
        try:
            raw = self.default_avatar_path.read_bytes()
            resized = resize_image_bytes(
                raw, max_width=180, max_height=180, quality=90, fmt="JPEG"
            )
            return to_data_uri(resized, "image/jpeg")
        except Exception:
            return "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100'><rect width='100' height='100' fill='%23ddd'/></svg>"

    def _cfg(self, path: str, default: Any = None) -> Any:
        current: Any = self.config
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

    def _font_css(self, *, include_nzbz: bool = True) -> str:
        fallback = (
            'local("Noto Sans CJK SC"), local("PingFang SC"), '
            'local("Microsoft YaHei"), local("sans-serif")'
        )
        if self._cfg("embed_font", True) and self.font_fzb:
            fzb = f'url("{self.font_fzb}") format("woff"), {fallback}'
        else:
            fzb = fallback
        if self._cfg("embed_font", True) and include_nzbz and self.font_nzbz:
            nzbz = f'url("{self.font_nzbz}") format("woff"), {fallback}'
        else:
            nzbz = fallback
        if self.font_maple:
            maple = (
                f'url("{self.font_maple}") format("woff2"), local("monospace")'
            )
        else:
            maple = 'local("monospace")'
        css = self.common_css
        css = css.replace("__FONT_FZB_SRC__", fzb)
        css = css.replace("__FONT_NZBZ_SRC__", nzbz)
        css = css.replace("__FONT_MAPLE_SRC__", maple)
        return css

    def _template_config(self) -> dict[str, Any]:
        style = self._cfg("style", {}) or {}
        if hasattr(style, "copy"):
            style = style.copy()
        else:
            style = {}
        return {
            "style": {
                "BotNameColor": str(style.get("BotNameColor", "#000")),
                "progressBarColor": {
                    "high": str(
                        (style.get("progressBarColor") or {}).get("high", "#d73403")
                    ),
                    "medium": str(
                        (style.get("progressBarColor") or {}).get("medium", "#ffa500")
                    ),
                    "low": (style.get("progressBarColor") or {}).get(
                        "low", ["#84A0DF", "#2EC272", "#8070F9"]
                    ),
                },
                "redisInfoValColor": str(
                    style.get("redisInfoValColor", "#485ab6")
                ),
                "startColumn": bool(style.get("startColumn", False)),
                "botInfoColor": {
                    "botVersion": str(
                        (style.get("botInfoColor") or {}).get(
                            "botVersion", "#FBE0F3"
                        )
                    ),
                    "platform": str(
                        (style.get("botInfoColor") or {}).get("platform", "#F0EDF2")
                    ),
                    "contacts": (style.get("botInfoColor") or {}).get(
                        "contacts", ["#F1EDFE", "#FDF1E6", "#E9F4FC"]
                    ),
                },
            },
            "chartsCfg": {
                "show": bool(self._cfg("chartsCfg.show", True)),
                "color": self._cfg("chartsCfg.color", ["#2ec7c9", "#b6a2de"]),
            },
        }

    def _base_context(self, *, include_nzbz: bool = True) -> dict[str, Any]:
        config_json = json.dumps(
            self._template_config(), ensure_ascii=False
        ).replace("</", "<\\/")
        return {
            "common_css": self._font_css(include_nzbz=include_nzbz),
            "index_css": self.index_css,
            "style_js": self.style_js,
            "icons": self.icons,
            "config_json": config_json,
        }

    def render_options(self, *, monitor: bool = False) -> dict[str, Any]:
        width_key = "render.monitor_viewport_width" if monitor else "render.viewport_width"
        default_width = 900 if monitor else 1000
        return {
            "full_page": True,
            "viewport_width": int(self._cfg(width_key, default_width) or default_width),
            "type": "jpeg",
            "quality": int(self._cfg("render.quality", 90) or 90),
            "scale": "device",
            "device_scale_factor_level": str(
                self._cfg("render.device_scale_factor_level", "high") or "high"
            ),
        }

    async def prepare_status_data(self, data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        style = dict(data.get("style") or {})
        style["backdrop"] = await self.get_backdrop()
        data["style"] = style
        bots = []
        for bot in data.get("BotStatusList") or []:
            bot = dict(bot)
            bot["avatar"] = await self.resolve_avatar(bot.get("avatar"))
            bots.append(bot)
        data["BotStatusList"] = bots
        data.update(self._base_context(include_nzbz=False))
        return data

    async def prepare_monitor_data(self, data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        style = dict(data.get("style") or self._template_config()["style"])
        style["backdrop"] = await self.get_backdrop()
        data["style"] = style
        data.update(self._base_context())
        return data

    async def render_status(self, data: dict[str, Any], *, umo: str | None = None) -> str:
        prepared = await self.prepare_status_data(data)
        return await self._render(self.index_template, prepared, monitor=False, umo=umo)

    async def render_monitor(self, data: dict[str, Any], *, umo: str | None = None) -> str:
        prepared = await self.prepare_monitor_data(data)
        return await self._render(self.monitor_template, prepared, monitor=True, umo=umo)

    async def _render(
        self,
        template: str,
        data: dict[str, Any],
        *,
        monitor: bool,
        umo: str | None,
    ) -> str:
        path = await self.star.html_render(
            tmpl=template,
            data=data,
            return_url=False,
            options=self.render_options(monitor=monitor),
            umo=umo,
        )
        path_obj = Path(path)
        if not path_obj.exists() or path_obj.stat().st_size <= 4096:
            raise RuntimeError("t2i 返回的图片无效")
        return str(path_obj)

    async def resolve_avatar(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return self._default_avatar_uri
        if text.startswith("data:"):
            return text
        now = asyncio.get_running_loop().time()
        cached = self._avatar_cache.get(text)
        if cached and now - cached[0] < 600:
            return cached[1]
        try:
            if text.startswith(("http://", "https://")):
                uri = await download_image_as_data_uri(
                    text, timeout=6, max_width=180, quality=90
                )
            else:
                raw = Path(text).read_bytes()
                if Path(text).suffix.lower() == ".svg":
                    uri = to_data_uri(raw, "image/svg+xml")
                else:
                    resized = resize_image_bytes(
                        raw, max_width=180, max_height=180, quality=90, fmt="JPEG"
                    )
                    uri = to_data_uri(resized, "image/jpeg")
        except Exception:
            uri = self._default_avatar_uri
        self._avatar_cache[text] = (now, uri)
        if len(self._avatar_cache) > 64:
            oldest = min(self._avatar_cache, key=lambda key: self._avatar_cache[key][0])
            self._avatar_cache.pop(oldest, None)
        return uri

    async def get_backdrop(self) -> str:
        ttl = float(self._cfg("style.backdropCacheTTL", 300) or 300)
        now = asyncio.get_running_loop().time()
        if self._backdrop_cache and now - self._backdrop_cache[0] < ttl:
            return self._backdrop_cache[1]

        api = str(self._cfg("style.backdrop", "https://t.alcy.cc/mp") or "").strip()
        uri = ""
        if api:
            try:
                remote_uri = await download_image_as_data_uri(
                    api, timeout=float(self._cfg("style.backdropTimeout", 8) or 8),
                    max_width=1400, quality=82,
                )
                uri = f'url("{remote_uri}")' if remote_uri else ""
            except Exception as exc:
                if self.logger:
                    self.logger.warning("状态卡片背景请求失败: %s", exc)

        if not uri:
            uri = self._local_backdrop()

        if not uri:
            uri = "linear-gradient(135deg, #e9e9e9 0%, #c5ccda 100%)"
        self._backdrop_cache = (now, uri)
        return uri

    def _local_backdrop(self) -> str:
        try:
            files = [
                path
                for path in self.bg_dir.iterdir()
                if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            ]
        except Exception:
            files = []
        if not files:
            return ""
        preferred = str(self._cfg("style.backdropDefault", "random") or "random")
        if preferred and preferred != "random":
            for file in files:
                if file.name == preferred:
                    return self._file_backdrop(file)
        return self._file_backdrop(random.choice(files))

    def _file_backdrop(self, path: Path) -> str:
        try:
            raw = path.read_bytes()
            resized = resize_image_bytes(
                raw, max_width=1400, max_height=2400, quality=82, fmt="JPEG"
            )
            return f'url("{to_data_uri(resized, "image/jpeg")}")'
        except Exception:
            return ""

    async def warmup(self) -> None:
        # Trigger one background request in the background so the first command is faster.
        try:
            await self.get_backdrop()
        except Exception:
            pass
