"""FormalLanguage Repository - 格式语言的存取。"""

import json
from src.graph.base_repo import BaseRepo


# ── 枚举常量 ──────────────────────────────────────

class FLSource:
    LLM_TRANSLATED = "llm_translated"
    MANUAL = "manual"
    ACTION_GENERATED = "action_generated"


class FLCategory:
    EVENT_MANAGEMENT = "event_management"
    GENERAL_INSTRUCTION = "general_instruction"
    OUTPUT_IMMEDIATE = "output_immediate"    # 即刻回复
    OUTPUT_SCHEDULED = "output_scheduled"    # 定时回复


class FLStatus:
    PENDING = "pending"
    EXECUTED = "executed"
    SCHEDULED = "scheduled"        # 等待定时触发
    REPLIED = "replied"            # 已回复


class FormalLanguageRepo(BaseRepo):
    """FormalLanguage 节点表 CRUD + 关系操作。"""

    # ── CRUD ────────────────────────────────────

    def create(self, payload: dict, source: str, category: str,
               status: str = FLStatus.PENDING,
               parent_message_id: str | None = None,
               parent_log_id: str | None = None,
               fl_id: str | None = None) -> str:
        """创建一条格式语言记录。

        Args:
            payload: 结构化指令体（dict）
            source: 来源（llm_translated / manual / action_generated）
            category: 类别（event_management / general_instruction / output_pending）
            status: 状态
            parent_message_id: 如果是翻译来源，关联的 RawMessage ID
            parent_log_id: 如果是行动产出，关联的 ActionLog ID
            fl_id: 可选 ID

        Returns:
            格式语言 ID
        """
        fid = fl_id or self.new_id()
        self.execute("""
            CREATE (fl:FormalLanguage {
                id: $id,
                source: $source,
                category: $category,
                payload: $payload,
                status: $status,
                parent_message_id: $parent_message_id,
                parent_log_id: $parent_log_id,
                created_at: $now
            })
        """, {
            "id": fid,
            "source": source,
            "category": category,
            "payload": json.dumps(payload, ensure_ascii=False),
            "status": status,
            "parent_message_id": parent_message_id or "",
            "parent_log_id": parent_log_id or "",
            "now": self.now(),
        })
        return fid

    def get(self, fl_id: str) -> dict | None:
        """获取单条格式语言（payload 自动解析为 dict）。"""
        result = self.execute(
            "MATCH (fl:FormalLanguage {id: $id}) RETURN fl",
            {"id": fl_id},
        )
        if result.has_next():
            row = result.get_next()
            node = dict(row[0])
            node["payload"] = json.loads(node["payload"])
            return node
        return None

    def update_status(self, fl_id: str, status: str) -> None:
        """更新格式语言的状态。"""
        self.execute(
            "MATCH (fl:FormalLanguage {id: $id}) SET fl.status = $status",
            {"id": fl_id, "status": status},
        )

    def list_by_status(self, status: str) -> list[dict]:
        """按状态查询格式语言列表。"""
        result = self.execute("""
            MATCH (fl:FormalLanguage {status: $status})
            RETURN fl
            ORDER BY fl.created_at ASC
        """, {"status": status})
        items = []
        while result.has_next():
            row = result.get_next()
            node = dict(row[0])
            node["payload"] = json.loads(node["payload"])
            items.append(node)
        return items

    def list_pending_instructions(self) -> list[dict]:
        """获取所有待执行的指令（非输出类）。"""
        result = self.execute("""
            MATCH (fl:FormalLanguage {status: $status})
            WHERE fl.category IN [$cat_event, $cat_instr]
            RETURN fl
            ORDER BY fl.created_at ASC
        """, {
            "status": FLStatus.PENDING,
            "cat_event": FLCategory.EVENT_MANAGEMENT,
            "cat_instr": FLCategory.GENERAL_INSTRUCTION,
        })
        items = []
        while result.has_next():
            row = result.get_next()
            node = dict(row[0])
            node["payload"] = json.loads(node["payload"])
            items.append(node)
        return items

    def list_due_scheduled(self) -> list[dict]:
        """查询所有到期的定时回复 FL（SCHEDULED 且 schedule_at <= now）。

        schedule_at 存储在 payload 中。
        """
        result = self.execute("""
            MATCH (fl:FormalLanguage {status: $status})
            RETURN fl
            ORDER BY fl.created_at ASC
        """, {"status": FLStatus.SCHEDULED})
        items = []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        while result.has_next():
            row = result.get_next()
            node = dict(row[0])
            node["payload"] = json.loads(node["payload"])
            schedule_at = node["payload"].get("schedule_at", "")
            if schedule_at and schedule_at <= now:
                items.append(node)
        return items

    # ── 关系操作 ────────────────────────────────

    def link_to_message(self, fl_id: str, message_id: str) -> None:
        """建立 TRANSLATES_TO 关系：RawMessage → FormalLanguage。"""
        self.execute("""
            MATCH (m:RawMessage {id: $msg_id})
            MATCH (fl:FormalLanguage {id: $fl_id})
            CREATE (m)-[:TRANSLATES_TO]->(fl)
        """, {"msg_id": message_id, "fl_id": fl_id})

    def link_to_event(self, fl_id: str, event_id: str) -> None:
        """建立 MANAGES 关系：FormalLanguage → Event（事件管理指令）。"""
        self.execute("""
            MATCH (fl:FormalLanguage {id: $fl_id})
            MATCH (e:Event {id: $event_id})
            CREATE (fl)-[:MANAGES]->(e)
        """, {"fl_id": fl_id, "event_id": event_id})

    def link_triggered_action(self, fl_id: str, log_id: str, event_id: str | None = None) -> None:
        """建立 TRIGGERED 关系：FormalLanguage → ActionLog。"""
        self.execute("""
            MATCH (fl:FormalLanguage {id: $fl_id})
            MATCH (log:ActionLog {id: $log_id})
            CREATE (fl)-[:TRIGGERED {event_id: $event_id}]->(log)
        """, {
            "fl_id": fl_id,
            "log_id": log_id,
            "event_id": event_id or "",
        })

    def link_generated_by(self, fl_id: str, log_id: str) -> None:
        """建立 GENERATED 关系：ActionLog → FormalLanguage（行动产出 FL）。"""
        self.execute("""
            MATCH (log:ActionLog {id: $log_id})
            MATCH (fl:FormalLanguage {id: $fl_id})
            CREATE (log)-[:GENERATED]->(fl)
        """, {"log_id": log_id, "fl_id": fl_id})