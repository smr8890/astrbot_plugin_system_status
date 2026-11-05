from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import psutil

from .utils import (
    ASTRBOT_VERSION,
    cfg,
    format_duration,
    get_astrbot_temp_path,
)

STATUS_ICON_MAP = {
    "running": "11",
    "error": "50",
    "stopped": "60",
    "pending": "70",
}


class PlatformCollector:
    def __init__(
        self,
        *,
        context: Any,
        config: Any,
        plugin_dir: Path,
        identity_cache: dict[str, Any] | None = None,
    ) -> None:
        self.context = context
        self.config = config
        self.plugin_dir = plugin_dir
        now = time.time()
        self._identity_cache: dict[str, tuple[float, float, dict[str, Any]]] = {}
        for platform_id, value in (identity_cache or {}).items():
            if not isinstance(value, dict):
                continue
            identity = dict(value)
            avatar = str(identity.get("avatar") or "")
            avatar_usable = (
                avatar.startswith(("http://", "https://", "data:"))
                or Path(avatar).exists()
            )
            # Entries without a usable avatar should be retried quickly,
            # otherwise a stale name-only cache hides a newly fetched avatar.
            ttl = 300.0 if avatar and avatar_usable else 5.0
            self._identity_cache[str(platform_id)] = (now, ttl, identity)
        self._process_uptime = self._get_process_uptime()

    def _get_process_uptime(self) -> float:
        try:
            return max(0.0, time.time() - psutil.Process().create_time())
        except Exception:
            return 0.0

    def export_identity_cache(self) -> dict[str, dict[str, Any]]:
        exported: dict[str, dict[str, Any]] = {}
        for platform_id, entry in self._identity_cache.items():
            if entry[1] < 300 or not entry[2].get("name"):
                continue
            identity = dict(entry[2])
            avatar = str(identity.get("avatar") or "")
            if (
                avatar
                and not avatar.startswith(("http://", "https://", "data:"))
                and not Path(avatar).exists()
            ):
                identity["avatar"] = ""
            exported[platform_id] = identity
        return exported

    async def collect(
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
        current_platform_type = ""
        try:
            current_platform_id = event.get_platform_id()
            current_platform_type = event.get_platform_name()
        except Exception:
            pass
        is_webchat_event = (
            current_platform_id == "webchat" or current_platform_type == "webchat"
        )
        show_all_adapters = bool(cfg(self.config, "showAllAdapters", True))

        bots: list[dict[str, Any]] = []
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            platform_id = str(getattr(meta, "id", "") or "")
            platform_type = str(getattr(meta, "name", "") or "")
            if not show_all_adapters:
                matches_current_id = bool(
                    current_platform_id and platform_id == current_platform_id
                )
                matches_current_type = bool(
                    not current_platform_id
                    and current_platform_type
                    and platform_type == current_platform_type
                )
                if not (matches_current_id or matches_current_type):
                    continue
            identity = await self._platform_identity(platform, meta)
            if platform_type == "webchat" or platform_id == "webchat":
                if not is_webchat_event:
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
            backend = str(identity.get("backend") or "").strip()

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
            total_sessions = int(conversation.get("total") or session.get("total") or 0)

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
                    or str(
                        self.plugin_dir
                        / "resources"
                        / "state"
                        / "img"
                        / "default_avatar.jpg"
                    ),
                    "nickname": identity.get("name")
                    or f"{display_name} {platform_id}".strip(),
                    "status": STATUS_ICON_MAP.get(status, "11"),
                    "botVersion": f"AstrBot v{ASTRBOT_VERSION}",
                    "platform": platform_label,
                    "backend": backend,
                    "botRunTime": format_duration(runtime),
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

        bots.sort(
            key=lambda item: (not item.get("is_current"), item.get("nickname", ""))
        )
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
            "botRunTime": format_duration(self._process_uptime),
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
        platform_type = str(getattr(meta, "name", "") or "")
        cached = self._identity_cache.get(platform_id)
        if cached and time.time() - cached[0] < cached[1]:
            cached_identity = dict(cached[2])
            if platform_type != "aiocqhttp" or "backend" in cached_identity:
                return cached_identity

        name = str(cfg(self.config, "bot.name", "") or "").strip()
        avatar = str(cfg(self.config, "bot.avatar", "") or "").strip()
        identity = {"name": name, "avatar": avatar, "backend": ""}
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
                    identity["avatar"] = f"https://q1.qlogo.cn/g?b=qq&s=0&nk={uid}"
                try:
                    version_info = await asyncio.wait_for(
                        client.call_action("get_version_info"), timeout=3
                    )
                except Exception:
                    version_info = {}
                app_name = str((version_info or {}).get("app_name") or "").strip()
                app_version = str((version_info or {}).get("app_version") or "").strip()
                if app_name:
                    identity["backend"] = (
                        f"{app_name} v{app_version}" if app_version else app_name
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
                    getattr(me, "first_name", "") or getattr(me, "username", "") or ""
                )
                # The Bot API does not include the bot photo in getMe, but
                # getUserProfilePhotos can read the bot's own profile picture.
                if not identity["avatar"]:
                    get_profile_photos = getattr(
                        client, "get_user_profile_photos", None
                    )
                    if get_profile_photos is not None:
                        photos = await asyncio.wait_for(
                            get_profile_photos(user_id=me.id, limit=1), timeout=3
                        )
                        photo_sets = getattr(photos, "photos", None) or []
                        if photo_sets and photo_sets[0]:
                            file = await asyncio.wait_for(
                                client.get_file(photo_sets[0][-1].file_id),
                                timeout=3,
                            )
                            suffix = (
                                Path(getattr(file, "file_path", "") or "").suffix
                                or ".jpg"
                            )
                            safe_platform_id = re.sub(
                                r"[^A-Za-z0-9_.-]", "_", platform_id or "telegram"
                            )
                            avatar_path = (
                                Path(get_astrbot_temp_path())
                                / f"telegram_{safe_platform_id}_avatar{suffix}"
                            )
                            avatar_path.parent.mkdir(parents=True, exist_ok=True)
                            await asyncio.wait_for(
                                file.download_to_drive(custom_path=avatar_path),
                                timeout=10,
                            )
                            identity["avatar"] = str(avatar_path)
                resolved = bool(identity["name"])
            elif platform_type == "discord":
                client = getattr(platform, "client", None)
                user = getattr(client, "user", None)
                if user is not None:
                    identity["name"] = identity["name"] or str(
                        getattr(user, "name", "")
                        or getattr(user, "display_name", "")
                        or ""
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
