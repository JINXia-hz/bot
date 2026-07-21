"""Web 管理后台插件。

将 FastAPI 路由挂载到 NoneBot2 内置的 FastAPI 应用上，
提供 /admin 路径的观测与管理面板。
"""

import logging

from nonebot import get_app

try:
    from fastapi import FastAPI
except ImportError:
    FastAPI = None  # type: ignore

from src.web.admin import router as admin_router

logger = logging.getLogger(__name__)


async def _mount_admin_router() -> None:
    """在 NoneBot2 的 FastAPI 应用上挂载管理后台路由。"""
    app = get_app()
    if FastAPI is None or not isinstance(app, FastAPI):
        logger.warning("[web_admin] 当前驱动不支持 FastAPI，管理后台未挂载")
        return

    # 避免重复挂载：检查是否已存在 /admin 前缀路由
    for r in app.routes:
        path = getattr(r, "path", "")
        if isinstance(path, str) and path.startswith("/admin"):
            logger.info("[web_admin] 管理后台路由已存在，跳过重复挂载")
            return

    app.include_router(admin_router)
    logger.info("[web_admin] 管理后台已挂载到 /admin")


# 使用 on_startup 钩子确保驱动已初始化
from nonebot import get_driver

driver = get_driver()
driver.on_startup(_mount_admin_router)
