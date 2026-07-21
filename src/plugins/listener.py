"""NoneBot2 消息监听插件。

监听群聊消息，区分有向/无向，收集到图数据库，
并触发 orchestrator 处理 @bot 消息。
"""

import logging
import re
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


_MENTION_RE = re.compile(r"@([^\s@]+)")


async def _resolve_qq_to_name(bot: Bot, group_id: int, qq: str) -> str:
    """通过 OneBot V11 接口把 qq 号解析成群名片/昵称，失败时返回 qq 号。"""
    try:
        info = await bot.get_group_member_info(
            group_id=group_id, user_id=int(qq), no_cache=False
        )
        # 部分实现返回 dict，部分返回 Member 对象
        if isinstance(info, dict):
            return info.get("card") or info.get("nickname") or qq
        return getattr(info, "card", None) or getattr(info, "nickname", None) or qq
    except Exception:
        return qq


async def _extract_mentions(bot: Bot, event: GroupMessageEvent) -> list[str]:
    """提取消息中 @ 的用户（排除 bot 自己）。

    同时处理两种情况：
      1. 真实的 OneBot at segment：解析成群名片/昵称；
      2. 纯文本里的 @昵称：兼容某些客户端没有发出 at segment 的场景。
    """
    self_id = str(bot.self_id) if bot.self_id else None
    group_id = event.group_id
    mentioned: set[str] = set()

    # 1. 真实 at segment
    for seg in event.get_message():
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq is None:
                continue
            qq_str = str(qq)
            if self_id is not None and qq_str == self_id:
                continue
            name = await _resolve_qq_to_name(bot, group_id, qq_str)
            if name:
                mentioned.add(name)

    # 2. 文本中的 @昵称
    text = event.get_plaintext()
    for m in _MENTION_RE.finditer(text):
        name = m.group(1).strip()
        if not name:
            continue
        if self_id is not None and name == self_id:
            continue
        if name.lower() == "bot":
            continue
        mentioned.add(name)

    return list(mentioned)


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
    mentions = await _extract_mentions(bot, event)

    # 存储到图数据库
    repo = RawMessageRepo()
    repo.create(
        content=raw_msg,
        sender=sender,
        group_id=group_id,
        is_directed=is_directed,
    )

    logger.info(f"[listener] {'有向' if is_directed else '无向'}消息: {sender}: {raw_msg[:50]} mentions={mentions}")

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

try:
    driver = get_driver()
except ValueError:
    # 在测试等未初始化 NoneBot 的环境中允许导入本模块
    driver = None


if driver is not None:
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