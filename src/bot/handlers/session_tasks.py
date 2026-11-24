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
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichBlockDetails,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichMessage,
    Message,
)

from bot.identity import account_id
from bot.preferences import group_for_account
from core.api import (
    ApiError,
    PolytechApi,
    SessionSubject,
    SessionTaskDraft,
    SessionTaskText,
)

router = Router()
logger = logging.getLogger(__name__)


class AddSessionTask(StatesGroup):
    waiting_for_text = State()
    waiting_for_confirmation = State()


def current_semester(day: date | None = None) -> str:
    day = day or datetime.now(ZoneInfo("Europe/Moscow")).date()
    if day.month == 1:
        return f"{day.year - 1}-2"
    return f"{day.year}-{'1' if day.month < 9 else '2'}"


def task_details(task: SessionTaskDraft) -> str:
    due = f"Срок: {task.due_date:%d.%m.%Y}" if task.due_date else "Без срока"
    return f"{task.text}\n{due}"


def subject_block(subject: SessionSubject) -> InputRichBlockDetails:
    paragraphs = [InputRichBlockParagraph(text=subject.text)]
    dates = [
        f"№{index} - {task.due_date:%d.%m.%Y}"
        for index, task in enumerate(subject.tasks, 1)
        if task.due_date is not None
    ]
    if dates:
        paragraphs.append(InputRichBlockParagraph(text="Сроки: " + ", ".join(dates)))
    return InputRichBlockDetails(
        summary=f"{subject.subject} · заданий: {len(subject.tasks)}",
        blocks=paragraphs,
        is_open=True,
    )


def render_session(
    group: str, semester: str, subjects: list[SessionSubject]
) -> InputRichMessage:
    year, half = semester.split("-")
    term = f"Весна {year}" if half == "1" else f"Осень {year} / зима {int(year) + 1}"
    blocks = [
        InputRichBlockSectionHeading(text="📚 Подготовка к сессии", size=1),
        InputRichBlockParagraph(text=f"{term} · Группа {group}"),
    ]
    blocks.extend(subject_block(subject) for subject in subjects)
    if not subjects:
        blocks.append(
            InputRichBlockParagraph(
                text="Заданий пока нет. Добавь первое через /session_add."
            )
        )
    return InputRichMessage(blocks=blocks)


@router.message(Command("sessia", "session"), StateFilter(None))
async def command_session(ms: Message, command: CommandObject, api: PolytechApi):
    if ms.from_user is None:
        return
    semester = (command.args or "").strip() or current_semester()
    if re.fullmatch(r"[0-9]{4}-[12]", semester) is None:
        await ms.answer(
            "Используй /sessia или /sessia 2026-2. 1 - весна, 2 - осень и следующая зима."
        )
        return
    group = await group_for_account(account_id(ms, ms.from_user.id))
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу через /start.")
        return
    try:
        subjects = await api.session_tasks(group, semester)
    except ApiError:
        logger.exception("Failed to load session tasks")
        await ms.answer("Не удалось загрузить задания к сессии. Попробуй позже.")
        return
    await ms.answer_rich(render_session(group, semester, subjects))


@router.message(Command("session_add"), StateFilter(None, AddSessionTask))
async def command_session_add(ms: Message, state: FSMContext, command: CommandObject):
    if ms.from_user is None:
        return
    group = await group_for_account(account_id(ms, ms.from_user.id))
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу через /start.")
        return
    mode = (command.args or "").strip()
    if mode not in {"", "manual"}:
        await ms.answer(
            "Используй /session_add или /session_add manual для ручного ввода."
        )
        return
    await state.clear()
    await state.set_data(
        {"group": group, "semester": current_semester(), "manual": mode == "manual"}
    )
    await state.set_state(AddSessionTask.waiting_for_text)
    if not mode:
        await ms.answer(
            "Опиши задания к сессии одним сообщением. Например: по физике сдать "
            "лабораторные 1-4 и защитить курсовую до 20 декабря.\n\n"
            "Для ручного ввода используй /session_add manual. Для отмены используй /cancel."
        )
        return
    await ms.answer(
        "Отправь предмет и задание с новой строки. Срок можно указать в первой строке после предмета через |.\n\n"
        "Физика | 20.12.2026\n"
        "Сдать отчёты по лабораторным работам\n\n"
        "Для отмены используй /cancel."
    )


@router.message(Command("cancel"), StateFilter(AddSessionTask))
async def cancel_session_add(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Добавление задания к сессии отменено.")


@router.message(AddSessionTask.waiting_for_text, F.text, ~F.text.startswith("/"))
async def receive_session_task(ms: Message, state: FSMContext):
    text = ms.text.strip()
    if not text:
        await ms.answer("Отправь текст задания.")
        return
    data = await state.get_data()
    if data.get("manual"):
        task = await manual_task(ms, data, text)
        if task is None:
            return
        preview = f"Добавить задание для группы {task.group}?\n\n{task.subject}\n{task_details(task)}"
    else:
        task = SessionTaskText(
            group=data["group"], semester=data["semester"], text=text
        )
        preview = (
            f"Разобрать текст и сохранить задания для группы {task.group}?\n\n{text}"
        )
    if len(preview.encode("utf-16-le")) // 2 > 4096:
        await ms.answer(
            "Сократи текст задания, чтобы он поместился в сообщении подтверждения."
        )
        return
    token = uuid4().hex
    await ms.answer(
        preview,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Добавить", callback_data=f"session_save:{token}"
                    ),
                    InlineKeyboardButton(
                        text="Отмена", callback_data=f"session_cancel:{token}"
                    ),
                ]
            ]
        ),
    )
    await state.update_data(task=task.model_dump(mode="json"), token=token)
    await state.set_state(AddSessionTask.waiting_for_confirmation)


async def manual_task(ms: Message, data: dict, text: str) -> SessionTaskDraft | None:
    header, _, text = text.partition("\n")
    subject, separator, due_text = header.partition("|")
    subject, text = subject.strip(), text.strip()
    if not subject or len(subject) > 200 or not text:
        await ms.answer(
            "На первой строке укажи предмет (до 200 символов), на следующих строках напиши задание."
        )
        return
    due = None
    if separator:
        try:
            due = datetime.strptime(due_text.strip(), "%d.%m.%Y").date()
        except ValueError:
            await ms.answer("Укажи срок в формате ДД.ММ.ГГГГ или убери | и дату.")
            return
    return SessionTaskDraft(
        group=data["group"],
        semester=data["semester"],
        subject=subject,
        text=text,
        due_date=due,
    )


@router.callback_query(
    F.data.startswith("session_save:") | F.data.startswith("session_cancel:")
)
async def confirm_session_task(
    callback: CallbackQuery, state: FSMContext, api: PolytechApi
):
    ms = callback.message
    data = await state.get_data()
    if (
        not isinstance(ms, Message)
        or await state.get_state() != AddSessionTask.waiting_for_confirmation.state
        or callback.data.split(":", 1)[1] != data.get("token")
    ):
        await callback.answer("Это подтверждение уже неактуально.")
        return
    await callback.answer()
    if callback.data.startswith("session_cancel:"):
        await state.clear()
        await ms.edit_reply_markup(reply_markup=None)
        await ms.answer("Добавление задания к сессии отменено.")
        return
    payload = data["task"]
    if payload["group"] != await group_for_account(
        account_id(ms, callback.from_user.id)
    ):
        await state.clear()
        await ms.edit_reply_markup(reply_markup=None)
        await ms.answer("Группа изменилась. Начни заново через /session_add.")
        return
    reply = "Задание добавлено. Список заданий группы доступен через /sessia."
    subject = None
    try:
        if "subject" in payload:
            await api.add_session_task(SessionTaskDraft.model_validate(payload))
        else:
            subject = await api.add_session_tasks_from_text(
                SessionTaskText.model_validate(payload)
            )
    except ApiError as exc:
        if exc.status == 422:
            await state.set_state(AddSessionTask.waiting_for_text)
            await ms.edit_reply_markup(reply_markup=None)
            detail = exc.detail or "Уточни предмет, задания или сроки."
            await ms.answer(
                f"{detail}\nОтправь полный текст с уточнением или используй /cancel.",
                parse_mode=None,
            )
            return
        if exc.status != 409:
            logger.warning("Failed to save session task, API status %s", exc.status)
            await ms.answer(
                "Не удалось сохранить задание. Повтори подтверждение или используй /cancel."
            )
            return
        reply = (
            "Такое задание уже есть у группы. Список заданий доступен через /sessia."
        )
    await state.clear()
    await ms.edit_reply_markup(reply_markup=None)
    if subject is None:
        await ms.answer(reply)
    else:
        await ms.answer_rich(
            InputRichMessage(
                blocks=[
                    InputRichBlockParagraph(
                        text="Актуальный список заданий по предмету"
                    ),
                    subject_block(subject),
                ]
            )
        )


@router.message(StateFilter(AddSessionTask))
async def session_task_input_required(ms: Message):
    await ms.answer(
        "Отправь предмет и задание, нажми кнопку подтверждения или используй /cancel."
    )
