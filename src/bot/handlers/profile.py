import re
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.modules.group import Group
from core.modules.user import User

router = Router()


class Profile(StatesGroup):
    waiting_for_group = State()
    waiting_for_time = State()


def render_profile(user: User, group: Group) -> InputRichMessage:
    enabled = user.reminders_enabled
    return InputRichMessage(blocks=[
        InputRichBlockSectionHeading(text="👤 Твой профиль", size=1),
        InputRichBlockParagraph(text=f"🎓 Группа: {group.name}"),
        InputRichBlockParagraph(text=(
            f"⏰ Уведомления включены: ежедневно в {user.reminder_time:%H:%M} по Москве."
            if enabled else "🔕 Напоминания отключены."
        )),
        InputRichBlockButtons(buttons=[
            RichMessageButton(
                text="✏️ Изменить группу",
                callback_data=f"profile:group:{user.telegram_id}",
            ),
        ]),
        InputRichBlockButtons(buttons=[
            RichMessageButton(
                text=f"⏰ Время уведомления: {user.reminder_time:%H:%M}",
                callback_data=f"profile:time:{user.telegram_id}",
            ),
            RichMessageButton(
                text="🔔 Включено" if enabled else "🔕 Выключено",
                callback_data=f"profile:{'off' if enabled else 'on'}:{user.telegram_id}",
            ),
        ]),
    ])


async def load_profile(session: AsyncSession, telegram_id: int):
    result = await session.execute(
        select(User, Group).join(Group, User.group_id == Group.id)
        .where(User.telegram_id == telegram_id)
    )
    return result.first()


@router.message(Command("profile"), StateFilter(None))
async def command_profile(ms: Message, session: AsyncSession):
    if ms.from_user is None:
        return
    profile = await load_profile(session, ms.from_user.id)
    if profile is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    await ms.answer_rich(render_profile(*profile))


@router.callback_query(F.data.startswith("profile:"))
async def profile_action(cb: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, action, owner = (cb.data or "").split(":", 2)
    if owner != str(cb.from_user.id):
        await cb.answer("Это профиль другого пользователя. Открой свой через /profile.", show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer()
        return
    if await state.get_state() is not None:
        await cb.answer("Сначала заверши текущий ввод или отправь /cancel.", show_alert=True)
        return
    profile = await load_profile(session, cb.from_user.id)
    if profile is None:
        await cb.answer("Сначала укажи свою учебную группу.", show_alert=True)
        return
    if action == "group":
        await state.set_state(Profile.waiting_for_group)
        await cb.answer()
        await cb.message.answer_rich(InputRichMessage(markdown=(
            "# ✏️ Изменить группу\n\n"
            "Отправь номер новой группы в формате **261-329**.\n\n"
            "Отмена: /cancel"
        )))
    elif action == "time":
        await state.set_state(Profile.waiting_for_time)
        await cb.answer()
        await cb.message.answer("Отправь время уведомления в формате hh:mm, например 09:30. Часовой пояс: Москва. Отмена: /cancel")
    elif action in ("on", "off"):
        user, group = profile
        user.reminders_enabled = action == "on"
        await session.commit()
        await cb.answer("Настройка сохранена")
        await cb.message.answer_rich(render_profile(user, group))
    else:
        await cb.answer()


@router.message(Profile.waiting_for_group, Command("cancel"))
async def cancel_group_change(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Изменение группы отменено. Профиль: /profile")


@router.message(Profile.waiting_for_group)
async def change_group(ms: Message, state: FSMContext, session: AsyncSession):
    if ms.from_user is None:
        return
    name = (ms.text or "").strip()
    if re.fullmatch(r"[0-9]{3}-[0-9]{3}", name) is None:
        await ms.answer("Отправь номер группы в формате 261-329. Отмена: /cancel")
        return
    user = await session.scalar(select(User).where(User.telegram_id == ms.from_user.id))
    if user is None:
        await state.clear()
        await ms.answer("Сначала укажи свою учебную группу через /start.")
        return
    group = await session.scalar(select(Group).where(Group.name == name))
    if group is None:
        group = Group(name=name)
        session.add(group)
        await session.flush()
    user.group_id = group.id
    await session.commit()
    await state.clear()
    await ms.answer_rich(render_profile(user, group))


@router.message(Profile.waiting_for_time, Command("cancel"))
async def cancel_time_change(ms: Message, state: FSMContext):
    await state.clear()
    await ms.answer("Изменение времени отменено. Настройки: /profile")


@router.message(Profile.waiting_for_time)
async def change_time(ms: Message, state: FSMContext, session: AsyncSession):
    if ms.from_user is None:
        return
    value = (ms.text or "").strip()
    if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
        await ms.answer("Введи время в формате hh:mm от 00:00 до 23:59, например 09:30. Отмена: /cancel")
        return
    profile = await load_profile(session, ms.from_user.id)
    if profile is None:
        await state.clear()
        await ms.answer("Сначала укажи свою учебную группу через /start.")
        return
    user, group = profile
    user.reminder_time = time.fromisoformat(value)
    await session.commit()
    await state.clear()
    await ms.answer_rich(render_profile(user, group))
