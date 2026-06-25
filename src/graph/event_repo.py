"""Event Repository - 事件（管理容器）的存取与生命周期管理。"""

from src.graph.base_repo import BaseRepo


class EventStatus:
    ACTIVE = "active"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class EventTriggerType:
    SYSTEM = "system"
    MANUAL = "manual"


class EventRepo(BaseRepo):
    """Event 节点表 CRUD + 生命周期管理。"""

    # ── CRUD ────────────────────────────────────

    def create(self, title: str, created_by: str,
               trigger_type: str = EventTriggerType.MANUAL,
               auto_settle_at: str | None = None,
               event_id: str | None = None) -> str:
        """创建一个事件。

        Args:
            title: 事件标题
            created_by: 创建者
            trigger_type: 触发类型（system/manual）
            auto_settle_at: 自动结算时间（ISO 8601），可选
            event_id: 可选 ID

        Returns:
            事件 ID
        """
        eid = event_id or self.new_id()
        now = self.now()
        auto_val = auto_settle_at if auto_settle_at else now
        self.execute("""
            CREATE (e:Event {
                id: $id,
                title: $title,
                status: $status,
                trigger_type: $trigger_type,
                auto_settle_at: $auto_settle_at,
                settled_at: $settled_at,
                summary_dp_id: $summary_dp_id,
                created_by: $created_by,
                created_at: $now
            })
        """, {
            "id": eid,
            "title": title,
            "status": EventStatus.ACTIVE,
            "trigger_type": trigger_type,
            "auto_settle_at": auto_val,
            "settled_at": now,
            "summary_dp_id": "",
            "created_by": created_by,
            "now": now,
        })
        return eid

    def get(self, event_id: str) -> dict | None:
        """获取单个事件。"""
        result = self.execute(
            "MATCH (e:Event {id: $id}) RETURN e",
            {"id": event_id},
        )
        if result.has_next():
            return dict(result.get_next()[0])
        return None

    def settle(self, event_id: str, summary_dp_id: str) -> None:
        """结算事件：设置状态为 settled，关联统合数据点。"""
        now = self.now()
        self.execute("""
            MATCH (e:Event {id: $id})
            SET e.status = $status,
                e.settled_at = $now,
                e.summary_dp_id = $summary_dp_id
        """, {
            "id": event_id,
            "status": EventStatus.SETTLED,
            "now": now,
            "summary_dp_id": summary_dp_id,
        })

    def cancel(self, event_id: str) -> None:
        """取消事件。"""
        now = self.now()
        self.execute("""
            MATCH (e:Event {id: $id})
            SET e.status = $status, e.settled_at = $now
        """, {
            "id": event_id,
            "status": EventStatus.CANCELLED,
            "now": now,
        })

    # ── 查询 ────────────────────────────────────

    def list_active(self) -> list[dict]:
        """查询所有活跃中的事件。"""
        result = self.execute("""
            MATCH (e:Event {status: $status})
            RETURN e
            ORDER BY e.created_at ASC
        """, {"status": EventStatus.ACTIVE})
        events = []
        while result.has_next():
            events.append(dict(result.get_next()[0]))
        return events

    def list_active_by_user(self, user_name: str) -> list[dict]:
        """查询某用户参与的活跃事件。"""
        result = self.execute("""
            MATCH (e:Event {status: $status})
            MATCH (dp:DataPoint)-[:BELONGS_TO]->(e)
            WHERE dp.user_name = $user_name
            RETURN DISTINCT e
            ORDER BY e.created_at ASC
        """, {
            "status": EventStatus.ACTIVE,
            "user_name": user_name,
        })
        events = []
        while result.has_next():
            events.append(dict(result.get_next()[0]))
        return events

    def get_due_events(self) -> list[dict]:
        """查询所有需要自动结算的事件（auto_settle_at <= now 且仍为 active）。"""
        result = self.execute("""
            MATCH (e:Event {status: $status})
            WHERE e.auto_settle_at <> '' AND e.auto_settle_at <= $now
            RETURN e
            ORDER BY e.auto_settle_at ASC
        """, {
            "status": EventStatus.ACTIVE,
            "now": self.now(),
        })
        events = []
        while result.has_next():
            events.append(dict(result.get_next()[0]))
        return events