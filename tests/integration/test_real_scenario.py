"""真实场景模拟测试：完整 Orchestrator + 真实 kuzu DB + Mock LLM。

这些测试模拟 QQ 群聊中用户与 bot 的完整交互流程，验证数据是否正确写入/更新图数据库。
"""

import json
import os
import shutil
import tempfile

import pytest

from src.graph.connection import close_connection, init_database
from src.pipeline.orchestrator import Orchestrator


class MockTranslator:
    """模拟 LLM 翻译器，按顺序返回预设响应。"""

    def __init__(self):
        self.responses = []
        self.index = 0
        self.nl_to_fl_calls = []
        self.fl_to_nl_calls = []

    def set_responses(self, responses: list[dict]) -> None:
        self.responses = responses
        self.index = 0

    def nl_to_fl(self, content: str, context_texts: list[str], graph_ctx: str) -> dict:
        self.nl_to_fl_calls.append((content, context_texts, graph_ctx))
        if self.index < len(self.responses):
            resp = self.responses[self.index]
            self.index += 1
            return resp
        return {"intent": "query", "response": "mock default", "instructions": None}

    def fl_to_nl(self, payload: dict) -> str:
        self.fl_to_nl_calls.append(payload)
        return f"[MOCK] {payload}"


@pytest.fixture
def real_scenario_db():
    """每个测试使用独立的临时文件数据库。"""
    tmpdir = tempfile.mkdtemp(prefix="bot_scenario_")
    db_path = os.path.join(tmpdir, "test.kuzu")
    os.environ["KUZU_DB_PATH"] = db_path
    close_connection()
    init_database()
    yield
    close_connection()
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.environ.pop("KUZU_DB_PATH", None)


@pytest.fixture
def orchestrator(real_scenario_db):
    """创建 Orchestrator 并注入 MockTranslator。"""
    orch = Orchestrator()
    mock = MockTranslator()
    orch.translator = mock
    return orch, mock


class TestOpenEventAndRecordExpense:
    """场景：群聊约定活动 → 开事件 → 多人记账 → 余额/债务自动计算。"""

    def test_full_flow(self, orchestrator):
        orch, mock = orchestrator

        # ── 前置闲聊（无向消息）──────────────────────────
        orch.raw_msg.create("晚上一起吃火锅吧", "张三", "g1", is_directed=False)
        orch.raw_msg.create("好，我叫上王五", "李四", "g1", is_directed=False)

        # ── 1. 开事件 ────────────────────────────────────
        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "open_event",
                "params": {"title": "火锅局", "created_by": "张三"},
            }],
        }])
        reply = orch.on_directed_message("@bot 开一下火锅局事件", "张三", "g1")

        events = orch.event_repo.list_active()
        assert len(events) == 1, f"应创建 1 个活跃事件，实际 {len(events)}"
        event = events[0]
        assert event["title"] == "火锅局"
        # 未指定 auto_settle_at 时，默认不应等于创建时间（避免立即触发结算）
        assert event["auto_settle_at"] > event["created_at"]

        # ── 2. 张三记一笔支出 ─────────────────────────────
        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "record_expense",
                "params": {
                    "user_name": "张三",
                    "amount": 150,
                    "category": "餐饮",
                    "note": "火锅局",
                },
            }],
        }])
        reply = orch.on_directed_message("@bot 我付了150火锅局", "张三", "g1")

        dps = orch.dp_repo.get_event_datapoints(event["id"])
        expenses = [dp for dp in dps if dp["dp_type"] == "expense"]
        balances = [dp for dp in dps if dp["dp_type"] == "balance"]
        assert len(expenses) == 1, f"应创建 1 笔支出，实际 {len(expenses)}"
        assert expenses[0]["payload"]["amount"] == 150
        assert len(balances) == 1, f"应自动创建 1 个 balance，实际 {len(balances)}"

        # balance 中张三独自一人：已付 150，应付 150，净额 0
        bal_payload = balances[0]["payload"]
        assert bal_payload["张三"]["paid"] == 150
        assert bal_payload["张三"]["net"] == 0

        # ── 3. 李四记一笔支出 ─────────────────────────────
        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "record_expense",
                "params": {
                    "user_name": "李四",
                    "amount": 90,
                    "category": "餐饮",
                    "note": "火锅局",
                },
            }],
        }])
        reply = orch.on_directed_message("@bot 我付了90火锅局", "李四", "g1")

        dps = orch.dp_repo.get_event_datapoints(event["id"])
        expenses = [dp for dp in dps if dp["dp_type"] == "expense"]
        balances = [dp for dp in dps if dp["dp_type"] == "balance"]
        debts = [dp for dp in dps if dp["dp_type"] == "debt"]
        assert len(expenses) == 2, f"应共有 2 笔支出，实际 {len(expenses)}"
        assert len(balances) == 2, f"应共有 2 个 balance（每次重算新增），实际 {len(balances)}"

        # 最新 balance：总额 240，人均 120，张三多付 30，李四少付 30
        latest_bal = balances[-1]["payload"]
        assert latest_bal["张三"]["paid"] == 150
        assert latest_bal["李四"]["paid"] == 90
        assert latest_bal["张三"]["net"] == 30
        assert latest_bal["李四"]["net"] == -30

        # 应生成债务：李四欠张三 30
        assert len(debts) >= 1, f"应生成债务数据点，实际 {len(debts)}"
        debt = debts[-1]["payload"]
        assert debt["debtor"] == "李四"
        assert debt["creditor"] == "张三"
        assert debt["amount"] == 30

        # ── 4. 查询债务 ──────────────────────────────────
        mock.set_responses([{
            "intent": "query",
            "instructions": [{
                "query_type": "debt",
                "params": {"event": "火锅局"},
            }],
        }])
        reply = orch.on_directed_message("@bot 火锅局谁欠谁", "张三", "g1")
        assert "李四" in reply and "张三" in reply and "30" in reply

    def test_open_event_with_iso_auto_settle(self, orchestrator):
        """LLM 返回 ISO 字符串形式的 auto_settle_at 时应被正确解析。"""
        orch, mock = orchestrator

        from datetime import datetime
        future_iso = "2026-12-31T22:00:00"
        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "open_event",
                "params": {"title": "跨年聚会", "created_by": "张三", "auto_settle_at": future_iso},
            }],
        }])
        orch.on_directed_message("@bot 开跨年聚会事件", "张三", "g1")

        events = orch.event_repo.list_active()
        assert len(events) == 1
        assert events[0]["title"] == "跨年聚会"
        # kuzu 返回的是 datetime 对象
        assert isinstance(events[0]["auto_settle_at"], datetime)
        assert events[0]["auto_settle_at"].isoformat().startswith("2026-12-31")

    def test_mentions_are_added_as_participants(self, orchestrator):
        """消息中 @ 的人应被自动加入事件参与者。"""
        orch, mock = orchestrator

        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "open_event",
                "params": {"title": "早饭", "created_by": "理静"},
            }],
        }])
        orch.on_directed_message("@bot 我和 @1466626219 一起吃早饭", "理静", "g1", mentions=["1466626219"])

        event = orch.event_repo.list_active()[0]
        participants = orch.inference.searcher.execute("event_participants", {"event_id": event["id"]})
        assert "理静" in participants
        assert "1466626219" in participants

    def test_mentions_split_bill(self, orchestrator):
        """@ 多人后记账，应自动按参与者分摊并生成债务。"""
        orch, mock = orchestrator

        # 开事件并 @ 一个参与者
        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "open_event",
                "params": {"title": "早饭", "created_by": "理静"},
            }],
        }])
        orch.on_directed_message("@bot 我和 @1466626219 一起吃早饭", "理静", "g1", mentions=["1466626219"])

        event = orch.event_repo.list_active()[0]

        # 理静付 100
        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "record_expense",
                "params": {"user_name": "理静", "amount": 100, "category": "餐饮", "note": "早饭"},
            }],
        }])
        orch.on_directed_message("@bot 我付了100早饭", "理静", "g1", mentions=["1466626219"])

        # 应生成 2 个参与者，人均 50，1466626219 欠理静 50
        participants = orch.inference.searcher.execute("event_participants", {"event_id": event["id"]})
        assert set(participants) == {"理静", "1466626219"}

        dps = orch.dp_repo.get_event_datapoints(event["id"])
        debts = [dp for dp in dps if dp["dp_type"] == "debt"]
        assert len(debts) == 1
        assert debts[0]["payload"]["debtor"] == "1466626219"
        assert debts[0]["payload"]["creditor"] == "理静"
        assert debts[0]["payload"]["amount"] == 50


class TestRepayScenario:
    """场景：已有债务 → 还款 → 生成 debt_settled/residual。"""

    def test_repay_flow(self, orchestrator):
        orch, mock = orchestrator

        # 初始化：火锅局，张三付 150，李四付 90
        orch.raw_msg.create("火锅局走起", "张三", "g1", is_directed=False)
        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "open_event",
                "params": {"title": "火锅局", "created_by": "张三"},
            }],
        }])
        orch.on_directed_message("@bot 开火锅局", "张三", "g1")

        event = orch.event_repo.list_active()[0]

        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "record_expense",
                "params": {"user_name": "张三", "amount": 150, "category": "餐饮", "note": "火锅局"},
            }],
        }])
        orch.on_directed_message("@bot 我付150", "张三", "g1")

        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "record_expense",
                "params": {"user_name": "李四", "amount": 90, "category": "餐饮", "note": "火锅局"},
            }],
        }])
        orch.on_directed_message("@bot 我付90", "李四", "g1")

        # ── 李四还款 30 给张三 ────────────────────────────
        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "repay",
                "params": {"debtor": "李四", "creditor": "张三", "amount": 30},
            }],
        }])
        reply = orch.on_directed_message("@bot 我还张三30", "李四", "g1")

        dps = orch.dp_repo.get_event_datapoints(event["id"])
        settled = [dp for dp in dps if dp["dp_type"] == "debt_settled"]
        residual = [dp for dp in dps if dp["dp_type"] == "debt" and dp["payload"].get("status") == "partial"]
        assert len(settled) == 1, f"应生成 1 个 debt_settled，实际 {len(settled)}"
        assert settled[0]["payload"]["amount"] == 30
        assert len(residual) == 0, "刚好还清，不应有 residual debt"


class TestNoEventRecordExpense:
    """场景：没有匹配事件时，支出应作为独立数据点落地。"""

    def test_orphan_expense(self, orchestrator):
        orch, mock = orchestrator

        mock.set_responses([{
            "intent": "action",
            "instructions": [{
                "op": "record_expense",
                "params": {"user_name": "张三", "amount": 20, "category": "零食", "note": "便利店"},
            }],
        }])
        reply = orch.on_directed_message("@bot 记一笔20块零食", "张三", "g1")

        # 应创建不关联事件的 expense dp
        result = orch.dp_repo.execute(
            "MATCH (dp:DataPoint {dp_type: 'expense'}) RETURN dp",
        )
        expenses = []
        while result.has_next():
            node = dict(result.get_next()[0])
            node["payload"] = json.loads(node["payload"])
            expenses.append(node)
        assert len(expenses) == 1
        assert expenses[0]["payload"]["amount"] == 20
        assert expenses[0]["event_id"] == ""
