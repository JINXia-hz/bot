"""行动执行器 - 将格式语言指令转化为数据点、数据线、日志。

核心公式：dpⁿ（输入数据点） + fl（格式语言） → dpᵐ（输出数据点） + dlʷ（数据线） + log（日志）

引擎是纯计算层，不直接碰 LLM API。
数据持久化通过 repo 层完成，引擎负责决策"该产出什么"。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionResult:
    """一次行动的结果。

    由 Executor.execute() 返回，pipeline 层负责将结果写入图数据库。
    """
    success: bool
    message: str
    action_summary: str = ""

    # 输入
    input_dp_ids: list[str] = field(default_factory=list)
    input_fl_ids: list[str] = field(default_factory=list)

    # 输出数据点（待创建）
    output_datapoints: list[dict] = field(default_factory=list)
    # 每个元素: {dp_type, user_name, payload, event_id?, dp_id?}

    # 输出格式语言（行动内部产出的 FL）
    output_fls: list[dict] = field(default_factory=list)
    # 每个元素: {payload, category, reply_type ("immediate"|"scheduled"), schedule_at?}

    # 数据线：[(from_dp_id, to_dp_id)]
    data_lines: list[tuple[str, str]] = field(default_factory=list)

    # 事件操作
    opened_event: dict | None = None     # {title, created_by, trigger_type?, auto_settle_at?}
    settled_event_id: str | None = None
    cancelled_event_id: str | None = None
    used_event_id: str | None = None     # 此行动所属的事件 ID

    # 消耗的数据点 ID（此行动"读取"了哪些旧数据点）
    consumed_dp_ids: list[str] = field(default_factory=list)


class Executor:
    """格式语言指令执行器。

    纯函数设计：不依赖任何外部服务，只做数据变换。
    结果通过 ActionResult 返回，由 pipeline 层落地。
    """

    # ── 主入口 ────────────────────────────────────

    def execute(
        self,
        fl_payload: dict,
        input_datapoints: dict[str, dict] | None = None,
        event_id: str | None = None,
    ) -> ActionResult:
        """执行一条格式语言指令。

        Args:
            fl_payload: 格式语言的 payload，包含 op 和 params
            input_datapoints: 已有的数据点映射 {dp_id: dict}（用于查找相关状态）
            event_id: 当前所属事件 ID

        Returns:
            ActionResult 包含所有待持久化的变更
        """
        op = fl_payload.get("op", "")
        params = fl_payload.get("params", {})

        handler = _DISPATCH.get(op)
        if handler is None:
            return ActionResult(
                success=False,
                message=f"不支持的操作类型: {op}",
            )

        result = handler(self, params, input_datapoints or {}, event_id)
        result.action_summary = f"{op}: {params}"
        return result

    def execute_batch(
        self,
        fl_payloads: list[dict],
        input_datapoints: dict[str, dict] | None = None,
        event_id: str | None = None,
    ) -> list[ActionResult]:
        """批量执行多条格式语言指令。"""
        results = []
        for fl in fl_payloads:
            result = self.execute(fl, input_datapoints, event_id)
            results.append(result)
        return results

    # ── 事件管理操作 ──────────────────────────────

    def _open_event(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        title = params.get("title", "未命名事件")
        created_by = params.get("user_name", "unknown")
        trigger_type = params.get("trigger_type", "manual")
        auto_settle_at = params.get("auto_settle_at")

        return ActionResult(
            success=True,
            message=f'事件「{title}」已开启',
            opened_event={
                "title": title,
                "created_by": created_by,
                "trigger_type": trigger_type,
                "auto_settle_at": auto_settle_at,
            },
        )

    def _settle_event(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        title = params.get("title", "")
        event_id_to_settle = event_id or ""

        # 生成统合数据点
        summary_dp = {
            "dp_type": "settlement_summary",
            "user_name": params.get("user_name", "system"),
            "payload": {
                "type": "event_settlement",
                "event_title": title,
                "settled_by": params.get("user_name", "system"),
            },
        }

        return ActionResult(
            success=True,
            message=f'事件「{title}」已结算',
            settled_event_id=event_id_to_settle,
            output_datapoints=[summary_dp],
        )

    def _cancel_event(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        return ActionResult(
            success=True,
            message=f"事件已取消",
            cancelled_event_id=event_id or "",
        )

    # ── 一般指令操作 ──────────────────────────────

    def _record_expense(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        user_name = params.get("user_name", "unknown")
        amount = float(params.get("amount", 0))
        category = params.get("category", "其他")
        note = params.get("note", "")

        dp = {
            "dp_type": "expense",
            "user_name": user_name,
            "payload": {
                "amount": amount,
                "category": category,
                "note": note,
            },
            "event_id": event_id,
        }

        return ActionResult(
            success=True,
            message=f"{user_name} 支出 {amount:.2f} 元（{category}: {note}）",
            output_datapoints=[dp],
        )

    def _record_income(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        user_name = params.get("user_name", "unknown")
        amount = float(params.get("amount", 0))
        note = params.get("note", "")

        dp = {
            "dp_type": "income",
            "user_name": user_name,
            "payload": {
                "amount": amount,
                "note": note,
            },
            "event_id": event_id,
        }

        return ActionResult(
            success=True,
            message=f"{user_name} 收入 {amount:.2f} 元（{note}）",
            output_datapoints=[dp],
        )

    def _add_reservation(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        user_name = params.get("user_name", "unknown")
        title = params.get("title", "未命名预定")
        content = params.get("content", "")
        time = params.get("time", "")
        people = params.get("people", [])

        dp = {
            "dp_type": "reservation",
            "user_name": user_name,
            "payload": {
                "title": title,
                "content": content,
                "time": time,
                "people": people,
            },
            "event_id": event_id,
        }

        # 产出即时回复 + 定时提醒
        immediate_fl = {
            "payload": {
                "op": "reservation_confirmed",
                "params": {"user_name": user_name, "title": title, "time": time},
            },
            "reply_type": "immediate",
        }
        scheduled_fl = {
            "payload": {
                "op": "reservation_reminder",
                "params": {
                    "user_name": user_name,
                    "content": f"预定提醒：{title} - {content}",
                    "related_people": people,
                },
            },
            "reply_type": "scheduled",
            "schedule_at": time,
        }

        return ActionResult(
            success=True,
            message=f"{user_name} 预定了「{title}」",
            output_datapoints=[dp],
            output_fls=[immediate_fl, scheduled_fl],
        )

    def _split_bill(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        user_name = params.get("user_name", "unknown")
        title = params.get("title", "未命名账单")
        total = float(params.get("total", 0))
        people = params.get("people", [])

        if len(people) < 2:
            return ActionResult(success=False, message="AA 至少需要 2 人参与")

        per_person = round(total / len(people), 2)

        # 为每个参与者创建一个待付数据点
        output_dps = []
        for person in people:
            output_dps.append({
                "dp_type": "bill_item",
                "user_name": person,
                "payload": {
                    "bill_title": title,
                    "total": total,
                    "per_person": per_person,
                    "status": "pending",
                },
                "event_id": event_id,
            })

        return ActionResult(
            success=True,
            message=f"AA 账单「{title}」已创建：{total:.2f} 元，{len(people)}人，每人 {per_person:.2f}",
            output_datapoints=output_dps,
        )

    def _pay_bill(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        user_name = params.get("user_name", "unknown")
        title = params.get("title", "")

        # 在已有数据点中查找该账单的待付项
        consumed_dp_ids = []
        settled_dp_id = None

        for dp_id, dp in dps.items():
            pl = dp.get("payload", {})
            if isinstance(pl, str):
                import json
                pl = json.loads(pl)
            if (dp.get("dp_type") == "bill_item"
                    and pl.get("bill_title") == title
                    and pl.get("status") == "pending"
                    and dp.get("user_name") == user_name):
                consumed_dp_ids.append(dp_id)
                settled_dp_id = dp_id

        if not consumed_dp_ids:
            return ActionResult(success=False, message=f"未找到 {user_name} 的待付账单「{title}」")

        # 产出已付款的数据点
        dp = {
            "dp_type": "bill_item",
            "user_name": user_name,
            "payload": {
                "bill_title": title,
                "status": "paid",
            },
            "event_id": event_id,
        }

        # 数据线：待付 → 已付
        data_lines = []
        if settled_dp_id:
            data_lines.append((settled_dp_id, dp.get("dp_id", "")))

        return ActionResult(
            success=True,
            message=f"{user_name} 已支付「{title}」",
            output_datapoints=[dp],
            consumed_dp_ids=consumed_dp_ids,
            data_lines=data_lines,
        )

    def _set_reminder(self, params: dict, dps: dict, event_id: str | None) -> ActionResult:
        user_name = params.get("user_name", "unknown")
        content = params.get("content", "")
        remind_at = params.get("remind_at", "")

        dp = {
            "dp_type": "reminder",
            "user_name": user_name,
            "payload": {
                "content": content,
                "remind_at": remind_at,
                "status": "pending",
            },
            "event_id": event_id,
        }

        # 产出定时回复 FL
        output_fl = {
            "payload": {
                "op": "reminder_notify",
                "params": {
                    "user_name": user_name,
                    "content": content,
                },
            },
            "reply_type": "scheduled",
            "schedule_at": remind_at,
        }

        return ActionResult(
            success=True,
            message=f"已为 {user_name} 设置提醒：{content}（{remind_at}）",
            output_datapoints=[dp],
            output_fls=[output_fl],
        )


# ── 操作分发表 ────────────────────────────────────

_DISPATCH = {
    # 事件管理
    "open_event": Executor._open_event,
    "settle_event": Executor._settle_event,
    "cancel_event": Executor._cancel_event,
    # 一般指令
    "record_expense": Executor._record_expense,
    "record_income": Executor._record_income,
    "add_reservation": Executor._add_reservation,
    "split_bill": Executor._split_bill,
    "pay_bill": Executor._pay_bill,
    "set_reminder": Executor._set_reminder,
}


def get_supported_ops() -> list[str]:
    """返回所有支持的操作类型。"""
    return list(_DISPATCH.keys())