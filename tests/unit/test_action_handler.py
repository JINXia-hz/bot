"""测试 ActionHandler 各处理器：balance 计算、债务贪心分配、穿透还款。"""

import pytest
import json
from src.engine.rule_engine import Var, Binding
from src.engine.action_handler import ActionHandler


class MockEngine:
    """模拟 InferenceEngine，只收集 ops。"""
    def __init__(self):
        self._ops = []


class TestActionHandler:
    @pytest.fixture
    def handler(self):
        engine = MockEngine()
        return ActionHandler(engine)

    def test_compute_per_person_balance(self, handler):
        """3 人，已知 expense 和 per_person，验证净额计算正确。"""
        result_var = Var("R")
        success, new_binding = handler._h_compute_per_person_balance({
            "people": ["张三", "李四", "王五"],
            "expenses": [
                {"user_name": "张三", "payload": {"amount": 150}},
                {"user_name": "李四", "payload": {"amount": 80}},
                {"user_name": "王五", "payload": {"amount": 100}},
            ],
            "per_person": 110,
            "result": result_var,
        }, Binding())

        assert success is True
        assert new_binding is not None
        balances = new_binding.get(result_var)
        assert balances["张三"]["net"] == 40
        assert balances["李四"]["net"] == -30
        assert balances["王五"]["net"] == -10

    def test_decompose_debts(self, handler):
        """将 balance 拆解为债务 dp，验证贪心分配。"""
        balance_dp = {
            "payload": {
                "张三": {"paid": 150, "owe": 110, "net": 40},
                "李四": {"paid": 80, "owe": 110, "net": -30},
                "王五": {"paid": 100, "owe": 110, "net": -10},
            },
        }
        handler._h_decompose_debts({"event_id": "e1", "balance": balance_dp}, Binding())

        ops = handler.engine._ops
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        # 2 笔 debt：李四→张三 30, 王五→张三 10
        assert len(create_ops) == 2
        debtors = [o["payload"]["debtor"] for o in create_ops]
        assert "李四" in debtors
        assert "王五" in debtors

    def test_repay_with_overflow_fully(self, handler):
        """穿透还款：还 35 覆盖 30 的债务，剩余 5。"""
        debts = [
            {"event_id": "e1", "event_title": "火锅局", "amount": 30, "dp_id": "d1"},
            {"event_id": "e2", "event_title": "KTV局", "amount": 10, "dp_id": "d2"},
        ]
        handler._h_repay_with_overflow({
            "debts": debts,
            "total_amount": 35,
            "debtor": "李四",
            "creditor": "张三",
        }, Binding())

        ops = handler.engine._ops
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        # 2 个 debt_settled（30+5）+ 1 个 residual debt（5）= 3 个 create_dp
        # 实际上还第一笔30(全额) + 还第二笔5(部分) = 2个debt_settled + 1个residual debt
        dp_types = [o["dp_type"] for o in create_ops]
        assert "debt_settled" in dp_types
        assert len(create_ops) >= 2

    def test_repay_with_overflow_excess(self, handler):
        """还 50，全部清偿（30+10=40）后还剩 10，创建反向 credit。"""
        debts = [
            {"event_id": "e1", "event_title": "火锅局", "amount": 30, "dp_id": "d1"},
            {"event_id": "e2", "event_title": "KTV局", "amount": 10, "dp_id": "d2"},
        ]
        handler._h_repay_with_overflow({
            "debts": debts,
            "total_amount": 50,
            "debtor": "李四",
            "creditor": "张三",
        }, Binding())

        ops = handler.engine._ops
        create_ops = [o for o in ops if o["type"] == "create_dp"]
        dp_types = [o["dp_type"] for o in create_ops]
        # 应有 2 个 debt_settled + 1 个 debt（反向 credit）
        assert dp_types.count("debt_settled") == 2
        credit = [o for o in create_ops if o["payload"].get("status") == "overpaid"]
        assert len(credit) == 1
        assert credit[0]["payload"]["debtor"] == "张三"
        assert credit[0]["payload"]["creditor"] == "李四"

    def test_extract_debt_item(self, handler):
        """从债务列表中提取匹配的项。"""
        debts = [
            {"payload": {"debtor": "李四", "creditor": "张三", "amount": 30}},
            {"payload": {"debtor": "王五", "creditor": "张三", "amount": 10}},
        ]
        success, new_binding = handler._h_extract_debt_item({
            "debts": debts,
            "debtor": Var("D"),
            "creditor": Var("C"),
            "amount": Var("A"),
        }, Binding())
        assert success is True
        assert new_binding is not None
        assert new_binding.get(Var("D")) == "李四"
        assert new_binding.get(Var("C")) == "张三"
        assert new_binding.get(Var("A")) == 30