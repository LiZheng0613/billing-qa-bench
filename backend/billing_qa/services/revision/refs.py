"""Prompt 模板引用计数维护。"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from billing_qa.models.prompt_template import PromptTemplate
from billing_qa.models.test_case import TestCase


async def refresh_template_refs(db: AsyncSession) -> None:
    """按当前用例引用关系重算 ref_count（软删除用例不计入）。

    单条分组查询替代「每个模板一条 COUNT」的 N+1：按 (simulator_template_id,
    judge_template_id) 分组计数，模板出现在任一位置即计入；两个位置指向
    同一模板的用例只计一次（集合去重），与原 ``or_`` 语义一致。
    """
    rows = (
        await db.execute(
            select(
                TestCase.simulator_template_id,
                TestCase.judge_template_id,
                func.count(TestCase.id),
            )
            .where(
                TestCase.deleted_at.is_(None),
                or_(
                    TestCase.simulator_template_id.is_not(None),
                    TestCase.judge_template_id.is_not(None),
                ),
            )
            .group_by(TestCase.simulator_template_id, TestCase.judge_template_id)
        )
    ).all()
    totals: dict[int, int] = {}
    for sim_id, judge_id, count in rows:
        for template_id in {sim_id, judge_id}:
            if template_id is not None:
                totals[int(template_id)] = totals.get(int(template_id), 0) + int(count)
    # 只需要 id 与写入 ref_count；noload 避免 selectin 把每个模板全部
    # 历史版本的 content（每份数 KB）拉进内存——该函数挂在所有用例写路径上。
    templates = (
        await db.execute(select(PromptTemplate).options(noload(PromptTemplate.versions)))
    ).scalars().all()
    for template in templates:
        template.ref_count = totals.get(int(template.id), 0)
