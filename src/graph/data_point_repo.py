"""DataPoint Repository - 数据点的存取。"""

import json
from src.graph.base_repo import BaseRepo


class DataPointRepo(BaseRepo):
    """DataPoint 节点表 CRUD + 关系操作。"""

    # ── CRUD ────────────────────────────────────

    def create(self, dp_type: str, user_name: str, payload: dict,
               event_id: str | None = None, dp_id: str | None = None) -> str:
        """创建不可变数据点。

        Args:
            dp_type: 数据类型（expense/income/reminder/settlement_summary/reservation...）
            user_name: 所属用户名
            payload: 实际数据
            event_id: 所属事件 ID
            dp_id: 可选 ID

        Returns:
            数据点 ID
        """
        did = dp_id or self.new_id()
        self.execute("""
            CREATE (dp:DataPoint {
                id: $id,
                dp_type: $dp_type,
                user_name: $user_name,
                payload: $payload,
                event_id: $event_id,
                created_at: $now
            })
        """, {
            "id": did,
            "dp_type": dp_type,
            "user_name": user_name,
            "payload": json.dumps(payload, ensure_ascii=False),
            "event_id": event_id or "",
            "now": self.now(),
        })
        return did

    def get(self, dp_id: str) -> dict | None:
        """获取单条数据点（payload 自动解析为 dict）。"""
        result = self.execute(
            "MATCH (dp:DataPoint {id: $id}) RETURN dp",
            {"id": dp_id},
        )
        if result.has_next():
            row = result.get_next()
            node = dict(row[0])
            node["payload"] = json.loads(node["payload"])
            return node
        return None

    def get_many(self, dp_ids: list[str]) -> list[dict]:
        """批量获取数据点。"""
        items = []
        for did in dp_ids:
            dp = self.get(did)
            if dp:
                items.append(dp)
        return items

    # ── 关系操作 ────────────────────────────────

    def link_to_event(self, dp_id: str, event_id: str) -> None:
        """建立 BELONGS_TO 关系：DataPoint → Event。"""
        self.execute("""
            MATCH (dp:DataPoint {id: $dp_id})
            MATCH (e:Event {id: $event_id})
            CREATE (dp)-[:BELONGS_TO]->(e)
        """, {"dp_id": dp_id, "event_id": event_id})

    def link_data_line(self, from_dp_id: str, to_dp_id: str,
                       action_log_id: str, event_id: str | None = None) -> None:
        """建立 DATA_LINE 关系：from → to（因果关系）。"""
        self.execute("""
            MATCH (a:DataPoint {id: $from_id})
            MATCH (b:DataPoint {id: $to_id})
            CREATE (a)-[:DATA_LINE {
                action_log_id: $log_id,
                event_id: $event_id,
                created_at: $now
            }]->(b)
        """, {
            "from_id": from_dp_id,
            "to_id": to_dp_id,
            "log_id": action_log_id,
            "event_id": event_id or "",
            "now": self.now(),
        })

    def link_consumed(self, dp_id: str, log_id: str) -> None:
        """建立 CONSUMED 关系：DataPoint → ActionLog（被行动消耗）。"""
        self.execute("""
            MATCH (dp:DataPoint {id: $dp_id})
            MATCH (log:ActionLog {id: $log_id})
            CREATE (dp)-[:CONSUMED]->(log)
        """, {"dp_id": dp_id, "log_id": log_id})

    def link_produced(self, log_id: str, dp_id: str) -> None:
        """建立 PRODUCED 关系：ActionLog → DataPoint（行动产出数据点）。"""
        self.execute("""
            MATCH (log:ActionLog {id: $log_id})
            MATCH (dp:DataPoint {id: $dp_id})
            CREATE (log)-[:PRODUCED]->(dp)
        """, {"log_id": log_id, "dp_id": dp_id})

    # ── 图查询 ────────────────────────────────────

    def get_data_lines_for_event(self, event_id: str) -> list[dict]:
        """查询某事件下的所有数据线（因果链）。"""
        result = self.execute("""
            MATCH (a:DataPoint)-[dl:DATA_LINE]->(b:DataPoint)
            WHERE dl.event_id = $event_id
            RETURN a.id AS from_id, a.dp_type AS from_type, a.user_name AS from_user,
                   b.id AS to_id, b.dp_type AS to_type, b.user_name AS to_user,
                   dl.action_log_id AS action_log_id, dl.created_at AS created_at
            ORDER BY dl.created_at ASC
        """, {"event_id": event_id})
        lines = []
        while result.has_next():
            row = result.get_next()
            lines.append({
                "from_id": row[0], "from_type": row[1], "from_user": row[2],
                "to_id": row[3], "to_type": row[4], "to_user": row[5],
                "action_log_id": row[6], "created_at": row[7],
            })
        return lines

    def get_event_datapoints(self, event_id: str) -> list[dict]:
        """查询某事件下所有数据点（通过 BELONGS_TO 关系）。"""
        result = self.execute("""
            MATCH (dp:DataPoint)-[:BELONGS_TO]->(e:Event {id: $event_id})
            RETURN dp
            ORDER BY dp.created_at ASC
        """, {"event_id": event_id})
        items = []
        while result.has_next():
            row = result.get_next()
            node = dict(row[0])
            node["payload"] = json.loads(node["payload"])
            items.append(node)
        return items