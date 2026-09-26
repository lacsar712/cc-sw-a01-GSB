import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from passlib.context import CryptContext
from psycopg.errors import UniqueViolation
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
CREATE TABLE IF NOT EXISTS lamp_tags (
    id serial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    calibratable boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    created_by text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    delisted_by text NOT NULL DEFAULT '',
    delisted_at timestamptz
);
CREATE TABLE IF NOT EXISTS lamp_events (
    id serial PRIMARY KEY,
    lamp_tag_id integer NOT NULL REFERENCES lamp_tags(id),
    action text NOT NULL,
    calibratable boolean NOT NULL,
    actor text NOT NULL,
    at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    lamp text NOT NULL,
    lamp_tag_id integer REFERENCES lamp_tags(id),
    nominal_nm double precision NOT NULL,
    measured_nm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lamp_tag_id integer;
"""


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value):
    return value.isoformat() if value else None


def tag_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "calibratable": row["calibratable"],
        "active": row["active"],
        "created_by": row["created_by"],
        "created_at": iso(row.get("created_at")),
        "delisted_by": row.get("delisted_by") or "",
        "delisted_at": iso(row.get("delisted_at")),
    }


def event_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "lamp_tag_id": row["lamp_tag_id"],
        "lamp_name": row["lamp_name"],
        "action": row["action"],
        "calibratable": row["calibratable"],
        "actor": row["actor"],
        "at": iso(row["at"]),
    }


def reject_submit(tag: dict | None) -> str | None:
    """提交时只允许圈了可校准且仍在牌的灯种；否则给出可读拒收理由。"""
    if tag is None:
        return "未绑定灯种牌，请先在灯种牌页建档并挂灯种牌"
    if not tag["active"]:
        return f"灯种牌「{tag['name']}」已摘牌，禁止提交"
    if not tag["calibratable"]:
        return f"灯种牌「{tag['name']}」没圈可校准，禁止提交"
    return None


class PostgresRepo:
    def __init__(self, conn):
        self.conn = conn

    def list_tags(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM lamp_tags ORDER BY active DESC, id ASC"
        ).fetchall()
        return [tag_dict(r) for r in rows]

    def get_tag(self, tag_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM lamp_tags WHERE id = %s", (tag_id,)).fetchone()
        return tag_dict(row) if row else None

    def create_tag(self, name: str, calibratable: bool, actor: str, ts: datetime) -> dict:
        try:
            row = self.conn.execute(
                """
                INSERT INTO lamp_tags(name, calibratable, active, created_by, created_at)
                VALUES (%s, %s, true, %s, %s)
                RETURNING *
                """,
                (name, calibratable, actor, ts),
            ).fetchone()
        except UniqueViolation as exc:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=f"灯种牌「{name}」已存在") from exc
        self.conn.execute(
            "INSERT INTO lamp_events(lamp_tag_id, action, calibratable, actor, at) VALUES (%s,'create',%s,%s,%s)",
            (row["id"], calibratable, actor, ts),
        )
        self.conn.commit()
        return tag_dict(row)

    def delist_tag(self, tag_id: int, actor: str, ts: datetime) -> dict:
        tag = self.get_tag(tag_id)
        if tag is None:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="灯种牌不存在")
        if not tag["active"]:
            raise HTTPException(status_code=HTTP_409_CONFLICT, detail=f"灯种牌「{tag['name']}」已摘牌，无需重复摘牌")
        self.conn.execute(
            "UPDATE lamp_tags SET active=false, delisted_by=%s, delisted_at=%s WHERE id=%s",
            (actor, ts, tag_id),
        )
        self.conn.execute(
            "INSERT INTO lamp_events(lamp_tag_id, action, calibratable, actor, at) VALUES (%s,'delist',%s,%s,%s)",
            (tag_id, tag["calibratable"], actor, ts),
        )
        self.conn.commit()
        return self.get_tag(tag_id)

    def list_events(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT e.id, e.lamp_tag_id, t.name AS lamp_name, e.action, e.calibratable, e.actor, e.at
            FROM lamp_events e JOIN lamp_tags t ON t.id = e.lamp_tag_id
            ORDER BY e.id DESC
            """
        ).fetchall()
        return [event_dict(r) for r in rows]

    def list_jobs(self) -> list[dict]:
        return list(self.conn.execute(
            """
            SELECT id, lamp, lamp_tag_id, nominal_nm, measured_nm, status, verdict, reason, created_by
            FROM jobs ORDER BY id DESC
            """
        ).fetchall())

    def get_job(self, job_id: int) -> dict | None:
        return self.conn.execute(
            """
            SELECT id, lamp, lamp_tag_id, nominal_nm, measured_nm, status, verdict, reason, created_by
            FROM jobs WHERE id = %s
            """,
            (job_id,),
        ).fetchone()

    def create_job(self, lamp_tag_id: int, nominal_nm: float, measured_nm: float, actor: str, ts: datetime) -> int:
        tag = self.get_tag(lamp_tag_id)
        reason = reject_submit(tag)
        if reason:
            # 拒收不落盘
            raise HTTPException(status_code=400, detail=reason)
        row = self.conn.execute(
            """
            INSERT INTO jobs(lamp, lamp_tag_id, nominal_nm, measured_nm, status, verdict, reason, created_by, created_at)
            VALUES (%s,%s,%s,%s,'pending','','',%s,%s) RETURNING id
            """,
            # lamp 取牌上称呼随单落盘，此后冻结，摘牌不改旧单
            (tag["name"], lamp_tag_id, nominal_nm, measured_nm, actor, ts),
        ).fetchone()
        self.conn.commit()
        return row["id"]

    def seed(self, ts: datetime) -> None:
        n_tags = self.conn.execute("SELECT COUNT(*) AS n FROM lamp_tags").fetchone()["n"]
        if n_tags == 0:
            he = self.conn.execute(
                "INSERT INTO lamp_tags(name, calibratable, active, created_by, created_at) VALUES ('氦灯-587', true, true, 'seed', %s) RETURNING id",
                (ts,),
            ).fetchone()["id"]
            hg = self.conn.execute(
                "INSERT INTO lamp_tags(name, calibratable, active, created_by, created_at) VALUES ('汞灯-546', true, true, 'seed', %s) RETURNING id",
                (ts,),
            ).fetchone()["id"]
            self.conn.execute(
                "INSERT INTO lamp_events(lamp_tag_id, action, calibratable, actor, at) VALUES (%s,'create',true,'seed',%s),(%s,'create',true,'seed',%s)",
                (he, ts, hg, ts),
            )
        n_jobs = self.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n_jobs == 0:
            he = self.conn.execute("SELECT id FROM lamp_tags WHERE name='氦灯-587'").fetchone()["id"]
            hg = self.conn.execute("SELECT id FROM lamp_tags WHERE name='汞灯-546'").fetchone()["id"]
            self.conn.execute(
                """
                INSERT INTO jobs(lamp, lamp_tag_id, nominal_nm, measured_nm, status, verdict, reason, created_by, created_at)
                VALUES
                ('氦灯-587', %s, 587.56, 587.50, 'done', '合格', '偏差 0.0600 nm 在允差内', 'seed', %s),
                ('汞灯-546', %s, 546.07, 546.30, 'done', '超差', '偏差 0.2300 nm 超过允差 0.08', 'seed', %s)
                """,
                (he, ts, hg, ts),
            )
        self.conn.commit()


@contextmanager
def open_repo():
    with connect() as conn:
        yield PostgresRepo(conn)


class LoginIn(BaseModel):
    username: str
    password: str


class TagIn(BaseModel):
    name: str
    calibratable: bool = False


class JobIn(BaseModel):
    lamp_tag_id: int
    nominal_nm: float
    measured_nm: float


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


def require_writer(request: Request) -> dict:
    user = user_from_request(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="巡检员只读：不能建档、摘牌或提交")
    return user


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
            "exp": datetime.now(timezone.utc) + timedelta(hours=12),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "role": u["role"], "username": data.username}


@get("/api/lamp-tags")
async def list_tags(request: Request) -> list:
    user_from_request(request)
    with open_repo() as repo:
        return repo.list_tags()


@post("/api/lamp-tags")
async def create_tag(request: Request, data: TagIn) -> dict:
    user = require_writer(request)
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="称呼不能为空")
    with open_repo() as repo:
        return repo.create_tag(name, data.calibratable, user["username"], now())


@post("/api/lamp-tags/{tag_id:int}/delist")
async def delist_tag(request: Request, tag_id: int) -> dict:
    user = require_writer(request)
    with open_repo() as repo:
        return repo.delist_tag(tag_id, user["username"], now())


@get("/api/lamp-events")
async def list_events(request: Request) -> list:
    user_from_request(request)
    with open_repo() as repo:
        return repo.list_events()


@get("/api/jobs")
async def list_jobs(request: Request) -> list:
    user_from_request(request)
    with open_repo() as repo:
        return repo.list_jobs()


@get("/api/jobs/{job_id:int}")
async def get_job(request: Request, job_id: int) -> dict:
    user_from_request(request)
    with open_repo() as repo:
        row = repo.get_job(job_id)
        if not row:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="任务不存在")
        return dict(row)


@post("/api/jobs")
async def create_job(request: Request, data: JobIn) -> dict:
    user = require_writer(request)
    with open_repo() as repo:
        job_id = repo.create_job(data.lamp_tag_id, data.nominal_nm, data.measured_nm, user["username"], now())
        return {"id": job_id, "status": "pending"}


def on_startup() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)
        conn.commit()
        PostgresRepo(conn).seed(now())


app = Litestar(
    route_handlers=[
        health,
        login,
        list_tags,
        create_tag,
        delist_tag,
        list_events,
        list_jobs,
        get_job,
        create_job,
    ],
    on_startup=[on_startup],
)
