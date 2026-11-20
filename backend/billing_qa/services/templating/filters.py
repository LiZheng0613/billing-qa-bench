"""模板表达式扩展：值转换工具、平台内置过滤器与全局函数。

新增过滤器建议直接在本模块登记到 ``BUILTIN_FILTERS``；运行期动态扩展可调用
``billing_qa.services.templating.register_filter`` / ``register_global``。
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
import uuid
from datetime import datetime
from typing import Any


def to_text(value: Any) -> str:
    """把任意值按 JSON 口径转成文本（bool 小写、None 空串、结构体 JSON）。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else to_text(value)


def _filter_md5(value: Any) -> str:
    """32 位小写 MD5（UTF-8 编码）。"""
    return hashlib.md5(_as_text(value).encode("utf-8")).hexdigest()


def _filter_sha256(value: Any) -> str:
    """64 位小写 SHA-256（UTF-8 编码）。"""
    return hashlib.sha256(_as_text(value).encode("utf-8")).hexdigest()


def _filter_base64(value: Any) -> str:
    """Base64 编码（UTF-8 原文 → ASCII 密文）。"""
    return base64.b64encode(_as_text(value).encode("utf-8")).decode("ascii")


def _filter_base64_decode(value: Any) -> str:
    """Base64 解码，非法输入按替换字符处理。"""
    return base64.b64decode(_as_text(value).encode("ascii")).decode("utf-8", "replace")


def _filter_url_encode(value: Any) -> str:
    """URL 百分号编码（连 ``/`` 也编码，适合直接拼进查询参数）。"""
    return urllib.parse.quote(_as_text(value), safe="")


def _filter_url_decode(value: Any) -> str:
    """URL 百分号解码。"""
    return urllib.parse.unquote(_as_text(value))


def _global_now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """当前本地时间，按 strftime 格式输出，如 ``{{ now('%Y%m%d') }}``。"""
    return datetime.now().strftime(str(fmt))


def _global_uuid() -> str:
    """随机 UUID4 字符串，如 ``{{ uuid() }}``。"""
    return str(uuid.uuid4())


#: 平台扩展过滤器（Jinja2 内置过滤器另行由引擎提供）
BUILTIN_FILTERS = {
    "md5": _filter_md5,
    "sha256": _filter_sha256,
    "base64": _filter_base64,
    "base64_encode": _filter_base64,
    "base64_decode": _filter_base64_decode,
    "url_encode": _filter_url_encode,
    "url_decode": _filter_url_decode,
}

#: 平台扩展全局函数
BUILTIN_GLOBALS = {
    "now": _global_now,
    "uuid": _global_uuid,
}
