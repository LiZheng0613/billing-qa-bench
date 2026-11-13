"""执行错误分类：区分环境类故障与业务失败。

环境类故障（ENV）指与被测服务/网关相关的瞬时问题：
连接拒绝、超时、HTTP 429/5xx、SSE 空响应、网关 HTML 错误页、鉴权服务不可达等。
这类错误可以按任务配置自动重试。

业务失败（BUSINESS）指用例内容、规则校验、裁判结论或平台配置问题：
规则不通过、裁判低分、轮次超限、复读熔断、模板变量缺失、非法用例类型等。
这类错误不重试，避免掩盖真实缺陷。
"""

from __future__ import annotations

from billing_qa.core.exceptions import TargetApiError

ERROR_KIND_ENV = "ENV"
ERROR_KIND_BUSINESS = "BUSINESS"
RETRY_MAX_DELAY_SECONDS = 60.0


def classify_exception(exc: BaseException) -> str:
    """把执行期异常归类为 ENV 或 BUSINESS。"""
    if isinstance(exc, TargetApiError) and exc.retryable:
        return ERROR_KIND_ENV
    return ERROR_KIND_BUSINESS


def is_env_error(exc: BaseException) -> bool:
    return classify_exception(exc) == ERROR_KIND_ENV


def retry_delay(failed_attempts: int, base_seconds: float) -> float:
    """计算第 failed_attempts 次失败后的退避时长（指数递增，封顶 60 秒）。

    failed_attempts=1 表示首次失败，等待 base_seconds；之后按 2 倍递增。
    """
    exponent = max(0, int(failed_attempts) - 1)
    delay = max(0.0, float(base_seconds)) * (2**exponent)
    return min(delay, RETRY_MAX_DELAY_SECONDS)
