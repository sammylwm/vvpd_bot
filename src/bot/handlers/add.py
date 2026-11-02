import asyncio
import logging
import unicodedata
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.ai import SubjectClarificationRequired, get_json_homework
from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User

router = Router()
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


async def save_deadline(
    session: AsyncSession, group: str, homework: dict[str, str]
) -> bool:
    """Save a new deadline; return False for an existing equivalent deadline."""
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
        for deadline in existing:
            if (
                normalize_deadline_text(deadline.subject) == subject
                and normalize_deadline_text(deadline.deadline) == task
            ):
                await session.commit()
                return False
        session.add(
            Deadline(
                group_id=db_group.id,
                date=day,
                subject=homework["subject"].strip(),
                deadline=homework["homework"].strip(),
            )
        )
        await session.commit()
        return True
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
        added = await save_deadline(session, group, homework)
    except Exception:
        logger.exception("Failed to save deadline")
        await ms.answer(
            "Не удалось сохранить дедлайн. Попробуй ещё раз или используй /cancel."
        )
        return

    await state.clear()
    if not added:
        await ms.answer("Такой дедлайн уже есть у твоей группы. Повторно не добавляю.")
        return
    await ms.answer(
        "Дедлайн добавлен.\n"
        f"Предмет: {escape(homework['subject'])}\n"
        f"Дата: {escape(homework['date']) or 'не определена'}\n"
        f"Задание: {escape(homework['homework'])}",
        parse_mode="HTML",
    )


@router.message(AddDeadline.waiting_for_text, F.text, ~F.text.startswith("/"))
async def receive_deadline(ms: Message, state: FSMContext, session: AsyncSession):
    text = (ms.text or "").strip()
    if not text:
        await ms.answer("Отправь непустой текст дедлайна.")
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

    await state.update_data(deadline_text=text, group=group)
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
