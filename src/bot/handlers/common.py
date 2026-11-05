from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import InputRichMessage, Message

router = Router()

START_MESSAGE = """# 🎓 Твой учебный помощник

Все важное для учёбы — в одном чате.

## ⏳ Дедлайны
Добавляй сроки сдачи работ, чтобы помнить, **что и когда нужно сдать**.

## 🗓 Расписание
Получай расписание занятий и планируй свой учебный день.

Команда /reminder — расписание на сегодня и дедлайны на завтра.
Команда /deadlines — только дедлайны на текущую неделю, с понедельника по воскресенье.

## 📚 Задания
Узнавай, что задали, и держи учебные задачи под рукой.

---

> Меньше времени на поиски — больше на учёбу и отдых.
"""


@router.message(CommandStart())
async def command_start(ms: Message):
    await ms.answer_rich(InputRichMessage(markdown=START_MESSAGE))
