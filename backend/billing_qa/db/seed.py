"""内置种子数据：两条默认 Prompt 模板。"""

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
