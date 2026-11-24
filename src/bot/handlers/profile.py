import re
from dataclasses import replace
from datetime import time

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InputRichBlockButtons,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichMessage,
    Message,
    RichMessageButton,
)

from bot.identity import account_id
from bot.preferences import ProfileSettings, preferences
from core.group_number import is_valid_group_number

router = Router()


class Profile(StatesGroup):
    waiting_for_group = State()
    waiting_for_time = State()


def render_profile(user: ProfileSettings) -> InputRichMessage:
    enabled = user.reminders_enabled
    return InputRichMessage(
        blocks=[
            InputRichBlockSectionHeading(text="👤 Твой профиль", size=1),
            InputRichBlockParagraph(text=f"🎓 Группа: {user.group}"),
            InputRichBlockParagraph(
                text=(
                    f"⏰ Уведомления включены: ежедневно в {user.reminder_time:%H:%M} по Москве."
                    if enabled
                    else "🔕 Напоминания отключены."
                )
            ),
            InputRichBlockButtons(
                buttons=[
                    RichMessageButton(
                        text="✏️ Изменить группу",
                        callback_data=f"profile:group:{user.telegram_id}",
                    ),
                ]
            ),
            InputRichBlockButtons(
                buttons=[
                    RichMessageButton(
                        text=f"⏰ Время: {user.reminder_time:%H:%M}",
                        callback_data=f"profile:time:{user.telegram_id}",
                    ),
                    RichMessageButton(
                        text="🔔 Включено" if enabled else "🔕 Выключено",
                        callback_data=f"profile:{'off' if enabled else 'on'}:{user.telegram_id}",
                    ),
                ]
            ),
        ]
    )


@router.message(Command("profile"), StateFilter(None))
async def command_profile(ms: Message):
    if ms.from_user is None:
        return
    profile = await preferences.profile(account_id(ms, ms.from_user.id))
    if profile is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    await ms.answer_rich(render_profile(profile))


@router.callback_query(F.data.startswith("profile:"))
async def profile_action(cb: CallbackQuery, state: FSMContext):
    _, action, owner = (cb.data or "").split(":", 2)
    if owner != str(account_id(cb.message, cb.from_user.id)):
        await cb.answer(
            "Это профиль другого пользователя. Открой свой через /profile.",
            show_alert=True,
        )
        return
    if not isinstance(cb.message, Message):
        await cb.answer()
        return
    if await state.get_state() is not None:
        await cb.answer(
            "Сначала заверши текущий ввод или отправь /cancel.", show_alert=True
        )
        return
    profile = await preferences.profile(account_id(cb.message, cb.from_user.id))
    if profile is None:
        await cb.answer("Сначала укажи свою учебную группу.", show_alert=True)
        return
    if action == "group":
        await state.set_state(Profile.waiting_for_group)
        await cb.answer()
        await cb.message.answer_rich(
            InputRichMessage(
                markdown=(
                    "# ✏️ Изменить группу\n\n"
                    "Отправь номер новой группы, например **261-329**.\n\n"
                    "Отмена: /cancel"
                )
            )
        )
    elif action == "time":
        await state.set_state(Profile.waiting_for_time)
        await cb.answer()
        await cb.message.answer(
            "Отправь время уведомления в формате hh:mm, например 09:30. Часовой пояс: Москва. Отмена: /cancel"
        )
    elif action in ("on", "off"):
        await preferences.update_profile(
            profile.telegram_id, reminders_enabled=action == "on"
        )
        profile = replace(profile, reminders_enabled=action == "on")
        await cb.answer("Настройка сохранена")
        await cb.message.answer_rich(render_profile(profile))
    else:
        await cb.answer()


@router.message(Profile.waiting_for_group, Command("cancel"))
async def cancel_group_change(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Изменение группы отменено. Профиль: /profile")


@router.message(Profile.waiting_for_group)
async def change_group(ms: Message, state: FSMContext):
    if ms.from_user is None:
        return
    name = (ms.text or "").strip()
    if not is_valid_group_number(name):
        await ms.answer("Отправь номер группы, например 261-329. Отмена: /cancel")
        return
    profile = await preferences.profile(account_id(ms, ms.from_user.id))
    if profile is None:
        await state.clear()
        await ms.answer("Сначала укажи свою учебную группу через /start.")
        return
    await preferences.set_group(profile.telegram_id, name)
    profile = replace(profile, group=name)
    await state.clear()
    await ms.answer_rich(render_profile(profile))


@router.message(Profile.waiting_for_time, Command("cancel"))
async def cancel_time_change(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Изменение времени отменено. Настройки: /profile")


@router.message(Profile.waiting_for_time)
async def change_time(ms: Message, state: FSMContext):
    if ms.from_user is None:
        return
    value = (ms.text or "").strip()
    if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
        await ms.answer(
            "Введи время в формате hh:mm от 00:00 до 23:59, например 09:30. Отмена: /cancel"
        )
        return
    profile = await preferences.profile(account_id(ms, ms.from_user.id))
    if profile is None:
        await state.clear()
        await ms.answer("Сначала укажи свою учебную группу через /start.")
        return
    reminder_time = time.fromisoformat(value)
    await preferences.update_profile(profile.telegram_id, reminder_time=reminder_time)
    profile = replace(profile, reminder_time=reminder_time)
    await state.clear()
    await ms.answer_rich(render_profile(profile))
