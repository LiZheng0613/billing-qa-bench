"""角色与权限 Schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PermissionOut(BaseModel):
    id: int
    code: str
    name: str
    module: str
    action: str
    description: str | None = None
    sort: int = 0


class RoleOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None = None
    data_scope: str = "ALL"
    is_system: bool = False
    status: str = "ACTIVE"
    user_count: int = 0
    permission_count: int = 0


class RoleDetail(RoleOut):
    permission_codes: list[str] = Field(default_factory=list)


class RoleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    data_scope: str = "ALL"
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    data_scope: str | None = None
    status: str | None = None


class RolePermissionsPayload(BaseModel):
    permission_codes: list[str] = Field(default_factory=list)
