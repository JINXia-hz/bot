"""kuzu 图数据库 Schema 定义。

定义了五大核心数据类型的节点表和关系表：
  - DataPoint     不可变状态单元
  - Event         管理容器（块）
  - ActionLog     每次行动的不可变记录
  - FormalLanguage 结构化指令/待输出内容
  - RawMessage    群聊原始消息
"""

from kuzu import Connection


DDL_STATEMENTS = [
    # ── 节点表 ──────────────────────────────
    """
    CREATE NODE TABLE IF NOT EXISTS DataPoint (
        id STRING PRIMARY KEY,
        dp_type STRING,
        user_name STRING,
        payload STRING,
        event_id STRING,
        created_at TIMESTAMP
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS Event (
        id STRING PRIMARY KEY,
        title STRING,
        status STRING DEFAULT 'active',
        trigger_type STRING,
        auto_settle_at TIMESTAMP,
        settled_at TIMESTAMP,
        summary_dp_id STRING,
        created_by STRING,
        created_at TIMESTAMP
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS ActionLog (
        id STRING PRIMARY KEY,
        event_id STRING,
        action_summary STRING,
        created_at TIMESTAMP
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS FormalLanguage (
        id STRING PRIMARY KEY,
        source STRING,
        category STRING,
        payload STRING,
        status STRING DEFAULT 'pending',
        parent_message_id STRING,
        parent_log_id STRING,
        created_at TIMESTAMP
    )
    """,
    """
    CREATE NODE TABLE IF NOT EXISTS RawMessage (
        id STRING PRIMARY KEY,
        content STRING,
        is_directed BOOLEAN,
        sender STRING,
        group_id STRING,
        timestamp TIMESTAMP
    )
    """,
    # ── 关系表 ──────────────────────────────
    """
    CREATE REL TABLE IF NOT EXISTS DATA_LINE (
        FROM DataPoint TO DataPoint,
        action_log_id STRING,
        event_id STRING,
        created_at TIMESTAMP
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS BELONGS_TO (
        FROM DataPoint TO Event
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS PRODUCED (
        FROM ActionLog TO DataPoint
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS CONSUMED (
        FROM DataPoint TO ActionLog
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS GENERATED (
        FROM ActionLog TO FormalLanguage
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS TRANSLATES_TO (
        FROM RawMessage TO FormalLanguage
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS TRIGGERED (
        FROM FormalLanguage TO ActionLog,
        event_id STRING
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS MANAGES (
        FROM FormalLanguage TO Event
    )
    """,
]


def init_schema(conn: Connection) -> None:
    """初始化图数据库 schema。

    所有 DDL 使用 IF NOT EXISTS，可安全重复调用。

    Args:
        conn: kuzu 数据库连接
    """
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)