from aiogram import Router

from . import add, authorization, common, day, deadlines, week

router = Router()
router.include_routers(
    authorization.router,
    common.router,
    day.router,
    deadlines.router,
    week.router,
    add.router,
)
