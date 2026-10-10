#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAHIR PANEL — Single-file Flask backend
Owner / Agent / User role system + Bot lifecycle + Watchdog
"""

import os, re, sys, time, json, signal, sqlite3, secrets, shutil, subprocess, threading
from datetime import datetime, timedelta
from functools import wraps
from queue import Queue, Empty

from flask import (Flask, render_template, render_template_string, request,
                   redirect, url_for, session, jsonify, flash, send_file, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
import psutil
import requests as rq

load_dotenv()

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════
BASE = os.path.dirname(os.path.abspath(__file__))
DB_FILE       = os.path.join(BASE, os.environ.get("DB_FILE", "users.db"))
BOT_SOURCE    = os.path.join(BASE, os.environ.get("BOT_SOURCE", "mahir.py"))
BOT_DIR       = os.path.join(BASE, os.environ.get("BOT_DIR", "bot_files"))
SECRET_KEY    = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
OWNER_USER    = os.environ.get("OWNER_USERNAME", "owner")
OWNER_HASH    = os.environ.get("OWNER_PASSWORD_HASH", "")
WATCH_INTERVAL= int(os.environ.get("WATCHDOG_INTERVAL", 15))
MAX_RAPID     = int(os.environ.get("MAX_RAPID_RESTARTS", 5))
COOLDOWN      = int(os.environ.get("RESTART_COOLDOWN", 60))

os.makedirs(BOT_DIR, exist_ok=True)

ROLE_OWNER = "owner"
ROLE_AGENT = "agent"
ROLE_USER  = "user"

# ══════════════════════════════════════════════════════════════
# FLASK APP
# ══════════════════════════════════════════════════════════════
app = Flask(__name__, template_folder=BASE, static_folder=None)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,       # set True behind HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(days=1),
    WTF_CSRF_TIME_LIMIT=None,
)
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["300 per minute"])

# ══════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════
def _conn():
    c = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    return c

class DB:
    def __enter__(self): self.c = _conn(); return self.c
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type: self.c.rollback()
            else: self.c.commit()
        finally: self.c.close()

def _has_col(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def init_db():
    with DB() as c:
        cur = c.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            email TEXT,
            registration_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_admin INTEGER DEFAULT 0,
            admin_uid TEXT,
            bot_uid TEXT,
            bot_pw TEXT,
            bot_file TEXT,
            bot_pid INTEGER,
            bot_status TEXT DEFAULT 'not_configured'
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            used_by TEXT,
            used_at TIMESTAMP,
            is_used INTEGER DEFAULT 0,
            expiry_date TIMESTAMP
        )''')
        # migrations
        for col, ddl in [
            ("role", "TEXT DEFAULT 'user'"),
            ("parent_agent", "TEXT"),
            ("subscription_expiry", "TIMESTAMP"),
            ("is_disabled", "INTEGER DEFAULT 0"),
            ("disable_reason", "TEXT"),
            ("personal_notice", "TEXT"),
            ("max_keys", "INTEGER DEFAULT 0"),
            ("last_seen", "TIMESTAMP"),
        ]:
            if not _has_col(cur, "users", col):
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        if not _has_col(cur, "keys", "agent"):
            cur.execute("ALTER TABLE keys ADD COLUMN agent TEXT")

        cur.execute('''CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            actor TEXT, actor_role TEXT, action TEXT, target TEXT,
            result TEXT, ip TEXT, meta TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT, key TEXT,
            start_date TIMESTAMP, expiry_date TIMESTAMP,
            renewed_by TEXT, agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT, target TEXT, message TEXT,
            enabled INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS global_state (
            key TEXT PRIMARY KEY, value TEXT
        )''')
        cur.execute("INSERT OR IGNORE INTO global_state (key,value) VALUES ('global_bot_stop','0')")

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════
ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mK]')
def clean_ansi(s):
    if not s: return ""
    return ANSI_RE.sub('', s).strip()

def safe_name(s):
    return re.sub(r'[^A-Za-z0-9_]', '_', s)

def audit(action, target="", result="ok", meta=""):
    try:
        actor = session.get("username", "anon")
        role  = session.get("role", "guest")
        ip    = request.remote_addr if request else ""
        with DB() as c:
            c.execute("INSERT INTO audit_log (actor,actor_role,action,target,result,ip,meta) VALUES (?,?,?,?,?,?,?)",
                      (actor, role, action, target, result, ip, meta))
    except Exception: pass

def get_global_stop():
    with DB() as c:
        r = c.execute("SELECT value FROM global_state WHERE key='global_bot_stop'").fetchone()
    return bool(r and r[0] == "1")

def set_global_stop(v):
    with DB() as c:
        c.execute("UPDATE global_state SET value=? WHERE key='global_bot_stop'", ("1" if v else "0",))

def get_global_notice():
    with DB() as c:
        r = c.execute("SELECT message FROM notices WHERE scope='global' AND enabled=1 LIMIT 1").fetchone()
    return r["message"] if r else ""

def is_expired(expiry_iso, role):
    if role in (ROLE_OWNER, ROLE_AGENT): return False
    if not expiry_iso: return False
    try:
        return datetime.fromisoformat(expiry_iso) < datetime.now()
    except Exception:
        return False

# ══════════════════════════════════════════════════════════════
# AUTH DECORATORS
# ══════════════════════════════════════════════════════════════
def _is_api():
    return request.path.startswith("/api/") or request.is_json

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("username"):
            return (jsonify({"error":"login required"}),401) if _is_api() else redirect(url_for("page_login"))
        return f(*a, **k)
    return w

def owner_required(f):
    @wraps(f)
    def w(*a, **k):
        if session.get("role") != ROLE_OWNER:
            return (jsonify({"error":"owner only"}),403) if _is_api() else redirect(url_for("page_login"))
        return f(*a, **k)
    return w

def agent_or_owner(f):
    @wraps(f)
    def w(*a, **k):
        if session.get("role") not in (ROLE_OWNER, ROLE_AGENT):
            return (jsonify({"error":"agent/owner only"}),403) if _is_api() else redirect(url_for("page_login"))
        return f(*a, **k)
    return w

# ══════════════════════════════════════════════════════════════
# PROCESS MONITOR
# ══════════════════════════════════════════════════════════════
class ProcessMonitor:
    def __init__(self, user_id, username, bot_file):
        self.user_id   = user_id
        self.username  = username
        self.bot_file  = bot_file
        self.process   = None
        self.is_running= False
        self.start_time= None
        self.restart_count = 0
        self.rapid_restarts= 0
        self.last_restart_ts = 0.0
        self.user_stopped = False
        self.lock = threading.Lock()

        self.output_lines, self.error_lines, self.message_lines = [], [], []
        self.max_out, self.max_err, self.max_msg = 300, 500, 200

        self.bot_uid = self.bot_name = self.bot_region = "N/A"
        self.bot_status = "🔴 OFFLINE"
        self.last_sender_uid = self.last_guild_name = self.last_nickname = "N/A"
        self.last_message = self.last_pfp_url = "N/A"
        self.cpu_history = [0] * 20
        self.ram_history = [0] * 20

    def _db(self, pid=None, status=None):
        try:
            with DB() as c:
                if pid is not None:
                    c.execute("UPDATE users SET bot_pid=?,bot_status=? WHERE id=?",
                              (pid, status or "running", self.user_id))
                elif status is not None:
                    c.execute("UPDATE users SET bot_status=? WHERE id=?", (status, self.user_id))
        except Exception: pass

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return True
            if not os.path.exists(self.bot_file):
                self.bot_status = "⚠️ NO FILE"; self._db(status="no_file"); return False
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-u", self.bot_file],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, errors="replace",
                    start_new_session=True,
                )
            except Exception as e:
                self.bot_status = f"❌ ERROR: {e}"; self._db(status="error"); return False
            self.is_running = True
            self.start_time = datetime.now()
            self.bot_status = "🟢 RUNNING"
            self.user_stopped = False
            threading.Thread(target=self._reader, daemon=True).start()
            self._db(pid=self.process.pid, status="running")
            return True

    def _reader(self):
        try:
            for line in iter(self.process.stdout.readline, ''):
                if not line: break
                ts = datetime.now().strftime('%H:%M:%S')
                line = line.rstrip()
                with self.lock:
                    self.output_lines.append(f"[{ts}] {line}")
                    if len(self.output_lines) > self.max_out:
                        self.output_lines = self.output_lines[-self.max_out:]
                    if self._is_err(line):
                        self.error_lines.append(f"[{ts}] {line}")
                        self.error_lines = self.error_lines[-self.max_err:]
                self._parse(line)
        except Exception: pass

    @staticmethod
    def _is_err(line):
        l = line.lower()
        return any(k in l for k in ("error","exception","traceback","failed","critical",
                                    "fatal","timeout","refused","denied","keyerror",
                                    "attributeerror","typeerror","valueerror","indexerror"))

    def _parse(self, line):
        c = clean_ansi(line)
        if not c: return
        for pat, field, conv in [
            (r'UID\s*[:：]\s*(\d+)', 'bot_uid', str),
            (r'NAME\s*[:：]\s*(.+?)$', 'bot_name', lambda v: v[:60]),
            (r'REGION\s*[:：]\s*(\w+)', 'bot_region', lambda v: v.upper()),
            (r'Sender UID\s*[:：]\s*(\d+)', 'last_sender_uid', str),
        ]:
            m = re.search(pat, c, re.I)
            if m:
                with self.lock: setattr(self, field, conv(m.group(1).strip()))
        m = re.search(r'Nickname\s*[:：]\s*(.+?)$', c, re.I)
        if m:
            with self.lock: self.last_nickname = m.group(1).strip()[:60]
        m = re.search(r'Guild Name\s*[:：]\s*(.+?)$', c, re.I)
        if m:
            with self.lock: self.last_guild_name = m.group(1).strip()[:60]
        m = re.search(r'PFP URL\s*[:：]\s*(https?://\S+)', c, re.I)
        if m:
            with self.lock: self.last_pfp_url = m.group(1)[:200]
        m = re.search(r'Message\s*[:：]\s*(.+?)$', c, re.I)
        if m:
            with self.lock:
                self.last_message = m.group(1).strip()[:200]
                self.message_lines.append({
                    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "data": {
                        "sender_uid": self.last_sender_uid,
                        "nickname":   self.last_nickname,
                        "message":    self.last_message,
                        "guild_name": self.last_guild_name,
                        "pfp_url":    self.last_pfp_url,
                    }
                })
                self.message_lines = self.message_lines[-self.max_msg:]
        if "LOGIN SUCCESSFUL" in c.upper():
            with self.lock: self.bot_status = "🟢 ACTIVE & ONLINE"

    def stop(self, by_user=True):
        with self.lock:
            if by_user: self.user_stopped = True
            if self.process:
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except Exception:
                    try: self.process.terminate()
                    except Exception: pass
                try: self.process.wait(timeout=5)
                except Exception:
                    try:
                        pgid = os.getpgid(self.process.pid)
                        os.killpg(pgid, signal.SIGKILL)
                    except Exception:
                        try: self.process.kill()
                        except Exception: pass
                self.process = None
            self.is_running = False
            self.bot_status = "🔴 OFFLINE"
            self._db(pid=None, status="stopped")

    def restart(self, by_watchdog=False):
        if by_watchdog:
            now = time.time()
            if now - self.last_restart_ts < 30: self.rapid_restarts += 1
            else: self.rapid_restarts = 0
            self.last_restart_ts = now
            if self.rapid_restarts > MAX_RAPID:
                time.sleep(min(COOLDOWN, 60))
                self.rapid_restarts = 0
        self.stop(by_user=False)
        time.sleep(1)
        ok = self.start()
        if ok: self.restart_count += 1
        return ok

    def status(self):
        alive = self.process is not None and self.process.poll() is None
        uptime = "00:00:00"
        if alive and self.start_time:
            uptime = str(datetime.now() - self.start_time).split('.')[0]
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            ram = psutil.virtual_memory().percent
            try: disk = psutil.disk_usage('/').percent
            except Exception: disk = psutil.disk_usage(os.path.expanduser("~")).percent
        except Exception:
            cpu = ram = disk = 0
        with self.lock:
            self.cpu_history.append(cpu); self.cpu_history = self.cpu_history[-20:]
            self.ram_history.append(ram); self.ram_history = self.ram_history[-20:]
            return {
                "is_running": alive,
                "uptime": uptime,
                "restart_count": self.restart_count,
                "cpu": cpu, "ram": ram, "disk": disk,
                "cpu_history": self.cpu_history, "ram_history": self.ram_history,
                "logs": self.output_lines[-200:],
                "error_logs": self.error_lines[-100:],
                "message_history": self.message_lines[-50:],
                "bot_uid": self.bot_uid, "bot_name": self.bot_name,
                "bot_region": self.bot_region, "bot_status": self.bot_status,
                "last_sender_uid": self.last_sender_uid,
                "last_guild_name": self.last_guild_name,
                "last_nickname": self.last_nickname,
                "last_message": self.last_message,
                "last_pfp_url": self.last_pfp_url,
            }

    def clear_errors(self):
        with self.lock: self.error_lines = []
    def clear_messages(self):
        with self.lock: self.message_lines = []

# ══════════════════════════════════════════════════════════════
# MONITOR REGISTRY + WATCHDOG
# ══════════════════════════════════════════════════════════════
monitors = {}
monitors_lock = threading.Lock()

def get_monitor(user_id, username=None, bot_file=None):
    with monitors_lock:
        if user_id in monitors: return monitors[user_id]
        if bot_file is None or username is None:
            with DB() as c:
                r = c.execute("SELECT username, bot_file FROM users WHERE id=?", (user_id,)).fetchone()
                if not r: return None
                username, bot_file = r["username"], r["bot_file"]
        if not bot_file: return None
        path = bot_file if os.path.isabs(bot_file) else os.path.join(BOT_DIR, bot_file)
        if not os.path.exists(path): return None
        m = ProcessMonitor(user_id, username, path)
        monitors[user_id] = m
        return m

def drop_monitor(user_id):
    with monitors_lock:
        m = monitors.pop(user_id, None)
    if m:
        try: m.stop(by_user=True)
        except Exception: pass

class Watchdog:
    def __init__(self):
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
    def start(self): self.thread.start()
    def stop(self):  self._stop.set()
    def _loop(self):
        while not self._stop.wait(WATCH_INTERVAL):
            try: self._tick()
            except Exception as e: print("[watchdog]", e)
    def _tick(self):
        global_stop = get_global_stop()
        with DB() as c:
            users = c.execute("""SELECT id, username, bot_file, is_disabled, role,
                                        subscription_expiry FROM users
                                 WHERE bot_file IS NOT NULL""").fetchall()
        for u in users:
            monitor = get_monitor(u["id"], u["username"], u["bot_file"])
            if not monitor: continue
            expired = is_expired(u["subscription_expiry"], u["role"])
            if expired:
                if monitor.is_running: monitor.stop(by_user=False)
                monitor.bot_status = "⏰ EXPIRED"
                monitor._db(status="expired")
                continue
            if u["is_disabled"] or global_stop:
                if monitor.is_running: monitor.stop(by_user=False)
                continue
            if monitor.user_stopped: continue
            if not monitor.is_running:
                monitor.restart(by_watchdog=True)

watchdog = Watchdog()

# ══════════════════════════════════════════════════════════════
# BOT DEPLOYMENT
# ══════════════════════════════════════════════════════════════
def deploy_bot(user_id, username, admin_uid, bot_uid, bot_pw):
    fname = f"{safe_name(username)}_mahir.py"
    path = os.path.join(BOT_DIR, fname)
    if not os.path.exists(BOT_SOURCE):
        with open(BOT_SOURCE, "w") as f:
            f.write("# Mahir bot\nUid, Pw = 'default', 'default'\nADMIN_UIDS = []\n")
    shutil.copy2(BOT_SOURCE, path)
    with open(path) as f: content = f.read()
    content = re.sub(r"Uid,\s*Pw\s*=\s*'[^']*',\s*'[^']*'",
                     f"Uid, Pw = '{bot_uid}', '{bot_pw}'", content)
    content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]",
                     f"ADMIN_UIDS = ['{admin_uid}']", content)
    with open(path, "w") as f: f.write(content)
    with DB() as c:
        c.execute("""UPDATE users SET bot_file=?, admin_uid=?, bot_uid=?, bot_pw=?,
                     bot_status='configured' WHERE id=?""",
                  (fname, admin_uid, bot_uid, bot_pw, user_id))
    return fname

# ══════════════════════════════════════════════════════════════
# PAGE ROUTES
# ══════════════════════════════════════════════════════════════
@app.route("/")
def page_index():
    r = session.get("role")
    if r == ROLE_OWNER: return redirect(url_for("page_owner"))
    if r == ROLE_AGENT: return redirect(url_for("page_agent"))
    if r == ROLE_USER:  return redirect(url_for("page_user"))
    return redirect(url_for("page_login"))

@app.route("/login", methods=["GET","POST"])
@limiter.limit("15 per minute", methods=["POST"])
def page_login():
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        # env owner
        if u == OWNER_USER and OWNER_HASH and check_password_hash(OWNER_HASH, p):
            session.permanent = True
            session["user_id"] = -1
            session["username"] = u
            session["role"] = ROLE_OWNER
            audit("login", u + " (owner)")
            return redirect(url_for("page_owner"))
        with DB() as c:
            row = c.execute("SELECT id,username,password,is_admin,role FROM users WHERE username=?", (u,)).fetchone()
        if row and check_password_hash(row["password"], p):
            role = row["role"] or (ROLE_OWNER if row["is_admin"] else ROLE_USER)
            session.permanent = True
            session["user_id"] = row["id"]
            session["username"] = row["username"]
            session["role"] = role
            audit("login", u)
            if role == ROLE_OWNER: return redirect(url_for("page_owner"))
            if role == ROLE_AGENT: return redirect(url_for("page_agent"))
            return redirect(url_for("page_user"))
        flash("Invalid credentials", "error")
    return render_template("login.html")

@app.route("/register", methods=["GET","POST"])
@limiter.limit("10 per minute", methods=["POST"])
def page_register():
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        e = request.form.get("email","")
        k = request.form.get("registration_key","").strip()
        if not (u and p and k):
            flash("All fields required", "error")
            return render_template("register.html")
        with DB() as c:
            kr = c.execute("SELECT id,expiry_date,is_used FROM keys WHERE key=?", (k,)).fetchone()
            if not kr or kr["is_used"]:
                flash("Invalid or used key", "error"); return render_template("register.html")
            if kr["expiry_date"]:
                try:
                    if datetime.fromisoformat(kr["expiry_date"]) < datetime.now():
                        flash("Key expired", "error"); return render_template("register.html")
                except Exception: pass
            if c.execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
                flash("Username taken", "error"); return render_template("register.html")
            c.execute("""INSERT INTO users (username,password,email,registration_key,
                         role, subscription_expiry, is_admin)
                         VALUES (?,?,?,?,?,?,0)""",
                      (u, generate_password_hash(p, method="pbkdf2:sha256:600000"),
                       e, k, ROLE_USER, kr["expiry_date"]))
            c.execute("UPDATE keys SET is_used=1, used_by=?, used_at=CURRENT_TIMESTAMP WHERE key=?",
                      (u, k))
            c.execute("INSERT INTO subscriptions (username,key,start_date,expiry_date) VALUES (?,?,CURRENT_TIMESTAMP,?)",
                      (u, k, kr["expiry_date"]))
        audit("register", u)
        flash("Registered! Please login.", "success")
        return redirect(url_for("page_login"))
    return render_template("register.html")

@app.route("/logout")
def page_logout():
    u = session.get("username","")
    session.clear()
    audit("logout", u)
    return redirect(url_for("page_login"))

@app.route("/owner")
@owner_required
def page_owner():
    return render_template("owner.html",
                           username=session.get("username"),
                           role=session.get("role"))

@app.route("/agent")
@agent_or_owner
def page_agent():
    return render_template("agent.html",
                           username=session.get("username"),
                           role=session.get("role"))

@app.route("/dashboard")
@login_required
def page_user():
    with DB() as c:
        u = c.execute("""SELECT admin_uid,bot_uid,bot_pw,bot_file,bot_status,
                                subscription_expiry,role,is_disabled,disable_reason,
                                personal_notice FROM users WHERE id=?""",
                      (session["user_id"],)).fetchone()
    if not u:
        session.clear(); return redirect(url_for("page_login"))
    expired = is_expired(u["subscription_expiry"], u["role"])
    return render_template("user.html",
                           username=session.get("username"),
                           role=session.get("role"),
                           config_done=bool(u["bot_file"]),
                           expired=expired,
                           disabled=bool(u["is_disabled"]),
                           disable_reason=u["disable_reason"] or "",
                           global_notice=get_global_notice(),
                           personal_notice=u["personal_notice"] or "")

# ══════════════════════════════════════════════════════════════
# USER APIs
# ══════════════════════════════════════════════════════════════
@app.route("/api/auth/me")
@login_required
def api_me():
    return jsonify({"username": session.get("username"), "role": session.get("role")})

@app.route("/api/configure", methods=["POST"])
@login_required
def api_configure():
    data = request.json or {}
    a_uid = (data.get("admin_uid") or "").strip()
    b_uid = (data.get("bot_uid") or "").strip()
    b_pw  = (data.get("bot_pw") or "").strip()
    if not (a_uid and b_uid and b_pw):
        return jsonify({"error": "all fields required"}), 400
    fname = deploy_bot(session["user_id"], session["username"], a_uid, b_uid, b_pw)
    m = get_monitor(session["user_id"], session["username"], fname)
    if m: m.start()
    audit("configure_bot", session["username"], meta=fname)
    return jsonify({"status":"ok"})

@app.route("/api/status")
@login_required
def api_status():
    m = get_monitor(session["user_id"])
    if not m:
        return jsonify({
            "is_running": False, "bot_status": "NOT CONFIGURED",
            "logs": [], "error_logs": [], "message_history": [],
            "cpu":0,"ram":0,"disk":0,
            "cpu_history":[0]*20, "ram_history":[0]*20,
            "uptime":"00:00:00","restart_count":0,
            "bot_uid":"N/A","bot_name":"N/A","bot_region":"N/A",
            "last_sender_uid":"N/A","last_guild_name":"N/A",
            "last_nickname":"N/A","last_message":"N/A","last_pfp_url":"N/A"
        })
    return jsonify(m.status())

@app.route("/api/control", methods=["POST"])
@login_required
def api_control():
    action = (request.json or {}).get("action")
    m = get_monitor(session["user_id"])
    if not m: return jsonify({"error":"not configured"}), 400
    if get_global_stop() and action == "start":
        return jsonify({"error":"Global stop active"}), 403
    with DB() as c:
        u = c.execute("SELECT is_disabled, subscription_expiry, role FROM users WHERE id=?",
                      (session["user_id"],)).fetchone()
    if u["is_disabled"] and action == "start":
        return jsonify({"error":"Bot disabled by owner"}), 403
    if is_expired(u["subscription_expiry"], u["role"]) and action == "start":
        return jsonify({"error":"Subscription expired"}), 403
    if action == "start": m.start()
    elif action == "stop": m.stop(by_user=True)
    elif action == "reset": m.stop(by_user=True); m.start()
    else: return jsonify({"error":"invalid action"}), 400
    audit(f"bot_{action}", session["username"])
    return jsonify({"status":"ok"})

@app.route("/api/clear_errors", methods=["POST"])
@login_required
def api_clear_errors():
    m = get_monitor(session["user_id"])
    if m: m.clear_errors()
    return jsonify({"status":"ok"})

@app.route("/api/clear_messages", methods=["POST"])
@login_required
def api_clear_messages():
    m = get_monitor(session["user_id"])
    if m: m.clear_messages()
    return jsonify({"status":"ok"})

@app.route("/api/export_logs")
@login_required
def api_export_logs():
    m = get_monitor(session["user_id"])
    return jsonify({"logs": m.output_lines if m else []})

@app.route("/api/export_errors")
@login_required
def api_export_errors():
    m = get_monitor(session["user_id"])
    return jsonify({"errors": m.error_lines if m else []})

@app.route("/api/export_messages")
@login_required
def api_export_messages():
    m = get_monitor(session["user_id"])
    return jsonify({"messages": m.message_lines if m else []})

@app.route("/api/admin_uids", methods=["GET","POST"])
@login_required
def api_admin_uids():
    m = get_monitor(session["user_id"])
    if not m: return jsonify({"uids":[]})
    if request.method == "GET":
        with open(m.bot_file) as f: content = f.read()
        match = re.search(r"ADMIN_UIDS\s*=\s*\[([^\]]*)\]", content)
        uids = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)) if match else []
        return jsonify({"uids": uids})
    uids = (request.json or {}).get("uids", [])
    with open(m.bot_file) as f: content = f.read()
    new_list = "[" + ", ".join(f"'{u}'" for u in uids) + "]"
    content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", f"ADMIN_UIDS = {new_list}", content)
    with open(m.bot_file, "w") as f: f.write(content)
    m.restart()
    audit("admin_uids_update", session["username"], meta=",".join(uids))
    return jsonify({"status":"success"})

@app.route("/api/bot_creds", methods=["GET","POST"])
@login_required
def api_bot_creds():
    m = get_monitor(session["user_id"])
    if not m: return jsonify({"uid":"","pw":""})
    if request.method == "GET":
        with open(m.bot_file) as f: content = f.read()
        match = re.search(r"Uid,\s*Pw\s*=\s*'([^']+)',\s*'([^']+)'", content)
        return jsonify({"uid": match.group(1) if match else "",
                        "pw":  match.group(2) if match else ""})
    data = request.json or {}
    uid, pw = data.get("uid",""), data.get("pw","")
    with open(m.bot_file) as f: content = f.read()
    content = re.sub(r"Uid,\s*Pw\s*=\s*'[^']*',\s*'[^']*'",
                     f"Uid, Pw = '{uid}', '{pw}'", content)
    with open(m.bot_file, "w") as f: f.write(content)
    m.restart()
    audit("bot_creds_update", session["username"])
    return jsonify({"status":"success"})

@app.route("/api/friend", methods=["POST"])
@login_required
def api_friend():
    data = request.json or {}
    action = data.get("action"); target = data.get("uid")
    m = get_monitor(session["user_id"])
    if not m: return jsonify({"status":"error","message":"not configured"}), 400
    with open(m.bot_file) as f: content = f.read()
    match = re.search(r"Uid,\s*Pw\s*=\s*'([^']+)',\s*'([^']+)'", content)
    if not match: return jsonify({"status":"error","message":"no creds"}), 400
    uid, pw = match.group(1), match.group(2)
    try:
        if action == "list":
            r = rq.get(f"https://mahir-friend-web.vercel.app/friend_list?uid={uid}&password={pw}", timeout=25)
        elif action in ("add","remove"):
            api = "add_friend" if action == "add" else "remove_friend"
            r = rq.get(f"https://mahir-friend-web.vercel.app/{api}?uid={uid}&password={pw}&friend_uid={target}", timeout=15)
        else:
            return jsonify({"status":"error","message":"invalid"}), 400
        return jsonify(r.json()) if r.status_code == 200 else jsonify({"status":"error","message":str(r.status_code)})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)})

@app.route("/api/guild", methods=["POST"])
@login_required
def api_guild():
    data = request.json or {}
    action = data.get("action"); gid = data.get("guild_id")
    if not gid: return jsonify({"success":False,"message":"missing guild_id"}), 400
    m = get_monitor(session["user_id"])
    if not m: return jsonify({"success":False,"message":"not configured"}), 400
    with open(m.bot_file) as f: content = f.read()
    match = re.search(r"Uid,\s*Pw\s*=\s*'([^']+)',\s*'([^']+)'", content)
    if not match: return jsonify({"success":False,"message":"no creds"}), 400
    uid, pw = match.group(1), match.group(2)
    try:
        r = rq.get(f"https://mahir-guild-api.vercel.app/{action}?clan_id={gid}&uid={uid}&pass={pw}", timeout=15)
        return jsonify(r.json()) if r.status_code == 200 else jsonify({"success":False,"message":str(r.status_code)})
    except Exception as e:
        return jsonify({"success":False,"message":str(e)})

# ══════════════════════════════════════════════════════════════
# OWNER APIs
# ══════════════════════════════════════════════════════════════
@app.route("/api/owner/stats")
@owner_required
def api_owner_stats():
    with DB() as c:
        total_users = c.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0]
        total_agents= c.execute("SELECT COUNT(*) FROM users WHERE role='agent'").fetchone()[0]
        total_keys  = c.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
        used_keys   = c.execute("SELECT COUNT(*) FROM keys WHERE is_used=1").fetchone()[0]
        unused_keys = total_keys - used_keys
        expired     = c.execute("""SELECT COUNT(*) FROM users
                                   WHERE role='user' AND subscription_expiry IS NOT NULL
                                   AND subscription_expiry < datetime('now')""").fetchone()[0]
    active = sum(1 for m in monitors.values() if m.is_running)
    return jsonify({
        "total_users": total_users, "total_agents": total_agents,
        "total_keys": total_keys, "used_keys": used_keys, "unused_keys": unused_keys,
        "expired_users": expired, "active_bots": active,
        "offline_bots": total_users - active,
        "global_stop": get_global_stop(),
        "cpu": psutil.cpu_percent(interval=0.1),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage('/').percent,
    })

@app.route("/api/owner/users")
@owner_required
def api_owner_users():
    q = request.args.get("q","").strip()
    with DB() as c:
        sql = """SELECT id,username,email,role,is_admin,bot_status,bot_file,bot_uid,
                        subscription_expiry,is_disabled,disable_reason,parent_agent,created_at
                 FROM users"""
        params = []
        if q:
            sql += " WHERE username LIKE ? OR bot_uid LIKE ?"
            params = [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY id DESC LIMIT 500"
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
    for r in rows:
        r["is_running"] = bool(monitors.get(r["id"], None) and monitors[r["id"]].is_running)
        r["is_expired"] = is_expired(r["subscription_expiry"], r["role"])
    return jsonify({"users": rows})

@app.route("/api/owner/user/<int:uid>/disable", methods=["POST"])
@owner_required
def api_owner_disable(uid):
    reason = (request.json or {}).get("reason","Disabled by owner")
    with DB() as c:
        c.execute("UPDATE users SET is_disabled=1, disable_reason=? WHERE id=?", (reason, uid))
        r = c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    m = monitors.get(uid)
    if m: m.stop(by_user=True)
    audit("user_disable", r["username"] if r else str(uid))
    return jsonify({"status":"ok"})

@app.route("/api/owner/user/<int:uid>/enable", methods=["POST"])
@owner_required
def api_owner_enable(uid):
    with DB() as c:
        c.execute("UPDATE users SET is_disabled=0, disable_reason=NULL WHERE id=?", (uid,))
    audit("user_enable", str(uid))
    return jsonify({"status":"ok"})

@app.route("/api/owner/user/<int:uid>/force-start", methods=["POST"])
@owner_required
def api_owner_force_start(uid):
    with DB() as c:
        u = c.execute("SELECT username,bot_file FROM users WHERE id=?", (uid,)).fetchone()
    if not u or not u["bot_file"]:
        return jsonify({"error":"no bot file"}), 400
    m = get_monitor(uid, u["username"], u["bot_file"])
    if m:
        m.user_stopped = False
        m.start()
    audit("force_start", u["username"])
    return jsonify({"status":"ok"})

@app.route("/api/owner/user/<int:uid>/restart", methods=["POST"])
@owner_required
def api_owner_restart(uid):
    with DB() as c:
        u = c.execute("SELECT username,bot_file FROM users WHERE id=?", (uid,)).fetchone()
    if not u or not u["bot_file"]: return jsonify({"error":"no bot file"}), 400
    m = get_monitor(uid, u["username"], u["bot_file"])
    if m: m.restart()
    audit("restart", u["username"])
    return jsonify({"status":"ok"})

@app.route("/api/owner/user/<int:uid>/delete", methods=["POST"])
@owner_required
def api_owner_delete(uid):
    with DB() as c:
        r = c.execute("SELECT username,bot_file,is_admin FROM users WHERE id=?", (uid,)).fetchone()
        if not r: return jsonify({"error":"not found"}), 404
        if r["is_admin"]: return jsonify({"error":"cannot delete owner"}), 403
        if r["bot_file"]:
            p = os.path.join(BOT_DIR, r["bot_file"])
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        c.execute("DELETE FROM users WHERE id=?", (uid,))
        c.execute("DELETE FROM subscriptions WHERE username=?", (r["username"],))
    drop_monitor(uid)
    audit("user_delete", r["username"])
    return jsonify({"status":"ok"})

@app.route("/api/owner/user/<int:uid>/renew", methods=["POST"])
@owner_required
def api_owner_renew(uid):
    days = int((request.json or {}).get("days", 30))
    new_exp = (datetime.now() + timedelta(days=days)).isoformat()
    with DB() as c:
        c.execute("UPDATE users SET subscription_expiry=? WHERE id=?", (new_exp, uid))
        r = c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        c.execute("INSERT INTO subscriptions (username, expiry_date, renewed_by) VALUES (?,?,?)",
                  (r["username"], new_exp, session["username"]))
    audit("renew", r["username"], meta=f"{days}d")
    return jsonify({"status":"ok", "expiry": new_exp})

@app.route("/api/owner/notice/global", methods=["GET","POST"])
@owner_required
def api_owner_notice_global():
    if request.method == "GET":
        return jsonify({"message": get_global_notice()})
    msg = (request.json or {}).get("message","")
    with DB() as c:
        c.execute("DELETE FROM notices WHERE scope='global'")
        if msg:
            c.execute("INSERT INTO notices (scope,message,enabled) VALUES ('global',?,1)", (msg,))
    audit("global_notice")
    return jsonify({"status":"ok"})

@app.route("/api/owner/notice/user/<int:uid>", methods=["POST"])
@owner_required
def api_owner_notice_user(uid):
    msg = (request.json or {}).get("message","")
    with DB() as c:
        c.execute("UPDATE users SET personal_notice=? WHERE id=?", (msg, uid))
    audit("personal_notice", str(uid))
    return jsonify({"status":"ok"})

@app.route("/api/owner/keys")
@owner_required
def api_owner_keys():
    with DB() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM keys ORDER BY id DESC LIMIT 300").fetchall()]
    return jsonify({"keys": rows})

@app.route("/api/owner/keys/create", methods=["POST"])
@owner_required
def api_owner_key_create():
    data = request.json or {}
    days = int(data.get("days", 30))
    lifetime = bool(data.get("lifetime", False))
    key = secrets.token_hex(16).upper()
    expiry = None if lifetime else (datetime.now() + timedelta(days=days)).isoformat()
    with DB() as c:
        c.execute("INSERT INTO keys (key,created_by,expiry_date) VALUES (?,?,?)",
                  (key, session["username"], expiry))
    audit("key_create", key)
    return jsonify({"status":"ok", "key": key, "expiry": expiry})

@app.route("/api/owner/keys/<int:kid>/delete", methods=["POST"])
@owner_required
def api_owner_key_delete(kid):
    with DB() as c:
        c.execute("DELETE FROM keys WHERE id=?", (kid,))
    audit("key_delete", str(kid))
    return jsonify({"status":"ok"})

@app.route("/api/owner/agents")
@owner_required
def api_owner_agents():
    with DB() as c:
        rows = [dict(r) for r in c.execute("""SELECT id,username,email,created_at,max_keys
                                              FROM users WHERE role='agent' ORDER BY id DESC""").fetchall()]
        for r in rows:
            r["customer_count"] = c.execute("SELECT COUNT(*) FROM users WHERE parent_agent=?",
                                             (r["username"],)).fetchone()[0]
            r["keys_created"] = c.execute("SELECT COUNT(*) FROM keys WHERE created_by=?",
                                          (r["username"],)).fetchone()[0]
    return jsonify({"agents": rows})

@app.route("/api/owner/agents/create", methods=["POST"])
@owner_required
def api_owner_agent_create():
    data = request.json or {}
    u = (data.get("username") or "").strip()
    p = data.get("password") or ""
    e = data.get("email") or ""
    mk = int(data.get("max_keys", 0))
    if not (u and p): return jsonify({"error":"username & password required"}), 400
    with DB() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
            return jsonify({"error":"username taken"}), 409
        c.execute("""INSERT INTO users (username,password,email,role,max_keys,is_admin)
                     VALUES (?,?,?,?,?,0)""",
                  (u, generate_password_hash(p, method="pbkdf2:sha256:600000"), e, ROLE_AGENT, mk))
    audit("agent_create", u)
    return jsonify({"status":"ok"})

@app.route("/api/owner/agents/<int:aid>/delete", methods=["POST"])
@owner_required
def api_owner_agent_delete(aid):
    with DB() as c:
        r = c.execute("SELECT username FROM users WHERE id=? AND role='agent'", (aid,)).fetchone()
        if not r: return jsonify({"error":"agent not found"}), 404
        c.execute("UPDATE users SET parent_agent=NULL WHERE parent_agent=?", (r["username"],))
        c.execute("DELETE FROM users WHERE id=?", (aid,))
    audit("agent_delete", r["username"])
    return jsonify({"status":"ok"})

@app.route("/api/owner/bots/global", methods=["POST"])
@owner_required
def api_owner_global_control():
    action = (request.json or {}).get("action")
    if action == "stop_all":
        set_global_stop(True)
        for m in list(monitors.values()):
            try: m.stop(by_user=True)
            except Exception: pass
    elif action == "start_all":
        set_global_stop(False)
        for m in list(monitors.values()):
            m.user_stopped = False
            try: m.start()
            except Exception: pass
    elif action == "restart_all":
        set_global_stop(False)
        for m in list(monitors.values()):
            try: m.restart()
            except Exception: pass
    else:
        return jsonify({"error":"invalid"}), 400
    audit(f"global_{action}")
    return jsonify({"status":"ok"})

@app.route("/api/owner/bots/live")
@owner_required
def api_owner_bots_live():
    out = []
    with DB() as c:
        rows = c.execute("""SELECT id,username,bot_file,subscription_expiry,role,
                                    is_disabled FROM users WHERE bot_file IS NOT NULL""").fetchall()
    for u in rows:
        m = monitors.get(u["id"])
        st = m.status() if m else {"is_running": False, "bot_status": "OFFLINE",
                                    "cpu": 0, "ram": 0, "uptime": "00:00:00",
                                    "restart_count": 0}
        out.append({
            "id": u["id"], "username": u["username"],
            "is_running": st["is_running"], "bot_status": st["bot_status"],
            "cpu": st["cpu"], "ram": st["ram"], "uptime": st["uptime"],
            "restart_count": st["restart_count"],
            "subscription_expiry": u["subscription_expiry"],
            "is_expired": is_expired(u["subscription_expiry"], u["role"]),
            "is_disabled": bool(u["is_disabled"]),
        })
    return jsonify({"bots": out})

@app.route("/api/owner/audit")
@owner_required
def api_owner_audit():
    with DB() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500").fetchall()]
    return jsonify({"rows": rows})

# ══════════════════════════════════════════════════════════════
# AGENT APIs
# ══════════════════════════════════════════════════════════════
@app.route("/api/agent/stats")
@agent_or_owner
def api_agent_stats():
    me = session["username"]
    with DB() as c:
        custs = c.execute("SELECT COUNT(*) FROM users WHERE parent_agent=?", (me,)).fetchone()[0]
        keys  = c.execute("SELECT COUNT(*) FROM keys WHERE created_by=?", (me,)).fetchone()[0]
        used  = c.execute("SELECT COUNT(*) FROM keys WHERE created_by=? AND is_used=1", (me,)).fetchone()[0]
    return jsonify({"customers": custs, "keys": keys, "used_keys": used,
                    "unused_keys": keys - used})

@app.route("/api/agent/customers")
@agent_or_owner
def api_agent_customers():
    me = session["username"]
    with DB() as c:
        rows = [dict(r) for r in c.execute("""SELECT id,username,subscription_expiry,
                                              bot_status,is_disabled
                                              FROM users WHERE parent_agent=?
                                              ORDER BY id DESC""", (me,)).fetchall()]
    for r in rows:
        r["is_expired"] = is_expired(r["subscription_expiry"], ROLE_USER)
    return jsonify({"customers": rows})

@app.route("/api/agent/keys")
@agent_or_owner
def api_agent_keys():
    me = session["username"]
    with DB() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM keys WHERE created_by=? ORDER BY id DESC LIMIT 200", (me,)).fetchall()]
    return jsonify({"keys": rows})

@app.route("/api/agent/keys/create", methods=["POST"])
@agent_or_owner
def api_agent_key_create():
    me = session["username"]
    with DB() as c:
        u = c.execute("SELECT max_keys FROM users WHERE username=?", (me,)).fetchone()
        if u and u["max_keys"]:
            made = c.execute("SELECT COUNT(*) FROM keys WHERE created_by=?", (me,)).fetchone()[0]
            if made >= u["max_keys"]:
                return jsonify({"error":"key limit reached"}), 403
    data = request.json or {}
    days = int(data.get("days", 30))
    key = secrets.token_hex(16).upper()
    expiry = (datetime.now() + timedelta(days=days)).isoformat()
    with DB() as c:
        c.execute("INSERT INTO keys (key,created_by,expiry_date,agent) VALUES (?,?,?,?)",
                  (key, me, expiry, me))
    audit("agent_key_create", key)
    return jsonify({"status":"ok", "key": key})

@app.route("/api/agent/customer/<int:uid>/renew", methods=["POST"])
@agent_or_owner
def api_agent_renew(uid):
    me = session["username"]
    days = int((request.json or {}).get("days", 30))
    with DB() as c:
        r = c.execute("SELECT username,parent_agent FROM users WHERE id=?", (uid,)).fetchone()
        if not r: return jsonify({"error":"not found"}), 404
        if session.get("role") != ROLE_OWNER and r["parent_agent"] != me:
            return jsonify({"error":"not your customer"}), 403
        new_exp = (datetime.now() + timedelta(days=days)).isoformat()
        c.execute("UPDATE users SET subscription_expiry=? WHERE id=?", (new_exp, uid))
        c.execute("INSERT INTO subscriptions (username,expiry_date,renewed_by,agent) VALUES (?,?,?,?)",
                  (r["username"], new_exp, me, me))
    audit("agent_renew", r["username"], meta=f"{days}d")
    return jsonify({"status":"ok", "expiry": new_exp})

# ══════════════════════════════════════════════════════════════
# BOOT / SHUTDOWN
# ══════════════════════════════════════════════════════════════
_booted = False
_boot_lock = threading.Lock()

def boot_bots():
    global _booted
    with _boot_lock:
        if _booted: return
        _booted = True
    init_db()
    global_stop = get_global_stop()
    with DB() as c:
        users = c.execute("""SELECT id,username,bot_file,is_disabled,role,
                                    subscription_expiry FROM users
                             WHERE bot_file IS NOT NULL""").fetchall()
    for u in users:
        if u["is_disabled"] or global_stop: continue
        if is_expired(u["subscription_expiry"], u["role"]): continue
        m = get_monitor(u["id"], u["username"], u["bot_file"])
        if m: m.start()
    watchdog.start()
    print(f"[boot] {len(monitors)} monitor(s) initialized.")

def shutdown_all(*_):
    print("\n[shutdown] stopping bots...")
    for m in list(monitors.values()):
        try: m.stop(by_user=True)
        except Exception: pass
    watchdog.stop()
    time.sleep(0.5)
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_all)
signal.signal(signal.SIGINT, shutdown_all)

# Boot once (avoid Flask reloader duplicate)
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not os.environ.get("FLASK_DEBUG"):
    boot_bots()

if __name__ == "__main__":
    app.run(host="0.0.0.0",
            port=int(os.environ.get("PORT", 8080)),
            debug=False, use_reloader=False)