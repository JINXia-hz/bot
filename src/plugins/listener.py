"""NoneBot2 消息监听插件。

监听群聊消息，区分有向/无向，收集到图数据库，
并触发 orchestrator 处理 @bot 消息。
"""

import logging
from nonebot import on_message, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import Rule

from src.graph.raw_message_repo import RawMessageRepo

logger = logging.getLogger(__name__)

# 全局 orchestrator 引用，在 bot.py 启动时注入
_orchestrator = None
_scheduler = None


def inject(orchestrator, scheduler) -> None:
    """注入 orchestrator 和 scheduler 实例。"""
    global _orchestrator, _scheduler
    _orchestrator = orchestrator
    _scheduler = scheduler


async def _is_at_bot(event: GroupMessageEvent) -> bool:
    """判断消息是否 @了机器人。"""
    if event.is_tome():
        return True
    # 部分协议可能不支持 is_tome()，备用检查 @bot 关键词
    msg = str(event.get_message()).strip()
    return msg.startswith("@bot")


def _extract_mentions(event: GroupMessageEvent, bot_self_id: int | None = None) -> list[str]:
    """提取消息中 @ 的用户（排除 bot 自己）。

    OneBot V11 的 at segment 中只有 qq 号，因此用 qq 号作为用户标识。
    """
    mentions = []
    for seg in event.get_message():
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq is None:
                continue
            qq_str = str(qq)
            if bot_self_id is not None and qq_str == str(bot_self_id):
                continue
            mentions.append(qq_str)
    return mentions


# 所有群消息监听器
all_messages = on_message(rule=Rule(lambda event: True), priority=100, block=False)


@all_messages.handle()
async def handle_all_messages(bot: Bot, event: GroupMessageEvent):
    """收集所有群聊消息。

    无向消息：直接存储
    有向消息（@bot）：存储并触发 orchestrator 处理
    """
    if not isinstance(event, GroupMessageEvent):
        return

    raw_msg = event.get_plaintext().strip()
    if not raw_msg:
        return

    sender = event.sender.nickname or str(event.user_id)
    group_id = str(event.group_id)
    is_directed = await _is_at_bot(event)
    mentions = _extract_mentions(event, bot_self_id=bot.self_id)

    # 存储到图数据库
    repo = RawMessageRepo()
    repo.create(
        content=raw_msg,
        sender=sender,
        group_id=group_id,
        is_directed=is_directed,
    )

    logger.debug(f"[listener] {'有向' if is_directed else '无向'}消息: {sender}: {raw_msg[:50]} mentions={mentions}")

    # 有向消息：触发处理
    if is_directed and _orchestrator:
        try:
            response = _orchestrator.on_directed_message(raw_msg, sender, group_id, mentions=mentions)
            if response:
                await bot.send(event, response)
        except Exception as e:
            logger.error(f"[listener] 处理消息出错: {e}", exc_info=True)
            await bot.send(event, f"❌ 处理失败: {str(e)}")


# ── 生命周期钩子 ────────────────────────────────────

driver = get_driver()


@driver.on_startup
async def on_startup():
    """应用启动时执行。"""
    logger.info("[bot] 插件层启动")
    if _scheduler:
        _scheduler.start()


@driver.on_shutdown
async def on_shutdown():
    """应用关闭时执行。"""
    logger.info("[bot] 插件层关闭")
    if _scheduler:
        _scheduler.shutdown()