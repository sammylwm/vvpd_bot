import asyncio
import logging
import re
from datetime import date, datetime
from html import escape
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputRichBlockButtons, InputRichBlockDetails, InputRichBlockDivider,
    InputRichBlockParagraph, InputRichBlockSectionHeading, InputRichMessage,
    Message, RichMessageButton, RichTextBold,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.ai import SubjectClarificationRequired, reconcile_homework
from ai.session_tasks import ParsedSessionTasks, parse_session_tasks
from bot.handlers.add import normalize_deadline_text
from bot.handlers.day import subject_key
from bot.handlers.semester import get_semester_schedule
from bot.identity import account_id
from core.modules.group import Group
from core.modules.session_task import SessionTask
from core.modules.user import User

router = Router()
logger = logging.getLogger(__name__)


class AddSessionTask(StatesGroup):
    subject = State()
    text = State()
    confirmation = State()


def current_semester(day: date | None = None) -> str:
    day = day or datetime.now(ZoneInfo("Europe/Moscow")).date()
    if day.month == 1:
        return f"{day.year - 1}-2"
    return f"{day.year}-{'1' if day.month < 9 else '2'}"


def semester_title(key: str) -> str:
    year, half = key.split("-")
    return f"Весна {year}" if half == "1" else f"Осень {year} / зима {int(year) + 1}"


async def user_group(session: AsyncSession, ms: Message, user_id: int):
    return await session.scalar(
        select(Group).join(User, User.group_id == Group.id)
        .where(User.telegram_id == account_id(ms, user_id))
    )


def render_session(group: str, key: str, tasks: list[SessionTask], owner: int) -> InputRichMessage:
    blocks = [
        InputRichBlockSectionHeading(text="📚 Подготовка к сессии", size=1),
        InputRichBlockParagraph(text=f"{semester_title(key)} · Группа {group}"),
        InputRichBlockParagraph(text="Задания до сессии для всей группы. Добавить: /session_add"),
    ]
    subjects = {}
    for task in tasks:
        subjects.setdefault(task.subject, []).append(task)
    for subject, items in subjects.items():
        contents = []
        for index, task in enumerate(items):
            if index:
                contents.append(InputRichBlockDivider())
            contents.append(InputRichBlockParagraph(text=RichTextBold(text=task.text)))
            contents.append(InputRichBlockParagraph(text=f"Срок: {task.due_date:%d.%m.%Y}" if task.due_date else "Без срока"))
            contents.append(InputRichBlockButtons(buttons=[RichMessageButton(
                text="Удалить", style="danger",
                callback_data=f"session_delete:ask:{owner}:{key}:{task.id}",
            )]))
        blocks.append(InputRichBlockDetails(summary=f"{subject} · заданий: {len(items)}", blocks=contents, is_open=True))
    if not tasks:
        blocks.append(InputRichBlockParagraph(text="Требований пока нет. Добавь первое через /session_add."))
    return InputRichMessage(blocks=blocks)


async def session_view(session: AsyncSession, group: Group, key: str, owner: int):
    tasks = list(await session.scalars(
        select(SessionTask).where(SessionTask.group_id == group.id, SessionTask.semester == key)
        .order_by(SessionTask.subject, SessionTask.due_date.asc().nulls_last(), SessionTask.id)
    ))
    return render_session(group.name, key, tasks, owner)


@router.message(Command("session"), StateFilter(None))
async def command_session(ms: Message, command: CommandObject, session: AsyncSession):
    if ms.from_user is None:
        return
    key = (command.args or current_semester()).strip()
    if re.fullmatch(r"[0-9]{4}-[12]", key) is None:
        await ms.answer("Используй /session или /session 2026-2. 1 - весна, 2 - осень и следующая зима.")
        return
    group = await user_group(session, ms, ms.from_user.id)
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    await ms.answer_rich(await session_view(session, group, key, ms.from_user.id))


@router.message(Command("session_add"), StateFilter(None))
async def command_session_add(ms: Message, state: FSMContext, session: AsyncSession):
    if ms.from_user is None:
        return
    group = await user_group(session, ms, ms.from_user.id)
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    await state.clear()
    await state.update_data(group_id=group.id, semester=current_semester())
    await state.set_state(AddSessionTask.text)
    await ms.answer("Отправь предмет и всё, что нужно сделать до сессии, одним сообщением. Например: по тимп сдать лабораторные 1-4 и защитить курсовую до 20 декабря. ИИ разделит задачи. Отмена: /cancel")


@router.message(StateFilter(AddSessionTask), Command("cancel"))
async def cancel_session_add(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Добавление требований к сессии отменено.")


async def prepare_session_tasks(ms: Message, state: FSMContext, session: AsyncSession, text: str):
    if ms.from_user is None:
        return
    data = await state.get_data()
    group = await user_group(session, ms, ms.from_user.id)
    if group is None or group.id != data.get("group_id"):
        await state.clear()
        await ms.answer("Группа изменилась. Начни заново: /session_add.")
        return
    await state.update_data(raw_text=text)
    try:
        schedule = await asyncio.to_thread(get_semester_schedule, group.name)
        existing = list(await session.scalars(select(SessionTask.subject).where(
            SessionTask.group_id == group.id, SessionTask.semester == data["semester"],
        ).distinct()))
        names = {}
        for name in existing + [lesson["subject"] for lesson in schedule["lessons"]]:
            names.setdefault(subject_key(name), name)
        parsed = await asyncio.to_thread(parse_session_tasks, text, list(names.values()), datetime.now(ZoneInfo("Europe/Moscow")).date())
    except SubjectClarificationRequired:
        await state.set_state(AddSessionTask.subject)
        await ms.answer("Уточни один предмет полным названием. Если предметов несколько, добавь их отдельно.")
        return
    except Exception:
        logger.exception("Failed to parse session tasks")
        await ms.answer("Не удалось обработать задания через ИИ. Ничего не добавлено. Повтори сообщение или используй /cancel.")
        return
    token = uuid4().hex
    await state.update_data(parsed=parsed.model_dump(), token=token)
    await state.set_state(AddSessionTask.confirmation)
    lines = [f"<b>{escape(parsed.subject)}</b>"]
    for index, task in enumerate(parsed.tasks, 1):
        due = date.fromisoformat(task.due_date).strftime("%d.%m.%Y") if task.due_date else "без срока"
        lines.append(f"{index}. {escape(task.text)} ({due})")
    lines.append("Дополнить список этого предмета для всей группы?")
    preview = "\n\n".join(lines)
    if len(preview) > 3800:
        await state.set_state(AddSessionTask.text)
        await ms.answer("Список слишком большой для подтверждения. Отправь его несколькими сообщениями через /session_add.")
        return
    await ms.answer(preview, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Добавить", callback_data=f"session_save:{token}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"session_cancel:{token}"),
    ]]))


@router.message(AddSessionTask.text, F.text, ~F.text.startswith("/"))
async def receive_session_text(ms: Message, state: FSMContext, session: AsyncSession):
    text = ms.text.strip()
    if not 1 <= len(text) <= 4000:
        await ms.answer("Отправь от 1 до 4000 символов с предметом и заданиями.")
        return
    await prepare_session_tasks(ms, state, session, text)


@router.message(AddSessionTask.subject, F.text, ~F.text.startswith("/"))
async def receive_session_subject(ms: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    await prepare_session_tasks(ms, state, session, f"{data['raw_text']}\nУточнение предмета: {ms.text}")


async def append_session_tasks(session: AsyncSession, group_id: int, key: str, parsed: ParsedSessionTasks) -> tuple[int, int]:
    try:
        group = await session.scalar(select(Group).where(Group.id == group_id).with_for_update())
        if group is None:
            raise ValueError("Group no longer exists")
        rows = list(await session.scalars(select(SessionTask).where(
            SessionTask.group_id == group_id, SessionTask.semester == key,
        ).order_by(SessionTask.id)))
        candidates = [task for task in rows if subject_key(task.subject) == subject_key(parsed.subject)]
        canonical = candidates[0].subject if candidates else parsed.subject
        for task in candidates:
            task.subject = canonical
        created = updated = 0
        for incoming in parsed.tasks:
            matched = next((task for task in candidates if normalize_deadline_text(task.text) == normalize_deadline_text(incoming.text)), None)
            merged = incoming.text
            if matched is None and candidates:
                comparison = await asyncio.to_thread(reconcile_homework, incoming.text, [task.text for task in candidates])
                if comparison.match_index is not None:
                    matched = candidates[comparison.match_index]
                    merged = comparison.merged_homework
            due = date.fromisoformat(incoming.due_date) if incoming.due_date else None
            if matched is not None:
                changed = False
                if normalize_deadline_text(matched.text) != normalize_deadline_text(merged):
                    matched.text = merged
                    changed = True
                if due is not None and matched.due_date != due:
                    matched.due_date = due
                    changed = True
                updated += int(changed)
            else:
                task = SessionTask(group_id=group_id, semester=key, subject=canonical, text=incoming.text, due_date=due)
                session.add(task)
                candidates.append(task)
                created += 1
        await session.commit()
        return created, updated
    except Exception:
        await session.rollback()
        raise


@router.callback_query(F.data.startswith("session_save:") | F.data.startswith("session_cancel:"))
async def confirm_session_tasks(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    if await state.get_state() != AddSessionTask.confirmation.state or (callback.data or "").split(":", 1)[1] != data.get("token"):
        await callback.answer("Это подтверждение уже неактуально.")
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    if callback.data.startswith("session_cancel:"):
        await state.clear()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Добавление отменено.")
        return
    group = await user_group(session, callback.message, callback.from_user.id)
    if group is None or group.id != data.get("group_id"):
        await state.clear()
        await callback.message.answer("Группа изменилась. Начни заново: /session_add.")
        return
    try:
        created, updated = await append_session_tasks(session, group.id, data["semester"], ParsedSessionTasks.model_validate(data["parsed"]))
    except Exception:
        logger.exception("Failed to append session tasks")
        await callback.message.answer("Не удалось дополнить список. Ничего не сохранено. Повтори подтверждение или используй /cancel.")
        return
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Добавлено задач: {created}. Дополнено: {updated}. Список по предметам: /session")


@router.message(StateFilter(AddSessionTask))
async def session_text_required(ms: Message):
    await ms.answer("Отправь текст или подтверди добавление кнопкой. Отмена: /cancel.")


@router.callback_query(F.data.startswith("session_set:"))
async def obsolete_session_completion(callback: CallbackQuery):
    await callback.answer("Отметки выполнения убраны. Открой список заново: /session.", show_alert=True)


@router.callback_query(F.data.startswith("session_delete:"))
async def delete_session_task(callback: CallbackQuery, session: AsyncSession):
    ms = callback.message
    if not isinstance(ms, Message):
        await callback.answer()
        return
    try:
        _, action, owner, key, task_id = (callback.data or "").split(":")
        owner, task_id = int(owner), int(task_id)
        if action not in {"ask", "yes", "no"} or re.fullmatch(r"[0-9]{4}-[12]", key) is None:
            raise ValueError("Invalid callback")
    except ValueError:
        await callback.answer("Некорректная кнопка.")
        return
    if owner != callback.from_user.id:
        await callback.answer("Открой свой список через /session.", show_alert=True)
        return
    if action == "no":
        await callback.answer("Удаление отменено.")
        await ms.edit_text("Удаление отменено. Список заданий: /session")
        return
    group = await user_group(session, ms, owner)
    if group is None:
        await callback.answer("Сначала укажи свою учебную группу.", show_alert=True)
        return
    condition = (SessionTask.id == task_id, SessionTask.group_id == group.id, SessionTask.semester == key)
    try:
        if action == "ask":
            task = await session.scalar(select(SessionTask).where(*condition))
            if task is None:
                await callback.answer("Задание уже удалено или недоступно.")
                return
            await callback.answer()
            prefix = f"session_delete:{{}}:{owner}:{key}:{task_id}"
            await ms.answer(
                f"Удалить задание к сессии для всей группы?\n\n"
                f"<b>{escape(task.subject)}</b>\n{escape(task.text)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Да, удалить", callback_data=prefix.format("yes")),
                    InlineKeyboardButton(text="Отмена", callback_data=prefix.format("no")),
                ]]),
            )
            return
        result = await session.execute(delete(SessionTask).where(*condition).returning(SessionTask.id))
        deleted = result.scalar_one_or_none()
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Failed to delete session task")
        await callback.answer("Не удалось удалить задание. Попробуй позже.", show_alert=True)
        return
    text = "Задание удалено для всей группы." if deleted is not None else "Задание уже удалено или недоступно."
    await callback.answer(text)
    await ms.edit_text(text)
    await ms.answer_rich(await session_view(session, group, key, owner))
