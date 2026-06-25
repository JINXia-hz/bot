"""规则库注册入口。

所有规则在此注册，RuleBase 统一管理。
修改规则只需编辑对应文件，不动引擎代码。
"""

from src.engine.rule_engine import RuleBase, Rule

# 规则库单例
_rule_base: RuleBase | None = None


def get_rule_base() -> RuleBase:
    """获取规则库单例。"""
    global _rule_base
    if _rule_base is None:
        _rule_base = RuleBase()
        _register_all(_rule_base)
    return _rule_base


def _register_all(rb: RuleBase) -> None:
    """注册所有规则模块。"""
    from src.engine.rules import expense_rules
    expense_rules.register(rb)

    from src.engine.rules import aa_rules
    aa_rules.register(rb)

    from src.engine.rules import reservation_rules
    reservation_rules.register(rb)

    from src.engine.rules import settlement_rules
    settlement_rules.register(rb)