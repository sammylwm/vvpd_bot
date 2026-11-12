import logging
import re
from datetime import date, datetime
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
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.identity import account_id
from core.modules.group import Group
from core.modules.session_task import SessionCompletion, SessionTask
from core.modules.user import User

router = Router()
logger = logging.getLogger(__name__)


class AddSessionTask(StatesGroup):
    subject = State()
    text = State()
    due_date = State()


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


def render_session(group: str, key: str, tasks: list[SessionTask], completed: set[int], owner: int) -> InputRichMessage:
    done = sum(task.id in completed for task in tasks)
    blocks = [
        InputRichBlockSectionHeading(text="📚 Подготовка к сессии", size=1),
        InputRichBlockParagraph(text=f"{semester_title(key)} · Группа {group}"),
        InputRichBlockParagraph(text=RichTextBold(text=f"Выполнено: {done} из {len(tasks)}")),
        InputRichBlockParagraph(text="Требования общие для группы. Отметки выполнения только твои. Добавить: /session_add"),
    ]
    subjects = {}
    for task in tasks:
        subjects.setdefault(task.subject, []).append(task)
    for subject, items in subjects.items():
        contents = []
        for index, task in enumerate(items):
            if index:
                contents.append(InputRichBlockDivider())
            is_done = task.id in completed
            contents.append(InputRichBlockParagraph(text=RichTextBold(text=f"{'✅' if is_done else '⬜'} {task.text}")))
            contents.append(InputRichBlockParagraph(text=f"Срок: {task.due_date:%d.%m.%Y}" if task.due_date else "Без срока"))
            contents.append(InputRichBlockButtons(buttons=[RichMessageButton(
                text="Вернуть в работу" if is_done else "Отметить выполненным",
                callback_data=f"session_set:{owner}:{key}:{task.id}:{0 if is_done else 1}",
                style=None if is_done else "success",
            )]))
        count = sum(task.id in completed for task in items)
        blocks.append(InputRichBlockDetails(summary=f"{subject} · {count}/{len(items)}", blocks=contents, is_open=True))
    if not tasks:
        blocks.append(InputRichBlockParagraph(text="Требований пока нет. Добавь первое через /session_add."))
    return InputRichMessage(blocks=blocks)


async def session_view(session: AsyncSession, group: Group, key: str, owner: int):
    tasks = list(await session.scalars(
        select(SessionTask).where(SessionTask.group_id == group.id, SessionTask.semester == key)
        .order_by(SessionTask.subject, SessionTask.due_date.asc().nulls_last(), SessionTask.id)
    ))
    completed = set(await session.scalars(
        select(SessionCompletion.task_id).join(SessionTask, SessionTask.id == SessionCompletion.task_id)
        .where(SessionTask.group_id == group.id, SessionTask.semester == key, SessionCompletion.telegram_id == owner)
    ))
    return render_session(group.name, key, tasks, completed, owner)


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
    await state.set_state(AddSessionTask.subject)
    await ms.answer("По какому предмету нужно подготовиться к сессии? Отправь название. Отмена: /cancel")


@router.message(StateFilter(AddSessionTask), Command("cancel"))
async def cancel_session_add(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Добавление требования к сессии отменено.")


@router.message(AddSessionTask.subject, F.text, ~F.text.startswith("/"))
async def receive_session_subject(ms: Message, state: FSMContext):
    subject = " ".join(ms.text.split())
    if not 1 <= len(subject) <= 200:
        await ms.answer("Название предмета должно содержать от 1 до 200 символов.")
        return
    await state.update_data(subject=subject)
    await state.set_state(AddSessionTask.text)
    await ms.answer("Что нужно сделать? Отправь один пункт, например: защитить лабораторные 1-4.")


@router.message(AddSessionTask.text, F.text, ~F.text.startswith("/"))
async def receive_session_text(ms: Message, state: FSMContext):
    text = ms.text.strip()
    if not 1 <= len(text) <= 2000:
        await ms.answer("Требование должно содержать от 1 до 2000 символов.")
        return
    token = uuid4().hex
    await state.update_data(text=text, token=token)
    await state.set_state(AddSessionTask.due_date)
    await ms.answer("Укажи срок в формате ДД.ММ.ГГГГ или нажми «Без срока».", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Без срока", callback_data=f"session_skip:{token}")]]
    ))


async def save_session_task(ms: Message, user_id: int, state: FSMContext, session: AsyncSession, due: date | None):
    data = await state.get_data()
    group = await user_group(session, ms, user_id)
    if group is None or group.id != data.get("group_id"):
        await state.clear()
        await ms.answer("Группа изменилась. Начни заново: /session_add.")
        return
    try:
        session.add(SessionTask(group_id=group.id, semester=data["semester"], subject=data["subject"], text=data["text"], due_date=due))
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Failed to save session task")
        await ms.answer("Не удалось сохранить требование. Повтори срок или используй /cancel.")
        return
    await state.clear()
    await ms.answer("Требование добавлено для всей группы. Твой список и прогресс: /session")


@router.message(AddSessionTask.due_date, F.text, ~F.text.startswith("/"))
async def receive_session_date(ms: Message, state: FSMContext, session: AsyncSession):
    try:
        text = ms.text.strip()
        if re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", text) is None:
            raise ValueError("Invalid date")
        due = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await ms.answer("Укажи существующую дату в формате ДД.ММ.ГГГГ или нажми «Без срока».")
        return
    if ms.from_user:
        await save_session_task(ms, ms.from_user.id, state, session, due)


@router.callback_query(F.data.startswith("session_skip:"))
async def skip_session_date(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    if await state.get_state() != AddSessionTask.due_date.state or callback.data != f"session_skip:{data.get('token')}":
        await callback.answer("Эта кнопка уже неактуальна.")
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await save_session_task(callback.message, callback.from_user.id, state, session, None)


@router.message(StateFilter(AddSessionTask))
async def session_text_required(ms: Message):
    await ms.answer("Ответь текстом или используй /cancel для отмены.")


@router.callback_query(F.data.startswith("session_set:"))
async def set_session_completion(callback: CallbackQuery, session: AsyncSession):
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    try:
        _, owner, key, task_id, done = callback.data.split(":")
        owner, task_id = int(owner), int(task_id)
        if done not in {"0", "1"} or re.fullmatch(r"[0-9]{4}-[12]", key) is None:
            raise ValueError("Invalid callback")
    except ValueError:
        await callback.answer("Некорректная кнопка.")
        return
    if owner != callback.from_user.id:
        await callback.answer("Это чужой прогресс. Открой свой список через /session.", show_alert=True)
        return
    group = await user_group(session, callback.message, owner)
    if group is None:
        await callback.answer("Сначала укажи свою учебную группу.", show_alert=True)
        return
    task = await session.scalar(select(SessionTask).where(
        SessionTask.id == task_id, SessionTask.group_id == group.id, SessionTask.semester == key,
    ))
    if task is None:
        await callback.answer("Требование недоступно. Открой список заново: /session.")
        return
    try:
        if done == "1":
            await session.execute(insert(SessionCompletion).values(task_id=task_id, telegram_id=owner)
                                  .on_conflict_do_nothing(constraint="uq_session_completion"))
        else:
            await session.execute(delete(SessionCompletion).where(
                SessionCompletion.task_id == task_id, SessionCompletion.telegram_id == owner,
            ))
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Failed to update session progress")
        await callback.answer("Не удалось сохранить отметку. Попробуй позже.", show_alert=True)
        return
    await callback.answer("Выполнено" if done == "1" else "Возвращено в работу")
    await callback.message.edit_text(rich_message=await session_view(session, group, key, owner))
