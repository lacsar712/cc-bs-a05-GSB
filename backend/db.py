import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rules import judge_microstrain

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54398/bridgestrain"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strain_readings (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    microstrain double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_strain_readings_status ON strain_readings (status, id);

CREATE TABLE IF NOT EXISTS span_openings (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    requested_by text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    requested_at timestamptz NOT NULL DEFAULT now(),
    confirmed_by text,
    confirmed_at timestamptz
);
-- 同一申请人对同一跨只允许有一条待确认申请
CREATE UNIQUE INDEX IF NOT EXISTS idx_span_openings_pending
    ON span_openings (span_code, requested_by)
    WHERE status = 'pending';
-- 写口闸门走该索引：某跨是否存在已确认开放
CREATE INDEX IF NOT EXISTS idx_span_openings_confirmed
    ON span_openings (span_code)
    WHERE status = 'confirmed';

CREATE TABLE IF NOT EXISTS span_confirmation_log (
    id bigserial PRIMARY KEY,
    opening_id integer NOT NULL REFERENCES span_openings(id),
    span_code text NOT NULL,
    requested_by text NOT NULL,
    confirmed_by text NOT NULL,
    action text NOT NULL DEFAULT 'confirm',
    confirmed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_span_confirmation_log_span
    ON span_confirmation_log (span_code, id);
"""


async def create_pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        conninfo=DSN,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await pool.open()
    return pool


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.commit()


async def seed_if_empty(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) AS n FROM strain_readings")
            row = await cur.fetchone()
            if row["n"] > 0:
                return
            samples = [
                ("跨中S1", 150.0),
                ("支座S2", 40.0),
            ]
            for span_code, microstrain in samples:
                verdict, reason = judge_microstrain(microstrain)
                await cur.execute(
                    """
                    INSERT INTO strain_readings
                        (span_code, microstrain, verdict, reason, status, created_by, processed_at)
                    VALUES (%s, %s, %s, %s, 'done', 'surveyor', now())
                    """,
                    (span_code, microstrain, verdict, reason),
                )
        await conn.commit()


def connect_sync():
    import psycopg

    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM strain_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("跨中S1", 150.0),
        ("支座S2", 40.0),
    ]
    for span_code, microstrain in samples:
        verdict, reason = judge_microstrain(microstrain)
        conn.execute(
            """
            INSERT INTO strain_readings
                (span_code, microstrain, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, 'done', 'surveyor', now())
            """,
            (span_code, microstrain, verdict, reason),
        )
    conn.commit()
