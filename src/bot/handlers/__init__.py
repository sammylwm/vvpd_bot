from aiogram import Router

from . import add, authorization, common, day, week

router = Router()
router.include_routers(
    authorization.router,
    common.router,
    day.router,
    week.router,
    add.router,
)
