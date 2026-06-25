"""图搜索引擎 - 将 kuzu 图数据库暴露为规则引擎可调用的命名查询。

所有方法接收绑定后的参数，返回 dict/list。
规则引擎通过 Clause.graph(query_name, params, result_var) 调用这些查询。

这是纯数据检索层，不包含任何业务规则。
"""

import json
from typing import Any
from kuzu import Connection

from src.graph.connection import get_connection
from src.graph.base_repo import _now_iso


class GraphSearcher:
    """图搜索引擎。

    为规则引擎提供事实数据。每个查询方法对应一个 query_name，
    被推理引擎的 FactProvider 调用。
    """

    def __init__(self, conn: Connection | None = None):
        self.conn = conn or get_connection()

    # ── 查询分发 ─────────────────────────────────

    def execute(self, query_name: str, params: dict[str, Any]) -> Any:
        """分发到具体的查询方法。

        Returns:
            查询结果（任何可 JSON 序列化的类型），
            或 None 表示无结果（后向链回溯时会跳过此分支）。
        """
        method = getattr(self, f"_q_{query_name}", None)
        if method is None:
            raise ValueError(f"未知图查询: {query_name}")
        return method(params)

    # ═══════════════════════════════════════════════
    # 事件类查询
    # ═══════════════════════════════════════════════

    def _q_find_event_by_title(self, params: dict) -> dict | None:
        """按标题模糊搜索事件。

        query_name: find_event_by_title
        params: {title: str}
        returns: {id, title, status, created_by, ...} 或 None
        """
        title = params.get("title", "")
        if not title:
            return None
        result = self.conn.execute("""
            MATCH (e:Event)
            WHERE e.title = $title
            RETURN e
            LIMIT 1
        """, {"title": title})
        if result.has_next():
            node = dict(result.get_next()[0])
            return node
        return None

    def _q_find_event_like(self, params: dict) -> dict | None:
        """按标题模糊搜索事件（使用 CONTAINS）。

        query_name: find_event_like
        params: {title: str}
        returns: {id, title, status, ...} 或 None
        """
        title = params.get("title", "")
        if not title:
            return None
        result = self.conn.execute("""
            MATCH (e:Event)
            WHERE e.title CONTAINS $title AND e.status = 'active'
            RETURN e
            ORDER BY e.created_at DESC
            LIMIT 1
        """, {"title": title})
        if result.has_next():
            node = dict(result.get_next()[0])
            return node
        return None

    def _q_active_events(self, params: dict) -> list[dict]:
        """查询所有活跃事件。

        query_name: active_events
        returns: [{id, title, status, created_by, ...}]
        """
        result = self.conn.execute("""
            MATCH (e:Event {status: 'active'})
            RETURN e
            ORDER BY e.created_at ASC
        """)
        return [dict(row[0]) for row in result] if result.has_next() else []

    def _q_user_active_events(self, params: dict) -> list[dict]:
        """查询某用户参与的活跃事件。

        query_name: user_active_events
        params: {user_name: str}
        returns: [{id, title, status, ...}]
        """
        user = params.get("user_name", "")
        if not user:
            return []
        result = self.conn.execute("""
            MATCH (e:Event {status: 'active'})
            MATCH (dp:DataPoint)-[:BELONGS_TO]->(e)
            WHERE dp.user_name = $user
            RETURN DISTINCT e
            ORDER BY e.created_at ASC
        """, {"user": user})
        items = []
        while result.has_next():
            items.append(dict(result.get_next()[0]))
        return items

    def _q_event_due_for_settle(self, params: dict) -> list[dict]:
        """查询所有到期的活跃事件（auto_settle_at <= now）。

        query_name: event_due_for_settle
        returns: [{id, title, auto_settle_at, ...}]
        """
        now = _now_iso()
        result = self.conn.execute("""
            MATCH (e:Event {status: 'active'})
            WHERE e.auto_settle_at <> '' AND e.auto_settle_at <= $now
            RETURN e
        """, {"now": now})
        items = []
        while result.has_next():
            items.append(dict(result.get_next()[0]))
        return items

    # ═══════════════════════════════════════════════
    # DataPoint 类查询
    # ═══════════════════════════════════════════════

    def _q_event_datapoints(self, params: dict) -> list[dict]:
        """查询某事件下所有数据点。

        query_name: event_datapoints
        params: {event_id: str}
        returns: [{id, dp_type, user_name, payload, ...}]
        """
        eid = params.get("event_id", "")
        if not eid:
            return []
        result = self.conn.execute("""
            MATCH (dp:DataPoint)-[:BELONGS_TO]->(e:Event {id: $eid})
            RETURN dp
            ORDER BY dp.created_at ASC
        """, {"eid": eid})
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    node["payload"] = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    node["payload"] = {}
            items.append(node)
        return items

    def _q_event_expenses(self, params: dict) -> list[dict]:
        """查询某事件下所有支出数据点。

        query_name: event_expenses
        params: {event_id: str}
        returns: [{id, user_name, payload: {amount, category, note}, ...}]
        """
        eid = params.get("event_id", "")
        if not eid:
            return []
        result = self.conn.execute("""
            MATCH (dp:DataPoint {dp_type: 'expense'})-[:BELONGS_TO]->(e:Event {id: $eid})
            RETURN dp
            ORDER BY dp.created_at ASC
        """, {"eid": eid})
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    node["payload"] = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    node["payload"] = {}
            items.append(node)
        return items

    def _q_event_participants(self, params: dict) -> list[str]:
        """查询某事件的所有参与者（去重）。

        query_name: event_participants
        params: {event_id: str}
        returns: ["张三", "李四", ...]
        """
        eid = params.get("event_id", "")
        if not eid:
            return []
        result = self.conn.execute("""
            MATCH (dp:DataPoint)-[:BELONGS_TO]->(e:Event {id: $eid})
            RETURN DISTINCT dp.user_name
        """, {"eid": eid})
        users = []
        while result.has_next():
            users.append(str(result.get_next()[0]))
        return users

    def _q_user_datapoints(self, params: dict) -> list[dict]:
        """查询某用户的所有数据点（最近 N 条）。

        query_name: user_datapoints
        params: {user_name: str, limit: int}
        returns: [{id, dp_type, payload, ...}]
        """
        user = params.get("user_name", "")
        limit = int(params.get("limit", 20))
        if not user:
            return []
        result = self.conn.execute("""
            MATCH (dp:DataPoint {user_name: $user})
            RETURN dp
            ORDER BY dp.created_at DESC
            LIMIT $limit
        """, {"user": user, "limit": limit})
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    node["payload"] = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    node["payload"] = {}
            items.append(node)
        return list(reversed(items))

    def _q_user_latest_dp(self, params: dict) -> dict | None:
        """查询某用户的最新数据点。

        query_name: user_latest_dp
        params: {user_name: str}
        returns: {id, dp_type, payload, ...} 或 None
        """
        items = self._q_user_datapoints({"user_name": params.get("user_name", ""), "limit": 1})
        return items[0] if items else None

    def _q_user_data_line_chain(self, params: dict) -> list[dict]:
        """查询某用户的数据线因果链。

        query_name: user_data_line_chain
        params: {user_name: str, limit: int}
        returns: [{from_id, to_id, from_type, to_type, ...}]
        """
        user = params.get("user_name", "")
        limit = int(params.get("limit", 30))
        if not user:
            return []
        result = self.conn.execute("""
            MATCH (a:DataPoint {user_name: $user})-[dl:DATA_LINE]->(b:DataPoint)
            RETURN a.id, a.dp_type, b.id, b.dp_type, dl.created_at, dl.action_log_id
            ORDER BY dl.created_at DESC
            LIMIT $limit
        """, {"user": user, "limit": limit})
        lines = []
        while result.has_next():
            row = result.get_next()
            lines.append({
                "from_id": row[0], "from_type": row[1],
                "to_id": row[2], "to_type": row[3],
                "created_at": row[4], "action_log_id": row[5],
            })
        return lines

    def _q_event_data_lines(self, params: dict) -> list[dict]:
        """查询某事件下所有数据线。

        query_name: event_data_lines
        params: {event_id: str}
        """
        eid = params.get("event_id", "")
        if not eid:
            return []
        result = self.conn.execute("""
            MATCH (a:DataPoint)-[dl:DATA_LINE]->(b:DataPoint)
            WHERE dl.event_id = $eid
            RETURN a.id, a.dp_type, a.user_name, b.id, b.dp_type, b.user_name,
                   dl.action_log_id, dl.created_at
            ORDER BY dl.created_at ASC
        """, {"eid": eid})
        lines = []
        while result.has_next():
            r = result.get_next()
            lines.append({
                "from_id": r[0], "from_type": r[1], "from_user": r[2],
                "to_id": r[3], "to_type": r[4], "to_user": r[5],
                "action_log_id": r[6], "created_at": r[7],
            })
        return lines

    # ═══════════════════════════════════════════════
    # 预定类查询
    # ═══════════════════════════════════════════════

    def _q_pending_reservations(self, params: dict) -> list[dict]:
        """查询所有待处理的预定数据点。

        query_name: pending_reservations
        returns: [{id, user_name, payload: {title, time, people, ...}, ...}]
        """
        now = _now_iso()
        result = self.conn.execute("""
            MATCH (dp:DataPoint {dp_type: 'reservation'})
            RETURN dp
            ORDER BY dp.created_at ASC
        """)
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    node["payload"] = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    node["payload"] = {}
            # 只返回时间已到但未处理的预定
            res_time = node["payload"].get("time", "")
            if res_time and res_time <= now:
                items.append(node)
        return items

    def _q_reservation_due(self, params: dict) -> list[dict]:
        """查询到期的预定（同 pending_reservations，别名）。"""
        return self._q_pending_reservations(params)

    # ═══════════════════════════════════════════════
    # 余额/债务类查询
    # ═══════════════════════════════════════════════

    def _q_latest_balance_in_event(self, params: dict) -> dict | None:
        """查询某事件下最新的 balance 数据点。

        query_name: latest_balance_in_event
        params: {event_id: str}
        returns: {id, payload: {person: {paid, owe, net}, ...}, ...} 或 None
        """
        eid = params.get("event_id", "")
        if not eid:
            return None
        result = self.conn.execute("""
            MATCH (dp:DataPoint {dp_type: 'balance'})-[:BELONGS_TO]->(e:Event {id: $eid})
            RETURN dp
            ORDER BY dp.created_at DESC
            LIMIT 1
        """, {"eid": eid})
        if result.has_next():
            node = dict(result.get_next()[0])
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    node["payload"] = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    node["payload"] = {}
            return node
        return None

    def _q_debt_dps_for_event(self, params: dict) -> list[dict]:
        """查询某事件下的所有债务数据点。

        query_name: debt_dps_for_event
        params: {event_id: str}
        returns: [{id, user_name, payload: {debtor, creditor, amount}, ...}]
        """
        eid = params.get("event_id", "")
        if not eid:
            return []
        result = self.conn.execute("""
            MATCH (dp:DataPoint {dp_type: 'debt'})-[:BELONGS_TO]->(e:Event {id: $eid})
            RETURN dp
            ORDER BY dp.created_at ASC
        """, {"eid": eid})
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    node["payload"] = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    node["payload"] = {}
            items.append(node)
        return items

    # ═══════════════════════════════════════════════
    # 触发器查询（前向链定时使用）
    # ═══════════════════════════════════════════════

    def _q_active_events_with_new_expenses(self, params: dict) -> list[dict]:
        """查询有新增支出但尚未更新 balance 的活跃事件。

        query_name: active_events_with_new_expenses
        返回有 expense dp 但无 balance dp 的事件。
        """
        result = self.conn.execute("""
            MATCH (e:Event {status: 'active'})
            MATCH (exp:DataPoint {dp_type: 'expense'})-[:BELONGS_TO]->(e)
            OPTIONAL MATCH (bal:DataPoint {dp_type: 'balance'})-[:BELONGS_TO]->(e)
            WITH e, COUNT(DISTINCT exp) AS exp_count, COUNT(DISTINCT bal) AS bal_count
            WHERE exp_count > bal_count
            RETURN e
        """)
        items = []
        while result.has_next():
            items.append(dict(result.get_next()[0]))
        return items

    # ═══════════════════════════════════════════════
    # 通用节点查询
    # ═══════════════════════════════════════════════

    def _q_get_datapoint(self, params: dict) -> dict | None:
        """获取单个数据点。

        query_name: get_datapoint
        params: {dp_id: str}
        """
        dpid = params.get("dp_id", "")
        if not dpid:
            return None
        result = self.conn.execute(
            "MATCH (dp:DataPoint {id: $id}) RETURN dp",
            {"id": dpid},
        )
        if result.has_next():
            node = dict(result.get_next()[0])
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    node["payload"] = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    node["payload"] = {}
            return node
        return None

    def _q_get_event(self, params: dict) -> dict | None:
        """获取单个事件。"""
        eid = params.get("event_id", "")
        if not eid:
            return None
        result = self.conn.execute(
            "MATCH (e:Event {id: $id}) RETURN e",
            {"id": eid},
        )
        if result.has_next():
            return dict(result.get_next()[0])
        return None

    # ═══════════════════════════════════════════════
    # 穿透性跨事件查询
    # ═══════════════════════════════════════════════

    def _q_user_in_event(self, params: dict) -> dict | None:
        """检查用户是否已在某事件中有数据点。

        query_name: user_in_event
        params: {user_name, event_id}
        returns: {user_name, event_id} 或 None
        """
        user = params.get("user_name", "")
        eid = params.get("event_id", "")
        if not user or not eid:
            return None
        result = self.conn.execute("""
            MATCH (dp:DataPoint {user_name: $user})-[:BELONGS_TO]->(e:Event {id: $eid})
            RETURN dp LIMIT 1
        """, {"user": user, "eid": eid})
        if result.has_next():
            return {"user_name": user, "event_id": eid}
        return None

    def _q_global_debts_between(self, params: dict) -> list[dict]:
        """两人之间跨所有活跃事件的债务（按事件分组）。

        query_name: global_debts_between
        params: {debtor, creditor}
        returns: [{event_id, event_title, amount, dp_id}, ...]
        """
        debtor = params.get("debtor", "")
        creditor = params.get("creditor", "")
        if not debtor or not creditor:
            return []
        result = self.conn.execute("""
            MATCH (dp:DataPoint {dp_type: 'debt'})-[:BELONGS_TO]->(e:Event {status: 'active'})
            RETURN dp, e.id, e.title
            ORDER BY dp.created_at ASC
        """)
        items = []
        while result.has_next():
            node, eid, etitle = result.get_next()
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    pl = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    pl = {}
            d_debtor = pl.get("debtor", "")
            d_creditor = pl.get("creditor", "")
            if d_debtor == debtor and d_creditor == creditor:
                items.append({
                    "event_id": eid,
                    "event_title": str(etitle),
                    "amount": float(pl.get("amount", 0)),
                    "dp_id": node.get("id", ""),
                })
        # 按金额升序（小债先还）
        items.sort(key=lambda x: x["amount"])
        return items

    def _q_global_owes_summary(self, params: dict) -> dict | None:
        """某人跨所有活跃事件的债务汇总。

        query_name: global_owes_summary
        params: {user_name}
        returns: {total_owe: float, total_owed: float, details: [{event, counterparty, amount}]}
        """
        user = params.get("user_name", "")
        if not user:
            return None
        result = self.conn.execute("""
            MATCH (dp:DataPoint {dp_type: 'debt'})-[:BELONGS_TO]->(e:Event {status: 'active'})
            RETURN dp, e.title
        """)
        total_owe = 0.0
        total_owed = 0.0
        details = []
        while result.has_next():
            node, etitle = result.get_next()
            pl = node.get("payload", "{}")
            if isinstance(pl, str):
                try:
                    pl = json.loads(pl)
                except (json.JSONDecodeError, TypeError):
                    pl = {}
            debtor = pl.get("debtor", "")
            creditor = pl.get("creditor", "")
            amount = float(pl.get("amount", 0))
            if debtor == user:
                total_owe += amount
                details.append({
                    "event": str(etitle), "direction": "owe",
                    "counterparty": creditor, "amount": amount,
                })
            elif creditor == user:
                total_owed += amount
                details.append({
                    "event": str(etitle), "direction": "owed",
                    "counterparty": debtor, "amount": amount,
                })
        if total_owe == 0 and total_owed == 0:
            return None
        return {
            "total_owe": round(total_owe, 2),
            "total_owed": round(total_owed, 2),
            "net": round(total_owed - total_owe, 2),
            "details": details,
        }

    def _q_events_for_person(self, params: dict) -> list[dict]:
        """某人参与的所有活跃事件。

        query_name: events_for_person
        params: {user_name}
        returns: [{id, title}]
        """
        user = params.get("user_name", "")
        if not user:
            return []
        result = self.conn.execute("""
            MATCH (dp:DataPoint {user_name: $user})-[:BELONGS_TO]->(e:Event {status: 'active'})
            RETURN DISTINCT e.id, e.title
            ORDER BY e.created_at ASC
        """, {"user": user})
        items = []
        while result.has_next():
            eid, etitle = result.get_next()
            items.append({"id": eid, "title": str(etitle)})
        return items
