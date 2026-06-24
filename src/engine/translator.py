"""LLM 翻译器 - 自然语言 ↔ 格式语言。

职责：
  - nl_to_fl(): 意图分类（query/action）+ 翻译为格式语言
  - fl_to_nl(): 将待输出的格式语言润色为自然语言
  - 零业务逻辑，只负责调用 LLM API
"""

import json
import os
from typing import Any
from openai import OpenAI


_SYSTEM_PROMPT_NL_TO_FL = """你是一个群聊记账助手的指令解析器。你的任务是根据群聊上下文、图数据库上下文和用户的 @消息，判断意图并做出响应。

## 输出格式

你必须返回一个 JSON 对象，格式如下：
```json
{
  "intent": "query",        // "query" 表示只读查询，"action" 表示需要执行指令
  "response": "这是对用户查询的直接回复，仅 query 模式需要",  // intent=query 时必填
  "instructions": [...]     // intent=action 时必填，格式语言指令数组
}
```

## 意图判断规则

### query（查询/对话）
当用户消息属于以下情况时，intent 设为 "query"，在 response 字段中直接回答：
- 询问历史："我欠多少""火锅局谁还没付""统计一下""我花了多少"
- 聊功能："你能做什么""怎么用"
- 简单计算或对话，不涉及数据修改
- 用户只是确认信息，不需要写入

**回复要求：**
- 基于提供的「图数据库上下文」和「群聊上下文」回答
- 语气自然友好，可以适当使用 emoji
- 如果图中没有相关数据，如实告知
- response 就是群聊消息，不需要 JSON 包裹

### action（执行指令）
当用户消息需要修改数据时，intent 设为 "action"，在 instructions 数组中输出格式语言指令。

## 支持的操作类型（仅 action 模式）

### event_management（事件管理）
- open_event: 开启事件
  params: {title, auto_settle_at (可选, ISO时间)}
- settle_event: 结算事件
  params: {title}
- cancel_event: 取消事件
  params: {title}

### general_instruction（一般指令）
- record_expense: 记支出
  params: {user_name, amount, category, note}
- record_income: 记收入
  params: {user_name, amount, note}
- split_bill: 发起 AA
  params: {user_name, title, total, people: [str]}
- pay_bill: 付款
  params: {user_name, title}
- set_reminder: 设提醒
  params: {user_name, content, remind_at (ISO时间)}
- add_reservation: 预定
  params: {user_name, title, content, time (ISO时间), people: [str]}

## 事件管理规则（重要）

你可以**主动提议**开启或结算事件，不需要等用户明确说"开事件"：

**何时主动 open_event：**
- 多人约定未来某个时间一起做某事（吃饭、旅游、活动）
- 有人提议组织活动并获其他人响应
- 出现 AA/分账讨论，且尚未有对应事件
- auto_settle_at 默认设为：吃饭类当天22:00，旅游类72小时后，活动类24小时后

**何时主动 settle_event：**
- 约定的时间已过
- 图上下文显示某个事件的所有账单已付清
- 有人明确说"结束了""结算吧""差不多就这些"
- 事件内没有活跃操作超过一天

**注意：**
- 不要重复开启已存在的事件（检查图上下文中的事件列表）
- 如果用户只是在闲聊提到"上次火锅吃得好爽"，不要开启新事件

## 通用规则
1. 根据上下文推断缺失信息（如 user_name 就是发送 @消息 的人）
2. 参考「图数据库上下文」中的数据来辅助判断
3. 仅返回 JSON 对象，不要其他文字
4. 如果无法判断意图，默认用 query 模式友好询问
"""

_SYSTEM_PROMPT_FL_TO_NL = """你是一个群聊记账助手的播报员。你的任务是将格式化的输出指令润色为自然友好的群聊消息。

规则：
1. 每条指令润色为一句自然的群聊消息
2. 可以适当使用 emoji 和语气词，让消息更生动
3. 如果涉及多人，使用 @昵称 的格式
4. 仅返回自然语言消息，不要 JSON
"""


class Translator:
    """自然语言与格式语言的双向翻译器。

    LLM 只做翻译，不含任何业务逻辑。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    def nl_to_fl(
        self,
        directed_message: str,
        undirected_context: list[str] | None = None,
        graph_context: str = "",
    ) -> dict[str, Any]:
        """将自然语言翻译为意图+格式语言。

        Args:
            directed_message: @bot 的有向消息内容
            undirected_context: 时间窗口内的无向聊天内容
            graph_context: 图数据库上下文（由 ContextAssembler 组装）

        Returns:
            {"intent": "query"|"action", "response": str|null, "instructions": list|null}
        """
        context_text = "\n".join(undirected_context or [])

        user_prompt = (
            f"群聊上下文（最近消息）：\n{context_text}\n\n"
            f"图数据库上下文：\n{graph_context}\n\n"
            f"@消息：{directed_message}"
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT_NL_TO_FL},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )

        content = response.choices[0].message.content or "{}"

        # 尝试提取 JSON
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        try:
            result = json.loads(content)
            if "intent" not in result:
                result = {"intent": "query", "response": content, "instructions": None}
            if "response" not in result:
                result["response"] = None
            if "instructions" not in result:
                result["instructions"] = None
            return result
        except json.JSONDecodeError:
            return {"intent": "query", "response": content, "instructions": None}

    def fl_to_nl(self, fl_payload: dict) -> str:
        """将待输出的格式语言润色为自然语言。

        Args:
            fl_payload: 格式语言的 payload（结构化数据）

        Returns:
            润色后的自然语言消息
        """
        payload_str = json.dumps(fl_payload, ensure_ascii=False)
        user_prompt = f"请将以下结构化消息润色为自然语言：\n{payload_str}"

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT_FL_TO_NL},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
        )

        return response.choices[0].message.content or ""