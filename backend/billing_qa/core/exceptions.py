"""业务异常与统一错误响应。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """可直接暴露给前端的业务异常。"""

    def __init__(self, message: str, code: str = "BUSINESS_ERROR", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, code="NOT_FOUND", status_code=404)


class ConflictError(AppError):
    def __init__(self, message: str = "资源冲突"):
        super().__init__(message, code="CONFLICT", status_code=409)


class ValidationError(AppError):
    def __init__(self, message: str = "参数校验失败"):
        super().__init__(message, code="VALIDATION_ERROR", status_code=422)


class TargetApiError(AppError):
    """被测接口调用失败。

    retryable=True 表示环境类故障（连接失败、超时、5xx/429、响应无法解析等），
    调度器可以按任务配置自动重试；业务类错误（4xx 参数错误、配置缺失等）不可重试。
    """

    def __init__(self, message: str = "被测接口调用失败", *, retryable: bool = False):
        super().__init__(message, code="TARGET_API_ERROR", status_code=502)
        self.retryable = retryable
