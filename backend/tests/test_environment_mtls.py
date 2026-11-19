"""环境级 mTLS 双向证书：入库加密、运行时落盘与请求客户端配置。"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

import pytest
from pydantic import ValidationError

from billing_qa.api.v1.environments import _encode_mtls
from billing_qa.api.v1.serializers import env_to_out
from billing_qa.core.crypto import decrypt_value, encrypt_value
from billing_qa.core.exceptions import ValidationError as ConfigValidationError
from billing_qa.models.environment import Environment
from billing_qa.schemas.environment import MtlsAuthIn
from billing_qa.services.adapter.target_api import EnvContext, TargetApiAdapter

CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIBdemoCertificateBody
-----END CERTIFICATE-----
"""
KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIBdemoPrivateKeyBody
-----END PRIVATE KEY-----
"""
CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBdemoCaBody
-----END CERTIFICATE-----
"""


def _context(**overrides) -> EnvContext:
    base = {
        "id": 1,
        "base_url": "https://target.example.com",
        "health_path": "/health",
        "api_method": "POST",
        "api_path": "/api/chat",
        "headers_template": None,
        "body_template": None,
        "is_stream": False,
        "extractors": None,
        "pre_hook": None,
        "timeout_seconds": 10.0,
        "verify_ssl": True,
    }
    base.update(overrides)
    return EnvContext(**base)


def test_mtls_schema_validates_pem_and_allows_empty_for_update() -> None:
    config = MtlsAuthIn(
        client_cert_pem=CERT_PEM,
        client_key_pem=KEY_PEM,
        ca_cert_pem=CA_PEM,
    )
    assert config.client_cert_pem is not None
    assert config.ca_cert_pem is not None
    # 编辑时留空表示沿用旧值，不应触发格式校验
    assert MtlsAuthIn(client_cert_pem="   ").client_cert_pem is None

    with pytest.raises(ValidationError):
        MtlsAuthIn(client_cert_pem="not-a-cert")
    with pytest.raises(ValidationError):
        MtlsAuthIn(client_key_pem="not-a-key")


def test_encode_mtls_encrypts_and_reuses_existing_values() -> None:
    stored = _encode_mtls(
        MtlsAuthIn(
            client_cert_pem=CERT_PEM,
            client_key_pem=KEY_PEM,
            key_password="s3cret",
            ca_cert_pem=CA_PEM,
        ),
        None,
    )
    assert stored is not None
    assert decrypt_value(stored["client_cert_encrypted"]) == CERT_PEM.strip()
    assert decrypt_value(stored["client_key_encrypted"]) == KEY_PEM.strip()
    assert decrypt_value(stored["key_password_encrypted"]) == "s3cret"
    assert decrypt_value(stored["ca_cert_encrypted"]) == CA_PEM.strip()

    # 只改口令：证书/私钥沿用旧密文
    updated = _encode_mtls(MtlsAuthIn(key_password="new-secret"), stored)
    assert updated is not None
    assert updated["client_cert_encrypted"] == stored["client_cert_encrypted"]
    assert decrypt_value(updated["key_password_encrypted"]) == "new-secret"


def test_encode_mtls_requires_cert_and_key() -> None:
    with pytest.raises(ConfigValidationError):
        _encode_mtls(MtlsAuthIn(client_cert_pem=CERT_PEM), None)


def test_env_to_out_hides_secrets_without_permission() -> None:
    from datetime import datetime

    stamp = datetime.now()
    env = Environment(
        id=999,
        code="ENV-REVEAL",
        name="密钥回显",
        base_url="https://target.example.com",
        health_path="/health",
        api_method="POST",
        api_path="/api/chat",
        is_stream=False,
        session_mode="SERVER_RETURNED",
        timeout_seconds=30,
        verify_ssl=True,
        is_active=True,
        created_at=stamp,
        updated_at=stamp,
        basic_auth={
            "username": "demo",
            "password_encrypted": encrypt_value("s3cret"),
        },
        mtls={
            "client_cert_encrypted": encrypt_value(CERT_PEM),
            "client_key_encrypted": encrypt_value(KEY_PEM),
            "key_password_encrypted": encrypt_value("s3cret"),
            "ca_cert_encrypted": encrypt_value(CA_PEM),
        },
    )
    masked = env_to_out(env)
    assert masked.basic_auth is not None
    assert masked.basic_auth.password_set is True
    assert masked.basic_auth.password is None
    assert masked.mtls is not None
    assert masked.mtls.cert_set is True
    assert masked.mtls.client_cert_pem is None
    assert masked.mtls.client_key_pem is None
    assert masked.mtls.key_password is None
    assert masked.mtls.ca_cert_pem is None

    revealed = env_to_out(env, reveal=True)
    assert revealed.basic_auth is not None
    assert revealed.basic_auth.password == "s3cret"
    assert revealed.mtls is not None
    assert revealed.mtls.client_cert_pem == CERT_PEM
    assert revealed.mtls.client_key_pem == KEY_PEM
    assert revealed.mtls.key_password == "s3cret"
    assert revealed.mtls.ca_cert_pem == CA_PEM


def test_env_context_decrypts_mtls_material() -> None:
    env = Environment(
        code="ENV-MTLS",
        name="mtls 解密",
        base_url="https://target.example.com",
        mtls={
            "client_cert_encrypted": encrypt_value(CERT_PEM),
            "client_key_encrypted": encrypt_value(KEY_PEM),
            "key_password_encrypted": encrypt_value("s3cret"),
            "ca_cert_encrypted": encrypt_value(CA_PEM),
        },
    )
    ctx = EnvContext.from_orm(env)
    assert ctx.mtls_enabled is True
    assert ctx.mtls_client_cert == CERT_PEM
    assert ctx.mtls_client_key == KEY_PEM
    assert ctx.mtls_key_password == "s3cret"
    assert ctx.mtls_ca_cert == CA_PEM


def test_adapter_materializes_and_cleans_mtls_files() -> None:
    adapter = TargetApiAdapter(
        _context(
            mtls_client_cert=CERT_PEM,
            mtls_client_key=KEY_PEM,
            mtls_key_password=None,
            mtls_ca_cert=CA_PEM,
        )
    )
    try:
        files = adapter._prepare_mtls()
        assert files is not None
        cert_path, key_path, password, ca_path = files
        assert password is None
        assert Path(cert_path).read_text(encoding="utf-8") == CERT_PEM
        assert Path(key_path).read_text(encoding="utf-8") == KEY_PEM
        assert ca_path is not None
        assert Path(ca_path).read_text(encoding="utf-8") == CA_PEM
        if os.name != "nt":
            assert (Path(cert_path).stat().st_mode & 0o777) == 0o600
        # 同一适配器复用同一组临时文件
        assert adapter._prepare_mtls() == files
    finally:
        adapter.close()
    assert not Path(cert_path).exists()


def test_adapter_without_mtls_has_no_cert() -> None:
    adapter = TargetApiAdapter(_context())
    try:
        assert adapter._prepare_mtls() is None
    finally:
        adapter.close()


def test_client_uses_ssl_context_instead_of_deprecated_cert(monkeypatch) -> None:
    """回归：httpx 0.28 在 verify 为字符串时会忽略 cert，必须走 SSLContext。"""
    captured: dict = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    sentinel = ssl.create_default_context()
    monkeypatch.setattr(
        "billing_qa.services.adapter.target_api.httpx.AsyncClient", FakeAsyncClient
    )
    monkeypatch.setattr(
        TargetApiAdapter, "_build_ssl_context", lambda self, mtls: sentinel
    )
    adapter = TargetApiAdapter(
        _context(mtls_client_cert=CERT_PEM, mtls_client_key=KEY_PEM, verify_ssl=False)
    )
    try:
        adapter._client()
        assert "cert" not in captured
        assert captured["verify"] is sentinel
    finally:
        adapter.close()
