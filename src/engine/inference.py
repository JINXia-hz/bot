"""后向链 + 前向链推理引擎。

后向链（backward_chain）：以 goal Fact 为目标，递归证明规则条件，返回变量绑定。
前向链（forward_chain）：接收新事实（如 LLM 产出的 FL），匹配规则 triggers，执行条件链，产出新事实和操作指令。

操作指令由 InferenceEngine 收集后交给 Orchestrator 落地到图数据库。
"""

import json
import logging
from typing import Any

from src.engine.rule_engine import (
    Rule, RuleBase, Fact, Var, Binding, Clause,
)
from src.engine.graph_searcher import GraphSearcher

logger = logging.getLogger(__name__)


class InferenceEngine:
    """规则推理引擎。

    不直接操作图写操作。所有写入通过收集的 `ops` 列表返回给 Orchestrator 落地。

    ops 列表元素格式：
      - {"type": "create_dp", "dp_type": str, "user_name": str, "payload": dict, "event_id": str | None}
      - {"type": "link", "rel_type": str, "from_id": str, "to_id": str, "props": dict | None}
      - {"type": "settle_event", "event_id": str}
      - {"type": "cancel_event", "event_id": str}
      - {"type": "create_event", "title": str, "created_by": str, "trigger_type": str, "auto_settle_at": str | None}
      - {"type": "create_fl", "payload": dict, "reply_type": str, "schedule_at": str | None}
    """

    def __init__(self, rule_base: RuleBase, searcher: GraphSearcher):
        self.rule_base = rule_base
        self.searcher = searcher
        self._ops: list[dict] = []
        # ActionHandler 延迟初始化避免循环导入
        self._action_handler = None

    @property
    def action_handler(self):
        if self._action_handler is None:
            from src.engine.action_handler import ActionHandler
            self._action_handler = ActionHandler(self)
        return self._action_handler

    def collect_ops(self) -> list[dict]:
        """获取收集到的所有操作指令，并清空。"""
        ops = list(self._ops)
        self._ops.clear()
        return ops

    # ═══════════════════════════════════════════════
    # 后向链：查询推理
    # ═══════════════════════════════════════════════

    def query(self, goal: Fact) -> list[Binding]:
        """后向链推理：给定 goal，返回所有使 goal 成立的绑定。

        例：query(Fact("debt", {"debtor": Var("A"), "creditor": Var("B")}))
        → [{Var("A"): "张三", Var("B"): "李四", ...}, ...]

        Args:
            goal: 查询目标事实（包含 Var 表示待求解）

        Returns:
            所有变量绑定解的列表
        """
        logger.info(f"[inference] 后向链查询: {goal}")
        bindings = self._prove(goal, Binding())
        logger.info(f"[inference] 查询结果: {len(bindings)} 个解")
        return bindings

    def _prove(self, goal: Fact, binding: Binding) -> list[Binding]:
        """证明 goal，从当前 binding 出发返回所有扩展 binding。"""
        all_bindings: list[Binding] = []

        # 1. 图事实查询——直接从 kuzu 查
        graph_bindings = self._prove_graph_fact(goal, binding)
        all_bindings.extend(graph_bindings)

        # 2. 规则链推理——找结论匹配的规则
        for rule in self.rule_base.get_by_conclusion(goal.predicate):
            rule_bindings = self._prove_rule(rule, goal, binding)
            all_bindings.extend(rule_bindings)

        return all_bindings

    def _prove_graph_fact(self, goal: Fact, binding: Binding) -> list[Binding]:
        """尝试将 goal 作为图查询来证明。

        通过 Clause.graph 风格的调用处理：将 goal 映射到对应的 query_name。
        图查询 -> 事实的映射由 `_goal_to_graph_query` 处理。
        """
        query_name, params = self._goal_to_graph_query(goal, binding)
        if query_name is None:
            return []

        try:
            result = self.searcher.execute(query_name, params)
        except Exception as e:
            logger.warning(f"[inference] 图查询失败 {query_name}: {e}")
            return []

        if result is None or (isinstance(result, list) and len(result) == 0):
            return []

        # 单个结果 vs 列表
        items = result if isinstance(result, list) else [result]

        bindings_out = []
        for item in items:
            b = binding.copy()
            success = self._unify_goal_with_item(goal, item, b)
            if success:
                bindings_out.append(b)

        return bindings_out

    def _goal_to_graph_query(self, goal: Fact, binding: Binding) -> tuple[str | None, dict]:
        """将 goal predicate 映射到 graph_searcher 的 query_name 和 params。

        根据 predicate 名称推断对应的图查询。
        """
        predicate = goal.predicate
        resolved = binding.resolve(goal.args)

        mapping = {
            "find_event": ("find_event_by_title", {"title": resolved.get("title", "")}),
            "find_event_like": ("find_event_like", {"title": resolved.get("title", "")}),
            "active_event": ("active_events", {}),
            "user_active_event": ("user_active_events", {"user_name": resolved.get("user_name", "")}),
            "event_expense": ("event_expenses", {"event_id": resolved.get("event_id", "")}),
            "event_datapoint": ("event_datapoints", {"event_id": resolved.get("event_id", "")}),
            "event_participant": ("event_participants", {"event_id": resolved.get("event_id", "")}),
            "user_datapoint": ("user_datapoints", {
                "user_name": resolved.get("user_name", ""),
                "limit": resolved.get("limit", 20),
            }),
            "user_latest_dp": ("user_latest_dp", {"user_name": resolved.get("user_name", "")}),
            "latest_balance": ("latest_balance_in_event", {"event_id": resolved.get("event_id", "")}),
            "debt_in_event": ("debt_dps_for_event", {"event_id": resolved.get("event_id", "")}),
            "pending_reservation": ("pending_reservations", {}),
            "reservation_due": ("reservation_due", {}),
            "event_due_settle": ("event_due_for_settle", {}),
            "event_with_new_expenses": ("active_events_with_new_expenses", {}),
            "get_dp": ("get_datapoint", {"dp_id": resolved.get("dp_id", "")}),
            "get_event": ("get_event", {"event_id": resolved.get("event_id", "")}),
            "user_data_line_chain": ("user_data_line_chain", {
                "user_name": resolved.get("user_name", ""),
                "limit": resolved.get("limit", 30),
            }),
            "event_data_line": ("event_data_lines", {"event_id": resolved.get("event_id", "")}),
        }

        return mapping.get(predicate, (None, {}))

    def _unify_goal_with_item(self, goal: Fact, item: dict, binding: Binding) -> bool:
        """将 goal 的 args 与图查询返回的 item 进行统一。

        例如 goal.args = {"event_id": Var("E"), "title": "火锅局"}
            item = {"id": "e1", "title": "火锅局", "status": "active"}
        则 binding 中 Var("E") → "e1"
        """
        for key, expected in goal.args.items():
            actual = item.get(key)
            if actual is None:
                continue

            if isinstance(expected, Var):
                extended = binding.extend(expected, actual)
                if extended is None:
                    return False
                # 原地更新 binding（这里用 binding.mapping 直接更新）
                binding.mapping.update(extended.mapping)
            elif expected != actual:
                return False

        return True

    def _prove_rule(self, rule: Rule, goal: Fact, binding: Binding) -> list[Binding]:
        """证明一条规则。

        1. 将规则结论与 goal 统一，提取初始绑定
        2. 逐一证明每条 condition
        3. 返回所有成功绑定
        """
        # 统一结论与 goal
        b = self._unify_conclusion(rule.conclusion, goal, binding)
        if b is None:
            return []

        # 证明条件
        bindings = [b]
        for clause_dict in rule.conditions:
            new_bindings = []
            for current_b in bindings:
                results = self._prove_clause(clause_dict, current_b)
                new_bindings.extend(results)
            bindings = new_bindings
            if not bindings:
                break

        return bindings

    def _unify_conclusion(self, conclusion: Fact, goal: Fact, binding: Binding) -> Binding | None:
        """统一规则结论与查询目标。"""
        if conclusion.predicate != goal.predicate:
            return None

        b = binding.copy()
        for key in conclusion.args:
            c_val = conclusion.args[key]
            g_val = goal.args.get(key)

            if g_val is None:
                continue

            resolved_c = b.get(c_val)
            resolved_g = b.get(g_val)

            if isinstance(resolved_c, Var) and isinstance(resolved_g, Var):
                extended = b.extend(resolved_c, resolved_g)
                if extended is None:
                    return None
                b = extended
            elif isinstance(resolved_c, Var):
                extended = b.extend(resolved_c, resolved_g)
                if extended is None:
                    return None
                b = extended
            elif isinstance(resolved_g, Var):
                extended = b.extend(resolved_g, resolved_c)
                if extended is None:
                    return None
                b = extended
            elif resolved_c != resolved_g:
                return None

        return b

    # ═══════════════════════════════════════════════
    # 子句证明
    # ═══════════════════════════════════════════════

    def _prove_clause(self, clause: dict, binding: Binding) -> list[Binding]:
        """证明单个子句，从给定 binding 出发返回所有扩展 binding。"""
        clause_type = clause.get("type", "")

        if clause_type == "graph":
            return self._prove_graph_clause(clause, binding)
        elif clause_type == "rule":
            return self._prove_rule_clause(clause, binding)
        elif clause_type == "builtin":
            return self._prove_builtin(clause, binding)
        elif clause_type == "compute":
            return self._prove_compute(clause, binding)
        elif clause_type == "not_":
            return self._prove_not(clause, binding)
        elif clause_type == "create_dp":
            return self._prove_create_dp(clause, binding)
        elif clause_type == "link":
            return self._prove_link(clause, binding)
        elif clause_type == "action":
            return self._prove_action(clause, binding)
        else:
            logger.warning(f"[inference] 未知子句类型: {clause_type}")
            return []

    def _prove_graph_clause(self, clause: dict, binding: Binding) -> list[Binding]:
        """证明图查询子句。"""
        query_name = clause["query"]
        # 先 resolve params，但如果有未绑定的 Var 则替换为默认值
        raw_params = clause["params"]
        resolved_params = {}
        has_unbound = False
        for k, v in raw_params.items():
            resolved = binding.get(v)
            if isinstance(resolved, Var):
                # Var 未绑定——图查询无法处理，用默认值
                has_unbound = True
                if k in ("limit",):
                    resolved_params[k] = 20
                else:
                    resolved_params[k] = ""
            else:
                resolved_params[k] = resolved
        # 如果有未绑定 Var 且 query 需要具体值，跳过
        if has_unbound and query_name in (
            "find_event_by_title", "find_event_like",
            "event_expenses", "event_participants",
            "latest_balance_in_event", "debt_dps_for_event",
            "get_datapoint", "get_event",
        ):
            return []
        result_var = clause.get("result_var")

        try:
            result = self.searcher.execute(query_name, resolved_params)
        except Exception as e:
            logger.warning(f"[inference] 图查询失败 {query_name}: {e}")
            return []

        if result is None:
            return []

        items = result if isinstance(result, list) else [result]
        if not items:
            return []

        bindings_out = []
        for item in items:
            b = binding.copy()
            if result_var is not None:
                extended = b.extend(result_var, item)
                if extended is None:
                    continue
                b = extended
            bindings_out.append(b)

        return bindings_out

    def _prove_rule_clause(self, clause: dict, binding: Binding) -> list[Binding]:
        """证明引用另一条规则的子句。"""
        rule_name = clause["rule_name"]
        binds = binding.resolve(clause["binds"])

        # 构造 goal
        goal = Fact(predicate=rule_name, args=binds)

        # 查找该 conclusion 的规则
        rules = self.rule_base.get_by_conclusion(rule_name)
        if not rules:
            # 可能是一个图查询
            return self._prove(Fact(predicate=rule_name, args=binds), binding)

        all_bindings = []
        for rule in rules:
            rule_bindings = self._prove_rule(rule, goal, binding)
            all_bindings.extend(rule_bindings)
        return all_bindings

    def _prove_builtin(self, clause: dict, binding: Binding) -> list[Binding]:
        """证明内置谓词。"""
        op = clause["op"]
        args = [binding.get(a) for a in clause.get("args", [])]

        if op == "eq":
            return [binding] if len(args) >= 2 and args[0] == args[1] else []
        elif op == "neq":
            return [binding] if len(args) >= 2 and args[0] != args[1] else []
        elif op == "lt":
            return [binding] if len(args) >= 2 and args[0] < args[1] else []
        elif op == "gt":
            return [binding] if len(args) >= 2 and args[0] > args[1] else []
        elif op == "lte":
            return [binding] if len(args) >= 2 and args[0] <= args[1] else []
        elif op == "gte":
            return [binding] if len(args) >= 2 and args[0] >= args[1] else []
        elif op == "member":
            if len(args) >= 2:
                container = args[1] if isinstance(args[1], (list, tuple)) else []
                if args[0] in container:
                    return [binding]
            return []
        elif op == "is_not_none":
            return [binding] if len(args) >= 1 and args[0] is not None else []
        elif op == "is_none":
            return [binding] if len(args) >= 1 and args[0] is None else []
        else:
            logger.warning(f"[inference] 未知 builtin: {op}")
            return []

    def _prove_not(self, clause: dict, binding: Binding) -> list[Binding]:
        """否定子句：内层 clause 无解时才成功。"""
        inner = clause["clause"]
        results = self._prove_clause(inner, binding)
        return [binding] if not results else []

    def _prove_compute(self, clause: dict, binding: Binding) -> list[Binding]:
        """证明计算子句。"""
        op = clause["op"]
        args = [binding.get(a) for a in clause.get("args", [])]

        # 最后一个 arg 可能是 result_var
        result_var = clause["args"][-1] if clause["args"] else None
        if isinstance(result_var, Var):
            compute_inputs = args[:-1]
        else:
            compute_inputs = list(args)
            result_var = None

        result = self._do_compute(op, compute_inputs)

        if result is None:
            return []

        b = binding.copy()
        if result_var is not None and isinstance(result_var, Var):
            extended = b.extend(result_var, result)
            if extended is None:
                return []
            b = extended

        return [b]

    def _do_compute(self, op: str, args: list) -> Any:
        """执行计算。"""
        try:
            if op == "sum":
                items, field = args[0], args[1]
                total = 0.0
                for item in (items if isinstance(items, list) else []):
                    if isinstance(item, dict):
                        total += float(item.get(field, 0))
                return total
            elif op == "count":
                items = args[0]
                return len(items) if isinstance(items, list) else 0
            elif op == "avg":
                items, field = args[0], args[1]
                if not isinstance(items, list) or not items:
                    return 0.0
                total = sum(float(item.get(field, 0)) for item in items if isinstance(item, dict))
                return total / len(items)
            elif op == "divide":
                return float(args[0]) / float(args[1]) if float(args[1]) != 0 else 0.0
            elif op == "subtract":
                return float(args[0]) - float(args[1])
            elif op == "add":
                return float(args[0]) + float(args[1])
            elif op == "multiply":
                return float(args[0]) * float(args[1])
            elif op == "min_of":
                candidates = args[0] if isinstance(args[0], list) else list(args)
                return min(candidates) if candidates else None
            elif op == "abs":
                return abs(float(args[0]))
            elif op == "len":
                return len(args[0]) if isinstance(args[0], (list, tuple)) else 0
            elif op == "round":
                return round(float(args[0]), int(args[1]) if len(args) > 1 else 2)
            elif op == "collect":
                # 从列表中提取字段值
                items, field = args[0], args[1]
                if isinstance(items, list):
                    return [item.get(field, 0) for item in items if isinstance(item, dict)]
                return []
            else:
                logger.warning(f"[inference] 未知计算: {op}")
                return None
        except (TypeError, ValueError, ZeroDivisionError) as e:
            logger.warning(f"[inference] 计算失败 {op}: {e}")
            return None

    def _prove_create_dp(self, clause: dict, binding: Binding) -> list[Binding]:
        """创建数据点（写入 ops 队列）。"""
        dp_type = clause["dp_type"]
        resolved = binding.resolve(clause["params"])
        result_var = clause.get("result_var")

        # ID 预生成，以便后续 link 引用
        from src.graph.base_repo import _new_id
        dp_id = _new_id()

        op = {
            "type": "create_dp",
            "dp_type": dp_type,
            "dp_id": dp_id,
            "user_name": resolved.get("user_name", "system"),
            "payload": resolved.get("payload", {}),
            "event_id": resolved.get("event_id"),
        }
        self._ops.append(op)

        b = binding.copy()
        if result_var is not None and isinstance(result_var, Var):
            b = b.extend(result_var, dp_id)
            if b is None:
                return []
        return [b]

    def _prove_link(self, clause: dict, binding: Binding) -> list[Binding]:
        """创建图关系（写入 ops 队列）。"""
        resolved = binding.resolve(clause)
        from_id = resolved.get("from_var")
        to_id = resolved.get("to_var")
        rel_type = resolved.get("rel_type")
        props = resolved.get("props")

        # from_var/to_var 是 Var，需要从 binding 取实际值
        from_id = binding.get(clause["from_var"]) if isinstance(clause.get("from_var"), Var) else from_id
        to_id = binding.get(clause["to_var"]) if isinstance(clause.get("to_var"), Var) else to_id

        if from_id and to_id:
            self._ops.append({
                "type": "link",
                "rel_type": rel_type,
                "from_id": from_id,
                "to_id": to_id,
                "props": props,
            })

        return [binding]

    def _prove_action(self, clause: dict, binding: Binding) -> list[Binding]:
        """处理 action 子句——通过 ActionHandler 真正执行。

        动作分两类：
          计算类：compute_per_person_balance, extract_debt_item 等
            → 计算后绑定变量，不收集 ops（变量被后续子句使用）
          副作用类：create_personal_reservations, schedule_reminder, decompose_debts 等
            → 收集到 ops 队列，返回原 binding
        """
        op_name = clause["op"]
        params = binding.resolve(clause["params"])

        # 纯副作用动作：不修改 binding，只收集 ops
        side_effect_ops = {
            "create_personal_reservations", "schedule_reminder",
            "activate_reservation_event", "notify_event_start",
            "open_event", "settle_event", "cancel_event",
            "ensure_user_in_event", "repay_with_overflow", "decompose_debts",
        }

        if op_name in side_effect_ops:
            self._ops.append({"type": "action", "op": op_name, "params": params})
            return [binding]

        # 通过 ActionHandler 处理（计算类：可能修改 binding）
        success, new_binding = self.action_handler.handle(op_name, params, binding)
        if success:
            return [new_binding if new_binding is not None else binding]
        return []

    # ═══════════════════════════════════════════════
    # 前向链：事实驱动的自动推理
    # ═══════════════════════════════════════════════

    def forward_chain(self, new_fact: Fact | None = None) -> list[dict]:
        """前向链推理。

        当有新事实（如 LLM 翻译的 FL）进入系统时调用。
        或定时由 Scheduler 调用（new_fact=None）做周期性检测。

        流程：
        1. 匹配所有 rules.triggers 与新事实（或无条件触发器）
        2. 执行匹配规则的 conditions
        3. 收集 create_dp / link / action 操作
        4. 递归：如果有新事实产出，继续前向链

        Args:
            new_fact: 新注入的事实（None 表示定时检测）

        Returns:
            所有操作指令列表（含 create_dp, link, create_event, create_fl 等）
        """
        self._ops.clear()

        if new_fact is not None:
            logger.info(f"[inference] 前向链触发: {new_fact}")
            # 匹配 triggers
            triggered = self._match_triggers(new_fact)
            for rule in triggered:
                self._prove_rule(rule, rule.conclusion, Binding())
        else:
            # 定时检测：触发所有无条件的周期规则
            for rule in self.rule_base.get_all_rules():
                if self._is_timed_rule(rule):
                    self._prove_rule(rule, rule.conclusion, Binding())

        return self.collect_ops()

    def _match_triggers(self, fact: Fact) -> list[Rule]:
        """根据新事实匹配规则触发器。"""
        matched = []
        for rule in self.rule_base.get_by_trigger(fact.predicate):
            # 检查 trigger 的参数是否与 fact 兼容
            for trigger in rule.triggers:
                if trigger.get("type") == "action" and trigger.get("op") == fact.predicate:
                    trigger_params = trigger.get("params", {})
                    # 简单匹配：fact.args 包含 trigger 所需的所有键
                    if all(k in fact.args for k in trigger_params):
                        matched.append(rule)
                        break
        return matched

    def _is_timed_rule(self, rule: Rule) -> bool:
        """判断是否为定时触发规则（无 trigger action，但有 conditions 需要周期性检查）。"""
        # 规则有名为 "timed" 的特殊 trigger
        for trigger in rule.triggers:
            if trigger.get("type") == "action" and trigger.get("op") == "timed":
                return True
        return False

    # ═══════════════════════════════════════════════
    # 工具：构造函数化查询
    # ═══════════════════════════════════════════════

    def query_debt(self, debtor: str | None = None, creditor: str | None = None,
                   event_title: str | None = None) -> list[dict]:
        """便捷方法：查询债务。

        Returns:
            [{debtor, creditor, amount, event}, ...]
        """
        goal_args = {}
        if debtor:
            goal_args["debtor"] = debtor
        else:
            goal_args["debtor"] = Var("A")
        if creditor:
            goal_args["creditor"] = creditor
        else:
            goal_args["creditor"] = Var("B")
        goal_args["amount"] = Var("X")
        if event_title:
            goal_args["event"] = event_title
        else:
            goal_args["event"] = Var("E")

        bindings = self.query(Fact("debt", goal_args))
        results = []
        for b in bindings:
            results.append({
                "debtor": str(b.get(Var("A"))) if Var("A") in b.mapping else debtor,
                "creditor": str(b.get(Var("B"))) if Var("B") in b.mapping else creditor,
                "amount": b.get(Var("X")) if Var("X") in b.mapping else None,
                "event": str(b.get(Var("E"))) if Var("E") in b.mapping else event_title,
            })
        return results

    def query_balance(self, event_title: str) -> dict | None:
        """便捷方法：查询事件余额。"""
        bindings = self.query(Fact("balance", {
            "event_title": event_title,
            "payload": Var("P"),
        }))
        if bindings:
            return bindings[0].get(Var("P"))
        return None