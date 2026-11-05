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
    group: str, start: date, end: date | None, deadlines: list[Deadline]
) -> str:
    parts = [
        "# 📝 Дедлайны на неделю" if end is not None else "# 📝 Все предстоящие дедлайны",
        f"## {start:%d.%m.%Y} — {end:%d.%m.%Y}" if end is not None else f"## С {start:%d.%m.%Y}",
        f"🎓 **Группа {markdown_text(group)}**",
    ]
    if not deadlines:
        parts.append(
            "> ✅ Дедлайнов на эту неделю пока нет."
            if end is not None else "> ✅ Предстоящих дедлайнов пока нет."
        )
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
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    await send_deadlines(ms, session, start, end)


@router.message(Command("all_deadlines"), StateFilter(None))
async def command_all_deadlines(ms: Message, session: AsyncSession):
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    await send_deadlines(ms, session, today)


async def send_deadlines(
    ms: Message, session: AsyncSession, start: date, end: date | None = None
):
    if ms.from_user is None:
        return
    try:
        group = await session.scalar(
            select(Group)
            .join(User, User.group_id == Group.id)
            .where(User.telegram_id == ms.from_user.id)
        )
        if group is None:
            await ms.answer("Сначала укажи свою учебную группу.")
            return
        query = (
            select(Deadline)
            .where(
                Deadline.group_id == group.id,
                Deadline.date >= start,
            )
            .order_by(Deadline.date, Deadline.subject, Deadline.id)
        )
        if end is not None:
            query = query.where(Deadline.date <= end)
        deadlines = list(await session.scalars(query))
    except Exception:
        logger.exception("Failed to load deadlines")
        await ms.answer("Не удалось загрузить дедлайны. Попробуй позже.")
        return
    await ms.answer_rich(InputRichMessage(
        markdown=render_deadlines(group.name, start, end, deadlines)
    ))
