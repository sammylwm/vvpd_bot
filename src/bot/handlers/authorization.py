from html import escape

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InputRichMessage, Message

from bot.identity import account_id
from bot.preferences import preferences
from core.group_number import is_valid_group_number

from .filters import UnregisteredUser

router = Router()


class Authorization(StatesGroup):
    waiting_for_group = State()


@router.message(Authorization.waiting_for_group)
async def receive_group(ms: Message, state: FSMContext):
    if ms.from_user is None:
        return

    group = (ms.text or "").strip()
    if not is_valid_group_number(group):
        await ms.answer(
            "✏️ <b>Номер группы должен содержать 3 цифры, дефис и 3 цифры</b>\n\n"
            "Например: <code>261-329</code>\n",
            parse_mode="HTML",
        )
        return

    owner = account_id(ms, ms.from_user.id)
    await preferences.set_group(owner, group)

    await state.clear()
    await ms.answer(
        f"✅ <b>Группа указана!</b>\n\n🎓 Твоя группа: <b>{escape(group)}</b>\n\n"
        "Время и статус уведомлений можно изменить кнопками в /profile.",
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
