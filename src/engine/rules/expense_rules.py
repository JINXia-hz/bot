"""支出相关规则。

核心规则：record_expense 触发真正的 action：
  1. 从图中搜索匹配的事件
  2. 获取已有支出和参与者
  3. 创建 expense dp（原始凭证）
  4. 触发 balance 重算
"""

from src.engine.rule_engine import Rule, Fact, Var, Clause


def register(rb) -> None:
    """注册支出相关规则到 RuleBase。"""

    # ── 规则：记一笔支出（前向链触发） ────────────────────

    rb.register(Rule(
        name="expense_triggers_posting",
        triggers=[
            Clause.action("record_expense", {
                "user_name": Var("U"),
                "amount": Var("A"),
                "category": Var("C"),
                "note": Var("N"),
            }),
        ],
        conclusion=Fact("expense_posted", {
            "user_name": Var("U"),
            "amount": Var("A"),
            "event_id": Var("E"),
            "expense_dp_id": Var("DP1"),
            "balance_dp_id": Var("DP2"),
        }),
        conditions=[
            # 1. 搜索事件（从 note 中匹配）
            Clause.graph("find_event_like", {"title": Var("N")}, Var("Ev")),
            # 1.5 如果用户不在事件中，自动补入
            Clause.action("ensure_user_in_event", {
                "user_name": Var("U"),
                "event_id": Var("E"),
                "event_title": Var("N"),
            }),
            # 2. 创建 expense 数据点
            Clause.create_dp("expense", {
                "user_name": Var("U"),
                "payload": {"amount": Var("A"), "category": Var("C"), "note": Var("N")},
                "event_id": Var("E"),
            }, Var("DP1")),
            # 3. 关联事件
            Clause.link("BELONGS_TO", Var("DP1"), Var("E")),
            # 4. 触发 balance 重算
            Clause.rule("compute_event_balance", {
                "event_id": Var("E"),
                "trigger_dp_id": Var("DP1"),
                "balance_dp_id": Var("DP2"),
            }),
            # 5. 检查是否需要债务拆解
            Clause.rule("decompose_debts_if_balanced", {
                "event_id": Var("E"),
                "balance_dp_id": Var("DP2"),
            }),
        ],
    ))

    # ── 规则：没有匹配到事件时，直接创建 dp（不关联事件） ──

    rb.register(Rule(
        name="expense_without_event",
        triggers=[
            Clause.action("record_expense", {
                "user_name": Var("U"),
                "amount": Var("A"),
                "category": Var("C"),
                "note": Var("N"),
            }),
        ],
        conclusion=Fact("expense_posted", {
            "user_name": Var("U"),
            "amount": Var("A"),
            "expense_dp_id": Var("DP1"),
        }),
        conditions=[
            # 没有找到匹配事件
            Clause.not_(Clause.graph("find_event_like", {"title": Var("N")}, Var("Ev"))),
            # 创建支出 dp（不关联事件）
            Clause.create_dp("expense", {
                "user_name": Var("U"),
                "payload": {"amount": Var("A"), "category": Var("C"), "note": Var("N")},
            }, Var("DP1")),
        ],
    ))

    # ── 规则：计算事件余额 ───────────────────────────

    rb.register(Rule(
        name="compute_event_balance",
        conclusion=Fact("balance_computed", {
            "event_id": Var("E"),
            "trigger_dp_id": Var("DP_TRIGGER"),
            "balance_dp_id": Var("DP_BAL"),
        }),
        conditions=[
            # 1. 获取事件下所有支出
            Clause.graph("event_expenses", {"event_id": Var("E")}, Var("Expenses")),
            # 2. 获取事件参与者
            Clause.graph("event_participants", {"event_id": Var("E")}, Var("People")),
            # 3. 计算总和
            Clause.compute("sum", Var("Expenses"), "amount", Var("Total")),
            # 4. 计算人头
            Clause.compute("count", Var("People"), Var("N")),
            # 5. 计算人均
            Clause.compute("divide", Var("Total"), Var("N"), Var("Per")),
            # 6. 为每人算净额——用 action 子句让 python 层做循环
            Clause.action("compute_per_person_balance", {
                "people": Var("People"),
                "expenses": Var("Expenses"),
                "per_person": Var("Per"),
                "result": Var("Balances"),
            }),
            # 7. 创建 balance dp
            Clause.create_dp("balance", {
                "user_name": "system",
                "payload": Var("Balances"),
                "event_id": Var("E"),
            }, Var("DP_BAL")),
            # 8. 建立数据线
            Clause.link("DATA_LINE", Var("DP_TRIGGER"), Var("DP_BAL")),
        ],
    ))

    # ── 规则：如果 balance 已存在，拆解债务 ──────────

    rb.register(Rule(
        name="decompose_debts_if_balanced",
        conclusion=Fact("debts_decomposed", {
            "event_id": Var("E"),
            "balance_dp_id": Var("DP_BAL"),
        }),
        conditions=[
            Clause.graph("get_dp", {"dp_id": Var("DP_BAL")}, Var("BalDP")),
            Clause.action("decompose_debts", {
                "event_id": Var("E"),
                "balance": Var("BalDP"),
            }),
        ],
    ))