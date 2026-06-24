"""kuzu 图数据库连接管理。

提供单例模式的数据库连接，确保整个应用使用同一个 kuzu 实例。
"""

import os
from pathlib import Path
from kuzu import Connection, Database


_db: Database | None = None
_conn: Connection | None = None


def get_db_path() -> Path:
    """获取数据库目录路径。

    从环境变量 KUZU_DB_PATH 读取，默认为 data/bot.kuzu。
    会相对于项目根目录解析。
    """
    env_path = os.getenv("KUZU_DB_PATH", "data/bot.kuzu")
    # 项目根目录：src/graph/connection.py → src/graph → src → 根
    base = Path(__file__).resolve().parent.parent.parent
    return base / env_path


def get_connection() -> Connection:
    """获取（或创建）kuzu 数据库连接。

    首次调用时自动创建 Database 和 Connection，
    后续调用返回同一个连接实例。

    Returns:
        kuzu Connection 实例
    """
    global _db, _conn
    if _conn is None:
        db_path = get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _db = Database(str(db_path))
        _conn = Connection(_db)
    return _conn


def close_connection() -> None:
    """关闭 kuzu 数据库连接。"""
    global _db, _conn
    _conn = None
    _db = None


def reset_database() -> None:
    """重置数据库（仅用于开发/测试）。

    关闭当前连接并删除数据库目录。
    """
    import shutil

    global _db, _conn
    close_connection()
    db_path = get_db_path()
    if db_path.exists():
        shutil.rmtree(db_path)


def init_database() -> Connection:
    """初始化数据库：获取连接并创建 schema。

    应用的启动入口应调用此函数。

    Returns:
        初始化后的 kuzu Connection
    """
    from src.graph.schema import init_schema

    conn = get_connection()
    init_schema(conn)
    return conn