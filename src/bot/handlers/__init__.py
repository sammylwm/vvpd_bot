from aiogram import Router

from . import add, authorization, common, day, deadline_buttons, deadlines, delete, profile, reminder_settings, week

router = Router()
router.include_routers(
    authorization.router,
    profile.router,
    common.router,
    day.router,
    deadline_buttons.router,
    deadlines.router,
    delete.router,
    reminder_settings.router,
    week.router,
    add.router,
)
