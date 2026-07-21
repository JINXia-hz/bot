"""测试推理引擎：后向链查询 + 前向链触发。"""

import pytest
from src.engine.rule_engine import Fact, Var, Binding


class TestForwardChain:
    """前向链：Fact 注入 → 规则匹配 → Ops 产出。"""

    def test_record_expense_no_matching_event(self, engine):
        """无匹配事件时，只创建 expense dp。"""
        ops = engine.forward_chain(Fact("record_expense", {
            "user_name": "测试用户", "amount": 50,
            "category": "测试", "note": "不存在的活动",
        }))
        # 应至少有一个 create_dp（expense）
        create_dps = [o for o in ops if o["type"] == "create_dp"]
        assert len(create_dps) >= 1
        assert create_dps[0]["dp_type"] == "expense"

    def test_repay_with_debts(self, engine, multi_event_db):
        """穿透还款：有了跨事件债务，repay 应生成 debt_settled/residual debt。"""
        ops = engine.forward_chain(Fact("repay", {
            "debtor": "李四", "creditor": "张三", "amount": 35,
        }))
        # repay_with_overflow 现在由 ActionHandler 直接处理，产生具体 create_dp/link
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        assert len(create_ops) >= 1, f"Expected at least 1 create_dp op, got {ops}"


class TestBackwardChain:
    """后向链：goal → 规则证明 → 变量绑定。"""

    def test_query_debt_with_data(self, engine, hotpot_with_balance):
        """预填充火锅局数据后，查询债务应返回绑定。"""
        # 手动验证前两步
        searcher = engine.searcher
        ev = searcher.execute("find_event_by_title", {"title": "火锅局"})
        assert ev is not None, "find_event_by_title returned None"
        eid = ev.get("id", "")
        assert eid == "e_hotpot", f"Expected e_hotpot, got {eid}"
        debts = searcher.execute("debt_in_event", {"event_id": eid})
        assert len(debts) == 2, f"Expected 2 debts, got {len(debts)}"

        bindings = engine.query(Fact("debt", {
            "debtor": Var("A"),
            "creditor": Var("B"),
            "amount": Var("X"),
            "event": "火锅局",
        }))
        assert len(bindings) >= 1, f"Expected at least 1 debt binding, got {len(bindings)}"

    def test_query_owes(self, engine, hotpot_with_balance):
        """张三欠别人？火锅局里别人欠张三，张三不欠别人。"""
        bindings = engine.query(Fact("owes", {
            "person": "张三", "event": "火锅局",
            "counterparty": Var("C"), "amount": Var("X"),
        }))
        assert len(bindings) == 0

    def test_query_owed_by(self, engine, hotpot_with_balance):
        """别人欠张三？extract_debt_item 返回第一个匹配。"""
        bindings = engine.query(Fact("owed_by", {
            "person": "张三", "event": "火锅局",
            "counterparty": Var("C"), "amount": Var("X"),
        }))
        assert len(bindings) >= 1, f"Expected at least 1, got {len(bindings)}"

    def test_query_balance(self, engine, hotpot_with_balance):
        """查询火锅局余额。"""
        bindings = engine.query(Fact("balance", {
            "event_title": "火锅局",
            "payload": Var("P"),
            "event_id": Var("E"),
        }))
        assert len(bindings) >= 1, f"Expected at least 1, got {len(bindings)}"

    def test_query_empty_db(self, engine):
        """空数据库查询债务应无结果。"""
        bindings = engine.query(Fact("debt", {
            "debtor": Var("A"), "creditor": Var("B"),
            "amount": Var("X"), "event": Var("E"),
        }))
        assert len(bindings) == 0


class TestEngineInternals:
    def test_forward_chain_empty(self, engine):
        """空 forward_chain（timed）不崩溃。"""
        ops = engine.forward_chain()
        assert isinstance(ops, list)

    def test_match_triggers(self, engine):
        """验证 trigger 匹配正确。"""
        matched = engine._match_triggers(Fact("record_expense", {
            "user_name": "张三", "amount": 100,
            "category": "餐", "note": "火锅局",
        }))
        # 应有 2 条（expense_triggers_posting + expense_without_event）
        assert len(matched) == 2

    def test_is_timed_rule(self, engine, rule_base):
        """验证 timed 规则检测。"""
        timed_rules = [r for r in rule_base.get_all_rules() if engine._is_timed_rule(r)]
        assert len(timed_rules) >= 1  # activate_due_reservation, auto_settle_due_events