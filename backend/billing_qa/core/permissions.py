"""权限目录与内置角色默认权限。

权限目录由代码维护，数据库只保存目录副本和角色绑定关系。
新增权限码后，``sync_auth_seed`` 会补进数据库并自动授予 SYSTEM_ADMIN。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PermissionCode(StrEnum):
    DASHBOARD_READ = "dashboard:read"

    CASE_READ = "case:read"
    CASE_CREATE = "case:create"
    CASE_UPDATE = "case:update"
    CASE_DELETE = "case:delete"
    CASE_IMPORT = "case:import"
    CASE_EXPORT = "case:export"
    CASE_REVIEW = "case:review"
    CASE_FREEZE = "case:freeze"
    CASE_ROLLBACK = "case:rollback"
    CASE_DEBUG = "case:debug"

    TASK_READ = "task:read"
    TASK_CREATE = "task:create"
    TASK_STOP = "task:stop"
    TASK_DELETE = "task:delete"
    TASK_EXPORT = "task:export"

    EXECUTION_READ = "execution:read"
    EXECUTION_RETRY = "execution:retry"
    EXECUTION_EXPORT = "execution:export"

    PROMPT_READ = "prompt:read"
    PROMPT_WRITE = "prompt:write"
    PROMPT_ROLLBACK = "prompt:rollback"

    ENVIRONMENT_READ = "environment:read"
    ENVIRONMENT_WRITE = "environment:write"
    ENVIRONMENT_SECRET_READ = "environment:secret_read"

    MODEL_READ = "model:read"
    MODEL_WRITE = "model:write"
    MODEL_TEST = "model:test"
    MODEL_SECRET_READ = "model:secret_read"

    CAPABILITY_READ = "capability:read"
    CAPABILITY_WRITE = "capability:write"
    CAPABILITY_EXECUTE = "capability:execute"

    CALIBER_READ = "caliber:read"
    CALIBER_WRITE = "caliber:write"

    CANDIDATE_READ = "candidate:read"
    CANDIDATE_TRIAGE = "candidate:triage"
    CANDIDATE_PROMOTE = "candidate:promote"

    GRADER_RUN = "grader:run"
    STATS_READ = "stats:read"

    USER_READ = "user:read"
    USER_WRITE = "user:write"
    USER_RESET_PASSWORD = "user:reset_password"

    ROLE_READ = "role:read"
    ROLE_WRITE = "role:write"
    ROLE_ASSIGN = "role:assign"

    AUDIT_READ = "audit:read"


@dataclass(frozen=True, slots=True)
class PermissionDef:
    code: str
    name: str
    module: str
    action: str
    description: str
    sort: int


def _p(
    code: PermissionCode,
    name: str,
    module: str,
    action: str,
    description: str,
    sort: int,
) -> PermissionDef:
    return PermissionDef(code.value, name, module, action, description, sort)


PERMISSION_CATALOG: tuple[PermissionDef, ...] = (
    _p(PermissionCode.DASHBOARD_READ, "查看测试大盘", "dashboard", "read", "全局看板与统计概览", 10),
    _p(PermissionCode.CASE_READ, "查看用例", "case", "read", "查看用例资产与版本", 100),
    _p(PermissionCode.CASE_CREATE, "新建用例", "case", "create", "创建单条或批量用例", 101),
    _p(PermissionCode.CASE_UPDATE, "编辑用例", "case", "update", "编辑草稿，自动生成版本", 102),
    _p(PermissionCode.CASE_DELETE, "删除用例", "case", "delete", "仅允许删除草稿", 103),
    _p(PermissionCode.CASE_IMPORT, "导入用例", "case", "import", "从 JSON 批量导入", 104),
    _p(PermissionCode.CASE_EXPORT, "导出用例", "case", "export", "导出版本信封", 105),
    _p(PermissionCode.CASE_REVIEW, "审核用例", "case", "review", "审核通过或驳回，不能自审", 106),
    _p(PermissionCode.CASE_FREEZE, "冻结用例", "case", "freeze", "冻结版本并锁定内容", 107),
    _p(PermissionCode.CASE_ROLLBACK, "回滚用例", "case", "rollback", "回滚到指定历史版本", 108),
    _p(PermissionCode.CASE_DEBUG, "调试用例", "case", "debug", "在沙盒环境执行单条用例", 109),
    _p(PermissionCode.TASK_READ, "查看任务", "task", "read", "查看批量任务与进度", 200),
    _p(PermissionCode.TASK_CREATE, "创建任务", "task", "create", "基于已发布用例创建任务", 201),
    _p(PermissionCode.TASK_STOP, "停止任务", "task", "stop", "中止或停止计划", 202),
    _p(PermissionCode.TASK_DELETE, "删除任务", "task", "delete", "删除任务及其记录", 203),
    _p(PermissionCode.TASK_EXPORT, "导出任务", "task", "export", "导出任务执行结果", 204),
    _p(PermissionCode.EXECUTION_READ, "查看执行结果", "execution", "read", "查看执行追踪与 Trace", 300),
    _p(PermissionCode.EXECUTION_RETRY, "重试执行", "execution", "retry", "重试失败用例", 301),
    _p(PermissionCode.EXECUTION_EXPORT, "导出执行结果", "execution", "export", "导出执行明细", 302),
    _p(PermissionCode.PROMPT_READ, "查看 Prompt", "prompt", "read", "查看模板与版本", 400),
    _p(PermissionCode.PROMPT_WRITE, "编辑 Prompt", "prompt", "write", "新建版本与修改模板", 401),
    _p(PermissionCode.PROMPT_ROLLBACK, "回滚 Prompt", "prompt", "rollback", "回滚模板版本", 402),
    _p(PermissionCode.ENVIRONMENT_READ, "查看环境", "environment", "read", "查看环境配置，密钥掩码", 500),
    _p(PermissionCode.ENVIRONMENT_WRITE, "编辑环境", "environment", "write", "新建或修改环境", 501),
    _p(PermissionCode.ENVIRONMENT_SECRET_READ, "查看环境密钥", "environment", "secret_read", "读取环境密钥明文", 502),
    _p(PermissionCode.MODEL_READ, "查看模型", "model", "read", "查看模型注册信息", 600),
    _p(PermissionCode.MODEL_WRITE, "编辑模型", "model", "write", "注册或修改模型配置", 601),
    _p(PermissionCode.MODEL_TEST, "测试模型", "model", "test", "执行模型连通性测试", 602),
    _p(PermissionCode.MODEL_SECRET_READ, "查看模型密钥", "model", "secret_read", "读取模型 Key 明文", 603),
    _p(PermissionCode.CAPABILITY_READ, "查看取数资产", "capability", "read", "查看取数工具与数据源", 700),
    _p(PermissionCode.CAPABILITY_WRITE, "编辑取数资产", "capability", "write", "维护取数工具与数据源", 701),
    _p(PermissionCode.CAPABILITY_EXECUTE, "执行取数探针", "capability", "execute", "执行只读取数探针", 702),
    _p(PermissionCode.CALIBER_READ, "查看业务口径", "caliber", "read", "查看业务口径", 800),
    _p(PermissionCode.CALIBER_WRITE, "编辑业务口径", "caliber", "write", "维护业务口径", 801),
    _p(PermissionCode.CANDIDATE_READ, "查看候选池", "candidate", "read", "查看用例候选", 900),
    _p(PermissionCode.CANDIDATE_TRIAGE, "候选池分诊", "candidate", "triage", "标记候选状态", 901),
    _p(PermissionCode.CANDIDATE_PROMOTE, "候选转正", "candidate", "promote", "将候选转成正式用例", 902),
    _p(PermissionCode.GRADER_RUN, "判分器自检", "grader", "run", "执行判分器自检", 1000),
    _p(PermissionCode.STATS_READ, "查看运营统计", "stats", "read", "查看看板与统计", 1100),
    _p(PermissionCode.USER_READ, "查看用户", "user", "read", "查看账号列表与详情", 1200),
    _p(PermissionCode.USER_WRITE, "管理用户", "user", "write", "创建、编辑、禁用账号", 1201),
    _p(PermissionCode.USER_RESET_PASSWORD, "重置密码", "user", "reset_password", "重置用户密码", 1202),
    _p(PermissionCode.ROLE_READ, "查看角色", "role", "read", "查看角色与权限", 1300),
    _p(PermissionCode.ROLE_WRITE, "管理角色", "role", "write", "创建、修改角色与权限", 1301),
    _p(PermissionCode.ROLE_ASSIGN, "分配角色", "role", "assign", "给用户分配角色", 1302),
    _p(PermissionCode.AUDIT_READ, "查看审计日志", "audit", "read", "查看登录与操作审计", 1400),
)


ALL_PERMISSION_CODES: frozenset[str] = frozenset(p.code for p in PERMISSION_CATALOG)
PERMISSION_BY_CODE: dict[str, PermissionDef] = {p.code: p for p in PERMISSION_CATALOG}


@dataclass(frozen=True, slots=True)
class BuiltinRole:
    code: str
    name: str
    description: str
    data_scope: str
    is_system: bool
    permissions: frozenset[str]


_ALL = ALL_PERMISSION_CODES
_BUILTIN_ROLES: tuple[BuiltinRole, ...] = (
    BuiltinRole(
        code="SYSTEM_ADMIN",
        name="系统管理员",
        description="管理账号、角色、环境、模型与全局配置",
        data_scope="ALL",
        is_system=True,
        permissions=_ALL,
    ),
    BuiltinRole(
        code="CASE_OPERATOR",
        name="用例操作员",
        description="编写、导入、调试用例，创建并执行任务",
        data_scope="ALL",
        is_system=True,
        permissions=frozenset(
            {
                PermissionCode.DASHBOARD_READ.value,
                PermissionCode.CASE_READ.value,
                PermissionCode.CASE_CREATE.value,
                PermissionCode.CASE_UPDATE.value,
                PermissionCode.CASE_IMPORT.value,
                PermissionCode.CASE_EXPORT.value,
                PermissionCode.CASE_DEBUG.value,
                PermissionCode.TASK_READ.value,
                PermissionCode.TASK_CREATE.value,
                PermissionCode.TASK_STOP.value,
                PermissionCode.TASK_EXPORT.value,
                PermissionCode.EXECUTION_READ.value,
                PermissionCode.EXECUTION_RETRY.value,
                PermissionCode.EXECUTION_EXPORT.value,
                PermissionCode.PROMPT_READ.value,
                PermissionCode.ENVIRONMENT_READ.value,
                PermissionCode.MODEL_READ.value,
                PermissionCode.CAPABILITY_READ.value,
                PermissionCode.CAPABILITY_EXECUTE.value,
                PermissionCode.CALIBER_READ.value,
                PermissionCode.CANDIDATE_READ.value,
                PermissionCode.CANDIDATE_TRIAGE.value,
                PermissionCode.GRADER_RUN.value,
                PermissionCode.STATS_READ.value,
            }
        ),
    ),
    BuiltinRole(
        code="CASE_REVIEWER",
        name="用例审核员",
        description="审核、冻结、回滚用例，维护取数资产与口径",
        data_scope="ALL",
        is_system=True,
        permissions=frozenset(
            {
                PermissionCode.DASHBOARD_READ.value,
                PermissionCode.CASE_READ.value,
                PermissionCode.CASE_CREATE.value,
                PermissionCode.CASE_UPDATE.value,
                PermissionCode.CASE_IMPORT.value,
                PermissionCode.CASE_EXPORT.value,
                PermissionCode.CASE_REVIEW.value,
                PermissionCode.CASE_FREEZE.value,
                PermissionCode.CASE_ROLLBACK.value,
                PermissionCode.CASE_DEBUG.value,
                PermissionCode.TASK_READ.value,
                PermissionCode.TASK_CREATE.value,
                PermissionCode.TASK_STOP.value,
                PermissionCode.TASK_EXPORT.value,
                PermissionCode.EXECUTION_READ.value,
                PermissionCode.EXECUTION_RETRY.value,
                PermissionCode.EXECUTION_EXPORT.value,
                PermissionCode.PROMPT_READ.value,
                PermissionCode.ENVIRONMENT_READ.value,
                PermissionCode.MODEL_READ.value,
                PermissionCode.CAPABILITY_READ.value,
                PermissionCode.CAPABILITY_WRITE.value,
                PermissionCode.CAPABILITY_EXECUTE.value,
                PermissionCode.CALIBER_READ.value,
                PermissionCode.CALIBER_WRITE.value,
                PermissionCode.CANDIDATE_READ.value,
                PermissionCode.CANDIDATE_TRIAGE.value,
                PermissionCode.CANDIDATE_PROMOTE.value,
                PermissionCode.GRADER_RUN.value,
                PermissionCode.STATS_READ.value,
            }
        ),
    ),
    BuiltinRole(
        code="TEST_OPERATOR",
        name="测试执行员",
        description="执行批量任务、查看 Trace、导出结果",
        data_scope="ALL",
        is_system=True,
        permissions=frozenset(
            {
                PermissionCode.DASHBOARD_READ.value,
                PermissionCode.CASE_READ.value,
                PermissionCode.TASK_READ.value,
                PermissionCode.TASK_CREATE.value,
                PermissionCode.TASK_STOP.value,
                PermissionCode.TASK_EXPORT.value,
                PermissionCode.EXECUTION_READ.value,
                PermissionCode.EXECUTION_RETRY.value,
                PermissionCode.EXECUTION_EXPORT.value,
                PermissionCode.PROMPT_READ.value,
                PermissionCode.ENVIRONMENT_READ.value,
                PermissionCode.MODEL_READ.value,
                PermissionCode.STATS_READ.value,
            }
        ),
    ),
    BuiltinRole(
        code="VIEWER",
        name="只读观察员",
        description="只读查看看板、用例与执行结果",
        data_scope="ALL",
        is_system=True,
        permissions=frozenset(
            {
                PermissionCode.DASHBOARD_READ.value,
                PermissionCode.CASE_READ.value,
                PermissionCode.TASK_READ.value,
                PermissionCode.EXECUTION_READ.value,
                PermissionCode.PROMPT_READ.value,
                PermissionCode.ENVIRONMENT_READ.value,
                PermissionCode.MODEL_READ.value,
                PermissionCode.CAPABILITY_READ.value,
                PermissionCode.CALIBER_READ.value,
                PermissionCode.STATS_READ.value,
            }
        ),
    ),
)

BUILTIN_ROLES: tuple[BuiltinRole, ...] = _BUILTIN_ROLES
BUILTIN_ROLE_CODES: frozenset[str] = frozenset(r.code for r in BUILTIN_ROLES)
SYSTEM_ADMIN_CODE = "SYSTEM_ADMIN"


def role_default_permissions(code: str) -> frozenset[str]:
    for role in BUILTIN_ROLES:
        if role.code == code:
            return role.permissions
    return frozenset()


def permission_modules() -> list[str]:
    seen: list[str] = []
    for item in PERMISSION_CATALOG:
        if item.module not in seen:
            seen.append(item.module)
    return seen
