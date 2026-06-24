"""主编排器 - 串联所有模块的唯一入口。

数据流：
  1. 有向 NL 到达 → 收集无向窗口 + 图上下文 → LLM 判断 query/action
  2. query: LLM 直接回答 → 返回群聊消息
  3. action: LLM 产出 FL 指令 → 执行 → 即刻回复 + 定时回复
"""

import logging
from src.graph.base_repo import BaseRepo
from src.graph.raw_message_repo import RawMessageRepo
from src.graph.formal_language_repo import (
    FormalLanguageRepo, FLSource, FLCategory, FLStatus,
)
from src.graph.data_point_repo import DataPointRepo
from src.graph.event_repo import EventRepo, EventTriggerType
from src.graph.action_log_repo import ActionLogRepo
from src.graph.context_assembler import ContextAssembler
from src.engine.translator import Translator
from src.engine.executor import Executor, ActionResult
from src.engine.event_manager import EventManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """流程编排器。

    调用方式：
        orch.on_directed_message(content, sender, group_id)     → 处理一条 @bot 消息，立刻返回响应
        orch.process_scheduled_replies()                         → 检查定时回复，到期则返回消息
        orch.check_auto_settle()                                 → 检查事件自动结算
    """

    def __init__(self):
        # repos
        self.raw_msg = RawMessageRepo()
        self.fl_repo = FormalLanguageRepo()
        self.dp_repo = DataPointRepo()
        self.event_repo = EventRepo()
        self.log_repo = ActionLogRepo()

        # engines
        self.translator = Translator()
        self.executor = Executor()
        self.event_mgr = EventManager()
        self.ctx_assembler = ContextAssembler()

        self._base = BaseRepo()
        self._last_log_id: str = ""

    # ── 核心流程：实时消息处理 ─────────────────────────

    def on_directed_message(
        self,
        content: str,
        sender: str,
        group_id: str,
    ) -> str:
        """处理一条 @bot 的有向消息。

        流程：
        1. 存储 RawMessage
        2. 获取时间窗口内的无向 NL
        3. 查图上下文（context_assembler）
        4. LLM 翻译（传入：有向 NL + 无向窗口 + 图上下文）
           → 返回 {intent: "query"|"action", response?, instructions?}
        5. intent=query → 直接返回 response
        6. intent=action → 走 _execute_and_persist 链路

        Args:
            content: 消息内容
            sender: 发送者
            group_id: 群号

        Returns:
            群聊回复消息，空字符串表示无回复
        """
        import os

        # 1. 存储有向消息
        msg_id = self.raw_msg.create(
            content=content,
            sender=sender,
            group_id=group_id,
            is_directed=True,
        )
        logger.info(f"[orchestrator] 收到有向消息 {msg_id}: {sender}: {content}")

        # 2. 获取时间窗口内的无向 NL
        window_minutes = int(os.getenv("UNDIRECTED_WINDOW_MINUTES", "30"))
        context = self.raw_msg.list_undirected_window(group_id, window_minutes)
        context_texts = [m.get("content", "") for m in context]
        logger.info(f"[orchestrator] 无向窗口: {len(context_texts)} 条消息（{window_minutes}分钟）")

        # 3. 查图上下文
        graph_ctx = self.ctx_assembler.assemble(sender, group_id)
        logger.info(f"[orchestrator] 图上下文长度: {len(graph_ctx)} 字符")

        # 4. LLM 翻译（含意图判断）
        result = self.translator.nl_to_fl(content, context_texts, graph_ctx)
        intent = result.get("intent", "query")
        instructions = result.get("instructions")
        response = result.get("response")

        logger.info(f"[orchestrator] LLM 判断意图: {intent}")

        # 5. intent=query → 直接返回
        if intent == "query":
            reply = response or "收到，但我不知道该怎么回复哦 😅"
            logger.info(f"[orchestrator] 查询模式回复: {reply[:80]}...")
            return reply

        # 6. intent=action → 走执行链路
        if not instructions or not isinstance(instructions, list):
            logger.warning(f"[orchestrator] action 模式但无有效 instructions")
            return "指令已收到，但无法解析具体操作"

        # 为每条 NL→FL 翻译创建 FL 记录
        fl_ids = []
        for instr in instructions:
            if not isinstance(instr, dict):
                continue
            op = instr.get("op", "")
            if op in ("open_event", "settle_event", "cancel_event"):
                category = FLCategory.EVENT_MANAGEMENT
            else:
                category = FLCategory.GENERAL_INSTRUCTION

            fl_id = self.fl_repo.create(
                payload=instr,
                source=FLSource.LLM_TRANSLATED,
                category=category,
                parent_message_id=msg_id,
            )
            self.fl_repo.link_to_message(fl_id, msg_id)
            fl_ids.append(fl_id)

        # 逐条执行指令并落地，收集即时回复
        immediate_replies = []
        for fl_id, instr in zip(fl_ids, instructions):
            if not isinstance(instr, dict):
                continue
            result = self._execute_and_persist(instr, fl_id)
            if result.message:
                immediate_replies.append(result.message)

            # 处理行动产出的输出 FL
            fl_replies = self._handle_output_fls(result, fl_id)
            immediate_replies.extend(fl_replies)

        # 标记所有已执行的 FL
        for fl_id in fl_ids:
            self.fl_repo.update_status(fl_id, FLStatus.EXECUTED)

        return "\n".join(immediate_replies) if immediate_replies else "指令已执行"

    def _handle_output_fls(self, result: ActionResult, trigger_fl_id: str) -> list[str]:
        """处理行动产出的输出 FL，分立即回复和定时回复。"""
        immediate_messages = []

        for fl_dict in result.output_fls:
            payload = fl_dict.get("payload", {})
            reply_type = fl_dict.get("reply_type", "immediate")
            schedule_at = fl_dict.get("schedule_at")

            if reply_type == "immediate":
                if schedule_at:
                    payload["schedule_at"] = schedule_at

                category = FLCategory.OUTPUT_IMMEDIATE
                status = FLStatus.REPLIED

                created_fl_id = self.fl_repo.create(
                    payload=payload,
                    source=FLSource.ACTION_GENERATED,
                    category=category,
                    parent_log_id=self._last_log_id,
                    status=status,
                )
                if self._last_log_id:
                    self.fl_repo.link_generated_by(created_fl_id, self._last_log_id)

                nl = self.translator.fl_to_nl(payload)
                if nl:
                    immediate_messages.append(nl)

                logger.info(f"[orchestrator] 即刻回复 FL {created_fl_id}")

            elif reply_type == "scheduled":
                payload["schedule_at"] = schedule_at or ""

                category = FLCategory.OUTPUT_SCHEDULED
                status = FLStatus.SCHEDULED

                created_fl_id = self.fl_repo.create(
                    payload=payload,
                    source=FLSource.ACTION_GENERATED,
                    category=category,
                    parent_log_id=self._last_log_id,
                    status=status,
                )
                if self._last_log_id:
                    self.fl_repo.link_generated_by(created_fl_id, self._last_log_id)

                logger.info(f"[orchestrator] 定时回复 FL {created_fl_id}，计划 {schedule_at}")

        return immediate_messages

    def _execute_and_persist(
        self,
        fl_payload: dict,
        fl_id: str,
    ) -> ActionResult:
        """执行单条 FL 指令并写入图数据库。"""
        input_dps: dict[str, dict] = {}

        result = self.executor.execute(fl_payload, input_dps)

        # 创建 ActionLog
        log_id = self.log_repo.create(
            event_id=result.used_event_id,
            action_summary=result.action_summary,
        )
        self._last_log_id = log_id
        logger.info(f"[orchestrator] 创建行动日志 {log_id}: {result.action_summary}")

        # 链接 FL → Log
        self.fl_repo.link_triggered_action(fl_id, log_id, result.used_event_id)

        # 落地数据点
        output_dp_ids = []
        for dp_dict in result.output_datapoints:
            dp_id = dp_dict.pop("dp_id", None)
            eid = dp_dict.get("event_id") or result.used_event_id

            if result.opened_event:
                event_id = self.event_repo.create(
                    title=result.opened_event["title"],
                    created_by=result.opened_event["created_by"],
                    trigger_type=result.opened_event.get("trigger_type", EventTriggerType.MANUAL),
                    auto_settle_at=result.opened_event.get("auto_settle_at"),
                )
                result.used_event_id = event_id
                eid = event_id
                logger.info(f"[orchestrator] 创建事件 {event_id}: {result.opened_event['title']}")

            created_dp_id = self.dp_repo.create(
                dp_type=dp_dict["dp_type"],
                user_name=dp_dict["user_name"],
                payload=dp_dict["payload"],
                event_id=eid,
                dp_id=dp_id,
            )
            output_dp_ids.append(created_dp_id)

            if eid:
                self.dp_repo.link_to_event(created_dp_id, eid)
            self.dp_repo.link_produced(log_id, created_dp_id)

            logger.info(f"[orchestrator] 创建数据点 {created_dp_id}: {dp_dict['dp_type']}")

        # 落地数据线
        for from_id, to_id in result.data_lines:
            if not to_id:
                continue
            self.dp_repo.link_data_line(from_id, to_id, log_id, result.used_event_id)
            logger.info(f"[orchestrator] 创建数据线 {from_id} → {to_id}")

        # 落地被消耗的数据点
        for dp_id in result.consumed_dp_ids:
            self.dp_repo.link_consumed(dp_id, log_id)

        # 处理事件结算/取消
        if result.settled_event_id:
            summary_dp_id = output_dp_ids[0] if output_dp_ids else ""
            self.event_repo.settle(result.settled_event_id, summary_dp_id)
            logger.info(f"[orchestrator] 结算事件 {result.settled_event_id}")

        if result.cancelled_event_id:
            self.event_repo.cancel(result.cancelled_event_id)
            logger.info(f"[orchestrator] 取消事件 {result.cancelled_event_id}")

        return result

    # ── 定时回复处理 ────────────────────────────────

    def process_scheduled_replies(self) -> list[str]:
        """检查到期的定时回复，翻译为自然语言并返回。"""
        due = self.fl_repo.list_due_scheduled()
        if not due:
            return []

        logger.info(f"[orchestrator] 处理 {len(due)} 条到期定时回复")

        messages = []
        for fl in due:
            nl = self.translator.fl_to_nl(fl.get("payload", {}))
            if nl:
                messages.append(nl)
            self.fl_repo.update_status(fl["id"], FLStatus.REPLIED)
            logger.info(f"[orchestrator] 定时回复已发送 {fl['id']}")

        return messages

    # ── 自动结算检测 ────────────────────────────────

    def check_auto_settle(self) -> list[str]:
        """检查所有活跃事件，对到期的执行自动结算。"""
        messages = []
        active_events = self.event_repo.list_active()

        for event in active_events:
            action = self.event_mgr.should_auto_settle(event)
            if action and action.action == "settle":
                dps = self.dp_repo.get_event_datapoints(event["id"])
                dls = self.dp_repo.get_data_lines_for_event(event["id"])
                summary = self.event_mgr.generate_summary(event, dps, dls)

                summary_dp_id = self.dp_repo.create(
                    dp_type="settlement_summary",
                    user_name="system",
                    payload=summary,
                    event_id=event["id"],
                )
                self.dp_repo.link_to_event(summary_dp_id, event["id"])
                self.event_repo.settle(event["id"], summary_dp_id)

                messages.append(
                    f"📋 事件「{event['title']}」已自动结算！"
                    f"共 {summary['total_datapoints']} 个数据点，"
                    f"{len(summary['participants'])} 人参与"
                )
                logger.info(f"[orchestrator] 自动结算事件 {event['id']}: {event['title']}")

        return messages