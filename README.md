# bot — 图记忆驱动的 QQ 群记账助手

基于 **kuzu 图数据库** + **逻辑规则引擎** + **LLM** 的群聊记账机器人。

数据不可变、因果可追溯。规则引擎做确定计算，LLM 只负责理解自然语言。

---

## 1. 数据结构

项目采用 kuzu 嵌入式图数据库，所有数据以**节点 + 关系**的形式存储。

### 1.1 五类节点

| 节点类型 | 说明 | 可变性 | 例 |
|----------|------|--------|---|
| **RawMessage** | 群聊原始消息 | 不可变，定时清理 | "火锅局我付了150" |
| **FormalLanguage** | LLM 产出的结构化指令或回复 | 不可变 | `{"op":"record_expense","params":{...}}` |
| **DataPoint** | 不可变状态单元 | **不可变**，只能新建 | 一笔 expense、一个 balance、一条 debt |
| **Event** | 管理容器（"块"） | 状态可切换 | "火锅局" → active / settled / cancelled |
| **ActionLog** | 一次行动的审计记录 | 不可变 | 某次 record_expense 的执行记录 |

### 1.2 七条关系（边）

```
RawMessage ──TRANSLATES_TO──→ FormalLanguage    "谁翻译的"
FormalLanguage ──TRIGGERED──→ ActionLog        "谁触发的"
ActionLog ──PRODUCED──→ DataPoint              "行动产出"
DataPoint ──CONSUMED──→ ActionLog              "被行动消耗（输入）"
DataPoint ──DATA_LINE──→ DataPoint             "因果关系链"
DataPoint ──BELONGS_TO──→ Event                "归属于哪个事件"
ActionLog ──GENERATED──→ FormalLanguage        "行动产出了什么回复"
FormalLanguage ──MANAGES──→ Event              "管理者"
```

### 1.3 DataPoint 的数据线（DATA_LINE）

数据线记录了**不可变的数据演变因果链**：

```
expense(张三,150) ──DATA_LINE──→ balance({张三:{paid:150, owe:110, net:+40}, ...})
balance ──DATA_LINE──→ debt({debtor:李四, creditor:张三, amount:30})
debt ──DATA_LINE──→ debt_settled({amount:30, status:fully_paid})
debt ──DATA_LINE──→ settlement_summary(...)
```

以此保证：**任何一个数据点都能沿数据线追溯其前因后果**。

### 1.4 关键 DataPoint 类型

| dp_type | payload 结构 | 生命周期 |
|----------|-------------|----------|
| `expense` | `{amount, category, note}` | 用户记一笔 → 触发 balance 重算 |
| `balance` | `{张三:{paid, owe, net}, 李四:{paid, owe, net}, ...}` | 每次新 expense 后自动生成 |
| `debt` | `{debtor, creditor, amount}` | 从 balance 净额矩阵贪心分配 |
| `debt_settled` | `{debtor, creditor, amount, status}` | 还款时创建，DATA_LINE 连接原 debt |
| `participant_entry` | `{event_title, role}` | 新参与者首次在事件中记账时自动创建 |
| `reservation` | `{title, content, time, people}` | 预定后创建，到期后激活 |
| `personal_reservation` | `{title, time, status}` | 为每个参与者各创建一条 |
| `settlement_summary` | `{event_title, participants, financial_summary}` | 结算时生成 |

---

## 2. 执行流程

### 2.1 实时消息处理

```
群聊消息到达
  │
  ├─ 所有消息 → RawMessage（无向/有向）
  │
  ├─ @bot 有向消息 → Orchestrator.on_directed_message()
  │   │
  │   ├─ 1. 存储有向消息
  │   ├─ 2. 精简化上下文收集：
  │   │     a. 取两个 @bot 之间的区间消息（list_since_last_directed）
  │   │     b. 提取区间内所有发言者 → 追溯每人最近 12 条消息
  │   │     c. 查询活跃事件列表（仅 id + title + 参与者）
  │   ├─ 3. LLM 翻译：NL → {intent, response?, instructions?}
  │   │
  │   ├─ intent=query
  │   │   ├─ 如果 instructions 包含 query_type → 规则引擎后向链 → 确定计算 → LLM 润色
  │   │   └─ 否则 → LLM 直接回答
  │   │
  │   └─ intent=action
  │       ├─ 创建 FormalLanguage 记录（FL）
  │       ├─ FL → 包装为 Fact → 注入推理引擎前向链
  │       │   ├─ 匹配规则 triggers → 执行条件链
  │       │   ├─ 图查询（GraphSearcher）→ 获取已有数据
  │       │   ├─ 规则链推理 → 计算新数据
  │       │   └─ 收集 Ops 指令列表（create_dp / link / create_event / create_fl）
  │       ├─ Orchestrator 逐条落地 Ops
  │       └─ 即时回复 FL → Translator.fl_to_nl() 润色 → 回复群聊
  │
  └─ 无向消息 → 仅存储，供后续上下文使用
```

### 2.2 定时任务（Scheduler / APScheduler）

```
每 30s: process_scheduled_replies()
  → 检查到期的定时 FL → Translator 润色 → 回复群聊

每 60s: check_auto_settle()
  → 调用 inference.forward_chain()（timed 规则）
  → 触发 auto_settle_due_events / activate_due_reservation 等规则
  → 收集 Ops → 落地执行 → 回复群聊

每 10min: cleanup_undirected()
  → 清理过期无向消息
```

---

## 3. 推理模式

### 3.1 规则引擎的三层架构

```
Rule（声明式规则） → InferenceEngine（推理引擎） → ActionHandler / GraphSearcher（执行/检索）
```

**规则库** (`src/engine/rules/`): 声明式规则，只定义"什么成立需要什么条件"。

**推理引擎** (`src/engine/inference.py`): 实现后向链和前向链，递归证明规则。

**图搜索引擎** (`src/engine/graph_searcher.py`): 将 kuzu Cypher 暴露为命名查询，含跨事件穿透查询。

### 3.2 前向链（Fact-Driven / Action 模式）

当 LLM 产出一条 FL（如 `record_expense`），规则引擎将其包装为 Fact 注入：

```python
new_fact = Fact(predicate="record_expense", args={"user_name":"张三", "amount":150, "note":"火锅局"})
ops = inference.forward_chain(new_fact)
```

推理引擎匹配到 `expense_triggers_posting`，链式展开：

```python
# 1. 搜事件 → 2. 自动补入参与者 → 3. 创建 expense dp → 4. compute_event_balance → 5. decompose_debts
```

`compute_event_balance` 规则：

```python
Rule(
    name="compute_event_balance",
    conditions=[
        Clause.graph("event_expenses", {"event_id": Var("E")}, Var("Expenses")),  # 搜所有支出
        Clause.graph("event_participants", {"event_id": Var("E")}, Var("People")),# 搜参与者
        Clause.compute("sum", Var("Expenses"), "amount", Var("Total")),            # 确定求和
        Clause.compute("count", Var("People"), Var("N")),                          # 人头计数
        Clause.compute("divide", Var("Total"), Var("N"), Var("Per")),             # 人均
        Clause.action("compute_per_person_balance", {...}),                        # 每人净额
        Clause.create_dp("balance", {...}, Var("DP_BAL")),                         # 产出 balance dp
        Clause.link("DATA_LINE", Var("DP_TRIGGER"), Var("DP_BAL")),               # 因果链
    ],
)
```

**一条 `record_expense` 最终产出的 Ops**：
```
create_dp("participant_entry", {张三, 火锅局})  # 如果张三之前不在事件中
create_dp("expense", {张三, 150, event:火锅局})
link BELONGS_TO
create_dp("balance", {张三:{+40}, 李四:{-30}, 王五:{-10}})
link DATA_LINE(expense → balance)
create_dp("debt", {debtor:李四, creditor:张三, amount:30})
create_dp("debt", {debtor:王五, creditor:张三, amount:10})
```

### 3.3 后向链（Query 模式）

用户问"A欠B多少"，LLM 产出结构化查询：

```json
{"intent":"query", "instructions":[{"query_type":"debt", "params":{"debtor":"张三","creditor":"李四","event":"火锅局"}}]}
```

Orchestrator 调用推理引擎后向链：

```python
bindings = inference.query(Fact("debt", {"debtor":"张三", "creditor":"李四", "event":"火锅局"}))
```

推理引擎匹配到 `debt_query` 规则，从图中检索已计算的 debt dp：

```python
Rule(
    name="debt_query",
    conclusion=Fact("debt", {"debtor": Var("A"), "creditor": Var("B"), "amount": Var("X"), "event": Var("ET")}),
    conditions=[
        Clause.graph("find_event_by_title", {"title": Var("ET")}, Var("Ev")),
        Clause.resolve(Var("Ev"), "id", Var("E")),                              # 从事件 dict 提取 id
        Clause.graph("debt_in_event", {"event_id": Var("E")}, Var("Debts")),
        Clause.action("extract_debt_item", {"debts": Var("Debts"), ...}),
    ],
)
```

**确定性返回**：`[{debtor:"张三", creditor:"李四", amount:40, event:"火锅局"}]`

LLM 仅负责将结果润色为自然友好的群聊消息。

### 3.4 全局欠款查询

用户问"我一共欠多少"，触发 `global_debt_query` 规则：

```python
Rule(
    name="global_debt_query",
    conclusion=Fact("global_debt", {"person": Var("P"), "total_owe": Var("TO"), "total_owed": Var("TR"), "net": Var("N")}),
    conditions=[
        Clause.graph("global_owes_summary", {"user_name": Var("P")}, Var("Summary")),
    ],
)
```

`global_owes_summary` 跨所有活跃事件聚合，返回 `{total_owe, total_owed, net, details}`。

### 3.5 穿透还款（跨事件、溢出填补）

用户说"我还了张三50"，LLM 产出：

```json
{"op": "repay", "params": {"debtor": "李四", "creditor": "张三", "amount": 50}}
```

规则引擎前向链 `repay_triggers_settlement`：

```python
Rule(
    name="repay_triggers_settlement",
    triggers=[Clause.action("repay", {"debtor": Var("D"), "creditor": Var("C"), "amount": Var("A")})],
    conditions=[
        Clause.graph("global_debts_between", {"debtor": Var("D"), "creditor": Var("C")}, Var("AllDebts")),
        Clause.action("repay_with_overflow", {"debts": Var("AllDebts"), "total_amount": Var("A"), ...}),
    ],
)
```

`repay_with_overflow` 按金额升序逐一清偿跨事件债务：
- 还清一笔 → 创建 `debt_settled` dp + DATA_LINE
- 溢出部分填补下一笔
- 全还完还有剩 → 创建反向 credit dp（对方反过来欠）

### 3.6 否定（Negation as Failure）

```python
Rule(
    name="expense_without_event",
    triggers=[Clause.action("record_expense", {...})],
    conditions=[
        Clause.not_(Clause.graph("find_event_like", {"title": Var("N")}, Var("Ev"))),  # 没找到匹配事件
        Clause.create_dp("expense", {...}, Var("DP1")),  # 创建不关联事件的 expense
    ],
)
```

当 `expense_triggers_posting` 因为找不到匹配事件而失败时，引擎回溯尝试 `expense_without_event`。

### 3.7 事件隔离 & 参与者自动补入

同一个 @bot 消息只匹配一个活跃事件。新事件和已有事件完全隔离（不同的 Event 节点，各自 BELONGS_TO 关系）。

如果一个参与者不在事件中时记账，`ensure_user_in_event` 自动创建 `participant_entry` dp 将其加入。不需要 LLM 参与，全是后台格式化操作。

### 3.8 如何新增能力

只需在 `src/engine/rules/` 下新增规则文件 + 在 `__init__.py` 中注册：

```python
# 新增 src/engine/rules/my_rules.py
def register(rb):
    rb.register(Rule(
        name="my_rule",
        triggers=[Clause.action("my_action", {"param": Var("P")})],
        conclusion=Fact("my_result", {"output": Var("O")}),
        conditions=[
            Clause.graph("some_query", {...}, Var("X")),
            Clause.compute("add", Var("X"), 1, Var("O")),
        ],
    ))

# 注册
from src.engine.rules import my_rules
my_rules.register(rb)
```

**不动引擎代码，只加规则。**

---

## 4. 项目结构

```
src/
├── web/                       # Web 管理后台
│   ├── admin.py               # FastAPI 路由 + API
│   └── static/
│       └── index.html         # 前端管理页面（纯 HTML/JS）
│
├── graph/                     # kuzu 图数据库层
│   ├── connection.py          # 连接管理（单例）
│   ├── schema.py              # DDL：5 张节点表 + 8 张关系表
│   ├── base_repo.py           # 基类（execute / new_id / now）
│   ├── data_point_repo.py     # DataPoint CRUD + 关系操作
│   ├── event_repo.py          # Event CRUD + 生命周期
│   ├── action_log_repo.py     # ActionLog CRUD
│   ├── formal_language_repo.py # FormalLanguage CRUD
│   ├── raw_message_repo.py    # RawMessage CRUD + 区间查询 + 按人追溯
│   └── context_assembler.py   # 组装 LLM 上下文（完整/轻量事件匹配两种）
│
├── engine/                    # 规则引擎 + 推理层
│   ├── rule_engine.py         # Rule / Clause / Fact / Var / Binding DSL
│   ├── inference.py           # 后向链 + 前向链推理引擎（含统一算法、回溯）
│   ├── graph_searcher.py      # 图搜索引擎（~30 种命名查询，含跨事件穿透）
│   ├── action_handler.py      # 内置动作处理器（balance、债务贪心、穿透还款、参与者补入）
│   ├── translator.py          # LLM 翻译器（NL ↔ FL）
│   ├── executor.py            # v2 兼容占位（旧假 handler 已删除）
│   ├── event_manager.py       # 事件生命周期管理（结算摘要生成）
│   └── rules/                 # 声明式规则库
│       ├── __init__.py        # 注册入口
│       ├── expense_rules.py   # 支出→过账→balance→债务拆解 + 参与者自动补入
│       ├── aa_rules.py        # 债务查询 + 全局欠款 + 穿透还款
│       ├── reservation_rules.py # 预定→事件激活→定时提醒
│       └── settlement_rules.py  # 自动/手动结算
│
├── pipeline/                  # 流程编排层
│   ├── orchestrator.py        # 主编排器：区间上下文→LLM→inference→ops 落地
│   └── scheduler.py           # APScheduler 定时任务（前向链驱动结算）
│
└── plugins/                   # NoneBot2 插件层
    ├── listener.py            # 群消息监听 + 有向消息路由
    └── web_admin.py           # 挂载 Web 管理后台到 NoneBot2 FastAPI
```

---

## 5. 环境配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API Key | - |
| `LLM_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `KUZU_DB_PATH` | 图数据库路径 | `data/bot.kuzu` |
| `SCHEDULED_CHECK_INTERVAL` | 定时检查间隔（秒） | `30` |
| `UNDIRECTED_WINDOW_MINUTES` | 无向消息窗口（分钟） | `30` |
| `UNDIRECTED_RETENTION_MINUTES` | 无向消息保留时间（分钟） | `60` |

---

## 6. 快速开始

```powershell
# Python 3.12
py -3.12 -m venv venv && .\venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env   # 编辑填入 LLM_API_KEY
py -3.12 bot.py
```

启动后，打开浏览器访问 `http://127.0.0.1:8080/admin`（默认端口）即可进入 Web 管理后台。

---

## 7. Web 管理后台

Bot 启动后会自动在 NoneBot2 内置的 FastAPI 应用上挂载 `/admin` 路径，无需额外进程。

### 7.1 功能

- **仪表盘**：统计事件、数据点、原始消息、格式语言、行动日志数量及活跃事件数。
- **事件管理**：查看事件列表与详情，手动结算或取消活跃事件。
- **数据点浏览器**：查看所有 `DataPoint` 节点，支持按事件和类型筛选。
- **消息流**：查看原始群聊消息，支持筛选有向/无向消息。

### 7.2 API

所有页面数据通过 `/admin/api/*` 提供：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/admin/api/health` | GET | 健康检查 |
| `/admin/api/stats` | GET | 统计信息 |
| `/admin/api/events` | GET | 事件列表 |
| `/admin/api/events/{id}` | GET | 事件详情 |
| `/admin/api/events/{id}/settle` | POST | 结算事件 |
| `/admin/api/events/{id}/cancel` | POST | 取消事件 |
| `/admin/api/datapoints` | GET | 数据点列表（支持 `event_id`、`dp_type`、`limit`） |
| `/admin/api/datapoints/{id}` | GET | 数据点详情 |
| `/admin/api/messages` | GET | 原始消息（支持 `group_id`、`is_directed`、`limit`） |

> ⚠️ 安全提示：管理后台默认无鉴权，建议仅在本地或可信内网使用。如需公网暴露，请在 NoneBot2/FastAPI 前增加反向代理鉴权（如 Nginx Basic Auth）。

---

## 8. 测试

77 个测试全部通过，覆盖三层：

| 层 | 测试数 | 测试内容 |
|---|--------|----------|
| **单元测试** (`tests/unit/`) | 68 | DSL 全部原语、图的每种命名查询、推理引擎前向/后向链、ActionHandler 全部动作、Web 管理后台 API |
| **集成测试** (`tests/integration/`) | 9 | 支出→balance→debt 完整链路、穿透还款多场景、参与者自动补入 |
| **E2E** | 0 | 暂延迟：需要启动 bot 实例和真实 LLM |

运行方式：
```powershell
py -3.12 -m pytest tests/ -v
```

🎯 零 LLM 消耗：所有测试通过 Mock Translator + 内存 kuzu 数据库执行，不使用任何 API 调用。

---

## 9. 技术栈

- **Bot 框架**: NoneBot2 + OneBot V11
- **图数据库**: kuzu（嵌入式，零配置）
- **推理引擎**: 前向链 + 后向链（含穿透债务、跨事件检索）
- **LLM**: DeepSeek / OpenAI 兼容 API
- **任务调度**: APScheduler
- **Web 服务**: FastAPI + uvicorn