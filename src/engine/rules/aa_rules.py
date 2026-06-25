"""AA 分摊与债务查询规则。

查询规则（后向链）：
  - balance: 查询某个事件中每个人的应付/已付/净额
  - debt: 查询 A 欠 B 多少钱

所有计算基于图中已有的 balance 和 debt 数据点。
"""

from src.engine.rule_engine import Rule, Fact, Var, Clause


def register(rb) -> None:
    """注册 AA/债务规则。"""

    # ── 后向链：查询事件余额 ──────────────────────────

    rb.register(Rule(
        name="event_balance",
        conclusion=Fact("balance", {
            "event_title": Var("ET"),
            "payload": Var("P"),
            "event_id": Var("E"),
        }),
        conditions=[
            # 按标题搜索事件
            Clause.graph("find_event_like", {"title": Var("ET")}, Var("Ev")),
            # 获取事件 ID
            # (从 Ev 中提取 event_id，用 find_event_by_title 直接拿)
            Clause.graph("find_event_by_title", {"title": Var("ET")}, Var("Ev2")),
            # 查最新 balance
            Clause.graph("latest_balance_in_event", {"event_id": Var("E")}, Var("Bal")),
        ],
    ))

    # ── 后向链：查询债务（A欠B多少） ──────────────────────

    rb.register(Rule(
        name="debt_query",
        conclusion=Fact("debt", {
            "debtor": Var("A"),
            "creditor": Var("B"),
            "amount": Var("X"),
            "event": Var("ET"),
        }),
        conditions=[
            # 1. 找到事件
            Clause.graph("find_event_by_title", {"title": Var("ET")}, Var("Ev")),
            # 2. 获取债务数据点
            Clause.graph("debt_in_event", {"event_id": Var("E")}, Var("Debts")),
            # 3. 匹配具体的债务项
            Clause.action("extract_debt_item", {
                "debts": Var("Debts"),
                "debtor": Var("A"),
                "creditor": Var("B"),
                "amount": Var("X"),
            }),
        ],
    ))

    # ── 后向链：查询某人所有债务 ──────────────────────

    rb.register(Rule(
        name="person_debts",
        conclusion=Fact("person_debt", {
            "person": Var("P"),
            "counterparty": Var("C"),
            "amount": Var("X"),
            "event": Var("ET"),
        }),
        conditions=[
            Clause.graph("find_event_like", {"title": Var("ET")}, Var("Ev")),
            Clause.graph("debt_in_event", {"event_id": Var("E")}, Var("Debts")),
            Clause.action("extract_debt_item", {
                "debts": Var("Debts"),
                "debtor": Var("P"),
                "creditor": Var("C"),
                "amount": Var("X"),
            }),
        ],
    ))

    # ── 后向链：某人欠别人 ─────────────────────────

    rb.register(Rule(
        name="person_owes",
        conclusion=Fact("owes", {
            "person": Var("P"),
            "counterparty": Var("C"),
            "amount": Var("X"),
            "event": Var("ET"),
        }),
        conditions=[
            Clause.graph("find_event_like", {"title": Var("ET")}, Var("Ev")),
            Clause.graph("debt_in_event", {"event_id": Var("E")}, Var("Debts")),
            Clause.action("extract_debt_item", {
                "debts": Var("Debts"),
                "debtor": Var("P"),
                "creditor": Var("C"),
                "amount": Var("X"),
            }),
        ],
    ))

    # ── 后向链：某人被欠 ─────────────────────────

    rb.register(Rule(
        name="person_owed_by",
        conclusion=Fact("owed_by", {
            "person": Var("P"),
            "counterparty": Var("C"),
            "amount": Var("X"),
            "event": Var("ET"),
        }),
        conditions=[
            Clause.graph("find_event_like", {"title": Var("ET")}, Var("Ev")),
            Clause.graph("debt_in_event", {"event_id": Var("E")}, Var("Debts")),
            Clause.action("extract_debt_item", {
                "debts": Var("Debts"),
                "debtor": Var("C"),
                "creditor": Var("P"),
                "amount": Var("X"),
            }),
        ],
    ))

    # ── 后向链：全局欠款汇总 ─────────────────────────

    rb.register(Rule(
        name="global_debt_query",
        conclusion=Fact("global_debt", {
            "person": Var("P"),
            "total_owe": Var("TO"),
            "total_owed": Var("TR"),
            "net": Var("N"),
        }),
        conditions=[
            Clause.graph("global_owes_summary", {"user_name": Var("P")}, Var("Summary")),
        ],
    ))

    # ═══════════════════════════════════════════════
    # 前向链：穿透还款（跨事件，溢出填补）
    # ═══════════════════════════════════════════════

    rb.register(Rule(
        name="repay_triggers_settlement",
        triggers=[
            Clause.action("repay", {
                "debtor": Var("D"),
                "creditor": Var("C"),
                "amount": Var("A"),
            }),
        ],
        conclusion=Fact("repayment_done", {
            "debtor": Var("D"),
            "creditor": Var("C"),
            "amount": Var("A"),
        }),
        conditions=[
            # 1. 全局检索：D 欠 C 的所有债务
            Clause.graph("global_debts_between", {"debtor": Var("D"), "creditor": Var("C")}, Var("AllDebts")),
            # 2. 穿透还款（带回退）
            Clause.action("repay_with_overflow", {
                "debts": Var("AllDebts"),
                "total_amount": Var("A"),
                "debtor": Var("D"),
                "creditor": Var("C"),
            }),
        ],
    ))
