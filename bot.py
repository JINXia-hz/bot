"""bot 启动入口。

初始化图数据库 → 创建 orchestrator → 注入 scheduler → 启动 NoneBot2。
"""

import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中（NoneBot2 导入 src 需要）
sys.path.insert(0, str(Path(__file__).parent))

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot")

# NoneBot2 初始化
nonebot.init()

# 注册 OneBot V11 适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 加载插件
nonebot.load_plugins("src/plugins")

# ── 初始化图数据库和业务层 ────────────────────────

from src.graph.connection import init_database
from src.pipeline.orchestrator import Orchestrator
from src.pipeline.scheduler import Scheduler
from src.plugins.listener import inject as inject_to_plugin

# 目标群号（后续可从配置中读取）
TARGET_GROUP_ID = "0"  # 默认值，在 .env 中覆盖


def setup_bot():
    """初始化所有业务组件。"""
    logger.info("[bot] 正在初始化图数据库...")
    init_database()
    logger.info("[bot] 图数据库初始化完成")

    logger.info("[bot] 正在创建业务组件...")
    orchestrator = Orchestrator()
    scheduler = Scheduler(orchestrator)

    # 设置定时任务
    scheduler.setup(TARGET_GROUP_ID)

    # 注入到插件层
    inject_to_plugin(orchestrator, scheduler)

    logger.info("[bot] 业务组件创建完成")
    return orchestrator, scheduler


# 在 NoneBot2 启动前执行初始化
driver.on_startup(setup_bot)


if __name__ == "__main__":
    logger.info("[bot] 启动中...")
    nonebot.run()