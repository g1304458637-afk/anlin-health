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

### 测试

```bash
cd backend
python -m pytest -q
python -m compileall -q app
```

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

## 生产实施路线（尚未实现，切勿直接部署）

1. 用 PostgreSQL 替换 SQLite，完成 Alembic 迁移和恢复演练。`APP_ENV=production` 会拒绝 SQLite。
2. 用正式认证替换演示账号（生产会禁用 `/api/auth/dev-login`）。建立职工 MFA/密码策略/账号审批。
3. 使用成熟反向代理/容器平台提供 HTTPS、域名、速率限制、健康探针、安全响应头。
4. 配置 `SECRET_KEY` 至少 48 位不可预测随机字符，并通过密钥管理系统注入；验证密钥轮换与 token 失效。
5. 微信生产凭据只能在服务端配置，校验 OpenID、code 一次性、每次请求授权范围。
6. 对医疗健康敏感个人信息建立存储保护、日志脱敏、细粒度 RBAC/ABAC、数据最小化、生命周期制度。
7. 通过机构合规/安全评审、微信小程序审核、性能/安全测试；准备监控告警和应急预案。

## 环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `APP_ENV` | development | production/test/development |
| `ENABLE_DEMO_AUTH` | false | 仅 dev/test 可显式开启演示登录 |
| `SECRET_KEY` | 空 | JWT 服务端签名密钥，生产强制校验 |
| `DATABASE_URL` | sqlite:///./community_health.db | SQLAlchemy URL |
| `WECHAT_APPID` | 空 | 小程序 AppID，仅服务端 |
| `WECHAT_APPSECRET` | 空 | 小程序 AppSecret，仅服务端 |
| `CORS_ORIGINS` | 空 | 逗号分隔的跨域来源白名单 |

## 回滚

当前仅能通过 Git 源码回滚；还没有数据库迁移/回滚流程。因此**严禁用当前代码进行正式环境的数据库升级**。后续 AI 必须先补迁移、数据库备份、灾难恢复演练、结构兼容验证。
