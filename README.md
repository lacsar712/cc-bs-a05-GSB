# 桥梁应变班交台

测量员上报跨段编号与微应变读数，后台工人用 `FOR UPDATE SKIP LOCKED` 认领待处理队列，按 **80～220 με** 判定 **合格** 或 **越界**。

## 班组确认闸门

跨段必须先经班组长确认开放，测量员才能报送该跨的读数：

1. 测量员在顶栏 **班组确认** 页申请开放某跨（进入左列“待确认跨段”）。
2. 班组长账号在左列点 **确认开放**，该跨进入右列“已确认清单”并下挂确认流水。
3. 确认前该跨的报送一律挡回——**网页表单与直连 API 同一个写口拦截**，不存在绕过。
4. 申请人不得自我确认；确认动作与流水写入在同一数据库事务内一次落库，禁止半截。
5. 复核员若兼班组长可执行确认，但因没有测量员角色仍不能报送读数。

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
| surveyor | surv123456 | 测量员（writer），可申请开放跨段、报送读数 |
| reviewer | rev123456 | 复核员（reader）兼班组长（leader），只读读数、可确认，不能报送 |
| foreman | lead123456 | 班组长（leader），可确认开放申请 |

## 接口

| 方法 路径 | 说明 |
|-----------|------|
| `POST /api/auth/login` | 登录，返回多角色 JWT |
| `GET /api/readings` | 读数列表 |
| `POST /api/readings` | 报送读数（写口：该跨须存在 `confirmed` 开放记录，否则 403） |
| `GET /api/span-requests` | 待确认/已确认清单（含权限标记与下挂流水） |
| `POST /api/span-requests` | 测量员申请开放某跨 |
| `POST /api/span-requests/<id>/confirm` | 班组长确认（leader 限定、禁止自确认、状态与流水单事务） |
| `GET /api/span-confirmation-log` | 全部确认流水 |

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
