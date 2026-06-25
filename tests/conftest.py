"""测试共享 fixtures。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from kuzu import Database, Connection

from src.graph.schema import init_schema
from src.graph.data_point_repo import DataPointRepo
from src.engine.graph_searcher import GraphSearcher
from src.engine.inference import InferenceEngine
from src.engine.rules import get_rule_base


@pytest.fixture
def temp_db_conn():
    db = Database()
    conn = Connection(db)
    init_schema(conn)
    yield conn
    db.close()


@pytest.fixture
def searcher(temp_db_conn):
    return GraphSearcher(temp_db_conn)


@pytest.fixture
def rule_base():
    return get_rule_base()


@pytest.fixture
def engine(rule_base, searcher):
    return InferenceEngine(rule_base, searcher)


@pytest.fixture
def dp_repo(temp_db_conn):
    return DataPointRepo(temp_db_conn)


class MockTranslator:
    def __init__(self):
        self._nl_to_fl_response = None
        self._fl_to_nl_response = None
        self.nl_to_fl_calls = []
        self.fl_to_nl_calls = []

    def set_nl_to_fl(self, response: dict):
        self._nl_to_fl_response = response

    def set_fl_to_nl(self, response: str):
        self._fl_to_nl_response = response

    def nl_to_fl(self, content, context_texts, graph_ctx):
        self.nl_to_fl_calls.append((content, context_texts, graph_ctx))
        return self._nl_to_fl_response or {"intent": "query", "response": "mock reply", "instructions": None}

    def fl_to_nl(self, payload):
        self.fl_to_nl_calls.append(payload)
        if self._fl_to_nl_response:
            return self._fl_to_nl_response
        import json
        params = payload.get("params", {})
        return str(params.get("result", json.dumps(payload, ensure_ascii=False)))


@pytest.fixture
def mock_translator():
    return MockTranslator()


def _create_event(conn, eid, title, created_by):
    """CREATE Event — 用内联值绕过 kuzu TIMESTAMP binder 问题。"""
    conn.execute(f"""
        CREATE (e:Event {{
            id: '{eid}', title: '{title}', status: 'active',
            trigger_type: 'manual',
            auto_settle_at: timestamp('2026-06-25 22:00:00'),
            settled_at: timestamp('2026-06-25 22:00:00'), summary_dp_id: '',
            created_by: '{created_by}',
            created_at: timestamp('2026-06-25 22:00:00')
        }})
    """)


def _create_expense(dp_repo, eid, user, amt, note):
    dpid = f"dp_exp_{user}_{amt}_{note}"
    dp_repo.create("expense", user, {"amount": amt, "category": "餐饮", "note": note}, event_id=eid, dp_id=dpid)
    dp_repo.link_to_event(dpid, eid)


def _create_balance(dp_repo, eid, balances):
    dpid = f"dp_balance_{eid}"
    dp_repo.create("balance", "system", balances, event_id=eid, dp_id=dpid)
    dp_repo.link_to_event(dpid, eid)


def _create_debt(dp_repo, eid, debtor, creditor, amt):
    dpid = f"dp_debt_{debtor}_{creditor}_{eid}"
    dp_repo.create("debt", debtor, {"debtor": debtor, "creditor": creditor, "amount": amt}, event_id=eid, dp_id=dpid)
    dp_repo.link_to_event(dpid, eid)


@pytest.fixture
def hotpot_event(temp_db_conn, dp_repo):
    eid = "e_hotpot"
    _create_event(temp_db_conn, eid, "火锅局", "张三")
    for user, amt in [("张三", 150), ("李四", 80), ("王五", 100)]:
        _create_expense(dp_repo, eid, user, amt, "火锅局")
    return {"event_id": eid, "title": "火锅局", "users": ["张三", "李四", "王五"]}


@pytest.fixture
def hotpot_with_balance(hotpot_event, dp_repo):
    eid = hotpot_event["event_id"]
    _create_balance(dp_repo, eid, {
        "张三": {"paid": 150, "owe": 110, "net": 40},
        "李四": {"paid": 80, "owe": 110, "net": -30},
        "王五": {"paid": 100, "owe": 110, "net": -10},
    })
    _create_debt(dp_repo, eid, "李四", "张三", 30)
    _create_debt(dp_repo, eid, "王五", "张三", 10)
    return hotpot_event


@pytest.fixture
def multi_event_db(temp_db_conn, dp_repo):
    e1 = "e_hotpot"
    e2 = "e_ktv"
    _create_event(temp_db_conn, e1, "火锅局", "张三")
    _create_event(temp_db_conn, e2, "KTV局", "李四")

    for user, amt in [("张三", 150), ("李四", 80), ("王五", 100)]:
        _create_expense(dp_repo, e1, user, amt, "火锅局")
    _create_balance(dp_repo, e1, {
        "张三": {"paid": 150, "owe": 110, "net": 40},
        "李四": {"paid": 80, "owe": 110, "net": -30},
        "王五": {"paid": 100, "owe": 110, "net": -10},
    })
    _create_debt(dp_repo, e1, "李四", "张三", 30)
    _create_debt(dp_repo, e1, "王五", "张三", 10)

    for user, amt in [("李四", 50), ("张三", 15), ("赵六", 10)]:
        _create_expense(dp_repo, e2, user, amt, "KTV局")
    _create_balance(dp_repo, e2, {
        "张三": {"paid": 15, "owe": 25, "net": -10},
        "李四": {"paid": 50, "owe": 25, "net": 25},
        "赵六": {"paid": 10, "owe": 25, "net": -15},
    })
    _create_debt(dp_repo, e2, "张三", "李四", 10)
    _create_debt(dp_repo, e2, "赵六", "李四", 15)

    return {"hotpot": e1, "ktv": e2}