import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.types import InputRichMessage, Message

from bot.attachments import send_attachment_links
from bot.identity import account_id
from bot.preferences import group_for_account
from core.api import ApiError, Deadline, PolytechApi

from .day import markdown_text

router = Router()
logger = logging.getLogger(__name__)


def render_deadlines(
    group: str, start: date, end: date | None, deadlines: list[Deadline]
) -> str:
    parts = [
        "# 📝 Дедлайны на неделю"
        if end is not None
        else "# 📝 Все предстоящие дедлайны",
        f"## {start:%d.%m.%Y} - {end:%d.%m.%Y}"
        if end is not None
        else f"## С {start:%d.%m.%Y}",
        f"🎓 **Группа {markdown_text(group)}**",
    ]
    if not deadlines:
        parts.append(
            "> ✅ Дедлайнов на эту неделю пока нет."
            if end is not None
            else "> ✅ Предстоящих дедлайнов пока нет."
        )
    current_day = None
    for task in deadlines:
        if task.date != current_day:
            current_day = task.date
            parts.append(f"### 🗓 {current_day:%d.%m.%Y}")
        parts.append(
            f"- **{markdown_text(task.subject)}** - {markdown_text(task.deadline)}"
        )
    return "\n\n".join(parts)


@router.message(Command("deadlines"), StateFilter(None))
async def command_deadlines(ms: Message, api: PolytechApi):
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    await send_deadlines(ms, api, start, end)


@router.message(Command("all_deadlines"), StateFilter(None))
async def command_all_deadlines(ms: Message, api: PolytechApi):
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    await send_deadlines(ms, api, today)


async def send_deadlines(
    ms: Message, api: PolytechApi, start: date, end: date | None = None
):
    if ms.from_user is None:
        return
    try:
        group = await group_for_account(account_id(ms, ms.from_user.id))
        if group is None:
            await ms.answer("Сначала укажи свою учебную группу.")
            return
        deadlines = await api.deadlines(group, start, end or date.max)
    except ApiError:
        logger.exception("Failed to load deadlines")
        await ms.answer("Не удалось загрузить дедлайны. Попробуй позже.")
        return
    await ms.answer_rich(
        InputRichMessage(markdown=render_deadlines(group, start, end, deadlines))
    )
    await send_attachment_links(ms, deadlines)
