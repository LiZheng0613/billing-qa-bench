# Agent 真实报文对接配置指南

> 适用场景：被测 Agent 平台「先取 conversationId → 首轮对话 → 回填 sessionid → 后续多轮」的对接方式。
> 平台版本：包含 `timestamp_iso` 运行时变量、环境级 Basic 鉴权、前置接口 `extract` 多字段提取（迁移 `a6c9e3f8b2d5`）。
> 本地 Mock 已内置等价接口，可按本文先离线跑通再切换真实环境。

---

## 1. 目标流程

```
① 前置接口  POST /api/v1/conversation
   Header: Authorization: Basic base64(账号:密码)
   Body:   {"userid": "...", "custid": "..."}          ← 可按用例取不同值
   → 返回 conversationId

② 首轮对话  POST /api/v1/agent/chat
   Header: Authorization: Basic base64(账号:密码)
   Body:   {
             "conversationId": "xxx",
             "message": {
               "content": "用户输入",
               "type": "text",
               "xxx": "固定值",
               "agent": "opencode",
               "model": "ds-xxx",
               "sessionid": "",                        ← 首轮空字符串 = 首轮对话
               "metadata": {
                 "timestampe": "2026-11-17T18:00:00.123",   ← ISO 时间戳，每轮刷新
                 "requestcontext": "userid:xxx,custid:xxx,beid:101"
               }
             }
           }
   → 响应返回 sessionid

③ 后续轮次  同上，message.sessionid 填 ② 返回值，其余字段不变
```

平台对应能力：

| 需求 | 平台能力 |
| --- | --- |
| 前置取 `conversationId` | 环境「前置接口钩子」，`extract` 支持多字段 |
| Basic 鉴权 | 环境「Basic 鉴权」，配置账号密码即可 |
| 动态时间戳 | 运行时变量 `{{timestamp_iso}}` |
| 按用例变化 userid/custid | 用例级 `request_variables` |
| 首轮空 sessionid、次轮回填 | 环境 `session_mode = SERVER_RETURNED` |

---

## 2. 环境配置

进入「设置 → 环境」，新建或编辑环境。

### 2.1 基础字段

| 字段 | 值 |
| --- | --- |
| Base URL | `https://<agent-gateway>`（本地 Mock：`http://127.0.0.1:8002`） |
| 接口方法 / 路径 | `POST` / `/api/v1/agent/chat`（按实际调整） |
| 超时 | 按接口 SLA 设置，默认 60s |
| 会话绑定 | **服务端返回会话**（`SERVER_RETURNED`） |

### 2.2 Basic 鉴权

勾选「启用 Basic 鉴权」，填写账号与密码：

- 密码加密存储，接口返回只显示 `password_set: true`；
- 编辑时留空表示不修改密码；
- 执行时自动生成 `Authorization: Basic base64(账号:密码)`，主对话请求与前置接口请求都会携带；
- 如果请求头模板里显式写了 `Authorization`，以模板为准（一般不需要）。

### 2.3 mTLS 双向证书认证

当被测接口要求 **mTLS 双向证书**（例如 `client_cert.pem` + `client_key.pem`）时，勾选「启用 mTLS 双向证书」，填写或上传：

- **客户端证书**：`client_cert.pem`（PEM，包含 `-----BEGIN CERTIFICATE-----`）；
- **客户端私钥**：`client_key.pem`（PEM，支持 `PRIVATE KEY` / `RSA PRIVATE KEY` / `EC PRIVATE KEY` / `ENCRYPTED PRIVATE KEY`）；
- **私钥口令**（可选）：加密私钥的口令；
- **自定义 CA**（可选）：用于校验服务端证书的 CA PEM；不填时沿用环境的 `verify_ssl` 配置。

说明：

- 证书与私钥使用应用层 Fernet 加密存储，接口只返回 `cert_set` / `key_set` 等布尔标记，不回显 PEM；
- 编辑时留空表示不修改已保存的证书、私钥或 CA；
- 执行、探活与前置接口请求都会携带客户端证书；
- mTLS 与 Basic 鉴权可以同时启用：mTLS 作用于 TLS 传输层，Basic 作用于 `Authorization` 请求头；
- 运行时会将 PEM 写入仅当前进程可读的临时文件，供 httpx 加载，适配器生命周期结束后立即清理。

### 2.4 前置接口钩子

勾选「启用前置接口钩子」，填写：

```json
{
  "enabled": true,
  "method": "POST",
  "path": "/api/v1/conversation",
  "headers": {},
  "body": {
    "userid": "{{userid}}",
    "custid": "{{custid}}"
  },
  "extract": {
    "conversationId": "$.data.conversationId"
  }
}
```

说明：

- `body` / `headers` 可用环境变量池与**用例请求变量**渲染；
- `extract` 为「变量名 → JSONPath」映射，提取结果写入变量池，供主请求与后续轮次复用；
- 每次执行只调用一次，即每次执行一个 `conversationId`；
- 所有 `extract` 字段必须都能取到非空值，否则执行直接报错（不会静默发送空值）；
- 兼容旧配置：不写 `extract` 时沿用 `token_jsonpath` / `token_var`。

### 2.5 请求体模板

```json
{
  "conversationId": "{{conversationId}}",
  "message": {
    "content": "{{query}}",
    "type": "text",
    "xxx": "固定值",
    "agent": "opencode",
    "model": "ds-xxx",
    "sessionid": "{{session_id}}",
    "metadata": {
      "timestampe": "{{timestamp_iso}}",
      "requestcontext": "userid:{{userid}},custid:{{custid}},beid:101"
    }
  }
}
```

- 示例中的 `xxx`、`agent`、`model` 为固定字段，按真实接口保留/增删；
- 首轮 `sessionid` 会渲染为 `""`，服务端按首轮处理；后续轮次自动填入响应返回值；
- `timestampe` 的字段名以接口为准（示例沿用对接报文中的拼写）。

### 2.6 响应提取器

```json
{
  "final_answer": "$.data.answer",
  "session_id": "$.data.sessionid",
  "intent": "$.data.intent"
}
```

- `session_id` 必须指向真实的会话字段路径，`SERVER_RETURNED` 模式依赖它回填；
- 自定义提取器为**整体替换**，请至少保留 `final_answer` 与 `session_id`；
- 其余字段（`intent` / `generated_sql` / `api_params` 等）按需保留。

### 2.7 完整环境 JSON 参考

```json
{
  "code": "AGENT-PROD",
  "name": "Agent 生产评测环境",
  "base_url": "https://<agent-gateway>",
  "health_path": "/health",
  "api_method": "POST",
  "api_path": "/api/v1/agent/chat",
  "session_mode": "SERVER_RETURNED",
  "basic_auth": { "username": "<账号>", "password": "<密码>" },
  "mtls": {
    "client_cert_pem": "-----BEGIN CERTIFICATE-----\n...",
    "client_key_pem": "-----BEGIN PRIVATE KEY-----\n...",
    "key_password": "<私钥口令，可选>",
    "ca_cert_pem": "-----BEGIN CERTIFICATE-----\n... 可选"
  },
  "pre_hook": {
    "enabled": true,
    "method": "POST",
    "path": "/api/v1/conversation",
    "body": { "userid": "{{userid}}", "custid": "{{custid}}" },
    "extract": { "conversationId": "$.data.conversationId" }
  },
  "headers_template": {},
  "body_template": {
    "conversationId": "{{conversationId}}",
    "message": {
      "content": "{{query}}",
      "type": "text",
      "agent": "opencode",
      "model": "ds-xxx",
      "sessionid": "{{session_id}}",
      "metadata": {
        "timestampe": "{{timestamp_iso}}",
        "requestcontext": "userid:{{userid}},custid:{{custid}},beid:101"
      }
    }
  },
  "extractors": {
    "final_answer": "$.data.answer",
    "session_id": "$.data.sessionid"
  },
  "timeout_seconds": 60,
  "verify_ssl": true,
  "is_active": true
}
```

> 通过界面保存时无需手写整段 JSON；此段用于核对字段或通过接口批量初始化。

---

## 3. 用例配置

每条用例只需维护**按用例变化**的字段，通常就是 `userid` / `custid`：

```json
{
  "code": "AGENT-CASE-001",
  "name": "账单查询（用户 A）",
  "case_type": "SINGLE",
  "environment_code": "AGENT-PROD",
  "query": "帮我查一下本月账单总额",
  "request_variables": {
    "userid": "13800138000",
    "custid": "C0001"
  }
}
```

- 用例抽屉「请求变量」支持按行或 JSON 编辑；
- **会话方式由执行环境统一决定**，用例上不再提供选择项；
- 运行时保留字（`query` / `session_id` / `history` / `history_text` / `turn` / `timestamp_iso`）不能出现在请求变量里。

---

## 4. 配置验证

### 4.1 报文预览（不发真实请求）

1. 保存环境后，点击「报文预览」，选择一条示例用例；
2. 检查：
   - `conversationId` 来源标注为「前置接口」，预览值为占位符 `<前置接口注入:conversationId>`；
   - `session_id` 来源为「会话」，首轮为空字符串；
   - `timestamp_iso` 来源为「运行时」，值为 ISO 时间；
   - `userid` / `custid` 来源为「用例」；
   - `Authorization` 显示为 `***`（Basic 鉴权已启用）。

### 4.2 单条调试

1. 在用例抽屉点击「调试」；
2. 执行完成后进入 Trace 页，检查第 1 轮「原始报文」：
   - 请求头含 `Authorization: Basic ***`；
   - 请求体 `conversationId` 为前置接口返回值；
   - `message.sessionid` 为 `""`；
   - `metadata.timestampe` 为 ISO 时间字符串；
3. 第 2 轮：
   - `conversationId` 与第 1 轮一致；
   - `message.sessionid` 等于第 1 轮响应中的 `sessionid`；
   - `timestampe` 已刷新（与第 1 轮不同）；
4. 若「会话」徽标提示连续性异常，优先检查 `extractors.session_id` 路径。

### 4.3 本地 Mock 验证（无需真实环境）

Mock 已内置等价接口（启动 `uvicorn mock_servers.target_api:app --port 8002`）：

| 接口 | 行为 |
| --- | --- |
| `POST /api/v1/conversation` | 校验 Basic 头，返回 `data.conversationId`，回显请求参数 |
| `POST /api/v1/agent/chat` | 校验 Basic 头；`message.sessionid` 为空视为首轮并生成 `sessionid`；回显 `received` |

> 内置演示环境 **`QA-MOCK-JSON`** 已按本文配置好（Agent 报文、Basic 鉴权、前置 `conversationId`、时间戳），
> 57 条演示用例默认绑定该环境，可直接执行或调试验证。

手工验证命令（PowerShell）：

```powershell
$basic = "Basic " + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("<账号>:<密码>"))
$h = @{ "Content-Type" = "application/json"; "Authorization" = $basic }

# 1) 取 conversationId
$conv = Invoke-RestMethod -Uri "http://127.0.0.1:8002/api/v1/conversation" -Method Post -Headers $h `
  -Body ([Text.Encoding]::UTF8.GetBytes('{"userid":"13800138000","custid":"C0001"}'))
$conv.data.conversationId

# 2) 首轮（sessionid 为空）
$b1 = @{ conversationId = $conv.data.conversationId; message = @{ content = "帮我查一下本月账单总额"; type = "text"; sessionid = ""; metadata = @{ timestampe = "2026-11-17T18:00:00.000" } } } | ConvertTo-Json -Depth 6
$r1 = Invoke-RestMethod -Uri "http://127.0.0.1:8002/api/v1/agent/chat" -Method Post -Headers $h `
  -Body ([Text.Encoding]::UTF8.GetBytes($b1))
$r1.data.sessionid

# 3) 次轮（带上 sessionid）
$b2 = @{ conversationId = $conv.data.conversationId; message = @{ content = "那费用都包含哪些项目？"; type = "text"; sessionid = $r1.data.sessionid; metadata = @{ timestampe = "2026-11-17T18:00:01.000" } } } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8002/api/v1/agent/chat" -Method Post -Headers $h `
  -Body ([Text.Encoding]::UTF8.GetBytes($b2))
```

Mock 环境下：

| 字段 | 值 |
| --- | --- |
| Base URL | `http://127.0.0.1:8002` |
| 对话路径 | `/api/v1/agent/chat` |
| 前置路径 | `/api/v1/conversation` |
| Basic 账号 / 密码 | 任意非空（Mock 只校验 Basic 头存在，并回显账号） |
| 提取器 | `{"final_answer": "$.data.answer", "session_id": "$.data.sessionid"}` |

---

## 5. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 执行报「前置接口失败 HTTP 401」 | Basic 账号密码错误，或前置接口使用了另一套鉴权；检查环境 Basic 配置 |
| 执行报「前置接口未提取到字段: conversationId」 | `extract` 的 JSONPath 与实际响应不一致；用预览/Trace 里的响应原文核对 |
| 执行报「前置接口变量缺失: userid」 | 用例未配置 `userid`，或前置 body 拼错了占位符名 |
| 执行报「请求模板变量缺失: conversationId」 | 前置钩子未启用，或主模板占位符名与 `extract` 变量名不一致 |
| 第 2 轮没有带上 sessionid | `extractors.session_id` 路径不对，或会话模式不是 `SERVER_RETURNED` |
| `timestampe` 是空值 | 模板写成 `{{timestamp}}`；正确变量名是 `{{timestamp_iso}}` |
| 接口把 `${userid}` 原样收下 | 平台占位符是 `{{userid}}`，需把报文里的 `${...}` 改为 `{{...}}` |

---

## 6. 安全与运维提示

- Basic 密码使用应用层 Fernet 加密存储，接口只返回 `password_set`；
- mTLS 客户端证书、私钥与私钥口令同样加密存储，接口只返回布尔标记，临时文件在适配器关闭后清理；
- Trace 与预览中的 `Authorization` 头均按敏感头掩码为 `***`；
- 前置接口每次执行调用一次；批量任务并发执行时，每条执行互相独立，`conversationId` 不共享；
- 其他部署环境升级后需执行一次 `alembic upgrade head`（新增 `environment.basic_auth`、`environment.mtls`）。
