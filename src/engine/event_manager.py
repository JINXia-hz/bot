"""事件管理器 - 事件生命周期的管理逻辑。

职责：
  - 自动检测：根据群聊上下文判断是否应自动开启事件
  - 自动结算：检测到期事件，执行结算
  - 统合数据点生成：结算时汇总事件内的所有数据点
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class EventAction:
    """事件管理操作结果。"""
    action: str                # "open" | "settle" | "cancel" | "none"
    event_title: str = ""
    created_by: str = "system"
    trigger_type: str = "system"  # "system" | "manual"
    auto_settle_at: str | None = None
    summary_payload: dict | None = None


class EventManager:
    """事件生命周期管理器。

    纯逻辑层，不直接操作数据库。
    事件开启/结算的决策已移交给 LLM（translator prompt 中处理），
    此管理器仅保留自动结算的定时检测和统合数据点生成。

    pipeline 层调用此管理器获取操作建议，然后通过 repo 落地。
    """

    # LLM 已接管事件开启/结算决策，关键词匹配已删除。
    # 参见 translator.py 中 _SYSTEM_PROMPT_NL_TO_FL 的事件管理规则。

    def should_auto_settle(
        self,
        event: dict,
    ) -> EventAction | None:
        """判断事件是否应自动结算。

        Args:
            event: 事件字典

        Returns:
            EventAction 或 None
        """
        auto_settle_at = event.get("auto_settle_at", "")
        if not auto_settle_at:
            return None

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        if auto_settle_at <= now and event.get("status") == "active":
            return EventAction(
                action="settle",
                event_title=event.get("title", ""),
            )

        return None

    def generate_summary(
        self,
        event: dict,
        datapoints: list[dict],
        data_lines: list[dict],
    ) -> dict:
        """生成事件的统合数据点。

        汇总事件内所有数据点，产出结算摘要。

        Args:
            event: 事件字典
            datapoints: 事件内所有数据点
            data_lines: 事件内所有数据线

        Returns:
            统合数据点的 payload dict
        """
        import json

        # 按类型分组统计
        type_counts: dict[str, int] = {}
        users: set[str] = set()
        total_expense = 0.0
        total_income = 0.0

        for dp in datapoints:
            pl = dp.get("payload", {})
            if isinstance(pl, str):
                pl = json.loads(pl)

            dp_type = dp.get("dp_type", "unknown")
            type_counts[dp_type] = type_counts.get(dp_type, 0) + 1
            users.add(dp.get("user_name", ""))

            if dp_type == "expense":
                total_expense += float(pl.get("amount", 0))
            elif dp_type == "income":
                total_income += float(pl.get("amount", 0))

        return {
            "type": "event_summary",
            "event_title": event.get("title", ""),
            "duration": f"{event.get('created_at', '')} → {event.get('settled_at', '')}",
            "participants": sorted(list(users)),
            "total_datapoints": len(datapoints),
            "total_data_lines": len(data_lines),
            "type_breakdown": type_counts,
            "financial_summary": {
                "total_expense": round(total_expense, 2),
                "total_income": round(total_income, 2),
                "net": round(total_income - total_expense, 2),
            },
        }