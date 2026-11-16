"""答案契约判分内置 Prompt（随代码版本走，不进 Prompt 资产库）。"""

from __future__ import annotations

CLAIM_EXTRACT_SYSTEM = (
    "你是信息抽取引擎。只输出合法 JSON，不要输出 Markdown，不要解释。"
    "你的任务是把答案中可核对的事实断言抽出来，不要评价答案对错。"
)

CLAIM_EXTRACT_USER = """请从下面的答案中抽出所有可核对的事实断言。

答案：
{answer}

只输出如下结构的 JSON：
{{
  "claims": [
    {{
      "text": "断言原文（不超过 60 字）",
      "kind": "number | date | entity | statement",
      "value": "number/date/entity 的原值；statement 可为 null",
      "evidence": "答案中的原文片段"
    }}
  ]
}}

要求：
1. 只抽取答案中明确出现的内容，不得推断、补全或改写。
2. 金额、数量、日期必须单独成条。
3. 没有可抽取内容时返回 {{"claims": []}}。
"""

CLARITY_SYSTEM = (
    "你是答案质量评审。只输出合法 JSON，不要输出 Markdown。"
    "不要判断事实是否正确，只判断表达质量。"
)

CLARITY_USER = """请评价下面这条回答的表达质量。

问题：
{question}

答案：
{answer}

按 1-5 打分（5 最好），只输出 JSON：
{{
  "conclusion_first": true/false,
  "conclusion_in_first_two_sentences": true/false,
  "readable": 1-5,
  "verbose": 1-5,
  "score": 1-5,
  "reason": "一句话理由"
}}
"""

BEHAVIOR_SYSTEM = (
    "你是对话行为分类器。只输出合法 JSON，不要输出 Markdown。"
)

BEHAVIOR_USER = """判断下面这条回答属于哪种交互行为，并给出依据。

用户问题：
{question}

回答：
{answer}

只输出 JSON：
{{
  "behavior": "answer | clarify | answer_with_assumption | partial | refuse",
  "evidence": "答案中的原文片段",
  "stated_assumption": true/false
}}
"""

SEMANTIC_EQUIVALENCE_SYSTEM = (
    "你是事实一致性评审。只输出合法 JSON，不要输出 Markdown，不要解释。"
)

SEMANTIC_EQUIVALENCE_USER = """判断答案是否在语义上覆盖了期望事实。

期望事实：
- 名称：{label}
- 类型：{kind}
- 期望值 / 语义线索：{expected}

答案：
{answer}

只有当答案明确表达了同一事实、且没有相反表述时才判定为 true；
含糊、不确定或无法判断时输出 false。

只输出 JSON：
{{
  "equivalent": true/false,
  "confidence": 0.0-1.0,
  "reason": "一句话理由"
}}
"""

RELEVANCE_SYSTEM = (
    "你是答案相关性评审。只输出合法 JSON，不要输出 Markdown，不要解释。"
)

RELEVANCE_USER = """判断答案是否在回答用户问题，而不是答非所问。

用户问题：
{question}

答案：
{answer}

期望覆盖的事实：
{facts}

只输出 JSON：
{{
  "relevant": true/false,
  "confidence": 0.0-1.0,
  "reason": "一句话理由"
}}
"""
