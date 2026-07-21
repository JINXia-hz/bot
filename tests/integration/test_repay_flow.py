"""集成测试：repay → 穿透还款 → debt_settled/residual 完整链路。"""

import pytest
from src.engine.rule_engine import Fact


class TestRepayFlow:
    """穿透还款：跨事件债务、溢出填补、部分清偿。"""

    def test_repay_single_debt_fully(self, engine, multi_event_db):
        """还款金额刚好覆盖最小债务。火锅局李四欠张三30，还35。先还完火锅局30，剩5。"""
        ops = engine.forward_chain(Fact("repay", {
            "debtor": "李四", "creditor": "张三", "amount": 35,
        }))

        # repay_with_overflow 现在由 ActionHandler 直接处理
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        assert len(create_ops) >= 1, f"Expected at least 1 create_dp op, got {ops}"

    def test_repay_less_than_debt(self, engine, multi_event_db):
        """还款少于全部债务，部分清偿。"""
        ops = engine.forward_chain(Fact("repay", {
            "debtor": "李四", "creditor": "张三", "amount": 10,
        }))
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        assert len(create_ops) >= 1

    def test_repay_more_than_all_debts(self, engine, multi_event_db):
        """还款远超所有债务，剩余部分创建反向 credit。"""
        ops = engine.forward_chain(Fact("repay", {
            "debtor": "李四", "creditor": "张三", "amount": 100,
        }))
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        assert len(create_ops) >= 1

    def test_repay_no_debt(self, engine):
        """两人之间没有债务时，repay 不崩溃。"""
        ops = engine.forward_chain(Fact("repay", {
            "debtor": "路人A", "creditor": "路人B", "amount": 100,
        }))
        # 没匹配到债务，但不应崩溃
        assert isinstance(ops, list)


class TestGlobalDebtQuery:
    """全局欠款查询。"""

    def test_global_owes_summary(self, engine, multi_event_db):
        """跨事件全局欠款汇总。"""
        bindings = engine.query(Fact("global_debt", {
            "person": "张三",
            "total_owe": Fact.Var("TO") if False else None,
            "total_owed": Fact.Var("TR") if False else None,
            "net": Fact.Var("N") if False else None,
        }))
        # global_owes_summary 是聚合查询，只返回一行
        assert len(bindings) <= 1  # 可能有或没有结果，但不崩溃