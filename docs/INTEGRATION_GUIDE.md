# World Cup Chat Server 接入指南

这份文档面向两类接入方：

- 人类工程师：需要知道怎么从业务系统调用 Chat Server。
- Agent / 自动化程序：需要按稳定接口发起聊天、订阅流、恢复会话、查询状态。

本文描述的是当前已经实现的接口契约，不描述未来正式认证、租户、计费系统。

## 1. 服务定位

World Cup Chat Server 是一个中心化的世界杯比赛预测 Agent Chat 服务。它负责：

- 接收聊天请求。
- 维护用户、会话、消息、运行状态。
- 调用底层模型，DockerHost 测试环境可以使用 mock 或 Z.AI `glm-5.2`。
- 支持 SSE / WebSocket 流式返回。
- 支持按 `conversation_id` 继续上下文。
- WC2026 对话按 `conversation_id` 和 `match_id` 一一绑定；同一个
  conversation 不能切换到另一场比赛。
- 普通聊天会按服务端配置透明使用内部世界杯预测知识库。
- 默认 Agent 输出只读赛前分析、证据账本、比分/WDL 概率、Polymarket 市场解释和 no-bet 条件；不代用户下单。
- 暴露健康检查和 Prometheus metrics。

接入方不需要自己保存完整消息历史，但应该保存当前用户正在使用的
`conversation_id`。如果接入方丢失了 `conversation_id`，可以通过会话列表接口重新查询。
WC2026 比赛页也可以用 `user_uuid + match_id` 找回该用户在某场比赛下的
`conversation_id`。

## 2. Base URL

当前已部署的 DockerHost 接入地址：

```text
https://api-chris-world-cup-chat-server.dkhost.vixmk-yo.org
```

环境信息：

| 字段 | 值 |
| --- | --- |
| DockerHost environment | `chris-world-cup-chat-server` |
| DockerHost web service | `api` |
| Base URL | `https://api-chris-world-cup-chat-server.dkhost.vixmk-yo.org` |
| Deployed Git ref | `main`；精确 commit 以 DockerHost `envctl status` 为准。 |
| Provider | Z.AI `glm-5.2` |
| Provider readiness | `/readyz` reports `provider_secret=configured` for single-key deployments or `provider_secret=key_pool` with `provider_key_pool=configured:<slot_count>` for multi-key deployments |
| WC2026 central API | 当前 DockerHost 环境使用 `https://moss-dev.moss.site/api/v1`，并已通过 DockerHost secret 注入 `WC2026_AGENT_API_KEY`。 |
| Verified at | `2026-06-30 16:24 Asia/Shanghai` |

下文示例统一使用：

```bash
export BASE_URL="https://api-chris-world-cup-chat-server.dkhost.vixmk-yo.org"
export USER_UUID="alice.internal"
```

已验证：

```bash
curl -fsS "$BASE_URL/healthz"
curl -fsS "$BASE_URL/readyz"
```

`/healthz` 返回 `{"status":"ok"}`，`/readyz` 返回 `status=ready`，并确认
DB、Redis、event bus、provider secret、provider limiter、reaper 已就绪。

DockerHost 地址默认视为测试/预发地址，除非运维明确声明为稳定生产入口。历史占位形态
`https://api-<owner>-world-cup-chat-server.dkhost.vixmk-yo.org` 不能直接使用，必须替换为上面的实际地址。

## 3. 当前身份模型

当前版本还没有正式登录态、OAuth、租户、API Key 管理系统。服务现在采用
URL-derived identity。普通 WC2026 chat-flow 接口必须把用户 uid 放在 URL query：

```http
?user_uuid=<user_uuid>
```

规则：

- `user_uuid` 会被服务当成内部 `user_id`。
- `user_uuid` 会写入数据库，用于 conversation、run、stream 的资源归属。
- 同一个用户继续会话、查会话、查 run、订阅 stream，都必须传同一个 `user_uuid`。
- 这不是正式认证，只是“上游服务已经完成认证后，把内部用户 ID 传给 Chat Server”的占位方式。
- 普通 WC2026 chat-flow 不再接受 `Authorization` 或 `X-API-Key` 作为用户身份来源。
- `/rag/*` 不是普通用户接口，只允许内部知识库管理员或内部 ingestion Agent 访问，仍使用 header 认证。

示例：

```http
POST /api/v1/wc2026/chat?user_uuid=alice.internal
```

这会被服务理解为：

```text
user_id = alice.internal
```

公开端点：

- `GET /healthz`
- `GET /readyz`
- `GET /metrics`
- `GET /docs`
- `GET /redoc`
- `GET /openapi.json`

普通 WC2026 chat-flow 端点需要 URL `user_uuid`。内部 `/rag/*` 端点需要身份 header。
`user_uuid` 如果包含特殊字符，接入方应按 URL query 参数规则编码。

## 4. 中心化数据模型

为了支持继续对话，Chat Server 内部有中心化持久化表。接入方不直接访问数据库，只通过 API 使用。

核心对象：

| 对象 | 用途 |
| --- | --- |
| `conversation` | 会话。归属于某个 `user_uuid` 对应的内部 `user_id`，用于多轮上下文。 |
| `conversation.wc2026_match_id` | WC2026 会话绑定的当前比赛 id，用于 `user_uuid + match_id` 找回 conversation。 |
| `message` | 会话里的用户消息和 assistant 消息。 |
| `agent_run` | 一次模型/Agent 执行。包含状态、trace、错误、plan。 |
| `idempotency_record` | 防止客户端重试导致重复创建 run。 |
| `knowledge_base` | 内部 RAG 知识库，通常归属于内部上传/运维身份。 |
| `rag_document` / `rag_document_chunk` | 内部 RAG 文档和切片。 |
| `rag_ingestion_job` | 文档向量化摄取任务。 |

关系：

```text
user_id
  └─ conversation(wc2026_match_id)
       ├─ message[]
       └─ agent_run[]

internal_rag_owner_user_id
  └─ knowledge_base
       └─ rag_document
            └─ rag_document_chunk[]
```

继续对话依赖 `conversation_id`：

- 第一次 `POST /api/v1/wc2026/chat?user_uuid=<user_uuid>` 不传 `conversation_id` 时，服务会自动创建一个新 conversation。
- 如果请求没有传 `conversation_id`，但同一个 `user_uuid` 已经有一个
  conversation 绑定到 `wc2026_context.current_match_id`，服务会自动复用这个
  conversation，而不是再创建一个新的。
- 新建或首次绑定 WC2026 conversation 时，服务会把
  `wc2026_context.current_match_id` 持久化到 `conversation.wc2026_match_id`；
  查询时优先走 `(user_id, wc2026_match_id)` 索引。
- 数据库对非空 `conversation.wc2026_match_id` 施加
  `user_id + wc2026_match_id` 唯一约束；同一用户同一比赛并发首轮请求只会落到一个
  conversation。realtime 新会话创建前也会按 `user_uuid + match_id` 加锁。
- 返回体里会给出 `conversation_id`。
- 后续请求传这个 `conversation_id`，服务会加载该会话历史，再进行新一轮 Agent 执行。
- 如果接入方不知道有哪些 conversation，可以调用 `GET /api/v1/wc2026/conversations?user_uuid=<user_uuid>` 查询当前用户下的会话列表。
- 如果接入方在比赛页只有 `match_id`，可以调用
  `GET /api/v1/wc2026/conversations?user_uuid=<user_uuid>&match_id=<match_id>`
  找回该用户这场比赛的 conversation。

### 4.1 中心化 Agent 数据接口配置

Chat Server 通过中心化 WC2026 Agent 数据接口给模型提供两类只读工具：

| 工具 | 中心化接口 | 用途 | 是否接受 `match_id` |
| --- | --- | --- | --- |
| `get_wc2026_model_methodology` | `GET /api/v1/wc2026/agent/methodology` | 回答模型方法论、k=0.943、9 个维度权重等跨场问题。 | 否 |
| `get_current_wc2026_match_context` | `GET /api/v1/wc2026/agent/match-context/{current_match_id}` | 获取当前比赛的模型概率、推荐、9D、行情、风险等快照。 | 否，match id 只来自服务端注入的 `wc2026_context.current_match_id` |

运行环境需要配置：

```bash
WC2026_AGENT_API_BASE_URL=https://moss-dev.moss.site/api/v1
WC2026_AGENT_API_KEY=<可选；中心化环境要求 wc-api-key 时配置>
WC2026_AGENT_API_TIMEOUT_S=10
```

`WC2026_AGENT_API_BASE_URL` 可以填 origin base，例如
`http://viki-api:8080`；也兼容同事文档里的 API base，例如
`https://moss-dev.moss.site/api/v1`。Chat Server 会保证最终请求路径是：

```text
/api/v1/wc2026/agent/methodology
/api/v1/wc2026/agent/match-context/{match_id}
```

`WC2026_AGENT_API_KEY` 是可选机器对机器密钥。配置后 Chat Server 会在中心化请求里发送
`wc-api-key` header；为空时 Chat Server 不发送该 header，直接请求中心化接口。
如果目标中心化环境要求 key，则缺少或错误 key 会由中心化接口返回 403，Chat Server
会 fail-closed。
当前 `chris-world-cup-chat-server` DockerHost 环境已配置
`WC2026_AGENT_API_BASE_URL=https://moss-dev.moss.site/api/v1`，并已通过
DockerHost secret 注入 `WC2026_AGENT_API_KEY`；真实 key 不写入仓库或文档。
已在 DockerHost 环境验证 `methodology` 和 `match-context/81|82|83` 均返回
`code=200`。如果目标入口缺少或拒绝 `wc-api-key`，已解锁场次调用 paid
`match-context` 会 fail-closed，不会编造或泄露内部错误。
中心化接口网络错误、超时或 transport 异常会在 Agent 工具结果中统一返回
`WC2026_AGENT_DATA_UNAVAILABLE`，不会把内部 URL、host 或异常字符串透传给模型；服务端日志只保留结构化错误类型和定位字段。

已有 Postgres volume 升级到该版本时，需要先执行：

```text
app/db/migrations/2026-06-30-wc2026-conversation-match-binding.sql
```

新建环境会通过 `app/db/init.sql` 自动包含同样的列和索引。

## 5. 最常见接入流程

```text
1. 业务系统确定内部 `user_uuid`
2. 上游代理注入可信 `wc2026_context`
3. `POST /api/v1/wc2026/chat?user_uuid=<user_uuid>`
4. 保存返回的 conversation_id 和 agent_run_id
5. 订阅 stream_url，读取 TOKEN 和 RUN_COMPLETED
6. 用户继续追问时，再调用同一个 chat URL，并传 conversation_id 和同一场比赛的 wc2026_context
7. 如果页面刷新或客户端丢失状态，用 conversations 接口按 match_id 找回会话
```

## 6. 发起聊天并接收流式返回

### 6.1 新建会话并聊天

不传 `conversation_id` 时，如果当前用户和当前比赛已有会话，服务会自动复用；
否则服务会自动创建新会话。WC2026 chat endpoint 只做异步受理，
成功后立即返回 `202`，接入方要马上用返回的 `stream_url` 或 `ws_url` 接收结果。

```bash
curl -sS -X POST "$BASE_URL/api/v1/wc2026/chat?user_uuid=$USER_UUID" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: chat-001' \
  -d '{
    "message": "请用三句话介绍一下这个世界杯预测 Chat Server 的能力。",
    "stream": true,
    "wc2026_context": {
      "current_match_id": "75",
      "current_match": {
        "id": "75",
        "fd_match_id": "fd-75",
        "description": "阿根廷 vs 法国",
        "stage": "final",
        "stage_label": "决赛",
        "home": {"name": "阿根廷", "short_name": "ARG"},
        "away": {"name": "法国", "short_name": "FRA"},
        "is_unlocked": true
      },
      "entitlements": {
        "has_all": false,
        "unlocked_matches": ["75"],
        "locked_matches": []
      }
    },
    "metadata": {
      "mode": "realtime",
      "task_type": "chat"
    }
  }'
```

请求字段：

```json
{
  "conversation_id": "可选；继续旧会话时传",
  "match_id": "可选；只用于校验，必须和 wc2026_context.current_match_id 一致",
  "message": "必填；用户消息，不能为空",
  "stream": true,
  "wc2026_context": {
    "current_match_id": "必填；当前比赛 id",
    "current_match": {
      "id": "必填；必须等于 current_match_id",
      "description": "当前比赛描述",
      "home": {"name": "主队名"},
      "away": {"name": "客队名"},
      "is_unlocked": true
    },
    "entitlements": {
      "has_all": false,
      "unlocked_matches": ["75"],
      "locked_matches": []
    }
  },
  "metadata": {
    "mode": "auto | realtime | batch",
    "task_type": "chat | file_analysis | slow_tool | batch"
  }
}
```

普通接入方不要传 `metadata.knowledge_base_id`。服务端会根据
`RAG_DEFAULT_KNOWLEDGE_BASE_ID` 决定是否在 Agent 运行中检索内部知识库；
默认情况下客户端传入的 `knowledge_base_id` 会被忽略。

`wc2026_context` 必须由上游代理层注入，不能信任前端原样传来的值。Chat Server
会使用这个上下文完成这些事：

- `conversation_id` 和 `current_match_id` 一一绑定。
- 当前比赛未解锁时，chat-flow 直接返回 `403 WC2026_MATCH_LOCKED`，不会创建或复用
  conversation，不会创建 run，也不会调用 Agent 或中心化接口。
- 当前比赛已解锁时，Block B 概率、Block D 推荐、9D 数值一起解锁。
- 如果未来启用中心化 locked payload，Chat Server 仍会对 Block D 推荐字段做防御性掩码，
  覆盖 `polymarket_implied_probability`、`probability_gap_pp`、`decimal_odds`、
  `expected_value` 等付费字段。
- 模型方法论问题走中心化 methodology 接口，但仍必须先满足当前比赛已解锁的 chat-flow
  入口权限。

如果缺少 `wc2026_context`，返回 `422 WC2026_CONTEXT_REQUIRED`。如果当前比赛
`is_unlocked=false`，返回 `403 WC2026_MATCH_LOCKED`。如果请求体里的
`match_id` 和 `wc2026_context.current_match_id` 不一致，返回
`422 WC2026_MATCH_ID_MISMATCH`。

响应是 HTTP `202`：

```json
{
  "conversation_id": "conv_xxx",
  "agent_run_id": "run_xxx",
  "trace_id": "trace_xxx",
  "status": "PENDING",
  "stream_url": "/api/v1/wc2026/chat/stream/run_xxx?user_uuid=alice.internal",
  "ws_url": "/api/v1/wc2026/chat/ws/run_xxx?user_uuid=alice.internal",
  "route_type": "realtime"
}
```

接入方应该保存：

- `conversation_id`：后续继续对话使用。
- `agent_run_id`：查询这次运行状态、订阅流使用。
- `trace_id`：排查问题时给服务端定位日志。

### 6.2 用 SSE 接收 token 和最终答案

拿到上一步返回的 `stream_url` 后立即订阅：

```bash
curl -N "$BASE_URL/api/v1/wc2026/chat/stream/run_xxx?user_uuid=$USER_UUID"
```

SSE frame 示例：

```text
id: 1782112292712-0
event: TOKEN
data: {"event_id":"evt_xxx","agent_run_id":"run_xxx","trace_id":"trace_xxx","type":"TOKEN","stream_id":"1782112292712-0","seq":6,"ts":1782112292.7,"data":{"token":"hello"}}
```

客户端从 `TOKEN.data.token` 读取增量文本：

```json
{
  "type": "TOKEN",
  "data": {
    "token": "文本片段"
  }
}
```

成功终止事件：

```json
{
  "type": "RUN_COMPLETED",
  "data": {
    "status": "SUCCEEDED",
    "content": "完整最终答案"
  }
}
```

失败终止事件：

```json
{
  "type": "ERROR",
  "data": {
    "stage": "provider_rate_limit | runner | stream_replay | agent",
    "error": "machine-readable error"
  }
}
```

重要事件：

- `RUN_STARTED`
- `PLANNING_STARTED`
- `RETRIEVAL_STARTED`
- `RETRIEVAL_FINISHED`
- `TOOL_CALL_STARTED`
- `TOOL_CALL_FINISHED`
- `LLM_GENERATING`
- `TOKEN`
- `RESULT_COMPOSED`
- `RUN_COMPLETED`
- `ERROR`

### 6.3 用 WebSocket 接收事件

如果接入方更适合 WebSocket，可以使用响应里的 `ws_url`：

```text
ws(s)://<host>/api/v1/wc2026/chat/ws/run_xxx?user_uuid=alice.internal
```

每条 WebSocket 消息是完整 `AgentEvent` JSON；处理规则和 SSE 相同：拼接
`TOKEN.data.token`，收到 `RUN_COMPLETED` 或 `ERROR` 后结束本轮。

### 6.4 断线续连和结果恢复

SSE 的 `id` 是 Redis Stream id。客户端应该保存最后一个 SSE `id`，重连时带：

```http
Last-Event-ID: <last_sse_id>
```

WebSocket 需要从 cursor 恢复时：

```text
ws(s)://<host>/api/v1/wc2026/chat/ws/run_xxx?user_uuid=alice.internal&last_event_id=<stream_id>
```

如果 cursor 已超过服务端保留窗口，服务端会返回：

```json
{
  "type": "ERROR",
  "data": {
    "stage": "stream_replay",
    "error": "STREAM_GAP"
  }
}
```

此时不要再尝试 replay token，应该查询：

- `GET /api/v1/wc2026/runs/{agent_run_id}?user_uuid=<user_uuid>`
- `GET /api/v1/wc2026/conversations/{conversation_id}?user_uuid=<user_uuid>`

### 6.5 继续已有会话

把上一次返回的 `conversation_id` 放进请求：

```bash
curl -sS -X POST "$BASE_URL/api/v1/wc2026/chat?user_uuid=$USER_UUID" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: chat-002' \
  -d '{
    "conversation_id": "conv_xxx",
    "message": "基于上一轮回答，再展开讲一下 RAG 链路。",
    "stream": true,
    "wc2026_context": {
      "current_match_id": "75",
      "current_match": {
        "id": "75",
        "description": "阿根廷 vs 法国",
        "home": {"name": "阿根廷"},
        "away": {"name": "法国"},
        "is_unlocked": true
      },
      "entitlements": {"has_all": false}
    },
    "metadata": {
      "mode": "realtime"
    }
  }'
```

注意：

- `conversation_id` 必须属于当前 URL `user_uuid` 对应的用户。
- `wc2026_context.current_match_id` 必须和该 conversation 已绑定的 match id 一致。
- 如果会话归属不匹配，返回 `403`。
- 如果同一个 conversation 被拿去问另一场比赛，返回 `409 WC2026_CONVERSATION_MATCH_CONFLICT`。
- 同一 conversation 的 realtime run 会串行化；如果上一轮还没结束，可能返回 `409 CONVERSATION_BUSY`。
- 不传 `conversation_id` 的同用户同比赛 realtime 首请求也按 `user_uuid + match_id`
  串行化，避免并发重复创建 conversation。

### 6.6 不支持同步等待

WC2026 chat endpoint 只做异步受理。`stream` 可以省略或传 `true`，但不能传 `false`。

如果传：

```json
{ "stream": false }
```

服务会返回：

```json
{ "detail": "STREAM_FALSE_NOT_SUPPORTED" }
```

内部脚本也应该先拿 `agent_run_id`，再订阅 `stream_url`，或用
versioned runs 和 conversations 接口做状态与结果恢复。

### 6.7 幂等重试

建议所有可重试的 WC2026 chat 请求都带：

```http
Idempotency-Key: <client-generated-stable-key>
```

语义：

- 同一个 `user_uuid` + 同一个 `Idempotency-Key` + 相同 payload：返回原始 run。
- 同一个 `user_uuid` + 同一个 `Idempotency-Key` + 不同 payload：返回 `409 IDEMPOTENCY_CONFLICT`。

## 7. 会话查询接口

这部分是继续对话能力的关键。Chat Server 会保存 `user_uuid` 对应用户下的 conversation 和 message，接入方可以通过 API 查询。

### 7.1 创建空会话

一般不必手动创建，因为 WC2026 chat endpoint 不带 `conversation_id` 时会自动创建。需要先建一个带标题的空会话时，可以调用：

```bash
curl -sS -X POST "$BASE_URL/api/v1/wc2026/conversations?user_uuid=$USER_UUID" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Support analysis"
  }'
```

响应：

```json
{
  "id": "conv_xxx",
  "user_id": "alice.internal",
  "title": "Support analysis",
  "wc2026_match_id": null,
  "created_at": "2026-06-22T07:00:00Z",
  "updated_at": "2026-06-22T07:00:00Z"
}
```

### 7.2 查询当前用户的会话列表

```bash
curl -sS "$BASE_URL/api/v1/wc2026/conversations?user_uuid=$USER_UUID&limit=20&offset=0"
```

返回当前 `user_uuid` 对应用户下的会话列表，按更新时间倒序：

```json
[
  {
    "id": "conv_xxx",
    "user_id": "alice.internal",
    "title": null,
    "wc2026_match_id": "75",
    "created_at": "2026-06-22T07:00:00Z",
    "updated_at": "2026-06-22T07:05:00Z"
  }
]
```

分页参数：

- `limit`: 1 到 100，默认 20。
- `offset`: 从 0 开始，默认 0。
- `match_id`: 可选；传入后只返回当前用户下绑定到该比赛的 conversation。

比赛页恢复会话时推荐这样查：

```bash
curl -sS "$BASE_URL/api/v1/wc2026/conversations?user_uuid=$USER_UUID&match_id=75&limit=1"
```

如果返回数组非空，取第一条的 `id` 作为继续聊天的 `conversation_id`。如果返回空数组，
说明这个用户还没有在该比赛下创建过会话，可以直接发起不带 `conversation_id` 的 chat 请求。

### 7.3 查询单个会话及消息

```bash
curl -sS "$BASE_URL/api/v1/wc2026/conversations/conv_xxx?user_uuid=$USER_UUID"
```

响应包含会话和消息列表：

```json
{
  "id": "conv_xxx",
  "user_id": "alice.internal",
  "title": null,
  "wc2026_match_id": "75",
  "created_at": "2026-06-22T07:00:00Z",
  "updated_at": "2026-06-22T07:05:00Z",
  "messages": [
    {
      "id": "msg_xxx",
      "conversation_id": "conv_xxx",
      "agent_run_id": null,
      "role": "USER",
      "content": "你好",
      "token_count": 0,
      "created_at": "2026-06-22T07:00:01Z",
      "meta": {}
    },
    {
      "id": "msg_yyy",
      "conversation_id": "conv_xxx",
      "agent_run_id": "run_xxx",
      "role": "ASSISTANT",
      "content": "你好，我是 Chat Server。",
      "token_count": 18,
      "created_at": "2026-06-22T07:00:03Z",
      "meta": {}
    }
  ]
}
```

权限语义：

- 只能查询当前 `user_uuid` 对应用户自己的 conversation。
- 其他用户的 conversation 返回 `403`。
- 不存在返回 `404`。

## 8. 运行状态查询

```bash
curl -sS "$BASE_URL/api/v1/wc2026/runs/run_xxx?user_uuid=$USER_UUID"
```

响应：

```json
{
  "agent_run_id": "run_xxx",
  "status": "PENDING | RUNNING | SUCCEEDED | FAILED | CANCELLED",
  "intent": null,
  "error": null
}
```

用途：

- SSE 断开后确认 run 是否结束。
- 客户端刷新页面后恢复状态。
- `STREAM_GAP` 后确认最终状态。

## 9. 内部 RAG 管理接口

这一组接口不是普通业务接入面。它只给内部知识库管理员、内部资料上传脚本、
或内部 ingestion Agent 使用，用于维护服务端自己的文档知识库。

普通用户和普通业务系统不要调用 `/rag/*`，也不要把 `knowledge_base_id`
放进 WC2026 chat metadata。普通聊天如果需要 RAG 增强，由服务端配置的内部知识库自动参与。

内部访问要求：

- `Authorization: Bearer <internal_rag_admin_user_id>`
- 该身份必须出现在服务端 `RAG_ADMIN_USER_IDS` 配置里。
- 没有进入白名单的身份会收到 `403 RAG_ADMIN_FORBIDDEN`。
- 直接 `/rag/query` 会返回原始 chunk，因此同样只允许内部调试/验收使用。

### 9.1 创建知识库

```bash
curl -sS -X POST "$BASE_URL/rag/knowledge-bases" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${RAG_ADMIN_AUTH_TOKEN:?set RAG_ADMIN_AUTH_TOKEN}" \
  -d '{
    "name": "Product docs",
    "description": "内部产品知识库"
  }'
```

响应：

```json
{
  "id": "kb_xxx",
  "owner_user_id": "rag_admin_user_id",
  "name": "Product docs",
  "description": "内部产品知识库",
  "status": "ACTIVE",
  "created_at": "2026-06-22T07:00:00Z",
  "updated_at": "2026-06-22T07:00:00Z"
}
```

### 9.2 查询知识库列表

```bash
curl -sS "$BASE_URL/rag/knowledge-bases" \
  -H "Authorization: Bearer ${RAG_ADMIN_AUTH_TOKEN:?set RAG_ADMIN_AUTH_TOKEN}"
```

### 9.3 导入文本/Markdown 文档

```bash
curl -sS -X POST "$BASE_URL/rag/documents" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${RAG_ADMIN_AUTH_TOKEN:?set RAG_ADMIN_AUTH_TOKEN}" \
  -d '{
    "knowledge_base_id": "kb_xxx",
    "title": "DockerHost notes",
    "content": "DockerHost 运行 API、worker、reaper、Postgres、Redis 和 pgvector。",
    "source_type": "api",
    "metadata": {
      "source": "integration-guide"
    }
  }'
```

响应：

```json
{
  "document_id": "doc_xxx",
  "job_id": "ragjob_xxx",
  "status": "PENDING",
  "replayed": false
}
```

### 9.4 查询文档摄取状态

```bash
curl -sS "$BASE_URL/rag/ingestion-jobs/ragjob_xxx" \
  -H "Authorization: Bearer ${RAG_ADMIN_AUTH_TOKEN:?set RAG_ADMIN_AUTH_TOKEN}"
```

等到：

```json
{
  "status": "SUCCEEDED"
}
```

再查文档：

```bash
curl -sS "$BASE_URL/rag/documents/doc_xxx" \
  -H "Authorization: Bearer ${RAG_ADMIN_AUTH_TOKEN:?set RAG_ADMIN_AUTH_TOKEN}"
```

文档状态应为：

```json
{
  "status": "EMBEDDED"
}
```

### 9.5 直接检索知识库

```bash
curl -sS -X POST "$BASE_URL/rag/query" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${RAG_ADMIN_AUTH_TOKEN:?set RAG_ADMIN_AUTH_TOKEN}" \
  -d '{
    "knowledge_base_id": "kb_xxx",
    "query": "DockerHost 部署里有哪些服务？",
    "top_k": 3,
    "strict": false
  }'
```

响应包含命中的 chunk：

```json
{
  "chunks": [
    {
      "chunk_id": "chunk_xxx",
      "document_id": "doc_xxx",
      "knowledge_base_id": "kb_xxx",
      "title": "DockerHost notes",
      "content": "命中的文本",
      "score": 0.83,
      "citation": {
        "source_uri": null,
        "page": null,
        "section": null,
        "chunk_index": 0
      },
      "metadata": {}
    }
  ],
  "degraded": false,
  "reason": null,
  "latency_ms": 120,
  "query_id": "optional"
}
```

### 9.6 让普通聊天使用内部知识库

创建并导入内部知识库后，由运维配置：

```bash
RAG_DEFAULT_KNOWLEDGE_BASE_ID=kb_xxx
RAG_INTERNAL_OWNER_USER_ID=rag-admin
```

配置完成后，普通 WC2026 chat 请求不需要传 KB ID。Agent 会在运行过程中自主决定是否调用
`search_knowledge`，服务端会把检索限制在配置好的内部知识库上。

## 10. 健康检查和观测

```bash
curl -sS "$BASE_URL/healthz"
curl -sS "$BASE_URL/readyz"
curl -sS "$BASE_URL/metrics"
```

`/healthz` 只表示 API 进程还活着。

`/readyz` 是接流量前应该看的就绪检查，包含：

- `db`
- `redis`
- `event_bus`
- `provider_secret`
- `provider_key_pool`
- `provider_limiter`
- `reaper`

单 key 真实模型链路下，`provider_secret` 应该是：

```json
"configured"
```

多 key 链路下，`provider_secret` 应该是 `key_pool`，并且 `provider_key_pool` 应该类似：

```json
"configured:2"
```

`/metrics` 返回 Prometheus text。常用指标：

- `chat_ttft_seconds_*`
- `provider_rate_limit_decisions_total`
- `provider_rate_limit_tokens_reserved_total`
- `provider_rate_limit_tokens_settled_total`
- `redis_stream_events_total`
- `runner_active_runs`
- `runner_timeouts_total`
- `reaper_runs_total`

## 11. 常见错误

HTTP 状态：

| 状态码 | 含义 |
| --- | --- |
| `401` | WC2026 chat-flow 缺少 URL `user_uuid`，或内部接口缺少身份 header。 |
| `403` | 当前用户无权访问该资源，或当前比赛未解锁。 |
| `404` | run、conversation、knowledge base、document 或 job 不存在。 |
| `409` | conversation busy、同一 conversation 切换比赛、知识库不可用、幂等冲突。 |
| `422` | 请求体不合法，常见为空 message / content / query、缺少 `wc2026_context`、`match_id` 不一致，或 `stream=false`。 |
| `429` | 用户级或 provider 级限流。 |
| `503` | 队列、Redis、provider limiter 或 guardrail 不可用。 |

常见 machine-readable detail / error：

- `CONVERSATION_BUSY`
- `IDEMPOTENCY_CONFLICT`
- `WC2026_CONTEXT_REQUIRED`
- `WC2026_MATCH_LOCKED`
- `WC2026_MATCH_ID_MISMATCH`
- `WC2026_CONVERSATION_MATCH_CONFLICT`
- `PROVIDER_LIMITER_UNAVAILABLE`
- `RAG_ADMIN_FORBIDDEN`
- `RAG_QUEUE_UNAVAILABLE`
- `STREAM_FALSE_NOT_SUPPORTED`
- `KNOWLEDGE_BASE_DISABLED`
- `STREAM_GAP`
- `RUN_TIMEOUT`

## 12. Agent 接入清单

1. 设置 `BASE_URL`。
2. 选择稳定的内部 `user_uuid`。
3. 普通 chat-flow 请求把 `user_uuid` 放进 URL query。
4. 上游代理层注入可信 `wc2026_context`，不要信任前端原始上下文。
5. 调用 `/api/v1/wc2026/chat` 时带 `Idempotency-Key`。
6. 新聊天不传 `conversation_id`，服务会按 `user_uuid + match_id` 自动复用或创建。
7. 保存返回的 `conversation_id`，后续追问必须传回同一场比赛的上下文。
8. 页面刷新时用 `/api/v1/wc2026/conversations?match_id=<match_id>` 找回 conversation。
9. 保存返回的 `agent_run_id`，用于订阅 stream 和查询 run。
10. 订阅 `stream_url`，读取 `TOKEN.data.token`。
11. 收到 `RUN_COMPLETED` 后，使用 `data.content` 作为最终答案。
12. 页面刷新或本地状态丢失时，调用 versioned conversations 列表找回会话。
13. 需要完整历史时，调用 versioned conversation detail。
14. SSE 断线时，用最后一个 SSE `id` 作为 `Last-Event-ID` 重连。
15. 遇到 `STREAM_GAP`，改查 versioned runs 和 conversations。
16. 不要调用 `/rag/*` 或传 `metadata.knowledge_base_id`；RAG 由服务端内部知识库配置透明生效。
17. 接流量前检查 `/readyz`，排障时查看 `/metrics`。
