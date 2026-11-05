from __future__ import annotations

from typing import Any


async def query_database(db: Any) -> dict[str, Any]:
    """Collect message and conversation statistics through the AstrBot DB API.

    Args:
        db: AstrBot database instance returned by ``context.get_db()``.

    Returns:
        Aggregated message and conversation counts keyed by platform ID.
    """
    result: dict[str, Any] = {
        "messages": {},
        "total_messages": 0,
        "conversations": {},
        "total_conversations": 0,
        "sessions": {},
    }
    if db is None:
        return result

    try:
        from sqlalchemy import case
        from sqlmodel import func, select

        from astrbot.core.db.po import ConversationV2, PlatformSession, PlatformStat
    except Exception:
        return result

    try:
        async with db.get_db() as session:
            rows = await session.execute(
                select(
                    PlatformStat.platform_id,
                    func.coalesce(func.sum(PlatformStat.count), 0),
                ).group_by(PlatformStat.platform_id)
            )
            for platform_id, count in rows.all():
                result["messages"][str(platform_id)] = int(count or 0)
            result["total_messages"] = sum(result["messages"].values())

            rows = await session.execute(
                select(
                    ConversationV2.platform_id,
                    func.count(ConversationV2.inner_conversation_id),
                    func.coalesce(
                        func.sum(
                            case(
                                (ConversationV2.user_id.like("%:GroupMessage:%"), 1),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                ).group_by(ConversationV2.platform_id)
            )
            conversations: dict[str, dict[str, int]] = {}
            for platform_id, total, group_count in rows.all():
                key = str(platform_id or "")
                total = int(total or 0)
                group = int(group_count or 0)
                conversations[key] = {
                    "total": total,
                    "group": group,
                    "private": total - group,
                }
            result["conversations"] = conversations
            result["total_conversations"] = sum(
                item["total"] for item in conversations.values()
            )

            rows = await session.execute(
                select(
                    PlatformSession.platform_id,
                    func.count(PlatformSession.inner_id),
                    func.coalesce(
                        func.sum(case((PlatformSession.is_group == 1, 1), else_=0)),
                        0,
                    ),
                ).group_by(PlatformSession.platform_id)
            )
            sessions: dict[str, dict[str, int]] = {}
            for platform_id, total, group_count in rows.all():
                key = str(platform_id or "")
                total = int(total or 0)
                group = int(group_count or 0)
                sessions[key] = {
                    "total": total,
                    "group": group,
                    "private": total - group,
                }
            result["sessions"] = sessions
    except Exception:
        pass
    return result
