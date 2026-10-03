import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sanic import Sanic
from sanic.response import json as sanic_json

from db import create_pool, ensure_schema, seed_if_empty

SECRET = os.environ.get("JWT_SECRET", "bridge-strain-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# writer=测量员（可申请开放、可报送）；reader=复核员（只读）；leader=班组长（可确认）。
# 复核员 reviewer 同时兼班组长：可确认，但没有 writer 角色故不能报送。
USERS = {
    "surveyor": {"roles": ["writer"], "password_hash": pwd.hash("surv123456")},
    "reviewer": {"roles": ["reader", "leader"], "password_hash": pwd.hash("rev123456")},
    "foreman": {"roles": ["leader"], "password_hash": pwd.hash("lead123456")},
}

ROLE_LABELS = {"writer": "测量员", "reader": "复核员", "leader": "班组长"}

app = Sanic("bridge-strain-shift")


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@app.exception(ApiError)
async def _api_error(_request, exc):
    return sanic_json({"detail": exc.detail}, status=exc.status)


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


def _require_user(request) -> dict | None:
    return _decode_user(_auth_header(request))


def _has_role(user: dict | None, role: str) -> bool:
    return bool(user) and role in user["roles"]


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


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
        {
            "access_token": token,
            "username": username,
            "roles": user["roles"],
            "role_labels": [ROLE_LABELS.get(r, r) for r in user["roles"]],
        }
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
    if not _has_role(user, "writer"):
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
            # 写口闸门：该跨必须存在已被班组长确认的开放记录，网页与直连一律在此挡回。
            await cur.execute(
                """
                SELECT 1
                FROM span_openings
                WHERE span_code = %s AND status = 'confirmed'
                LIMIT 1
                """,
                (span_code,),
            )
            if await cur.fetchone() is None:
                return sanic_json(
                    {
                        "detail": f"跨段 {span_code} 尚未经班组长确认开放，"
                        "请先在班组确认页申请开放"
                    },
                    status=403,
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
            "message": "已入队，后台工人将认领并判定",
        },
        status=201,
    )


def _opening_out(r) -> dict:
    return {
        "id": r["id"],
        "span_code": r["span_code"],
        "requested_by": r["requested_by"],
        "status": r["status"],
        "requested_at": _iso(r["requested_at"]),
        "confirmed_by": r["confirmed_by"],
        "confirmed_at": _iso(r["confirmed_at"]),
    }


@app.get("/api/span-requests")
async def list_span_requests(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, span_code, requested_by, status,
                       requested_at, confirmed_by, confirmed_at
                FROM span_openings
                WHERE status = 'pending'
                ORDER BY id
                """
            )
            pending = [_opening_out(r) for r in await cur.fetchall()]
            await cur.execute(
                """
                SELECT o.id, o.span_code, o.requested_by, o.status,
                       o.requested_at, o.confirmed_by, o.confirmed_at,
                       l.id AS log_id, l.confirmed_by AS log_confirmed_by,
                       l.action AS log_action, l.confirmed_at AS log_confirmed_at
                FROM span_openings o
                LEFT JOIN span_confirmation_log l ON l.opening_id = o.id
                WHERE o.status = 'confirmed'
                ORDER BY o.confirmed_at DESC, o.id DESC, l.id
                """
            )
            confirmed = {}
            for r in await cur.fetchall():
                opening = confirmed.setdefault(r["id"], _opening_out(r))
                opening.setdefault("log", [])
                if r["log_id"] is not None:
                    opening["log"].append(
                        {
                            "id": r["log_id"],
                            "action": r["log_action"],
                            "confirmed_by": r["log_confirmed_by"],
                            "confirmed_at": _iso(r["log_confirmed_at"]),
                        }
                    )
    return sanic_json(
        {
            "pending": pending,
            "confirmed": list(confirmed.values()),
            "can_confirm": _has_role(user, "leader"),
            "can_apply": _has_role(user, "writer"),
        }
    )


@app.post("/api/span-requests")
async def create_span_request(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if not _has_role(user, "writer"):
        return sanic_json({"detail": "仅测量员可申请开放跨段"}, status=403)
    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 该跨已被确认开放则无需重复申请
            await cur.execute(
                """
                SELECT 1 FROM span_openings
                WHERE span_code = %s AND status = 'confirmed'
                LIMIT 1
                """,
                (span_code,),
            )
            if await cur.fetchone() is not None:
                return sanic_json(
                    {"detail": f"跨段 {span_code} 已确认开放，可直接报送读数"},
                    status=409,
                )
            await cur.execute(
                """
                SELECT 1 FROM span_openings
                WHERE span_code = %s AND requested_by = %s AND status = 'pending'
                """,
                (span_code, user["username"]),
            )
            if await cur.fetchone() is not None:
                return sanic_json(
                    {"detail": f"跨段 {span_code} 的开放申请已提交，等待班组长确认"},
                    status=409,
                )
            try:
                await cur.execute(
                    """
                    INSERT INTO span_openings (span_code, requested_by, status, requested_at)
                    VALUES (%s, %s, 'pending', now())
                    RETURNING id, span_code, requested_by, status,
                              requested_at, confirmed_by, confirmed_at
                    """,
                    (span_code, user["username"]),
                )
                row = await cur.fetchone()
                await conn.commit()
            except Exception:
                # 并发下可能撞 pending 唯一索引
                await conn.rollback()
                return sanic_json(
                    {"detail": f"跨段 {span_code} 的开放申请已存在，等待班组长确认"},
                    status=409,
                )
    return sanic_json(_opening_out(row), status=201)


@app.post("/api/span-requests/<opening_id:int>/confirm")
async def confirm_span_request(request, opening_id: int):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if not _has_role(user, "leader"):
        return sanic_json({"detail": "仅班组长可确认开放申请"}, status=403)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            try:
                # 状态翻转与流水写入必须一次落库：同一事务内提交，禁止半截。
                async with conn.transaction():
                    await cur.execute(
                        """
                        SELECT id, span_code, requested_by, status
                        FROM span_openings
                        WHERE id = %s
                        FOR UPDATE
                        """,
                        (opening_id,),
                    )
                    opening = await cur.fetchone()
                    if opening is None:
                        raise ApiError(404, "开放申请不存在")
                    if opening["status"] != "pending":
                        raise ApiError(409, "该申请已被确认")
                    # 申请人不得自我确认
                    if opening["requested_by"] == user["username"]:
                        raise ApiError(403, "申请人不得自我确认，请由其他班组长确认")
                    await cur.execute(
                        """
                        UPDATE span_openings
                        SET status = 'confirmed',
                            confirmed_by = %s,
                            confirmed_at = now()
                        WHERE id = %s
                        """,
                        (user["username"], opening_id),
                    )
                    await cur.execute(
                        """
                        INSERT INTO span_confirmation_log
                            (opening_id, span_code, requested_by, confirmed_by, action, confirmed_at)
                        VALUES (%s, %s, %s, %s, 'confirm', now())
                        """,
                        (
                            opening_id,
                            opening["span_code"],
                            opening["requested_by"],
                            user["username"],
                        ),
                    )
                    await cur.execute(
                        """
                        SELECT id, span_code, requested_by, status,
                               requested_at, confirmed_by, confirmed_at
                        FROM span_openings
                        WHERE id = %s
                        """,
                        (opening_id,),
                    )
                    row = await cur.fetchone()
                await conn.commit()
            except ApiError:
                raise
    return sanic_json(_opening_out(row))


@app.get("/api/span-confirmation-log")
async def list_confirmation_log(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, opening_id, span_code, requested_by,
                       confirmed_by, action, confirmed_at
                FROM span_confirmation_log
                ORDER BY id DESC
                """
            )
            rows = await cur.fetchall()
    out = [
        {
            "id": r["id"],
            "opening_id": r["opening_id"],
            "span_code": r["span_code"],
            "requested_by": r["requested_by"],
            "confirmed_by": r["confirmed_by"],
            "action": r["action"],
            "confirmed_at": _iso(r["confirmed_at"]),
        }
        for r in rows
    ]
    return sanic_json(out)
