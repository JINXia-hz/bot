"""预定与事件激活规则。

前向链：
  - add_reservation: 预定 → 为每个参与者创建 dp → 关联事件 → 设定时提醒
  - timed: 定时检测到期预定 → 激活事件 → 群通知
"""

from src.engine.rule_engine import Rule, Fact, Var, Clause


def register(rb) -> None:
    """注册预定规则。"""

    # ── 前向链：预定触发 ────────────────────────────

    rb.register(Rule(
        name="reservation_triggers_setup",
        triggers=[
            Clause.action("add_reservation", {
                "user_name": Var("U"),
                "title": Var("T"),
                "content": Var("CT"),
                "time": Var("TM"),
                "people": Var("PP"),
            }),
        ],
        conclusion=Fact("reservation_setup", {
            "title": Var("T"),
            "event_id": Var("E"),
            "people": Var("PP"),
        }),
        conditions=[
            # 1. 找到或创建聚会事件
            Clause.graph("find_event_like", {"title": Var("T")}, Var("Ev")),
            # 2. 创建预定 dp（原始记录）
            Clause.create_dp("reservation", {
                "user_name": Var("U"),
                "payload": {"title": Var("T"), "content": Var("CT"), "time": Var("TM"), "people": Var("PP")},
                "event_id": Var("E"),
            }, Var("RP")),
            # 3. 关联到事件
            Clause.link("BELONGS_TO", Var("RP"), Var("E")),
            # 4. 为每个参与者创建个人预定 dp
            Clause.action("create_personal_reservations", {
                "people": Var("PP"),
                "title": Var("T"),
                "time": Var("TM"),
                "event_id": Var("E"),
            }),
            # 5. 产出定时提醒 FL（提前1小时）
            Clause.action("schedule_reminder", {
                "title": Var("T"),
                "time": Var("TM"),
                "people": Var("PP"),
                "event_id": Var("E"),
            }),
        ],
    ))

    # ── 前向链：没有匹配到事件时，创建新事件 ──────────────

    rb.register(Rule(
        name="reservation_creates_event",
        triggers=[
            Clause.action("add_reservation", {
                "user_name": Var("U"),
                "title": Var("T"),
                "content": Var("CT"),
                "time": Var("TM"),
                "people": Var("PP"),
            }),
        ],
        conclusion=Fact("reservation_setup", {
            "title": Var("T"),
            "event_created": Var("E"),
            "people": Var("PP"),
        }),
        conditions=[
            # 没有匹配到事件
            Clause.not_(Clause.graph("find_event_like", {"title": Var("T")}, Var("Ev"))),
            # 创建新事件
            Clause.action("open_event", {
                "title": Var("T"),
                "created_by": Var("U"),
                "auto_settle_at": Var("TM"),
            }),
            # 创建预定 dp
            Clause.create_dp("reservation", {
                "user_name": Var("U"),
                "payload": {"title": Var("T"), "content": Var("CT"), "time": Var("TM"), "people": Var("PP")},
            }, Var("RP")),
            # 为每个人创建预定 dp
            Clause.action("create_personal_reservations", {
                "people": Var("PP"),
                "title": Var("T"),
                "time": Var("TM"),
            }),
            # 设定时提醒
            Clause.action("schedule_reminder", {
                "title": Var("T"),
                "time": Var("TM"),
                "people": Var("PP"),
            }),
        ],
    ))

    # ── 定时规则：检查到期的预定，激活事件 ───────────

    rb.register(Rule(
        name="activate_due_reservation",
        triggers=[
            Clause.action("timed", {}),
        ],
        conclusion=Fact("reservation_activated", {
            "event_id": Var("E"),
            "title": Var("T"),
            "people": Var("PP"),
        }),
        conditions=[
            # 查询到期预定
            Clause.graph("reservation_due", {}, Var("R")),
            Clause.action("activate_reservation_event", {
                "reservation": Var("R"),
            }),
            # 发送通知
            Clause.action("notify_event_start", {
                "title": Var("T"),
                "people": Var("PP"),
            }),
        ],
    ))