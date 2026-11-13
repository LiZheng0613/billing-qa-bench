"""对称加密与敏感字段掩码。"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from billing_qa.core.config import settings


def _build_fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


_fernet = _build_fernet()


def encrypt_value(raw: str | None) -> str | None:
    """加密明文；空值原样返回。"""
    if raw is None or raw == "":
        return raw
    return _fernet.encrypt(raw.encode("utf-8")).decode("utf-8")


def decrypt_value(token: str | None) -> str | None:
    """解密；若历史数据是非密文（例如手工写入）则原样返回，避免阻断业务。"""
    if token is None or token == "":
        return token
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return token


def mask_value(raw: str | None, keep: int = 4) -> str:
    """掩码展示：保留尾部 keep 位。"""
    if not raw:
        return ""
    text = str(raw)
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * min(8, len(text) - keep) + text[-keep:]
