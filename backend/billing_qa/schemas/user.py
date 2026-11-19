"""用户管理 Schema。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class RoleBrief(BaseModel):
    id: int
    code: str
    name: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    email: str | None = None
    phone: str | None = None
    status: str
    roles: list[RoleBrief] = Field(default_factory=list)
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    must_change_password: bool = False


class UserDetail(UserOut):
    last_login_ip: str | None = None
    created_by: int | None = None
    updated_at: datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    password: str = Field(min_length=8, max_length=72)
    role_ids: list[int] = Field(default_factory=list)

    @field_validator("username")
    @classmethod
    def _username_valid(cls, value: str) -> str:
        value = value.strip()
        if not _USERNAME_RE.match(value):
            raise ValueError("用户名只能包含字母、数字、下划线、点和短横线，长度 3–32")
        return value


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    status: Literal["ACTIVE", "DISABLED", "LOCKED"] | None = None


class RoleAssignment(BaseModel):
    role_ids: list[int] = Field(default_factory=list)


class ResetPasswordOut(BaseModel):
    temp_password: str
    message: str = "临时密码仅展示一次，请要求用户首次登录后修改"
