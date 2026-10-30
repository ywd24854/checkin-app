"""
比赛打卡记录 · Web 版后端（FastAPI + SQLite）

启动：python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
或双击根目录 run.bat
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import os
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "checkin.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIE = "sid"
SESSION_DAYS = 7            # 管理员会话 7 天
MEMBER_SESSION_DAYS = 30    # 队员会话 30 天：配合前端本机记忆，日常基本不用再输名字
# 上了 HTTPS 就设 CHECKIN_COOKIE_SECURE=1，Cookie 只在 HTTPS 下发送
COOKIE_SECURE = os.getenv("CHECKIN_COOKIE_SECURE", "").strip() == "1"


# ---------------------------------------------------------------- 基础工具
def _secret() -> bytes:
    p = DATA_DIR / "secret.key"
    if not p.exists():
        p.write_bytes(secrets.token_bytes(32))
    return p.read_bytes()


SECRET = _secret()


def hash_pwd(pwd: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    iters = 120_000
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def verify_pwd(pwd: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def make_token(uid: int, role: str) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{uid}.{role}.{exp}"
    sig = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()


def read_token(token: str) -> tuple[int, str] | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        uid, role, exp, sig = raw.split(".")
        payload = f"{uid}.{role}.{exp}"
        want = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, want) or int(exp) < time.time():
            return None
        return int(uid), role
    except Exception:
        return None


def today_str() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------- 数据库
USERS_COLS = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    team TEXT DEFAULT '',
    phone TEXT UNIQUE,
    pwd TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'member',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
"""

CHECKINS_COLS = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    photo TEXT DEFAULT '',
    task TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, day)
"""

SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS users ({USERS_COLS});\n"
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_name ON users(name);\n"
    f"CREATE TABLE IF NOT EXISTS checkins ({CHECKINS_COLS});\n"
    "CREATE INDEX IF NOT EXISTS idx_checkins_day ON checkins(day);\n"
    "CREATE INDEX IF NOT EXISTS idx_checkins_user ON checkins(user_id);\n"
)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 部署到服务器后多进程/并发写更常见：WAL 让读写不互相阻塞，busy_timeout 让写排队而不是直接报错
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate_users(conn: sqlite3.Connection) -> None:
    """把老库（phone/pwd NOT NULL）迁到新结构：注册只填姓名，手机号可空。
    同时兜底修复「上次迁移中途失败留下的 users_old / 指向错误外键」的情况。"""
    info = conn.execute("PRAGMA table_info(users)").fetchall()
    if not info:
        return
    cols = {r["name"]: r for r in info}
    has_old = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users_old'"
    ).fetchone() is not None
    need_nullable = bool(cols.get("phone") and cols["phone"]["notnull"] == 1)
    if not has_old and not need_nullable:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        f"""
        DROP TABLE IF EXISTS users_new;
        CREATE TABLE users_new ({USERS_COLS});
        INSERT OR IGNORE INTO users_new (id, name, team, phone, pwd, role, active, created_at)
            SELECT id, name, COALESCE(team, ''), NULLIF(phone, ''), COALESCE(pwd, ''),
                   role, active, created_at
            FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        """
    )
    if has_old:
        # 上次 RENAME 把 checkins 的外键指到了 users_old，重建 checkins 指回 users
        conn.executescript(
            f"""
            DROP TABLE IF EXISTS checkins_new;
            CREATE TABLE checkins_new ({CHECKINS_COLS});
            INSERT INTO checkins_new SELECT * FROM checkins;
            DROP TABLE checkins;
            ALTER TABLE checkins_new RENAME TO checkins;
            DROP TABLE IF EXISTS users_old;
            """
        )
    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_users_name ON users(name);
        CREATE INDEX IF NOT EXISTS idx_checkins_day ON checkins(day);
        CREATE INDEX IF NOT EXISTS idx_checkins_user ON checkins(user_id);
        """
    )
    try:
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('users_old','users_new','checkins_new')")
    except sqlite3.Error:
        pass
    conn.execute("PRAGMA foreign_keys = ON")


def init_db() -> None:
    with db() as conn:
        _migrate_users(conn)
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT id, phone FROM users WHERE role='admin' LIMIT 1").fetchone()
        if not row:
            # 首次建库的初始密码可用环境变量覆盖，避免默认 123456 上线
            init_pwd = os.getenv("CHECKIN_ADMIN_PASSWORD", "123456")
            init_pwd = init_pwd if len(init_pwd) >= 4 else "123456"
            conn.execute(
                "INSERT INTO users (name, team, phone, pwd, role, active, created_at) VALUES (?,?,?,?,?,1,?)",
                ("管理员", "", "admin", hash_pwd(init_pwd), "admin", datetime.now().isoformat(timespec="seconds")),
            )
        elif not (row["phone"] or "").strip():
            # 账号字段被清空的兜底，否则管理员登录不了
            conn.execute("UPDATE users SET phone='admin' WHERE id=?", (row["id"],))


init_db()


# ---------------------------------------------------------------- 鉴权依赖
def user_from_request(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    info = read_token(token)
    if not info:
        return None
    uid, role = info
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
    return row


def need_user(request: Request) -> sqlite3.Row:
    row = user_from_request(request)
    if not row:
        raise HTTPException(status_code=401, detail="请先登录")
    return row


def need_admin(request: Request) -> sqlite3.Row:
    row = need_user(request)
    if row["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return row


# ---------------------------------------------------------------- 照片处理
def save_photo(upload: UploadFile, name: str, day: str) -> str:
    raw = upload.file.read()
    if not raw:
        return ""
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="图片格式不支持")
    # 长边压到 1600
    img.thumbnail((1600, 1600))
    # 右下角水印：日期 + 姓名
    try:
        draw = ImageDraw.Draw(img)
        text = f"{day}  {name}"
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", max(18, img.width // 40))
        except Exception:
            font = ImageFont.load_default()
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        pad = 10
        x, y = img.width - tw - pad * 2, img.height - th - pad * 2
        draw.rectangle([x - pad, y - pad, x + tw + pad, y + th + pad], fill=(0, 0, 0))
        draw.text((x, y), text, fill=(255, 255, 255), font=font)
    except Exception:
        pass
    sub = UPLOAD_DIR / day.replace("-", "")[:6]
    sub.mkdir(parents=True, exist_ok=True)
    fname = f"{day}_{secrets.token_hex(6)}.jpg"
    path = sub / fname
    img.save(path, "JPEG", quality=85)
    return str(path.relative_to(DATA_DIR)).replace("\\", "/")


def photo_url(rel: str) -> str:
    return f"/files/{rel}" if rel else ""


# ---------------------------------------------------------------- 应用
app = FastAPI(title="比赛打卡记录", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/files/{rel:path}")
def get_file(rel: str):
    target = (DATA_DIR / rel).resolve()
    if not str(target).startswith(str(DATA_DIR.resolve())) or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


def page(name: str) -> HTMLResponse:
    # no-store：改完前端刷新即生效，不需要用户按 Ctrl+F5 清缓存
    return HTMLResponse(
        (APP_DIR / "static" / name).read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    row = user_from_request(request)
    if row and row["role"] == "admin":
        return page("admin.html")
    return page("index.html")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return page("login.html")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    row = user_from_request(request)
    if not row or row["role"] != "admin":
        return HTMLResponse('<meta http-equiv="refresh" content="0;url=/login">')
    return page("admin.html")


# ---------------------------------------------------------------- 认证 API
def _enter_by_name(name: str, team: str = "") -> tuple[sqlite3.Row, bool, sqlite3.Connection]:
    """姓名即身份：不存在就建档，存在就直接进。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "请填写姓名")
    if len(name) > 24:
        raise HTTPException(400, "姓名请控制在 24 字以内")
    if name.lower() == "admin":
        raise HTTPException(400, "该姓名被管理员占用")
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
    if row:
        if not row["active"]:
            conn.close()
            raise HTTPException(403, "该姓名已被停用，请联系管理员")
        return row, False, conn
    conn.execute(
        "INSERT INTO users (name, team, phone, pwd, role, active, created_at) VALUES (?,?,NULL,'','member',1,?)",
        (name, (team or "").strip(), datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
    return row, True, conn


@app.post("/api/enter")
def api_enter(response: Response, name: str = Form(...), team: str = Form("")):
    """队员入口：只填姓名（首次即注册）。"""
    row, created, conn = _enter_by_name(name, team)
    conn.close()
    response.set_cookie(
        COOKIE,
        make_token(row["id"], row["role"]),
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=MEMBER_SESSION_DAYS * 86400,
    )
    return {"ok": True, "created": created, "name": row["name"], "role": row["role"]}


@app.post("/api/register")
def api_register(response: Response, name: str = Form(...), team: str = Form("")):
    """兼容旧调用：注册即进入，只要姓名。"""
    return api_enter(response, name, team)


@app.post("/api/login")
def api_login(response: Response, identity: str = Form(...), password: str = Form("")):
    """管理员登录：严格校验，账号不存在或密码错误一律拒绝，绝不建档。

    队员进入请走 /api/enter（只填姓名）。这里曾经有个兜底分支会把未匹配的账号
    当队员建档，导致"管理员登录输错账号，后台却多出一个队员"。
    """
    ident = (identity or "").strip()
    if not ident:
        raise HTTPException(400, "请输入管理员账号")
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE role='admin' AND phone=?", (ident,)).fetchone()
    if not row:
        raise HTTPException(400, "管理员账号不存在（默认账号 admin）")
    if not verify_pwd(password, row["pwd"] or ""):
        raise HTTPException(400, "管理员密码错误")
    if not row["active"]:
        raise HTTPException(403, "该账号已停用")
    response.set_cookie(
        COOKIE,
        make_token(row["id"], row["role"]),
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=SESSION_DAYS * 86400,
    )
    return {"ok": True, "role": "admin", "name": row["name"], "account": row["phone"]}


@app.post("/api/logout")
def api_logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    row = user_from_request(request)
    if not row:
        raise HTTPException(401, "未登录")
    return {
        "id": row["id"],
        "name": row["name"],
        "team": row["team"],
        "role": row["role"],
        "account": row["phone"] or "",
    }


@app.post("/api/admin/account")
def admin_update_account(
    request: Request,
    current_password: str = Form(...),
    new_account: str = Form(""),
    new_password: str = Form(""),
):
    """修改管理员自己的账号 / 密码（需要当前密码）。"""
    me = need_admin(request)
    if not verify_pwd(current_password or "", me["pwd"] or ""):
        raise HTTPException(400, "当前密码不正确")
    new_account = (new_account or "").strip()
    new_password = (new_password or "").strip()
    if not new_account and not new_password:
        raise HTTPException(400, "新账号和新密码至少填一项")
    if new_account == (me["phone"] or ""):
        new_account = ""
    if new_account:
        if not (2 <= len(new_account) <= 32) or not all(
            c.isascii() and (c.isalnum() or c in "_.-") for c in new_account
        ):
            raise HTTPException(400, "账号只能用 2~32 位字母、数字、下划线、点或短横线")
    if new_password and len(new_password) < 4:
        raise HTTPException(400, "新密码至少 4 位")

    with db() as conn:
        if new_account:
            clash = conn.execute(
                "SELECT 1 FROM users WHERE id<>? AND (phone=? OR name=?)", (me["id"], new_account, new_account)
            ).fetchone()
            if clash:
                raise HTTPException(400, "该账号与已有成员姓名或账号重复，换一个")
            conn.execute("UPDATE users SET phone=? WHERE id=?", (new_account, me["id"]))
        if new_password:
            conn.execute("UPDATE users SET pwd=? WHERE id=?", (hash_pwd(new_password), me["id"]))
    return {"ok": True, "account": new_account or (me["phone"] or ""), "password_changed": bool(new_password)}


# ---------------------------------------------------------------- 打卡 API
@app.post("/api/checkin")
def api_checkin(
    request: Request,
    task: str = Form(""),
    note: str = Form(""),
    photo: Optional[UploadFile] = File(None),
):
    me = need_user(request)
    day = today_str()
    rel = ""
    if photo is not None and photo.filename:
        rel = save_photo(photo, me["name"], day)

    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        old = conn.execute("SELECT * FROM checkins WHERE user_id=? AND day=?", (me["id"], day)).fetchone()
        if old:
            conn.execute(
                "UPDATE checkins SET photo=?, task=?, note=?, updated_at=? WHERE id=?",
                (rel or old["photo"], task.strip(), note.strip(), now, old["id"]),
            )
        else:
            if not rel:
                raise HTTPException(400, "首次打卡必须上传照片")
            conn.execute(
                "INSERT INTO checkins (user_id, day, photo, task, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (me["id"], day, rel, task.strip(), note.strip(), now, now),
            )
    return {"ok": True, "day": day, "photo": photo_url(rel)}


@app.get("/api/my/checkins")
def api_my_checkins(request: Request, month: str = ""):
    me = need_user(request)
    like = f"{month}%" if month else "%"
    with db() as conn:
        rows = conn.execute(
            "SELECT day, photo, task, note, updated_at FROM checkins WHERE user_id=? AND day LIKE ? ORDER BY day DESC LIMIT 200",
            (me["id"], like),
        ).fetchall()
    return {"items": [{**dict(r), "photo_url": photo_url(r["photo"])} for r in rows]}


@app.get("/api/my/today")
def api_my_today(request: Request):
    me = need_user(request)
    with db() as conn:
        r = conn.execute("SELECT * FROM checkins WHERE user_id=? AND day=?", (me["id"], today_str())).fetchone()
    if not r:
        return {"done": False}
    return {"done": True, "task": r["task"], "note": r["note"], "photo_url": photo_url(r["photo"])}


# ---------------------------------------------------------------- 管理员：成员
@app.get("/api/admin/members")
def admin_members(request: Request, q: str = ""):
    need_admin(request)
    with db() as conn:
        if q:
            rows = conn.execute(
                "SELECT * FROM users WHERE role='member' AND (name LIKE ? OR team LIKE ? OR phone LIKE ?) ORDER BY id DESC",
                (f"%{q}%", f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM users WHERE role='member' ORDER BY id DESC").fetchall()
        counts = {
            r["user_id"]: r["c"]
            for r in conn.execute("SELECT user_id, COUNT(*) c FROM checkins GROUP BY user_id").fetchall()
        }
    out = []
    for r in rows:
        d = dict(r)
        d.pop("pwd", None)
        d["checkin_count"] = counts.get(r["id"], 0)
        out.append(d)
    return {"items": out}


@app.post("/api/admin/members")
def admin_add_member(
    request: Request,
    name: str = Form(...),
    team: str = Form(""),
    phone: str = Form(""),
    password: str = Form(""),
):
    need_admin(request)
    name, team, phone = name.strip(), team.strip(), phone.strip()
    if not name:
        raise HTTPException(400, "姓名必填")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone():
            raise HTTPException(400, "该姓名已存在")
        if phone and conn.execute("SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone():
            raise HTTPException(400, "该手机号已存在")
        conn.execute(
            "INSERT INTO users (name, team, phone, pwd, role, active, created_at) VALUES (?,?,?,?, 'member',1,?)",
            (name, team, phone or None, hash_pwd(password) if password else "", datetime.now().isoformat(timespec="seconds")),
        )
    return {"ok": True}


@app.post("/api/admin/members/import")
async def admin_import_members(request: Request, file: UploadFile = File(...)):
    """CSV 列：姓名[,队伍][,手机号][,密码] —— 只有姓名也认；重名跳过。"""
    need_admin(request)
    raw = (await file.read()).decode("utf-8-sig", errors="ignore")
    reader = csv.reader(io.StringIO(raw))
    added, skipped = 0, 0
    with db() as conn:
        for i, cells in enumerate(reader):
            cells = [c.strip() for c in cells if c is not None]
            if not cells or not cells[0]:
                skipped += 1
                continue
            if i == 0 and any(k in cells[0] for k in ("姓名", "名字", "name")):
                continue
            name = cells[0]
            team = cells[1] if len(cells) >= 2 else ""
            phone = cells[2] if len(cells) >= 3 else ""
            pwd = cells[3] if len(cells) >= 4 and cells[3] else ""
            if conn.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone():
                skipped += 1
                continue
            if phone and conn.execute("SELECT 1 FROM users WHERE phone=?", (phone,)).fetchone():
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO users (name, team, phone, pwd, role, active, created_at) VALUES (?,?,?,?, 'member',1,?)",
                (name, team, phone or None, hash_pwd(pwd) if pwd else "", datetime.now().isoformat(timespec="seconds")),
            )
            added += 1
    return {"ok": True, "added": added, "skipped": skipped}


@app.post("/api/admin/members/{uid}/reset")
def admin_reset_pwd(request: Request, uid: int, password: str = Form("123456")):
    need_admin(request)
    with db() as conn:
        conn.execute("UPDATE users SET pwd=? WHERE id=? AND role='member'", (hash_pwd(password or "123456"), uid))
    return {"ok": True}


@app.post("/api/admin/members/{uid}/toggle")
def admin_toggle(request: Request, uid: int):
    need_admin(request)
    with db() as conn:
        row = conn.execute("SELECT active FROM users WHERE id=? AND role='member'", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "成员不存在")
        conn.execute("UPDATE users SET active=? WHERE id=?", (0 if row["active"] else 1, uid))
    return {"ok": True, "active": 0 if row["active"] else 1}


@app.post("/api/admin/members/{uid}/delete")
def admin_delete_member(request: Request, uid: int):
    """彻底移除成员：连带删除其打卡记录与本地照片文件。管理员账号不可移除。"""
    need_admin(request)
    with db() as conn:
        row = conn.execute("SELECT id, name, role FROM users WHERE id=?", (uid,)).fetchone()
        if not row or row["role"] != "member":
            raise HTTPException(404, "成员不存在（管理员账号不可移除）")
        photos = [r["photo"] for r in conn.execute("SELECT photo FROM checkins WHERE user_id=?", (uid,)).fetchall()]
        n = conn.execute("SELECT COUNT(*) c FROM checkins WHERE user_id=?", (uid,)).fetchone()["c"]
        conn.execute("DELETE FROM checkins WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=? AND role='member'", (uid,))

    deleted_photos = 0
    root = DATA_DIR.resolve()
    for rel in photos:
        if not rel:
            continue
        target = (DATA_DIR / rel).resolve()
        try:
            if str(target).startswith(str(root)) and target.is_file():
                target.unlink()
                deleted_photos += 1
        except OSError:
            pass
    return {"ok": True, "name": row["name"], "deleted_checkins": n, "deleted_photos": deleted_photos}


# ---------------------------------------------------------------- 管理员：记录 / 统计 / 导出
def _query_checkins(conn, start: str, end: str, uid: int = 0, q: str = ""):
    sql = (
        "SELECT c.id, c.day, c.photo, c.task, c.note, c.updated_at, u.name, u.team, u.phone "
        "FROM checkins c JOIN users u ON u.id=c.user_id WHERE c.day>=? AND c.day<=?"
    )
    args: list = [start, end]
    if uid:
        sql += " AND c.user_id=?"
        args.append(uid)
    if q:
        sql += " AND (u.name LIKE ? OR u.team LIKE ? OR c.task LIKE ?)"
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY c.day DESC, u.name"
    return conn.execute(sql, args).fetchall()


@app.get("/api/admin/checkins")
def admin_checkins(request: Request, start: str = "", end: str = "", uid: int = 0, q: str = ""):
    need_admin(request)
    end = end or today_str()
    start = start or (date.today() - timedelta(days=6)).isoformat()
    with db() as conn:
        rows = _query_checkins(conn, start, end, uid, q)
    return {
        "items": [
            {**dict(r), "photo_url": photo_url(r["photo"])} for r in rows
        ],
        "range": [start, end],
    }


@app.get("/api/admin/stats")
def admin_stats(request: Request, day: str = ""):
    need_admin(request)
    day = day or today_str()
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM users WHERE role='member' AND active=1").fetchone()["c"]
        done = conn.execute("SELECT COUNT(*) c FROM checkins WHERE day=?", (day,)).fetchone()["c"]
        miss = conn.execute(
            "SELECT u.name, u.team FROM users u WHERE u.role='member' AND u.active=1 "
            "AND u.id NOT IN (SELECT user_id FROM checkins WHERE day=?) ORDER BY u.name",
            (day,),
        ).fetchall()
    return {"day": day, "total": total, "done": done, "missing": [dict(r) for r in miss]}


@app.get("/api/admin/export")
def admin_export(request: Request, start: str = "", end: str = "", fmt: str = "xlsx"):
    need_admin(request)
    end = end or today_str()
    start = start or (date.today() - timedelta(days=29)).isoformat()
    with db() as conn:
        rows = _query_checkins(conn, start, end)
        total_members = conn.execute("SELECT COUNT(*) c FROM users WHERE role='member' AND active=1").fetchone()["c"]

    header = ["日期", "姓名", "队伍", "手机号", "当日完成任务", "备注", "提交时间", "照片地址", "是否打卡"]
    records = []
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    stat_by_user: dict[str, int] = {}
    for r in rows:
        stat_by_user[r["name"]] = stat_by_user.get(r["name"], 0) + 1

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        for r in rows:
            w.writerow(
                [r["day"], r["name"], r["team"], r["phone"], r["task"], r["note"], r["updated_at"], photo_url(r["photo"]), "是"]
            )
        data = ("\ufeff" + buf.getvalue()).encode("utf-8")
        fname = f"打卡汇总_{start}_{end}.csv"
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
        )

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "打卡记录"
    ws.append(header)
    for r in rows:
        ws.append(
            [r["day"], r["name"], r["team"], r["phone"], r["task"], r["note"], r["updated_at"], photo_url(r["photo"]), "是"]
        )
    for i, col in enumerate("ABCDEFGHI", start=1):
        ws.column_dimensions[col].width = [12, 10, 12, 14, 40, 24, 20, 46, 8][i - 1]

    ws2 = wb.create_sheet("人统计")
    ws2.append(["姓名", "队伍", "手机号", "打卡天数", "统计区间天数", "出勤率"])
    with db() as conn:
        members = conn.execute("SELECT name, team, phone FROM users WHERE role='member' AND active=1 ORDER BY name").fetchall()
    for m in members:
        c = stat_by_user.get(m["name"], 0)
        ws2.append([m["name"], m["team"], m["phone"], c, days, f"{c / days:.0%}" if days else "-"])
    for i, col in enumerate("ABCDEF", start=1):
        ws2.column_dimensions[col].width = [10, 12, 14, 10, 14, 10][i - 1]

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"打卡汇总_{start}_{end}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )
