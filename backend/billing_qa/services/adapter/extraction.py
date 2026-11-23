"""条件解析引擎：环境 extractors 的「规则对象」形态在此求值。

与字符串 JSONPath（extractor.py 原有行为）的关系：

- 值为字符串 -> 走 extractor.extract_value，行为与历史版本完全一致；
- 值为对象（含 ``rules``） -> 走本模块：规则按序求值，首个产出非空值的
  规则生效（即 if / elif / else 语义），全部未命中由调用方兜底。

规则结构::

    {
      "rules": [
        {
          "id": "if-last-chart",           # 可选，用于 Trace 与报错定位
          "path": "$..[?(@.type=='chart')]",  # 定位表达式（jsonpath-ng ext 语法）
          "occurrence": "last",            # first(默认) | last | 0-based 下标
          "take": ["$.message", "$..message"],   # 命中节点上的相对路径，按序尝试
          "take_join": {                   # 拼接取值：多字段非空值按分隔符拼接
            "fields": ["$.message", "$..detail", "parent::$.remark"],
            "separator": "\n"
          },
          "take_parent": ["$.message"]     # 最近 dict 祖先上的相对路径（上级场景）
        },
        {"id": "else-context", "path": "$..context", "occurrence": "last"}
      ],
      "fallback_content": true,   # 流式全未命中时回退聚合文本
      "legacy_fallback": false    # 是否再走 FALLBACK_ANSWER_PATHS（仅 final_answer）
    }

求值顺序严格为：先按 ``path`` + ``occurrence`` 定位节点，再依次尝试
``take``（同级/下层，首个非空生效）→ ``take_join``（跨层级拼接）→
``take_parent``（上级兜底），保证「最后一次出现」的语义优先于
「较早出现但字段层级更靠前」的命中。``take_join.fields`` 每条路径
不带前缀在命中节点上求值（同级 ``$.x`` / 子层 ``$..x``），带
``parent::`` 前缀在最近 dict 祖先上求值（上级），支持跨层级混拼。

提取文档由 :class:`ExtractionContext` 构造：``_frames``（SSE 有序帧，
最多 300 帧）与 ``_content``（聚合文本）排在最前，保证 ``first`` / ``last``
与流的到达顺序一致；非流式响应 frames 为空列表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jsonpath_ng import parse as jsonpath_parse
from jsonpath_ng.ext import parse as jsonpath_ext_parse

from billing_qa.core.logging import get_logger

logger = get_logger(__name__)

# 规格级允许的键
_SPEC_KEYS = {"rules", "fallback_content", "legacy_fallback"}
# 规则级允许的键
_RULE_KEYS = {"id", "path", "occurrence", "take", "take_join", "take_parent"}
# take_join 允许的键
_JOIN_KEYS = {"fields", "separator"}


def is_extraction_spec(value: Any) -> bool:
    """extractor 值是否为条件规则对象（含 rules 键的 dict）。"""
    return isinstance(value, dict) and "rules" in value


def _joinable(rule: dict) -> bool:
    """规则是否配置了可用的拼接取值（take_join.fields 非空列表）。"""
    join_spec = rule.get("take_join")
    return (
        isinstance(join_spec, dict)
        and isinstance(join_spec.get("fields"), list)
        and bool(join_spec["fields"])
    )


@dataclass
class ExtractionContext:
    """一次响应的提取上下文：payload（合并元数据或 JSON body）+ 有序帧 + 聚合文本。"""

    payload: Any = None
    frames: list[Any] = field(default_factory=list)
    content: str = ""

    def document(self) -> Any:
        """构造提取文档；``_frames`` / ``_content`` 置前保证文档序 = 流序。"""
        if not isinstance(self.payload, dict):
            return self.payload
        doc: dict[str, Any] = {"_frames": self.frames, "_content": self.content}
        doc.update(self.payload)
        return doc


def _pick_occurrence(matches: list[Any], occurrence: Any) -> Any | None:
    """按 occurrence 选择器从匹配集中取一个；无命中返回 None。"""
    if not matches:
        return None
    if occurrence == "last":
        return matches[-1]
    if occurrence in (None, "first"):
        return matches[0]
    try:
        index = int(occurrence)
    except (TypeError, ValueError):
        return matches[0]
    return matches[index] if 0 <= index < len(matches) else None


def _is_non_empty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _nearest_dict_ancestor(match: Any) -> dict | None:
    """从匹配节点向上找最近的 dict 祖先（跳过 list 包装）。"""
    context = match.context
    while context is not None:
        if isinstance(context.value, dict):
            return context.value
        context = context.context
    return None


def _relative_value(root: Any, expression: str) -> Any:
    """在匹配节点（或其祖先）上执行相对路径，返回首个命中值。"""
    if root is None:
        return None
    try:
        matches = jsonpath_parse(expression).find(root)
    except Exception as exc:  # jsonpath_ng 抛多种异常，统一降级
        logger.warning("条件解析 take 表达式非法 %s: %s", expression, exc)
        return None
    return matches[0].value if matches else None


def _to_text(value: Any) -> str:
    """拼接前统一转文本；dict/list 序列化为 JSON。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _split_parent_prefix(expression: str) -> tuple[bool, str]:
    """拆出 ``parent::`` 前缀：返回 (是否上级, 剩余相对路径)。"""
    if expression.startswith("parent::"):
        return True, expression[len("parent::") :]
    return False, expression


def _join_fields(
    chosen: Any,
    ancestor: dict | None,
    fields: list[str],
    separator: str,
) -> Any | None:
    """拼接取值：逐条求相对路径，收集非空值按分隔符拼接。

    每条 field 支持三级导航：不带前缀在命中节点上求值（同级 ``$.x`` /
    子层 ``$..x``）；带 ``parent::`` 前缀在最近 dict 祖先上求值（上级）。
    缺失字段跳过（宽容拼接）；全部为空返回 None（规则未命中）。
    """
    values: list[str] = []
    for raw in fields:
        is_parent, relative = _split_parent_prefix(str(raw))
        root = ancestor if is_parent else chosen.value
        if root is None:
            continue
        value = _relative_value(root, relative)
        if _is_non_empty(value):
            values.append(_to_text(value))
    if not values:
        return None
    return separator.join(values) if len(values) > 1 else values[0]


def _locate_source(match: Any, frames: list[Any]) -> tuple[str, int | None]:
    """判断命中节点来自第几帧（对象同一性判断）；非帧内命中标记为 payload。"""
    node: Any = match
    while node is not None:
        value = getattr(node, "value", None)
        for index, frame in enumerate(frames):
            if frame is value:
                return "frame", index
        node = getattr(node, "context", None)
    return "payload", None


def _match_index(matches: list[Any], chosen: Any) -> int | None:
    """被选中的匹配在匹配集中的下标（对象同一性判断）。"""
    for index, item in enumerate(matches):
        if item is chosen:
            return index
    return None


def evaluate_rules(
    doc: Any, rules: list[Any], frames: list[Any] | None = None
) -> tuple[Any, dict[str, Any] | None]:
    """按序求值规则列表；返回 (值, 命中轨迹)，全未命中返回 (None, None)。"""
    frames = frames or []
    for position, rule in enumerate(rules):
        if not isinstance(rule, dict):
            logger.warning("条件解析规则 #%d 不是对象，已跳过", position)
            continue
        path = rule.get("path")
        if not path or not isinstance(path, str):
            logger.warning("条件解析规则 #%d 缺少 path，已跳过", position)
            continue
        try:
            matches = jsonpath_ext_parse(path).find(doc)
        except Exception as exc:
            logger.warning("条件解析规则 path 非法 %s: %s", path, exc)
            continue
        chosen = _pick_occurrence(matches, rule.get("occurrence", "first"))
        if chosen is None:
            continue
        take_list = rule.get("take") or []
        parent_list = rule.get("take_parent") or []
        rule_id = str(rule.get("id") or path)
        source, frame_index = _locate_source(chosen, frames)
        trace_base = {
            "rule": rule_id,
            "path": path,
            "rule_index": position,
            "occurrence_index": _match_index(matches, chosen),
            "source": source,
            "frame_index": frame_index,
        }
        # 直取规则：无 take / take_join / take_parent 时返回匹配值本身
        if not take_list and not parent_list and not _joinable(rule):
            if _is_non_empty(chosen.value):
                return chosen.value, {**trace_base, "take": None}
            continue
        # 同级 / 下层：对命中节点重定根求值
        for relative in take_list:
            value = _relative_value(chosen.value, str(relative))
            if _is_non_empty(value):
                return value, {**trace_base, "take": str(relative)}
        ancestor = _nearest_dict_ancestor(chosen)
        # 拼接取值：跨层级（同级 / 子层 / parent:: 上级）多字段拼接
        join_spec = rule.get("take_join")
        if isinstance(join_spec, dict) and isinstance(join_spec.get("fields"), list):
            fields = [str(item) for item in join_spec["fields"]]
            separator = join_spec.get("separator", "\n")
            if not isinstance(separator, str):
                separator = "\n"
            value = _join_fields(chosen, ancestor, fields, separator)
            if _is_non_empty(value):
                joined = "+".join(fields)
                return value, {**trace_base, "take": f"join:{joined}"}
        # 上级：对最近 dict 祖先重定根求值
        for relative in parent_list:
            if ancestor is None:
                break
            value = _relative_value(ancestor, str(relative))
            if _is_non_empty(value):
                return value, {**trace_base, "take": f"parent::{relative}"}
    return None, None


def evaluate_spec(context: ExtractionContext, spec: Any) -> tuple[Any, dict[str, Any] | None]:
    """对条件规则对象求值；spec 非法时记录告警并返回 (None, None)。"""
    if not isinstance(spec, dict) or not isinstance(spec.get("rules"), list):
        logger.warning("条件解析规格缺少 rules 列表，按未命中处理")
        return None, None
    return evaluate_rules(context.document(), spec["rules"], context.frames)


# --------------------------------------------------------------------- #
# 保存期校验（环境 API 携带时返回明确错误，避免运行期才发现配置问题）
# --------------------------------------------------------------------- #
def _validate_expression(expression: str, where: str) -> None:
    try:
        jsonpath_ext_parse(expression)
    except Exception as exc:
        raise ValueError(f"{where} 表达式非法: {expression}（{exc}）") from exc


def _validate_relative(expression: str, where: str) -> None:
    if not expression.startswith("$"):
        raise ValueError(f"{where} 相对路径需以 $ 开头: {expression}")
    try:
        jsonpath_parse(expression)
    except Exception as exc:
        raise ValueError(f"{where} 相对路径非法: {expression}（{exc}）") from exc


def validate_extractors(mapping: Any) -> Any:
    """保存期校验环境 extractors。

    - 条件规则对象：严格校验（结构、表达式、occurrence、未知键），
      非法直接抛 ValueError（API 层转为 422）；
    - 字符串表达式：与历史行为一致，仅解析失败时告警、不阻断保存。
    """
    if mapping is None:
        return None
    if not isinstance(mapping, dict):
        raise ValueError("extractors 需为「字段 -> 表达式/规则」的对象")
    for field_name, value in mapping.items():
        where = f"extractors.{field_name}"
        if isinstance(value, str):
            try:
                jsonpath_ext_parse(value)
            except Exception as exc:
                # 与历史兼容：字符串路径不在保存期阻断，运行期按未命中降级
                logger.warning("%s JSONPath 表达式非法（保存不阻断）: %s", where, exc)
            continue
        if not isinstance(value, dict):
            raise ValueError(f"{where} 需为字符串 JSONPath 或条件规则对象")
        unknown = set(value) - _SPEC_KEYS
        if unknown:
            raise ValueError(f"{where} 含未知配置项: {', '.join(sorted(unknown))}")
        rules = value.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"{where}.rules 需为非空规则列表")
        for index, rule in enumerate(rules):
            rule_where = f"{where}.rules[{index}]"
            if not isinstance(rule, dict):
                raise ValueError(f"{rule_where} 需为对象")
            rule_unknown = set(rule) - _RULE_KEYS
            if rule_unknown:
                raise ValueError(f"{rule_where} 含未知配置项: {', '.join(sorted(rule_unknown))}")
            path = rule.get("path")
            if not isinstance(path, str) or not path.strip():
                raise ValueError(f"{rule_where}.path 不能为空")
            _validate_expression(path, rule_where)
            occurrence = rule.get("occurrence", "first")
            if occurrence not in ("first", "last"):
                try:
                    int(occurrence)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{rule_where}.occurrence 需为 first / last / 非负整数下标"
                    ) from exc
                if int(occurrence) < 0:
                    raise ValueError(f"{rule_where}.occurrence 下标不能为负数")
            for key in ("take", "take_parent"):
                items = rule.get(key)
                if items is None:
                    continue
                if not isinstance(items, list) or not all(
                    isinstance(item, str) and item.strip() for item in items
                ):
                    raise ValueError(f"{rule_where}.{key} 需为非空字符串数组")
                for item in items:
                    _validate_relative(item, f"{rule_where}.{key}")
            join_spec = rule.get("take_join")
            if join_spec is not None:
                join_where = f"{rule_where}.take_join"
                if not isinstance(join_spec, dict):
                    raise ValueError(f"{join_where} 需为对象")
                join_unknown = set(join_spec) - _JOIN_KEYS
                if join_unknown:
                    raise ValueError(
                        f"{join_where} 含未知配置项: {', '.join(sorted(join_unknown))}"
                    )
                join_fields = join_spec.get("fields")
                if (
                    not isinstance(join_fields, list)
                    or not join_fields
                    or not all(isinstance(item, str) and item.strip() for item in join_fields)
                ):
                    raise ValueError(f"{join_where}.fields 需为非空字符串数组")
                for item in join_fields:
                    is_parent, relative = _split_parent_prefix(item)
                    where = f"{join_where}.fields"
                    if is_parent:
                        where += "（parent:: 前缀）"
                    _validate_relative(relative, where)
                separator = join_spec.get("separator")
                if separator is not None and not isinstance(separator, str):
                    raise ValueError(f"{join_where}.separator 需为字符串")
        for key in ("fallback_content", "legacy_fallback"):
            flag = value.get(key)
            if flag is not None and not isinstance(flag, bool):
                raise ValueError(f"{where}.{key} 需为布尔值")
    return mapping
