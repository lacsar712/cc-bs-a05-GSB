import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from psycopg import errors as pg_errors
from sanic import Sanic
from sanic.response import json as sanic_json

from db import create_pool, ensure_schema, seed_if_empty

SECRET = os.environ.get("JWT_SECRET", "bridge-strain-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 角色说明：
#   surveyor 测量员——可报送读数、申请开放跨段
#   leader    班组长——可确认跨段开放申请
#   reviewer  复核员——只读列表；若兼班组长则可确认，但仍不可报送
USERS = {
    "surveyor": {"roles": ["surveyor"], "password_hash": pwd.hash("surv123456")},
    "leader_a": {"roles": ["leader"], "password_hash": pwd.hash("lead123456")},
    "reviewer": {"roles": ["reviewer", "leader"], "password_hash": pwd.hash("rev123456")},
}

app = Sanic("bridge-strain-shift")


def _auth_header(request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    roles = payload.get("roles")
    if not isinstance(roles, list):
        roles = []
    return {"username": sub, "roles": roles}


def _require_user(request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        return None
    return user


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _application_json(row: dict, ledger: list | None = None) -> dict:
    return {
        "id": row["id"],
        "span_code": row["span_code"],
        "status": row["status"],
        "applicant": row["applicant"],
        "created_at": _iso(row["created_at"]),
        "confirmed_by": row["confirmed_by"],
        "confirmed_at": _iso(row["confirmed_at"]),
        "ledger": ledger or [],
    }


@app.before_server_start
async def setup(_app, _loop):
    pool = await create_pool()
    _app.ctx.pool = pool
    await ensure_schema(pool)
    await seed_if_empty(pool)


@app.after_server_stop
async def teardown(_app, _loop):
    pool = _app.ctx.pool
    if pool:
        await pool.close()


@app.get("/api/health")
async def health(_request):
    return sanic_json({"status": "ok", "service": "bridge-strain-shift"})


@app.post("/api/auth/login")
async def login(request):
    body = request.json or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return sanic_json({"detail": "用户名或密码错误"}, status=401)
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "roles": user["roles"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return sanic_json(
        {"access_token": token, "username": username, "roles": user["roles"]}
    )


@app.get("/api/readings")
async def list_readings(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, span_code, microstrain, verdict, reason, status,
                       created_by, created_at, processed_at
                FROM strain_readings
                ORDER BY id DESC
                """
            )
            rows = await cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "span_code": r["span_code"],
                "microstrain": r["microstrain"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": _iso(r["created_at"]),
                "processed_at": _iso(r["processed_at"]),
            }
        )
    return sanic_json(out)


@app.post("/api/readings")
async def create_reading(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if "surveyor" not in user["roles"]:
        return sanic_json({"detail": "仅测量员可提交应变读数"}, status=403)
    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)
    try:
        microstrain = float(body.get("microstrain"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "微应变必须是数字"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 写口拦截：跨段未经班组长确认开放前，一律挡回（网页与直连同此口径）
            await cur.execute(
                """
                SELECT 1 FROM span_applications
                WHERE span_code = %s AND status = 'confirmed'
                LIMIT 1
                """,
                (span_code,),
            )
            if not await cur.fetchone():
                return sanic_json(
                    {
                        "detail": f"跨段 {span_code} 尚未经班组长确认开放，请先在班组确认页申请"
                    },
                    status=409,
                )
            await cur.execute(
                """
                INSERT INTO strain_readings (span_code, microstrain, status, created_by, created_at)
                VALUES (%s, %s, 'pending', %s, now())
                RETURNING id, span_code, microstrain, verdict, reason, status,
                          created_by, created_at, processed_at
                """,
                (span_code, microstrain, user["username"]),
            )
            row = await cur.fetchone()
        await conn.commit()

    return sanic_json(
        {
            "id": row["id"],
            "span_code": row["span_code"],
            "microstrain": row["microstrain"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": _iso(row["created_at"]),
            "processed_at": None,
            "message": "已入队候审，后台工人将认领并判定",
        },
        status=201,
    )


@app.get("/api/span-applications")
async def list_span_applications(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    status_filter = str(request.args.get("status", "")).strip()
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            if status_filter in ("pending", "confirmed"):
                await cur.execute(
                    """
                    SELECT id, span_code, status, applicant, created_at,
                           confirmed_by, confirmed_at
                    FROM span_applications
                    WHERE status = %s
                    ORDER BY id
                    """,
                    (status_filter,),
                )
            else:
                await cur.execute(
                    """
                    SELECT id, span_code, status, applicant, created_at,
                           confirmed_by, confirmed_at
                    FROM span_applications
                    ORDER BY id
                    """
                )
            apps = await cur.fetchall()
            await cur.execute(
                """
                SELECT id, application_id, span_code, applicant, confirmer,
                       action, created_at
                FROM span_confirmation_ledger
                ORDER BY id
                """
            )
            ledger_rows = await cur.fetchall()

    ledger_by_app: dict[int, list] = {}
    for entry in ledger_rows:
        ledger_by_app.setdefault(entry["application_id"], []).append(
            {
                "id": entry["id"],
                "span_code": entry["span_code"],
                "applicant": entry["applicant"],
                "confirmer": entry["confirmer"],
                "action": entry["action"],
                "created_at": _iso(entry["created_at"]),
            }
        )
    out = [
        _application_json(row, ledger_by_app.get(row["id"], [])) for row in apps
    ]
    return sanic_json(out)


@app.post("/api/span-applications")
async def create_span_application(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT status, applicant FROM span_applications
                WHERE span_code = %s AND status IN ('pending', 'confirmed')
                LIMIT 1
                """,
                (span_code,),
            )
            existing = await cur.fetchone()
            if existing and existing["status"] == "confirmed":
                return sanic_json(
                    {"detail": f"跨段 {span_code} 已由班组长确认开放，可直接报送"},
                    status=409,
                )
            if existing:
                return sanic_json(
                    {
                        "detail": f"跨段 {span_code} 已有待确认申请（申请人 {existing['applicant']}），请等待班组长确认"
                    },
                    status=409,
                )
            try:
                await cur.execute(
                    """
                    INSERT INTO span_applications (span_code, status, applicant, created_at)
                    VALUES (%s, 'pending', %s, now())
                    RETURNING id, span_code, status, applicant, created_at,
                              confirmed_by, confirmed_at
                    """,
                    (span_code, user["username"]),
                )
                row = await cur.fetchone()
            except pg_errors.UniqueViolation:
                # 并发下另一请求已抢先申请同一跨段
                return sanic_json(
                    {"detail": f"跨段 {span_code} 已存在申请，请刷新后查看"},
                    status=409,
                )
        await conn.commit()

    return sanic_json(_application_json(row), status=201)


@app.post("/api/span-applications/<app_id:int>/confirm")
async def confirm_span_application(request, app_id: int):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if "leader" not in user["roles"]:
        return sanic_json({"detail": "仅班组长账号可确认"}, status=403)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        # 确认动作与确认流水在同一事务落库，禁止半截
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, span_code, status, applicant
                    FROM span_applications
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (app_id,),
                )
                row = await cur.fetchone()
                if not row:
                    return sanic_json({"detail": "申请不存在"}, status=404)
                if row["status"] != "pending":
                    return sanic_json(
                        {"detail": "该申请已确认，请勿重复操作"}, status=409
                    )
                if row["applicant"] == user["username"]:
                    return sanic_json(
                        {"detail": "申请人不得自我确认"}, status=403
                    )
                await cur.execute(
                    """
                    UPDATE span_applications
                    SET status = 'confirmed', confirmed_by = %s, confirmed_at = now()
                    WHERE id = %s
                    """,
                    (user["username"], app_id),
                )
                await cur.execute(
                    """
                    INSERT INTO span_confirmation_ledger
                        (application_id, span_code, applicant, confirmer, action, created_at)
                    VALUES (%s, %s, %s, %s, 'confirm', now())
                    RETURNING id, created_at
                    """,
                    (app_id, row["span_code"], row["applicant"], user["username"]),
                )
                ledger_row = await cur.fetchone()

    return sanic_json(
        {
            "id": app_id,
            "span_code": row["span_code"],
            "status": "confirmed",
            "confirmed_by": user["username"],
            "ledger_id": ledger_row["id"],
            "message": f"跨段 {row['span_code']} 已确认开放，测量员可报送",
        }
    )
