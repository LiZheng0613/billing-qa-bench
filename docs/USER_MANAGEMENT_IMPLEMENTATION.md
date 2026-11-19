# 用户与权限系统 · 实现说明

> 设计稿：[`USER_MANAGEMENT_DESIGN.md`](./USER_MANAGEMENT_DESIGN.md)、[`USER_MANAGEMENT_UI.md`](./USER_MANAGEMENT_UI.md)
>
> 当前完成的是可运行的 RBAC 基础设施与用户与权限前端；部分增强项在文末列出。

---

## 1. 后端

### 数据模型

新增 `backend/billing_qa/models/user.py`：

- `sys_user`：账号、密码哈希、状态、`token_version`、登录失败/锁定字段。
- `sys_role`：角色编码、名称、数据范围、内置标识。
- `sys_permission`：权限目录，`资源:动作` 唯一。
- `sys_user_role` / `sys_role_permission`：授权关系。
- `sys_audit_log`：登录、账号、角色与权限变更审计。

迁移：`backend/migrations/versions/b4c8d1e6f2a9_add_rbac.py`。

### 认证

- 登录改为数据库账号；密码使用 **bcrypt**。
- JWT 载荷：`sub=user_id`、`ver=token_version`、`jti`；不再把权限写进 Token。
- 改密、改角色、禁用、重置密码都会递增 `token_version`，旧会话下一次请求 401。
- 保留环境变量引导管理员：空库首次登录时自动创建 `admin` 并同步角色/权限。
- 旧格式 Token 在数据库启用后强制重新登录。

### 权限目录与内置角色

`backend/billing_qa/core/permissions.py`：

- 46 个权限码，覆盖用例、任务、执行、Prompt、环境、模型、取数资产、口径、候选池、判分器、统计、用户、角色、审计。
- 5 个内置角色：`SYSTEM_ADMIN`、`CASE_OPERATOR`、`CASE_REVIEWER`、`TEST_OPERATOR`、`VIEWER`。
- `SYSTEM_ADMIN` 始终拥有全量权限；其他内置角色首次创建时写入默认权限。

### 接口

| 接口 | 说明 |
| --- | --- |
| `POST /api/v1/auth/login` | 登录，返回 token + user + permissions |
| `GET /api/v1/auth/me` | 当前用户、角色与权限 |
| `POST /api/v1/auth/change-password` | 自助改密 |
| `POST /api/v1/auth/logout` | 退出并写审计 |
| `GET/POST /api/v1/users` | 用户列表 / 创建 |
| `GET/PATCH /api/v1/users/{id}` | 详情 / 更新资料与状态 |
| `PUT /api/v1/users/{id}/roles` | 分配角色 |
| `POST /api/v1/users/{id}/reset-password` | 重置密码（返回一次性临时密码） |
| `POST /api/v1/users/{id}/unlock` | 解锁账号 |
| `GET/POST /api/v1/roles` | 角色列表 / 创建 |
| `GET/PATCH/DELETE /api/v1/roles/{id}` | 角色详情 / 更新 / 删除 |
| `PUT /api/v1/roles/{id}/permissions` | 设置角色权限 |
| `GET /api/v1/roles/permissions/catalog` | 权限目录 |
| `GET /api/v1/audit-logs` | 审计日志分页与筛选 |
| `GET /api/v1/notifications` | 统一通知（过滤登录/退出等审计流水） |

统一通知由审计事件投影为业务消息，目前覆盖账号创建、状态变更、角色调整、密码重置、账号解锁、角色与权限变更；`auth.*` 登录/退出流水不会进入通知。

### 权限执行

- 新增 `require_permission`、`require_any`、`require_module_access`。
- 现有业务路由已加模块级权限：
  - 读请求要求 `xxx:read`。
  - 写请求要求模块任一写权限。
- 用户/角色/审计接口使用动作级权限（`user:write`、`role:assign`、`audit:read` 等）。
- `SYSTEM_ADMIN` 拥有全量权限；普通角色访问用户与权限接口返回 403。

### 关键约束

- 不能禁用自己。
- 不能修改自己的角色。
- 至少保留一个启用状态的系统管理员。
- 内置角色不能删除；`SYSTEM_ADMIN` 权限固定为全量。
- 重置密码、禁用、改角色会失效目标用户旧会话。

---

## 2. 前端

### 路由与外壳

- 新增 `/access/users`、`/access/roles`、`/access/audit`，挂在主 `AppShell` 下。
- 主侧边栏新增「用户与权限」入口，与「运营后台」同级；仅 `SYSTEM_ADMIN` 可见。
- 不单独做系统管理外壳；右侧沿用主工作台框架，页面内提供三个标签。
- 通知位于侧边栏左下角铃铛；账号入口位于左下角用户卡片。
- 路由守卫：登录态 + `SYSTEM_ADMIN` + 页面权限；无权限进入 403 页面。

### 页面

- `frontend/src/access/UsersView.vue`：全宽用户表格、搜索/角色/状态筛选、批量选择/禁用、右侧详情抽屉、新建用户、重置密码、禁用/启用、分配角色。
- `frontend/src/access/RolesView.vue`：角色横向切换、权限卡片、全选、权限搜索、进度环、保存权限。
- `frontend/src/access/AuditView.vue`：时间/动作/资源/结果筛选、分页审计表。
- `frontend/src/access/AccessTabs.vue`：三个标签页切换。

### 状态管理

- `stores/auth.ts`：`user`、`roles`、`permissions`、`can`、`canAny`、`isAdmin`、`hasRole`。
- 登录后直接写入 profile；刷新页面时 `fetchProfile` 重新加载。

---

## 3. 运行与验证

```bash
cd backend
python -m billing_qa.db.init_db
python -m uvicorn billing_qa.main:app --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm run dev
```

默认引导账号：`admin / admin123`（生产环境请立即修改，并替换 `SECRET_KEY`）。

已验证：

- `python -m ruff check billing_qa`：通过
- `python -m compileall billing_qa`：通过
- `python -m pytest -q`：**344 passed**
- `npm run typecheck`：通过
- `npm run build`：通过
- API 冒烟：登录、用户/角色/权限/审计、普通角色 403 均通过

---

## 4. 尚未完成 / 下一步

1. **角色创建/复制 UI**：后端 `POST /roles` 已就绪，前端按钮目前为提示占位。
2. **用户资料编辑 UI**：后端 `PATCH /users/{id}` 已就绪，抽屉暂只支持角色/状态/密码操作。
3. **导出**：用户 CSV、审计 CSV、权限矩阵导出尚为占位。
4. **全局审计**：当前审计覆盖登录与用户/角色变更；业务写操作尚未统一接入审计。
5. **对象级数据范围**：`sys_role.data_scope` 已预留，尚未按环境/创建人过滤。
6. **WebSocket 鉴权**：任务进度 WS 目前仍只校验 Token，未校验账号状态/`task:read`。
7. **强制首次改密**：`must_change_password` 已返回，前端尚未强制跳转改密页。
8. **演示账号**：当前空库只创建 admin；如需演示可运行 `init_db --demo` 或通过用户管理创建。
