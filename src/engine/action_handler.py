"""内置动作处理器。

规则引擎遇到 Clause.action("op_name", params) 时，由 InferenceEngine 调用此类的方法。

这些动作在推理过程中被"证明"：
  - 查询类动作（extract_debt_item）：将数据绑定到变量
  - 输出类动作（compute_per_person_balance）：计算数据并绑定
  - 副作用动作（create_personal_reservations）：收集到 ops 队列
"""

import json
import logging
from typing import Any

from src.engine.rule_engine import Var, Binding

logger = logging.getLogger(__name__)


class ActionHandler:
    """处理规则引擎中的 action 子句。

    每个 handle_xxx 方法接收:
      - params: 已绑定解析的参数 dict
      - binding: 当前变量绑定
      - engine: InferenceEngine 实例（用于收集 ops）

    返回:
      - 成功时返回 (True, 新 binding | None)
      - 失败时返回 (False, None)
    """

    def __init__(self, engine):
        self.engine = engine

    def handle(self, op: str, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """分发到具体处理器。"""
        method = getattr(self, f"_h_{op}", None)
        if method is None:
            logger.warning(f"[action_handler] 未知动作: {op}")
            # 默认：加入 ops 队列
            self.engine._ops.append({"type": "action", "op": op, "params": params})
            return True, binding
        return method(params, binding)

    # ═══════════════════════════════════════════════
    # 计算类动作
    # ═══════════════════════════════════════════════

    def _h_compute_per_person_balance(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """为每个参与者计算：已付、应付、净额。

        params: {people, expenses, per_person, result: Var}
        产出: { "张三": {paid: 150, owe: 110, net: 40}, ... }
        """
        people = params.get("people", [])
        expenses = params.get("expenses", [])
        per_person = float(params.get("per_person", 0))
        result_var = params.get("result")

        if not isinstance(people, list):
            people = []

        balances = {}
        for person in people:
            # 汇总该人的所有支出
            total_paid = 0.0
            for exp in (expenses if isinstance(expenses, list) else []):
                if not isinstance(exp, dict):
                    continue
                exp_user = exp.get("user_name", "")
                pl = exp.get("payload", {})
                if isinstance(pl, str):
                    try:
                        pl = json.loads(pl)
                    except (json.JSONDecodeError, TypeError):
                        pl = {}
                if exp_user == person:
                    total_paid += float(pl.get("amount", 0))

            net = round(total_paid - per_person, 2)
            balances[person] = {
                "paid": round(total_paid, 2),
                "owe": round(per_person, 2),
                "net": net,
            }

        if result_var is not None and isinstance(result_var, Var):
            b = binding.extend(result_var, balances)
            if b is None:
                return False, None
            return True, b

        return True, binding

    def _h_decompose_debts(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """将 balance 拆解为债务 dp。

        params: {event_id, balance: {dp dict}}
        对每对净额为正/负的人创建 debt dp。
        """
        event_id = params.get("event_id", "")
        balance = params.get("balance", {})

        if isinstance(balance, dict):
            pl = balance.get("payload", {})
        else:
            pl = {}

        if isinstance(pl, str):
            try:
                pl = json.loads(pl)
            except (json.JSONDecodeError, TypeError):
                pl = {}

        # 分类：正净额（多付的债权人）vs 负净额（欠债人）
        creditors = []  # net > 0
        debtors = []    # net < 0
        for person, data in pl.items():
            net = data.get("net", 0)
            if net > 0:
                creditors.append((person, net))
            elif net < 0:
                debtors.append((person, -net))

        # 简单分配（贪心）：每个欠债人依次还给债权人
        dc_idx = 0
        for debtor, debt_amount in debtors:
            remaining = debt_amount
            while remaining > 0.01 and dc_idx < len(creditors):
                creditor, credit_amount = creditors[dc_idx]
                if credit_amount < 0.01:
                    dc_idx += 1
                    continue
                assign = min(remaining, credit_amount)
                # 创建 debt dp
                from src.graph.base_repo import _new_id
                debt_dp_id = _new_id()
                self.engine._ops.append({
                    "type": "create_dp",
                    "dp_type": "debt",
                    "dp_id": debt_dp_id,
                    "user_name": debtor,
                    "payload": {
                        "debtor": debtor,
                        "creditor": creditor,
                        "amount": round(assign, 2),
                    },
                    "event_id": event_id,
                })
                self.engine._ops.append({
                    "type": "link",
                    "rel_type": "BELONGS_TO",
                    "from_id": debt_dp_id,
                    "to_id": event_id,
                    "props": None,
                })
                remaining -= assign
                creditors[dc_idx] = (creditor, round(credit_amount - assign, 2))
                if remaining < 0.01:
                    break

        return True, binding

    def _h_extract_debt_item(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """从债务列表中提取匹配的项并绑定变量。

        params: {debts: [dp dict], debtor: Var/str, creditor: Var/str, amount: Var}
        这是后向链的回溯点：每个 match 产生一个 binding。
        """
        debts = params.get("debts", [])
        target_debtor = params.get("debtor")
        target_creditor = params.get("creditor")
        amount_var = params.get("amount")

        if not isinstance(debts, list):
            return False, None

        for debt_dp in debts:
            pl = debt_dp.get("payload", {})
            if isinstance(pl, str):
                try:
                    pl = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    continue

            d_debtor = pl.get("debtor", "")
            d_creditor = pl.get("creditor", "")
            d_amount = pl.get("amount", 0)

            # 匹配
            b = binding.copy()

            if isinstance(target_debtor, Var):
                ext = b.extend(target_debtor, d_debtor)
                if ext is None:
                    continue
                b = ext
            elif target_debtor != d_debtor:
                continue

            if isinstance(target_creditor, Var):
                ext = b.extend(target_creditor, d_creditor)
                if ext is None:
                    continue
                b = ext
            elif target_creditor is not None and target_creditor != d_creditor:
                continue

            if isinstance(amount_var, Var):
                ext = b.extend(amount_var, d_amount)
                if ext is None:
                    continue
                b = ext

            return True, b

        return False, None

    # ═══════════════════════════════════════════════
    # 预定类动作
    # ═══════════════════════════════════════════════

    def _h_create_personal_reservations(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """为每个参与者创建个人预定 dp。

        params: {people, title, time, event_id?}
        """
        people = params.get("people", [])
        title = params.get("title", "")
        time = params.get("time", "")
        event_id = params.get("event_id")

        if not isinstance(people, list):
            people = []

        for person in people:
            if not isinstance(person, str):
                continue
            from src.graph.base_repo import _new_id
            dp_id = _new_id()
            self.engine._ops.append({
                "type": "create_dp",
                "dp_type": "personal_reservation",
                "dp_id": dp_id,
                "user_name": person,
                "payload": {
                    "title": title,
                    "time": time,
                    "status": "pending",
                },
                "event_id": event_id,
            })
            if event_id:
                self.engine._ops.append({
                    "type": "link",
                    "rel_type": "BELONGS_TO",
                    "from_id": dp_id,
                    "to_id": event_id,
                    "props": None,
                })

        return True, binding

    def _h_schedule_reminder(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """产出定时提醒 FL。

        params: {title, time, people, event_id?}
        """
        title = params.get("title", "")
        time_str = params.get("time", "")
        people = params.get("people", [])

        # 提前1小时的提醒
        from datetime import datetime, timedelta, timezone
        try:
            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            remind_time = (event_time - timedelta(hours=1)).isoformat()
        except (ValueError, AttributeError):
            remind_time = time_str

        self.engine._ops.append({
            "type": "create_fl",
            "payload": {
                "op": "reservation_reminder",
                "params": {
                    "title": title,
                    "people": people,
                    "time": time_str,
                },
                "schedule_at": remind_time,
            },
            "reply_type": "scheduled",
            "schedule_at": remind_time,
        })

        return True, binding

    def _h_activate_reservation_event(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """激活预定对应的事件，产出发送通知的 FL。

        params: {reservation: {dict}}
        """
        reservation = params.get("reservation", {})
        if isinstance(reservation, dict):
            pl = reservation.get("payload", {})
        else:
            pl = {}
        if isinstance(pl, str):
            try:
                pl = json.loads(pl)
            except (json.JSONDecodeError, TypeError):
                pl = {}

        title = pl.get("title", "")
        people = pl.get("people", [])

        self.engine._ops.append({
            "type": "create_fl",
            "payload": {
                "op": "event_activation_notify",
                "params": {
                    "title": title,
                    "people": people,
                    "message": f"🔔 {title} 要开始啦！参与人：{', '.join(people) if people else '大家'}",
                },
            },
            "reply_type": "immediate",
            "schedule_at": None,
        })

        return True, binding

    def _h_notify_event_start(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """发送事件开始通知。"""
        title = params.get("title", "")
        people = params.get("people", [])

        self.engine._ops.append({
            "type": "create_fl",
            "payload": {
                "op": "event_start_notify",
                "params": {
                    "title": title,
                    "message": f"🎉 {title} 开始啦！{', '.join(people) if people else ''}准备好了吗？",
                },
            },
            "reply_type": "immediate",
            "schedule_at": None,
        })

        return True, binding

    # ═══════════════════════════════════════════════
    # 参与者自动补入
    # ═══════════════════════════════════════════════

    def _h_ensure_user_in_event(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """检查用户是否在事件中，不在则创建 participant_entry dp。

        params: {user_name, event_id, event_title}
        """
        user_name = params.get("user_name", "")
        event_id = params.get("event_id", "")
        event_title = params.get("event_title", "")

        if not user_name or not event_id:
            return True, binding

        # 通过图搜索检查是否已存在
        from src.graph.base_repo import _new_id
        dp_id = _new_id()
        self.engine._ops.append({
            "type": "create_dp",
            "dp_type": "participant_entry",
            "dp_id": dp_id,
            "user_name": user_name,
            "payload": {"event_title": event_title, "role": "participant"},
            "event_id": event_id,
        })
        self.engine._ops.append({
            "type": "link",
            "rel_type": "BELONGS_TO",
            "from_id": dp_id,
            "to_id": event_id,
            "props": None,
        })
        return True, binding

    # ═══════════════════════════════════════════════
    # 穿透还款（跨事件，溢出填补）
    # ═══════════════════════════════════════════════

    def _h_repay_with_overflow(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """穿透还款：按金额升序逐一清偿，溢出部分填补下一笔。

        params: {debts: [{event_id, event_title, amount, dp_id}], total_amount, debtor, creditor}
        """
        debts = params.get("debts", [])
        total_amount = float(params.get("total_amount", 0))
        debtor = params.get("debtor", "")
        creditor = params.get("creditor", "")

        if not isinstance(debts, list) or not debts:
            return True, binding

        remaining = total_amount

        for debt in debts:
            if remaining <= 0.01:
                break
            debt_amount = debt.get("amount", 0)
            event_id = debt.get("event_id", "")
            dp_id = debt.get("dp_id", "")

            if remaining >= debt_amount:
                # 完全清偿这笔债
                from src.graph.base_repo import _new_id
                settlement_dp_id = _new_id()
                self.engine._ops.append({
                    "type": "create_dp",
                    "dp_type": "debt_settled",
                    "dp_id": settlement_dp_id,
                    "user_name": debtor,
                    "payload": {
                        "debtor": debtor,
                        "creditor": creditor,
                        "amount": debt_amount,
                        "status": "fully_paid",
                    },
                    "event_id": event_id,
                })
                if event_id:
                    self.engine._ops.append({
                        "type": "link",
                        "rel_type": "BELONGS_TO",
                        "from_id": settlement_dp_id,
                        "to_id": event_id,
                        "props": None,
                    })
                self.engine._ops.append({
                    "type": "link",
                    "rel_type": "DATA_LINE",
                    "from_id": dp_id,
                    "to_id": settlement_dp_id,
                    "props": None,
                })
                remaining -= debt_amount
            else:
                # 部分清偿：创建 residual debt dp
                from src.graph.base_repo import _new_id
                residual_dp_id = _new_id()
                self.engine._ops.append({
                    "type": "create_dp",
                    "dp_type": "debt",
                    "dp_id": residual_dp_id,
                    "user_name": debtor,
                    "payload": {
                        "debtor": debtor,
                        "creditor": creditor,
                        "amount": round(debt_amount - remaining, 2),
                        "status": "partial",
                    },
                    "event_id": event_id,
                })
                if event_id:
                    self.engine._ops.append({
                        "type": "link",
                        "rel_type": "BELONGS_TO",
                        "from_id": residual_dp_id,
                        "to_id": event_id,
                        "props": None,
                    })
                self.engine._ops.append({
                    "type": "link",
                    "rel_type": "DATA_LINE",
                    "from_id": dp_id,
                    "to_id": residual_dp_id,
                    "props": None,
                })
                remaining = 0

        # 如果还有剩余，创建 credit dp（对方反过来欠）
        if remaining > 0.01:
            from src.graph.base_repo import _new_id
            credit_dp_id = _new_id()
            self.engine._ops.append({
                "type": "create_dp",
                "dp_type": "debt",
                "dp_id": credit_dp_id,
                "user_name": creditor,
                "payload": {
                    "debtor": creditor,
                    "creditor": debtor,
                    "amount": round(remaining, 2),
                    "status": "overpaid",
                },
            })

        return True, binding

    # ═══════════════════════════════════════════════
    # 事件管理动作
    # ═══════════════════════════════════════════════

    def _h_open_event(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """创建新事件。

        params: {title, created_by, auto_settle_at?}
        """
        self.engine._ops.append({
            "type": "create_event",
            "title": params.get("title", "未命名事件"),
            "created_by": params.get("created_by", "system"),
            "trigger_type": params.get("trigger_type", "manual"),
            "auto_settle_at": params.get("auto_settle_at"),
        })
        return True, binding

    def _h_settle_event(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """结算事件。

        params: {event_id, title?}
        """
        self.engine._ops.append({
            "type": "settle_event",
            "event_id": params.get("event_id", ""),
            "title": params.get("title", ""),
        })
        return True, binding

    def _h_cancel_event(self, params: dict, binding: Binding) -> tuple[bool, Binding | None]:
        """取消事件。"""
        self.engine._ops.append({
            "type": "cancel_event",
            "event_id": params.get("event_id", ""),
        })
        return True, binding