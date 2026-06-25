"""行动执行器 - v2：纯指令落地器。

旧版 Executor 假 action 已废弃（LLM→params 照抄为 dp，输入=输出）。
v2 架构中，所有检索/计算/推导由 规则引擎（InferenceEngine）完成，
Executor 仅负责将 Ops 指令落地到图数据库。

此文件保留兼容，实际落地逻辑已移入 orchestrator._exec_create_dp / _exec_link。
"""

from dataclasses import dataclass, field


@dataclass
class ActionResult:
    """一次行动的结果。v2 中仅用于兼容旧接口。"""
    success: bool
    message: str
    action_summary: str = ""
    input_dp_ids: list[str] = field(default_factory=list)
    output_datapoints: list[dict] = field(default_factory=list)
    output_fls: list[dict] = field(default_factory=list)
    data_lines: list[tuple[str, str]] = field(default_factory=list)
    opened_event: dict | None = None
    settled_event_id: str | None = None
    cancelled_event_id: str | None = None
    used_event_id: str | None = None
    consumed_dp_ids: list[str] = field(default_factory=list)


# v2: Executor 不再执行假 action。
# 指令落地见 Orchestrator._execute_via_inference → _exec_create_dp / _exec_link
# 逻辑推理见 InferenceEngine + src/engine/rules/
