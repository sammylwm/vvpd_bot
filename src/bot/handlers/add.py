import asyncio
import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from openai import APIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.ai import SubjectClarificationRequired, get_json_homework, reconcile_homework
from bot.attachments import AlbumMiddleware, message_attachments
from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User

router = Router()
router.message.middleware(AlbumMiddleware())
logger = logging.getLogger(__name__)


class AddDeadline(StatesGroup):
    waiting_for_text = State()
    waiting_for_subject = State()


@router.message(Command("deadline"), StateFilter(None, AddDeadline))
async def command_deadline(ms: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddDeadline.waiting_for_text)
    await ms.answer(
        "Отправь текст дедлайна, например: сдать лабораторную по физике до пятницы."
        "\nМожно прикрепить фото или файлы, в том числе альбомом. Текст укажи в подписи."
        "\nДля отмены: /cancel"
    )


@router.message(StateFilter(AddDeadline), Command("cancel"))
async def cancel_deadline(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Добавление дедлайна отменено.")


def normalize_deadline_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).rstrip(
        ".!? "
    )


@dataclass(frozen=True)
class SaveDeadlineResult:
    status: str  # created, updated, unchanged
    deadline: Deadline


def merge_attachments(
    existing: list[dict[str, str]], incoming: list[dict[str, str]]
) -> list[dict[str, str]]:
    merged = list(existing)
    for attachment in incoming:
        if any(
            attachment["file_id"] == old["file_id"]
            or (
                attachment.get("file_unique_id")
                and attachment.get("file_unique_id") == old.get("file_unique_id")
            )
            for old in merged
        ):
            continue
        merged.append(attachment)
    return merged


async def save_deadline(
    session: AsyncSession,
    group: str,
    homework: dict[str, str],
    attachments: list[dict[str, str]] | None = None,
) -> SaveDeadlineResult:
    """Create or enrich one assignment while holding the group transaction lock."""
    day = datetime.strptime(homework["date"], "%d-%m-%Y").date()
    try:
        # Serialize additions for this group so simultaneous requests cannot duplicate it.
        db_group = await session.scalar(
            select(Group).where(Group.name == group).with_for_update()
        )
        if db_group is None:
            raise ValueError("Group no longer exists")
        existing = await session.scalars(
            select(Deadline).where(
                Deadline.group_id == db_group.id,
                Deadline.date == day,
            )
        )
        subject = normalize_deadline_text(homework["subject"])
        task = normalize_deadline_text(homework["homework"])
        candidates = [
            deadline
            for deadline in existing
            if normalize_deadline_text(deadline.subject) == subject
        ]
        matched = next(
            (
                deadline
                for deadline in candidates
                if normalize_deadline_text(deadline.deadline) == task
            ),
            None,
        )
        merged_text = matched.deadline if matched is not None else ""
        if matched is None and candidates:
            comparison = await asyncio.to_thread(
                reconcile_homework,
                homework["homework"],
                [deadline.deadline for deadline in candidates],
            )
            if comparison.match_index is not None:
                matched = candidates[comparison.match_index]
                merged_text = comparison.merged_homework.strip()
        if matched is not None:
            files = merge_attachments(matched.attachments or [], attachments or [])
            changed = normalize_deadline_text(merged_text) != normalize_deadline_text(
                matched.deadline
            ) or files != (matched.attachments or [])
            if changed:
                matched.deadline = merged_text
                matched.attachments = files
            await session.commit()
            return SaveDeadlineResult("updated" if changed else "unchanged", matched)
        deadline = Deadline(
            group_id=db_group.id,
            date=day,
            subject=homework["subject"].strip(),
            deadline=homework["homework"].strip(),
            attachments=merge_attachments([], attachments or []),
        )
        session.add(deadline)
        await session.commit()
        return SaveDeadlineResult("created", deadline)
    except Exception:
        await session.rollback()
        raise


async def parse_deadline(
    ms: Message, state: FSMContext, text: str, group: str, session: AsyncSession
):
    try:
        homework = await asyncio.to_thread(get_json_homework, text, group)
    except SubjectClarificationRequired as exc:
        await state.set_state(AddDeadline.waiting_for_subject)
        await ms.answer(str(exc))
        return
    except APIError:
        logger.exception("AI service is unavailable")
        await ms.answer(
            "Сервис ИИ сейчас недоступен или не отвечает. Дедлайн не добавлен. "
            "Повтори сообщение позже или используй /cancel для отмены."
        )
        return
    except Exception:
        logger.exception("Failed to parse deadline")
        await ms.answer(
            "Не удалось обработать дедлайн. Попробуй ещё раз или используй /cancel."
        )
        return

    if not homework["date"] or not homework["homework"].strip():
        await state.set_state(AddDeadline.waiting_for_text)
        await ms.answer(
            "Не удалось определить дату или задание. Отправь полный текст дедлайна с датой ещё раз."
        )
        return
    try:
        data = await state.get_data()
        result = await save_deadline(
            session, group, homework, data.get("attachments", [])
        )
    except APIError:
        logger.exception("AI duplicate check is unavailable")
        await ms.answer(
            "Сервис ИИ сейчас недоступен. Не удалось проверить дубликаты, "
            "поэтому дедлайн не добавлен. Повтори сообщение позже или используй /cancel."
        )
        return
    except Exception:
        logger.exception("Failed to save deadline")
        await ms.answer(
            "Не удалось сохранить дедлайн. Попробуй ещё раз или используй /cancel."
        )
        return

    await state.clear()
    if result.status == "unchanged":
        await ms.answer("Такой дедлайн уже есть у твоей группы. Повторно не добавляю.")
        return
    title = "Дедлайн обновлён." if result.status == "updated" else "Дедлайн добавлен."
    deadline = result.deadline
    await ms.answer(
        f"{title}\n"
        f"Предмет: {escape(deadline.subject)}\n"
        f"Дата: {deadline.date:%d-%m-%Y}\n"
        f"Задание: {escape(deadline.deadline)}\n"
        f"Вложений: {len(deadline.attachments or [])}",
        parse_mode="HTML",
    )


@router.message(AddDeadline.waiting_for_text, ~F.text.startswith("/"))
async def receive_deadline(
    ms: Message, state: FSMContext, session: AsyncSession, album: list[Message]
):
    text = "\n".join(message.text or message.caption or "" for message in album).strip()
    attachments = [item for message in album for item in message_attachments(message)]
    if attachments:
        data = await state.get_data()
        attachments = data.get("attachments", []) + attachments
        await state.update_data(attachments=attachments)
    if not text:
        await ms.answer(
            "Отправь текст дедлайна с датой. Прикреплённые файлы сохранены."
            if attachments
            else "Отправь непустой текст дедлайна."
        )
        return
    if ms.from_user is None:
        return
    group = await session.scalar(
        select(Group.name)
        .join(User, User.group_id == Group.id)
        .where(User.telegram_id == ms.from_user.id)
    )
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return

    if not attachments:
        data = await state.get_data()
        attachments = data.get("attachments", [])
    await state.update_data(deadline_text=text, group=group, attachments=attachments)
    await parse_deadline(ms, state, text, group, session)


@router.message(AddDeadline.waiting_for_subject, F.text, ~F.text.startswith("/"))
async def receive_subject(ms: Message, state: FSMContext, session: AsyncSession):
    subject = (ms.text or "").strip()
    if not subject:
        await ms.answer("Напиши название предмета.")
        return
    data = await state.get_data()
    text = f"{data['deadline_text']}\nУточнение предмета: {subject}"
    await parse_deadline(ms, state, text, data["group"], session)


@router.message(StateFilter(AddDeadline))
async def deadline_text_required(ms: Message):
    await ms.answer("Отправь ответ текстом или используй /cancel для отмены.")
