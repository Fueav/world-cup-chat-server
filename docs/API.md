# World Cup Chat Server — 前端接口文档

> 版本:0.1.0 ｜ 适用后端:PydanticAI 驱动的世界杯预测 agentic 执行平台
> 本文档面向前端对接,描述全部 HTTP 端点、事件流(SSE / WebSocket)、鉴权、错误码与集成示例。

## 0. 核心模型(必读)

这是一个**异步 Agent 平台**,不是同步问答接口。一次对话的生命周期:

```
POST /chat ──202──▶ 返回 agent_run_id + stream_url
                         │
                         ▼
            订阅 SSE / WebSocket 实时事件流
            (RUN_STARTED → 工具调用 → TOKEN 逐字 → RUN_COMPLETED)
```

- 提交后**立即返回**(HTTP 202),默认实时 Chat 的推理(LLM 自主检索 / 调用工具 / 流式生成)由常驻 async RealtimeRunner 执行;慢任务/批任务继续走后台 Worker。
- 前端通过 **SSE 或 WebSocket** 订阅 `agent_run_id` 的事件流,拿到逐 token 输出与工具调用进度。
- 不支持同步等待结果。脚本和前端都必须按 `agent_run_id` 使用事件流、状态接口或会话历史恢复结果。

---

## 1. 通用约定

### Base URL
```
http://localhost:8000        # 本地默认,按部署环境替换
```

### 鉴权(所有业务端点必需)
除健康检查与文档外,所有端点都需在请求头携带凭证之一,**否则 401**:

```
Authorization: Bearer <token>
# 或
X-API-Key: <key>
```

> ⚠️ Demo 模式:`token`/`key` 的**值本身被当作 user_id**,任意非空字符串即可通过(用于区分用户与限流)。生产环境会替换为真实校验。

豁免鉴权的公开路径:`/healthz`、`/readyz`、`/docs`、`/redoc`、`/openapi.json`。

### Trace ID
- 每个响应都会回写头 `X-Trace-Id`,用于排查问题,建议前端日志记录。
- 可在请求头主动传 `X-Trace-Id` 透传(不传则后端生成)。

### CORS
后端开启宽松 CORS(`allow_origins: *`),前端可跨域直连(生产会收敛)。
浏览器 `OPTIONS` preflight 会在鉴权前由 CORS 中间件处理;真正的 `POST /chat` 仍必须携带 `Authorization: Bearer <token>` 或 `X-API-Key`。

### 限流
仅对 `POST /chat` 限流(按 route + user_id 滑动窗口,默认 **60 次/分钟**)。超限返回 **429**:
```
HTTP 429
Retry-After: <秒>
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
```
正常响应也会带 `X-RateLimit-Limit` / `X-RateLimit-Remaining`。
该入口限流是 best-effort 防滥用保护;Redis 异常时会降级放行并打告警/指标。真实 LLM provider 的 RPM/TPM 与成本保护由独立 provider limiter 执行,不依赖这里的 fail-open 行为。

### 统一错误体
```json
{ "detail": "错误说明" }
```

| 状态码 | 含义 |
|--------|------|
| 401 | 缺少/无效鉴权凭证 |
| 403 | 无权访问该资源(会话归属不符) |
| 404 | 资源不存在 |
| 422 | 请求体校验失败(如 message 为空或 `stream:false`) |
| 429 | 触发限流 |
| 503 | 任务队列 / 依赖未就绪 |

---

## 2. 端点总览

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/chat` | 提交一次对话(核心) | ✅ |
| GET  | `/stream/{agent_run_id}` | SSE 事件流 | ✅* |
| WS   | `/ws/{agent_run_id}` | WebSocket 事件流 | ✅(query token) |
| GET  | `/runs/{agent_run_id}` | 查询运行状态 | ✅ |
| POST | `/conversations` | 创建会话 | ✅ |
| GET  | `/conversations/{id}` | 会话详情(含消息) | ✅ |
| GET  | `/conversations` | 会话列表(分页) | ✅ |
| GET  | `/healthz` `/readyz` | 健康检查 | ❌ |

> *SSE 鉴权对浏览器有坑,见 [§5.3](#53-浏览器集成注意鉴权与-sse)。

---

## 3. 对话接口

### 3.1 POST /chat — 提交对话(核心)

**请求头**:`Authorization: Bearer <token>`、`Content-Type: application/json`

**请求体** `ChatRequest`:

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `message` | string | ✅ | — | 用户消息(非空) |
| `conversation_id` | string \| null | ❌ | null | 续接已有会话;不传则**自动新建** |
| `stream` | boolean | ❌ | `true` | 兼容字段;省略或传 `true`;`false` 会返回 422 |
| `metadata` | object | ❌ | `{}` | 透传元数据(当前预留,后端暂未消费) |

```json
{
    "message": "阿根廷 vs 法国这场世界杯比赛怎么做赛前预测?",
  "conversation_id": null,
  "stream": true
}
```

#### 响应: **202 Accepted**(`ChatAccepted`)

```json
{
  "conversation_id": "conv_xxx",
  "agent_run_id": "run_xxx",
  "trace_id": "trace_xxx",
  "status": "PENDING",
  "stream_url": "/stream/run_xxx",
  "ws_url": "/ws/run_xxx"
}
```
> 拿到 `agent_run_id` 后,立刻用 `stream_url` 订阅 SSE,或 `ws_url` 连 WebSocket。

#### 不支持 `stream:false`

`POST /chat` 永远是异步受理接口。传 `stream:false` 会返回 **422**:

```json
{ "detail": "STREAM_FALSE_NOT_SUPPORTED" }
```

脚本场景如果不想渲染逐 token，也应该订阅到 `RUN_COMPLETED`，或轮询
`GET /runs/{agent_run_id}` 后读取 `GET /conversations/{conversation_id}` 的消息历史。

---

## 4. 事件流(SSE / WebSocket)

两种通道**推送完全相同的事件对象**,任选其一。每条事件为一个 `AgentEvent`:

```jsonc
{
  "event_id": "evt_xxx",
  "agent_run_id": "run_xxx",
  "trace_id": "trace_xxx",
  "type": "TOKEN",          // 事件类型,见下表
  "seq": 9,                  // 同一 run 内单调递增,用于排序/去重
  "ts": 1780903390.12,       // unix 秒(float)
  "data": { "token": "向量" } // 载荷,结构随 type 而定
}
```

### 4.1 事件类型与 data 载荷

| `type` | 触发时机 | `data` 字段 | 前端处理建议 |
|--------|----------|-------------|--------------|
| `RUN_STARTED` | 运行开始 | `{ message }` | 显示"思考中" |
| `PLANNING_STARTED` | 进入 agentic loop | `{}` | — |
| `RETRIEVAL_STARTED` | LLM 自主发起知识检索 | `{ query }` | 显示"检索资料…" |
| `RETRIEVAL_FINISHED` | 检索完成 | `{}` | — |
| `TOOL_CALL_STARTED` | LLM 自主调用工具 | `{ tool_name }` | 显示"调用 {tool}…" |
| `TOOL_CALL_FINISHED` | 工具返回 | `{ tool_name }` | — |
| `LLM_GENERATING` | 开始生成最终回答 | `{}` | 准备打字机 |
| **`TOKEN`** | **逐 token 输出** | `{ token }` | **拼接 token 渲染** |
| `RESULT_COMPOSED` | 回答生成完毕 | `{ length }` | — |
| **`RUN_COMPLETED`** | **运行结束(终止事件)** | `{ status, content? }` | **结束流;读 content** |
| `ERROR` | 某阶段出错(终止事件) | `{ stage, error }` | 提示错误 |

**关键规则**:
- **拼接答案** = 把所有 `TOKEN` 事件的 `data.token` 按 `seq` 顺序连起来。
- **何时结束** = 收到 `RUN_COMPLETED` 或 `ERROR` 即终止(两者都是终止事件,流随后关闭)。
- `RUN_COMPLETED` 成功时 `data = { status: "SUCCEEDED", content: "<完整答案>" }`;失败时 `data = { status: "FAILED" }`(无 content)。可用 `content` 兜底校验你拼的 TOKEN 是否完整。
- 工具调用可能发生**多次**(LLM 自主决定),前端按 START/FINISHED 成对展示进度即可。

> 📌 **agentic 改造说明**:旧的 `INTENT_RESOLVED` 事件已不再产生(意图识别被 LLM 自主推理取代);`/runs` 与运行对象里的 `intent` 字段恒为 `null`,保留仅为兼容,前端可忽略。

### 4.2 SSE:GET /stream/{agent_run_id}

标准 `text/event-stream`,每条:
```
event: TOKEN
id: 9
data: {"event_id":"evt_x","agent_run_id":"run_x","type":"TOKEN","seq":9,"ts":1780903390.1,"data":{"token":"向量"}}
```
- `event` = 事件类型,`id` = seq,`data` = 完整 `AgentEvent` JSON 字符串(需 `JSON.parse`)。
- 服务端在 `RUN_COMPLETED`/`ERROR` 后主动关闭连接。

### 4.3 WebSocket:WS /ws/{agent_run_id}

- 每帧是一条 `AgentEvent` 的 **JSON 字符串**(注意:**不带** SSE 的 `event:`/`id:` 包装,直接 `JSON.parse(frame)`)。
- 收到终止事件后服务端关闭连接。
- **鉴权**:浏览器 WebSocket 不能设自定义头,用 **query token**:
  ```
  ws://localhost:8000/ws/run_xxx?token=<你的token>
  ```

---

## 5. 前端集成示例

### 5.1 提交 + SSE(推荐:fetch-based SSE,可带鉴权头)

浏览器原生 `EventSource` **无法设置请求头**,而 `/stream` 需要鉴权头,故推荐用 [`@microsoft/fetch-event-source`](https://www.npmjs.com/package/@microsoft/fetch-event-source):

```ts
import { fetchEventSource } from '@microsoft/fetch-event-source';

const TOKEN = 'demo-user-1';
const BASE = 'http://localhost:8000';

// 1) 提交对话
const res = await fetch(`${BASE}/chat`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
  body: JSON.stringify({ message: '帮我算一下 (123+456)*7', stream: true }),
});
const { agent_run_id, stream_url } = await res.json();

// 2) 订阅事件流,拼接 TOKEN
let answer = '';
await fetchEventSource(`${BASE}${stream_url}`, {
  headers: { Authorization: `Bearer ${TOKEN}` },
  onmessage(ev) {
    const evt = JSON.parse(ev.data);            // AgentEvent
    switch (evt.type) {
      case 'TOOL_CALL_STARTED':
        console.log('调用工具', evt.data.tool_name); break;
      case 'TOKEN':
        answer += evt.data.token;                // 逐字渲染
        render(answer); break;
      case 'RUN_COMPLETED':
        console.log('完成', evt.data.status, evt.data.content); break;
      case 'ERROR':
        console.error('出错', evt.data.error); break;
    }
  },
});
```

### 5.2 WebSocket(浏览器可用 query token)

```ts
const ws = new WebSocket(`ws://localhost:8000/ws/${agent_run_id}?token=${TOKEN}`);
let answer = '';
ws.onmessage = (e) => {
  const evt = JSON.parse(e.data);               // 直接是 AgentEvent
  if (evt.type === 'TOKEN') { answer += evt.data.token; render(answer); }
  if (evt.type === 'RUN_COMPLETED' || evt.type === 'ERROR') ws.close();
};
```

### 5.3 浏览器集成注意:鉴权与 SSE

| 通道 | 浏览器能否设鉴权 | 方案 |
|------|------------------|------|
| 原生 `EventSource` | ❌ 不能设 header | 改用 `@microsoft/fetch-event-source`(带 Authorization 头) |
| WebSocket | ❌ 不能设 header | 用 `?token=<token>` query 鉴权(已支持) |
| `fetch`(普通 REST) | ✅ | 正常设 `Authorization` 头 |

> 若团队坚持用原生 `EventSource`,需后端为 `/stream` 增加 query token 支持(当前未实现)。可向后端提需求。

---

## 6. 会话与状态接口

### 6.1 POST /conversations — 创建会话(201)
请求 `{ "title": "可选标题" }` → 返回 `ConversationOut`:
```json
{ "id": "conv_xxx", "user_id": "demo-user-1", "title": "可选标题",
  "created_at": "2026-06-08T10:00:00Z", "updated_at": "2026-06-08T10:00:00Z" }
```
> 不必先建会话:`POST /chat` 不传 `conversation_id` 会自动创建。

### 6.2 GET /conversations/{id} — 会话详情(含消息)
返回 `ConversationDetailOut`(在 `ConversationOut` 基础上加 `messages`):
```json
{
  "id": "conv_xxx", "user_id": "demo-user-1", "title": null,
  "created_at": "...", "updated_at": "...",
  "messages": [
    { "id": "msg_1", "conversation_id": "conv_xxx", "role": "USER",
      "content": "帮我算一下 (123+456)*7", "token_count": 12,
      "created_at": "...", "meta": {} },
    { "id": "msg_2", "conversation_id": "conv_xxx", "role": "ASSISTANT",
      "content": "（123+456）×7 的结果是 4053。", "token_count": 18,
      "created_at": "...", "meta": {} }
  ]
}
```
> 访问非本人会话返回 403;不存在返回 404。

### 6.3 GET /conversations — 会话列表(分页)
Query:`limit`(1–100,默认 20)、`offset`(默认 0)。返回 `ConversationOut[]`(当前用户的会话)。

### 6.4 GET /runs/{agent_run_id} — 运行状态(轮询用)
返回 `RunStatusOut`:
```json
{ "agent_run_id": "run_xxx", "status": "SUCCEEDED", "intent": null, "error": null }
```
> 适合不走事件流时轮询状态;`intent` 恒为 `null`(见 §4.1 说明)。

### 6.5 GET /healthz · /readyz — 健康检查(公开)
存活/就绪探针,无需鉴权,返回 200 表示正常。

---

## 7. 枚举附录

**RunStatus**(运行状态):`PENDING` · `RUNNING` · `SUCCEEDED` · `FAILED` · `CANCELLED`
**MessageRole**(消息角色):`USER` · `ASSISTANT` · `SYSTEM` · `TOOL`
**EventType**(事件类型):见 [§4.1](#41-事件类型与-data-载荷)

---

## 8. 典型前端流程小结

```
1. (可选) POST /conversations            → 拿 conversation_id(或直接跳过)
2. POST /chat { message, stream:true }   → 拿 agent_run_id + stream_url
3. 订阅 SSE(fetch-event-source)/ WS     → 拼 TOKEN、展示工具进度
4. 收到 RUN_COMPLETED                     → 渲染最终答案(data.content)
5. (可选) GET /conversations/{id}         → 回显历史消息
```

后端默认 `LLM_PROVIDER=mock`(零 key 可跑通);切换 claude / openai / qwen / gemini 对前端**完全透明**,接口与事件流不变。
