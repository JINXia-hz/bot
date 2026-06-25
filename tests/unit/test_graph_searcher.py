"""测试 GraphSearcher 各命名查询的正确性。"""

import pytest
from src.engine.graph_searcher import GraphSearcher


class TestEventQueries:
    def test_find_event_by_title(self, searcher, hotpot_event):
        result = searcher.execute("find_event_by_title", {"title": "火锅局"})
        assert result is not None
        assert result["title"] == "火锅局"

    def test_find_event_by_title_missing(self, searcher):
        result = searcher.execute("find_event_by_title", {"title": "不存在"})
        assert result is None

    def test_find_event_like(self, searcher, hotpot_event):
        result = searcher.execute("find_event_like", {"title": "火锅"})
        assert result is not None

    def test_active_events(self, searcher, hotpot_event):
        result = searcher.execute("active_events", {})
        assert len(result) >= 1

    def test_event_participants(self, searcher, hotpot_event):
        result = searcher.execute("event_participants", {"event_id": "e_hotpot"})
        assert "张三" in result
        assert len(result) == 3

    def test_event_expenses(self, searcher, hotpot_event):
        result = searcher.execute("event_expenses", {"event_id": "e_hotpot"})
        assert len(result) == 3


class TestDataPointQueries:
    def test_user_datapoints(self, searcher, hotpot_event):
        result = searcher.execute("user_datapoints", {"user_name": "张三", "limit": 5})
        assert len(result) >= 1

    def test_user_latest_dp(self, searcher, hotpot_event):
        result = searcher.execute("user_latest_dp", {"user_name": "张三"})
        assert result is not None
        assert result["dp_type"] == "expense"


class TestMultiEventQueries:
    def test_global_debts_between(self, searcher, multi_event_db):
        result = searcher.execute("global_debts_between", {"debtor": "李四", "creditor": "张三"})
        assert len(result) >= 1
        # 火锅局李四欠张三30
        assert result[0]["amount"] == 30

    def test_global_owes_summary(self, searcher, multi_event_db):
        result = searcher.execute("global_owes_summary", {"user_name": "张三"})
        assert result is not None
        assert result["total_owed"] >= 30  # 李四+王五欠张三共40

    def test_events_for_person(self, searcher, multi_event_db):
        result = searcher.execute("events_for_person", {"user_name": "张三"})
        assert len(result) == 2  # 火锅局 + KTV局

    def test_user_in_event(self, searcher, multi_event_db):
        result = searcher.execute("user_in_event", {"user_name": "张三", "event_id": "e_hotpot"})
        assert result is not None

    def test_user_not_in_event(self, searcher, multi_event_db):
        result = searcher.execute("user_in_event", {"user_name": "路人X", "event_id": "e_hotpot"})
        assert result is None