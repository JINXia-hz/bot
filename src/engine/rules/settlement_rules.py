"""结算规则。

前向链定时检测：
  - 检测到期事件 → 结算
  - 检测所有账单已付清的事件 → 结算

后向链查询：
  - event_summary: 获取结算摘要
"""

from src.engine.rule_engine import Rule, Fact, Var, Clause


def register(rb) -> None:
    """注册结算规则。"""

    # ── 定时规则：auto_settle_at 到期 → 结算 ──────────

    rb.register(Rule(
        name="auto_settle_due_events",
        triggers=[
            Clause.action("timed", {}),
        ],
        conclusion=Fact("event_auto_settled", {
            "event_id": Var("E"),
            "title": Var("T"),
        }),
        conditions=[
            Clause.graph("event_due_settle", {}, Var("Ev")),
            Clause.resolve(Var("Ev"), "id", Var("E")),
            Clause.resolve(Var("Ev"), "title", Var("T")),
            Clause.action("settle_event", {
                "event_id": Var("E"),
                "title": Var("T"),
            }),
        ],
    ))

    # ── 前向链：LLM 指令开启事件 ─────────────────────────

    rb.register(Rule(
        name="manual_open_event",
        triggers=[
            Clause.action("open_event", {
                "title": Var("T"),
                "auto_settle_at": Var("AT"),
                "created_by": Var("U"),
            }),
        ],
        conclusion=Fact("event_opened", {
            "event_id": Var("E"),
            "title": Var("T"),
        }),
        conditions=[
            Clause.action("open_event", {
                "title": Var("T"),
                "created_by": Var("U"),
                "auto_settle_at": Var("AT"),
                "result": Var("E"),
            }),
        ],
    ))

    # ── 前向链：LLM 指令结算 ─────────────────────────

    rb.register(Rule(
        name="manual_settle_event",
        triggers=[
            Clause.action("settle_event", {
                "title": Var("T"),
            }),
        ],
        conclusion=Fact("event_settled", {
            "event_id": Var("E"),
            "title": Var("T"),
        }),
        conditions=[
            Clause.graph("find_event_by_title", {"title": Var("T")}, Var("Ev")),
            Clause.resolve(Var("Ev"), "id", Var("E")),
            Clause.action("settle_event", {
                "event_id": Var("E"),
                "title": Var("T"),
            }),
        ],
    ))

    # ── 前向链：取消事件 ──────────────────────────

    rb.register(Rule(
        name="cancel_event_rule",
        triggers=[
            Clause.action("cancel_event", {
                "title": Var("T"),
            }),
        ],
        conclusion=Fact("event_cancelled", {
            "event_id": Var("E"),
        }),
        conditions=[
            Clause.graph("find_event_by_title", {"title": Var("T")}, Var("Ev")),
            Clause.resolve(Var("Ev"), "id", Var("E")),
            Clause.action("cancel_event", {
                "event_id": Var("E"),
            }),
        ],
    ))