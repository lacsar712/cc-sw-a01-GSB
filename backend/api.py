import os
from datetime import datetime, timedelta, timezone

import psycopg
from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, patch
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext
from psycopg.rows import dict_row
from pydantic import BaseModel

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54395/spectrum")
SECRET = os.environ.get("JWT_SECRET", "spectrum-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "calibrator": {"role": "writer", "password_hash": pwd.hash("calib123456")},
    "inspector": {"role": "reader", "password_hash": pwd.hash("insp123456")},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS lamps (
    id serial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    calibratable boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    retired_by text,
    retired_at timestamptz
);
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    lamp text NOT NULL,
    lamp_id integer REFERENCES lamps(id),
    nominal_nm double precision NOT NULL,
    measured_nm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
"""
# 既有库补列（首次建库时 SCHEMA 已含 lamp_id，重复执行无副作用）
MIGRATIONS = (
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lamp_id integer REFERENCES lamps(id)",
)


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


def now():
    return datetime.now(timezone.utc)


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    lamp_id: int | None = None
    nominal_nm: float
    measured_nm: float


class LampIn(BaseModel):
    name: str
    calibratable: bool = False


class LampPatchIn(BaseModel):
    calibratable: bool | None = None
    active: bool | None = None


def user_from_request(request: Request) -> dict:
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = jwt.decode(auth[7:], SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict) -> None:
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅校准员可操作")


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "spectrum-wavelength-desk"}


@post("/api/login")
async def login(data: LoginIn) -> dict:
    u = USERS.get(data.username)
    if not u or not pwd.verify(data.password, u["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    token = jwt.encode(
        {
            "sub": data.username,
            "role": u["role"],
            "exp": now() + timedelta(hours=12),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "role": u["role"], "username": data.username}


LAMP_COLS = (
    "id, name, calibratable, active, created_by, created_at, retired_by, retired_at"
)


@get("/api/lamps")
async def list_lamps(request: Request) -> dict:
    """灯种牌：上格挂牌（可校准维护），下格摘牌履历。巡检员可看。"""
    user_from_request(request)
    with connect() as conn:
        active = conn.execute(
            f"SELECT {LAMP_COLS} FROM lamps WHERE active ORDER BY id"
        ).fetchall()
        retired = conn.execute(
            f"SELECT {LAMP_COLS} FROM lamps WHERE NOT active ORDER BY retired_at DESC, id DESC"
        ).fetchall()
        return {"active": list(active), "retired": list(retired)}


@post("/api/lamps")
async def create_lamp(request: Request, data: LampIn) -> dict:
    """校准员建档：写固定称呼并圈可校准。建档即挂牌。"""
    user = user_from_request(request)
    require_writer(user)
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="固定称呼不能为空")
    with connect() as conn:
        exists = conn.execute("SELECT id, active FROM lamps WHERE name=%s", (name,)).fetchone()
        if exists:
            if exists["active"]:
                raise HTTPException(status_code=400, detail=f"灯种牌「{name}」已建档挂牌")
            raise HTTPException(status_code=400, detail=f"灯种牌「{name}」在摘牌履历中，固定称呼不可复用")
        row = conn.execute(
            """
            INSERT INTO lamps(name, calibratable, active, created_by, created_at)
            VALUES (%s,%s,true,%s,%s) RETURNING id
            """,
            (name, data.calibratable, user["username"], now()),
        ).fetchone()
        conn.commit()
        return {"id": row["id"], "name": name, "calibratable": data.calibratable, "active": True}


@patch("/api/lamps/{lamp_id:int}")
async def patch_lamp(request: Request, lamp_id: int, data: LampPatchIn) -> dict:
    """上格维护：圈/取消可校准、摘牌。固定称呼不可改，摘牌不可复挂。"""
    user = user_from_request(request)
    require_writer(user)
    with connect() as conn:
        lamp = conn.execute(f"SELECT {LAMP_COLS} FROM lamps WHERE id=%s", (lamp_id,)).fetchone()
        if not lamp:
            raise HTTPException(status_code=404, detail="灯种牌不存在")
        if not lamp["active"]:
            raise HTTPException(status_code=400, detail="该灯种牌已摘牌，不能维护或复挂")
        if data.active is False:
            conn.execute(
                "UPDATE lamps SET active=false, retired_by=%s, retired_at=%s WHERE id=%s",
                (user["username"], now(), lamp_id),
            )
        elif data.calibratable is not None:
            conn.execute(
                "UPDATE lamps SET calibratable=%s WHERE id=%s",
                (data.calibratable, lamp_id),
            )
        conn.commit()
        row = conn.execute(f"SELECT {LAMP_COLS} FROM lamps WHERE id=%s", (lamp_id,)).fetchone()
        return dict(row)


@get("/api/jobs")
async def list_jobs(request: Request) -> list:
    user_from_request(request)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, lamp, lamp_id, nominal_nm, measured_nm, status, verdict, reason, created_by FROM jobs ORDER BY id DESC"
        ).fetchall()
        return list(rows)


@get("/api/jobs/{job_id:int}")
async def get_job(request: Request, job_id: int) -> dict:
    user_from_request(request)
    with connect() as conn:
        row = conn.execute(
            "SELECT id, lamp, lamp_id, nominal_nm, measured_nm, status, verdict, reason, created_by FROM jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在")
        return dict(row)


@post("/api/jobs")
async def create_job(request: Request, data: JobIn) -> dict:
    """入队必须绑灯种牌：只收仍挂牌且圈了可校准的灯。称呼随单落盘即冻结。"""
    user = user_from_request(request)
    require_writer(user)
    if data.lamp_id is None:
        raise HTTPException(status_code=400, detail="没圈可校准灯：请先在灯种牌上格圈选一块牌再提交")
    with connect() as conn:
        lamp = conn.execute("SELECT id, name, calibratable, active FROM lamps WHERE id=%s", (data.lamp_id,)).fetchone()
        if not lamp:
            raise HTTPException(status_code=400, detail="没圈可校准灯：所选灯种牌不存在")
        if not lamp["active"]:
            raise HTTPException(status_code=400, detail=f"灯种牌「{lamp['name']}」已摘牌，不能提交")
        if not lamp["calibratable"]:
            raise HTTPException(status_code=400, detail=f"灯种牌「{lamp['name']}」未圈可校准，不能提交")
        # lamp 列为随单落盘的称呼快照，此后摘牌或改牌均不影响本单
        row = conn.execute(
            """
            INSERT INTO jobs(lamp, lamp_id, nominal_nm, measured_nm, status, verdict, reason, created_by, created_at)
            VALUES (%s,%s,%s,%s,'pending','','',%s,%s) RETURNING id
            """,
            (lamp["name"], lamp["id"], data.nominal_nm, data.measured_nm, user["username"], now()),
        ).fetchone()
        conn.commit()
        return {"id": row["id"], "status": "pending", "lamp": lamp["name"]}


def on_startup() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)
        for stmt in MIGRATIONS:
            conn.execute(stmt)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            ts = now()
            conn.execute(
                """
                INSERT INTO jobs(lamp, lamp_id, nominal_nm, measured_nm, status, verdict, reason, created_by, created_at)
                VALUES
                ('氦灯-587', NULL, 587.56, 587.50, 'done', '合格', '偏差 0.0600 nm 在允差内', 'seed', %s),
                ('汞灯-546', NULL, 546.07, 546.30, 'done', '超差', '偏差 0.2300 nm 超过允差 0.08', 'seed', %s)
                """,
                (ts, ts),
            )
        conn.commit()


app = Litestar(
    route_handlers=[health, login, list_lamps, create_lamp, patch_lamp, list_jobs, get_job, create_job],
    on_startup=[on_startup],
)
