"""测试用例管理：CRUD、版本审计、Diff/回滚、导入导出、历史执行与对比。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.api.v1.serializers import build_execution_detail, case_to_out
from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.core.security import get_current_operator
from billing_qa.db.session import get_db
from billing_qa.models.enums import (
    CaseType,
    ModelRole,
    RevisionChangeType,
    TemplateStrategy,
    TemplateType,
)
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.model_config import ModelConfig
from billing_qa.models.prompt_template import PromptTemplate
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase, TestCaseRevision
from billing_qa.schemas.case import (
    CaseCreate,
    CaseDebugRequest,
    CaseExecutionItem,
    CaseOut,
    CaseTrend,
    CaseUpdate,
    DiffChange,
    ImportResult,
    ImportRowError,
    RevisionDiff,
    RevisionOut,
    RollbackRequest,
    RunCompare,
    RunCompareSide,
    TrendPoint,
)
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.task import ExecutionDetail
from billing_qa.services.engine.executor import CaseExecutor
from billing_qa.services.io.excel import (
    EXCEL_COLUMNS,
    export_cases_xlsx,
    parse_excel,
    parse_json,
)
from billing_qa.services.revision.refs import refresh_template_refs
from billing_qa.services.revision.snapshot import (
    SNAPSHOT_FIELDS,
    build_snapshot,
    diff_snapshots,
    summarize_changes,
)

router = APIRouter(
    prefix="/cases", tags=["测试用例"], dependencies=[Depends(get_current_operator)]
)


# ---------------------------------------------------------------------- #
# 基础工具
# ---------------------------------------------------------------------- #
async def _get_case(db: AsyncSession, case_id: int, *, include_deleted: bool = False) -> TestCase:
    case = await db.get(TestCase, case_id)
    if case is None or (case.deleted_at is not None and not include_deleted):
        raise NotFoundError("测试用例不存在或已删除")
    return case


async def _validate_references(db: AsyncSession, data: dict[str, Any]) -> None:
    environment_id = data.get("environment_id")
    if environment_id:
        env = await db.get(Environment, environment_id)
        if env is None:
            raise ValidationError("指定的执行环境不存在")
    simulator_id = data.get("simulator_template_id")
    if simulator_id:
        template = await db.get(PromptTemplate, simulator_id)
        if template is None or template.template_type != TemplateType.USER_SIMULATOR:
            raise ValidationError("模拟器模板不存在或类型不匹配")
    judge_id = data.get("judge_template_id")
    if judge_id:
        template = await db.get(PromptTemplate, judge_id)
        if template is None or template.template_type != TemplateType.LLM_JUDGE:
            raise ValidationError("裁判模板不存在或类型不匹配")
    simulator_model_id = data.get("simulator_model_id")
    if simulator_model_id:
        model = await db.get(ModelConfig, simulator_model_id)
        if model is None or model.role != ModelRole.SIMULATOR:
            raise ValidationError("模拟器模型不存在或角色不匹配")
    for model_id in data.get("judge_model_ids") or []:
        model = await db.get(ModelConfig, int(model_id))
        if model is None or model.role != ModelRole.JUDGE:
            raise ValidationError(f"裁判模型 {model_id} 不存在或角色不匹配")


def _validate_strategy(data: dict[str, Any]) -> None:
    for strategy_key, version_key, label in (
        ("simulator_template_strategy", "simulator_template_version", "模拟器模板"),
        ("judge_template_strategy", "judge_template_version", "裁判模板"),
    ):
        strategy = data.get(strategy_key)
        if strategy == TemplateStrategy.PINNED and not data.get(version_key):
            raise ValidationError(f"{label}选择 PINNED 策略时必须指定版本号")


async def _add_revision(
    db: AsyncSession,
    case: TestCase,
    change_type: str,
    operator: str,
    summary: str | None,
) -> TestCaseRevision:
    revision = TestCaseRevision(
        case_id=case.id,
        version=case.current_version,
        change_type=change_type,
        operator=operator,
        summary=summary,
        snapshot=build_snapshot(case),
    )
    db.add(revision)
    return revision


async def _enrich_cases(db: AsyncSession, cases: list[TestCase]) -> list[CaseOut]:
    env_ids = {case.environment_id for case in cases if case.environment_id}
    template_ids: set[int] = set()
    for case in cases:
        if case.simulator_template_id:
            template_ids.add(case.simulator_template_id)
        if case.judge_template_id:
            template_ids.add(case.judge_template_id)
    env_map: dict[int, str] = {}
    if env_ids:
        rows = (
            await db.execute(select(Environment.id, Environment.name).where(Environment.id.in_(env_ids)))
        ).all()
        env_map = {row[0]: row[1] for row in rows}
    template_map: dict[int, str] = {}
    if template_ids:
        rows = (
            await db.execute(
                select(PromptTemplate.id, PromptTemplate.code).where(PromptTemplate.id.in_(template_ids))
            )
        ).all()
        template_map = {row[0]: row[1] for row in rows}
    return [
        case_to_out(
            case,
            env_name=env_map.get(case.environment_id or 0),
            simulator_code=template_map.get(case.simulator_template_id or 0),
            judge_code=template_map.get(case.judge_template_id or 0),
        )
        for case in cases
    ]


# ---------------------------------------------------------------------- #
# CRUD
# ---------------------------------------------------------------------- #
@router.get("", response_model=Page[CaseOut], summary="用例列表")
async def list_cases(
    case_type: CaseType | None = None,
    status: str | None = None,
    tag: str | None = None,
    environment_id: int | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[CaseOut]:
    conditions = [TestCase.deleted_at.is_(None)]
    if case_type:
        conditions.append(TestCase.case_type == case_type)
    if status:
        conditions.append(TestCase.status == status)
    if environment_id:
        conditions.append(TestCase.environment_id == environment_id)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            TestCase.name.ilike(like)
            | TestCase.code.ilike(like)
            | TestCase.description.ilike(like)
        )
    if tag:
        # tags 为 JSON 数组：PostgreSQL 下用 JSONB 包含判断，
        # 避免依赖 json::text 的转义形式（中文在库里存为 \uXXXX，文本 LIKE 匹配不到）
        conditions.append(cast(TestCase.tags, JSONB).contains([tag]))
    total = (
        await db.execute(select(func.count()).select_from(TestCase).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(TestCase)
            .where(*conditions)
            .order_by(TestCase.updated_at.desc(), TestCase.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[CaseOut](
        items=await _enrich_cases(db, list(rows)),
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=CaseOut, summary="创建用例")
async def create_case(
    payload: CaseCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> CaseOut:
    exists = (
        await db.execute(select(TestCase.id).where(TestCase.code == payload.code))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"用例编号 {payload.code} 已存在")
    data = payload.model_dump()
    await _validate_references(db, data)
    _validate_strategy(data)
    case = TestCase(**data)
    case.current_version = 1
    case.created_by = operator
    case.updated_by = operator
    db.add(case)
    await db.flush()
    await _add_revision(db, case, RevisionChangeType.CREATE, operator, "创建用例")
    await refresh_template_refs(db)
    await db.commit()
    enriched = await _enrich_cases(db, [case])
    return enriched[0]


@router.get("/export", summary="导出用例（JSON / Excel）")
async def export_cases(
    format: str = Query(default="json", pattern="^(json|xlsx)$"),
    case_type: CaseType | None = None,
    ids: str | None = Query(default=None, description="逗号分隔的用例 id"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    conditions = [TestCase.deleted_at.is_(None)]
    if case_type:
        conditions.append(TestCase.case_type == case_type)
    if ids:
        id_list = [int(item) for item in ids.split(",") if item.strip().isdigit()]
        if id_list:
            conditions.append(TestCase.id.in_(id_list))
    cases = (
        await db.execute(select(TestCase).where(*conditions).order_by(TestCase.id.asc()))
    ).scalars().all()

    env_map: dict[int, str] = {}
    env_rows = (await db.execute(select(Environment.id, Environment.code))).all()
    env_map = {row[0]: row[1] for row in env_rows}
    template_map: dict[int, str] = {}
    template_rows = (await db.execute(select(PromptTemplate.id, PromptTemplate.code))).all()
    template_map = {row[0]: row[1] for row in template_rows}

    rows: list[dict[str, Any]] = []
    for case in cases:
        item: dict[str, Any] = {
            key: getattr(case, key, None) for key, _ in EXCEL_COLUMNS if key in TestCase.__table__.columns
        }
        item["environment_code"] = env_map.get(case.environment_id or 0)
        item["simulator_template_code"] = template_map.get(case.simulator_template_id or 0)
        item["judge_template_code"] = template_map.get(case.judge_template_id or 0)
        item["tags"] = ",".join(case.tags or [])
        rows.append(item)

    if format == "xlsx":
        content = export_cases_xlsx(rows)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="test_cases.xlsx"'},
        )
    body = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="test_cases.json"'},
    )


@router.post("/import", response_model=ImportResult, summary="批量导入用例")
async def import_cases(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> ImportResult:
    raw = await file.read()
    filename = (file.filename or "").lower()
    if filename.endswith((".xlsx", ".xlsm")):
        records = parse_excel(raw)
    elif filename.endswith(".json"):
        records = parse_json(raw)
    else:
        raise ValidationError("仅支持 .xlsx / .json 文件")

    env_map = {
        code: env_id
        for env_id, code in (await db.execute(select(Environment.id, Environment.code))).all()
    }
    template_map = {
        code: template_id
        for template_id, code in (
            await db.execute(select(PromptTemplate.id, PromptTemplate.code))
        ).all()
    }

    created = 0
    updated = 0
    errors: list[ImportRowError] = []
    imported_ids: list[int] = []

    for record in records:
        row_no = int(record.pop("__row__", 0) or 0)
        savepoint = await db.begin_nested()
        try:
            record.pop("id", None)
            if record.get("environment_code"):
                env_code = str(record.pop("environment_code"))
                env_id = env_map.get(env_code)
                if env_id is None:
                    raise ValidationError(f"环境编码不存在: {env_code}")
                record["environment_id"] = env_id
            if record.get("simulator_template_code"):
                record["simulator_template_id"] = template_map.get(
                    str(record.pop("simulator_template_code"))
                )
            if record.get("judge_template_code"):
                record["judge_template_id"] = template_map.get(
                    str(record.pop("judge_template_code"))
                )
            record.setdefault("status", "ACTIVE")
            record.setdefault("priority", "P1")
            existing = None
            if record.get("code"):
                existing = (
                    await db.execute(
                        select(TestCase).where(
                            TestCase.code == record["code"], TestCase.deleted_at.is_(None)
                        )
                    )
                ).scalar_one_or_none()
            if existing is None:
                case_payload = CaseCreate(**record)
                data = case_payload.model_dump()
                await _validate_references(db, data)
                _validate_strategy(data)
                case = TestCase(**data)
                case.current_version = 1
                case.created_by = operator
                case.updated_by = operator
                db.add(case)
                await db.flush()
                await _add_revision(
                    db, case, RevisionChangeType.IMPORT, operator, "批量导入创建"
                )
                await db.flush()
                imported_ids.append(int(case.id))
                created += 1
            else:
                update_payload = CaseUpdate(**{k: v for k, v in record.items() if k != "code"})
                updates = update_payload.model_dump(exclude_unset=True)
                updates.pop("change_summary", None)
                await _validate_references(db, updates)
                _validate_strategy(updates)
                for key, value in updates.items():
                    setattr(existing, key, value)
                existing.updated_by = operator
                existing.current_version += 1
                await db.flush()
                await _add_revision(
                    db, existing, RevisionChangeType.IMPORT, operator, "批量导入更新"
                )
                await db.flush()
                imported_ids.append(int(existing.id))
                updated += 1
            await savepoint.commit()
        except Exception as exc:
            await savepoint.rollback()
            errors.append(ImportRowError(row=row_no, message=f"{type(exc).__name__}: {exc}"))
    await refresh_template_refs(db)
    await db.commit()
    return ImportResult(
        total=len(records),
        created=created,
        updated=updated,
        failed=len(errors),
        errors=errors,
        case_ids=imported_ids,
    )


@router.get("/{case_id}", response_model=CaseOut, summary="用例详情")
async def get_case(case_id: int, db: AsyncSession = Depends(get_db)) -> CaseOut:
    case = await _get_case(db, case_id)
    enriched = await _enrich_cases(db, [case])
    return enriched[0]


@router.put("/{case_id}", response_model=CaseOut, summary="编辑用例（自动生成版本快照）")
async def update_case(
    case_id: int,
    payload: CaseUpdate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> CaseOut:
    case = await _get_case(db, case_id)
    updates = payload.model_dump(exclude_unset=True)
    summary = updates.pop("change_summary", None)
    if not updates:
        enriched = await _enrich_cases(db, [case])
        return enriched[0]
    await _validate_references(db, updates)
    merged = {field: getattr(case, field) for field in SNAPSHOT_FIELDS}
    merged.update(updates)
    _validate_strategy(merged)

    old_snapshot = build_snapshot(case)
    for key, value in updates.items():
        setattr(case, key, value)
    case.updated_by = operator
    case.current_version += 1
    new_snapshot = build_snapshot(case)
    changes = diff_snapshots(old_snapshot, new_snapshot)
    await db.flush()
    await _add_revision(
        db,
        case,
        RevisionChangeType.UPDATE,
        operator,
        summary or summarize_changes(changes),
    )
    await refresh_template_refs(db)
    await db.commit()
    enriched = await _enrich_cases(db, [case])
    return enriched[0]


@router.delete("/{case_id}", response_model=MessageResponse, summary="删除用例（软删除）")
async def delete_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> MessageResponse:
    case = await _get_case(db, case_id)
    case.deleted_at = __import__("datetime").datetime.now()
    case.status = "DISABLED"
    case.updated_by = operator
    await refresh_template_refs(db)
    await db.commit()
    return MessageResponse(message=f"用例 {case.code} 已删除")


# ---------------------------------------------------------------------- #
# 变更审计
# ---------------------------------------------------------------------- #
@router.get(
    "/{case_id}/revisions", response_model=list[RevisionOut], summary="用例修改记录"
)
async def list_revisions(
    case_id: int,
    include_snapshot: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[RevisionOut]:
    await _get_case(db, case_id, include_deleted=True)
    rows = (
        await db.execute(
            select(TestCaseRevision)
            .where(TestCaseRevision.case_id == case_id)
            .order_by(TestCaseRevision.version.desc())
        )
    ).scalars().all()
    items: list[RevisionOut] = []
    for row in rows:
        item = RevisionOut.model_validate(row)
        item.snapshot = row.snapshot if include_snapshot else None
        items.append(item)
    return items


@router.get(
    "/{case_id}/revisions/diff", response_model=RevisionDiff, summary="版本字段级 Diff"
)
async def diff_revisions(
    case_id: int,
    from_version: int = Query(ge=1),
    to_version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
) -> RevisionDiff:
    await _get_case(db, case_id, include_deleted=True)
    rows = (
        await db.execute(
            select(TestCaseRevision).where(
                TestCaseRevision.case_id == case_id,
                TestCaseRevision.version.in_([from_version, to_version]),
            )
        )
    ).scalars().all()
    mapping = {row.version: row for row in rows}
    left = mapping.get(from_version)
    right = mapping.get(to_version)
    if left is None or right is None:
        raise NotFoundError("指定版本快照不存在")
    changes = diff_snapshots(left.snapshot, right.snapshot)
    return RevisionDiff(
        case_id=case_id,
        from_version=from_version,
        to_version=to_version,
        changes=[DiffChange(**item) for item in changes],
        total=len(changes),
    )


@router.post("/{case_id}/rollback", response_model=CaseOut, summary="回滚至指定版本")
async def rollback_case(
    case_id: int,
    payload: RollbackRequest,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> CaseOut:
    case = await _get_case(db, case_id)
    target = (
        await db.execute(
            select(TestCaseRevision).where(
                TestCaseRevision.case_id == case_id,
                TestCaseRevision.version == payload.version,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError(f"版本 v{payload.version} 不存在")
    snapshot = target.snapshot or {}
    for field in SNAPSHOT_FIELDS:
        if field in snapshot:
            setattr(case, field, snapshot[field])
    case.updated_by = operator
    case.current_version += 1
    await db.flush()
    await _add_revision(
        db,
        case,
        RevisionChangeType.ROLLBACK,
        operator,
        payload.summary or f"回滚至 v{payload.version}",
    )
    await refresh_template_refs(db)
    await db.commit()
    enriched = await _enrich_cases(db, [case])
    return enriched[0]


# ---------------------------------------------------------------------- #
# 历史执行与趋势
# ---------------------------------------------------------------------- #
@router.get(
    "/{case_id}/executions",
    response_model=Page[CaseExecutionItem],
    summary="用例历史执行记录",
)
async def case_executions(
    case_id: int,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[CaseExecutionItem]:
    await _get_case(db, case_id, include_deleted=True)
    conditions = [ExecutionRecord.case_id == case_id]
    if status:
        conditions.append(ExecutionRecord.status == status)
    total = (
        await db.execute(select(func.count()).select_from(ExecutionRecord).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(ExecutionRecord, BatchTask.name)
            .outerjoin(BatchTask, BatchTask.id == ExecutionRecord.task_id)
            .where(*conditions)
            .order_by(ExecutionRecord.started_at.desc(), ExecutionRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items: list[CaseExecutionItem] = []
    for record, task_name in rows:
        items.append(
            CaseExecutionItem(
                id=record.id,
                task_id=record.task_id,
                task_name=task_name,
                source=record.source,
                status=record.status,
                attempt_no=record.attempt_no,
                error_kind=record.error_kind,
                pass_flag=record.pass_flag,
                total_score=record.total_score,
                case_version=record.case_version,
                turns_used=record.turns_used,
                latency_ms=record.latency_ms,
                ttft_ms=record.ttft_ms,
                started_at=record.started_at,
                finished_at=record.finished_at,
            )
        )
    return Page[CaseExecutionItem](
        items=items, total=int(total), page=page, page_size=page_size
    )


@router.get("/{case_id}/trend", response_model=CaseTrend, summary="用例近 20 次执行趋势")
async def case_trend(case_id: int, db: AsyncSession = Depends(get_db)) -> CaseTrend:
    await _get_case(db, case_id, include_deleted=True)
    rows = (
        await db.execute(
            select(ExecutionRecord)
            .where(ExecutionRecord.case_id == case_id)
            .order_by(ExecutionRecord.id.desc())
            .limit(20)
        )
    ).scalars().all()
    ordered = list(reversed(rows))
    points = [
        TrendPoint(
            execution_id=record.id,
            started_at=record.started_at,
            total_score=record.total_score,
            latency_ms=record.latency_ms,
            status=record.status,
            pass_flag=record.pass_flag,
            turns_used=record.turns_used,
        )
        for record in ordered
    ]
    scored = [record for record in ordered if record.pass_flag is not None]
    pass_rate = (
        round(sum(1 for record in scored if record.pass_flag) / len(scored) * 100, 2)
        if scored
        else 0.0
    )
    scores = [float(record.total_score) for record in ordered if record.total_score is not None]
    latencies = [float(record.latency_ms) for record in ordered if record.latency_ms is not None]
    flaky = len(scored) >= 3 and 0 < pass_rate < 100
    return CaseTrend(
        case_id=case_id,
        points=points,
        pass_rate=pass_rate,
        avg_score=round(sum(scores) / len(scores), 2) if scores else 0.0,
        avg_latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        flaky=flaky,
    )


@router.get("/compare/runs", response_model=RunCompare, summary="两次历史执行横向对比")
async def compare_runs(
    run_a: int = Query(description="基线执行 id"),
    run_b: int = Query(description="对比执行 id"),
    db: AsyncSession = Depends(get_db),
) -> RunCompare:
    records = (
        await db.execute(
            select(ExecutionRecord).where(ExecutionRecord.id.in_([run_a, run_b]))
        )
    ).scalars().all()
    mapping = {record.id: record for record in records}
    left = mapping.get(run_a)
    right = mapping.get(run_b)
    if left is None or right is None:
        raise NotFoundError("执行记录不存在")
    if left.case_id != right.case_id:
        raise ValidationError("只能对比同一条用例的执行记录")

    def _side(record: ExecutionRecord) -> RunCompareSide:
        last_turn = record.turns[-1] if record.turns else None
        sql = next(
            (turn.generated_sql for turn in reversed(record.turns) if turn.generated_sql), None
        )
        return RunCompareSide(
            execution_id=record.id,
            case_version=record.case_version,
            status=record.status,
            total_score=record.total_score,
            pass_flag=record.pass_flag,
            started_at=record.started_at,
            final_answer=last_turn.final_answer if last_turn else None,
            generated_sql=sql,
            raw_db_data=last_turn.raw_db_data if last_turn else None,
            judge_reason=(record.judge_result or {}).get("reason"),
            turns=[
                {
                    "turn_index": turn.turn_index,
                    "query": turn.query,
                    "final_answer": turn.final_answer,
                    "generated_sql": turn.generated_sql,
                    "raw_db_data": turn.raw_db_data,
                    "rule_check": turn.rule_check,
                }
                for turn in record.turns
            ],
        )

    left_side = _side(left)
    right_side = _side(right)
    diffs: list[DiffChange] = []

    def _cmp(path: str, label: str, old: Any, new: Any) -> None:
        if old != new:
            diffs.append(
                DiffChange(
                    path=path,
                    label=label,
                    old=old,
                    new=new,
                    change_type="modified" if old is not None and new is not None else "added",
                )
            )

    _cmp("case_version", "用例版本", left_side.case_version, right_side.case_version)
    _cmp("status", "执行状态", left_side.status, right_side.status)
    _cmp("total_score", "总分", left_side.total_score, right_side.total_score)
    _cmp("final_answer", "最终回复", left_side.final_answer, right_side.final_answer)
    _cmp("generated_sql", "生成 SQL", left_side.generated_sql, right_side.generated_sql)
    _cmp("raw_db_data", "底层取数", left_side.raw_db_data, right_side.raw_db_data)
    _cmp("judge_reason", "裁判理由", left_side.judge_reason, right_side.judge_reason)

    return RunCompare(
        case_id=left.case_id, left=left_side, right=right_side, diffs=diffs
    )


@router.post("/{case_id}/debug", response_model=ExecutionDetail, summary="单条用例调试执行")
async def debug_case(
    case_id: int,
    payload: CaseDebugRequest,
    db: AsyncSession = Depends(get_db),
) -> ExecutionDetail:
    case = await _get_case(db, case_id)
    revision = (
        await db.execute(
            select(TestCaseRevision).where(
                TestCaseRevision.case_id == case.id,
                TestCaseRevision.version == case.current_version,
            )
        )
    ).scalar_one_or_none()
    snapshot = revision.snapshot if revision is not None else build_snapshot(case)
    if payload.case_snapshot_override:
        snapshot = {**snapshot, **payload.case_snapshot_override}
    executor = CaseExecutor()
    # 先结束当前事务，避免旧快照看不到执行器新写入的记录
    await db.commit()
    execution_id = await executor.execute(
        case_id=case.id,
        case_version=case.current_version,
        snapshot=snapshot,
        source="DEBUG",
        environment_id=payload.environment_id or case.environment_id,
    )
    record = await db.get(ExecutionRecord, execution_id)
    if record is None:
        raise NotFoundError("调试执行记录生成失败")
    return await build_execution_detail(
        db, record, case_code=case.code, case_name=case.name
    )
