"""权限依赖：默认拒绝，缺少权限返回 403。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status

from billing_qa.core.context import CurrentUser
from billing_qa.core.security import get_current_user

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="没有执行该操作的权限",
    )


def require_permission(code: str) -> Callable[..., Awaitable[CurrentUser]]:
    async def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if code not in user.permissions:
            raise _forbidden()
        return user

    return _checker


def require_any(*codes: str) -> Callable[..., Awaitable[CurrentUser]]:
    async def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.permissions.intersection(codes):
            raise _forbidden()
        return user

    return _checker


def require_module_access(
    read: str, write: tuple[str, ...]
) -> Callable[..., Awaitable[CurrentUser]]:
    """按 HTTP 方法做模块级粗粒度校验。

    GET/HEAD/OPTIONS 要求读权限；其余写方法要求任一写权限。
    更细的对象级校验仍在 service 内完成。
    """

    async def _checker(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if request.method in _READ_METHODS:
            if read not in user.permissions:
                raise _forbidden()
        elif not user.permissions.intersection(write):
            raise _forbidden()
        return user

    return _checker
