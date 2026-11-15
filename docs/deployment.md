# 部署说明

本文面向单机/单节点生产部署（Linux）。开发环境（Windows + 双 Mock 服务 + Vite）的启动方式见根目录 `README.md`。

## 1. 部署拓扑

```
Browser ──HTTPS──> Nginx ──┬── /                     -> frontend/dist（静态资源）
                           ├── /api/                 -> 127.0.0.1:8000（含 WebSocket Upgrade）
                           └── /health               -> 127.0.0.1:8000
                                   │
                               FastAPI（uvicorn）
                                   ├── PostgreSQL 15+
                                   └── Redis（控制面 / 进度事件）
```

- 前端为纯静态 SPA，构建后由 Nginx 托管。
- 后端为单进程 asyncio 调度模型：**同一时间只应运行一个 API 实例承担任务调度**，避免重复执行；如使用多 worker，任务调度仍由每个 worker 各自持有，务必仅保留 1 个 worker 或后续引入独立 worker 进程。
- 若部署多实例，必须共享 Redis，并保证只有调度实例处理任务。

## 2. 前置检查

| 项 | 要求 |
| --- | --- |
| Python | 3.11+ |
| Node.js | 18+（仅构建阶段需要） |
| PostgreSQL | 15+，UTF-8 编码 |
| Redis | 6.0+ |
| 系统 | Linux（systemd），2 vCPU / 4 GB 起；按并发与用例规模上调 |
| 网络 | 后端需可访问被测系统与 LLM 服务；浏览器需可访问 Nginx |

## 3. 后端部署

### 3.1 安装

```bash
sudo useradd -r -s /bin/false billingqa || true
sudo mkdir -p /opt/billing-qa && sudo chown -R billingqa:billingqa /opt/billing-qa
sudo -u billingqa git clone <repo-url> /opt/billing-qa/app
cd /opt/billing-qa/app/backend

sudo -u billingqa python3 -m venv .venv
sudo -u billingqa .venv/bin/pip install --upgrade pip
sudo -u billingqa .venv/bin/pip install -e .
```

### 3.2 配置

```bash
sudo -u billingqa cp .env.example .env
sudo -u billingqa chmod 600 .env
# 编辑 .env：数据库、Redis、SECRET_KEY、管理员密码、CORS
```

生成安全密钥：

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

将输出写入 `SECRET_KEY`；该值同时用于 JWT 与 Fernet 加密，**变更后已加密的环境变量 / 模型 Key 将无法解密**，需重新录入。

### 3.3 初始化与迁移

```bash
# 首次：创建数据库 + 升级表结构 + 内置模板（生产不建议带 --demo）
sudo -u billingqa .venv/bin/python -m billing_qa.db.init_db

# 结构有变更时（开发阶段生成，部署时升级）
sudo -u billingqa .venv/bin/alembic upgrade head
```

> 表结构只有一条链路：`init_db` 负责建库后调用 `alembic upgrade head`（实现见 `billing_qa/db/migrations.py`），空库会按 `migrations/versions` 从零建表，不再使用 `create_all` 或 `alembic stamp`。已在生产运行且执行过初始化或升级的库，后续只需 `alembic upgrade head`。建库账号需具备 `CREATEDB` 权限；若使用受限账号，请先由 DBA 创建 `batch_test` 库（UTF-8）再执行初始化。

### 3.4 从 MySQL 迁移已有数据（可选）

仅切换数据库、无需保留旧数据时可跳过本节。需要保留 MySQL 中的环境、模型、用例与执行记录时：

> 切换代码前，请先在旧版本代码上对 MySQL 执行 `alembic upgrade head`，确保源库表结构与当前 ORM 完全一致；否则迁移脚本可能因缺列失败。

```bash
# 1) 创建空的 PostgreSQL 目标库（也可先执行 init_db，它会建库并升级表结构）
createdb -U postgres batch_test

# 2) 建表结构（不会写入种子数据）
cd /opt/billing-qa/app/backend
sudo -u billingqa .venv/bin/alembic upgrade head

# 3) 安装旧库驱动
sudo -u billingqa .venv/bin/pip install -e ".[mysql-import]"

# 4) 导入数据：按外键顺序复制、保留主键、重置自增序列
sudo -u billingqa env MYSQL_SOURCE_URL='mysql+pymysql://root:root@127.0.0.1:3306/batch_test?charset=utf8mb4' \
  .venv/bin/python scripts/migrate_mysql_to_postgres.py --truncate
```

迁移完成后建议抽查用例数量、版本快照与执行记录，并执行一次 `python scripts/api_smoke.py` 验证读写。迁移期间请停止旧服务写入；`SECRET_KEY` 必须与旧环境一致，否则已加密的环境变量与模型 Key 无法解密。

> PostgreSQL 默认大小写敏感：各表 `code` 唯一约束按原值比较；列表关键字搜索已使用 `ILIKE` 保持不区分大小写的体验。JSON 列在 PostgreSQL 中使用 `JSON` 类型，如需更高效的 JSON 查询可后续迁移到 `JSONB`。

### 3.5 systemd 服务

`/etc/systemd/system/billing-qa.service`：

```ini
[Unit]
Description=Billing QA Platform API
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=billingqa
Group=billingqa
WorkingDirectory=/opt/billing-qa/app/backend
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/billing-qa/app/backend/.venv/bin/python -m uvicorn billing_qa.main:app \
    --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now billing-qa
sudo systemctl status billing-qa
curl -s http://127.0.0.1:8000/health
```

日志通过 stdout 交给 journald：

```bash
journalctl -u billing-qa -f
```

## 4. 前端构建与 Nginx

```bash
cd /opt/billing-qa/app/frontend
npm ci
npm run build          # 生成 dist/
```

Nginx 站点示例（`/etc/nginx/conf.d/billing-qa.conf`）：

```nginx
server {
    listen 80;
    server_name qa.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name qa.example.com;

    ssl_certificate     /etc/nginx/certs/qa.example.com.pem;
    ssl_certificate_key /etc/nginx/certs/qa.example.com.key;

    root /opt/billing-qa/app/frontend/dist;
    index index.html;

    # SPA 路由回退
    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket 实时大盘
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

前端构建产物中的 API 地址为同源相对路径（`/api/v1`），无需额外配置环境变量；若前后端跨域部署，需同时调整 `CORS_ORIGINS` 与前端反向代理。

## 5. 安全加固清单

- [ ] 替换 `SECRET_KEY`、`ADMIN_PASSWORD`，关闭 `DEBUG`，设置 `APP_ENV=prod`
- [ ] PostgreSQL 使用独立账号并最小权限（仅 `batch_test` 库的 DML/DDL）
- [ ] Redis 设置密码/ACL，禁止公网暴露
- [ ] 全站 HTTPS，`CORS_ORIGINS` 仅保留正式域名
- [ ] Nginx 增加请求体大小、超时与限流配置
- [ ] `.env` 权限 600，`logs/`（如自行落盘）禁止公网访问
- [ ] 定期轮换管理员密码；轮换 `SECRET_KEY` 前先备份并重录加密配置

## 6. 备份与恢复

需要备份的只有 PostgreSQL；Redis 仅存任务控制标志与进度事件，丢失后任务需重跑。

```bash
# 备份（自定义格式，适合大库与并行恢复）
pg_dump -U postgres -Fc batch_test > /backup/batch_test_$(date +%F).dump

# 恢复
pg_restore -U postgres -d batch_test --clean --if-exists /backup/batch_test_2026-01-01.dump
```

也可使用纯 SQL 备份：

```bash
pg_dump -U postgres -Fp batch_test | gzip > /backup/batch_test_$(date +%F).sql.gz
gunzip -c /backup/batch_test_2026-01-01.sql.gz | psql -U postgres -d batch_test
```

建议保留 7~30 天，并定期演练恢复。

## 7. 监控与巡检

| 项 | 方式 |
| --- | --- |
| 存活 | `GET /health`（返回 `status / redis / env`） |
| 接口文档 | `/api/v1/docs`（生产可限制访问） |
| 任务健康 | 大盘「运行中」数量、失败率、P95 时延；异常时查看 Trace |
| 日志 | `journalctl -u billing-qa`（WARNING 及以上重点关注执行取消、熔断、外部接口超时） |
| 磁盘 | PostgreSQL 数据目录、备份目录、Nginx 日志 |

## 8. 升级与回滚

```bash
cd /opt/billing-qa/app
sudo -u billingqa git fetch --all
sudo -u billingqa git checkout <tag-or-commit>

cd backend
sudo -u billingqa .venv/bin/pip install -e .
sudo -u billingqa .venv/bin/alembic upgrade head
sudo systemctl restart billing-qa

cd ../frontend
npm ci && npm run build
sudo systemctl reload nginx
```

回滚：

1. 切回上一版本代码并 `pip install -e .`；
2. 若已执行不可逆迁移，需用备份恢复数据库；
3. 重启服务并验证 `/health`、登录、一次小批量任务。

## 9. 容器化（参考）

仓库未内置 Dockerfile，可按以下片段自行封装（**本次验证未使用容器环境**）：

后端：

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml /app/
COPY backend/billing_qa /app/billing_qa
COPY backend/migrations /app/migrations
COPY backend/alembic.ini /app/alembic.ini
RUN pip install --no-cache-dir -e .
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "billing_qa.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

前端：

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
```

容器部署时需注意：

- `HOST=0.0.0.0`，`--workers 1`（调度单实例）；
- 数据库与 Redis 通过环境变量指向外部服务；
- 首次启动前执行 `python -m billing_qa.db.init_db`。

## 10. 部署后验收

```bash
curl -s https://qa.example.com/health
cd /opt/billing-qa/app/backend
.venv/bin/python scripts/api_smoke.py     # 43 项
```

再通过浏览器完成一次登录 + 新建批量任务 + 打开 Trace，确认 WebSocket 与静态资源均正常。
