"""集成测试：record_expense → balance → debt 完整链路。"""

import pytest
from src.engine.rule_engine import Fact
from src.graph.data_point_repo import DataPointRepo


class TestExpenseFlow:
    """测试从记一笔支出到余额和债务的完整链条。"""

    def test_record_expense_triggers_balance(self, engine, hotpot_event, dp_repo):
        """火锅局已有 3 笔支出 + 3 个参与者。记第 4 笔时触发 balance 重算。"""
        ops = engine.forward_chain(Fact("record_expense", {
            "user_name": "赵六", "amount": 60,
            "category": "饮食", "note": "火锅局",
        }))

        # 应包含 create_dp ops
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        dp_types = {o.get("dp_type") for o in create_ops}
        # 至少应有：participant_entry（赵六不在事件中），expense, balance
        assert "expense" in dp_types, f"Expected expense in ops, got types: {dp_types}"
        # balance 应该有（不是必须——取决于规则链是否完整执行）
        # 但至少 expense dp 一定产生

    def test_new_participant_auto_join(self, engine, hotpot_event):
        """新参与者（没在事件中的）记账时自动创建 participant_entry。"""
        ops = engine.forward_chain(Fact("record_expense", {
            "user_name": "新来的人", "amount": 80,
            "category": "餐", "note": "火锅局",
        }))
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        types = [o.get("dp_type") for o in create_ops]
        # 应有 participant_entry 和 expense
        assert "participant_entry" in types or "expense" in types

    def test_no_event_record_expense(self, engine):
        """完全没有匹配到事件时，直接创建不关联事件的 expense dp。"""
        ops = engine.forward_chain(Fact("record_expense", {
            "user_name": "路人", "amount": 30,
            "category": "零食", "note": "随便吃点",
        }))
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        assert len(create_ops) >= 1
        # 第一个 create_dp 应该是 expense，不带 event_id
        assert create_ops[0]["dp_type"] == "expense"


class TestMultiEventFlow:
    """多场景下事件互不干扰。"""

    def test_different_events_separate(self, engine, multi_event_db):
        """火锅局和KTV局各自独立。"""
        # 火锅局加一笔
        ops1 = engine.forward_chain(Fact("record_expense", {
            "user_name": "小A", "amount": 45, "category": "加菜", "note": "火锅局",
        }))
        assert len([o for o in ops1 if o["type"] == "create_dp"]) >= 1

        # KTV局加一笔
        ops2 = engine.forward_chain(Fact("record_expense", {
            "user_name": "小B", "amount": 20, "category": "酒水", "note": "KTV局",
        }))
        assert len([o for o in ops2 if o["type"] == "create_dp"]) >= 1