"""Prompt 模板引用计数维护。"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.models.prompt_template import PromptTemplate
from billing_qa.models.test_case import TestCase


async def refresh_template_refs(db: AsyncSession) -> None:
    """按当前用例引用关系重算 ref_count（软删除用例不计入）。"""
    templates = (await db.execute(select(PromptTemplate))).scalars().all()
    for template in templates:
        count = (
            await db.execute(
                select(func.count())
                .select_from(TestCase)
                .where(
                    TestCase.deleted_at.is_(None),
                    or_(
                        TestCase.simulator_template_id == template.id,
                        TestCase.judge_template_id == template.id,
                    ),
                )
            )
        ).scalar_one()
        template.ref_count = int(count or 0)
