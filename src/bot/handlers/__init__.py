from aiogram import Router

from . import add, authorization, common

router = Router()
router.include_routers(
    authorization.router,
    common.router,
    add.router,
)
