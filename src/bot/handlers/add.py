import logging
from contextlib import ExitStack
from html import escape
from tempfile import TemporaryFile
from uuid import uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiohttp import ClientError

from bot.attachments import (
    MAX_FILE_SIZE,
    MAX_FILES,
    attachment_from_message,
    send_attachment_links,
)
from bot.identity import account_id
from bot.preferences import group_for_account
from core.api import ApiError, DeadlinePreview, PolytechApi

router = Router()
logger = logging.getLogger(__name__)


class AddDeadline(StatesGroup):
    waiting_for_text = State()
    waiting_for_confirmation = State()


def deadline_details(preview: DeadlinePreview) -> str:
    return (
        f"Предмет: {escape(preview.subject)}\n"
        f"Дата: {preview.date:%d.%m.%Y}\n"
        f"Задание: {escape(preview.deadline)}"
    )


@router.message(Command("deadline"), StateFilter(None, AddDeadline))
async def command_deadline(ms: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddDeadline.waiting_for_text)
    await ms.answer(
        "Отправь текст дедлайна, например: сдать лабораторную по физике до пятницы."
        "\nМожно отправить до 5 фото или документов. Добавь текст в подпись или отправь его после файлов."
        "\nДля отмены: /cancel"
    )


@router.message(StateFilter(AddDeadline), Command("cancel"))
async def cancel_deadline(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Добавление дедлайна отменено.")


@router.message(AddDeadline.waiting_for_text, ~F.text.startswith("/"))
async def receive_deadline(
    ms: Message, state: FSMContext, api: PolytechApi, album: list[Message] | None = None
):
    if ms.from_user is None:
        return
    group = await group_for_account(account_id(ms, ms.from_user.id))
    if group is None:
        await state.clear()
        await ms.answer("Сначала укажи свою учебную группу через /start.")
        return
    data = await state.get_data()
    if data.get("group") != group:
        data = {}
    attachments = {item["unique_id"]: item for item in data.get("attachments", [])}
    messages = album or [ms]
    for message in messages:
        attachment = attachment_from_message(message)
        if attachment is None:
            if message.text:
                continue
            await ms.answer(
                "Можно прикрепить фото или документ. Отправь поддерживаемый файл либо текст дедлайна."
            )
            return
        if attachment["size"] > MAX_FILE_SIZE:
            await ms.answer("Файл превышает 20 МиБ. Отправь файл меньшего размера.")
            return
        attachments[attachment["unique_id"]] = attachment
    if len(attachments) > MAX_FILES:
        await ms.answer("К одному дедлайну можно прикрепить до 5 файлов.")
        return

    files = list(attachments.values())
    await state.update_data(
        group=group,
        attachments=files,
        clarification_text=data.get("clarification_text"),
    )
    text = "\n".join(
        part
        for message in messages
        if (part := (message.text or message.caption or "").strip())
    )
    if not text:
        await ms.answer(
            f"Выбрано файлов: {len(files)}. Отправь текст дедлайна или добавь ещё файлы."
        )
        return
    if data.get("clarification_text"):
        text = f"{data['clarification_text']}\nУточнение: {text}"
    try:
        preview = await api.parse(group, text)
    except ApiError as exc:
        if exc.status == 422:
            await state.update_data(group=group, clarification_text=text)
            detail = escape(exc.detail or "Уточни предмет, дату или задание.")
            await ms.answer(f"{detail}\nОтправь уточнение или используй /cancel.")
        else:
            logger.warning("Failed to parse deadline, API status %s", exc.status)
            await ms.answer(
                "Не удалось обработать дедлайн. Повтори сообщение позже или используй /cancel."
            )
        return

    token = uuid4().hex
    await ms.answer(
        f"Подтвердить добавление ДЗ?\n{deadline_details(preview)}\nФайлов: {len(files)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подтвердить", callback_data=f"add_yes:{token}"
                    ),
                    InlineKeyboardButton(
                        text="Отмена", callback_data=f"add_no:{token}"
                    ),
                ]
            ]
        ),
    )
    await state.update_data(
        preview=preview.model_dump(mode="json"), confirmation_token=token
    )
    await state.set_state(AddDeadline.waiting_for_confirmation)


@router.callback_query(
    F.data.startswith("add_yes:")
    | F.data.startswith("add_no:")
    | F.data.startswith("add_text:")
)
async def confirm_deadline(
    callback: CallbackQuery, state: FSMContext, api: PolytechApi
):
    ms = callback.message
    data = await state.get_data()
    if (
        not isinstance(ms, Message)
        or await state.get_state() != AddDeadline.waiting_for_confirmation.state
        or callback.data.split(":", 1)[1] != data.get("confirmation_token")
    ):
        await callback.answer("Это подтверждение уже неактуально.")
        return
    await callback.answer()
    if callback.data.startswith("add_no:"):
        await state.clear()
        await ms.edit_reply_markup(reply_markup=None)
        await ms.answer("Добавление дедлайна отменено.")
        return
    preview = DeadlinePreview.model_validate(data["preview"])
    if preview.group != await group_for_account(account_id(ms, callback.from_user.id)):
        await state.clear()
        await ms.answer("Группа изменилась. Начни добавление заново: /deadline.")
        return
    attachments = (
        [] if callback.data.startswith("add_text:") else data.get("attachments", [])
    )
    try:
        if attachments:
            with ExitStack() as stack:
                uploads = []
                for attachment in attachments:
                    stream = stack.enter_context(TemporaryFile())
                    await ms.bot.download(attachment["file_id"], destination=stream)
                    uploads.append(
                        (attachment["name"], attachment["content_type"], stream)
                    )
                result = await api.confirm(preview, uploads)
        else:
            result = await api.confirm(preview)
    except (TelegramAPIError, ClientError, OSError, TimeoutError):
        logger.exception("Failed to download Telegram attachments")
        await ms.answer(
            "Не удалось скачать файлы из Telegram. Повтори подтверждение позже."
        )
        return
    except ApiError as exc:
        if attachments and exc.status in {409, 413, 422}:
            detail = escape(exc.detail or "API отклонил вложения.")
            token = data["confirmation_token"]
            await ms.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Сохранить без файлов",
                                callback_data=f"add_text:{token}",
                            ),
                            InlineKeyboardButton(
                                text="Отмена", callback_data=f"add_no:{token}"
                            ),
                        ]
                    ]
                )
            )
            await ms.answer(
                f"{detail}\nМожно сохранить только текст или начать заново через /deadline."
            )
            return
        logger.warning("Failed to confirm deadline, API status %s", exc.status)
        await ms.answer(
            "Не удалось сохранить дедлайн. Нажми «Подтвердить» позже или используй /cancel."
        )
        return

    await state.clear()
    await ms.edit_reply_markup(reply_markup=None)
    title = {
        "created": "Дедлайн добавлен.",
        "updated": "Дедлайн обновлён.",
        "unchanged": "Такой дедлайн уже есть у твоей группы.",
    }[result.status]
    await ms.answer(f"{title}\n{deadline_details(result.deadline)}")
    await send_attachment_links(ms, [result.deadline])


@router.message(StateFilter(AddDeadline))
async def deadline_text_required(ms: Message):
    await ms.answer(
        "Отправь текст дедлайна, нажми кнопку подтверждения или используй /cancel."
    )
