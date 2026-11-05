import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.types import InputRichMessage, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User

from .day import markdown_text

router = Router()
logger = logging.getLogger(__name__)


def render_deadlines(
    group: str, start: date, end: date, deadlines: list[Deadline]
) -> str:
    parts = [
        "# 📝 Дедлайны на неделю",
        f"## {start:%d.%m.%Y} — {end:%d.%m.%Y}",
        f"🎓 **Группа {markdown_text(group)}**",
    ]
    if not deadlines:
        parts.append("> ✅ Дедлайнов на эту неделю пока нет.")
    current_day = None
    for task in deadlines:
        if task.date != current_day:
            current_day = task.date
            parts.append(f"### 🗓 {current_day:%d.%m.%Y}")
        parts.append(
            f"- **{markdown_text(task.subject)}** — {markdown_text(task.deadline)}"
        )
    return "\n\n".join(parts)


@router.message(Command("deadlines"), StateFilter(None))
async def command_deadlines(ms: Message, session: AsyncSession):
    if ms.from_user is None:
        return
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    try:
        group = await session.scalar(
            select(Group)
            .join(User, User.group_id == Group.id)
            .where(User.telegram_id == ms.from_user.id)
        )
        if group is None:
            await ms.answer("Сначала укажи свою учебную группу.")
            return
        deadlines = list(await session.scalars(
            select(Deadline)
            .where(
                Deadline.group_id == group.id,
                Deadline.date >= start,
                Deadline.date <= end,
            )
            .order_by(Deadline.date, Deadline.subject, Deadline.id)
        ))
    except Exception:
        logger.exception("Failed to load weekly deadlines")
        await ms.answer("Не удалось загрузить дедлайны. Попробуй позже.")
        return
    await ms.answer_rich(InputRichMessage(
        markdown=render_deadlines(group.name, start, end, deadlines)
    ))
