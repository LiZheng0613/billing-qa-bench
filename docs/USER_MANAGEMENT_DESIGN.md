# 用户与权限管理系统 · 关键设计

> 目标：让系统管理员、用例操作员、用例审核员、测试执行员、只读观察员在同一套系统中拥有不同的操作能力，并且权限判定可审计、可演进、不破坏现有数据。
>
> 关键设计图见：[用户与权限管理系统 · 关键设计图](./USER_MANAGEMENT_DIAGRAMS.md)
>
> 界面设计见：[用户与权限管理系统 · 界面设计](./USER_MANAGEMENT_UI.md)
>
> 角色权限页备选方案见：[角色与权限 · 界面方案对比](./USER_MANAGEMENT_ROLE_DESIGNS.md)
>
> 用户管理页备选方案见：[用户管理 · 界面方案对比](./USER_MANAGEMENT_USER_DESIGNS.md)
>
> 当前实现说明见：[用户与权限系统 · 实现说明](./USER_MANAGEMENT_IMPLEMENTATION.md)

---

## 0. 方案摘要

采用 **RBAC（用户 → 角色 → 权限）**，不引入 ABAC / 多租户：

- **5 个内置角色**：系统管理员、用例操作员、用例审核员、测试执行员、只读观察员。
- **权限码统一命名**为 `资源:动作`，例如 `case:update`、`task:stop`、`user:write`；权限目录由代码维护，角色与权限的绑定关系落库。
- **JWT 只放身份**（`uid`、`username`、`token_version`），不放权限快照；每次请求服务端加载用户状态与权限，避免角色调整后旧 Token 继续生效。
- **后端是唯一裁决点**：路由级 `require_permission(...)` + 业务对象级归属校验；前端只做菜单/按钮裁剪和路由拦截，不作为安全边界。
- **所有写操作与登录行为进入审计日志**；关键约束（最后一个管理员、不能改自己的角色、不能审批自己创建的用例）在服务层强制。
- **兼容现有代码**：保留 `get_current_operator()` 作为过渡壳，现有 `created_by` / `operator` 字符串继续保存用户名快照，不要求一次性重写业务表。

---

## 1. 现状与差距

| 位置 | 现状 | 问题 |
| --- | --- | --- |
| `backend/billing_qa/core/security.py` | 单管理员账号来自 `admin_username/admin_password` | 无法新增用户，无法禁用，无法改密 |
| `api/v1/auth.py` | 登录只返回 token/username | 无角色、无权限、无 `user_id` |
| 各业务 router | 仅 `Depends(get_current_operator)` | 只做了认证，所有登录用户都能增删改一切 |
| `schemas/auth.py` | `UserInfo.role` 固定 `"admin"` | 前端展示与实际权限无关联 |
| `frontend/src/components/AppShell.vue` | 角色文案写死“管理员” | 无法按角色显示菜单 |
| `frontend/src/router.ts` | 只判断 token 是否存在 | 无路由级权限 |
| 业务表 | `created_by` / `operator` 存用户名 | 可用作归属判断，但无用户外键 |

结论：当前是 **AuthN only**，需要补的是 **AuthZ + 用户生命周期管理 + 审计**。

---

## 2. 设计目标与非目标

### 2.1 目标

1. 账号可创建、禁用、重置密码、修改密码、分配角色。
2. 权限控制到“动作”级别，覆盖现有全部业务模块。
3. 角色调整、禁用、改密后，旧 Token 立即失效。
4. 关键操作可追溯到人、时间、对象、前后变化。
5. 兼容存量数据与现有接口路径，可分阶段上线。

### 2.2 一期不做（明确边界）

- 不接 SSO / LDAP / OAuth。
- 不做多租户、组织架构、审批流。
- 不做行级数据隔离（按环境/项目过滤），只做“写操作按创建人归属”；保留 `data_scope` 字段作为二期扩展点。
- 不做完全自定义权限码；管理员可调整“角色拥有哪些权限”，但不能发明新权限。

---

## 3. 权限模型

### 3.1 三层模型

```text
用户 User ──< 用户角色 UserRole >── 角色 Role ──< 角色权限 RolePermission >── 权限 Permission
```

- 一个用户可拥有多个角色，最终权限 = 所有角色权限的并集。
- 权限码是不可变的字符串常量，代码与数据库保持一致。
- 权限码命名：`模块:动作`，全小写，动作用下划线，例如 `user:reset_password`。

### 3.2 内置角色

| 角色 | code | 职责 | 关键边界 |
| --- | --- | --- | --- |
| 系统管理员 | `SYSTEM_ADMIN` | 用户、角色、环境、模型、Prompt、全局配置 | 唯一可管理账号与密钥的角色；仍受“最后一个管理员”保护 |
| 用例操作员 | `CASE_OPERATOR` | 编写/导入/调试用例，维护候选池，创建并执行批量任务 | 只能修改自己创建的用例；不能审核/冻结/回滚；不能改环境与模型 |
| 用例审核员 | `CASE_REVIEWER` | 审核、冻结、回滚用例，维护取数资产与口径，可执行回归 | 不能审批自己创建的用例；不能管理用户；Prompt 只读 |
| 测试执行员 | `TEST_OPERATOR` | 基于已发布用例创建任务、启动/停止、查看 Trace、导出结果 | 不改用例、不改资产、不看密钥 |
| 只读观察员 | `VIEWER` | 查看看板、用例、任务与执行结果 | 所有写接口拒绝；不导出敏感数据 |

> 小团队可以把 `CASE_REVIEWER` 的权限合并给 `SYSTEM_ADMIN`；角色本身保留，不删除。

### 3.3 权限矩阵

图例：`✓` 允许；`○` 仅限本人创建/负责的数据；`—` 拒绝。

| 功能 | 权限码 | 管理员 | 用例操作员 | 审核员 | 执行员 | 观察员 |
| --- | --- | :---: | :---: | :---: | :---: | :---: |
| 测试大盘 | `dashboard:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 查看用例 | `case:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 新建/编辑/导入用例 | `case:create` / `case:update` / `case:import` | ✓ | ○ | ○ | — | — |
| 删除用例 | `case:delete` | ✓ | ○（仅草稿） | — | — | — |
| 审核/冻结/回滚用例 | `case:review` / `case:freeze` / `case:rollback` | ✓ | — | ✓ | — | — |
| 单用例调试 | `case:debug` | ✓ | ✓ | ✓ | — | — |
| 导出用例 | `case:export` | ✓ | ✓ | ✓ | ✓ | — |
| 查看任务 | `task:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 创建/启动任务 | `task:create` | ✓ | ✓ | ✓ | ✓ | — |
| 停止任务 | `task:stop` | ✓ | ○ | ○ | ○ | — |
| 失败重试 | `execution:retry` | ✓ | ✓ | ✓ | ✓ | — |
| 查看执行结果 / Trace | `execution:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 导出执行结果 | `execution:export` | ✓ | ✓ | ✓ | ✓ | — |
| 查看 Prompt | `prompt:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 编辑/删除/回滚 Prompt | `prompt:write` | ✓ | — | — | — | — |
| 查看环境与模型 | `environment:read` / `model:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 编辑/删除环境与模型 | `environment:write` / `model:write` | ✓ | — | — | — | — |
| 模型连通性测试 | `model:test` | ✓ | — | — | — | — |
| 查看密钥明文 | `model:secret_read` / `environment:secret_read` | ✓ | — | — | — | — |
| 查看取数资产 / 业务口径 | `capability:read` / `caliber:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 编辑取数资产 / 业务口径 | `capability:write` / `caliber:write` | ✓ | — | ✓ | — | — |
| 执行取数探针 | `capability:execute` | ✓ | ✓ | ✓ | — | — |
| 查看候选池 | `candidate:read` | ✓ | ✓ | ✓ | — | — |
| 候选池分诊 | `candidate:triage` | ✓ | ✓ | ✓ | — | — |
| 候选转正 | `candidate:promote` | ✓ | — | ✓ | — | — |
| 判分器自检 | `grader:run` | ✓ | ✓ | ✓ | — | — |
| 运营统计 | `stats:read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 用户管理 | `user:read` / `user:write` / `user:reset_password` | ✓ | — | — | — | — |
| 角色与授权 | `role:read` / `role:write` / `role:assign` | ✓ | — | — | — | — |
| 审计日志 | `audit:read` | ✓ | — | — | — | — |

### 3.4 对象级规则（在服务层强制）

仅靠权限码不够，以下规则必须在 service 层执行：

1. **用例编辑**：`case:update` + 创建人本人或管理员 + 用例状态不是 `FROZEN`。
2. **用例删除**：`case:delete` + 创建人本人或管理员 + 仅 `DRAFT` + 无执行历史（沿用现有删除约束）。
3. **用例审核**：`case:review` + 审核人不是创建人（职责分离）；创建人本人只能提交，不能自审。
4. **用例冻结/回滚**：`case:freeze` / `case:rollback` + 版本状态符合现有状态机；冻结后内容只读。
5. **任务停止/中止**：`task:stop` + 任务创建人或管理员；执行员只能操作自己创建的任务。
6. **重试**：`execution:retry` + 对任务有 `task:read` 且任务属于当前用户可见范围。
7. **密钥查看**：只有管理员可读取明文；普通读接口一律返回掩码。
8. **账号保护**：
   - 不能禁用自己、不能删除自己。
   - 不能修改自己的角色（防止自我提权）。
   - 系统必须至少保留 1 个 `ACTIVE` 的 `SYSTEM_ADMIN`；撤销/禁用前 `SELECT ... FOR UPDATE` 计数校验。
9. **审计只追加**：不提供修改/删除审计日志的 API。

### 3.5 数据范围（二期预留）

一期：

- **读**：全团队可见，便于协作排查。
- **写**：按 `created_by` / 任务归属限制。

二期在 `sys_role` 上启用 `data_scope`：

- `ALL`：全部数据。
- `ENV`：仅被授权环境的数据。
- `OWN`：仅本人创建的数据。

一期先把字段和枚举建好，不实现过滤，避免过早复杂化。

---

## 4. 数据模型

### 4.1 表清单

| 表 | 作用 |
| --- | --- |
| `sys_user` | 用户账号、状态、密码、Token 版本 |
| `sys_role` | 角色定义、数据范围、是否内置 |
| `sys_permission` | 权限目录（与代码常量同步） |
| `sys_user_role` | 用户-角色多对多 |
| `sys_role_permission` | 角色-权限多对多 |
| `sys_audit_log` | 追加型审计日志 |

建议新增文件：

- `backend/billing_qa/models/sys_user.py`
- `backend/billing_qa/models/sys_role.py`
- `backend/billing_qa/models/sys_permission.py`
- `backend/billing_qa/models/sys_audit.py`

### 4.2 `sys_user`

```text
id                    BIGSERIAL     PK
username              VARCHAR(64)   NOT NULL           -- 登录名，创建后不可改
username_lower        VARCHAR(64)   NOT NULL UNIQUE    -- 登录时大小写不敏感
display_name          VARCHAR(64)   NOT NULL           -- 展示名
email                 VARCHAR(128)  NULL
password_hash         VARCHAR(255)  NOT NULL           -- bcrypt
status                VARCHAR(16)   NOT NULL DEFAULT 'ACTIVE'
                                                        -- ACTIVE / DISABLED / LOCKED
must_change_password  BOOLEAN       NOT NULL DEFAULT TRUE
token_version         INTEGER       NOT NULL DEFAULT 1  -- 改密/改角色/禁用时 +1
failed_login_count    INTEGER       NOT NULL DEFAULT 0
locked_until          DATETIME      NULL
last_login_at         DATETIME      NULL
last_login_ip         VARCHAR(64)   NULL
created_by            BIGINT        NULL               -- 创建人 user_id
created_at / updated_at
```

约束与索引：

- `UNIQUE (username_lower)`，禁用后也不允许复用用户名。
- `INDEX (status)`。
- 不做物理删除；停用即 `status=DISABLED`，保留审计关联。

### 4.3 `sys_role`

```text
id            BIGSERIAL     PK
code          VARCHAR(64)   NOT NULL UNIQUE   -- SYSTEM_ADMIN / CASE_OPERATOR ...
name          VARCHAR(64)   NOT NULL
description   VARCHAR(255)  NULL
data_scope    VARCHAR(16)   NOT NULL DEFAULT 'ALL'  -- ALL / ENV / OWN（一期仅存值）
is_system     BOOLEAN       NOT NULL DEFAULT FALSE  -- 内置角色不可删除
status        VARCHAR(16)   NOT NULL DEFAULT 'ACTIVE'
created_at / updated_at
```

### 4.4 `sys_permission`

```text
id          BIGSERIAL     PK
code        VARCHAR(64)   NOT NULL UNIQUE   -- case:update
name        VARCHAR(64)   NOT NULL
module      VARCHAR(32)   NOT NULL          -- case / task / user ...
action      VARCHAR(32)   NOT NULL          -- read / write / review ...
description VARCHAR(255)  NULL
sort        INTEGER       NOT NULL DEFAULT 0
```

权限目录的 **代码常量是唯一事实源**；启动时执行 `sync_permissions()`，把代码中新增的权限码补齐到表里，避免“新功能上线但管理员没权限”。

### 4.5 关联表

```text
sys_user_role(user_id, role_id, created_at)             PK(user_id, role_id)
sys_role_permission(role_id, permission_id, created_at) PK(role_id, permission_id)
```

外键均 `ON DELETE CASCADE`；用户/角色本身使用状态控制，不做物理删除。

### 4.6 `sys_audit_log`

```text
id             BIGSERIAL     PK
request_id     VARCHAR(64)   NULL
actor_id       BIGINT        NULL
actor_username VARCHAR(64)   NOT NULL      -- 快照，防止用户改名/删除后断链
action         VARCHAR(64)   NOT NULL      -- user.assign_roles / case.review ...
resource       VARCHAR(64)   NOT NULL      -- user / case / task ...
resource_id    VARCHAR(64)   NULL
method         VARCHAR(8)    NULL
path           VARCHAR(512)  NULL
ip             VARCHAR(64)   NULL
user_agent     VARCHAR(512)  NULL
status_code    INTEGER       NULL
success        BOOLEAN       NOT NULL DEFAULT TRUE
detail         JSON          NULL          -- 变更前后摘要，敏感字段脱敏
created_at     DATETIME      NOT NULL
```

索引：`(created_at)`、`(actor_id, created_at)`、`(action, created_at)`、`(resource, resource_id)`。

---

## 5. 认证与会话设计

### 5.1 登录流程

```text
POST /api/v1/auth/login
  → 规范化 username（trim + lower 查询）
  → 账号不存在：执行一次假哈希校验，返回统一错误（防用户枚举）
  → 账号 DISABLED / LOCKED：返回“账号不可用”，不透露细节
  → bcrypt 校验密码
      ├─ 失败：failed_login_count +1；达到 5 次锁定 15 分钟
      └─ 成功：清零计数，更新 last_login_*，签发 JWT
  → 返回 token + 用户信息 + 权限列表
```

### 5.2 JWT 载荷

```json
{
  "sub": "12",
  "username": "zhangsan",
  "ver": 3,
  "jti": "e2f1...",
  "iat": 1730000000,
  "exp": 1730028800
}
```

关键点：

- `sub` 使用 **user_id**，不使用用户名；用户名只做展示与审计快照。
- **不放 roles / permissions 快照**：角色变更后旧 Token 会继续携带旧权限，是常见越权来源。
- `ver` 与 `sys_user.token_version` 比对；不一致即 401，强制重新登录。
- 有效期一期沿用 8 小时（`jwt_expire_minutes` 默认调整为 480）；内部系统不做 refresh token，过期重登。
- `jti` 仅用于日志关联，一期不做黑名单；撤销依赖 `token_version`。

### 5.3 权限加载

每次请求：

1. `get_current_user()` 解析 Token，取 `uid` + `ver`。
2. 查询 `sys_user`，校验 `status == ACTIVE` 且 `token_version == ver`。
3. 查询用户角色与权限并集。
4. 可选：Redis 缓存 `authz:user:{id}:{ver}`，TTL 300 秒；`token_version` 变化天然失效。
5. 构造 `CurrentUser` 注入路由。

用户量很小，一期直接查库也可接受；缓存作为优化项，不改变语义。

### 5.4 密码策略

- 使用 **bcrypt**（新增依赖 `bcrypt`），不自己实现哈希。
- 最小长度 10，至少包含大小写字母与数字；禁止与用户名相同。
- 管理员重置密码后：`must_change_password=TRUE` 且 `token_version += 1`，旧会话立即失效。
- 用户自助改密：校验旧密码 → 写入新哈希 → `token_version += 1` → 返回新 Token（或要求重新登录）。
- 首次启动创建的管理员密码来自环境变量，但必须 `must_change_password=TRUE`。

### 5.5 登录防爆破

- 同一用户名连续失败 5 次，锁定 15 分钟；`failed_login_count` + `locked_until` 落库，多实例一致。
- 同一 IP 的登录失败做 Redis 计数限流（例如 30 次 / 10 分钟）。
- 登录失败响应统一为“用户名或密码错误”，不区分账号不存在/密码错误。

### 5.6 WebSocket

现有 `/ws/tasks/{task_id}` 只解码 Token，需要补齐：

1. 解析 token → 加载用户并校验 `status` 与 `token_version`。
2. 校验 `task:read`，否则关闭 `4403`。
3. 认证失败关闭 `4401`，与 HTTP 语义保持一致。
4. Query 传 token 的方式保留，但日志中必须脱敏。

### 5.7 首次初始化

- 启动时若 `sys_user` 为空：用 `admin_username/admin_password` 创建 `SYSTEM_ADMIN`，`must_change_password=TRUE`。
- 已有用户后，环境变量中的默认口令 **不再参与登录**；生产环境部署后应删除 `ADMIN_PASSWORD`。
- 初始化逻辑幂等：只认“用户表为空”，不覆盖已有管理员。

---

## 6. 后端鉴权落地

### 6.1 当前用户上下文

```python
# billing_qa/core/context.py
@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    roles: frozenset[str]
    permissions: frozenset[str]
    is_admin: bool
```

### 6.2 认证依赖

```python
# billing_qa/core/security.py（重写）
async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if credentials is None:
        raise unauthorized("未登录或缺少访问令牌")
    payload = decode_access_token(credentials.credentials)
    user_id = int(payload["sub"])
    token_version = int(payload.get("ver", 0))

    user = await db.get(SysUser, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise unauthorized("账号不可用")
    if user.token_version != token_version:
        raise unauthorized("登录状态已失效，请重新登录")

    roles, permissions = await load_user_roles_and_permissions(db, user.id)
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=frozenset(roles),
        permissions=frozenset(permissions),
        is_admin="SYSTEM_ADMIN" in roles,
    )


async def get_current_operator(
    user: CurrentUser = Depends(get_current_user),
) -> str:
    """兼容壳：现有 API 继续拿到用户名，但已经过完整账号校验。"""
    return user.username
```

> 先上线这个兼容壳，现有 12 个 router 立刻获得“禁用用户 / 改密后旧 Token 失效”的能力；再逐个接口补权限码。

### 6.3 权限依赖

```python
# billing_qa/core/authz.py
def require_permission(code: str):
    async def _checker(
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if code not in user.permissions:
            raise forbidden()
        return user
    return _checker


def require_any(*codes: str):
    async def _checker(
        user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if not user.permissions.intersection(codes):
            raise forbidden()
        return user
    return _checker
```

路由用法：

```python
router = APIRouter(
    prefix="/cases",
    tags=["测试用例"],
    dependencies=[Depends(require_permission("case:read"))],
)

@router.post("", dependencies=[Depends(require_permission("case:create"))])
async def create_case(...): ...

@router.put("/{case_id}", dependencies=[Depends(require_permission("case:update"))])
async def update_case(
    case_id: int,
    user: CurrentUser = Depends(require_permission("case:update")),
    ...
): ...
```

规则：

- **读接口用 router 级**统一 `xxx:read`。
- **写接口用端点级**，避免 router 级权限把读接口误伤。
- 同一端点需要多个动作时用 `require_any`，但不要用“读权限”兜底写操作。
- 业务对象校验放在 service，依赖只负责“有没有这个动作权限”。

### 6.4 现有模块权限映射

| 路由文件 | 读权限 | 写权限 |
| --- | --- | --- |
| `environments.py` | `environment:read` | `environment:write` |
| `models.py` | `model:read` | `model:write` / `model:test` / `model:secret_read` |
| `prompts.py` | `prompt:read` | `prompt:write` |
| `cases.py` | `case:read` | `case:create` / `case:update` / `case:delete` / `case:import` / `case:review` / `case:freeze` / `case:rollback` / `case:debug` / `case:export` |
| `capabilities.py` | `capability:read` | `capability:write` / `capability:execute` |
| `candidates.py` | `candidate:read` | `candidate:triage` / `candidate:promote` |
| `calibers.py` | `caliber:read` | `caliber:write` |
| `grader.py` | `grader:run` | 同左（无状态操作） |
| `stats.py` | `stats:read` | — |
| `tasks.py` | `task:read` | `task:create` / `task:stop` / `execution:retry` / `execution:export` |
| `executions.py` | `execution:read` | `execution:retry` / `execution:export` |
| `ws.py` | `task:read` | — |
| `auth.py` | 公开登录 / 登录后 `me` | `change-password` |

### 6.5 错误语义

| 场景 | HTTP | 业务码 |
| --- | --- | --- |
| 缺少/过期/无效 Token | 401 | `UNAUTHORIZED` |
| 账号禁用、Token 版本失效 | 401 | `ACCOUNT_DISABLED` / `TOKEN_REVOKED` |
| 已登录但无权限 | 403 | `FORBIDDEN` |
| 用户名密码错误 | 401 | `AUTH_FAILED` |
| 用户名已存在 | 409 | `USERNAME_TAKEN` |
| 最后一个管理员 | 409 | `LAST_ADMIN` |
| 自我禁用/自我改角色 | 409 | `SELF_OPERATION_DENIED` |

403 响应不返回“缺少哪个权限”的细节，详细原因只进审计日志。

### 6.6 审计落地

两种方式配合：

1. **中间件**：捕获所有 `POST/PUT/PATCH/DELETE`，记录 actor、路径、状态码、耗时；用于“有没有调用”。
2. **业务显式记录**：在 service 里记录 `action`、`resource`、`resource_id`、`detail`（before/after 摘要）；用于“改了什么”。

必须审计的事件：

- 登录成功/失败/锁定、登出。
- 用户创建、禁用、启用、重置密码、角色变更。
- 角色创建/修改、权限重新分配。
- 用例删除、审核、冻结、回滚。
- 任务创建、启动、停止、计划变更。
- 环境/模型/Prompt 的增删改与密钥读取。

禁止记录：密码、Token、模型 Key、Basic Auth 密码、完整请求体中的敏感字段。

---

## 7. API 设计

统一前缀 `/api/v1`，分页沿用现有 `Page` 结构。

### 7.1 认证

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/auth/login` | 公开 | 登录 |
| GET | `/auth/me` | 登录 | 当前用户 + 角色 + 权限 |
| POST | `/auth/logout` | 登录 | 客户端清理即可；服务端记审计 |
| PUT | `/auth/password` | 登录 | 自助改密，需旧密码 |

登录响应（保留 `username` 顶层字段兼容旧前端）：

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 28800,
  "username": "zhangsan",
  "user": {
    "id": 12,
    "username": "zhangsan",
    "display_name": "张三",
    "roles": ["CASE_OPERATOR"],
    "permissions": ["dashboard:read", "case:read", "case:create"],
    "must_change_password": false
  }
}
```

### 7.2 用户管理

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/users` | `user:read` | 分页、关键字、状态、角色筛选 |
| POST | `/users` | `user:write` | 创建用户，初始密码一次性展示 |
| GET | `/users/{id}` | `user:read` | 详情 |
| PATCH | `/users/{id}` | `user:write` | 改展示名/邮箱/状态；用户名不可改 |
| POST | `/users/{id}/reset-password` | `user:reset_password` | 生成临时密码，强制改密 |
| PUT | `/users/{id}/roles` | `role:assign` | 全量覆盖角色；不能改自己 |
| POST | `/users/{id}/unlock` | `user:write` | 解除锁定并清零失败次数 |

创建用户请求：

```json
{
  "username": "zhangsan",
  "display_name": "张三",
  "email": "zhangsan@example.com",
  "role_ids": [2],
  "initial_password": "Temp#2026"
}
```

### 7.3 角色与权限

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/roles` | `role:read` | 角色列表 + 权限数量 |
| POST | `/roles` | `role:write` | 自定义角色（二期可只开放复制内置角色） |
| PATCH | `/roles/{id}` | `role:write` | 改名称/描述/数据范围；内置角色不可删 |
| DELETE | `/roles/{id}` | `role:write` | 仅非内置且无用户绑定 |
| GET | `/roles/{id}/permissions` | `role:read` | 角色权限详情 |
| PUT | `/roles/{id}/permissions` | `role:write` | 全量覆盖权限集合 |
| GET | `/permissions` | `role:read` | 权限目录，按 module 分组 |

### 7.4 审计

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/audit-logs` | `audit:read` | 按人/动作/资源/时间段筛选，只读 |

---

## 8. 前端设计

### 8.1 auth store

`frontend/src/stores/auth.ts` 扩展：

```ts
interface UserProfile {
  id: number
  username: string
  display_name: string
  roles: string[]
  permissions: string[]
  must_change_password: boolean
}

const user = ref<UserProfile | null>(null)
const permissions = computed(() => new Set(user.value?.permissions ?? []))
const loaded = ref(false)

function can(code: string): boolean {
  return permissions.value.has(code)
}

function canAny(codes: string[]): boolean {
  return codes.some(can)
}
```

- 登录成功后写入 token + user，不再只存 username。
- 应用启动时若已有 token，先 `fetchProfile()`；失败 401 则清理并回登录页。
- 不改用 localStorage 存权限作为安全依据；localStorage 只可缓存展示信息。

### 8.2 路由守卫

```ts
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (!getToken()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  const auth = useAuthStore()
  if (!auth.loaded) {
    try {
      await auth.fetchProfile()
    } catch {
      return { name: 'login' }
    }
  }

  const required = to.meta.permission as string | undefined
  if (required && !auth.can(required)) {
    return { name: 'forbidden' }
  }
  return true
})
```

路由 meta 增加 `permission`：

| 路由 | 权限 |
| --- | --- |
| `/` | `dashboard:read` |
| `/tasks`、`/tasks/:id` | `task:read` |
| `/cases`、`/cases/:id` | `case:read` |
| `/prompts` | `prompt:read` |
| `/trace` | `execution:read` |
| `/ops` | `stats:read` |
| `/settings` | `environment:read`（编辑按钮再判 `environment:write`） |
| `/access/users` | `SYSTEM_ADMIN` + `user:read` |
| `/access/roles` | `SYSTEM_ADMIN` + `role:read` |
| `/access/audit` | `SYSTEM_ADMIN` + `audit:read` |

### 8.3 菜单与按钮

- 用户与权限采用**主侧边栏入口**，与「运营后台」同级；该入口仅对 `SYSTEM_ADMIN` 渲染（`v-if="auth.isAdmin"`），不再单独做系统管理外壳。
- 右上角不放置搜索、通知和账号；通知入口固定在侧边栏左下角（铃铛 + 未读角标，点击展开面板），账号入口为左下角用户卡片。
- `AppShell.vue` 的 `navItems` 增加 `permission` / `adminOnly` 字段，用 `computed` 过滤；无权限的菜单不渲染，但后端仍然会拒绝直接访问。
- 系统管理员可管理当前系统的全部账号，用户列表不按创建人过滤；写操作按钮按 `user:write`、`role:assign` 等权限显示。
- 用户卡片显示 `display_name` + 角色中文名，不再写死“管理员”。
- 页面内写操作按钮使用 `v-if="auth.can('case:update')"`；导出、删除、审核、冻结分别判断。
- 新增 `403` 页面；API 返回 401 时统一清理并跳登录。

### 8.4 系统管理页面

- **用户管理**：全宽表格 + 右侧滑出详情（方案 B），支持搜索、角色/状态筛选、批量禁用、新建、重置密码、分配角色、解锁。
- **角色管理**：角色横向切换 + 权限模块卡片（方案 A）；带授权进度环、模块完成度、全选与权限搜索；内置角色标识。
- **审计日志**：时间、操作人、动作、资源、结果、IP；支持筛选与详情抽屉。
- 入口为侧边栏唯一图标，仅 `SYSTEM_ADMIN` 可见；不再放在用户下拉菜单中。

---

## 9. 安全清单

- [ ] 生产环境禁止默认 `secret_key` 与默认管理员密码；启动时检测并告警。
- [ ] 密码哈希使用 bcrypt；绝不记录明文密码与 Token。
- [ ] 登录失败统一文案，防用户枚举。
- [ ] 禁用用户、改密、改角色后 `token_version += 1`。
- [ ] 不能自我提权、不能禁用自己、不能移除最后一个管理员。
- [ ] 密钥/密码字段出参一律掩码，明文读取单独权限并审计。
- [ ] 审计日志只追加，保留期可配置（建议 180 天）。
- [ ] WebSocket 同样校验用户状态、Token 版本和 `task:read`。
- [ ] CORS 白名单保持现有配置，不因新接口放宽。
- [ ] 前端隐藏按钮不是安全边界；所有写接口必须有后端权限依赖 + 对象校验。

---

## 10. 迁移与实施计划

### 10.1 分阶段上线

| 阶段 | 内容 | 产出 |
| --- | --- | --- |
| P1 | 建表 + 权限常量 + 角色/权限种子 + 初始化管理员 | Alembic 迁移、`sync_permissions()`、幂等 seed |
| P2 | 重写认证：`get_current_user`、兼容 `get_current_operator`、登录/me/改密 | 现有接口立即支持禁用与 Token 失效 |
| P3 | 按模块补权限依赖 + 对象级校验 + 审计 | 全量鉴权生效 |
| P4 | 前端 store/守卫/菜单/403 + 用户/角色/审计页面 | 管理闭环 |
| P5 | 测试与加固：权限矩阵参数化、最后管理员、越权用例 | 上线 |

### 10.2 兼容策略

1. **先上兼容壳**：`get_current_operator` 改为依赖 `get_current_user`，保留返回 `str`，现有 12 个 router 不用改也能获得账号状态校验。
2. **保留字符串归属**：现有 `created_by` / `operator` 继续存用户名，避免大范围迁移；新写入同时保证与用户表一致。
3. **接口路径不变**：`/api/v1/auth/login`、`/auth/me` 字段只增不改；前端可灰度。
4. **存量数据无需回填**：历史 `created_by="admin"` 仍可显示；需要按用户统计时再逐步映射。
5. **默认管理员只用于空库初始化**：已有用户后环境变量口令失效。

### 10.3 种子数据

- 5 个内置角色，`is_system=TRUE`。
- 全量权限码，按模块排序。
- 内置角色 → 权限映射按第 3.3 节矩阵写入。
- 启动时 `sync_permissions()` 幂等补齐新增权限；`SYSTEM_ADMIN` 自动补齐全量权限，避免管理员被新功能锁死。

### 10.4 测试清单

后端：

- [ ] 未登录访问任意受保护接口 → 401。
- [ ] 5 个角色 × 关键端点参数化矩阵 → 期望 200/403。
- [ ] 禁用用户、改密、改角色后旧 Token → 401。
- [ ] 用例操作员修改他人用例 → 403。
- [ ] 审核员审批自己创建的用例 → 403。
- [ ] 撤销最后一个管理员 → 409。
- [ ] 管理员修改自己的角色 → 409。
- [ ] 登录失败 5 次锁定；锁定过期恢复。
- [ ] 审计日志记录角色变更前后值，且不含密码。

前端：

- [ ] 不同角色登录后菜单数量与内容正确。
- [ ] 直接输入无权限路由 → 403 页面。
- [ ] 无权限按钮不渲染。
- [ ] 401 自动清理登录态并跳转登录。

---

## 11. 需要确认的取舍

| 议题 | 默认建议 | 备选 |
| --- | --- | --- |
| 审核员是否独立 | 独立，落实职责分离 | 小团队并入管理员 |
| 用例操作员能否编辑他人用例 | 不能，仅本人 + 管理员 | 全团队可编辑 |
| 数据可见范围 | 一期全团队可读，写按归属 | 一期就按环境隔离 |
| Prompt 编辑权 | 仅管理员 | 管理员 + 审核员 |
| 会话策略 | 8 小时 Bearer Token | httpOnly Cookie + CSRF |
| 自定义角色 | 二期开放 | 一期只允许复制内置角色 |
| SSO / LDAP | 一期不做 | 预留 `auth_source` 字段 |

---

## 12. 关键文件改动清单

后端：

- `backend/billing_qa/models/sys_user.py`（新增）
- `backend/billing_qa/models/sys_role.py`（新增）
- `backend/billing_qa/models/sys_permission.py`（新增）
- `backend/billing_qa/models/sys_audit.py`（新增）
- `backend/billing_qa/core/permissions.py`（新增：权限常量 + 内置角色默认映射）
- `backend/billing_qa/core/security.py`（重写：JWT、当前用户、密码校验）
- `backend/billing_qa/core/authz.py`（新增：`require_permission` / `require_any`）
- `backend/billing_qa/schemas/auth.py`（扩展登录/用户信息）
- `backend/billing_qa/schemas/user.py`、`role.py`、`audit.py`（新增）
- `backend/billing_qa/api/v1/auth.py`（登录/me/改密）
- `backend/billing_qa/api/v1/users.py`、`roles.py`、`permissions.py`、`audit.py`（新增）
- `backend/billing_qa/api/v1/router.py`（注册新路由）
- `backend/billing_qa/services/auth/service.py`（用户/角色/密码/会话失效）
- `backend/migrations/versions/*_add_rbac.py`（新增）
- `backend/billing_qa/db/seed_auth.py`（新增：幂等种子）

前端：

- `frontend/src/types.ts`（UserProfile / Role / Permission / AuditLog）
- `frontend/src/api/index.ts`（用户、角色、审计接口）
- `frontend/src/stores/auth.ts`（profile、can/canAny、loaded）
- `frontend/src/router.ts`（`meta.permission`、异步守卫、403）
- `frontend/src/components/AppShell.vue`（菜单过滤、角色展示）
- `frontend/src/access/UsersView.vue`、`RolesView.vue`、`AuditView.vue`、`AccessTabs.vue`（新增）
- `frontend/src/views/ForbiddenView.vue`（新增）

