"""图上下文组装器 - 从 kuzu 图数据库提取结构化上下文供 LLM 使用。

利用 Cypher 做定向图遍历，查询与当前用户/群聊相关的：
  - 活跃事件 + 参与人
  - 待付账单
  - 近期数据点
  - 群内最近事件

输出为格式化文本，嵌入 translator 的 prompt 中。
"""

import json
from kuzu import Connection
from src.graph.connection import get_connection


class ContextAssembler:
    """从 kuzu 图数据库组装 LLM 上下文。

    不做全文搜索——利用图结构沿边做定向遍历。
    """

    def __init__(self, conn: Connection | None = None):
        self.conn = conn or get_connection()

    def assemble_for_event_matching(
        self, sender: str, senders: set[str],
        sender_traces: dict[str, list[dict]],
    ) -> str:
        """组装轻量上下文，仅用于 LLM 匹配活跃事件。

        与 assemble() 不同：不发全量数据，只发"谁最近说了什么 + 活跃事件列表"。

        Args:
            sender: 当前 @bot 的人
            senders: 区间内所有发言者
            sender_traces: {发言人: [最近消息], ...}
        """
        parts = []

        # 参与者最近发言
        trace_lines = []
        for person in sorted(senders):
            msgs = sender_traces.get(person, [])
            if not msgs:
                continue
            short = [m.get("content", "")[:60] for m in msgs[-3:]]  # 每人最多 3 条
            trace_lines.append(f"- {person}: {' | '.join(short)}")
        if trace_lines:
            parts.append(f"[参与者最近发言]\n" + "\n".join(trace_lines))

        # 活跃事件列表（轻量）
        ev_lines = self._active_events_light()
        if ev_lines:
            parts.append(f"[活跃事件]\n" + "\n".join(ev_lines))

        return "\n\n".join(parts) if parts else "（无上下文）"

    def _active_events_light(self) -> list[str]:
        """活跃事件简要列表：仅 title + id + 参与者。

        Returns:
            如 ["- id:e1 火锅局(参与者:张三/李四)", ...]
        """
        result = self.conn.execute("""
            MATCH (e:Event {status: "active"})
            MATCH (dp:DataPoint)-[:BELONGS_TO]->(e)
            WHERE dp.dp_type IN ['expense', 'income', 'participant_entry']
            WITH e, COLLECT(DISTINCT dp.user_name) AS users
            RETURN e.id, e.title, users
            ORDER BY e.created_at DESC
            LIMIT 10
        """)
        lines = []
        while result.has_next():
            eid, title, users = result.get_next()
            participants_str = "/".join(users) if users else "无"
            lines.append(f"- id:{eid} {title}(参与者:{participants_str})")
        return lines

    def assemble(self, user_name: str, group_id: str) -> str:
        """为主叫用户组装完整图上下文，返回格式化文本。

        Args:
            user_name: 调用者用户名
            group_id: 群号（预留，当前暂不按群过滤）

        Returns:
            格式化的多段落文本，可直接嵌入 LLM prompt
        """
        parts = []

        ev = self._active_events(user_name)
        if ev:
            parts.append(f"[活跃事件]\n{ev}")

        bills = self._pending_bills(user_name)
        if bills:
            parts.append(f"[待付账单]\n{bills}")

        dps = self._recent_datapoints(user_name, limit=10)
        if dps:
            parts.append(f"[近期数据点]\n{dps}")

        events = self._recent_events(limit=5)
        if events:
            parts.append(f"[群内事件]\n{events}")

        return "\n\n".join(parts) if parts else "（无历史上下文）"

    # ── 各查询方法 ────────────────────────────────

    def _active_events(self, user_name: str) -> str:
        """用户参与的活跃事件，含数据点数量摘要。"""
        result = self.conn.execute("""
            MATCH (e:Event {status: "active"})
            MATCH (dp:DataPoint)-[:BELONGS_TO]->(e)
            WHERE dp.user_name = $user
            WITH e, COUNT(DISTINCT dp) AS dp_count
            RETURN e.title, e.trigger_type, e.auto_settle_at,
                   dp_count, e.created_at, e.created_by, e.id
            ORDER BY e.created_at DESC
            LIMIT 5
        """, {"user": user_name})

        lines = []
        while result.has_next():
            row = result.get_next()
            title, trigger, auto, count, created, creator, eid = row
            trigger_label = "系统" if trigger == "system" else "手动"
            auto_label = auto if auto else "手动结算"
            created_date = str(created)[:10] if created else "?"
            lines.append(
                f"-「{title}」({trigger_label}开启, {count}个数据点, "
                f"创建者: {creator}, 结算: {auto_label}, 创建于: {created_date})"
            )
        return "\n".join(lines) if lines else ""

    def _pending_bills(self, user_name: str) -> str:
        """用户的待付账单。"""
        result = self.conn.execute("""
            MATCH (dp:DataPoint {dp_type: "bill_item", user_name: $user})
            RETURN dp.payload, dp.created_at, dp.id
            ORDER BY dp.created_at DESC
            LIMIT 10
        """, {"user": user_name})

        lines = []
        while result.has_next():
            row = result.get_next()
            pl = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            if pl.get("status") == "pending":
                title = pl.get("bill_title", "?")
                amount = pl.get("per_person", "?")
                lines.append(f"- {title}: {amount} 元 (待付)")
        return "\n".join(lines) if lines else ""

    def _recent_datapoints(self, user_name: str, limit: int = 10) -> str:
        """用户最近的数据点（正序）。"""
        result = self.conn.execute("""
            MATCH (dp:DataPoint {user_name: $user})
            RETURN dp.dp_type, dp.payload, dp.created_at
            ORDER BY dp.created_at DESC
            LIMIT $limit
        """, {"user": user_name, "limit": limit})

        items = []
        while result.has_next():
            row = result.get_next()
            dp_type = row[0]
            pl = json.loads(row[1]) if isinstance(row[1], str) else row[1]
            ts = str(row[2])[:10] if row[2] else "?"
            summary = self._summarize_dp(dp_type, pl)
            items.append(f"- {ts} [{dp_type}] {summary}")

        return "\n".join(reversed(items)) if items else ""  # 正序

    def _recent_events(self, limit: int = 5) -> str:
        """群内最近的事件（不限用户，不限状态）。"""
        result = self.conn.execute("""
            MATCH (e:Event)
            RETURN e.title, e.status, e.created_by, e.created_at, e.auto_settle_at
            ORDER BY e.created_at DESC
            LIMIT $limit
        """, {"limit": limit})

        lines = []
        while result.has_next():
            row = result.get_next()
            title, status, creator, created, auto = row
            created_date = str(created)[:10] if created else "?"
            status_cn = {"active": "进行中", "settled": "已结算", "cancelled": "已取消"}.get(status, status)
            auto_info = f", 计划自动结算: {str(auto)[:10]}" if auto else ""
            lines.append(f"-「{title}」({status_cn}, 由 {creator} 开启{auto_info})")
        return "\n".join(lines) if lines else ""

    @staticmethod
    def _summarize_dp(dp_type: str, payload: dict) -> str:
        """把数据点 payload 压缩为一行摘要文本。"""
        if dp_type == "expense":
            return f"支出 {payload.get('amount', '?')} {payload.get('category', '')} {payload.get('note', '')}".strip()
        elif dp_type == "income":
            return f"收入 {payload.get('amount', '?')} {payload.get('note', '')}".strip()
        elif dp_type == "bill_item":
            return f"{payload.get('bill_title', '?')} {payload.get('per_person', '?')}元 ({payload.get('status', '?')})"
        elif dp_type == "reservation":
            return f"预定「{payload.get('title', '?')}」{payload.get('time', '')}".strip()
        elif dp_type == "reminder":
            return f"提醒: {payload.get('content', '?')}"
        elif dp_type == "settlement_summary":
            return f"事件结算: {payload.get('event_title', '?')}"
        # fallback: 截取前80字符
        return str(payload)[:80]