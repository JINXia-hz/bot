"""Repository 基类 - 提供 Cypher 查询执行工具。

所有 repo 继承此类以获得：
  - 统一的连接访问
  - 参数化查询执行
  - 节点/关系存在性检查
"""

import uuid
from datetime import datetime, timezone
from typing import Any
from kuzu import Connection

from src.graph.connection import get_connection


def _new_id() -> str:
    """生成唯一 ID（UUID4 hex，32 字符）。"""
    return uuid.uuid4().hex


def _now_iso() -> str:
    """返回 ISO 8601 格式的 UTC 当前时间（字符串）。"""
    return datetime.now(timezone.utc).isoformat()


def _now_dt():
    """返回 kuzu TIMESTAMP 兼容的当前时间。"""
    return datetime.now().replace(microsecond=0)


class BaseRepo:
    """各节点/关系表的 Repository 基类。"""

    def __init__(self, conn: Connection | None = None):
        self.conn = conn or get_connection()

    def execute(self, query: str, params: dict[str, Any] | None = None) -> Any:
        """执行一条 Cypher 查询。

        Args:
            query: Cypher 查询语句
            params: 命名参数

        Returns:
            查询结果（kuzu QueryResult，可迭代）
        """
        if params is None:
            params = {}
        return self.conn.execute(query, params)

    def exists(self, table: str, id_val: str) -> bool:
        """检查指定表中是否存在给定 ID 的节点。

        Args:
            table: 节点表名
            id_val: 节点 ID

        Returns:
            True 表示存在
        """
        result = self.execute(
            f"MATCH (n:{table}) WHERE n.id = $id RETURN n.id",
            {"id": id_val},
        )
        return result.has_next()

    @staticmethod
    def new_id() -> str:
        return _new_id()

    @staticmethod
    def now():
        """返回 kuzu TIMESTAMP 兼容的当前时间。"""
        return _now_dt()

    @staticmethod
    def now_iso() -> str:
        """返回 ISO 8601 时间字符串（用于字符串比较等场景）。"""
        return _now_iso()
