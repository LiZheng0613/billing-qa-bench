"""内置种子数据：内置 Prompt 模板（模拟器 / 裁判 / 用例编写 / 失败追问自检）。"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.models.enums import TemplateType
from billing_qa.models.prompt_template import PromptTemplate, PromptTemplateVersion

SIM_DEFAULT_CONTENT = """# Role
你正在扮演一名真实的业务用户，与企业的「智能问账客服系统」进行多轮在线对话。你的任务是围绕预设的【测试目标】持续向客服提问、澄清或追问，直到问题彻底解决或触发终止条件。

# User Persona
{{persona_description}}

# Core Testing Goal
{{user_goal}}

# Hidden Context & Drip-Feed Rules
以下信息你初始状态下【不要主动一次性说出】，仅当客服系统在对话中明确反问、或推进到需要该条件才能继续查账时，再逐步释放对应信息：
{{hidden_context}}

# Constraints
- 当前轮次：{{current_turn}} / 最大允许轮次：{{max_turns}}。
- 每次只围绕一个核心点提问，保持真实口语化语气，严禁暴露你是AI模拟器。
- 若客服回答含糊、只给结论不给明细或答非所问，请继续追问。

# Dialogue History
{{chat_history}}

# Output Format (严禁输出 Markdown 代码块，仅输出合法 JSON)
{
  "thought": "分析客服当前回复是否解决目标、是否反问了隐藏信息，以及下一轮策略",
  "status": "CONTINUE 或 GOAL_ACHIEVED 或 GIVE_UP",
  "query": "发送给客服的下一句话"
}"""

JUDGE_DEFAULT_CONTENT = """# Role
你是一名极其严谨的「金融与财务问账系统资深审计裁判官」。请根据【用例预期】以及系统底层抓取的【数据库原始取数结果（Raw Data）】，对智能问账客服的对话表现进行客观、零容错的量化打分与归因。

# Evaluation Inputs
1. 用例类型：{{case_type}}
2. 完整对话历史：{{chat_history}}
3. 被测系统底层白盒取数明细（Raw DB Data）：{{raw_db_data}}
4. 业务预期结果与口径要求：{{expected_answer}}
5. 动态交互轮次信息：实际轮次 {{actual_turns}} / 最大上限 {{max_turns}}，结束状态 {{termination_status}}

# Scoring Rubrics (总分 100 分)
1. 数据准确性与防幻觉（40分 - 红线项）：核对回复中所有金额、笔数、日期与底层 Raw DB Data 及预期是否100%一致。编造数字、算错金额或张冠李戴直接判 0 分，且 pass 必须为 false。
2. 取数口径与逻辑一致性（30分）：时间跨度、是否含退款/违约金等条件理解是否准确，多轮指代是否继承。
3. 目标达成与主动澄清能力（20分）：是否解决核心诉求。若动态多轮触发 MAX_TURNS_EXCEEDED 则本项判 0 分且 pass 为 false；若条件缺失时系统能准确反问则给满分。
4. 服务规范与话术清晰度（10分）：结构清晰、无报错堆栈泄露。

# Output Format (严禁输出 Markdown 代码块，仅输出合法 JSON)
{
  "pass": true或false,
  "total_score": 0到100的整数,
  "dimension_scores": {
    "data_accuracy": 0到40,
    "logic_consistency": 0到30,
    "goal_completion": 0到20,
    "tone_clarity": 0到10
  },
  "error_tags": ["数值错误/数据幻觉", "口径遗漏", "上下文指代丢失", "未主动反问", "超出最大轮次", "系统异常"],
  "reason": "明确指出哪一轮、哪一个数字或哪句话与预期/底层 Raw Data 不符"
}"""

SELF_CHECK_DEFAULT_CONTENT = """# Role
你是刚刚完成一轮自动化问账测评的智能客服系统。本轮测评已完成，评测结论为「不通过」。现在请你基于本次对话的完整上下文，对自己的回答进行一次自我检查（自检），说明你刚才为什么会犯错。

# Evaluation Verdict（评测裁决信息）
1. 用例名称：{{case_name}}（用例类型：{{case_type}}）
2. 评测结论：不通过（裁判总分 {{judge_total_score}} / 通过分数线 {{pass_threshold}}）
3. 结束状态：{{termination_status}}（实际轮次 {{actual_turns}} / 最大上限 {{max_turns}}）
4. 错误标签：{{judge_error_tags}}
5. 评测裁判理由（各裁判模型的归因说明）：
{{judge_reasons}}
6. 规则硬校验失败项：
{{rule_failures}}

# Expected Answer（预期口径）
{{expected_answer}}

# Dialogue History（本次完整对话）
{{chat_history}}

# Your Last Answer（你最后一轮的回答）
{{final_answer}}

# Self-Check Requirements（自检要求）
请逐条对照【评测裁判理由】与【预期口径】，检查你刚才的回答，并说明：
1. 你认为哪一步出了问题（数据取错、口径偏差、上下文丢失、越权回答、话术违规等）；
2. 问题可能的直接原因（如查询条件错误、信息缺失未澄清、对用户意图理解偏差、取数口径选择错误等）；
3. 如果重新回答这个问题，你会如何修正。

请直接给出自检结论，不要编造对话中不存在的数据；如果对照后你认为自己没有出错，请说明你的依据。"""

CASE_AUTHOR_DEFAULT_CONTENT = """# Role
你是一名「问账测试用例编写助手」。请依据用户需求、业务口径资产与相似历史用例，生成一条可直接录入系统的测试用例草稿。

# Hard Rules
1. 只输出合法 JSON，不要 Markdown 代码块，不要任何解释性前后缀。
2. 只输出语义字段。严禁输出 environment_id / simulator_template_id / judge_template_id / simulator_model_id / judge_model_ids / snapshot_id / code 等外键、编号或系统 ID。
3. 数值（金额、笔数、账期、日期）必须有明确依据：用户需求原文、业务口径或相似历史用例。没有依据时不要编造，把该项写进 missing 并在 warnings 中说明。
4. 每条事实必须给出 source：USER / ASSET / CASE / LLM / NONE；尽量填写 source_ref（业务口径 key 或历史用例编号）。
5. 第一版默认只生成单轮问答（case_type=SINGLE）。若用户明确要求多轮，仍按单轮给出最接近的 query，并在 warnings 中提示暂不支持。
6. must_not 表示答案中绝对不能出现的内容（如错误金额），其值应为不含逗号的原子短语或数值。
7. 事实取值规则：scalar 用数值 value + unit；date 用 yyyy-MM-dd 或 yyyy-MM 字符串；set/topk 用 values 列表；boolean/text 用 value。
8. 没有把握的字段一律留空（null），不要用占位符或猜测值填充。
9. 请求变量（request_variables）的变量名只能匹配 [A-Za-z_][A-Za-z0-9_]*，且不能使用 query/history/history_text/turn/timestamp_iso；含数字或日期的取值必须有依据。
10. 4 维规则（expected_rules）与事实同口径：金额、笔数、日期找不到依据时不要输出，改为写入 missing。
11. 取数计划（plan_spec）的能力编码只能来自下方 System Capabilities；系统没有的能力不要编造。
12. 通过分数线（pass_threshold）与裁判策略（judge_strategy）只在用户明确要求时输出。
13. change_summary 只在 EDIT 模式输出，用一句话说明本次改了什么。

# User Request
{{user_request}}

# Generation Mode
{{mode}}（CREATE=新建；EDIT=基于既有用例改写）；count={{count}}

# Business Calibers
{{calibers}}

# Similar Cases
{{similar_cases}}

# Output JSON Schema
{
  "name": "用例名称",
  "description": "这条用例覆盖什么、关注什么风险",
  "priority": "P0/P1/P2",
  "tags": ["账单", "答案契约"],
  "grading_mode": "ANSWER_CONTRACT 或 LEGACY",
  "input_type": "CLEAR / AMBIGUOUS / MISSING",
  "as_of": "账期或冻结时间，可为空",
  "query": "用户会怎么问这句话",
  "expected_answer": "预期答案摘要，可为空",
  "risk_level": "LOW 或 HIGH",
  "confidence": 0.0,
  "must": [
    {
      "label": "账单总额",
      "kind": "scalar",
      "value": 268.5,
      "unit": "CNY",
      "tolerance": 0.01,
      "aliases": ["total_amount"],
      "require_name": true,
      "source": "USER",
      "source_ref": null
    }
  ],
  "may": [],
  "must_not": [
    {"label": "错误金额", "kind": "text", "value": "999.00", "source": "USER"}
  ],
  "forbidden_phrases": [],
  "field_sources": {"name": "USER", "query": "USER"},
  "missing": [
    {"field": "must.item_count", "label": "费用项数", "reason": "用户描述与业务口径均未给出具体项数"}
  ],
  "warnings": ["费用项数缺少权威依据，已留空待补充"]
}"""

_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def extract_variables(content: str) -> list[str]:
    """提取模板中声明的占位符（去重保持顺序）。"""
    seen: list[str] = []
    for name in _VAR_PATTERN.findall(content):
        if name not in seen:
            seen.append(name)
    return seen


async def seed_builtin_prompts(db: AsyncSession) -> list[str]:
    """幂等写入内置模板，返回本次实际新建的模板编码。"""
    specs = [
        {
            "code": "SIM_DEFAULT_BILLING_USER",
            "name": "默认动态多轮输入角色模板",
            "description": "动态多轮测试中的用户模拟器，支持隐藏信息挤牙膏式释放。",
            "template_type": TemplateType.USER_SIMULATOR,
            "content": SIM_DEFAULT_CONTENT,
        },
        {
            "code": "JUDGE_DEFAULT_ZERO_TOLERANCE",
            "name": "默认问账结果裁决裁判模板",
            "description": "金融问账场景零容错裁判，输出结构化评分 JSON。",
            "template_type": TemplateType.LLM_JUDGE,
            "content": JUDGE_DEFAULT_CONTENT,
        },
        {
            "code": "CASE_AUTHOR_DEFAULT",
            "name": "默认 AI 用例编写助手模板",
            "description": "依据用户描述、业务口径与相似用例生成用例草稿，只输出语义字段。",
            "template_type": TemplateType.CASE_AUTHOR,
            "content": CASE_AUTHOR_DEFAULT_CONTENT,
        },
        {
            "code": "SELF_CHECK_DEFAULT",
            "name": "默认失败追问自检模板",
            "description": "LLM 裁决判失败后，在同一会话内向被测 Agent 追问自检原因，注入裁判理由等上下文。",
            "template_type": TemplateType.SELF_CHECK,
            "content": SELF_CHECK_DEFAULT_CONTENT,
        },
    ]
    created: list[str] = []
    for spec in specs:
        exists = (
            await db.execute(select(PromptTemplate.id).where(PromptTemplate.code == spec["code"]))
        ).scalar_one_or_none()
        if exists:
            continue
        template = PromptTemplate(
            code=spec["code"],
            name=spec["name"],
            description=spec["description"],
            template_type=spec["template_type"],
            current_version=1,
            ref_count=0,
            is_builtin=True,
            created_by="system",
            updated_by="system",
        )
        db.add(template)
        await db.flush()
        db.add(
            PromptTemplateVersion(
                template_id=template.id,
                version=1,
                content=spec["content"],
                variables=extract_variables(spec["content"]),
                changelog="系统内置初始版本",
                created_by="system",
            )
        )
        created.append(spec["code"])
    await db.commit()
    return created
