"""内置 Mock：OpenAI 兼容 LLM（同时扮演用户模拟器与裁判）。

启动：uvicorn mock_servers.llm_api:app --port 8001
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="Mock OpenAI Compatible LLM", version="1.0.0")

_PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
_ACCOUNT_PATTERN = re.compile(r"CTC\d{8,}")
_ORDER_PATTERN = re.compile(r"[A-Z]\d{7}")
_PERIOD_PATTERN = re.compile(r"(20\d{2})\s*[-/年]\s*(\d{1,2})")
_TURN_PATTERN = re.compile(r"当前轮次：\s*(\d+)")
_TERMINATION_PATTERN = re.compile(r"结束状态\s*([A-Z_]+)")
_AMOUNT_PATTERN = re.compile(
    r"(?<![\d.])(\d{1,6}(?:,\d{3})*(?:\.\d{1,2})?)\s*元"
    r"|(?<![\d.])(\d{1,6}\.\d{1,2})(?![\d.])"
)

DISTINCT_QUERIES = [
    "那流量超套的 9GB 是怎么算的？",
    "这些费用里包含增值业务扣费吗？",
    "上个月的账单是不是也都结清了？",
    "电子发票大概什么时候可以开出来？",
    "有没有产生额外的违约金或者滞纳金？",
]


def _hidden_entity(prompt: str) -> tuple[str, str] | None:
    """从渲染后的隐藏上下文里找到可释放的实体（手机号 / 宽带账号 / 订单号）。"""
    for pattern, label in (
        (_PHONE_PATTERN, "手机号"),
        (_ACCOUNT_PATTERN, "宽带账号"),
        (_ORDER_PATTERN, "订单号"),
    ):
        match = pattern.search(prompt)
        if match:
            return label, match.group(0)
    return None


def _prompt_period(prompt: str) -> str | None:
    match = _PERIOD_PATTERN.search(prompt)
    if match is None:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{int(match.group(1)):04d}-{month:02d}"


def _goal_text(prompt: str) -> str:
    """只取模拟器 Prompt 的「测试目标」段落，避免被对话历史里的词干扰。"""
    marker = "# Core Testing Goal"
    if marker not in prompt:
        return prompt
    block = prompt.split(marker, 1)[1]
    for stop in ("# Hidden Context", "# Constraints", "# Dialogue History"):
        if stop in block:
            block = block.split(stop, 1)[0]
    return block


def _topic_query(prompt: str) -> str:
    goal = _goal_text(prompt)
    if any(word in goal for word in ("余额", "预存", "冻结", "赠费")):
        return "查一下账户可用余额、冻结余额和赠费到期时间"
    if any(
        word in goal
        for word in ("offer", "可订购", "可办理", "能办理", "订购", "合约", "优惠", "折扣")
    ):
        if any(word in goal for word in ("已订", "合约", "到期", "生效")):
            return "查一下已订购 offer 的月费、优惠价和合约到期时间"
        return "查一下当前可以订购的 offer 和月费"
    if any(word in goal for word in ("欠费", "停机", "复机")):
        return "查一下欠费金额、账期和停机状态"
    if "漫游" in goal:
        return "查一下国际漫游费用明细"
    if any(word in goal for word in ("增值", "彩铃", "会员", "退订")):
        return "查一下已订购的增值业务和扣费"
    if "发票" in goal:
        return "查一下可开具的电子发票金额"
    if any(word in goal for word in ("套餐", "余量", "流量", "超套")):
        return "查一下套餐余量和超套费用"
    return "核对一下账单总额和费用明细"


def _simulator_content(prompt: str) -> dict[str, Any]:
    turn_match = _TURN_PATTERN.search(prompt)
    turn = int(turn_match.group(1)) if turn_match else 1
    entity = _hidden_entity(prompt)

    if "[MOCK:GIVEUP]" in prompt:
        return {
            "thought": "客服一直给不出有效信息，放弃追问",
            "status": "GIVE_UP",
            "query": "那算了吧",
        }
    if "[MOCK:REPEAT]" in prompt:
        return {
            "thought": "继续用同一句话追问，用于验证复读机熔断",
            "status": "CONTINUE",
            "query": "帮我查一下手机号 13800138000 的账单金额",
        }
    if "[MOCK:MAXTURNS]" in prompt:
        query = DISTINCT_QUERIES[(turn - 1) % len(DISTINCT_QUERIES)]
        return {"thought": "目标尚未达成，继续追问不同细节", "status": "CONTINUE", "query": query}

    if turn >= 3:
        return {
            "thought": "已经拿到金额和明细，目标达成",
            "status": "GOAL_ACHIEVED",
            "query": "好的，清楚了，谢谢",
        }
    if turn == 2 and entity:
        label, value = entity
        period = _prompt_period(prompt) or "2026-10"
        return {
            "thought": "客服要求提供必要信息，这里释放隐藏的手机号与账期",
            "status": "CONTINUE",
            "query": f"{label}是 {value}，账期是 {period}，麻烦帮我{_topic_query(prompt)}。",
        }
    if turn == 2:
        return {
            "thought": "先补充账期信息",
            "status": "CONTINUE",
            "query": "账期是 2026-10，麻烦查一下。",
        }
    return {
        "thought": "先描述诉求，暂不主动给出手机号",
        "status": "CONTINUE",
        "query": f"{_topic_query(prompt)}，但手机号一时找不到。",
    }


def _judge_content(prompt: str, model: str) -> dict[str, Any]:
    termination_match = _TERMINATION_PATTERN.search(prompt)
    termination = termination_match.group(1) if termination_match else "COMPLETED"

    if "[MOCK:FAIL]" in prompt or "[MOCK:WRONG]" in prompt:
        return {
            "pass": False,
            "total_score": 18,
            "dimension_scores": {
                "data_accuracy": 0,
                "logic_consistency": 8,
                "goal_completion": 10,
                "tone_clarity": 8,
            },
            "error_tags": ["数值错误/数据幻觉"],
            "reason": "第 1 轮回复中的金额与底层 Raw DB Data 不一致，触发数据零容错红线。",
        }
    if termination == "MAX_TURNS_EXCEEDED":
        return {
            "pass": False,
            "total_score": 52,
            "dimension_scores": {
                "data_accuracy": 30,
                "logic_consistency": 22,
                "goal_completion": 0,
                "tone_clarity": 9,
            },
            "error_tags": ["超出最大轮次", "未主动反问"],
            "reason": "对话达到最大轮次仍未解决用户诉求，目标达成项判 0 分。",
        }
    if termination == "GIVE_UP":
        return {
            "pass": False,
            "total_score": 44,
            "dimension_scores": {
                "data_accuracy": 18,
                "logic_consistency": 14,
                "goal_completion": 4,
                "tone_clarity": 8,
            },
            "error_tags": ["用户放弃追问", "目标未达成"],
            "reason": "用户在第 1 轮即放弃追问，未获得所需账单信息，目标达成项接近 0 分。",
        }
    if model.endswith("judge-2"):
        return {
            "pass": True,
            "total_score": 88,
            "dimension_scores": {
                "data_accuracy": 38,
                "logic_consistency": 26,
                "goal_completion": 16,
                "tone_clarity": 8,
            },
            "error_tags": [],
            "reason": "金额与底层取数一致，口径正确，但明细展示可更清晰。",
        }
    return {
        "pass": True,
        "total_score": 94,
        "dimension_scores": {
            "data_accuracy": 40,
            "logic_consistency": 28,
            "goal_completion": 18,
            "tone_clarity": 8,
        },
        "error_tags": [],
        "reason": "金额、笔数与底层 Raw DB Data 完全一致，主动澄清缺失条件，话术清晰。",
    }


def _author_request(prompt: str) -> str:
    marker = "# User Request"
    if marker not in prompt:
        return prompt
    block = prompt.split(marker, 1)[1]
    for stop in (
        "# Generation Mode",
        "# Business Calibers",
        "# Similar Cases",
        "# Output JSON Schema",
    ):
        if stop in block:
            block = block.split(stop, 1)[0]
    return block.strip()


def _author_amounts(text: str) -> list[str]:
    values: list[str] = []
    for match in _AMOUNT_PATTERN.finditer(text):
        raw = (match.group(1) or match.group(2) or "").replace(",", "")
        if not raw:
            continue
        value = f"{float(raw):.2f}"
        if value not in values:
            values.append(value)
    return values


def _base_case_type(text: str) -> str:
    match = re.search(r"case_type：\s*([A-Z_]+)", text or "")
    return match.group(1) if match else "SINGLE"


def _base_name(text: str) -> str | None:
    match = re.search(r"^\[[^\]]+\]\s*(.+)$", (text or "").strip(), re.MULTILINE)
    return match.group(1).strip() if match else None


def _base_description(text: str) -> str | None:
    match = re.search(r"^描述：\s*(.+)$", (text or "").strip(), re.MULTILINE)
    return match.group(1).strip() if match else None


def _base_query(text: str) -> str | None:
    match = re.search(r"^query：\s*(.+)$", (text or "").strip(), re.MULTILINE)
    return match.group(1).strip() if match else None


def _base_priority(text: str) -> str | None:
    match = re.search(r"^优先级：\s*(P[0-3])", (text or "").strip(), re.MULTILINE)
    return match.group(1) if match else None


def _base_dynamic(text: str) -> dict[str, Any] | None:
    """从基线用例简介里还原动态多轮语义字段。"""
    if _base_case_type(text) != "DYNAMIC_MULTI":
        return None

    def line_value(key: str) -> str | None:
        match = re.search(rf"^{re.escape(key)}：\s*(.+)$", text or "", re.MULTILINE)
        return match.group(1).strip() if match else None

    hidden: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        match = re.match(r"^隐藏信息：([^｜]*)｜([^｜]*)｜(.*)$", raw_line.strip())
        if not match:
            continue
        key = match.group(1).strip() or "信息"
        content = match.group(2).strip()
        if not content:
            continue
        hidden.append(
            {
                "key": key,
                "content": content,
                "release_hint": match.group(3).strip() or None,
                "source": "CASE",
            }
        )
    max_turns_raw = line_value("max_turns")
    max_turns = int(max_turns_raw) if max_turns_raw and max_turns_raw.isdigit() else 5
    keywords_raw = line_value("终止关键词")
    keywords = [item for item in (keywords_raw or "").split("、") if item.strip()]
    return {
        "user_goal": line_value("user_goal"),
        "initial_query": line_value("initial_query"),
        "persona_description": line_value("persona"),
        "hidden_context": hidden,
        "max_turns": max_turns,
        "terminate_conditions": {"keywords": keywords} if keywords else None,
    }


def _base_turns(text: str) -> list[dict[str, Any]]:
    """从基线用例简介里还原静态多轮脚本。"""
    if _base_case_type(text) != "STATIC_MULTI":
        return []
    queries: dict[int, str] = {}
    answers: dict[int, str] = {}
    notes: dict[int, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        match = re.match(r"第(\d+)轮：(.*)$", line)
        if match:
            queries[int(match.group(1))] = match.group(2).strip()
            continue
        match = re.match(r"第(\d+)轮预期：(.*)$", line)
        if match:
            answers[int(match.group(1))] = match.group(2).strip()
            continue
        match = re.match(r"第(\d+)轮备注：(.*)$", line)
        if match:
            notes[int(match.group(1))] = match.group(2).strip()
    turns = []
    for index in sorted(queries):
        if not queries[index]:
            continue
        turns.append(
            {
                "query": queries[index],
                "expected_answer": answers.get(index),
                "notes": notes.get(index),
            }
        )
    return turns


_CN_TURN_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _turn_no(text: str) -> int | None:
    """解析「第 2 轮 / 第二轮」中的轮次（1-based）。"""
    match = re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*轮", text or "")
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if raw.startswith("十"):
        return 10 + _CN_TURN_DIGITS.get(raw[1:], 0)
    if "十" in raw:
        left, right = raw.split("十", 1)
        return _CN_TURN_DIGITS.get(left, 0) * 10 + (
            _CN_TURN_DIGITS.get(right, 0) if right else 0
        )
    return _CN_TURN_DIGITS.get(raw)


def _author_capability(topic: str) -> str | None:
    """按业务主题给出取数能力编码（与系统能力资产编码保持一致）。"""
    if any(word in topic for word in ("欠费", "停机", "复机")):
        return "arrears_status"
    if any(word in topic for word in ("流量", "超套", "套餐", "余量")):
        return "usage_current"
    if any(
        word in topic
        for word in ("offer", "订购", "可办理", "能办理", "合约", "优惠", "折扣")
    ):
        return "offer_available"
    if any(word in topic for word in ("余额", "预存", "冻结", "赠费")):
        return "balance_account"
    if "漫游" in topic:
        return "roaming_usage"
    if any(word in topic for word in ("账单", "金额", "费用", "明细")):
        return "bill_current"
    return None


def _author_content(prompt: str) -> dict[str, Any]:
    """确定性生成用例草稿：只使用用户描述中明确出现的信息。"""
    request = _author_request(prompt)
    if "# Base Case" in request:
        user_part, base_part = request.split("# Base Case", 1)
    else:
        user_part, base_part = request, ""

    variant_match = re.search(r"候选\s*(\d+)\s*/\s*(\d+)", prompt)
    variant = int(variant_match.group(1)) if variant_match else 1

    # EDIT 模式：用户新描述优先；没给数值/账期时回落到基线用例，模拟「保留既有语义」。
    entity = _hidden_entity(user_part) or _hidden_entity(base_part)
    period = _prompt_period(user_part) or _prompt_period(base_part)
    amounts = _author_amounts(user_part) or _author_amounts(base_part)
    topic = user_part
    scenario_words = ("欠费", "停机", "复机", "流量", "超套", "套餐", "余量", "账单", "金额", "费用")
    if not any(word in topic for word in scenario_words):
        topic = f"{user_part} {base_part}"

    base_turns = _base_turns(base_part)
    base_dynamic = _base_dynamic(base_part)
    base_name = _base_name(base_part)
    base_description = _base_description(base_part)
    base_query = _base_query(base_part)
    base_priority = _base_priority(base_part)

    if entity:
        label, value = entity
        subject = f"{label} {value}"
    else:
        subject = "该号码"
    period_text = period or "最近一期"

    if any(word in topic for word in ("欠费", "停机", "复机")):
        name = "欠费金额与停机状态校验"
        tags = ["欠费", "停复机"]
        fact_label, unit = "欠费金额", "CNY"
    elif any(word in topic for word in ("流量", "超套", "套餐", "余量")):
        name = "套餐余量与超套费用校验"
        tags = ["套餐", "超套"]
        fact_label, unit = "超套费用", "CNY"
    elif any(word in topic for word in ("账单", "金额", "费用")):
        name = "账单总额与费用项数校验"
        tags = ["账单", "费用明细"]
        fact_label, unit = "账单总额", "CNY"
    else:
        name = "AI 生成用例草稿"
        tags = ["AI 草稿"]
        fact_label, unit = "核对金额", "CNY"

    query = f"帮我查一下 {subject} 在 {period_text} 的{fact_label}和费用项数？"

    # 多候选：保持业务数值不变，只让名称 / 标签 / 提问措辞可区分。
    if variant > 1:
        suffix = "边界" if variant % 2 == 0 else "明细"
        name = f"{name}（{suffix}）"
        tags = [*tags, suffix]
        if variant % 2 == 0:
            query = f"请问 {subject} 在 {period_text} 的{fact_label}是多少？"
        else:
            query = f"核对一下 {subject} 在 {period_text} 的{fact_label}和费用明细"

    # EDIT：用户没要求换提问时保留基线提问，避免"只改优先级"却把原提问覆盖成模板句。
    if base_query and not any(
        word in user_part for word in ("改成问", "换成问", "改问", "重新问", "改成查询")
    ):
        rewritten = base_query
        base_period = _prompt_period(base_part)
        if period and base_period and period != base_period:
            rewritten = _PERIOD_PATTERN.sub(period, rewritten)
        if (
            ("口语化" in user_part or "口语" in user_part)
            and not rewritten.startswith("麻烦问下")
        ):
            rewritten = f"麻烦问下，{rewritten}"
        query = rewritten

    grading = "ANSWER_CONTRACT" if (amounts or "契约" in request) else "LEGACY"
    if grading == "ANSWER_CONTRACT":
        name = f"{name}（契约）"
        tags = [*tags, "答案契约"]

    must: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    warnings: list[str] = []
    if period:
        must.append(
            {
                "label": "本期账期",
                "kind": "date",
                "value": period,
                "source": "USER",
                "source_ref": None,
            }
        )
    if amounts:
        must.append(
            {
                "label": fact_label,
                "kind": "scalar",
                "value": float(amounts[0]),
                "unit": unit,
                "tolerance": 0.01,
                "aliases": ["total_amount"] if fact_label == "账单总额" else [],
                "require_name": True,
                "source": "USER",
                "source_ref": None,
            }
        )
    else:
        missing.append(
            {
                "field": "must.amount",
                "label": fact_label,
                "reason": "用户描述中没有给出具体数值，已留空待补充",
            }
        )
        warnings.append(f"{fact_label}缺少权威依据，已留空，请人工补充")

    if "费用项" in request:
        missing.append(
            {
                "field": "must.item_count",
                "label": "费用项数",
                "reason": "用户描述没有给出具体项数，已留空待补充",
            }
        )
        warnings.append("费用项数缺少权威依据，已留空，请人工补充")

    must_not: list[dict[str, Any]] = []
    for amount in amounts[1:]:
        must_not.append(
            {
                "label": "错误金额",
                "kind": "text",
                "value": amount,
                "source": "USER",
                "source_ref": None,
            }
        )
        warnings.append(f"已将 {amount} 作为禁止出现的错误金额")

    description = (
        f"校验 {subject} 在 {period_text} 的{fact_label}"
        + ("与费用项数" if "费用项" in request else "")
        + "，关注数值准确性与口径一致性。"
    )

    turns: list[dict[str, Any]] = []
    if not base_turns and re.search(r"多轮|两轮|2\s*轮|三轮|3\s*轮|追问|连续对话", user_part):
        # 新建多轮：先给出与单轮一致的提问，再补一轮口语化追问；只写问法，不编造数值。
        turns = [
            {"index": 1, "query": query, "expected_answer": None, "notes": None},
            {
                "index": 2,
                "query": "那这笔账单的费用明细都有哪些？",
                "expected_answer": None,
                "notes": "追问明细，验证上下文继承",
            },
        ]
        if "三轮" in user_part or "3 轮" in user_part:
            turns.append(
                {
                    "index": 3,
                    "query": "这些费用里哪一项超套了？",
                    "expected_answer": None,
                    "notes": "继续追问超套项",
                }
            )
    if base_turns:
        turns = [dict(item) for item in base_turns]
        # 支持「把第 2 轮改成『...』」这类明确的轮次改写。
        quoted = re.search(
            r"第\s*[0-9一二三四五六七八九十]+\s*轮[^「『\"“]*[「『\"“]([^」』\"”]+)[」』\"”]",
            user_part,
        )
        if quoted:
            target = (_turn_no(user_part) or 1) - 1
            if 0 <= target < len(turns):
                turns[target]["query"] = quoted.group(1).strip()
        # 用户给了新账期时，同步替换各轮里的旧账期。
        base_period = _prompt_period(base_part)
        if period and base_period and period != base_period:
            for turn in turns:
                turn["query"] = _PERIOD_PATTERN.sub(period, str(turn.get("query") or ""))
        if "口语化" in user_part and turns:
            index = max(0, min(len(turns) - 1, (_turn_no(user_part) or 1) - 1))
            query_text = str(turns[index].get("query") or "")
            if not query_text.startswith("麻烦问下"):
                turns[index]["query"] = f"麻烦问下，{query_text}"
        # 「补充一轮…追问」：在末尾追加一轮口语化追问，只写问法，不带任何数值。
        if re.search(r"(?:补充|增加|添加|再来)\s*(?:一|1|两|2)?\s*轮", user_part):
            topic = "费用明细" if "明细" in user_part else ("账单总额" if "总额" in user_part else "账单")
            extra_query = f"那这笔账单的{topic}能再说细一点吗？"
            if all(extra_query != str(turn.get("query") or "") for turn in turns):
                turns.append(
                    {
                        "index": len(turns) + 1,
                        "query": extra_query,
                        "expected_answer": None,
                        "notes": "用户在基线基础上新增的追问轮次",
                    }
                )
        if base_name and not any(word in user_part for word in ("名称", "改名", "命名为")):
            name = base_name
        if base_description and "描述" not in user_part:
            description = base_description

    priority_match = re.search(r"优先级[^P0-9]{0,6}P([0-3])", user_part) or re.search(
        r"(?<![A-Za-z])P([0-3])(?![0-9])", user_part
    )
    if priority_match:
        priority = f"P{priority_match.group(1)}"
        priority_source = "USER"
    elif base_priority:
        priority = base_priority
        priority_source = "CASE"
    else:
        priority = "P0" if grading == "ANSWER_CONTRACT" else "P1"
        priority_source = "LLM"

    # 动态多轮：只给目标 / 初始提问 / 隐藏信息 / 轮次上限，脚本交给模拟器实时生成。
    dynamic_requested = bool(
        re.search(r"动态多轮|动态追问|隐藏信息|不主动给|反问后|挤牙膏", user_part)
    )
    dynamic: dict[str, Any] | None = None
    if base_dynamic is not None or (not base_turns and dynamic_requested):
        seed = base_dynamic or {}
        hidden = [dict(item) for item in seed.get("hidden_context") or []]
        # 基线里可能用不同 key 记录了同一实体（如 phone / 手机号），按 key 与取值双重去重。
        if entity and all(
            str(item.get("key")) != entity[0] and entity[1] not in str(item.get("content") or "")
            for item in hidden
        ):
            hidden.append(
                {
                    "key": entity[0],
                    "content": entity[1],
                    "release_hint": f"客服反问{entity[0]}时释放",
                    "source": "USER",
                }
            )
        goal_quote = re.search(r"用户目标[^「『\"“]*[「『\"“]([^」』\"”]+)[」』\"”]", user_part)
        if goal_quote:
            goal = goal_quote.group(1).strip()
        elif seed.get("user_goal"):
            goal = str(seed["user_goal"])
        else:
            hidden_key = str(hidden[0]["key"]) if hidden else "关键条件"
            goal = f"不主动提供{hidden_key}，通过反问拿到 {period_text} 的{fact_label}并核对口径"
        initial_query = seed.get("initial_query") or f"我想核对一下{period_text}的{fact_label}"
        quotes = re.search(r"初始提问[^「『\"“]*[「『\"“]([^」』\"”]+)[」』\"”]", user_part)
        if quotes:
            initial_query = quotes.group(1).strip()
        max_turns = int(seed.get("max_turns") or 5)
        turns_match = re.search(
            r"(?:最多|不超过|最大|改成|改为|设置为|限制为)\s*(\d{1,2})\s*轮", user_part
        )
        if turns_match:
            max_turns = int(turns_match.group(1))
        keywords = list((seed.get("terminate_conditions") or {}).get("keywords") or [])
        if not keywords:
            keywords = [fact_label]
        dynamic = {
            "user_goal": goal,
            "initial_query": initial_query,
            "persona_description": seed.get("persona_description")
            or "普通用户，说话简短，只在客服反问时补充关键条件",
            "hidden_context": hidden,
            "max_turns": max_turns,
            "terminate_conditions": {"keywords": keywords},
        }

    # 全字段覆盖：请求变量 / 4 维规则 / 响应契约 / 取数计划 / 配置。
    request_variables: dict[str, Any] = {}
    if entity:
        key = {"手机号": "phone", "宽带账号": "account_no", "订单号": "order_no"}.get(
            entity[0], "entity"
        )
        request_variables[key] = entity[1]
    if period:
        request_variables["billing_period"] = period

    # 4 维评分规则：LEGACY 下只用有依据的日期，绝不编造金额。
    expected_rules: dict[str, Any] | None = None
    if grading == "LEGACY" and period:
        expected_rules = {
            "amounts": [],
            "counts": [],
            "dates": [period],
            "must_contain": [],
            "must_not_contain": [],
        }

    # 取数计划：能力编码使用系统既有用例的词汇。
    capability = _author_capability(topic)
    plan_spec: dict[str, Any] | None = None
    if capability:
        plan_spec = {
            "required_capabilities": [capability],
            "max_tool_calls": 2,
            "allow_parallel": True,
        }

    ambiguous = bool(
        re.search(r"模糊|歧义|没说清|信息不全|指代不清|不清晰", user_part)
    )
    input_type = "UNDERSPECIFIED" if ambiguous else "CLEAR"
    response_contract: dict[str, Any] | None = None
    if ambiguous:
        response_contract = {
            "allowed": ["clarify", "refuse"],
            "clarify": {
                "must_ask_slot": ["time_range"],
                "must_not_assert": ["amount"],
            },
            "refuse": {"must_reason": True},
        }

    threshold_match = re.search(r"(?:阈值|分数线)\s*(\d{1,3})", user_part)
    pass_threshold: float | None = None
    if threshold_match:
        value = int(threshold_match.group(1))
        if 0 <= value <= 100:
            pass_threshold = float(value)
    if re.search(r"VETO|一票否决", user_part, re.IGNORECASE):
        judge_strategy: str | None = "VETO"
    elif re.search(r"AVG|平均分", user_part, re.IGNORECASE):
        judge_strategy = "AVG"
    else:
        judge_strategy = None

    fail_fast: bool | None = None
    if re.search(r"失败即停|快速失败|fail.?fast", user_part, re.IGNORECASE):
        fail_fast = True
    if re.search(r"严格包络|strict", user_part, re.IGNORECASE):
        extras_policy: str | None = "strict"
    elif re.search(r"宽松包络|lenient", user_part, re.IGNORECASE):
        extras_policy = "lenient"
    else:
        extras_policy = None

    change_summary = "按用户要求更新了提问与语义字段" if base_part.strip() else None

    payload: dict[str, Any] = {
        "case_type": "STATIC_MULTI" if turns else "SINGLE",
        "name": name,
        "description": description,
        "priority": priority,
        "tags": tags,
        "grading_mode": grading,
        "input_type": input_type,
        "as_of": period,
        "query": None if turns else query,
        "expected_answer": (
            None
            if turns
            else (f"{period_text}{fact_label}为 {amounts[0]} 元" if amounts else None)
        ),
        "turns": turns,
        "request_variables": request_variables or None,
        "expected_rules": expected_rules,
        "plan_spec": plan_spec,
        "response_contract": response_contract,
        "fail_fast": fail_fast,
        "extras_policy": extras_policy,
        "judge_strategy": judge_strategy,
        "pass_threshold": pass_threshold,
        "change_summary": change_summary,
        "risk_level": "HIGH" if grading == "ANSWER_CONTRACT" else "LOW",
        "confidence": 0.72,
        "must": must,
        "may": [],
        "must_not": must_not,
        "forbidden_phrases": [],
        "field_sources": {
            "case_type": "CASE" if base_turns else "LLM",
            "name": "CASE" if base_name and name == base_name else "LLM",
            "priority": priority_source,
            "query": "CASE" if base_query and query == base_query else "LLM",
            "turns": "CASE" if base_turns else ("LLM" if turns else "NONE"),
            "as_of": "USER" if period else "NONE",
            "request_variables": "USER" if request_variables else "NONE",
            "expected_rules": "USER" if expected_rules else "NONE",
            "plan_spec": "LLM" if plan_spec else "NONE",
            fact_label: "USER" if amounts else "NONE",
        },
        "missing": missing,
        "warnings": warnings,
    }

    if dynamic is not None:
        payload.update(dynamic)
        payload["case_type"] = "DYNAMIC_MULTI"
        payload["query"] = None
        payload["expected_answer"] = None
        payload["turns"] = []
        payload["field_sources"] = {
            **payload["field_sources"],
            "case_type": "CASE" if base_dynamic is not None else "LLM",
            "query": "NONE",
            "turns": "NONE",
            "user_goal": "CASE" if base_dynamic and base_dynamic.get("user_goal") else "LLM",
            # 编辑场景下隐藏信息沿用基线，来源应标 CASE 而不是 USER。
            "hidden_context": (
                "CASE"
                if base_dynamic is not None
                else ("USER" if dynamic["hidden_context"] else "NONE")
            ),
            "max_turns": "LLM",
        }

    return payload


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-llm"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict[str, Any]:
    payload = await request.json()
    messages = payload.get("messages") or []
    model = str(payload.get("model") or "mock-model")
    prompt = "\n".join(str(item.get("content") or "") for item in messages)
    is_author = "问账测试用例编写助手" in prompt or "# Generation Mode" in prompt

    # 故障注入只认执行类 Prompt。用例编写 Prompt 会附带相似用例，
    # 其中可能包含 [MOCK:LLMERROR] 文本，不能误判为裁判/模拟器故障。
    if "[MOCK:LLMERROR]" in prompt and not is_author:
        # 故障注入：模拟模型服务 5xx，验证执行错误分类与任务级自动重试。
        raise HTTPException(status_code=500, detail="mock llm error injected")

    if "用户模拟器" in prompt or "你正在扮演一名真实的业务用户" in prompt:
        content: Any = _simulator_content(prompt)
    elif "审计裁判官" in prompt or "Scoring Rubrics" in prompt:
        content = _judge_content(prompt, model)
    elif is_author:
        content = _author_content(prompt)
    elif '"ok": true' in prompt:
        content = {"ok": True}
    else:
        content = {"echo": prompt[:200]}

    await asyncio.sleep(0.03)
    text = json.dumps(content, ensure_ascii=False)
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(text) // 4)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
