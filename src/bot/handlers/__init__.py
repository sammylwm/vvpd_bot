from aiogram import Router

from . import add, authorization, common, day, deadlines, profile, reminder_settings, week

router = Router()
router.include_routers(
    authorization.router,
    profile.router,
    common.router,
    day.router,
    deadlines.router,
    reminder_settings.router,
    week.router,
    add.router,
)
