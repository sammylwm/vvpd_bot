from aiogram import Router

from . import authorization, common

router = Router()
router.include_routers(
    authorization.router,
    common.router,
)
