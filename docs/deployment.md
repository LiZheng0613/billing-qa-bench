# 部署说明

本文面向单机/单节点生产部署（Linux）。开发环境（Windows + 双 Mock 服务 + Vite）的启动方式见根目录 `README.md`。

## 1. 部署拓扑

```
Browser ──HTTPS──> Nginx ──┬── /                     -> frontend/dist（静态资源）
                           ├── /api/                 -> 127.0.0.1:8000（含 WebSocket Upgrade）
                           └── /health               -> 127.0.0.1:8000
                                   │
                               FastAPI（uvicorn）
                                   ├── MySQL 8（utf8mb4）
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
| MySQL | 8.0+，`utf8mb4` / `utf8mb4_0900_ai_ci` |
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
# 首次：建库建表 + 内置模板（生产不建议带 --demo）
sudo -u billingqa .venv/bin/python -m billing_qa.db.init_db

# 结构有变更时（开发阶段生成，部署时升级）
sudo -u billingqa .venv/bin/alembic upgrade head
```

> `init_db` 会执行 `alembic stamp head`；已在生产运行且执行过该命令的库，后续只需 `alembic upgrade head`。

### 3.4 systemd 服务

`/etc/systemd/system/billing-qa.service`：

```ini
[Unit]
Description=Billing QA Platform API
After=network.target mysql.service redis.service

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
- [ ] MySQL 使用独立账号并最小权限（仅 `batch_test` 库的 DML/DDL）
- [ ] Redis 设置密码/ACL，禁止公网暴露
- [ ] 全站 HTTPS，`CORS_ORIGINS` 仅保留正式域名
- [ ] Nginx 增加请求体大小、超时与限流配置
- [ ] `.env` 权限 600，`logs/`（如自行落盘）禁止公网访问
- [ ] 定期轮换管理员密码；轮换 `SECRET_KEY` 前先备份并重录加密配置

## 6. 备份与恢复

需要备份的只有 MySQL；Redis 仅存任务控制标志与进度事件，丢失后任务需重跑。

```bash
# 备份
mysqldump -uroot -p --single-transaction --routines --triggers batch_test \
  | gzip > /backup/batch_test_$(date +%F).sql.gz

# 恢复
gunzip -c /backup/batch_test_2026-01-01.sql.gz | mysql -uroot -p batch_test
```

建议保留 7~30 天，并定期演练恢复。

## 7. 监控与巡检

| 项 | 方式 |
| --- | --- |
| 存活 | `GET /health`（返回 `status / redis / env`） |
| 接口文档 | `/api/v1/docs`（生产可限制访问） |
| 任务健康 | 大盘「运行中」数量、失败率、P95 时延；异常时查看 Trace |
| 日志 | `journalctl -u billing-qa`（WARNING 及以上重点关注执行取消、熔断、外部接口超时） |
| 磁盘 | MySQL 数据目录、备份目录、Nginx 日志 |

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
