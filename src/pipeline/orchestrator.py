"""主编排器 - 串联所有模块的唯一入口（v2 规则引擎架构）。

数据流：
  1. 有向 NL 到达 → 收集无向窗口 + 图上下文 → LLM 判断 query/action
  2. query: 规则引擎后向链查询 → 确定性结果 → LLM 润色 → 返回
  3. action: LLM 产出 FL → 注入推理引擎前向链 → 收集 ops → 落地执行
"""

import json
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
from src.engine.event_manager import EventManager
from src.engine.rule_engine import Fact, Var
from src.engine.graph_searcher import GraphSearcher
from src.engine.inference import InferenceEngine
from src.engine.rules import get_rule_base

logger = logging.getLogger(__name__)


class Orchestrator:
    """流程编排器 v2。

    规则引擎驱动：LLM 负责 NL→FL，推理引擎负责计算。
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
        self.event_mgr = EventManager()
        self.ctx_assembler = ContextAssembler()

        # 规则引擎
        self.graph_searcher = GraphSearcher()
        self.rule_base = get_rule_base()
        self.inference = InferenceEngine(self.rule_base, self.graph_searcher)

        self._base = BaseRepo()

    # ═══════════════════════════════════════════════════
    # 实时消息处理
    # ═══════════════════════════════════════════════════

    def on_directed_message(
        self,
        content: str,
        sender: str,
        group_id: str,
    ) -> str:
        """处理一条 @bot 的有向消息。

        流程：
        1. 存储 RawMessage
        2. 收集上下文
        3. LLM 翻译 → {intent, response?, instructions?}
        4. query → 后向链查询规则引擎 → 确定性结果 → LLM 润色
        5. action → 注入前向链 → 收集 ops → 落地执行
        """
        import os

        # 1. 存储有向消息
        msg_id = self.raw_msg.create(
            content=content, sender=sender, group_id=group_id, is_directed=True,
        )
        logger.info(f"[orchestrator] 收到有向消息 {msg_id}: {sender}: {content}")

        # 2. 精简化上下文：区间消息 + 参与者追溯 + 活跃事件列表
        interval_msgs = self.raw_msg.list_since_last_directed(group_id)
        # 提取区间参与发言者
        senders_in_interval = set()
        for m in interval_msgs:
            s = m.get("sender", "")
            if s and s != sender and s != "bot":
                senders_in_interval.add(s)
        senders_in_interval.add(sender)
        # 追溯每个参与者的最近发言
        sender_traces = self.raw_msg.list_by_senders(group_id, list(senders_in_interval), limit_each=12)
        # 组装轻量匹配上下文
        graph_ctx = self.ctx_assembler.assemble_for_event_matching(
            sender, senders_in_interval, sender_traces,
        )
        # 同时保留纯文本上下文给 LLM
        context_texts = [m.get("content", "") for m in interval_msgs[-30:]]

        # 3. LLM 翻译
        result = self.translator.nl_to_fl(content, context_texts, graph_ctx)
        intent = result.get("intent", "query")
        instructions = result.get("instructions")
        response = result.get("response")

        logger.info(f"[orchestrator] LLM 判断意图: {intent}")

        # ── 4. query 路径 ──
        if intent == "query":
            # 尝试从 instructions 中提取结构化查询参数
            if instructions and isinstance(instructions, list) and len(instructions) > 0:
                query_instr = instructions[0]
                if isinstance(query_instr, dict) and "query_type" in query_instr:
                    computed = self._handle_structured_query(query_instr, sender)
                    if computed:
                        # 将确定计算结果给 LLM 润色
                        return self.translator.fl_to_nl({
                            "op": "computed_answer",
                            "params": {"sender": sender, "result": computed},
                        })

            # 纯 LLM 回答
            reply = response or "收到，但我不知道该怎么回复哦 😅"
            logger.info(f"[orchestrator] 查询模式回复: {reply[:80]}...")
            return reply

        # ── 5. action 路径 ──
        if not instructions or not isinstance(instructions, list):
            return "指令已收到，但无法解析具体操作"

        # 创建 FL 记录
        fl_ids = []
        for instr in instructions:
            if not isinstance(instr, dict):
                continue
            op = instr.get("op", "")
            category = (
                FLCategory.EVENT_MANAGEMENT
                if op in ("open_event", "settle_event", "cancel_event")
                else FLCategory.GENERAL_INSTRUCTION
            )
            fl_id = self.fl_repo.create(
                payload=instr,
                source=FLSource.LLM_TRANSLATED,
                category=category,
                parent_message_id=msg_id,
            )
            self.fl_repo.link_to_message(fl_id, msg_id)
            fl_ids.append(fl_id)

        # 逐条执行：注入推理引擎前向链
        immediate_replies = []
        for fl_id, instr in zip(fl_ids, instructions):
            if not isinstance(instr, dict):
                continue
            reply = self._execute_via_inference(instr, fl_id, msg_id)
            if reply:
                immediate_replies.append(reply)

        # 标记 FL 已执行
        for fl_id in fl_ids:
            self.fl_repo.update_status(fl_id, FLStatus.EXECUTED)

        return "\n".join(immediate_replies) if immediate_replies else "指令已执行"

    # ═══════════════════════════════════════════════════
    # 结构化查询处理（query 路径用规则引擎确定计算）
    # ═══════════════════════════════════════════════════

    def _handle_structured_query(self, query_instr: dict, sender: str) -> str | None:
        """用规则引擎后向链处理结构化查询。

        LLM 产出 query_type 如: "debt", "balance", "owes", "owed_by"
        """
        query_type = query_instr.get("query_type", "")
        params = query_instr.get("params", {})

        try:
            if query_type == "debt":
                debtor = params.get("debtor", sender)
                creditor = params.get("creditor")
                event = params.get("event", "")
                debts = self.inference.query_debt(debtor, creditor, event)
                if not debts:
                    return None
                lines = []
                for d in debts:
                    lines.append(f"{d['debtor']} 欠 {d['creditor']}: {d['amount']} 元 (事件: {d['event']})")
                return "\n".join(lines)

            elif query_type == "balance":
                event = params.get("event", "")
                bal = self.inference.query_balance(event)
                if not bal:
                    return None
                return json.dumps(bal, ensure_ascii=False)

            elif query_type == "owes":
                person = params.get("person", sender)
                event = params.get("event", "")
                bindings = self.inference.query(Fact("owes", {
                    "person": person, "event": event,
                    "counterparty": Var("C"), "amount": Var("X"),
                }))
                if not bindings:
                    return None
                lines = []
                for b in bindings:
                    lines.append(
                        f"{person} 欠 {b.get(Var('C'))}: {b.get(Var('X'))} 元"
                    )
                return "\n".join(lines)

            elif query_type == "owed_by":
                person = params.get("person", sender)
                event = params.get("event", "")
                bindings = self.inference.query(Fact("owed_by", {
                    "person": person, "event": event,
                    "counterparty": Var("C"), "amount": Var("X"),
                }))
                if not bindings:
                    return None
                lines = []
                for b in bindings:
                    lines.append(
                        f"{b.get(Var('C'))} 欠 {person}: {b.get(Var('X'))} 元"
                    )
                return "\n".join(lines)

        except Exception as e:
            logger.error(f"[orchestrator] 结构化查询失败: {e}", exc_info=True)

        return None

    # ═══════════════════════════════════════════════════
    # 前向链执行（action 路径）
    # ═══════════════════════════════════════════════════

    def _execute_via_inference(self, fl_payload: dict, fl_id: str, msg_id: str) -> str:
        """用推理引擎执行一条 FL 指令。

        流程：FL → Fact → 前向链 → collect_ops → 落地
        返回：即时回复文本
        """
        op = fl_payload.get("op", "")
        params = fl_payload.get("params", {})

        # 注入推理引擎：将 FL 包装为 Fact，触发前向链
        new_fact = Fact(predicate=op, args=params)
        ops = self.inference.forward_chain(new_fact)

        # 落地所有操作指令，收集即时回复
        immediate_replies = []
        log_id = ""  # action_log 可能是由后续 op 创建的

        for op_dict in ops:
            op_type = op_dict.get("type", "")

            if op_type == "create_dp":
                dp_id = self._exec_create_dp(op_dict, fl_id, msg_id, log_id)
                # 如果有 event_id 且无 log_id，创建一个
                if not log_id and op_dict.get("event_id"):
                    log_id = self.log_repo.create(
                        event_id=op_dict["event_id"],
                        action_summary=f"{op}: {params}",
                    )
                    self.fl_repo.link_triggered_action(fl_id, log_id, op_dict.get("event_id"))

            elif op_type == "link":
                self._exec_link(op_dict)

            elif op_type == "create_event":
                event_id = self.event_repo.create(
                    title=op_dict.get("title", "未命名事件"),
                    created_by=op_dict.get("created_by", "system"),
                    trigger_type=op_dict.get("trigger_type", EventTriggerType.MANUAL),
                    auto_settle_at=op_dict.get("auto_settle_at"),
                )
                logger.info(f"[orchestrator] 创建事件 {event_id}: {op_dict.get('title')}")

            elif op_type == "settle_event":
                eid = op_dict.get("event_id", "")
                if eid:
                    # 生成结算摘要
                    dps = self.dp_repo.get_event_datapoints(eid)
                    dls = self.dp_repo.get_data_lines_for_event(eid)
                    event = self.event_repo.get(eid) or {}
                    summary = self.event_mgr.generate_summary(event, dps, dls)
                    summary_dp_id = self.dp_repo.create(
                        dp_type="settlement_summary",
                        user_name="system",
                        payload=summary,
                        event_id=eid,
                    )
                    self.dp_repo.link_to_event(summary_dp_id, eid)
                    self.event_repo.settle(eid, summary_dp_id)
                    immediate_replies.append(
                        f"📋 事件「{op_dict.get('title', eid)}」已结算！"
                        f"{len(summary['participants'])} 人参与"
                    )

            elif op_type == "cancel_event":
                eid = op_dict.get("event_id", "")
                if eid:
                    self.event_repo.cancel(eid)
                    immediate_replies.append(f"事件「{eid}」已取消")

            elif op_type == "create_fl":
                payload = op_dict.get("payload", {})
                reply_type = op_dict.get("reply_type", "immediate")
                schedule_at = op_dict.get("schedule_at")

                if reply_type == "scheduled":
                    fl_id_created = self.fl_repo.create(
                        payload=payload,
                        source=FLSource.ACTION_GENERATED,
                        category=FLCategory.OUTPUT_SCHEDULED,
                        status=FLStatus.SCHEDULED,
                        parent_log_id=log_id,
                    )
                else:
                    fl_id_created = self.fl_repo.create(
                        payload=payload,
                        source=FLSource.ACTION_GENERATED,
                        category=FLCategory.OUTPUT_IMMEDIATE,
                        status=FLStatus.REPLIED,
                        parent_log_id=log_id,
                    )
                    nl = self.translator.fl_to_nl(payload)
                    if nl:
                        immediate_replies.append(nl)

                if log_id:
                    self.fl_repo.link_generated_by(fl_id_created, log_id)

            elif op_type == "action":
                # 纯计算/副作用 action，已在 ActionHandler 中处理
                sub_op = op_dict.get("op", "")
                sub_params = op_dict.get("params", {})
                logger.info(f"[orchestrator] action 子操作: {sub_op}")
                # 处理可能由 action 产出的隐式 dp 创建
                #（已在 ActionHandler 中作为 ops 追加，此处跳过）

        return "\n".join(immediate_replies) if immediate_replies else ""

    def _exec_create_dp(self, op_dict: dict, fl_id: str, msg_id: str, log_id: str) -> str:
        """落地一个 create_dp 操作。"""
        dp_type = op_dict.get("dp_type", "unknown")
        user_name = op_dict.get("user_name", "system")
        payload = op_dict.get("payload", {})
        event_id = op_dict.get("event_id")
        dp_id = op_dict.get("dp_id")

        created_id = self.dp_repo.create(
            dp_type=dp_type,
            user_name=user_name,
            payload=payload,
            event_id=event_id,
            dp_id=dp_id,
        )

        # 关联到事件
        if event_id:
            self.dp_repo.link_to_event(created_id, event_id)

        # 关联到 ActionLog
        if log_id:
            self.dp_repo.link_produced(log_id, created_id)

        logger.info(f"[orchestrator] 创建数据点 {created_id}: {dp_type}")
        return created_id

    def _exec_link(self, op_dict: dict) -> None:
        """落地一个 link 操作。"""
        rel_type = op_dict.get("rel_type", "")
        from_id = op_dict.get("from_id", "")
        to_id = op_dict.get("to_id", "")

        if not from_id or not to_id:
            return

        if rel_type == "BELONGS_TO":
            self.dp_repo.link_to_event(from_id, to_id)
        elif rel_type == "DATA_LINE":
            self.dp_repo.link_data_line(from_id, to_id, "", op_dict.get("event_id"))
        else:
            logger.warning(f"[orchestrator] 未知关系类型: {rel_type}")

    # ═══════════════════════════════════════════════════
    # 定时任务
    # ═══════════════════════════════════════════════════

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
        return messages

    def check_auto_settle(self) -> list[str]:
        """通过推理引擎前向链检查到期的自动结算。"""
        ops = self.inference.forward_chain()  # 触发 timed 规则
        messages = []

        for op_dict in ops:
            if op_dict.get("type") == "settle_event":
                eid = op_dict.get("event_id", "")
                if eid:
                    try:
                        dps = self.dp_repo.get_event_datapoints(eid)
                        dls = self.dp_repo.get_data_lines_for_event(eid)
                        event = self.event_repo.get(eid) or {}
                        summary = self.event_mgr.generate_summary(event, dps, dls)
                        summary_dp_id = self.dp_repo.create(
                            dp_type="settlement_summary",
                            user_name="system",
                            payload=summary,
                            event_id=eid,
                        )
                        self.dp_repo.link_to_event(summary_dp_id, eid)
                        self.event_repo.settle(eid, summary_dp_id)
                        messages.append(
                            f"📋 事件「{event.get('title', eid)}」已自动结算！"
                            f"共 {summary['total_datapoints']} 个数据点"
                        )
                    except Exception as e:
                        logger.error(f"[orchestrator] 自动结算失败: {e}", exc_info=True)

            elif op_dict.get("type") == "create_fl":
                payload = op_dict.get("payload", {})
                reply_type = op_dict.get("reply_type", "immediate")
                schedule_at = op_dict.get("schedule_at")

                if reply_type == "immediate":
                    nl = self.translator.fl_to_nl(payload)
                    if nl:
                        messages.append(nl)

        return messages