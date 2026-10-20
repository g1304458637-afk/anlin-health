# 运行、构建和部署

## 本地（推荐先验证）

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell 使用 .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env
python scripts/dev_start.py --seed
```

`dev_start.py` 会解析 `backend/.env`，启动仅监听 `127.0.0.1:8000` 的 API 和工作人员工作台。默认演示账号详见 README。如果本机端口占用，`--port 8001` 并修改小程序 `config.js`。

> **升级 v0.1 → v0.2 的旧开发库**：用户表新增列（锁定/TOTP/token_version 等）、`residents.phone` 换加密类型、新增 `claim_requests` 表。开发库没有自动迁移，请删除旧 `community_health.db` 重新 seed，或用下面的 Alembic 流程在备份上验证后再升级。

### 测试

```bash
cd backend
python -m pytest -q          # 57 passed
python -m compileall -q app
```

### 数据库迁移（Alembic）

```bash
cd backend
# 指向目标库（生产用 PostgreSQL URL）
DATABASE_URL="sqlite:////tmp/mig_check.db" .venv/bin/alembic upgrade head     # 建表
DATABASE_URL="sqlite:////tmp/mig_check.db" .venv/bin/alembic downgrade base   # 回滚（测试已覆盖）
# 新增模型变更后生成迁移脚本：
DATABASE_URL="sqlite:///./_autogen.db" .venv/bin/alembic revision --autogenerate -m "describe change"
```

规则：**任何模型变更必须先出迁移脚本并在临时库验证 up/down**，再进入主干；生产执行迁移前必须完成备份并演练过恢复。开发环境仍以 `create_all` 建新库，生产环境禁止。

### API Schema

运行后访问 `/docs` 查看 FastAPI OpenAPI UI；`/openapi.json` 提供机器可读接口说明。

## Docker：仅限本机演示

```bash
cp backend/.env.example backend/.env
# 在根目录执行：
docker compose -f ops/compose.demo.yml build
docker compose -f ops/compose.demo.yml up -d
# 注意：Docker 容器镜像不包含 seed 脚本，此 compose 默认不灌入演示用户。
# 可按下方方式单独初始化虚构数据，或直接使用本地 Python 启动。
```

镜像已包含 `app/seed.py`（只生成虚构样例）。需要演示登录账号时，首次启动后执行：

```bash
docker compose -f ops/compose.demo.yml exec api python -m app.seed
```

容器仅在 `127.0.0.1:8000` 开放。**不要修改成 `0.0.0.0:8000` 暴露公网**；这个 compose 文件不会实施 TLS、正式鉴权、审计集中化与备份。

## 生产实施路线（仍需人工完成，切勿直接部署）

1. PostgreSQL + Alembic 迁移 + 最小权限账号 + 定期备份与**恢复演练**（迁移脚本已具备，演练未做）。
2. 机构账号开通流程：为员工创建账号（设置 `password_hash`）、按需开通 TOTP（`POST /api/auth/mfa/totp`，认证器绑定走人工流程）；生产自动禁用 `/api/auth/dev-login` 与演示提示。
3. 反向代理/平台提供 HTTPS（应用在生产自动下发 HSTS）；`ALLOWED_HOSTS` 必须配置，否则启动即拒绝。
4. `SECRET_KEY` ≥48 位随机、`FIELD_ENCRYPTION_KEY` 为 32 字节 base64（生产缺一启动即拒绝），均从密钥管理注入；**密钥轮换流程未实现**，当前轮换需人工重加密迁移。
5. 限流为进程内存实现：**多 worker/多实例部署必须替换为共享存储（Redis 等）**，见 `app/ratelimit.py` 模块注释。
6. 微信生产凭据仅在服务端配置；code 已做一次性防重放，仍需真实联调验收。
7. 医疗健康敏感个人信息：存储保护、日志脱敏、数据生命周期制度、合规评审、渗透测试，全部仍是阻塞项。

## 环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `APP_ENV` | development | production/test/development |
| `ENABLE_DEMO_AUTH` | false | 仅 dev/test 可显式开启演示登录与控制台演示提示 |
| `SECRET_KEY` | 空 | JWT 服务端签名密钥，生产强制 ≥48 位 |
| `DATABASE_URL` | sqlite:///./community_health.db | SQLAlchemy URL（生产强制 PostgreSQL） |
| `WECHAT_APPID` | 空 | 小程序 AppID，仅服务端 |
| `WECHAT_APPSECRET` | 空 | 小程序 AppSecret，仅服务端 |
| `CORS_ORIGINS` | 空 | 逗号分隔的跨域来源白名单 |
| `ALLOWED_HOSTS` | 空 | 生产必填；Host 头白名单，缺失启动失败 |
| `SESSION_TTL_MINUTES` | 480 | 访问令牌有效期（分钟，最小 5） |
| `FIELD_ENCRYPTION_KEY` | 空 | 32 字节 base64；生产必填，用于手机号等敏感列 AES-GCM 加密 |
| `LOGIN_MAX_ATTEMPTS` | 5 | 连续失败锁定阈值 |
| `LOGIN_LOCKOUT_MINUTES` | 15 | 锁定时长 |
| `LOGIN_RATE_PER_MINUTE` | 10 | 密码/TOTP 登录每分钟每账号+IP 上限 |
| `WECHAT_LOGIN_RATE_PER_MINUTE` | 10 | 微信登录每分钟每 IP 上限 |

## 回滚

- **代码**：Git 标签 `baseline-v0.1` 起全程留痕，可回退任意提交。
- **数据库**：`alembic downgrade` 可回退迁移（SQLite 上已测试）；**生产回滚前必须先备份**，且 downgrade 不恢复被删除的数据。备份/恢复演练本身仍是未完成项。
