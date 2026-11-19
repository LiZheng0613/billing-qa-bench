"""执行错误分类与重试退避策略单元测试。"""

from __future__ import annotations

import pytest

from billing_qa.core.exceptions import TargetApiError, ValidationError
from billing_qa.services.adapter.target_api import is_retryable_status
from billing_qa.services.engine.error_policy import (
    ERROR_KIND_BUSINESS,
    ERROR_KIND_ENV,
    ERROR_KIND_LLM,
    RETRY_MAX_DELAY_SECONDS,
    classify_exception,
    is_env_error,
    retry_delay,
)
from billing_qa.services.llm.client import LLMError


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (400, False),
        (401, False),
        (404, False),
        (422, False),
        (200, False),
    ],
)
def test_retryable_status(status_code: int, expected: bool) -> None:
    assert is_retryable_status(status_code) is expected


def test_env_error_classification() -> None:
    exc = TargetApiError("被测接口超时", retryable=True)
    assert classify_exception(exc) == ERROR_KIND_ENV
    assert is_env_error(exc) is True


def test_business_error_classification() -> None:
    assert classify_exception(TargetApiError("请求模板变量缺失")) == ERROR_KIND_BUSINESS
    assert classify_exception(ValidationError("用例类型非法")) == ERROR_KIND_BUSINESS
    assert classify_exception(RuntimeError("unexpected")) == ERROR_KIND_BUSINESS
    assert is_env_error(TargetApiError("HTTP 400")) is False


def test_llm_error_classification() -> None:
    assert classify_exception(LLMError("模型调用失败")) == ERROR_KIND_LLM
    assert classify_exception(LLMError("裁判输出非法 JSON")) == ERROR_KIND_LLM
    assert is_env_error(LLMError("模型调用失败")) is False


@pytest.mark.parametrize(
    ("failed_attempts", "expected"),
    [
        (1, 3.0),
        (2, 6.0),
        (3, 12.0),
        (4, 24.0),
        (10, RETRY_MAX_DELAY_SECONDS),
    ],
)
def test_retry_delay_exponential_backoff(failed_attempts: int, expected: float) -> None:
    assert retry_delay(failed_attempts, 3.0) == expected


def test_retry_delay_handles_zero_base() -> None:
    assert retry_delay(3, 0.0) == 0.0
