"""Prolog 风格规则引擎 DSL。

声明式规则定义：Rule(conclusion, conditions, triggers)
- Clause.graph():  图查询子句——从 kuzu 搜事实
- Clause.rule():   规则引用子句——链式推理
- Clause.builtin(): 内置谓词——比较、数学、集合
- Clause.compute(): 计算子句——sum/avg/count/divide/subtract
- Clause.not_():    否定子句
- Clause.create_dp(): 创建新数据点
- Clause.link():   创建图关系
- Clause.action(): 执行操作指令
"""

from dataclasses import dataclass, field
from typing import Any, Callable


# ═══════════════════════════════════════════════════
# 逻辑变量
# ═══════════════════════════════════════════════════

@dataclass(frozen=True)
class Var:
    """逻辑变量，在推理过程中被绑定到具体值。

    Var("A") 表示一个名为 A 的变量，query() 尝试为其找到具体值。
    """
    name: str

    def __repr__(self):
        return f"?{self.name}"

    def __hash__(self):
        return hash(("Var", self.name))

    def __eq__(self, other):
        if isinstance(other, Var):
            return self.name == other.name
        return NotImplemented


# ═══════════════════════════════════════════════════
# 事实
# ═══════════════════════════════════════════════════

@dataclass(frozen=True)
class Fact:
    """一个结构化事实（类似 Prolog 中的 term）。

    Fact("expense_paid", {"user": "张三", "amount": 150})
    表示：张三支付了150。

    字段值可以是具体值或 Var，Var 在推理中被绑定。
    """
    predicate: str
    args: dict[str, Any] = field(default_factory=dict)

    def __repr__(self):
        args_str = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.predicate}({args_str})"


# ═══════════════════════════════════════════════════
# 绑定（环境）
# ═══════════════════════════════════════════════════

@dataclass
class Binding:
    """推理过程中的变量绑定环境。

    {Var("A"): "张三", Var("X"): 150.0}
    """
    mapping: dict[Var, Any] = field(default_factory=dict)

    def get(self, var_or_value: Any) -> Any:
        """如果是 Var 且在绑定中，返回绑定值；否则返回原始值。"""
        if isinstance(var_or_value, Var):
            return self.mapping.get(var_or_value, var_or_value)
        return var_or_value

    def resolve(self, data: dict[str, Any]) -> dict[str, Any]:
        """将 dict 中的所有 Var 替换为绑定值。"""
        return {k: self.get(v) for k, v in data.items()}

    def extend(self, var: Var, value: Any) -> "Binding | None":
        """返回新 Binding。若已有不同绑定则返回 None（统一失败）。"""
        if var in self.mapping:
            return self if self.mapping[var] == value else None
        new_mapping = dict(self.mapping)
        new_mapping[var] = value
        return Binding(new_mapping)

    def copy(self) -> "Binding":
        return Binding(dict(self.mapping))


# ═══════════════════════════════════════════════════
# 子句
# ═══════════════════════════════════════════════════

class Clause:
    """规则条件中的一个子句。

    类型：
      - "graph":     图查询，调用 FactProvider
      - "rule":      引用另一条规则
      - "builtin":   内置谓词（eq, neq, lt, gt, member, not_）
      - "compute":   计算（sum, count, avg, divide, subtract, min_of, abs）
      - "create_dp": 创建新数据点
      - "link":      创建图关系
      - "action":    执行操作
    """

    @staticmethod
    def graph(query_name: str, params: dict[str, Any], result_var: Var):
        """图查询子句：调用 FactProvider 执行命名查询。

        Args:
            query_name: 查询名称（由 FactProvider 实现）
            params: 查询参数（值或 Var）
            result_var: 结果绑定到的变量
        """
        return {
            "type": "graph",
            "query": query_name,
            "params": params,
            "result_var": result_var,
        }

    @staticmethod
    def rule(rule_name: str, binds: dict[str, Any]):
        """引用另一条规则（规则链式推理）。

        Args:
            rule_name: 被引用的规则名
            binds: 参数绑定
        """
        return {
            "type": "rule",
            "rule_name": rule_name,
            "binds": binds,
        }

    @staticmethod
    def builtin(op: str, *args):
        """内置谓词。

        支持：eq(v1,v2), neq(v1,v2), lt(v1,v2), gt(v1,v2),
              lte(v1,v2), gte(v1,v2), member(v,list),
              not_(clause)
        """
        return {
            "type": "builtin",
            "op": op,
            "args": list(args),
        }

    @staticmethod
    def compute(op: str, *args_and_result):
        """计算子句。

        支持：
          compute("sum", list_var, field, result_var)
          compute("count", list_var, result_var)
          compute("avg", list_var, field, result_var)
          compute("divide", a, b, result_var)
          compute("subtract", a, b, result_var)
          compute("add", a, b, result_var)
          compute("multiply", a, b, result_var)
          compute("min_of", [a,b,...], result_var)
          compute("abs", a, result_var)
          compute("len", list_var, result_var)
        """
        return {
            "type": "compute",
            "op": op,
            "args": list(args_and_result),
        }

    @staticmethod
    def not_(clause: dict):
        """否定子句：当子 clause 无解时成功。"""
        return {
            "type": "not_",
            "clause": clause,
        }

    @staticmethod
    def create_dp(dp_type: str, params: dict[str, Any], result_var: Var | None = None):
        """创建新 DataPoint 节点。

        Args:
            dp_type: 数据点类型
            params: {user_name, payload, event_id, ...}（值或 Var）
            result_var: 新节点 ID 绑定到的变量
        """
        clause = {
            "type": "create_dp",
            "dp_type": dp_type,
            "params": params,
        }
        if result_var is not None:
            clause["result_var"] = result_var
        return clause

    @staticmethod
    def link(rel_type: str, from_var: Var, to_var: Var, props: dict[str, Any] | None = None):
        """创建图关系。

        Args:
            rel_type: 关系类型（BELONGS_TO, DATA_LINE, PRODUCED, CONSUMED, ...）
            from_var: 源节点 ID
            to_var: 目标节点 ID
            props: 关系属性（可选）
        """
        clause = {
            "type": "link",
            "rel_type": rel_type,
            "from_var": from_var,
            "to_var": to_var,
        }
        if props:
            clause["props"] = props
        return clause

    @staticmethod
    def action(op: str, params: dict[str, Any]):
        """执行操作指令。

        与 Clause.graph 不同，这是告诉 Executor 去"做事"，
        而非从图中"查询"。

        Args:
            op: 操作名（activate_event, send_message, ...）
            params: 操作参数
        """
        return {
            "type": "action",
            "op": op,
            "params": params,
        }


# ═══════════════════════════════════════════════════
# 规则
# ═══════════════════════════════════════════════════

@dataclass
class Rule:
    """一条声明式推理规则。

    格式：结论 :- 条件1, 条件2, ...

    前向链：当 triggers 中的 action 发生时自动触发。
    后向链：当 query 以结论 predicate 为目标时被搜索。

    示例：
        Rule(
            name="debt",
            conclusion=Fact("debt", {"debtor": Var("A"), "creditor": Var("B"), "amount": Var("X")}),
            conditions=[
                Clause.rule("balance", {"event": Var("E")}),
                Clause.builtin("member", Var("D"), Var("Balance")),
                Clause.builtin("lt", Var("D").field("net"), 0),
                ...
            ],
        )
    """
    name: str
    conclusion: Fact
    conditions: list[dict] = field(default_factory=list)
    triggers: list[dict] = field(default_factory=list)

    def __repr__(self):
        return f"Rule({self.name}: {self.conclusion})"


# ═══════════════════════════════════════════════════
# 规则库
# ═══════════════════════════════════════════════════

@dataclass
class RuleBase:
    """规则集合。

    按 predicate 名和 action 触发条件建立索引，
    以便推理引擎快速查找。
    """

    rules: list[Rule] = field(default_factory=list)

    # 按结论 predicate 索引（后向链）
    _by_conclusion: dict[str, list[Rule]] = field(default_factory=dict, repr=False)

    # 按触发 action op 索引（前向链）
    _by_trigger: dict[str, list[Rule]] = field(default_factory=dict, repr=False)

    def register(self, rule: Rule) -> None:
        """注册一条规则。"""
        self.rules.append(rule)

        pred = rule.conclusion.predicate
        if pred not in self._by_conclusion:
            self._by_conclusion[pred] = []
        self._by_conclusion[pred].append(rule)

        for trigger in rule.triggers:
            if trigger.get("type") == "action":
                op = trigger.get("op", "")
                if op not in self._by_trigger:
                    self._by_trigger[op] = []
                self._by_trigger[op].append(rule)

    def get_by_conclusion(self, predicate: str) -> list["Rule"]:
        """按结论 predicate 查找规则（后向链）。"""
        return self._by_conclusion.get(predicate, [])

    def get_by_trigger(self, action_op: str) -> list["Rule"]:
        """按触发 action op 查找规则（前向链）。"""
        return self._by_trigger.get(action_op, [])

    def get_all_rules(self) -> list["Rule"]:
        return list(self.rules)