"""名称-数值绑定：让事实匹配从「答案出现过这个数值」升级为「数值归属这个业务名称」。

背景：仅按数值匹配时，``账单总额=100`` / ``税额=10`` 即使被写成
``账单总额=10 / 税额=100`` 也会全部通过，属于语义错误。本模块提供三层能力：

1. **JSON 键绑定**：最终答案本身是 JSON（或带 ```json 代码块）时，按 key 取值；
2. **分句绑定**：中文散文中，名称与数值处于同一分句（或短窗口内）才算归属；
3. **冲突识别**：名称缺失时允许按数值兜底，但若该数值已明确绑定到另一个
   业务名称，则判定为「张冠李戴」。

所有判定均为确定性代码，不依赖 LLM。LLM 抽取的 claim 仅作为最后的名称锚定补证。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, TypeError):
        return None


def close_enough(actual: Any, expected: Any, tolerance: float) -> bool:
    left = to_decimal(actual)
    right = to_decimal(expected)
    if left is None or right is None:
        return str(actual).strip() == str(expected).strip()
    return abs(left - right) <= Decimal(str(tolerance or 0)) + Decimal("1e-9")

# 与 rule_evaluator 保持同一口径：金额必须带货币标记或「元」后缀。
_CURRENCY_AMOUNT = re.compile(
    r"(?:¥|￥|RMB|人民币)\s*"
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"\s*(万元|万|元)?"
)
_UNIT_AMOUNT = re.compile(
    r"(?<![\d.])"
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"\s*(万元|万|元)"
)
_COUNT_PATTERN = re.compile(r"(\d+)\s*(笔|条|单|次|个|张|份|项|期)")
# JSON / ``key: 100`` 这类没有货币标记的裸数字，只在已出现业务名称的分句内兜底。
_BARE_NUMBER = re.compile(
    r"(?<![\w.¥￥%])"
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"(?![\w%])"
)
_NUMERIC_STRING = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
# 分句：名称与数值的绑定不跨越句号、换行、分号、逗号、表格竖线。
_SEGMENT_SPLIT = re.compile(r"[。！？!?\n；;，,|]+")
# 名称归一化：大小写、空白、下划线、连字符、标点都不参与比较。
_NAME_DROP = re.compile(r"[\s,，。、；;：:！!？?（）()【】\[\]“”\"'‘’《》<>·|/\\_\-]+")
#: 名称与数值不在同一分句时，允许的字符窗口。
_NAME_WINDOW = 32


@dataclass(frozen=True)
class NumberSpan:
    """答案中的一个数值及其原文位置。"""

    value: float
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class NameHit:
    """一个业务名称在答案中的出现位置。"""

    name: str
    start: int
    end: int


@dataclass(frozen=True)
class Segment:
    start: int
    end: int


@dataclass(frozen=True)
class Binding:
    """名称与数值的绑定关系。"""

    name: str
    number: NumberSpan
    mode: str  # segment / window
    segment: str = ""


@dataclass
class AnchorResult:
    """名称锚定匹配结果。"""

    passed: bool
    mode: str
    bound_value: float | None = None
    bound_name: str | None = None
    conflict_name: str | None = None
    reason: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "mode": self.mode,
            "bound_value": self.bound_value,
            "bound_name": self.bound_name,
            "conflict_name": self.conflict_name,
            "reason": self.reason,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------- #
# 名称归一化与定位
# ---------------------------------------------------------------------- #
def compact_name(text: str | None) -> tuple[str, list[int]]:
    """返回压缩后的名称文本和每个字符在原串中的下标。"""
    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text or ""):
        if _NAME_DROP.match(char):
            continue
        chars.append(char.lower())
        offsets.append(index)
    return "".join(chars), offsets


def normalize_name(text: str | None) -> str:
    return compact_name(text)[0]


def dedupe_names(names: Iterable[str | None]) -> list[str]:
    """按归一化结果去重，保留原始写法。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name is None:
            continue
        raw = str(name).strip()
        key = normalize_name(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(raw)
    return ordered


def find_names(text: str, names: Iterable[str]) -> list[NameHit]:
    """返回所有名称出现位置，按起点排序。"""
    compact, offsets = compact_name(text)
    if not compact:
        return []
    hits: list[NameHit] = []
    seen: set[tuple[int, int]] = set()
    for name in names:
        needle = normalize_name(name)
        if not needle:
            continue
        cursor = compact.find(needle)
        while cursor >= 0:
            end_index = cursor + len(needle) - 1
            span = (offsets[cursor], offsets[end_index] + 1)
            if span not in seen:
                seen.add(span)
                hits.append(NameHit(str(name), span[0], span[1]))
            cursor = compact.find(needle, cursor + 1)
    hits.sort(key=lambda item: (item.start, item.end))
    return hits


def segments(text: str) -> list[Segment]:
    """按句号 / 换行 / 分号 / 逗号 / 表格竖线切分句。"""
    result: list[Segment] = []
    cursor = 0
    for match in _SEGMENT_SPLIT.finditer(text or ""):
        if match.start() > cursor:
            result.append(Segment(cursor, match.start()))
        cursor = match.end()
    if cursor < len(text or ""):
        result.append(Segment(cursor, len(text)))
    return result


# ---------------------------------------------------------------------- #
# 数值抽取
# ---------------------------------------------------------------------- #
def money_spans(text: str) -> list[NumberSpan]:
    """带货币标记或「元」后缀的金额。"""
    spans: dict[int, NumberSpan] = {}
    for pattern in (_CURRENCY_AMOUNT, _UNIT_AMOUNT):
        for match in pattern.finditer(text or ""):
            start = match.start(1)
            if start in spans:
                continue
            number = to_decimal(match.group(1))
            if number is None:
                continue
            if match.group(2) in {"万", "万元"}:
                number = number * Decimal(10000)
            spans[start] = NumberSpan(
                float(number), match.group(0).strip(), match.start(), match.end()
            )
    return [spans[key] for key in sorted(spans)]


def count_spans(text: str) -> list[NumberSpan]:
    """带量词的计数（笔/条/项/期…）。"""
    return [
        NumberSpan(float(match.group(1)), match.group(0).strip(), match.start(), match.end())
        for match in _COUNT_PATTERN.finditer(text or "")
    ]


def number_spans(
    text: str, names: Iterable[str], *, kind: str = "auto"
) -> list[NumberSpan]:
    """抽取候选数值；名称所在分句没有货币标记时，补抽裸数字（JSON / key:value）。

    ``kind``：money（金额）/ count（带量词计数）/ auto（两者合并，供契约标量使用）。
    """
    if kind == "money":
        base = money_spans(text)
    elif kind == "count":
        base = count_spans(text)
    else:
        base = _dedupe_spans([*money_spans(text), *count_spans(text)])
    name_list = list(names)
    extra: list[NumberSpan] = []
    for segment in segments(text):
        has_name = any(
            segment.start <= hit.start < segment.end for hit in find_names(text, name_list)
        )
        if not has_name:
            continue
        if kind in {"money", "auto"} and any(
            segment.start <= span.start < segment.end for span in base
        ):
            continue
        for match in _BARE_NUMBER.finditer(text, segment.start, segment.end):
            number = to_decimal(match.group(1))
            if number is None:
                continue
            extra.append(
                NumberSpan(float(number), match.group(0).strip(), match.start(), match.end())
            )
    return _dedupe_spans([*base, *extra])


def _dedupe_spans(spans: list[NumberSpan]) -> list[NumberSpan]:
    merged: list[NumberSpan] = []
    for span in sorted(spans, key=lambda item: (item.start, item.end)):
        if merged and span.start < merged[-1].end:
            continue
        merged.append(span)
    return merged


# ---------------------------------------------------------------------- #
# 名称-数值绑定
# ---------------------------------------------------------------------- #
def bind_names(text: str, names: Iterable[str], spans: list[NumberSpan]) -> list[Binding]:
    """把名称绑定到最近的数值：同分句优先，窗口兜底。"""
    name_list = list(names)
    hits = find_names(text, name_list)
    if not hits or not spans:
        return []
    segs = segments(text)
    bindings: list[Binding] = []
    for hit in hits:
        segment = next(
            (item for item in segs if item.start <= hit.start < item.end), None
        )
        candidates = (
            [span for span in spans if segment.start <= span.start < segment.end]
            if segment
            else []
        )
        chosen, mode = _nearest(hit, candidates)
        if chosen is None:
            nearby = [span for span in spans if _distance(hit, span) <= _NAME_WINDOW]
            chosen, mode = _nearest(hit, nearby)
            if chosen is not None:
                mode = "window"
        if chosen is None:
            continue
        bindings.append(
            Binding(
                name=hit.name,
                number=chosen,
                mode=mode,
                segment=(text[segment.start : segment.end].strip() if segment else ""),
            )
        )
    return bindings


def _nearest(hit: NameHit, candidates: list[NumberSpan]) -> tuple[NumberSpan | None, str]:
    if not candidates:
        return None, ""
    after = [span for span in candidates if span.start >= hit.end]
    if after:
        return min(after, key=lambda span: span.start - hit.end), "segment"
    before = [span for span in candidates if span.end <= hit.start]
    if before:
        return min(before, key=lambda span: hit.start - span.end), "segment"
    return None, ""


def _distance(hit: NameHit, span: NumberSpan) -> int:
    if span.start >= hit.end:
        return span.start - hit.end
    if span.end <= hit.start:
        return hit.start - span.end
    return 0


# ---------------------------------------------------------------------- #
# JSON 答案
# ---------------------------------------------------------------------- #
def json_number_map(text: str) -> dict[str, list[tuple[float, str]]] | None:
    """解析最终答案里的 JSON 对象，返回「归一化 key → 数值」映射。"""
    for candidate in _json_candidates(text):
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        result: dict[str, list[tuple[float, str]]] = {}
        _flatten_json(data, "", result)
        return result
    return None


def _json_candidates(text: str) -> list[str]:
    raw = (text or "").strip()
    candidates: list[str] = []
    if raw.startswith("{") and raw.endswith("}"):
        candidates.append(raw)
    candidates.extend(match.group(1) for match in _JSON_FENCE.finditer(text or ""))
    return candidates


def _flatten_json(
    data: Any, prefix: str, out: dict[str, list[tuple[float, str]]]
) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                _flatten_json(value, path, out)
                continue
            number = _json_number(value)
            if number is None:
                continue
            for alias in {path, str(key)}:
                key_norm = normalize_name(alias)
                if not key_norm:
                    continue
                out.setdefault(key_norm, []).append((number, path))
    elif isinstance(data, list):
        for item in data:
            _flatten_json(item, prefix, out)


def _json_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _NUMERIC_STRING.fullmatch(value.strip()):
        return float(value.replace(",", ""))
    return None


# ---------------------------------------------------------------------- #
# 统一匹配入口
# ---------------------------------------------------------------------- #
def match_number(
    text: str,
    expected: Any,
    tolerance: Any,
    names: Iterable[str],
    spans: list[NumberSpan],
    *,
    group_key: str = "",
    other_groups: dict[str, list[str]] | None = None,
    claims: Iterable[Any] | None = None,
    require_name: bool = False,
) -> AnchorResult:
    """名称锚定的数值匹配。

    参数
    ----
    text: 最终答案原文。
    expected / tolerance: 期望值与容差。
    names: 该事实可接受的业务名称（label / aliases / 英文 key）。
    spans: 预先抽取的候选数值（金额或计数）。
    other_groups: 全部事实的名称表，用于识别数值归属到其他事实的「张冠李戴」。
    claims: LLM 抽取的断言，作为名称锚定的补充证据。
    require_name: True 时名称未出现或名称未绑定数值都判失败。
    """
    raw = text or ""
    name_list = dedupe_names(names)
    expected_dec = to_decimal(expected)
    if expected_dec is None:
        return AnchorResult(False, "invalid", reason=f"期望值不是数值: {expected}")
    tolerance_dec = Decimal(str(tolerance or 0))

    # 1) JSON key 绑定：key 本身就是业务名称时，数值必须与 key 对齐。
    json_map = json_number_map(raw)
    if json_map:
        matched_key = False
        wrong: list[tuple[float, str, str]] = []
        for name in name_list:
            entries = json_map.get(normalize_name(name)) or []
            if not entries:
                continue
            matched_key = True
            for value, path in entries:
                if abs(Decimal(str(value)) - expected_dec) <= tolerance_dec:
                    return AnchorResult(
                        True,
                        "json_key",
                        bound_value=value,
                        bound_name=name,
                        reason=f"JSON 字段「{path}」与期望值一致",
                        evidence=_key_evidence(raw, path),
                    )
                wrong.append((value, name, path))
        if matched_key and wrong:
            value, name, path = wrong[0]
            return AnchorResult(
                False,
                "json_key_mismatch",
                bound_value=value,
                bound_name=name,
                reason=f"JSON 字段「{path}」为 {value}，期望 {expected}",
                evidence=_key_evidence(raw, path),
            )

    # 2) 分句绑定：名称与数值同句才算归属。
    bindings = bind_names(raw, name_list, spans)
    if bindings:
        for binding in bindings:
            if close_enough(
                binding.number.value, float(expected_dec), float(tolerance_dec)
            ):
                return AnchorResult(
                    True,
                    "name_bound",
                    bound_value=binding.number.value,
                    bound_name=binding.name,
                    reason=f"名称「{binding.name}」绑定数值 {binding.number.value}",
                    evidence=_snippet(raw, binding.number),
                )
        binding = bindings[0]
        return AnchorResult(
            False,
            "name_mismatch",
            bound_value=binding.number.value,
            bound_name=binding.name,
            reason=(
                f"名称「{binding.name}」绑定到 {binding.number.value}，"
                f"期望 {expected}"
            ),
            evidence=_snippet(raw, binding.number),
        )

    # 3) LLM claim 补证：claim 原文同时出现名称与数值时，视为弱锚定。
    claim_result = _match_claim(raw, expected_dec, tolerance_dec, name_list, claims)
    if claim_result is not None:
        return claim_result

    mentions = bool(find_names(raw, name_list))
    hit = next(
        (
            span
            for span in spans
            if close_enough(span.value, float(expected_dec), float(tolerance_dec))
        ),
        None,
    )
    if hit is None:
        if mentions and require_name:
            return AnchorResult(
                False,
                "name_unbound",
                reason=f"答案出现了名称，但未与数值 {expected} 绑定",
            )
        return AnchorResult(
            False,
            "missing",
            reason=f"未在答案中找到 {expected}（容差 {tolerance or 0}）",
        )

    # 4) 数值已归属其他业务名称时，不能算作本事实命中。
    if other_groups:
        owners = _conflict_owners(
            raw, expected_dec, tolerance_dec, spans, other_groups, group_key
        )
        if owners:
            return AnchorResult(
                False,
                "name_conflict",
                bound_value=hit.value,
                conflict_name=owners[0],
                reason=(
                    f"数值 {expected} 在答案中归属于「{owners[0]}」，"
                    "与本事实名称不符"
                ),
                evidence=_snippet(raw, hit),
            )

    if require_name:
        return AnchorResult(
            False,
            "name_missing",
            bound_value=hit.value,
            reason=(
                f"答案未出现业务名称「{name_list[0] if name_list else ''}」，"
                "按名称绑定策略判失败"
            ),
            evidence=_snippet(raw, hit),
        )

    return AnchorResult(
        True,
        "value_only",
        bound_value=hit.value,
        reason="答案未出现业务名称，按数值匹配（未锚定名称）",
        evidence=_snippet(raw, hit),
    )


def _match_claim(
    text: str,
    expected: Decimal,
    tolerance: Decimal,
    names: list[str],
    claims: Iterable[Any] | None,
) -> AnchorResult | None:
    if not claims or not names:
        return None
    for claim in claims:
        if str(getattr(claim, "kind", "")) != "number":
            continue
        value = getattr(claim, "value", None)
        if not isinstance(value, (int, float)):
            continue
        claim_text = " ".join(
            str(getattr(claim, field, "") or "") for field in ("text", "evidence")
        )
        hit = find_names(claim_text, names)
        if not hit:
            continue
        if close_enough(value, float(expected), float(tolerance)):
            return AnchorResult(
                True,
                "claim_bound",
                bound_value=float(value),
                bound_name=hit[0].name,
                reason=f"断言中名称「{hit[0].name}」与数值 {value} 同句",
                evidence=claim_text[:120],
            )
        return AnchorResult(
            False,
            "claim_mismatch",
            bound_value=float(value),
            bound_name=hit[0].name,
            reason=(
                f"断言中名称「{hit[0].name}」绑定到 {value}，"
                f"期望 {expected}"
            ),
            evidence=claim_text[:120],
        )
    return None


def _conflict_owners(
    text: str,
    expected: Decimal,
    tolerance: Decimal,
    spans: list[NumberSpan],
    groups: dict[str, list[str]],
    group_key: str,
) -> list[str]:
    owners: list[str] = []
    for key, names in groups.items():
        if key == group_key:
            continue
        result = match_number(
            text,
            expected,
            tolerance,
            names,
            spans,
            require_name=False,
        )
        if result.passed and result.mode in {"name_bound", "json_key"}:
            owners.append(result.bound_name or (names[0] if names else key))
    return owners


def _snippet(text: str, span: NumberSpan, window: int = 30) -> str:
    start = max(0, span.start - window)
    end = min(len(text), span.end + window)
    return text[start:end].replace("\n", " ").strip()


def _key_evidence(text: str, path: str, window: int = 40) -> str:
    key = path.split(".")[-1]
    index = text.find(key)
    if index < 0:
        return ""
    start = max(0, index - window)
    end = min(len(text), index + len(key) + window)
    return text[start:end].replace("\n", " ").strip()
