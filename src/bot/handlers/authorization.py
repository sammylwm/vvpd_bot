from html import escape
import re

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InputRichMessage, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.modules.group import Group
from core.modules.user import User

from .filters import UnregisteredUser

router = Router()


class Authorization(StatesGroup):
    waiting_for_group = State()


@router.message(Authorization.waiting_for_group)
async def receive_group(ms: Message, state: FSMContext, session: AsyncSession):
    if ms.from_user is None:
        return

    group = ms.text or ""
    if re.fullmatch(r"[0-9]{3}-[0-9]{3}", group) is None:
        await ms.answer(
            "✏️ <b>Номер группы должен содержать 3 цифры, дефис и 3 цифры</b>\n\n"
            "Например: <code>261-329</code>\n",
            parse_mode="HTML",
        )
        return

    async with session.begin():
        db_group = await session.scalar(select(Group).where(Group.name == group))
        if db_group is None:
            db_group = Group(name=group)
            session.add(db_group)

        user = await session.scalar(
            select(User).where(User.telegram_id == ms.from_user.id)
        )
        if user is None:
            session.add(User(telegram_id=ms.from_user.id, group=db_group))
        else:
            user.group = db_group

    await state.clear()
    await ms.answer(
        f"✅ <b>Группа указана!</b>\n\n🎓 Твоя группа: <b>{escape(group)}</b>\n\n"
        "Настройка утренних напоминаний: /reminders\n"
        "Отключить: /reminders off · Включить: /reminders on",
        parse_mode="HTML",
    )


@router.message(StateFilter(None), UnregisteredUser())
async def not_sign(ms: Message, state: FSMContext):
    await state.set_state(Authorization.waiting_for_group)
    await ms.answer_rich(
        InputRichMessage(
            markdown=(
                "# 🎓 Давай познакомимся!\n\n"
                "Укажи свою **учебную группу**, чтобы продолжить.\n\n"
                "✏️ Отправь номер в формате: 3 цифры, дефис, 3 цифры.\n"
                "Например: **261-329**"
            )
        )
    )
