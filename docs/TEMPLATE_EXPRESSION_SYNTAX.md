# 模板表达式语法指南

本文档说明平台模板中变量占位符与表达式的完整语法，适用于：

- 被测环境的 `headers_template`（请求头模板）
- 被测环境的 `body_template`（请求体模板）
- 前置接口钩子的 `headers` / `body`
- Prompt 模板（模拟器 / 裁判 / 用例编写 / 失败追问自检）

渲染实现基于 [Jinja2 3.1+](https://jinja.palletsprojects.com/) 的沙箱环境（`SandboxedEnvironment`），
在保留历史 `{{variable}}` 占位符语义的基础上，增加了变量加工能力。

---

## 1. 基础占位符

```json
{
  "x-userid": "{{userid}}",
  "content": "{{query}}",
  "sessionid": "{{session_id}}"
}
```

- 变量名规则：`[A-Za-z_][A-Za-z0-9_]*`
- 花括号内允许空白：`{{ userid }}` 与 `{{userid}}` 等价
- **整值占位符保留原始类型**：值恰好是 `{{history}}` 时，得到的是对象 / 数组 / 数字本身，而不是字符串
- 变量优先级：运行时变量 > 用例 `request_variables` > 环境变量池（前置接口注入的变量与两者合并）

运行时保留变量：

| 变量 | 说明 |
| --- | --- |
| `query` | 本轮用户提问 |
| `session_id` | 会话标识（首轮可能为空字符串） |
| `history` | 历史对话的 OpenAI messages 数组 |
| `history_text` | 历史对话的纯文本 |
| `turn` | 当前轮次（从 1 开始） |
| `timestamp_iso` | 请求时刻的 ISO 8601 字符串，每轮刷新 |

---

## 2. 表达式

在占位符内通过**管道符 `|`** 调用过滤器，对变量值做加工：

```
{{ 变量名 | 过滤器1 | 过滤器2(参数...) }}
```

- 固定文本直接写在占位符外面，天然完成拼接，不需要额外的字符串拼接运算符
- 过滤器参数支持字符串（单 / 双引号）、数字、布尔与变量名
- 表达式结果一律按字符串输出；结构体（对象 / 数组）变量参与表达式时保留原始类型（如 `length` 按元素个数计算）

### 典型场景：固定前缀 + 去空格

用例变量 `custCode = "abc xxx"`，请求头需要 `x-agent-usr = admin_abcxxx`：

```json
{
  "headers_template": {
    "x-agent-usr": "admin_{{ custCode | replace(' ', '') }}"
  }
}
```

> JSON 模板中推荐在表达式里使用**单引号**，避免与 JSON 的双引号冲突。

### 更多示例

| 需求 | 写法 | 结果 |
| --- | --- | --- |
| 去首尾空白 | `{{ name \| trim }}` | `" 张三 "` → `"张三"` |
| 转大写 | `{{ code \| upper }}` | `"abc"` → `"ABC"` |
| 全量替换 | `{{ phone \| replace('-', '') }}` | `"138-0013"` → `"1380013"` |
| 取长度 | `{{ history \| length }}` | 消息条数 |
| 截断 | `{{ text \| truncate(20) }}` | 超长文本截断并加 `...` |
| 取首元素 | `{{ items \| first }}` | 数组第一个元素 |
| 拼接数组 | `{{ tags \| join(',') }}` | `["a","b"]` → `"a,b"` |
| 转 JSON | `{{ payload \| tojson }}` | 结构体序列化为 JSON 字符串 |
| 多个过滤器串联 | `{{ custCode \| trim \| upper \| replace(' ', '') }}` | 依次去空白、大写、去空格 |

---

## 3. 内置过滤器（Jinja2）

平台直接启用 Jinja2 内置过滤器，常用清单如下（完整列表见
[官方文档 Builtin Filters](https://jinja.palletsprojects.com/en/3.1.x/templates/#builtin-filters)）：

### 字符串

| 过滤器 | 说明 | 示例 |
| --- | --- | --- |
| `trim` | 去首尾空白 | `{{ v \| trim }}` |
| `upper` / `lower` | 大小写转换 | `{{ v \| upper }}` |
| `capitalize` | 首字母大写，其余小写 | `{{ v \| capitalize }}` |
| `title` | 每个单词首字母大写 | `{{ v \| title }}` |
| `replace(old, new)` | 全量替换 | `{{ v \| replace(' ', '') }}` |
| `truncate(n)` | 截断到 n 个字符并追加 `...` | `{{ v \| truncate(20) }}` |
| `center(width)` | 居中补空格 | `{{ v \| center(10) }}` |
| `wordcount` | 单词数 | `{{ v \| wordcount }}` |

### 数值与集合

| 过滤器 | 说明 |
| --- | --- |
| `length` / `count` | 字符串长度、数组 / 对象元素个数 |
| `int` / `float` | 转整数 / 浮点数 |
| `round(precision)` | 四舍五入 |
| `abs` | 绝对值 |
| `first` / `last` | 首 / 尾元素 |
| `join(sep)` | 数组按分隔符拼接 |
| `sort` / `reverse` / `unique` | 排序 / 反转 / 去重 |
| `sum` / `min` / `max` | 求和 / 最小 / 最大 |

### 转换

| 过滤器 | 说明 |
| --- | --- |
| `string` | 转字符串 |
| `tojson` | 结构体转 JSON 字符串（输出带空格的 JSON） |
| `urlencode` | URL 编码（空格编码为 `+`，平台另提供 `url_encode`） |

---

## 4. 平台扩展过滤器

| 过滤器 | 说明 | 示例 |
| --- | --- | --- |
| `md5` | 32 位小写 MD5（UTF-8） | `{{ token \| md5 }}` |
| `sha256` | 64 位小写 SHA-256（UTF-8） | `{{ token \| sha256 }}` |
| `base64` | Base64 编码（UTF-8 → ASCII） | `{{ secret \| base64 }}` |
| `base64_encode` | 同 `base64` | `{{ secret \| base64_encode }}` |
| `base64_decode` | Base64 解码，非法输入按替换字符处理 | `{{ encoded \| base64_decode }}` |
| `url_encode` | URL 百分号编码（`/` 也编码） | `{{ q \| url_encode }}` |
| `url_decode` | URL 百分号解码 | `{{ q \| url_decode }}` |

---

## 5. 全局函数

| 函数 | 说明 | 示例 | 结果 |
| --- | --- | --- | --- |
| `now(fmt)` | 当前本地时间，按 `strftime` 格式化 | `{{ now('%Y%m%d%H%M%S') }}` | `20261120153000` |
| `uuid()` | 随机 UUID4 字符串 | `{{ uuid() }}` | `4f1c...` |

`now()` 不传参数时默认格式为 `%Y-%m-%d %H:%M:%S`。

> 内置的 `timestamp_iso` 变量是请求时刻的 ISO 8601 字符串（每轮刷新）；
> 需要自定义格式时可用 `{{ now('...') }}`，或对 `timestamp_iso` 做字符串截取。

---

## 6. 组合示例

### 6.1 认证头（固定前缀 + 变量清洗）

```json
{
  "x-agent-usr": "admin_{{ custCode | replace(' ', '') }}",
  "x-request-id": "{{ uuid() }}",
  "x-sign": "{{ (app_id ~ app_secret) | md5 }}"
}
```

### 6.2 查询参数

```json
{
  "userid": "{{ userid }}",
  "ts": "{{ now('%Y%m%d%H%M%S') }}",
  "q": "{{ query | url_encode }}"
}
```

### 6.3 请求体中的格式化字段

```json
{
  "conversationId": "{{conversationId}}",
  "message": {
    "content": "{{query}}",
    "sessionid": "{{session_id}}",
    "metadata": {
      "timestampe": "{{timestamp_iso}}",
      "requestcontext": "userid:{{userid}},custid:{{custid | upper}},beid:101"
    }
  }
}
```

### 6.4 前置接口钩子

```json
{
  "enabled": true,
  "method": "POST",
  "path": "/api/v1/conversation",
  "headers": {
    "x-agent-usr": "admin_{{ custCode | replace(' ', '') }}"
  },
  "body": {
    "userid": "{{userid}}",
    "custid": "{{custid}}"
  },
  "extract": {
    "conversationId": "$.data.conversationId"
  }
}
```

---

## 7. 缺失变量与错误语义

平台采用**严格模式**：不接受静默的空值替换。

| 场景 | 渲染结果 | 最终行为 |
| --- | --- | --- |
| 变量缺失（未定义或值为 `null`） | 该 `{{ ... }}` 片段保留原文 | 记入 missing，执行时抛出「请求模板变量缺失: xxx」/「前置接口变量缺失: xxx」，该用例判定为 `ERROR`（`BUSINESS`，不自动重试） |
| 同一模板部分变量存在 | 存在变量正常渲染，缺失片段保留原文 | 同上 |
| 表达式语法错误 | 整段原文保留 | 抛出「请求模板表达式错误: ...（第 N 行）」/「前置接口模板表达式错误: ...」 |
| 沙箱拦截（如 `''.__class__`） | 整段原文保留 | 抛出「模板表达式渲染失败: ...」 |
| 模板含 `{% if %}` 等语句块且存在缺失变量 | 整段原文保留 | 记入 missing，不做半渲染 |

接口配置页的「报文预览」不会中断，而是把缺失变量与表达式错误写入 `warnings`，便于在保存前发现模板问题。

> **关于 `default()`**：由于平台严格校验变量是否存在，`{{ var | default('x') }}` 中的 `default`
> 不会为「缺失变量」兜底（缺失时整段保留原文并报错）。需要兜底值时，请在用例变量或环境变量池中显式定义。

---

## 8. 安全与限制

- 渲染运行在 Jinja2 **沙箱**中：禁止访问对象内部属性（如 `__class__`、`__globals__`）、禁止文件与网络操作
- 不使用 Python `eval`，过滤器与全局函数均为白名单注册
- 不启用 HTML 自动转义（输出用于 HTTP 报文，而非网页）
- 变量名避免使用 Jinja2 内置全局名：`range`、`dict`、`lipsum`、`cycler`、`joiner`、`namespace`、`now`、`uuid`
- 表达式结果统一按字符串输出；整值占位符（如 `"{{history}}"`）仍保留原始类型
- 模板按字符串缓存编译结果，同一模板重复渲染不会重复编译

---

## 9. 能力包与扩展方式

模板渲染已抽成独立、可复用的能力包，其他功能直接导入即可复用，不需要复制实现：

```
backend/billing_qa/services/templating/
├── __init__.py   # 公共 API
├── engine.py     # Jinja2 沙箱引擎：渲染 / 变量识别 / 注册表
└── filters.py    # 平台内置扩展过滤器与全局函数
```

### 9.1 复用渲染能力

```python
from billing_qa.services.templating import render_structure, render_text, find_variables

headers = render_structure({"x-agent-usr": "admin_{{ custCode | replace(' ', '') }}"}, {"custCode": "abc xxx"})
text, missing = render_text("第 {{ turn }} 轮", {"turn": 2})
names = find_variables("{{ query }} {{ custCode | upper }}")  # ["query", "custCode"]
```

- `render_structure`：递归渲染 dict / list / str，整值占位符保留原始类型
- `render_text`：渲染单个字符串，返回 `(渲染结果, 缺失变量列表)`，可选第三个参数收集表达式错误
- `render_prompt`：Prompt 模板渲染，`strict=True` 时缺失变量或表达式错误直接抛错
- `find_variables`：提取模板引用的变量名（自动排除过滤器与全局函数）

> 历史导入路径 `billing_qa.services.adapter.renderer` 保留为兼容层，行为与能力包一致。

### 9.2 新增过滤器 / 全局函数

**方式一：运行期注册**（适合插件化或业务侧扩展）

```python
import uuid

from billing_qa.services.templating import register_filter, register_global

# 模板中：{{ custCode | admin_user }}  →  admin_abcxxx
register_filter("admin_user", lambda code: f"admin_{str(code).replace(' ', '')}")

# 模板中：{{ request_id('req') }}  →  req-<uuid>
register_global("request_id", lambda prefix: f"{prefix}-{uuid.uuid4()}")
```

**方式二：内置登记**（适合平台通用能力）

在 `services/templating/filters.py` 的 `BUILTIN_FILTERS` / `BUILTIN_GLOBALS` 中登记后重启生效。

### 9.3 能力发现

```python
from billing_qa.services.templating import available_filters, available_globals

available_filters()  # ["abs", "base64", ..., "md5", "replace", ...]
available_globals()  # ["now", "uuid", ...]
```

前端函数提示、文档自动生成等场景可直接消费这两个接口。

---

## 10. 版本与参考

| 项目 | 说明 |
| --- | --- |
| 渲染引擎 | Jinja2 `>=3.1.6,<4.0.0` |
| 官方文档 | <https://jinja.palletsprojects.com/> |
| 模板语法 | <https://jinja.palletsprojects.com/en/3.1.x/templates/> |
| 内置过滤器 | <https://jinja.palletsprojects.com/en/3.1.x/templates/#builtin-filters> |
| 沙箱说明 | <https://jinja.palletsprojects.com/en/3.1.x/sandbox/> |
| 变更记录 | <https://jinja.palletsprojects.com/en/3.1.x/changes/> |

相关文档：

- 真实 Agent 报文对接：`docs/AGENT_INTEGRATION_GUIDE.md`
- 用例 JSON 格式：`docs/CASE_JSON_FORMAT.md`
