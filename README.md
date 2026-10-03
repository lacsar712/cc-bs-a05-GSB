# 桥梁应变班交台

测量员上报跨段编号与微应变读数，后台工人用 `FOR UPDATE SKIP LOCKED` 认领待处理队列，按 **80～220 με** 判定 **合格** 或 **越界**。

**班组确认闭环**：测量员先在「班组确认」专页申请开放某跨段，班组长账号确认后该跨才可报送；未确认前网页与直连接口一律挡回。确认动作与确认流水在同一事务落库，申请人不得自我确认。专页左列为待确认跨段，右列为已确认清单并下挂确认流水。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python Sanic + psycopg（异步连接池） |
| 工人 | `worker.py`（psycopg 同步，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Mithril.js + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3198 |
| 接口 | http://localhost:8198 |
| PostgreSQL | localhost:54398（库名 `bridgestrain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| surveyor | surv123456 | 测量员，可申请开放跨段、提交读数 |
| leader_a | lead123456 | 甲班组长，可确认跨段开放申请 |
| reviewer | rev123456 | 复核员兼班组长，可确认但不可报送 |

## 班组确认流程

1. 测量员 `POST /api/span-applications` 申请开放跨段（状态 `pending`）。
2. 班组长 `POST /api/span-applications/<id>/confirm` 确认；确认与流水（`span_confirmation_ledger`）一次落库。
3. 确认前 `POST /api/readings` 对该跨段返回 **409**；确认后报送进入 `pending` 候审队列，由后台工人判定。

## 启动

```bash
cd projects/19-bridge-strain-shift
docker compose up --build
```

健康检查：`GET http://localhost:8198/api/health` → `{"status":"ok","service":"bridge-strain-shift"}`

## 种子数据

| 跨段 | 微应变 | 结论 |
|------|--------|------|
| 跨中S1 | 150 με | 合格 |
| 支座S2 | 40 με | 越界 |

## 本地开发（可选）

```bash
cd backend && pip install -r requirements.txt
python -m sanic api.app --host=0.0.0.0 --port=8000 --single-process
python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8198**。
