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

-- 跨段开放申请：测量员申请，班组长确认后该跨才可报送
CREATE TABLE IF NOT EXISTS span_applications (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    applicant text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    confirmed_by text,
    confirmed_at timestamptz
);
-- 同一跨段只允许一条待确认申请
CREATE UNIQUE INDEX IF NOT EXISTS idx_span_applications_pending
    ON span_applications (span_code) WHERE status = 'pending';
-- 同一跨段只允许一条已确认记录，杜绝并发重复确认
CREATE UNIQUE INDEX IF NOT EXISTS idx_span_applications_confirmed
    ON span_applications (span_code) WHERE status = 'confirmed';

-- 班组确认流水：确认动作与流水必须在同一事务落库
CREATE TABLE IF NOT EXISTS span_confirmation_ledger (
    id serial PRIMARY KEY,
    application_id integer NOT NULL REFERENCES span_applications (id),
    span_code text NOT NULL,
    applicant text NOT NULL,
    confirmer text NOT NULL,
    action text NOT NULL DEFAULT 'confirm',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_span_confirmation_ledger_app
    ON span_confirmation_ledger (application_id);
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
