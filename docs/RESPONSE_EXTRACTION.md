# 响应提取器配置手册（JSONPath 与条件规则）

> 适用位置：**执行环境 → 字段提取器（extractors）**。所有挂在该环境上的用例，
> 在批量执行、调试工作台、探活调试时都按这套配置从被测 Agent 的响应中提取字段。
>
> 支持流式（SSE）与非流式（JSON）两种接口形态；条件规则在两种形态下语义一致。

---

## 1. 两种配置形态

字段提取器是「**字段名 → 取值方式**」的对象，每个字段的取值方式支持两种形态，**可混用**：

| 形态 | 写法 | 说明 |
| --- | --- | --- |
| 字符串 JSONPath | `"$.data.answer"` | 历史形态，行为与升级前完全一致 |
| 条件规则对象 | `{"rules": [ ... ]}` | 新增形态，支持条件匹配、最后一次出现、同级/上级/下级导航、多分支兜底 |

```json
{
  "final_answer": {
    "rules": [
      {
        "id": "if-last-chart",
        "path": "$..[?(@.type=='chart')]",
        "occurrence": "last",
        "take": ["$.message", "$..message"],
        "take_parent": ["$.message"]
      },
      { "id": "else-last-context", "path": "$..context", "occurrence": "last" }
    ]
  },
  "session_id": "$.data.sessionid",
  "intent": "$.data.intent"
}
```

> 字符串形态不配置条件规则时，永远走原有 JSONPath 逻辑；已存量的环境无需任何迁移。

---

## 2. 条件规则语法

### 2.1 规格级字段

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `rules` | 数组 | 必填 | **按序求值，首个产出非空值的规则生效**（即 if / elif / else 语义） |
| `fallback_content` | bool | `true` | 全部规则未命中时，流式场景回退 SSE 聚合文本 |
| `legacy_fallback` | bool | `false` | 是否再走平台内置兜底路径（`$.data.answer` 等，仅对 `final_answer` 有意义） |

### 2.2 规则级字段

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `id` | 字符串 | 取 `path` | 规则标识，用于 Trace 命中定位与报错提示 |
| `path` | 字符串 | **必填** | 定位表达式，jsonpath-ng 扩展语法：支持 `[?(@.type=='chart')]` 过滤、`$..` 递归、`` `parent` `` 上级操作符、`&&` / `\|\|` / `!=` / 数值比较 |
| `occurrence` | `first` / `last` / 下标 | `first` | 定位表达式的匹配集中取哪一个；**`last` 即「最后一次出现」** |
| `take` | 字符串数组 | — | 命中节点上的**相对路径**，按序尝试第一个非空值生效（同级 `$.message` → 下层 `$..message`） |
| `take_parent` | 字符串数组 | — | 命中节点的**最近对象祖先**上的相对路径（上级场景），在 `take` 全部未命中后尝试 |
| （不配 take） | — | — | 直取规则：返回匹配值本身（如 `$..context`） |

**求值顺序（严格）：**

1. `path` 在响应文档上求值 → 得到匹配集；
2. `occurrence` 从匹配集中选中一个节点；
3. 依次尝试 `take` 里的相对路径（同级 / 下层）；
4. 依次尝试 `take_parent` 里的相对路径（上级）；
5. 该规则无产出 → 进入下一条规则（elif / else）；全部未命中 → 规格级兜底。

先定位、后取值的顺序保证：**「最后一次 chart」的语义优先于「较早 chart 的同级 message」**。

### 2.3 响应文档与保留键

规则表达式在一个「提取文档」上求值：

- **流式（SSE）**：`{"_frames": [第1帧, 第2帧, ...], "_content": "聚合文本", ...合并元数据}`；
- **非流式（JSON）**：`{"_frames": [], "_content": "", ...响应体}`。

| 保留键 | 含义 |
| --- | --- |
| `_frames` | SSE 按到达顺序保留的帧（最多 300 帧，非流式为空数组）。`$._frames[?(@.type=='chart')]` 可只扫帧 |
| `_content` | SSE 聚合文本增量 |

`_frames` 在文档中排在最前，因此 `$..` 递归时**帧先于合并元数据**、帧与帧之间保持到达顺序——`first` / `last` 即流上的第一次 / 最后一次出现。

> 注意：`_frames` 与 `_content` 是平台保留键，若被测接口自身返回同名顶层字段会被覆盖，请避开。

### 2.4 示例速查

| 诉求 | 表达式 / 规则 |
| --- | --- |
| 任意深度找 type=chart 的节点 | `$..[?(@.type=='chart')]` |
| 最后一次出现 | `"occurrence": "last"` |
| 多条件 AND | `$..[?(@.type=='chart' && @.status=='done')]` |
| 取命中节点的同级 message | `"take": ["$.message"]` |
| 取命中节点的下层 message | `"take": ["$..message"]` |
| 取命中节点的上级 message | `"take_parent": ["$.message"]` 或 `"path": "$..[?(@.type=='chart')].`parent`.message"` |
| 没配 take（直取） | `{"path": "$..context", "occurrence": "last"}` |
| 只扫 SSE 帧 | `"path": "$._frames[?(@.type=='chart')]"` |

---

## 3. 完整配置样例

### 样例 1：有 chart 取其 message，否则取最后一次 context（流式）

```json
{
  "final_answer": {
    "rules": [
      {
        "id": "IF-最后一次chart",
        "path": "$..[?(@.type=='chart')]",
        "occurrence": "last",
        "take": ["$.message", "$..message"],
        "take_parent": ["$.message"]
      },
      {
        "id": "ELSE-最后一次context",
        "path": "$..context",
        "occurrence": "last"
      }
    ],
    "fallback_content": true
  },
  "session_id": "$.data.sessionid"
}
```

判定顺序等价于：

```
if 存在 type=chart 的结构:
    取「最后一次出现」的那个 chart，依次找 message（同级 → 下层 → 上级）
elif 存在 context:
    取「最后一次出现」的 context
else:
    取 SSE 聚合文本（fallback_content）
```

### 样例 2：多分支 if / elif / else

```json
{
  "final_answer": {
    "rules": [
      {
        "id": "IF-chart",
        "path": "$..[?(@.type=='chart')]",
        "occurrence": "last",
        "take": ["$.message", "$..message"]
      },
      {
        "id": "ELIF-table",
        "path": "$..[?(@.type=='table')].summary",
        "occurrence": "last"
      },
      {
        "id": "ELSE-context",
        "path": "$..context",
        "occurrence": "last"
      }
    ]
  }
}
```

### 样例 3：会话 ID 取最后一个 session 事件帧（流式）

```json
{
  "session_id": {
    "rules": [
      { "id": "last-session-frame", "path": "$._frames[?(@.type=='session')]", "occurrence": "last", "take": ["$.sessionId"] },
      { "path": "$.data.sessionid" }
    ]
  }
}
```

### 样例 4：混合形态（字段级混用，互不影响）

```json
{
  "final_answer": {
    "rules": [
      { "path": "$..[?(@.type=='chart')]", "occurrence": "last", "take": ["$.message", "$..message"] },
      { "path": "$..context", "occurrence": "last" }
    ],
    "fallback_content": true
  },
  "session_id": "$.data.sessionid",
  "intent": "$.data.intent",
  "generated_sql": "$.data.generated_sql"
}
```

---

## 4. 保存期校验与运行期容错

| 阶段 | 行为 |
| --- | --- |
| 保存环境（创建 / 更新） | 条件规则对象**严格校验**：结构、JSONPath 语法、`occurrence` 取值、相对路径以 `$` 开头、未知键，非法直接 422 并指明 `extractors.<字段>.rules[i]` 的具体位置 |
| 保存环境（字符串形态） | 与历史一致：语法错误仅记录告警、**不阻断保存**，避免存量环境编辑被卡 |
| 运行期 | 非法表达式按「未命中」降级（该字段为 `None`），记录 warning，不中断批量执行 |

## 5. 命中轨迹（Trace）

使用条件规则时，执行记录的「原始报文」中会追加 `extraction` 节点，逐字段记录命中细节：

```json
{
  "final_answer": {
    "rule": "IF-最后一次chart",
    "path": "$..[?(@.type=='chart')]",
    "rule_index": 0,
    "occurrence_index": 1,
    "source": "frame",
    "frame_index": 3,
    "take": "$..message"
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `rule` | 命中的规则 id（未配 id 时为 path） |
| `rule_index` / `occurrence_index` | 规则序号 / 匹配集中被选中的下标 |
| `source` / `frame_index` | 命中来源（`frame` = SSE 帧，`payload` = 合并元数据或 JSON body）与帧序号 |
| `take` | 生效的相对路径；`parent::` 前缀表示上级导航；`null` 表示直取 |

排障建议：先看 `extraction.rule` 确认走了哪个分支，再对照 `frames` 数组核对第 `frame_index` 帧的结构。

## 6. 兼容性说明

- 字符串 JSONPath 的提取行为（单命中标量、多命中列表、内置兜底路径、SSE 聚合文本兜底）**与升级前一致**；
- `DEFAULT_EXTRACTORS`、`FALLBACK_ANSWER_PATHS` 未变；存量环境零迁移；
- 条件规则是「整体权威」：配置了 `rules` 就按规则链走，不再隐式套用内置兜底路径（除非显式 `legacy_fallback: true`）；
- `final_answer` 之外的任意白盒字段（`session_id` / `intent` / `generated_sql` / `api_params` / `raw_db_data` / `tool_calls`）同样支持条件规则。

## 7. 常见问题

**Q：`[-1]` 可以取最后一个吗？**
`$.frames[?(@.type=='chart')][-1]` 在 jsonpath-ng 中会按数组下标语义解析而不是「匹配集最后一个」，平台不依赖该写法；请统一使用 `"occurrence": "last"`。

**Q：`take` 里写 `message` 不带 `$` 行吗？**
不行。相对路径需以 `$` 开头（`$.message`、`$..message`），保存期会校验。

**Q：chart 的 message 是对象（富文本卡片）怎么办？**
提取到非字符串值时，`final_answer` 会按 JSON 序列化成文本参与判分；如需取其中字段，把相对路径写细即可，例如 `"take": ["$.message.content"]`。

**Q：为什么我的规则命中了较早的帧？**
检查 `occurrence` 是否写了 `"last"`（默认是 `first`）；并确认没有把「定位」和「取值」拆成两条规则——应写在同一条规则内（先定位再 take）。
