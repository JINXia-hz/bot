# bot — 图数据库驱动的 QQ 群记账机器人

基于 **kuzu 图数据库** + **LLM** 的群聊记账助手，数据模型围绕「数据点 / 数据线 / 事件」构建。

## 核心概念

| 类型 | 说明 | 例子 |
|------|------|------|
| **无向自然语言** | 群友闲聊，无界数据流 | "下午吃啥" |
| **有向自然语言** | @bot 的消息 | "@bot 记账 50 午餐" |
| **格式语言** | LLM 输出的结构化指令 | `{"op":"record_expense","params":{...}}` |
| **数据点** | 不可变状态单元 | 一笔支出、一次预定、一个提醒 |
| **数据线** | 数据点之间的因果链 | "待付 → 已付" |
| **事件** | 管理容器 | "火锅局""三亚旅游" |
| **行动** | dpⁿ + fl → dpᵐ + dl + log | 一次记账/AA/预定操作 |

## 架构

```
群聊 → RawMessage(无向/有向) → LLM(intent判断)
                                  ├─ query → 直接自然语言回复
                                  └─ action → FL → 引擎执行 → 数据点/数据线/ActionLog
                                      └─ 输出 FL → 即时回复 / 定时回复
```

```
src/
├── graph/          # kuzu 图数据库（连接、schema、repo）
├── engine/         # 纯计算引擎（翻译、执行、事件管理）
├── pipeline/       # 流程编排（orchestrator、scheduler）
└── plugins/        # NoneBot2 插件（消息监听）
```

## 环境要求

- **Python 3.12**（kuzu 预编译 wheel 最高支持 3.13）
- QQ 小号（用于机器人登录）

## 快速开始

### 1. 安装依赖

```powershell
# 设置 UTF-8 编码（Windows 必需）
$env:PYTHONUTF8 = 1

# 创建虚拟环境（推荐）
py -3.12 -m venv venv
.\venv\Scripts\activate

# 安装
pip install -e ".[dev]"
```

### 2. 配置

```powershell
copy .env.example .env
# 编辑 .env，填入 LLM API Key
```

**.env 说明：**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | DeepSeek 或其他 OpenAI 兼容 API Key | - |
| `LLM_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `SCHEDULED_CHECK_INTERVAL` | 定时回复检查间隔（秒） | `30` |
| `UNDIRECTED_WINDOW_MINUTES` | 无向 NL 窗口（分钟） | `30` |
| `UNDIRECTED_RETENTION_MINUTES` | 无向 NL 保留时间（分钟） | `60` |
| `KUZU_DB_PATH` | 图数据库路径 | `data/bot.kuzu` |

### 3. 安装 go-cqhttp（QQ 协议端）

go-cqhttp 是连接 QQ 和 bot 的桥梁，负责接收/发送 QQ 消息。

#### 下载

从 [go-cqhttp Release](https://github.com/Mrs4s/go-cqhttp/releases) 下载最新版：

- Windows 64 位：`go-cqhttp_windows_amd64.exe`
- 如果 GitHub 打不开，用镜像：`https://ghproxy.com/` + 原链接

#### 初始化

```powershell
mkdir D:\go-cqhttp
# 把 go-cqhttp_windows_amd64.exe 放进去，双击运行
```

首次运行会提示选择通信方式，**输入 `3`（反向 WebSocket）**，程序会自动生成 `config.yml`。

#### 配置 config.yml

打开 `D:\go-cqhttp\config.yml`，修改以下部分：

```yaml
account:
  uin: 你的QQ小号        # QQ 号
  password: "你的密码"    # 注意加引号

# 反向 WebSocket 配置
servers:
  - ws-reverse:
      universal: ws://127.0.0.1:8080/onebot/v11/ws
      reconnect-interval: 3000
```

**重要：**
- 不要改 `universal` 的地址和路径——这是 bot 监听的地址
- QQ 密码建议用小号的，避免主号被封

#### 启动 go-cqhttp

```powershell
cd D:\go-cqhttp
.\go-cqhttp_windows_amd64.exe
```

首次启动会要求扫码登录。成功后会看到类似 `连接到反向 WebSocket 服务器` 的日志。

### 4. 启动 bot

```powershell
cd D:\projects\bot
py -3.12 bot.py
```

看到 `Application startup complete.` 和 `Uvicorn running on http://127.0.0.1:8080` 即可。

### 5. 测试

群里 @bot 发送：

```
@bot 记账 50 午餐
@bot 花了 25.5 打车
@bot 收入 200 兼职
@bot AA 火锅局 200 @张三 @李四
@bot 我欠多少
```

## 支持的命令

| 操作 | 示例 |
|------|------|
| 记支出 | `@bot 记账 50 午餐` |
| 记收入 | `@bot 收入 200 兼职` |
| 发起 AA | `@bot AA 火锅局 200 @张三 @李四` |
| 付款 | `@bot 结账 火锅局` |
| 查询 | `@bot 我欠多少` `@bot 统计一下` |
| 提醒 | `@bot 提醒 明天下午3点 开会` |

LLM 还会自动判断何时开启/结算事件，不需要手动命令。

## 技术栈

- **Bot 框架**: NoneBot2 + OneBot V11
- **图数据库**: kuzu（嵌入式，零配置）
- **LLM**: DeepSeek / OpenAI 兼容 API
- **任务调度**: APScheduler
- **Web 服务**: FastAPI + uvicorn

## License

GPL
