"""测试 Rule/Clause/Fact/Var/Binding DSL 的正确性。"""

import pytest
from src.engine.rule_engine import Rule, Fact, Var, Clause, RuleBase, Binding


class TestVar:
    def test_var_creation(self):
        v = Var("A")
        assert v.name == "A"
        assert repr(v) == "?A"

    def test_var_equality(self):
        assert Var("A") == Var("A")
        assert Var("A") != Var("B")

    def test_var_immutable(self):
        s = {Var("A"), Var("B"), Var("A")}
        assert len(s) == 2


class TestFact:
    def test_fact_with_values(self):
        f = Fact("expense", {"user": "张三", "amount": 150})
        assert f.predicate == "expense"
        assert f.args["user"] == "张三"

    def test_fact_with_vars(self):
        f = Fact("debt", {"debtor": Var("A"), "amount": Var("X")})
        assert isinstance(f.args["debtor"], Var)
        assert f.args["debtor"].name == "A"

    def test_fact_repr(self):
        f = Fact("foo", {"x": 1})
        assert "foo(x=1)" in repr(f)


class TestBinding:
    def test_empty_binding(self):
        b = Binding()
        assert b.get("hello") == "hello"
        assert b.get(Var("A")).name == "A"

    def test_bind_and_get(self):
        b = Binding().extend(Var("A"), "张三")
        assert b is not None
        assert b.get(Var("A")) == "张三"

    def test_conflict(self):
        b = Binding({Var("A"): "张三"})
        b2 = b.extend(Var("A"), "李四")
        assert b2 is None  # 冲突

    def test_extend_chain(self):
        b = Binding()
        b = b.extend(Var("A"), 1)
        b = b.extend(Var("B"), 2)
        assert b.get(Var("A")) == 1
        assert b.get(Var("B")) == 2

    def test_resolve(self):
        b = Binding({Var("E"): "e1"})
        resolved = b.resolve({"event_id": Var("E"), "title": "test"})
        assert resolved == {"event_id": "e1", "title": "test"}


class TestRuleBase:
    def test_register_and_find_by_conclusion(self):
        rb = RuleBase()
        r = Rule(
            name="test_rule",
            conclusion=Fact("my_pred", {"x": Var("X")}),
        )
        rb.register(r)
        found = rb.get_by_conclusion("my_pred")
        assert len(found) == 1
        assert found[0].name == "test_rule"

    def test_register_and_find_by_trigger(self):
        rb = RuleBase()
        r = Rule(
            name="triggered_rule",
            conclusion=Fact("result", {}),
            triggers=[Clause.action("my_action", {"user": Var("U")})],
        )
        rb.register(r)
        found = rb.get_by_trigger("my_action")
        assert len(found) == 1
        assert found[0].name == "triggered_rule"

    def test_get_by_conclusion_missing(self):
        rb = RuleBase()
        assert rb.get_by_conclusion("nonexistent") == []

    def test_get_by_trigger_missing(self):
        rb = RuleBase()
        assert rb.get_by_trigger("no_such_trigger") == []


class TestClause:
    def test_graph_clause(self):
        c = Clause.graph("find_event", {"title": Var("T")}, Var("E"))
        assert c["type"] == "graph"
        assert c["query"] == "find_event"
        assert isinstance(c["result_var"], Var)

    def test_rule_clause(self):
        c = Clause.rule("balance", {"event_id": Var("E")})
        assert c["type"] == "rule"
        assert c["rule_name"] == "balance"

    def test_builtin_clause(self):
        c = Clause.builtin("eq", Var("A"), Var("B"))
        assert c["type"] == "builtin"
        assert c["op"] == "eq"

    def test_compute_clause(self):
        c = Clause.compute("sum", Var("L"), "amount", Var("R"))
        assert c["type"] == "compute"
        assert c["op"] == "sum"

    def test_not_clause(self):
        inner = Clause.graph("find_event", {}, Var("E"))
        c = Clause.not_(inner)
        assert c["type"] == "not_"

    def test_create_dp_clause(self):
        c = Clause.create_dp("expense", {"user_name": "张三"}, Var("DP1"))
        assert c["type"] == "create_dp"
        assert c["dp_type"] == "expense"

    def test_link_clause(self):
        c = Clause.link("BELONGS_TO", Var("DP"), Var("E"))
        assert c["type"] == "link"
        assert c["rel_type"] == "BELONGS_TO"

    def test_action_clause(self):
        c = Clause.action("settle_event", {"event_id": Var("E")})
        assert c["type"] == "action"
        assert c["op"] == "settle_event"


class TestRule:
    def test_rule_creation(self):
        r = Rule(
            name="my_rule",
            conclusion=Fact("outcome", {"x": Var("X")}),
            conditions=[Clause.graph("q", {}, Var("X"))],
            triggers=[Clause.action("start", {})],
        )
        assert r.name == "my_rule"
        assert len(r.conditions) == 1
        assert len(r.triggers) == 1

    def test_rule_repr(self):
        r = Rule(name="r", conclusion=Fact("p", {"x": 1}))
        assert "Rule(r:" in repr(r)