from aiogram import Router

from bot.group_topics import DeadlineTopicMiddleware

from . import add, authorization, common, day, deadline_buttons, deadlines, delete, group_setup, profile, semester, week

router = Router()
work_router = Router()
work_router.message.outer_middleware(DeadlineTopicMiddleware())
work_router.callback_query.outer_middleware(DeadlineTopicMiddleware())
router.include_routers(group_setup.router, work_router)
work_router.include_routers(
    authorization.router,
    profile.router,
    common.router,
    day.router,
    deadline_buttons.router,
    deadlines.router,
    delete.router,
    week.router,
    semester.router,
    add.router,
)
