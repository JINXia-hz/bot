"""ActionLog Repository - 行动日志的存取。"""

from src.graph.base_repo import BaseRepo


class ActionLogRepo(BaseRepo):
    """ActionLog 节点表 CRUD。"""

    def create(self, event_id: str | None = None,
               action_summary: str = "",
               log_id: str | None = None) -> str:
        """创建一条行动日志。

        Args:
            event_id: 所属事件 ID
            action_summary: 行动描述
            log_id: 可选 ID

        Returns:
            日志 ID
        """
        lid = log_id or self.new_id()
        self.execute("""
            CREATE (log:ActionLog {
                id: $id,
                event_id: $event_id,
                action_summary: $action_summary,
                created_at: $now
            })
        """, {
            "id": lid,
            "event_id": event_id or "",
            "action_summary": action_summary,
            "now": self.now(),
        })
        return lid

    def get(self, log_id: str) -> dict | None:
        """获取单条行动日志。"""
        result = self.execute(
            "MATCH (log:ActionLog {id: $id}) RETURN log",
            {"id": log_id},
        )
        if result.has_next():
            return dict(result.get_next()[0])
        return None

    def get_logs_for_event(self, event_id: str) -> list[dict]:
        """查询某事件的所有行动日志（按时间排序）。"""
        result = self.execute("""
            MATCH (log:ActionLog {event_id: $event_id})
            RETURN log
            ORDER BY log.created_at ASC
        """, {"event_id": event_id})
        logs = []
        while result.has_next():
            logs.append(dict(result.get_next()[0]))
        return logs

    # ── 图查询：行动产生的数据点和数据线 ──────────

    def get_produced_datapoints(self, log_id: str) -> list[dict]:
        """查询某行动产出的所有数据点。"""
        import json
        result = self.execute("""
            MATCH (log:ActionLog {id: $log_id})-[:PRODUCED]->(dp:DataPoint)
            RETURN dp
            ORDER BY dp.created_at ASC
        """, {"log_id": log_id})
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            node["payload"] = json.loads(node["payload"])
            items.append(node)
        return items

    def get_consumed_datapoints(self, log_id: str) -> list[dict]:
        """查询某行动消耗（输入）的所有数据点。"""
        import json
        result = self.execute("""
            MATCH (dp:DataPoint)-[:CONSUMED]->(log:ActionLog {id: $log_id})
            RETURN dp
            ORDER BY dp.created_at ASC
        """, {"log_id": log_id})
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            node["payload"] = json.loads(node["payload"])
            items.append(node)
        return items

    def get_generated_fls(self, log_id: str) -> list[dict]:
        """查询某行动产出的格式语言。"""
        import json
        result = self.execute("""
            MATCH (log:ActionLog {id: $log_id})-[:GENERATED]->(fl:FormalLanguage)
            RETURN fl
            ORDER BY fl.created_at ASC
        """, {"log_id": log_id})
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            node["payload"] = json.loads(node["payload"])
            items.append(node)
        return items

    def get_triggering_fls(self, log_id: str) -> list[dict]:
        """查询是哪些格式语言触发了该行动。"""
        import json
        result = self.execute("""
            MATCH (fl:FormalLanguage)-[:TRIGGERED]->(log:ActionLog {id: $log_id})
            RETURN fl
            ORDER BY fl.created_at ASC
        """, {"log_id": log_id})
        items = []
        while result.has_next():
            node = dict(result.get_next()[0])
            node["payload"] = json.loads(node["payload"])
            items.append(node)
        return items