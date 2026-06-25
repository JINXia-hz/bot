"""RawMessage Repository - 群聊原始消息的存取。"""

from src.graph.base_repo import BaseRepo


class RawMessageRepo(BaseRepo):
    """RawMessage 节点表 CRUD。"""

    def create(self, content: str, sender: str, group_id: str,
               is_directed: bool = False, msg_id: str | None = None) -> str:
        """创建一条原始消息记录。

        Args:
            content: 消息正文
            sender: 发送者 QQ 号或昵称
            group_id: 群号
            is_directed: 是否为 @bot 的有向消息
            msg_id: 消息 ID，不传则自动生成

        Returns:
            消息 ID
        """
        mid = msg_id or self.new_id()
        self.execute("""
            CREATE (m:RawMessage {
                id: $id,
                content: $content,
                is_directed: $is_directed,
                sender: $sender,
                group_id: $group_id,
                timestamp: $now
            })
        """, {
            "id": mid,
            "content": content,
            "is_directed": is_directed,
            "sender": sender,
            "group_id": group_id,
            "now": self.now(),
        })
        return mid

    def get(self, msg_id: str) -> dict | None:
        """获取单条消息。"""
        result = self.execute(
            "MATCH (m:RawMessage {id: $id}) RETURN m",
            {"id": msg_id},
        )
        if result.has_next():
            row = result.get_next()
            return dict(row[0])
        return None

    def list_undirected_window(self, group_id: str, window_minutes: int = 30) -> list[dict]:
        """获取指定群最近 N 分钟内的无向消息（时间窗口）。

        Args:
            group_id: 群号
            window_minutes: 时间窗口（分钟），默认 30

        Returns:
            时间窗口内的无向消息列表（按时间正序）
        """
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()

        result = self.execute("""
            MATCH (m:RawMessage)
            WHERE m.group_id = $group_id AND m.is_directed = false
              AND m.timestamp >= $cutoff
            RETURN m
            ORDER BY m.timestamp ASC
        """, {"group_id": group_id, "cutoff": cutoff})
        messages = []
        while result.has_next():
            row = result.get_next()
            messages.append(dict(row[0]))
        return messages

    def list_since_last_directed(self, group_id: str) -> list[dict]:
        """获取从上一条有向消息到现在的所有消息。

        用于上下文精简化：只取两个 @bot 之间的"新对话"。

        Returns:
            按时间正序的消息列表
        """
        from datetime import datetime, timedelta, timezone

        # 先找最近一条有向消息（不含当前这条）
        result = self.execute("""
            MATCH (m:RawMessage)
            WHERE m.group_id = $gid AND m.is_directed = true
            RETURN m.timestamp
            ORDER BY m.timestamp DESC
            LIMIT 2
        """, {"gid": group_id})

        timestamps = []
        while result.has_next():
            timestamps.append(result.get_next()[0])

        if len(timestamps) < 2:
            # 没有上一条有向消息，取最近 15 分钟
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        else:
            cutoff = timestamps[1]  # 上一条有向消息的时间

        result2 = self.execute("""
            MATCH (m:RawMessage)
            WHERE m.group_id = $gid AND m.timestamp >= $cutoff
            RETURN m
            ORDER BY m.timestamp ASC
        """, {"gid": group_id, "cutoff": cutoff})

        messages = []
        while result2.has_next():
            messages.append(dict(result2.get_next()[0]))
        return messages

    def list_by_senders(self, group_id: str, senders: list[str],
                        limit_each: int = 15) -> dict[str, list[dict]]:
        """按发言者分别追溯最近 N 条消息。

        用于上下文精简化：了解每个参与者的最近发言动向。

        Returns:
            {sender_name: [msg_dict, ...]}
        """
        result_map: dict[str, list[dict]] = {}
        for sender in senders:
            if not sender:
                continue
            r = self.execute("""
                MATCH (m:RawMessage)
                WHERE m.group_id = $gid AND m.sender = $sender
                RETURN m
                ORDER BY m.timestamp DESC
                LIMIT $limit
            """, {"gid": group_id, "sender": sender, "limit": limit_each})
            msgs = []
            while r.has_next():
                msgs.append(dict(r.get_next()[0]))
            result_map[sender] = list(reversed(msgs))
        return result_map

    def cleanup_old_undirected(self, retention_minutes: int = 60) -> int:
        """清理超过保留时间的无向消息。

        有向消息不清理（保留审计链）。

        Args:
            retention_minutes: 保留时长（分钟），默认 60

        Returns:
            删除的消息数量
        """
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=retention_minutes)).isoformat()

        # 先统计
        count_result = self.execute("""
            MATCH (m:RawMessage)
            WHERE m.is_directed = false AND m.timestamp < $cutoff
            RETURN COUNT(m) AS cnt
        """, {"cutoff": cutoff})
        count = 0
        if count_result.has_next():
            count = count_result.get_next()[0]

        if count > 0:
            # kuzu 不支持 DELETE 带 WHERE 条件，逐条删除
            result = self.execute("""
                MATCH (m:RawMessage)
                WHERE m.is_directed = false AND m.timestamp < $cutoff
                RETURN m.id
            """, {"cutoff": cutoff})
            ids_to_delete = []
            while result.has_next():
                ids_to_delete.append(result.get_next()[0])

            for mid in ids_to_delete:
                self.execute(
                    "MATCH (m:RawMessage {id: $id}) DELETE m",
                    {"id": mid},
                )

        return count

