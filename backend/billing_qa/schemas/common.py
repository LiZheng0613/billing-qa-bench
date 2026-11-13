"""通用 Schema。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 20


class MessageResponse(BaseModel):
    message: str
    detail: str | None = None


class IdResponse(BaseModel):
    id: int


class KeyValue(BaseModel):
    key: str
    value: str


class ErrorBody(BaseModel):
    code: str = Field(examples=["BUSINESS_ERROR"])
    message: str


class PaginationQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
