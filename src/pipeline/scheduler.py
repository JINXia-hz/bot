"""定时任务调度器。

使用 APScheduler 管理后台任务：
  - 每 30 秒检查到期的定时回复
  - 每 60 秒检查事件自动结算
  - 每 10 分钟清理过期的无向消息
"""

import os
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.graph.raw_message_repo import RawMessageRepo

logger = logging.getLogger(__name__)


class Scheduler:
    """定时任务管理器。"""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self._scheduler: AsyncIOScheduler | None = None
        self._raw_msg_repo = RawMessageRepo()

    def setup(self, group_id: str) -> None:
        """注册所有定时任务。

        Args:
            group_id: 目标群号
        """
        self._scheduler = AsyncIOScheduler()

        # 定时回复检查间隔（秒）
        check_interval = int(os.getenv("SCHEDULED_CHECK_INTERVAL", "30"))

        # 定时回复检查
        self._scheduler.add_job(
            self._job_process_scheduled_replies,
            IntervalTrigger(seconds=check_interval),
            args=[group_id],
            id="scheduled_replies",
            name="检查定时回复",
            replace_existing=True,
        )

        # 自动结算检查
        self._scheduler.add_job(
            self._job_check_auto_settle,
            IntervalTrigger(seconds=60),
            args=[group_id],
            id="auto_settle",
            name="检查自动结算",
            replace_existing=True,
        )

        # 无向消息清理（每 10 分钟）
        retention = int(os.getenv("UNDIRECTED_RETENTION_MINUTES", "60"))
        self._scheduler.add_job(
            self._job_cleanup_undirected,
            IntervalTrigger(minutes=10),
            args=[group_id],
            id="cleanup_undirected",
            name="清理过期无向消息",
            replace_existing=True,
        )

        logger.info(
            f"[scheduler] 已注册定时任务: "
            f"每{check_interval}s检查定时回复 + 每60s检查自动结算 + 每10min清理(保留{retention}min)"
        )

    async def _job_process_scheduled_replies(self, group_id: str) -> None:
        """定时任务：检查到期的定时回复并发送。"""
        try:
            messages = self.orchestrator.process_scheduled_replies()
            if messages:
                logger.info(f"[scheduler] 定时回复: {len(messages)} 条")
                # 消息由调用方（plugin 层）发送到群
                for msg in messages:
                    logger.info(f"[scheduler] 定时回复内容: {msg[:100]}...")
        except Exception as e:
            logger.error(f"[scheduler] 定时回复检查出错: {e}", exc_info=True)

    async def _job_cleanup_undirected(self, group_id: str) -> None:
        """定时任务：清理过期的无向消息。"""
        try:
            retention = int(os.getenv("UNDIRECTED_RETENTION_MINUTES", "60"))
            count = self._raw_msg_repo.cleanup_old_undirected(retention)
            if count > 0:
                logger.info(f"[scheduler] 清理了 {count} 条过期无向消息")
        except Exception as e:
            logger.error(f"[scheduler] 清理无向消息出错: {e}", exc_info=True)

    async def _job_check_auto_settle(self, group_id: str) -> None:
        """定时任务：检查事件自动结算（由推理引擎前向链驱动）。"""
        try:
            settle_msgs = self.orchestrator.check_auto_settle()
            if settle_msgs:
                logger.info(f"[scheduler] 自动结算消息: {len(settle_msgs)} 条")
                for msg in settle_msgs:
                    logger.info(f"[scheduler] 结算: {msg[:100]}...")
        except Exception as e:
            logger.error(f"[scheduler] 自动结算检查出错: {e}", exc_info=True)

    def start(self) -> None:
        """启动调度器。"""
        if self._scheduler:
            self._scheduler.start()
            logger.info("[scheduler] 调度器已启动")

    def shutdown(self) -> None:
        """关闭调度器。"""
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("[scheduler] 调度器已关闭")