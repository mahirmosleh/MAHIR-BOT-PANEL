#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import sqlite3
import hashlib
import secrets
import json
import time
import subprocess
import shutil
import threading
import re
import signal
import zipfile
from datetime import datetime, timedelta
from queue import Queue, Empty
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, flash, send_file
from functools import wraps
import psutil
import requests


def install_dependencies():
    required_pkgs = ["colorama", "psutil", "flask", "requests"]
    for pkg in required_pkgs:
        try:
            __import__(pkg)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

install_dependencies()

from colorama import init, Fore, Style
init(autoreset=True)

app = Flask(__name__)

SECRET_KEY_FILE = ".secret_key"
if os.path.exists(SECRET_KEY_FILE):
    try:
        with open(SECRET_KEY_FILE, 'r') as f:
            app.secret_key = f.read().strip()
    except:
        app.secret_key = secrets.token_hex(32)
else:
    app.secret_key = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_FILE, 'w') as f:
            f.write(app.secret_key)
        os.chmod(SECRET_KEY_FILE, 0o600)
    except: pass

app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

DB_FILE = "users.db"
MAHIR_SOURCE = "mahir.py"
USER_BOTS_DIR = "."
MASTER_ADMIN_UID = "1120167200"

OWNER_USERNAME = "MAHIR TCP"
OWNER_PASSWORD = "MAHIR0208@"

monitors_lock = threading.Lock()


# ============================================================
#  DATABASE
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        email TEXT,
        registration_key TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_admin INTEGER DEFAULT 0,
        is_agent INTEGER DEFAULT 0,
        admin_uid TEXT,
        bot_uid TEXT,
        bot_pw TEXT,
        bot_file TEXT,
        bot_pid INTEGER,
        bot_status TEXT DEFAULT 'not_configured',
        key_limit INTEGER DEFAULT -1,
        can_manage_db INTEGER DEFAULT 0,
        bot_disabled_by_admin INTEGER DEFAULT 0,
        disable_reason TEXT,
        subscription_expiry TIMESTAMP NULL,
        created_by_agent TEXT,
        bot_force_active INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        used_by TEXT,
        used_at TIMESTAMP,
        is_used INTEGER DEFAULT 0,
        expiry_date TIMESTAMP NULL,
        duration_days INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS subscription_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        extended_by TEXT,
        extended_by_role TEXT,
        days_added INTEGER,
        mode TEXT,
        old_expiry TIMESTAMP,
        new_expiry TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()


def migrate_db():
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in c.fetchall()}
        if 'key_limit' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN key_limit INTEGER DEFAULT -1')
        if 'can_manage_db' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN can_manage_db INTEGER DEFAULT 0')
        if 'bot_disabled_by_admin' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN bot_disabled_by_admin INTEGER DEFAULT 0')
        if 'disable_reason' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN disable_reason TEXT')
        if 'subscription_expiry' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN subscription_expiry TIMESTAMP NULL')
        if 'created_by_agent' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN created_by_agent TEXT')
        if 'bot_force_active' not in cols:
            c.execute('ALTER TABLE users ADD COLUMN bot_force_active INTEGER DEFAULT 0')

        c.execute("PRAGMA table_info(keys)")
        kcols = {row[1] for row in c.fetchall()}
        if 'duration_days' not in kcols:
            c.execute('ALTER TABLE keys ADD COLUMN duration_days INTEGER DEFAULT 0')

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='subscription_history'")
        if not c.fetchone():
            c.execute('''CREATE TABLE subscription_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                extended_by TEXT,
                extended_by_role TEXT,
                days_added INTEGER,
                mode TEXT,
                old_expiry TIMESTAMP,
                new_expiry TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
        else:
            c.execute("PRAGMA table_info(subscription_history)")
            hcols = {row[1] for row in c.fetchall()}
            if 'extended_by_role' not in hcols:
                c.execute('ALTER TABLE subscription_history ADD COLUMN extended_by_role TEXT')

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Migration warning: {e}")


def get_setting(key, default=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT value FROM settings WHERE key=?', (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except: return default


def set_setting(key, value):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
        conn.commit()
        conn.close()
        return True
    except: return False


def log_subscription_history(user_id, username, extended_by, extended_by_role, days_added, mode, old_expiry, new_expiry):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''INSERT INTO subscription_history 
                     (user_id, username, extended_by, extended_by_role, days_added, mode, old_expiry, new_expiry)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, username, extended_by, extended_by_role, days_added, mode,
                   old_expiry.isoformat() if old_expiry else None,
                   new_expiry.isoformat() if new_expiry else None))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"History log error: {e}")


def get_global_stop():
    return get_setting('global_bot_stop', '0') == '1'


def get_global_notice():
    return get_setting(
        'global_notice_text',
        '╔══════════════════════════════╗\n'
        '🌸 **আসসালামু আলাইকুম** 🌸\n'
        '╚══════════════════════════════╝\n\n'
        '💙 **প্রিয় TCP Bot User সবাইকে,**\n\n'
        'আশা করি আল্লাহর রহমতে আপনারা সবাই ভালো আছেন এবং সুস্থ আছেন। 🤍\n\n'
        '🔥 **MAHIR TCP BOT**-এর সাথে থাকার জন্য আপনাদের সবাইকে আন্তরিকভাবে ধন্যবাদ। 🫶\n\n'
        '⚙️ Bot ব্যবহার করার সময় যদি কোনো **সমস্যা • Bug • Error • Update Issue** '
        'অথবা অন্য কোনো সমস্যা দেখতে পান, তাহলে অবশ্যই আমাদের জানাবেন।\n\n'
        '🛠️ আপনাদের দেওয়া রিপোর্ট অনুযায়ী সমস্যাগুলো ঠিক করার সর্বোচ্চ চেষ্টা করব, ইনশাআল্লাহ। ❤️\n\n'
        '⏳ কাজের ব্যস্ততার কারণে কোনো Update বা Fix দিতে মাঝে মাঝে একটু সময় লাগতে পারে। '
        'তবে চিন্তা করবেন না—সময় পেলেই সবকিছু ঠিক করার চেষ্টা করব। ⚡\n\n'
        '━━━━━━━━━━━━━━━━━━━━━━\n\n'
        '🌸 **সবাই ভালো থাকবেন**\n'
        '🤍 **সুস্থ থাকবেন**\n'
        '🤲 **নিজেদের খেয়াল রাখবেন**\n\n'
        '🔥 **MAHIR TCP BOT** 🔥\n'
        '💫 *Stay Connected • Stay Updated* 💫\n\n'
        '╚══════════════════════════════╝'
    )


def get_user_login_notice():
    return get_setting('user_login_notice_text', '')


def is_user_login_notice_enabled():
    return get_setting('user_login_notice_enabled', '0') == '1'


def validate_db_file(filepath):
    try:
        with open(filepath, 'rb') as f:
            header = f.read(16)
            if not header.startswith(b'SQLite format 3\x00'):
                return False, "Not a valid SQLite database file"
        conn = sqlite3.connect(filepath)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in c.fetchall()}
        missing = {'users', 'keys'} - tables
        if missing:
            conn.close()
            return False, f"Missing tables: {', '.join(missing)}"
        c.execute("PRAGMA integrity_check")
        result = c.fetchone()
        conn.close()
        if result[0] != 'ok':
            return False, f"Integrity: {result[0]}"
        return True, "OK"
    except sqlite3.DatabaseError as e:
        return False, f"Invalid: {e}"
    except Exception as e:
        return False, f"Error: {e}"


init_db()
migrate_db()


# ============================================================
#  HELPERS
# ============================================================
def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


def is_hashed(pw):
    return len(pw) == 64 and all(c in '0123456789abcdefABCDEF' for c in pw)


def check_password(stored, provided):
    if is_hashed(stored):
        return stored == hashlib.sha256(provided.encode()).hexdigest()
    return stored == provided


def parse_admin_uids(admin_uid_str):
    uids = [MASTER_ADMIN_UID]; seen = {MASTER_ADMIN_UID}
    if admin_uid_str:
        for p in re.split(r'[,;\s]+', str(admin_uid_str)):
            p = p.strip().strip("'\"")
            if p and p not in seen:
                uids.append(p); seen.add(p)
    return uids


def build_admin_uids_list_string(uids):
    unique = []; seen = set()
    for uid in uids:
        uid = str(uid).strip()
        if uid and uid not in seen:
            unique.append(uid); seen.add(uid)
    if MASTER_ADMIN_UID in unique:
        unique.remove(MASTER_ADMIN_UID)
    unique.insert(0, MASTER_ADMIN_UID)
    return '[' + ', '.join(f"'{uid}'" for uid in unique) + ']'


def inject_credentials_into_bot_file(filepath, bot_uid, bot_pw, admin_uids_list):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        content = re.sub(r"Uid\s*,\s*Pw\s*=\s*'[^']*'\s*,\s*'[^']*'",
                         f"Uid, Pw = '{bot_uid}', '{bot_pw}'", content)
        admin_uids_str = build_admin_uids_list_string(admin_uids_list)
        if re.search(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", content):
            content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", f"ADMIN_UIDS = {admin_uids_str}", content)
        else:
            content = re.sub(r"(Uid\s*,\s*Pw\s*=\s*'[^']*'\s*,\s*'[^']*')",
                             f"\\1\nADMIN_UIDS = {admin_uids_str}", content)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, "OK"
    except Exception as e:
        return False, str(e)


def check_subscription_status(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT subscription_expiry, registration_key, is_admin, is_agent FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {'status': 'unknown', 'days_left': 0, 'expiry': None}
    if row[2] == 1 or row[3] == 1:
        conn.close()
        return {'status': 'unlimited', 'days_left': 999999, 'expiry': None}
    sub_expiry = row[0]
    if not sub_expiry and row[1]:
        c.execute('SELECT expiry_date FROM keys WHERE key=?', (row[1],))
        kr = c.fetchone()
        if kr: sub_expiry = kr[0]
    conn.close()
    if not sub_expiry:
        return {'status': 'unlimited', 'days_left': 999999, 'expiry': None}
    try:
        expiry_dt = datetime.fromisoformat(sub_expiry)
        diff = expiry_dt - datetime.now()
        if diff.total_seconds() <= 0:
            return {'status': 'expired', 'days_left': 0, 'expiry': expiry_dt}
        total_hours = diff.total_seconds() / 3600
        days_left = diff.days if total_hours > 24 else 0
        return {'status': 'active', 'days_left': days_left, 'expiry': expiry_dt, 'hours_left': int(total_hours)}
    except:
        return {'status': 'unknown', 'days_left': 0, 'expiry': None}


def format_time_left(expiry_dt):
    if not expiry_dt:
        return '∞'
    diff = expiry_dt - datetime.now()
    total = int(diff.total_seconds())
    if total <= 0:
        return 'Expired'
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0 and days < 7: parts.append(f"{minutes}m")
    return ' '.join(parts) if parts else 'Less than 1m'


def expire_user_bot(user_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT bot_file FROM users WHERE id=?', (user_id,))
        row = c.fetchone()
        conn.close()

        with monitors_lock:
            if user_id in monitors:
                try:
                    monitors[user_id].watchdog_running = False
                    monitors[user_id].stop_process()
                    print(f"🛑 IMMEDIATE STOP: User {user_id} bot killed (expired)")
                except Exception as e:
                    print(f"Stop error: {e}")
                try: del monitors[user_id]
                except: pass

        if row and row[0]:
            bot_file = row[0]
            path = os.path.join(USER_BOTS_DIR, bot_file)
            if os.path.exists(path):
                try: os.remove(path)
                except: pass
            login_file = bot_file.replace('.py', '_login.py')
            login_path = os.path.join(USER_BOTS_DIR, login_file)
            if os.path.exists(login_path):
                try: os.remove(login_path)
                except: pass
            base_name = bot_file.replace('.py', '')
            try:
                for f in os.listdir(USER_BOTS_DIR):
                    if f.startswith(base_name) and f.endswith('.py'):
                        try: os.remove(os.path.join(USER_BOTS_DIR, f))
                        except: pass
            except: pass

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET bot_file=NULL, bot_status="expired", bot_pid=NULL WHERE id=?', (user_id,))
        conn.commit(); conn.close()
        print(f"✅ User {user_id}: Expired → Stopped & Deleted")
    except Exception as e:
        print(f"expire_user_bot error: {e}")


def check_all_expired_bots():
    while True:
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('''SELECT id, subscription_expiry, registration_key, bot_file, bot_status
                         FROM users WHERE is_admin=0 AND is_agent=0''')
            rows = c.fetchall()
            conn.close()
            for user_id, sub_expiry, reg_key, bot_file, bot_status in rows:
                expiry_val = sub_expiry
                if not expiry_val and reg_key:
                    conn = sqlite3.connect(DB_FILE)
                    cc = conn.cursor()
                    cc.execute('SELECT expiry_date FROM keys WHERE key=?', (reg_key,))
                    kr = cc.fetchone()
                    conn.close()
                    if kr: expiry_val = kr[0]
                if expiry_val:
                    try:
                        expiry_dt = datetime.fromisoformat(expiry_val)
                        if datetime.now() > expiry_dt:
                            if bot_status == 'expired' and not bot_file:
                                continue
                            print(f"\n⏰ EXPIRY DETECTED → User {user_id}")
                            expire_user_bot(user_id)
                    except: pass
        except Exception as e:
            print(f"BG check error: {e}")
        time.sleep(15)


threading.Thread(target=check_all_expired_bots, daemon=True).start()


def update_bot_bio(uid, password, username):
    bio_text = f"[c][b][i][00BFFF]{username} [00FF00]বটে আপনাকে স্বাগতম। [FFFF00]নিজের জন্য এমন একটি Bot কিনতে চাইলে যোগাযোগ করুন আমাদের [7CFC00]WEBSITE NAME: [00FFFF]MAHIR.XO.JE [00FF00]WHATSAPP [00FFFF]: [00FFFF]MAHIR__222"
    encoded = requests.utils.quote(bio_text, safe='')
    encoded_uid = requests.utils.quote(str(uid), safe='')
    encoded_pw = requests.utils.quote(str(password), safe='')
    try:
        resp = requests.get(f"https://mahir-long-bio.vercel.app/bio_upload?bio={encoded}&uid={encoded_uid}&pass={encoded_pw}", timeout=10)
        return resp.status_code == 200
    except: return False


# ============================================================
#  PROCESS MONITOR
# ============================================================
class ProcessMonitor:
    def __init__(self, user_id, bot_file_path):
        self.user_id = user_id
        self.process = None
        self.process_name = bot_file_path
        self.is_running = False
        self.start_time = None
        self.restart_count = 0
        self.output_lines = []
        self.full_history = []
        self.error_lines = []
        self.message_info_lines = []
        self.max_display_lines = 500
        self.max_history_lines = 5000
        self.max_error_lines = 1000
        self.max_message_lines = 500
        self.lock = threading.Lock()
        self.output_queue = Queue()
        self.output_thread = None
        self.cpu_history = [0] * 20
        self.ram_history = [0] * 20
        self.bot_uid = "N/A"
        self.bot_name = "N/A"
        self.bot_region = "N/A"
        self.bot_status = "🔴 OFFLINE"
        self.bot_access_token = "N/A"
        self.bot_jwt_token = "N/A"
        self.bot_dynamic_key = "N/A"
        self.bot_dynamic_iv = "N/A"
        self.bot_server = "N/A"
        self.bot_bd_time = "N/A"
        self.last_sender_uid = "N/A"
        self.last_guild_name = "N/A"
        self.last_nickname = "N/A"
        self.last_message = "N/A"
        self.last_pfp_url = "N/A"
        self.account_info_found = False
        self.in_user_info = False
        self.in_tokens = False
        self.in_security = False
        self.in_system = False
        self.collecting_message = False
        self.message_started = False
        self.message_stored = False
        self.user_info_buffer = []
        self.tokens_buffer = []
        self.security_buffer = []
        self.system_buffer = []
        self.message_buffer = []
        self.temp_sender_uid = "N/A"
        self.temp_nickname = "N/A"
        self.temp_message = "N/A"
        self.temp_guild_name = "N/A"
        self.temp_pfp_url = "N/A"

        self.auto_restart_running = True
        self.auto_restart_thread = threading.Thread(target=self._auto_restart_loop, daemon=True)
        self.auto_restart_thread.start()

        self.watchdog_running = True
        self.watchdog_thread = threading.Thread(target=self._expiry_watchdog, daemon=True)
        self.watchdog_thread.start()

    def _auto_restart_loop(self):
        while self.auto_restart_running:
            try:
                time.sleep(8)
                if not self.auto_restart_running:
                    break
                if self.is_running and self.process and self.process.poll() is not None:
                    print(f"🔄 Auto-restart: user {self.user_id} bot died, restarting...")
                    self.restart_count += 1
                    self.start_process()
            except Exception as e:
                print(f"Auto-restart error: {e}")

    def _expiry_watchdog(self):
        while self.watchdog_running:
            try:
                time.sleep(10)
                if not self.watchdog_running:
                    break
                if not self.is_running:
                    continue
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute('SELECT subscription_expiry, registration_key FROM users WHERE id=?', (self.user_id,))
                row = c.fetchone()
                expiry_val = None
                if row:
                    expiry_val = row[0]
                    if not expiry_val and row[1]:
                        c.execute('SELECT expiry_date FROM keys WHERE key=?', (row[1],))
                        kr = c.fetchone()
                        if kr: expiry_val = kr[0]
                conn.close()
                if expiry_val:
                    try:
                        expiry_dt = datetime.fromisoformat(expiry_val)
                        if datetime.now() > expiry_dt:
                            print(f"\n⏰ WATCHDOG: User {self.user_id} EXPIRED → Killing bot NOW")
                            self.watchdog_running = False
                            self.auto_restart_running = False
                            self.stop_process()
                            expire_user_bot(self.user_id)
                            break
                    except: pass
            except Exception as e:
                print(f"Watchdog error: {e}")

    def can_start(self):
        sub = check_subscription_status(self.user_id)
        if sub['status'] == 'expired':
            return False, "expired"
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('SELECT bot_disabled_by_admin, bot_force_active FROM users WHERE id=?', (self.user_id,))
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return False, "admin_disabled"
            if get_global_stop():
                if row and row[1]:
                    return True, "force_active"
                return False, "global_stop"
        except: pass
        return True, "ok"

    def clean_ansi(self, text):
        if not text: return ""
        text = re.compile(r'\x1b\[[0-9;]*[mK]').sub('', text)
        text = re.sub(r'\[\d+m', '', text)
        text = re.sub(r'\[\d+;\d+m', '', text)
        text = re.sub(r'\[\d+;\d+;\d+m', '', text)
        text = text.replace('[]', '')
        try:
            text = text.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
        except:
            text = ''.join(c for c in text if c.isprintable() or c in '\n\r\t')
        return text.strip()

    def parse_user_info(self, lines):
        d = {}
        for line in lines:
            clean = self.clean_ansi(line)
            if not clean: continue
            nm = re.search(r'NAME\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
            if nm:
                raw = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', re.sub(r'\[[0-9;]*m', '', nm.group(1).strip()))
                if raw: d['name'] = raw[:100]
            um = re.search(r'UID\s*[:：]\s*(\d+)', clean, re.IGNORECASE)
            if um: d['uid'] = um.group(1)
            rm = re.search(r'REGION\s*[:：]\s*(\w+)', clean, re.IGNORECASE)
            if rm: d['region'] = rm.group(1).strip().upper()
        return d

    def parse_tokens(self, lines):
        t = {}
        for line in lines:
            clean = self.clean_ansi(line)
            am = re.search(r'ACCESS TOKEN\s*[:：]\s*([a-zA-Z0-9_.-]+)', clean, re.IGNORECASE)
            if am: t['access_token'] = am.group(1)
            jm = re.search(r'JWT TOKEN\s*[:：]\s*([a-zA-Z0-9_.-]+)', clean, re.IGNORECASE)
            if jm: t['jwt_token'] = jm.group(1)
        return t

    def parse_security(self, lines):
        s = {}
        for line in lines:
            clean = self.clean_ansi(line)
            km = re.search(r'DYNAMIC KEY\s*[:：]\s*([a-fA-F0-9]+)', clean, re.IGNORECASE)
            if km: s['dynamic_key'] = km.group(1)
            im = re.search(r'DYNAMIC IV\s*[:：]\s*([a-fA-F0-9]+)', clean, re.IGNORECASE)
            if im: s['dynamic_iv'] = im.group(1)
        return s

    def parse_system(self, lines):
        s = {}
        for line in lines:
            clean = self.clean_ansi(line)
            tm = re.search(r'BD TIME\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
            if tm: s['bd_time'] = tm.group(1).strip()
            sm = re.search(r'ONLINE SRV\s*[:：]\s*([\d.]+:\d+)', clean, re.IGNORECASE)
            if sm: s['server'] = sm.group(1)
        return s

    def parse_message_info(self, line):
        clean = self.clean_ansi(line)
        if not clean: return None
        m = re.search(r'Sender UID\s*[:：]\s*(\d+)', clean, re.IGNORECASE)
        if m: return {'type': 'sender_uid', 'value': m.group(1)}
        m = re.search(r'Nickname\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
        if m:
            v = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', re.sub(r'\[[0-9;]*m', '', m.group(1).strip()))
            if v and len(v) > 1: return {'type': 'nickname', 'value': v[:100]}
        m = re.search(r'Message\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
        if m:
            v = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', re.sub(r'\[[0-9;]*m', '', m.group(1).strip()))
            if v: return {'type': 'message', 'value': v[:500]}
        m = re.search(r'Guild Name\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
        if m:
            v = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', re.sub(r'\[[0-9;]*m', '', m.group(1).strip()))
            if v: return {'type': 'guild_name', 'value': v[:100]}
        m = re.search(r'PFP URL\s*[:：]\s*(https?://[^\s]+)', clean, re.IGNORECASE)
        if m: return {'type': 'pfp_url', 'value': m.group(1)[:200]}
        return None

    def process_line(self, line, timestamp):
        clean = self.clean_ansi(line)
        if not clean: return
        if 'USER INFO' in clean or '👤 USER INFO' in clean:
            self.in_user_info = True; self.user_info_buffer = [clean]; return
        if self.in_user_info:
            self.user_info_buffer.append(clean)
            if 'TOKENS' in clean or '🌐 TOKENS' in clean:
                self.in_user_info = False
                d = self.parse_user_info(self.user_info_buffer)
                if d:
                    with self.lock:
                        if 'uid' in d: self.bot_uid = d['uid']
                        if 'name' in d: self.bot_name = d['name']
                        if 'region' in d: self.bot_region = d['region']
                        self.account_info_found = True
                self.user_info_buffer = []
            return
        if 'TOKENS' in clean or '🌐 TOKENS' in clean:
            self.in_tokens = True; self.tokens_buffer = [clean]; return
        if self.in_tokens:
            self.tokens_buffer.append(clean)
            if 'SECURITY' in clean or '🔑 SECURITY' in clean:
                self.in_tokens = False
                d = self.parse_tokens(self.tokens_buffer)
                if d:
                    with self.lock:
                        if 'access_token' in d:
                            t = d['access_token']
                            self.bot_access_token = t[:30]+'...' if len(t)>30 else t
                        if 'jwt_token' in d:
                            t = d['jwt_token']
                            self.bot_jwt_token = t[:30]+'...' if len(t)>30 else t
                self.tokens_buffer = []
            return
        if 'SECURITY' in clean or '🔑 SECURITY' in clean:
            self.in_security = True; self.security_buffer = [clean]; return
        if self.in_security:
            self.security_buffer.append(clean)
            if 'SYSTEM STATUS' in clean or '⏱ SYSTEM STATUS' in clean:
                self.in_security = False
                d = self.parse_security(self.security_buffer)
                if d:
                    with self.lock:
                        if 'dynamic_key' in d: self.bot_dynamic_key = d['dynamic_key']
                        if 'dynamic_iv' in d: self.bot_dynamic_iv = d['dynamic_iv']
                self.security_buffer = []
            return
        if 'SYSTEM STATUS' in clean or '⏱ SYSTEM STATUS' in clean:
            self.in_system = True; self.system_buffer = [clean]; return
        if self.in_system:
            self.system_buffer.append(clean)
            if '══════' in clean and len(self.system_buffer) > 3:
                self.in_system = False
                d = self.parse_system(self.system_buffer)
                if d:
                    with self.lock:
                        if 'bd_time' in d: self.bot_bd_time = d['bd_time']
                        if 'server' in d: self.bot_server = d['server']
                self.system_buffer = []
            return
        if 'MESSAGE INFO' in clean or '╔══════════════ [ MESSAGE INFO ]' in clean:
            self.collecting_message = True; self.message_started = True; self.message_stored = False
            self.message_buffer = [clean]
            self.temp_sender_uid = "N/A"; self.temp_nickname = "N/A"; self.temp_message = "N/A"
            self.temp_guild_name = "N/A"; self.temp_pfp_url = "N/A"
            return
        if self.collecting_message and self.message_started:
            self.message_buffer.append(clean)
            parsed = self.parse_message_info(clean)
            if parsed:
                t = parsed['type']
                if t == 'sender_uid': self.temp_sender_uid = parsed['value']
                elif t == 'nickname': self.temp_nickname = parsed['value']
                elif t == 'message': self.temp_message = parsed['value']
                elif t == 'guild_name': self.temp_guild_name = parsed['value']
                elif t == 'pfp_url': self.temp_pfp_url = parsed['value']
            if '╚══════════════════════════════════════════════╝' in clean or '═╝' in clean:
                self.collecting_message = False; self.message_started = False
                if not self.message_stored and self.temp_sender_uid != 'N/A':
                    with self.lock:
                        self.last_sender_uid = self.temp_sender_uid
                        self.last_nickname = self.temp_nickname
                        self.last_message = self.temp_message
                        self.last_guild_name = self.temp_guild_name
                        self.last_pfp_url = self.temp_pfp_url
                        self.bot_status = "🟢 ACTIVE & ONLINE"
                        self.message_info_lines.append({
                            'timestamp': timestamp,
                            'data': {
                                'sender_uid': self.temp_sender_uid, 'nickname': self.temp_nickname,
                                'message': self.temp_message, 'guild_name': self.temp_guild_name,
                                'pfp_url': self.temp_pfp_url
                            }
                        })
                        if len(self.message_info_lines) > self.max_message_lines:
                            self.message_info_lines = self.message_info_lines[-self.max_message_lines:]
                        self.message_stored = True
                self.message_buffer = []
            return
        if 'LOGIN SUCCESSFUL' in clean:
            with self.lock:
                self.bot_status = "🟢 ACTIVE & ONLINE"
                self.account_info_found = True
            return

    def is_error_line(self, line):
        if not line: return False
        l = line.lower()
        for p in ['error','exception','failed','traceback','critical','fatal','timeout',
                  'connection refused','permission denied','keyerror','attributeerror',
                  'typeerror','valueerror','indexerror','authentication failed','disconnected']:
            if p in l: return True
        return False

    def start_process(self):
        can, reason = self.can_start()
        if not can:
            with self.lock:
                self.is_running = False
                self.bot_status = "🔴 BLOCKED"
            print(f"⛔ Cannot start user {self.user_id}: {reason}")
            return False
        with self.lock:
            if self.process and self.process.poll() is None: return True
            if self.process: self._stop_process_internal()
            if not os.path.exists(self.process_name):
                print(f"Error: {self.process_name} not found"); return False
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-u", self.process_name],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, universal_newlines=True, errors='replace')
                self.is_running = True
                self.start_time = datetime.now()
                self.bot_status = "🟢 ACTIVE & ONLINE"
                try:
                    conn = sqlite3.connect(DB_FILE)
                    c = conn.cursor()
                    c.execute('UPDATE users SET bot_status="running", bot_pid=? WHERE id=?',
                              (self.process.pid, self.user_id))
                    conn.commit(); conn.close()
                except: pass
                def enqueue():
                    try:
                        for line in iter(self.process.stdout.readline, ''):
                            if line:
                                ts = datetime.now().strftime('%H:%M:%S')
                                self.output_queue.put(f"[{ts}] {line.rstrip()}")
                                self.process_line(line, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    except: pass
                self.output_thread = threading.Thread(target=enqueue, daemon=True)
                self.output_thread.start()
                return True
            except Exception as e:
                self.output_lines.append(f"Error: {str(e)}")
                return False

    def _stop_process_internal(self):
        if self.process:
            try:
                p = psutil.Process(self.process.pid)
                for child in p.children(recursive=True):
                    try: child.kill()
                    except: pass
                p.kill(); p.wait(timeout=5)
            except:
                try: self.process.kill()
                except: pass
            self.process = None
        self.is_running = False
        self.bot_status = "🔴 OFFLINE"
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('UPDATE users SET bot_pid=NULL, bot_status="stopped" WHERE id=?', (self.user_id,))
            conn.commit(); conn.close()
        except: pass

    def stop_process(self):
        with self.lock: self._stop_process_internal()

    def restart_logic(self):
        self.stop_process(); time.sleep(2)
        s = self.start_process()
        if s:
            with self.lock: self.restart_count += 1
        return s

    def update_logs(self):
        new_logs = []
        while not self.output_queue.empty():
            try: new_logs.append(self.output_queue.get_nowait())
            except Empty: break
        if new_logs:
            with self.lock:
                self.full_history.extend(new_logs)
                if len(self.full_history) > self.max_history_lines:
                    self.full_history = self.full_history[-self.max_history_lines:]
                self.output_lines.extend(new_logs)
                if len(self.output_lines) > self.max_display_lines:
                    self.output_lines = self.output_lines[-self.max_display_lines:]
                for log in new_logs:
                    if self.is_error_line(log):
                        self.error_lines.append(log)
                        if len(self.error_lines) > self.max_error_lines:
                            self.error_lines = self.error_lines[-self.max_error_lines:]
        return new_logs

    def get_status(self):
        self.update_logs()
        uptime = "00:00:00"
        if self.is_running and self.start_time:
            uptime = str(datetime.now() - self.start_time).split('.')[0]
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory().percent
            try: disk = psutil.disk_usage('/').percent
            except: disk = psutil.disk_usage(os.path.expanduser("~")).percent
        except: cpu, ram, disk = 0, 0, 0
        with self.lock:
            self.cpu_history.append(cpu); self.ram_history.append(ram)
            if len(self.cpu_history) > 20:
                self.cpu_history = self.cpu_history[-20:]; self.ram_history = self.ram_history[-20:]
        return {
            'is_running': self.is_running, 'process_name': os.path.basename(self.process_name),
            'uptime': uptime, 'script_remaining': 'No Limit', 'cpu': cpu, 'ram': ram, 'disk': disk,
            'restart_count': self.restart_count, 'logs': self.output_lines[-200:],
            'full_logs': self.full_history, 'error_logs': self.error_lines[-200:],
            'message_history': self.message_info_lines[-50:], 'bot_uid': self.bot_uid,
            'bot_name': self.bot_name, 'bot_status': self.bot_status, 'bot_region': self.bot_region,
            'bot_access_token': self.bot_access_token, 'bot_jwt_token': self.bot_jwt_token,
            'bot_dynamic_key': self.bot_dynamic_key, 'bot_dynamic_iv': self.bot_dynamic_iv,
            'bot_server': self.bot_server, 'bot_bd_time': self.bot_bd_time,
            'last_sender_uid': self.last_sender_uid, 'last_guild_name': self.last_guild_name,
            'last_nickname': self.last_nickname, 'last_message': self.last_message,
            'last_pfp_url': self.last_pfp_url, 'cpu_history': self.cpu_history,
            'ram_history': self.ram_history, 'auto_restart_minutes': 0
        }

    def clear_errors(self):
        with self.lock: self.error_lines = []
        return True

    def clear_messages(self):
        with self.lock: self.message_info_lines = []
        return True

    def hard_reset(self):
        self.stop_process()
        with self.lock:
            self.restart_count = 0
            self.output_lines = []; self.full_history = []
            self.error_lines = []; self.message_info_lines = []
            self.account_info_found = False
            self.bot_uid = "N/A"; self.bot_name = "N/A"; self.bot_status = "🔴 OFFLINE"
            self.bot_region = "N/A"; self.bot_access_token = "N/A"; self.bot_jwt_token = "N/A"
            self.bot_dynamic_key = "N/A"; self.bot_dynamic_iv = "N/A"; self.bot_server = "N/A"
            self.bot_bd_time = "N/A"; self.last_sender_uid = "N/A"; self.last_guild_name = "N/A"
            self.last_nickname = "N/A"; self.last_message = "N/A"; self.last_pfp_url = "N/A"
        return self.start_process()


# ============================================================
#  MONITORS
# ============================================================
monitors = {}


def get_monitor(user_id):
    with monitors_lock:
        if user_id in monitors: return monitors[user_id]
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT bot_file FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]: return None
    bot_file = row[0]
    if not os.path.dirname(bot_file): bot_file = os.path.join(USER_BOTS_DIR, bot_file)
    if not os.path.exists(bot_file): return None
    monitor = ProcessMonitor(user_id, bot_file)
    with monitors_lock:
        if user_id in monitors: return monitors[user_id]
        monitors[user_id] = monitor
    monitor.start_process()
    return monitor


def startup_launch_all_bots():
    try:
        print("\n🚀 Startup: Launching all valid bots...")
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''SELECT id, bot_file FROM users 
                     WHERE bot_file IS NOT NULL AND bot_uid IS NOT NULL
                     AND is_admin=0 AND is_agent=0''')
        rows = c.fetchall()
        conn.close()
        count = 0
        for user_id, bot_file in rows:
            sub = check_subscription_status(user_id)
            if sub['status'] == 'expired':
                expire_user_bot(user_id)
                continue
            path = os.path.join(USER_BOTS_DIR, bot_file)
            if os.path.exists(path):
                m = ProcessMonitor(user_id, path)
                with monitors_lock:
                    monitors[user_id] = m
                m.start_process()
                count += 1
                print(f"  ✅ Bot launched: user_id={user_id} ({bot_file})")
        print(f"🚀 Startup complete: {count} bots launched\n")
    except Exception as e:
        print(f"Startup error: {e}")


# ============================================================
#  DECORATORS
# ============================================================
def owner_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get('is_admin'):
            flash('Owner access required', 'error')
            return redirect(url_for('owner_login'))
        return f(*a, **k)
    return d


def agent_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get('is_agent'):
            flash('Agent access required', 'error')
            return redirect(url_for('agent_login'))
        return f(*a, **k)
    return d


def login_required(f):
    @wraps(f)
    def d(*a, **k):
        if not session.get('user_id') and not session.get('is_admin'):
            flash('Please login first', 'error')
            return redirect(url_for('login'))
        return f(*a, **k)
    return d


# ============================================================
#  COMMON CSS / SIDEBAR
# ============================================================
SIDEBAR_MENU = '''
<div class="hamburger-menu">
  <button class="hamburger-btn" onclick="toggleMenu()" aria-label="Menu"><i class="fas fa-bars"></i></button>
  <div class="menu-dropdown" id="menuDropdown">
    <div class="menu-title">Premium App</div>
    <a href="https://www.mediafire.com/file/lvykrek51q17hae/MAHIR_TCP.apk" target="_blank" class="sidebar-download"><i class="fas fa-download"></i> DOWNLOAD APK</a>
    <div class="menu-divider"></div>
    <div class="menu-title">Authentication</div>
    <a href="/login" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>
    <a href="/register" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>
    <a href="/recover" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>
    <div class="menu-divider"></div>
    <div class="menu-title">Roles</div>
    <a href="/owner/login" class="menu-item"><i class="fas fa-crown"></i> Owner Login</a>
    <a href="/agent/login" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>
    <div class="menu-divider"></div>
    <div class="menu-title">Social</div>
    <a href="https://t.me/mahirtcpchat" target="_blank" class="menu-item"><i class="fab fa-telegram"></i> Telegram</a>
    <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="menu-item"><i class="fab fa-tiktok"></i> TikTok</a>
    <a href="https://whatsapp.com/channel/0029Vb9Omjk2ZjCevpv6h209" target="_blank" class="menu-item"><i class="fab fa-whatsapp" style="color:#25D366;"></i> WhatsApp Channel</a>
    <a href="https://chat.whatsapp.com/CTiEuMnEKacHZ7wirSNjqs" target="_blank" class="menu-item"><i class="fab fa-whatsapp" style="color:#25D366;"></i> WhatsApp Group</a>
    <div class="menu-divider"></div>
    <a href="https://MAHIR.XO.JE/" target="_blank" class="menu-item"><i class="fas fa-globe"></i> Website</a>
  </div>
</div>
'''

COMMON_CSS = '''
*{margin:0;padding:0;box-sizing:border-box}
:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:'JetBrains Mono',monospace}
body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}
body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),#07070f}
a{color:var(--gold2)}
.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}
.card-title i{color:var(--purple)}
.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}
.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}
.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}
.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}
.btn:disabled{opacity:.55;cursor:not-allowed}
.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}
.btn-xs{padding:4px 9px;font-size:.68rem;border-radius:8px}
.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}
.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}
.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}
.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}
.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}
.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}
.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}
.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}
.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}
.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}
.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}
.btn-admin{background:linear-gradient(135deg,#00d9f5,#8540F5);color:#fff}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}
.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}
.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}
.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}
.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}
.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}
.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}
.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}
.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}
.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}
.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}
.badge-expired{background:rgba(212,42,58,.15);color:#ff5a76;border-color:rgba(212,42,58,.5)}
.badge-owner{background:linear-gradient(135deg,rgba(245,200,66,.2),rgba(133,64,245,.2));color:#FFE28A;border-color:rgba(245,200,66,.5)}
.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem}
input:focus{outline:none;border-color:var(--gold)}
input::placeholder{color:rgba(233,233,248,.3)}
input[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}
input[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}
textarea{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;width:100%;resize:vertical}
select{padding:10px;border-radius:10px;background:#0a0a14;color:#fff;border:1px solid rgba(245,200,66,.2);font-family:inherit;font-size:.85rem}
.table-wrapper{overflow-x:auto;margin-top:12px}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}
th{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}
tr:hover td{background:rgba(245,200,66,.03)}
td code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem}
.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}
.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center}
.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}
.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}
.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}
.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}
.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}
.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}
.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px}
.breadcrumb a{color:var(--gold2);text-decoration:none}
.current-dir{color:var(--gold);font-weight:600}
.folder-link{color:var(--gold);text-decoration:none;font-weight:600}
.file-name{color:#8fc0ff}
.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.flex-grow{flex:1;min-width:150px}
.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}
.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}
.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.auth-card{width:100%;max-width:440px;padding:38px 34px;border-radius:24px}
.logo-wrap{width:96px;height:96px;margin:0 auto 18px;border-radius:50%;padding:6px;background:linear-gradient(135deg,rgba(59,140,255,.15),rgba(133,64,245,.12));border:1px solid rgba(59,140,255,.25)}
.logo-wrap img{width:100%;height:100%;border-radius:50%;object-fit:cover}
.auth-title{text-align:center;font-size:1.7rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:6px}
.auth-sub{text-align:center;color:var(--muted);font-size:.85rem;margin-bottom:22px}
.field{position:relative;margin-bottom:14px}
.field i{position:absolute;left:16px;top:50%;transform:translateY(-50%);color:rgba(233,233,248,.35);font-size:.95rem}
.field input{width:100%;padding:14px 16px 14px 46px}
.btn-block{width:100%;padding:14px;font-size:1rem}
.auth-links{text-align:center;margin-top:18px;padding-top:16px;border-top:1px solid rgba(245,200,66,.08);display:flex;flex-direction:column;gap:8px}
.auth-links a{color:var(--gold2);text-decoration:none;font-size:.88rem}
.alert{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border-radius:12px;margin-bottom:16px;font-size:.88rem;border:1px solid;text-align:center}
.alert.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}
.alert.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}
.hamburger-menu{position:fixed;top:20px;left:20px;z-index:1000}
.hamburger-btn{background:rgba(14,14,28,.92);border:1px solid rgba(245,200,66,.2);color:var(--gold);width:46px;height:46px;border-radius:12px;cursor:pointer;font-size:1.2rem}
.menu-dropdown{display:none;position:absolute;top:56px;left:0;background:rgba(12,12,24,.97);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:8px 0;min-width:250px;box-shadow:0 20px 50px rgba(0,0,0,.6)}
.menu-dropdown.active{display:block}
.menu-title{padding:8px 20px 4px;color:rgba(233,233,248,.35);font-size:.62rem;text-transform:uppercase;letter-spacing:2px;font-weight:700}
.menu-item{display:flex;align-items:center;gap:12px;padding:9px 20px;color:rgba(233,233,248,.75);text-decoration:none;font-size:.88rem;border-left:3px solid transparent}
.menu-item:hover{background:rgba(245,200,66,.05);border-left-color:var(--gold);color:var(--gold)}
.menu-item i{width:18px;text-align:center;color:rgba(233,233,248,.35)}
.menu-divider{border-top:1px solid rgba(245,200,66,.07);margin:6px 14px}
.sidebar-download{display:flex;align-items:center;justify-content:center;gap:10px;margin:8px 14px;padding:11px;border-radius:12px;background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14 !important;text-decoration:none;font-weight:800;font-size:.82rem;border-left:none !important}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:99999;justify-content:center;align-items:center;padding:16px;backdrop-filter:blur(6px)}
.modal-overlay.active{display:flex;animation:fadeIn .25s ease}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.modal-box{background:#101022;border:1px solid rgba(245,200,66,.14);border-radius:18px;padding:24px;max-width:720px;width:100%;max-height:90vh;overflow-y:auto;position:relative;animation:slideUp .3s ease}
@keyframes slideUp{from{transform:translateY(30px);opacity:0}to{transform:translateY(0);opacity:1}}
.modal-close{position:absolute;top:12px;right:16px;font-size:1.7rem;color:var(--gold2);cursor:pointer;background:none;border:none}
.modal-title{font-size:1.3rem;font-weight:800;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}
.modal-section{background:rgba(0,0,0,.3);border:1px solid rgba(245,200,66,.07);border-radius:14px;padding:16px;margin-bottom:14px}
.modal-section h3{color:var(--gold);font-size:.95rem;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.modal-input-group{display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.modal-input-group label{min-width:90px;color:var(--gold2);font-weight:600;font-size:.82rem}
.modal-input-group input{flex:1;min-width:160px}
.modal-btn{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border:none;border-radius:10px;font-weight:700;font-size:.78rem;cursor:pointer;font-family:inherit}
.modal-btn-save{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}
.modal-btn-cancel{background:rgba(255,255,255,.08);color:var(--text)}
.modal-btn-action{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}
.modal-btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}
.modal-btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}
.modal-result-box{background:rgba(0,0,0,.45);border:1px solid rgba(245,200,66,.08);border-radius:10px;padding:12px;margin-top:10px;max-height:160px;overflow-y:auto;font-size:.78rem;color:#c5c5e5;white-space:pre-wrap;word-break:break-word}
.notification{position:fixed;top:20px;right:20px;padding:12px 18px;border-radius:12px;z-index:999999;font-weight:600;font-size:.85rem;box-shadow:0 10px 30px rgba(0,0,0,.5)}
.notification-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}
.notification-error{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}
.notification-info{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}
.notice-indicator{position:fixed;bottom:20px;right:20px;background:linear-gradient(135deg,#F5C842,#FF5A6A);color:#0a0a14;padding:12px 20px;border-radius:14px;cursor:pointer;font-weight:800;font-size:.85rem;box-shadow:0 10px 30px rgba(245,200,66,.4);z-index:9998;display:flex;align-items:center;gap:10px;animation:pulseB 2s infinite}
@keyframes pulseB{0%,100%{transform:scale(1)}50%{transform:scale(1.05)}}
.notice-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:99998;justify-content:center;align-items:center;padding:20px;backdrop-filter:blur(8px)}
.notice-overlay.show{display:flex;animation:fadeIn .3s ease}
.notice-box{background:linear-gradient(135deg,#1a0a2e,#0a0a1a);border:2px solid rgba(245,200,66,.35);border-radius:22px;padding:32px 28px;max-width:480px;width:100%;text-align:center;box-shadow:0 0 60px rgba(245,200,66,.3);animation:slideUp .4s ease;position:relative}
.notice-icon{font-size:3.5rem;margin-bottom:16px;display:block}
.notice-title{font-size:1.4rem;font-weight:900;background:linear-gradient(120deg,#F5C842,#FF5A6A,#B388FF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:14px;letter-spacing:1px}
.notice-msg{color:#c5c5e5;font-size:.95rem;line-height:1.7;margin-bottom:22px;padding:16px;background:rgba(0,0,0,.4);border-radius:14px;border:1px solid rgba(245,200,66,.1);text-align:left}
.notice-contact-title{font-size:.78rem;color:var(--gold2);text-transform:uppercase;letter-spacing:2px;margin-bottom:14px;font-weight:700}
.notice-buttons{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
.notice-btn{display:inline-flex;align-items:center;gap:8px;padding:12px 20px;border-radius:12px;text-decoration:none;font-weight:700;font-size:.85rem;color:#fff !important}
.notice-btn:hover{transform:translateY(-2px)}
.notice-btn-tg{background:linear-gradient(135deg,#0088cc,#005f8a)}
.notice-btn-tt{background:linear-gradient(135deg,#ff0050,#c40040)}
.notice-btn-web{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400 !important}
.notice-btn-wa-ch{background:linear-gradient(135deg,#25D366,#128C7E)}
.notice-btn-wa-gp{background:linear-gradient(135deg,#075E54,#128C7E)}
.keys-table-wrap{background:linear-gradient(135deg,rgba(15,10,30,.95),rgba(10,5,20,.95));border:1px solid rgba(245,200,66,.15);border-radius:20px;padding:22px;box-shadow:0 15px 40px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.03)}
.keys-table{width:100%;border-collapse:separate;border-spacing:0 8px}
.keys-table thead th{color:#FFE28A;font-size:.7rem;text-transform:uppercase;letter-spacing:2px;background:transparent;padding:10px 14px;border:none;font-weight:800;text-align:left}
.keys-table tbody tr{background:rgba(0,0,0,.35);transition:all .25s}
.keys-table tbody tr:hover{background:rgba(245,200,66,.06);transform:translateY(-1px)}
.keys-table tbody td{padding:14px;border:none;vertical-align:middle;color:#e9e9f8;font-size:.85rem}
.keys-table tbody tr td:first-child{border-top-left-radius:12px;border-bottom-left-radius:12px}
.keys-table tbody tr td:last-child{border-top-right-radius:12px;border-bottom-right-radius:12px}
.key-code{background:linear-gradient(135deg,rgba(245,200,66,.08),rgba(133,64,245,.08));padding:6px 14px;border-radius:10px;color:#F5C842;font-family:'Courier New',monospace;font-size:.78rem;font-weight:600;letter-spacing:.5px;border:1px solid rgba(245,200,66,.2);display:inline-block;word-break:break-all;max-width:280px}
.creator-badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:8px;background:rgba(245,200,66,.08);color:#FFE28A;font-weight:700;font-size:.72rem;border:1px solid rgba(245,200,66,.2);letter-spacing:.5px;text-transform:uppercase}
.creator-badge.agent{background:rgba(59,140,255,.08);color:#7ab5ff;border-color:rgba(59,140,255,.25)}
.creator-badge.owner{background:linear-gradient(135deg,rgba(245,200,66,.2),rgba(133,64,245,.2));color:#FFE28A;border-color:rgba(245,200,66,.5)}
.used-by{color:#c5c5e5;font-weight:500;font-style:italic}
.used-by .user-icon{color:#7dd3fc;margin-right:4px}
.duration-tag{display:inline-block;padding:3px 10px;border-radius:20px;background:rgba(74,222,128,.1);color:#4ade80;font-size:.68rem;font-weight:700;letter-spacing:.5px;border:1px solid rgba(74,222,128,.25)}
.delete-key-btn{background:linear-gradient(135deg,#D42A3A,#8A1A28);border:none;color:#fff;width:34px;height:34px;border-radius:10px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .25s}
.delete-key-btn:hover{transform:scale(1.1);box-shadow:0 6px 20px rgba(212,42,58,.5)}
.status-badge{display:inline-flex;align-items:center;gap:6px;padding:5px 14px;border-radius:20px;font-size:.68rem;font-weight:800;letter-spacing:.8px;text-transform:uppercase}
.status-badge.used{background:linear-gradient(135deg,rgba(59,140,255,.15),rgba(15,76,191,.15));color:#7ab5ff;border:1px solid rgba(59,140,255,.4)}
.status-badge.available{background:linear-gradient(135deg,rgba(245,200,66,.15),rgba(201,154,26,.15));color:#FFE28A;border:1px solid rgba(245,200,66,.4)}
.days-badge{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:20px;font-size:.72rem;font-weight:800;letter-spacing:.5px;background:linear-gradient(135deg,rgba(59,140,255,.15),rgba(15,76,191,.15));color:#7ab5ff;border:1px solid rgba(59,140,255,.4)}
.days-badge.warn{background:linear-gradient(135deg,rgba(245,200,66,.15),rgba(201,154,26,.15));color:#FFE28A;border-color:rgba(245,200,66,.4)}
.days-badge.danger{background:linear-gradient(135deg,rgba(212,42,58,.15),rgba(138,26,40,.15));color:#ff5a76;border-color:rgba(212,42,58,.4)}
.days-badge.permanent{background:linear-gradient(135deg,rgba(74,222,128,.15),rgba(34,140,80,.15));color:#4ade80;border-color:rgba(74,222,128,.4)}
'''

SIDEBAR_JS = '''
<script>
function toggleMenu(){document.getElementById('menuDropdown').classList.toggle('active');}
document.addEventListener('click',function(e){var m=document.querySelector('.hamburger-menu');if(m&&!m.contains(e.target)){var d=document.getElementById('menuDropdown');if(d)d.classList.remove('active');}});
</script>
'''


# ============================================================
#  LOGIN PAGES
# ============================================================
LOGIN_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Welcome Back - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
''' + SIDEBAR_MENU + '''
<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">Welcome Back</div><div class="auth-sub">Sign in to your account</div>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
<form method="POST" id="loginForm">
  <div class="field"><i class="fas fa-user"></i><input type="text" name="username" placeholder="Username" required/></div>
  <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required/></div>
  <button type="submit" class="btn btn-gold btn-block" id="loginBtn"><i class="fas fa-sign-in-alt"></i> Login</button>
</form>
<script>document.getElementById('loginForm').addEventListener('submit',function(){var b=document.getElementById('loginBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Authenticating...';});</script>
<div class="auth-links"><a href="/register"><i class="fas fa-user-plus"></i> Don't have an account? Register</a><a href="/recover"><i class="fas fa-key"></i> Forgot Password?</a></div></div></div>
''' + SIDEBAR_JS + '''</body></html>'''

REGISTER_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Create Account - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
''' + SIDEBAR_MENU + '''
<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">Create Account</div><div class="auth-sub">Join the MAHIR network</div>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
<form method="POST" id="registerForm">
  <div class="field"><i class="fas fa-user"></i><input type="text" name="username" placeholder="Username" required/></div>
  <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required/></div>
  <div class="field"><i class="fas fa-envelope"></i><input type="email" name="email" placeholder="Email (optional)"/></div>
  <div class="field"><i class="fas fa-key"></i><input type="text" name="registration_key" placeholder="Registration Key" required/></div>
  <button type="submit" class="btn btn-gold btn-block" id="registerBtn"><i class="fas fa-paper-plane"></i> Register</button>
</form>
<script>document.getElementById('registerForm').addEventListener('submit',function(){var b=document.getElementById('registerBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Processing...';});</script>
<div class="auth-links"><a href="/login"><i class="fas fa-arrow-left"></i> Already have an account? Login</a></div></div></div>
''' + SIDEBAR_JS + '''</body></html>'''

RECOVER_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Recover Password - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
''' + SIDEBAR_MENU + '''
<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap" style="display:flex;align-items:center;justify-content:center;"><i class="fas fa-key" style="font-size:2rem;color:var(--gold);"></i></div><div class="auth-title">Recover Password</div><div class="auth-sub">Reset your password</div>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
<form method="POST" id="recoverForm">
  <div class="field"><i class="fas fa-user"></i><input type="text" name="username" placeholder="Username" required/></div>
  <div class="field"><i class="fas fa-envelope"></i><input type="email" name="email" placeholder="Email Address" required/></div>
  <button type="submit" class="btn btn-gold btn-block" id="recoverBtn"><i class="fas fa-paper-plane"></i> Recover</button>
</form>
<script>document.getElementById('recoverForm').addEventListener('submit',function(){var b=document.getElementById('recoverBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Processing...';});</script>
<div class="auth-links"><a href="/login"><i class="fas fa-arrow-left"></i> Back to Login</a></div></div></div>
''' + SIDEBAR_JS + '''</body></html>'''

OWNER_LOGIN_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Owner Access - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
''' + SIDEBAR_MENU + '''
<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">👑 Owner Access</div><div class="auth-sub">Master control panel</div>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
<form method="POST" id="ownerLoginForm">
  <div class="field"><i class="fas fa-crown"></i><input type="text" name="username" placeholder="Owner Username" required/></div>
  <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required/></div>
  <button type="submit" class="btn btn-gold btn-block" id="loginBtn"><i class="fas fa-sign-in-alt"></i> Login</button>
</form>
<script>document.getElementById('ownerLoginForm').addEventListener('submit',function(){var b=document.getElementById('loginBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Authenticating...';});</script>
<div class="auth-links"><a href="/login"><i class="fas fa-arrow-left"></i> Back to Main</a></div></div></div>
''' + SIDEBAR_JS + '''</body></html>'''

AGENT_LOGIN_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Agent Login - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
''' + SIDEBAR_MENU + '''
<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">Agent Login</div><div class="auth-sub">Agent panel access</div>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
<form method="POST" id="agentLoginForm">
  <div class="field"><i class="fas fa-user-tie"></i><input type="text" name="username" placeholder="Agent Username" required/></div>
  <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required/></div>
  <button type="submit" class="btn btn-gold btn-block" id="loginBtn"><i class="fas fa-sign-in-alt"></i> Login</button>
</form>
<script>document.getElementById('agentLoginForm').addEventListener('submit',function(){var b=document.getElementById('loginBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Authenticating...';});</script>
<div class="auth-links"><a href="/login"><i class="fas fa-arrow-left"></i> Back to Main</a></div></div></div>
''' + SIDEBAR_JS + '''</body></html>'''


# ============================================================
#  AGENT DASHBOARD (with Subscription Management link)
# ============================================================
AGENT_DASHBOARD_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Agent Dashboard - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-user-tie"></i> Agent Dashboard</h1>
    <div class="flex">
      <a href="/agent/subscription" class="btn btn-gold btn-sm"><i class="fas fa-clock-rotate-left"></i> Subscription Management</a>
      <span class="welcome-text">Welcome, <strong>{{ session.username }}</strong></span>
      <a href="/agent/logout" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-chart-simple"></i> Stats</div>
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-label">Keys Created</div><div class="stat-value">{{ key_count }}</div></div>
      <div class="stat-card"><div class="stat-label">My Customers</div><div class="stat-value">{{ my_users|length }}</div></div>
      <div class="stat-card"><div class="stat-label">Key Limit</div><div class="stat-value">{% if key_limit >= 0 %}{{ key_limit }}{% else %}∞{% endif %}</div></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-key"></i> Generate Registration Key</div>
    {% if key_limit >= 0 and key_count >= key_limit %}
    <div style="padding:14px;background:rgba(212,42,58,.1);border-radius:12px;color:var(--red2);"><i class="fas fa-exclamation-triangle"></i> Max {{ key_limit }} keys.</div>
    {% else %}
    <form method="POST" action="/agent/create_key" class="input-group">
      <label>Days (0=Permanent):</label>
      <input type="number" name="days_valid" value="30" min="0" style="width:110px;"/>
      <button type="submit" class="btn btn-gold"><i class="fas fa-plus-circle"></i> Generate</button>
    </form>
    {% endif %}
    {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
    {% if new_key %}
    <div class="key-display"><strong>New Key:</strong><code>{{ new_key }}</code>{% if days == 0 %}<span class="badge badge-permanent">∞ Permanent</span>{% else %}<span style="color:var(--muted);">valid {{ days }} days</span>{% endif %}</div>
    {% endif %}
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-users"></i> My Customers ({{ my_users|length }})</div>
    <p style="color:var(--muted);font-size:.82rem;margin-bottom:12px;"><i class="fas fa-info-circle"></i> আপনার তৈরি key দিয়ে যারা register হয়েছে। Renew / Extend করতে পারবেন।</p>
    <div class="table-wrapper">
      <table>
        <thead><tr><th>ID</th><th>Username</th><th>Bot UID</th><th>Status</th><th>Days Left</th><th>Time Left</th><th>Expiry</th><th>Renewals</th><th style="text-align:right;">Renew</th></tr></thead>
        <tbody>
          {% for u in my_users %}
          <tr>
            <td>{{ u.id }}</td>
            <td><strong>{{ u.username }}</strong><br><small style="color:var(--muted);font-size:.7rem;">{{ u.email or '—' }}</small></td>
            <td>{{ u.bot_uid or '—' }}</td>
            <td>
              {% if u.sub_status == 'expired' %}<span class="badge badge-expired">Expired</span>
              {% elif u.sub_status == 'unlimited' %}<span class="badge badge-permanent">∞</span>
              {% else %}<span class="badge badge-active">Active</span>{% endif %}
            </td>
            <td>
              {% if u.sub_status == 'unlimited' %}
                <span class="days-badge permanent"><i class="fas fa-infinity"></i> Unlimited</span>
              {% elif u.sub_status == 'expired' %}
                <span class="days-badge danger"><i class="fas fa-times-circle"></i> 0 days</span>
              {% elif u.sub_days <= 3 %}
                <span class="days-badge danger"><i class="fas fa-exclamation-triangle"></i> {{ u.sub_days }} days</span>
              {% elif u.sub_days <= 7 %}
                <span class="days-badge warn"><i class="fas fa-clock"></i> {{ u.sub_days }} days</span>
              {% else %}
                <span class="days-badge"><i class="fas fa-check-circle"></i> {{ u.sub_days }} days</span>
              {% endif %}
            </td>
            <td><small style="color:var(--gold2);">{{ u.sub_time_left }}</small></td>
            <td><small style="color:var(--muted);">{{ u.sub_expiry or '—' }}</small></td>
            <td>{% if u.renew_count > 0 %}<span class="days-badge"><i class="fas fa-redo"></i> {{ u.renew_count }}x (+{{ u.renew_days }}d)</span>{% else %}<small style="color:var(--muted);">—</small>{% endif %}</td>
            <td>
              <form method="POST" action="/agent/renew_subscription/{{ u.id }}" style="display:flex;gap:4px;justify-content:flex-end;">
                <input type="number" name="days" value="30" min="1" style="width:70px;padding:4px;font-size:.72rem;"/>
                <select name="mode" style="padding:4px;font-size:.72rem;border-radius:6px;background:#000;color:#fff;border:1px solid rgba(245,200,66,.2);">
                  <option value="extend">Extend</option>
                  <option value="reset">New</option>
                </select>
                <button type="submit" class="btn btn-gold btn-sm" style="padding:4px 8px;font-size:.65rem;"><i class="fas fa-sync"></i></button>
              </form>
            </td>
          </tr>
          {% else %}<tr class="empty-row"><td colspan="9">No customers yet.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-key" style="color:var(--purple);"></i> Recent Keys (Mine)</div>
    <div class="keys-table-wrap">
      <table class="keys-table">
        <thead>
          <tr>
            <th>KEY</th>
            <th>CREATED</th>
            <th>USER DAYS LEFT</th>
            <th>USED BY</th>
            <th>STATUS</th>
          </tr>
        </thead>
        <tbody>
          {% for key in keys %}
          <tr>
            <td><span class="key-code">{{ key.key }}</span></td>
            <td style="color:var(--muted);font-size:.78rem;">{{ key.created_at[:16] if key.created_at else '—' }}</td>
            <td>
              {% if key.user_sub_status == 'unlimited' %}
                <span class="days-badge permanent"><i class="fas fa-infinity"></i> Unlimited</span>
              {% elif key.user_sub_status == 'expired' %}
                <span class="days-badge danger"><i class="fas fa-times-circle"></i> Expired</span>
              {% elif key.user_sub_days is not none and key.user_sub_days >= 0 %}
                {% if key.user_sub_days <= 3 %}
                  <span class="days-badge danger"><i class="fas fa-exclamation-triangle"></i> {{ key.user_sub_days }} days left</span>
                {% elif key.user_sub_days <= 7 %}
                  <span class="days-badge warn"><i class="fas fa-clock"></i> {{ key.user_sub_days }} days left</span>
                {% else %}
                  <span class="days-badge"><i class="fas fa-check-circle"></i> {{ key.user_sub_days }} days left</span>
                {% endif %}
              {% else %}
                <span style="color:var(--muted);font-size:.75rem;">— (unused)</span>
              {% endif %}
            </td>
            <td>{% if key.used_by %}<span class="used-by"><i class="fas fa-user user-icon"></i>{{ key.used_by }}</span>{% else %}<span style="color:var(--muted);">—</span>{% endif %}</td>
            <td>{% if key.is_used %}<span class="status-badge used"><i class="fas fa-check-circle"></i> USED</span>{% else %}<span class="status-badge available"><i class="fas fa-clock"></i> AVAILABLE</span>{% endif %}</td>
          </tr>
          {% else %}<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:30px;">No keys yet. Generate one above.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <a href="/login" class="back-link"><i class="fas fa-arrow-left"></i> Back</a>
</div></body></html>'''


# ============================================================
#  AGENT SUBSCRIPTION MANAGEMENT PAGE
# ============================================================
AGENT_SUBSCRIPTION_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Subscription Management - Agent</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-clock-rotate-left"></i> Subscription Management</h1>
    <div class="flex">
      <a href="/agent/dashboard" class="btn btn-primary btn-sm"><i class="fas fa-arrow-left"></i> Dashboard</a>
      <span class="welcome-text">Agent: <strong>{{ session.username }}</strong></span>
      <a href="/agent/logout" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>

  {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}

  <div class="card">
    <div class="card-title"><i class="fas fa-chart-simple"></i> Subscription Overview</div>
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-label">Total Customers</div><div class="stat-value">{{ my_users|length }}</div></div>
      <div class="stat-card"><div class="stat-label">Active</div><div class="stat-value" style="color:#4ade80;">{{ active_count }}</div></div>
      <div class="stat-card"><div class="stat-label">Expiring Soon (≤7d)</div><div class="stat-value" style="color:#F5C842;">{{ expiring_count }}</div></div>
      <div class="stat-card"><div class="stat-label">Expired</div><div class="stat-value" style="color:#ff5a76;">{{ expired_count }}</div></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-users"></i> All Customers — Extend Subscription</div>
    <p style="color:var(--muted);font-size:.82rem;margin-bottom:12px;"><i class="fas fa-info-circle"></i> আপনি যেকোনো customer-এর মেয়াদ extend বা reset করতে পারবেন। Extend = current expiry-র সাথে যোগ হবে, Reset = আজ থেকে নতুন করে শুরু হবে।</p>
    <div class="keys-table-wrap">
      <table class="keys-table">
        <thead>
          <tr>
            <th>USERNAME</th>
            <th>STATUS</th>
            <th>DAYS LEFT</th>
            <th>TIME LEFT</th>
            <th>EXPIRY</th>
            <th>RENEWALS</th>
            <th style="text-align:right;">RENEW / EXTEND</th>
          </tr>
        </thead>
        <tbody>
          {% for u in my_users %}
          <tr>
            <td>
              <strong style="color:#FFE28A;">{{ u.username }}</strong>
              <br><small style="color:var(--muted);font-size:.7rem;">ID #{{ u.id }} · {{ u.email or '—' }}</small>
            </td>
            <td>
              {% if u.sub_status == 'expired' %}<span class="badge badge-expired">Expired</span>
              {% elif u.sub_status == 'unlimited' %}<span class="badge badge-permanent">∞ Unlimited</span>
              {% elif u.sub_days <= 3 %}<span class="badge badge-expired">Expiring</span>
              {% else %}<span class="badge badge-running">Active</span>{% endif %}
            </td>
            <td>
              {% if u.sub_status == 'unlimited' %}
                <span class="days-badge permanent"><i class="fas fa-infinity"></i> ∞</span>
              {% elif u.sub_status == 'expired' %}
                <span class="days-badge danger"><i class="fas fa-times-circle"></i> 0d</span>
              {% elif u.sub_days <= 3 %}
                <span class="days-badge danger"><i class="fas fa-exclamation-triangle"></i> {{ u.sub_days }}d</span>
              {% elif u.sub_days <= 7 %}
                <span class="days-badge warn"><i class="fas fa-clock"></i> {{ u.sub_days }}d</span>
              {% else %}
                <span class="days-badge"><i class="fas fa-check-circle"></i> {{ u.sub_days }}d</span>
              {% endif %}
            </td>
            <td><small style="color:var(--gold2);">{{ u.sub_time_left }}</small></td>
            <td><small style="color:var(--muted);">{{ u.sub_expiry or '—' }}</small></td>
            <td>
              {% if u.renew_count > 0 %}
                <span class="days-badge"><i class="fas fa-redo"></i> {{ u.renew_count }}x (+{{ u.renew_days }}d)</span>
              {% else %}
                <small style="color:var(--muted);">—</small>
              {% endif %}
            </td>
            <td>
              <form method="POST" action="/agent/renew_subscription/{{ u.id }}" style="display:flex;gap:4px;justify-content:flex-end;align-items:center;">
                <input type="number" name="days" value="30" min="1" style="width:70px;padding:6px;font-size:.75rem;"/>
                <select name="mode" style="padding:6px;font-size:.75rem;border-radius:8px;background:#000;color:#fff;border:1px solid rgba(245,200,66,.2);">
                  <option value="extend">Extend</option>
                  <option value="reset">Reset</option>
                </select>
                <button type="submit" class="btn btn-gold btn-sm" style="padding:5px 10px;font-size:.7rem;"><i class="fas fa-sync"></i> Apply</button>
              </form>
              <div style="text-align:right;margin-top:4px;">
                <a href="/agent/customer_history/{{ u.id }}" class="btn btn-info btn-xs" style="font-size:.65rem;"><i class="fas fa-history"></i> History</a>
              </div>
            </td>
          </tr>
          {% else %}<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:30px;">No customers yet.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-list-alt" style="color:var(--purple);"></i> My Recent Renewal History (Latest 100)</div>
    <p style="color:var(--muted);font-size:.82rem;margin-bottom:12px;"><i class="fas fa-info-circle"></i> আপনার করা renewal-এর সব লগ।</p>
    <div class="keys-table-wrap">
      <table class="keys-table">
        <thead>
          <tr>
            <th>USERNAME</th>
            <th>DAYS ADDED</th>
            <th>MODE</th>
            <th>OLD EXPIRY</th>
            <th>NEW EXPIRY</th>
            <th>WHEN</th>
          </tr>
        </thead>
        <tbody>
          {% for h in my_history %}
          <tr>
            <td><strong style="color:#FFE28A;">{{ h.username }}</strong></td>
            <td>
              {% if h.mode == 'reset' %}
                <span class="days-badge warn"><i class="fas fa-sync"></i> Reset +{{ h.days_added }}d</span>
              {% else %}
                <span class="days-badge permanent"><i class="fas fa-plus-circle"></i> +{{ h.days_added }} days</span>
              {% endif %}
            </td>
            <td><small style="color:var(--muted);">{{ h.mode }}</small></td>
            <td><small style="color:var(--muted);">{{ h.old_expiry[:16] if h.old_expiry else '—' }}</small></td>
            <td><small style="color:#7dd3fc;">{{ h.new_expiry[:16] if h.new_expiry else '—' }}</small></td>
            <td><small style="color:var(--muted);">{{ h.created_at[:16] if h.created_at else '—' }}</small></td>
          </tr>
          {% else %}<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:30px;">No renewal history yet.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <a href="/agent/dashboard" class="back-link"><i class="fas fa-arrow-left"></i> Back to Dashboard</a>
</div></body></html>'''


# ============================================================
#  AGENT CUSTOMER HISTORY PAGE
# ============================================================
AGENT_CUSTOMER_HISTORY_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Customer History - Agent</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''
.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:12px}
.detail-item{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:14px;padding:14px 16px}
.detail-label{font-size:.65rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--gold2);font-weight:700;margin-bottom:6px}
.detail-value{font-size:.95rem;color:#fff;word-break:break-all;font-family:var(--mono);line-height:1.4}
</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-user-circle"></i> {{ user.username }}</h1>
    <div class="flex">
      <a href="/agent/subscription" class="btn btn-gold btn-sm"><i class="fas fa-arrow-left"></i> Subscription</a>
      <a href="/agent/dashboard" class="btn btn-primary btn-sm"><i class="fas fa-th-large"></i> Dashboard</a>
      <a href="/agent/logout" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>

  {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}

  <div class="card">
    <div class="card-title"><i class="fas fa-id-card"></i> Customer Information</div>
    <div class="detail-grid">
      <div class="detail-item"><div class="detail-label">User ID</div><div class="detail-value">#{{ user.id }}</div></div>
      <div class="detail-item"><div class="detail-label">Username</div><div class="detail-value">{{ user.username }}</div></div>
      <div class="detail-item"><div class="detail-label">Email</div><div class="detail-value">{{ user.email or '—' }}</div></div>
      <div class="detail-item"><div class="detail-label">Bot UID</div><div class="detail-value">{{ user.bot_uid or '—' }}</div></div>
      <div class="detail-item"><div class="detail-label">Status</div><div class="detail-value">{% if user.sub_status == 'expired' %}Expired{% elif user.sub_status == 'unlimited' %}Unlimited{% else %}Active{% endif %}</div></div>
      <div class="detail-item"><div class="detail-label">Days Left</div><div class="detail-value">{{ user.sub_days }}{% if user.sub_status == 'unlimited' %}∞{% endif %}</div></div>
      <div class="detail-item"><div class="detail-label">Time Left</div><div class="detail-value">{{ user.sub_time_left }}</div></div>
      <div class="detail-item"><div class="detail-label">Expires On</div><div class="detail-value">{{ user.sub_expiry or '—' }}</div></div>
      <div class="detail-item"><div class="detail-label">Total Renewals</div><div class="detail-value">{{ user.renew_count }}x (+{{ user.renew_days }}d)</div></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-sync"></i> Quick Renew</div>
    <form method="POST" action="/agent/renew_subscription/{{ user.id }}" class="flex">
      <label style="color:var(--gold2);font-weight:600;">Mode:</label>
      <select name="mode">
        <option value="extend">Extend from current</option>
        <option value="reset">New package (reset)</option>
      </select>
      <label style="color:var(--gold2);font-weight:600;">Days:</label>
      <input type="number" name="days" value="30" min="1" style="width:120px;"/>
      <button type="submit" class="btn btn-gold"><i class="fas fa-save"></i> Apply</button>
    </form>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-history" style="color:var(--purple);"></i> Renewal History ({{ sub_history|length }})</div>
    <div class="keys-table-wrap">
      <table class="keys-table">
        <thead>
          <tr>
            <th>#</th>
            <th>DAYS ADDED</th>
            <th>MODE</th>
            <th>OLD EXPIRY</th>
            <th>NEW EXPIRY</th>
            <th>EXTENDED BY</th>
            <th>ROLE</th>
            <th>WHEN</th>
          </tr>
        </thead>
        <tbody>
          {% for h in sub_history %}
          <tr>
            <td>{{ loop.index }}</td>
            <td>
              {% if h.mode == 'reset' %}
                <span class="days-badge warn"><i class="fas fa-sync"></i> Reset +{{ h.days_added }}d</span>
              {% else %}
                <span class="days-badge permanent"><i class="fas fa-plus-circle"></i> +{{ h.days_added }} days</span>
              {% endif %}
            </td>
            <td><small style="color:var(--muted);">{{ h.mode }}</small></td>
            <td><small style="color:var(--muted);">{{ h.old_expiry[:16] if h.old_expiry else '—' }}</small></td>
            <td><small style="color:#7dd3fc;">{{ h.new_expiry[:16] if h.new_expiry else '—' }}</small></td>
            <td><strong style="color:#FFE28A;">{{ h.extended_by or '—' }}</strong></td>
            <td>
              {% if h.extended_by_role == 'owner' %}<span class="creator-badge owner"><i class="fas fa-crown"></i> OWNER</span>
              {% elif h.extended_by_role == 'agent' %}<span class="creator-badge agent"><i class="fas fa-user-tie"></i> AGENT</span>
              {% else %}<small style="color:var(--muted);">—</small>{% endif %}
            </td>
            <td><small style="color:var(--muted);">{{ h.created_at[:16] if h.created_at else '—' }}</small></td>
          </tr>
          {% else %}<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:30px;">No renewal history yet.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <a href="/agent/subscription" class="back-link"><i class="fas fa-arrow-left"></i> Back to Subscription</a>
</div></body></html>'''


# ============================================================
#  OWNER DASHBOARD
# ============================================================
OWNER_DASHBOARD_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Owner Dashboard - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-crown"></i> Owner Dashboard</h1>
    <div class="flex">
      <form method="POST" action="/owner/reset_all_bots" onsubmit="return confirm('Reset all bots?');" style="display:inline;"><button type="submit" class="btn btn-reset btn-sm"><i class="fas fa-power-off"></i> Reset All</button></form>
      {% if global_stop %}
        <form method="POST" action="/owner/start_all_bots" style="display:inline;"><button type="submit" class="btn btn-success btn-sm"><i class="fas fa-play"></i> Resume All</button></form>
      {% else %}
        <form method="POST" action="/owner/stop_all_bots" onsubmit="return confirm('Stop all bots globally?');" style="display:inline;"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-hand-paper"></i> Stop All (Lock)</button></form>
      {% endif %}
      <span class="welcome-text">Welcome, <strong>{{ session.username }}</strong></span>
      <a href="/owner/logout" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>

  {% if global_stop %}
  <div class="card" style="background:rgba(212,42,58,.1);border-color:rgba(212,42,58,.4);">
    <div style="display:flex;align-items:center;gap:12px;color:var(--red2);font-weight:700;"><i class="fas fa-exclamation-triangle" style="font-size:1.5rem;"></i> <span>Global Bot Stop চালু! সব user bot বন্ধ (force-active বাদে)।</span></div>
  </div>
  {% endif %}

  <div class="card">
    <div class="card-title"><i class="fas fa-server"></i> Server Resources</div>
    <div class="system-stats">
      <div class="stat-card"><div class="stat-label">CPU</div><div class="stat-value">{{ cpu }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ cpu }}%;"></div></div></div>
      <div class="stat-card"><div class="stat-label">RAM</div><div class="stat-value">{{ ram_percent }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ ram_percent }}%;"></div></div></div>
      <div class="stat-card"><div class="stat-label">Disk</div><div class="stat-value">{{ disk_percent }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ disk_percent }}%;"></div></div></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-bullhorn"></i> Global Notice Settings</div>
    <p style="color:var(--muted);font-size:.82rem;margin-bottom:12px;"><i class="fas fa-info-circle"></i> User panel-এ button আকারে দেখানো হবে।</p>
    <form method="POST" action="/owner/set_global_notice">
      <textarea name="notice_text" rows="3">{{ global_notice }}</textarea>
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;">
        <button type="submit" class="btn btn-gold btn-sm"><i class="fas fa-save"></i> Save Notice</button>
        <a href="/owner/preview_global_notice" target="_blank" class="btn btn-info btn-sm"><i class="fas fa-eye"></i> Preview</a>
      </div>
    </form>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-bell"></i> User Login Notice (Popup)</div>
    <p style="color:var(--muted);font-size:.82rem;margin-bottom:12px;">
      <i class="fas fa-info-circle"></i> এই notice enable করলে <b style="color:var(--gold2);">প্রত্যেক user</b> তার panel-এ login করলে এই popup দেখতে পাবে।
    </p>
    <form method="POST" action="/owner/set_user_login_notice">
      <div class="flex" style="margin-bottom:12px;">
        <label style="color:var(--gold2);font-weight:600;">Status:</label>
        {% if login_notice_enabled %}
          <span class="badge badge-running">🟢 ENABLED</span>
        {% else %}
          <span class="badge badge-stopped">🔴 DISABLED</span>
        {% endif %}
      </div>
      <textarea name="notice_text" rows="4" placeholder="Type notice text here...">{{ login_notice_text }}</textarea>
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;">
        <button type="submit" name="action" value="save" class="btn btn-gold btn-sm"><i class="fas fa-save"></i> Save</button>
        {% if login_notice_enabled %}
          <button type="submit" name="action" value="disable" class="btn btn-danger btn-sm"><i class="fas fa-toggle-off"></i> Disable Notice</button>
        {% else %}
          <button type="submit" name="action" value="enable" class="btn btn-success btn-sm"><i class="fas fa-toggle-on"></i> Enable Notice</button>
        {% endif %}
      </div>
    </form>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-upload"></i> Owner Actions</div>
    <div class="flex">
      <a href="/owner/files" class="btn btn-gold"><i class="fas fa-folder"></i> File Manager</a>
      <a href="/owner/subscription" class="btn btn-info"><i class="fas fa-clock-rotate-left"></i> Subscription Management</a>
    </div>
    <div style="margin-top:14px;">
      <div style="font-size:.82rem;color:var(--gold2);font-weight:600;margin-bottom:8px;">Upload mahir.py</div>
      <form method="POST" action="/owner/upload_mahir" enctype="multipart/form-data" class="upload-form" id="uploadMahirForm">
        <input type="file" name="mahir_file" accept=".py" required style="flex:1;min-width:200px;" id="mahirFileInput"/>
        <button type="submit" class="btn btn-warning btn-sm" id="uploadMahirBtn"><i class="fas fa-cloud-upload-alt"></i> Upload & Auto-Reset</button>
      </form>
    </div>
    <div style="margin-top:14px;">
      <div style="font-size:.82rem;color:var(--gold2);font-weight:600;margin-bottom:8px;">Upload users.db (auto-create bots)</div>
      <form method="POST" action="/owner/upload_users_db" enctype="multipart/form-data" class="upload-form" id="uploadDbForm">
        <input type="file" name="db_file" accept=".db" required style="flex:1;min-width:200px;"/>
        <button type="submit" class="btn btn-warning btn-sm" id="uploadDbBtn" onclick="return confirm('Replace DB and recreate all bots?');"><i class="fas fa-upload"></i> Upload DB</button>
      </form>
    </div>
    {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-user-tie"></i> Agent Management</div>
    <form method="POST" action="/owner/create_agent" class="flex" id="createAgentForm">
      <input type="text" name="username" placeholder="Username" required class="flex-grow"/>
      <input type="email" name="email" placeholder="Email" required class="flex-grow"/>
      <input type="password" name="password" placeholder="Password" required class="flex-grow"/>
      <button type="submit" class="btn btn-success btn-sm" id="createAgentBtn"><i class="fas fa-user-plus"></i> Create</button>
    </form>
    <div class="table-wrapper">
      <table>
        <thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Keys/Limit</th><th>DB</th><th>Action</th></tr></thead>
        <tbody>
          {% for agent in agents %}
          <tr>
            <td>{{ agent.id }}</td>
            <td><strong>{{ agent.username }}</strong></td>
            <td>{{ agent.email or '-' }}</td>
            <td>
              <span class="badge badge-admin">{{ agent.key_count }}{% if agent.key_limit >= 0 %} / {{ agent.key_limit }}{% else %} / ∞{% endif %}</span>
              <form method="POST" action="/owner/set_key_limit/{{ agent.id }}" style="margin-top:4px;display:flex;gap:4px;">
                <input type="number" name="key_limit" value="{{ agent.key_limit }}" min="-1" style="width:70px;padding:4px;font-size:.72rem;"/>
                <button type="submit" class="btn btn-gold btn-xs"><i class="fas fa-save"></i></button>
              </form>
            </td>
            <td>
              {% if agent.can_manage_db %}<span class="badge badge-running">ON</span>{% else %}<span class="badge badge-stopped">OFF</span>{% endif %}
              <form method="POST" action="/owner/toggle_db_access/{{ agent.id }}" style="margin-top:4px;">
                <button type="submit" class="btn btn-xs {% if agent.can_manage_db %}btn-danger{% else %}btn-success{% endif %}"><i class="fas fa-toggle-{% if agent.can_manage_db %}on{% else %}off{% endif %}"></i></button>
              </form>
            </td>
            <td>
              <div class="td-actions">
                <a href="/owner/login_as/{{ agent.id }}" class="btn btn-warning btn-sm" title="Login as this user"><i class="fas fa-sign-in-alt"></i></a>
                <a href="/owner/user_details/{{ agent.id }}" class="btn btn-info btn-sm"><i class="fas fa-eye"></i></a>
                <form method="POST" action="/owner/delete_agent/{{ agent.id }}" onsubmit="return confirm('Delete?');" style="display:inline;"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button></form>
              </div>
            </td>
          </tr>
          {% else %}<tr class="empty-row"><td colspan="6">No agents</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-key"></i> Generate Key (Owner)</div>
    <form method="POST" action="/owner/create_key" class="flex">
      <div class="input-group"><label>Days (0=Permanent):</label><input type="number" name="days_valid" value="30" min="0" style="width:110px;"/></div>
      <button type="submit" class="btn btn-gold"><i class="fas fa-plus-circle"></i> Generate</button>
    </form>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-users"></i> Registered Users</div>
    <div class="table-wrapper">
      <table>
        <thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Bot Status</th><th>Days Left</th><th>Renewals</th><th>Role</th><th style="text-align:right;">Actions</th></tr></thead>
        <tbody>
          {% for user in users %}
          <tr>
            <td>{{ user.id }}</td>
            <td><strong>{{ user.username }}</strong>{% if user.created_by_agent %}<br><small style="color:var(--muted);font-size:.68rem;">by: {{ user.created_by_agent }}</small>{% endif %}</td>
            <td>{{ user.email or '-' }}</td>
            <td>{% if user.is_agent %}<span class="badge badge-agent">AGENT</span>{% elif user.bot_status == 'running' %}<span class="badge badge-running">Running</span>{% elif user.bot_status == 'expired' %}<span class="badge badge-expired">Expired</span>{% elif user.bot_status == 'stopped' %}<span class="badge badge-stopped">Stopped</span>{% else %}<span class="badge badge-unused">{{ user.bot_status or 'Not Configured' }}</span>{% endif %}</td>
            <td>
              {% if user.sub_status == 'unlimited' %}
                <span class="days-badge permanent"><i class="fas fa-infinity"></i> ∞</span>
              {% elif user.sub_status == 'expired' %}
                <span class="days-badge danger"><i class="fas fa-times-circle"></i> Expired</span>
              {% elif user.sub_days <= 3 %}
                <span class="days-badge danger"><i class="fas fa-exclamation-triangle"></i> {{ user.sub_days }}d</span>
              {% elif user.sub_days <= 7 %}
                <span class="days-badge warn"><i class="fas fa-clock"></i> {{ user.sub_days }}d</span>
              {% else %}
                <span class="days-badge"><i class="fas fa-check-circle"></i> {{ user.sub_days }}d</span>
              {% endif %}
            </td>
            <td>
              {% if user.renew_count and user.renew_count > 0 %}
                <span class="days-badge"><i class="fas fa-redo"></i> {{ user.renew_count }}x <small style="opacity:.7;">(+{{ user.renew_days }}d)</small></span>
              {% else %}
                <span style="color:var(--muted);font-size:.75rem;">—</span>
              {% endif %}
            </td>
            <td>{% if user.is_admin %}<span class="badge badge-owner">OWNER</span>{% elif user.is_agent %}<span class="badge badge-agent">Agent</span>{% else %}<span class="badge badge-user">User</span>{% endif %}</td>
            <td>
              <div class="td-actions">
                <a href="/owner/login_as/{{ user.id }}" class="btn btn-warning btn-sm" title="Login as user"><i class="fas fa-sign-in-alt"></i></a>
                <a href="/owner/user_details/{{ user.id }}" class="btn btn-info btn-sm" title="Details"><i class="fas fa-eye"></i></a>
                {% if not user.is_admin and not user.is_agent %}
                  {% if user.bot_disabled_by_admin %}
                  <form method="POST" action="/owner/toggle_user_bot/{{ user.id }}" style="display:inline;"><button type="submit" class="btn btn-success btn-sm" title="Enable bot"><i class="fas fa-play"></i></button></form>
                  {% else %}
                  <button type="button" class="btn btn-warning btn-sm" onclick="openDisableModal({{ user.id }}, '{{ user.username }}')" title="Disable bot"><i class="fas fa-hand-paper"></i></button>
                  {% endif %}
                  <form method="POST" action="/owner/force_start_bot/{{ user.id }}" style="display:inline;"><button type="submit" class="btn btn-gold btn-sm" title="Force start (override global stop)"><i class="fas fa-bolt"></i></button></form>
                  <form method="POST" action="/owner/delete_user/{{ user.id }}" onsubmit="return confirm('Delete?');" style="display:inline;"><button type="submit" class="btn btn-danger btn-sm" title="Delete"><i class="fas fa-trash"></i></button></form>
                {% endif %}
              </div>
            </td>
          </tr>
          {% else %}<tr class="empty-row"><td colspan="8">No users</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-list-alt" style="color:var(--purple);"></i> Recent Subscription History (Latest 100)</div>
    <div class="keys-table-wrap">
      <table class="keys-table">
        <thead>
          <tr>
            <th>USERNAME</th>
            <th>DAYS ADDED</th>
            <th>MODE</th>
            <th>OLD EXPIRY</th>
            <th>NEW EXPIRY</th>
            <th>EXTENDED BY</th>
            <th>ROLE</th>
            <th>WHEN</th>
          </tr>
        </thead>
        <tbody>
          {% for h in sub_history %}
          <tr>
            <td><strong style="color:#FFE28A;">{{ h.username }}</strong></td>
            <td>
              {% if h.mode == 'reset' %}
                <span class="days-badge warn"><i class="fas fa-sync"></i> Reset +{{ h.days_added }}d</span>
              {% else %}
                <span class="days-badge permanent"><i class="fas fa-plus-circle"></i> +{{ h.days_added }} days</span>
              {% endif %}
            </td>
            <td><small style="color:var(--muted);">{{ h.mode }}</small></td>
            <td><small style="color:var(--muted);">{{ h.old_expiry[:16] if h.old_expiry else '—' }}</small></td>
            <td><small style="color:#7dd3fc;">{{ h.new_expiry[:16] if h.new_expiry else '—' }}</small></td>
            <td>
              {% if h.extended_by_role == 'owner' or h.extended_by == 'MAHIR TCP' or h.extended_by == 'OWNER' %}
                <span class="creator-badge owner"><i class="fas fa-crown"></i> OWNER</span>
              {% else %}
                <span class="creator-badge agent"><i class="fas fa-user-tie"></i> {{ h.extended_by or '—' }}</span>
              {% endif %}
            </td>
            <td><small style="color:var(--muted);">{{ h.extended_by_role or '—' }}</small></td>
            <td><small style="color:var(--muted);">{{ h.created_at[:16] if h.created_at else '—' }}</small></td>
          </tr>
          {% else %}<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:30px;">No subscription history yet.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-key" style="color:var(--purple);"></i> Recent Keys (All)</div>
    <div class="keys-table-wrap">
      <table class="keys-table">
        <thead>
          <tr>
            <th>KEY</th>
            <th>CREATED BY</th>
            <th>CREATED</th>
            <th>DURATION</th>
            <th>USED BY</th>
            <th>STATUS</th>
            <th style="text-align:right;">ACTION</th>
          </tr>
        </thead>
        <tbody>
          {% for key in keys %}
          <tr>
            <td><span class="key-code">{{ key.key }}</span></td>
            <td>
              {% if key.created_by == 'MAHIR TCP' or key.created_by == 'admin' or key.created_by == 'OWNER' %}
                <span class="creator-badge owner"><i class="fas fa-crown"></i> OWNER</span>
              {% else %}
                <span class="creator-badge agent"><i class="fas fa-user-tie"></i> {{ key.created_by or '—' }}</span>
              {% endif %}
            </td>
            <td style="color:var(--muted);font-size:.78rem;">{{ key.created_at[:16] if key.created_at else '—' }}</td>
            <td>
              {% if key.duration_days == 0 %}
                <span class="duration-tag" style="background:rgba(74,222,128,.15);color:#4ade80;border-color:rgba(74,222,128,.4);">∞ Permanent</span>
              {% else %}
                <span class="duration-tag">{{ key.duration_days }}d</span>
              {% endif %}
            </td>
            <td>{% if key.used_by %}<span class="used-by"><i class="fas fa-user user-icon"></i>{{ key.used_by }}</span>{% else %}<span style="color:var(--muted);">—</span>{% endif %}</td>
            <td>{% if key.is_used %}<span class="status-badge used"><i class="fas fa-check-circle"></i> USED</span>{% else %}<span class="status-badge available"><i class="fas fa-clock"></i> AVAILABLE</span>{% endif %}</td>
            <td style="text-align:right;">
              <form method="POST" action="/owner/delete_key/{{ key.id }}" onsubmit="return confirm('Delete this key and associated users?');" style="display:inline;">
                <button type="submit" class="delete-key-btn" title="Delete"><i class="fas fa-trash"></i></button>
              </form>
            </td>
          </tr>
          {% else %}<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:30px;">No keys yet.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  <a href="/logout" class="back-link"><i class="fas fa-arrow-left"></i> Back</a>
</div>

<div class="modal-overlay" id="disableModal">
  <div class="modal-box" style="max-width:500px;">
    <button class="modal-close" onclick="closeDisableModal()">&times;</button>
    <div class="modal-title"><i class="fas fa-hand-paper" style="color:var(--red2);"></i> Disable User Bot</div>
    <p style="color:var(--muted);font-size:.85rem;margin-bottom:12px;">User: <strong id="disableUserName" style="color:var(--gold2);"></strong></p>
    <form method="POST" id="disableForm">
      <div class="modal-section">
        <h3><i class="fas fa-comment"></i> Reason (User দেখতে পাবে)</h3>
        <textarea name="reason" rows="4">Owner আপনার বট কিছু কাজের জন্য বন্ধ করে দিয়েছে। আপনি owner এর সাথে যোগাযোগ করুন।</textarea>
      </div>
      <div style="text-align:right;display:flex;gap:8px;justify-content:flex-end;">
        <button type="button" onclick="closeDisableModal()" class="modal-btn modal-btn-cancel">Cancel</button>
        <button type="submit" class="modal-btn modal-btn-danger"><i class="fas fa-hand-paper"></i> Disable Bot</button>
      </div>
    </form>
  </div>
</div>

<script>
function openDisableModal(uid, uname){
  document.getElementById('disableUserName').textContent = uname;
  document.getElementById('disableForm').action = '/owner/disable_user/' + uid;
  document.getElementById('disableModal').classList.add('active');
}
function closeDisableModal(){document.getElementById('disableModal').classList.remove('active');}
document.getElementById('disableModal').addEventListener('click', function(e){if(e.target===this)closeDisableModal();});
document.getElementById('uploadMahirForm').addEventListener('submit',function(e){var f=document.getElementById('mahirFileInput').files[0];if(f && f.name.toLowerCase()!=='mahir.py'){e.preventDefault();alert('❌ Only "mahir.py"!');return false;}var b=document.getElementById('uploadMahirBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Uploading...';});
document.getElementById('uploadDbForm').addEventListener('submit',function(){var b=document.getElementById('uploadDbBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Processing...';});
document.getElementById('createAgentForm').addEventListener('submit',function(){var b=document.getElementById('createAgentBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Creating...';});
</script>
</body></html>'''


# ============================================================
#  USER DETAILS (Owner view)
# ============================================================
USER_DETAILS_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>User Details - OWNER PANEL</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''
.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:12px}
.detail-item{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:14px;padding:14px 16px}
.detail-label{font-size:.65rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--gold2);font-weight:700;margin-bottom:6px}
.detail-value{font-size:.95rem;color:#fff;word-break:break-all;font-family:var(--mono);line-height:1.4}
.detail-value.big{font-size:1.1rem;font-weight:700}
.copy-btn{background:rgba(245,200,66,.1);border:1px solid rgba(245,200,66,.25);color:var(--gold);padding:4px 10px;border-radius:8px;cursor:pointer;font-size:.7rem;margin-left:6px;font-weight:600}
.live-badge{display:inline-flex;align-items:center;gap:8px;padding:6px 14px;border-radius:999px;font-size:.75rem;font-weight:700;text-transform:uppercase}
.live-online{background:rgba(74,222,128,.12);color:#4ade80;border:1px solid rgba(74,222,128,.35)}
.live-offline{background:rgba(212,42,58,.12);color:#ff5a76;border:1px solid rgba(212,42,58,.35)}
</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-user-circle"></i> {{ user.username }}</h1>
    <div class="flex">
      {% if not user.is_admin %}
      <a href="/owner/login_as/{{ user.id }}" class="btn btn-warning btn-sm"><i class="fas fa-sign-in-alt"></i> Login as this user</a>
      {% endif %}
      <a href="/owner/dashboard" class="btn btn-primary btn-sm"><i class="fas fa-arrow-left"></i> Dashboard</a>
      <a href="/owner/logout" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-id-card"></i> Account Information</div>
    <div style="text-align:center;margin-bottom:16px;">
      {% if user.bot_disabled_by_admin %}<span class="live-badge live-offline"><i class="fas fa-hand-paper"></i> OWNER DISABLED</span>
      {% elif user.sub_status == 'expired' %}<span class="live-badge live-offline"><i class="fas fa-clock"></i> EXPIRED</span>
      {% elif live and live.is_running %}<span class="live-badge live-online"><i class="fas fa-circle"></i> LIVE · RUNNING</span>
      {% else %}<span class="live-badge live-offline"><i class="fas fa-circle"></i> OFFLINE</span>{% endif %}
    </div>
    <div class="detail-grid">
      <div class="detail-item"><div class="detail-label">User ID</div><div class="detail-value big">#{{ user.id }}</div></div>
      <div class="detail-item"><div class="detail-label">Username</div><div class="detail-value big">{{ user.username }}<button class="copy-btn" onclick="copyT('{{ user.username }}')">Copy</button></div></div>
      <div class="detail-item"><div class="detail-label">Email</div><div class="detail-value">{{ user.email }}</div></div>
      <div class="detail-item"><div class="detail-label">Password</div><div class="detail-value">{{ user.password }}<button class="copy-btn" onclick="copyT('{{ user.password }}')">Copy</button></div></div>
      <div class="detail-item"><div class="detail-label">Role</div><div class="detail-value">{% if user.is_admin %}OWNER{% elif user.is_agent %}AGENT{% else %}USER{% endif %}</div></div>
      <div class="detail-item"><div class="detail-label">Created</div><div class="detail-value">{{ user.created_at }}</div></div>
      <div class="detail-item">
        <div class="detail-label">Subscription</div>
        <div class="detail-value">
          {% if user.sub_status == 'unlimited' %}<span style="color:#4ade80;">∞ Unlimited</span>
          {% elif user.sub_status == 'expired' %}<span style="color:#ff5a76;">Expired</span>
          {% else %}<span style="color:#4ade80;">{{ user.sub_days }} days left</span>{% endif %}
          <br><small style="color:var(--muted);">Time: {{ user.sub_time_left }}</small>
          {% if user.sub_expiry %}<br><small style="color:var(--muted);">Expires: {{ user.sub_expiry }}</small>{% endif %}
        </div>
      </div>
      <div class="detail-item"><div class="detail-label">Renew Count</div><div class="detail-value big">{{ user.renew_count }}x <small style="color:#4ade80;">(+{{ user.renew_days }}d)</small></div></div>
      <div class="detail-item"><div class="detail-label">Registration Key</div><div class="detail-value">{{ user.registration_key }}</div></div>
      <div class="detail-item"><div class="detail-label">Created By</div><div class="detail-value">{{ user.created_by_agent or 'Owner' }}</div></div>
    </div>
  </div>

  {% if user.bot_uid != '—' and not user.is_agent %}
  <div class="card">
    <div class="card-title"><i class="fas fa-robot"></i> Bot Information</div>
    <div class="detail-grid">
      <div class="detail-item"><div class="detail-label">Bot UID</div><div class="detail-value big">{{ user.bot_uid }}</div></div>
      <div class="detail-item"><div class="detail-label">Bot Password</div><div class="detail-value">{{ user.bot_pw }}</div></div>
      <div class="detail-item"><div class="detail-label">Bot File</div><div class="detail-value">{{ user.bot_file }}</div></div>
      <div class="detail-item"><div class="detail-label">Owner UIDs</div><div class="detail-value">{{ user.admin_uid }}</div></div>
      <div class="detail-item"><div class="detail-label">Bot Status</div><div class="detail-value">{{ user.bot_status }}</div></div>
      <div class="detail-item"><div class="detail-label">Force Active</div><div class="detail-value">{% if user.bot_force_active %}<span style="color:#4ade80;">YES</span>{% else %}<span style="color:var(--muted);">NO</span>{% endif %}</div></div>
    </div>
  </div>
  {% endif %}

  {% if live and not user.is_agent %}
  <div class="card">
    <div class="card-title"><i class="fas fa-satellite-dish"></i> Live Bot Status</div>
    <div class="detail-grid">
      <div class="detail-item"><div class="detail-label">Live Bot Name</div><div class="detail-value big">{{ live.bot_name }}</div></div>
      <div class="detail-item"><div class="detail-label">Region</div><div class="detail-value">{{ live.bot_region }}</div></div>
      <div class="detail-item"><div class="detail-label">Uptime</div><div class="detail-value">{{ live.uptime }}</div></div>
      <div class="detail-item"><div class="detail-label">CPU</div><div class="detail-value">{{ live.cpu }}%</div></div>
      <div class="detail-item"><div class="detail-label">RAM</div><div class="detail-value">{{ live.ram }}%</div></div>
      <div class="detail-item"><div class="detail-label">Last Message</div><div class="detail-value">{{ live.last_message }}</div></div>
    </div>
  </div>
  {% endif %}

  {% if not user.is_agent and not user.is_admin %}
  <div class="card">
    <div class="card-title"><i class="fas fa-sync"></i> Subscription Management</div>
    <form method="POST" action="/owner/renew_subscription/{{ user.id }}" class="flex">
      <label style="color:var(--gold2);font-weight:600;">Package:</label>
      <select name="mode">
        <option value="extend">Extend from current</option>
        <option value="reset">New package (reset)</option>
      </select>
      <label style="color:var(--gold2);font-weight:600;">Days:</label>
      <input type="number" name="days" value="30" min="1" style="width:120px;"/>
      <button type="submit" class="btn btn-gold"><i class="fas fa-save"></i> Apply</button>
    </form>
  </div>
  {% endif %}

  {% if sub_history %}
  <div class="card">
    <div class="card-title"><i class="fas fa-history"></i> Renewal History ({{ sub_history|length }})</div>
    <div class="keys-table-wrap">
      <table class="keys-table">
        <thead>
          <tr>
            <th>#</th>
            <th>DAYS ADDED</th>
            <th>MODE</th>
            <th>OLD EXPIRY</th>
            <th>NEW EXPIRY</th>
            <th>EXTENDED BY</th>
            <th>ROLE</th>
            <th>WHEN</th>
          </tr>
        </thead>
        <tbody>
          {% for h in sub_history %}
          <tr>
            <td>{{ loop.index }}</td>
            <td>
              {% if h.mode == 'reset' %}
                <span class="days-badge warn"><i class="fas fa-sync"></i> Reset +{{ h.days_added }}d</span>
              {% else %}
                <span class="days-badge permanent"><i class="fas fa-plus-circle"></i> +{{ h.days_added }} days</span>
              {% endif %}
            </td>
            <td><small style="color:var(--muted);">{{ h.mode }}</small></td>
            <td><small style="color:var(--muted);">{{ h.old_expiry[:16] if h.old_expiry else '—' }}</small></td>
            <td><small style="color:#7dd3fc;">{{ h.new_expiry[:16] if h.new_expiry else '—' }}</small></td>
            <td><strong style="color:#FFE28A;">{{ h.extended_by or '—' }}</strong></td>
            <td>
              {% if h.extended_by_role == 'owner' %}<span class="creator-badge owner"><i class="fas fa-crown"></i> OWNER</span>
              {% elif h.extended_by_role == 'agent' %}<span class="creator-badge agent"><i class="fas fa-user-tie"></i> AGENT</span>
              {% else %}<small style="color:var(--muted);">—</small>{% endif %}
            </td>
            <td><small style="color:var(--muted);">{{ h.created_at[:16] if h.created_at else '—' }}</small></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endif %}

  {% if not user.is_agent and not user.is_admin %}
  <div class="card">
    <div class="card-title"><i class="fas fa-cog"></i> Bot Controls</div>
    <div class="flex" style="gap:10px;">
      {% if user.bot_disabled_by_admin %}
      <form method="POST" action="/owner/toggle_user_bot/{{ user.id }}"><button type="submit" class="btn btn-success"><i class="fas fa-play"></i> Enable Bot</button></form>
      {% else %}
      <button type="button" class="btn btn-warning" onclick="document.getElementById('disableModalDetails').classList.add('active')"><i class="fas fa-hand-paper"></i> Disable Bot</button>
      {% endif %}
      <form method="POST" action="/owner/force_start_bot/{{ user.id }}"><button type="submit" class="btn btn-gold"><i class="fas fa-bolt"></i> Force Start (Global Stop Override)</button></form>
      {% if user.sub_status == 'expired' or not user.bot_file %}
      <form method="POST" action="/owner/recreate_bot/{{ user.id }}"><button type="submit" class="btn btn-gold"><i class="fas fa-plus-circle"></i> Recreate Bot File</button></form>
      {% endif %}
    </div>
  </div>
  {% endif %}

  {% if user.is_agent %}
  <div class="card">
    <div class="card-title"><i class="fas fa-key"></i> Key Limit</div>
    <form method="POST" action="/owner/set_key_limit/{{ user.id }}" class="flex">
      <label style="color:var(--gold2);font-weight:600;">Key Limit (-1=∞):</label>
      <input type="number" name="key_limit" value="{{ user.key_limit }}" min="-1" style="width:140px;"/>
      <button type="submit" class="btn btn-gold btn-sm"><i class="fas fa-save"></i> Save</button>
    </form>
  </div>

  <div class="card">
    <div class="card-title"><i class="fas fa-database"></i> DB Access</div>
    <div class="flex">
      <span>Status: {% if user.can_manage_db %}<span class="badge badge-running">ON</span>{% else %}<span class="badge badge-stopped">OFF</span>{% endif %}</span>
      <form method="POST" action="/owner/toggle_db_access/{{ user.id }}"><button type="submit" class="btn {% if user.can_manage_db %}btn-danger{% else %}btn-success{% endif %} btn-sm"><i class="fas fa-toggle-{% if user.can_manage_db %}on{% else %}off{% endif %}"></i> Toggle</button></form>
    </div>
  </div>
  {% endif %}

  <a href="/owner/dashboard" class="back-link"><i class="fas fa-arrow-left"></i> Back</a>
</div>

<div class="modal-overlay" id="disableModalDetails">
  <div class="modal-box" style="max-width:500px;">
    <button class="modal-close" onclick="document.getElementById('disableModalDetails').classList.remove('active')">&times;</button>
    <div class="modal-title"><i class="fas fa-hand-paper" style="color:var(--red2);"></i> Disable Bot</div>
    <form method="POST" action="/owner/disable_user/{{ user.id }}">
      <textarea name="reason" rows="4">Owner আপনার বট কিছু কাজের জন্য বন্ধ করে দিয়েছে। আপনি owner এর সাথে যোগাযোগ করুন।</textarea>
      <div style="margin-top:14px;text-align:right;display:flex;gap:8px;justify-content:flex-end;">
        <button type="button" onclick="document.getElementById('disableModalDetails').classList.remove('active')" class="btn btn-clear btn-sm">Cancel</button>
        <button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-hand-paper"></i> Disable</button>
      </div>
    </form>
  </div>
</div>

<script>
function copyT(t){navigator.clipboard.writeText(t).then(function(){alert('Copied!');}).catch(function(){var x=document.createElement('textarea');x.value=t;document.body.appendChild(x);x.select();document.execCommand('copy');x.remove();alert('Copied!');});}
</script>
</body></html>'''


# ============================================================
#  FILE MANAGER
# ============================================================
FILE_MANAGER_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>File Manager - OWNER</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''
.modal-box.fullscreen{max-width:96vw !important;width:96vw;height:94vh;max-height:94vh}
.modal-box.fullscreen #editContent{height:calc(94vh - 200px) !important}
.fs-btn{position:absolute;top:12px;right:60px;background:rgba(59,140,255,.15);border:1px solid rgba(59,140,255,.35);color:#6db2ff;width:34px;height:34px;border-radius:8px;cursor:pointer;font-size:1rem;display:flex;align-items:center;justify-content:center}
</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-folder-open"></i> File Manager</h1>
    <div class="flex">
      <a href="/owner/dashboard" class="btn btn-primary btn-sm"><i class="fas fa-th-large"></i> Dashboard</a>
      <a href="/owner/logout" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-upload"></i> Upload File (ZIP auto-extracted)</div>
    <form method="POST" action="/owner/upload_file" enctype="multipart/form-data" class="upload-form" id="uploadForm">
      <input type="file" name="uploaded_file" required style="flex:1;min-width:200px;"/>
      <button type="submit" class="btn btn-gold" id="uploadBtn"><i class="fas fa-cloud-upload-alt"></i> Upload</button>
    </form>
    {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-folder"></i> Directory: <span class="current-dir">{{ current_path }}</span></div>
    <div class="breadcrumb"><a href="/owner/files">/</a>{% for part in breadcrumb_parts %} / <a href="/owner/files/{{ part }}">{{ part }}</a>{% endfor %}</div>
    <div class="table-wrapper">
      <table>
        <thead><tr><th>Name</th><th>Size</th><th>Modified</th><th style="text-align:right;">Actions</th></tr></thead>
        <tbody>
          {% if parent_dir is not none %}<tr><td><a href="/owner/files/{{ parent_dir }}" class="folder-link"><i class="fas fa-arrow-up"></i> ..</a></td><td>—</td><td>—</td><td>—</td></tr>{% endif %}
          {% for item in files %}
          <tr>
            <td>{% if item.is_dir %}<a href="/owner/files/{{ item.path }}" class="folder-link"><i class="fas fa-folder"></i> {{ item.name }}</a>{% else %}<span class="file-name"><i class="fas fa-file"></i> {{ item.name }}</span>{% endif %}</td>
            <td>{{ item.size if not item.is_dir else '—' }}</td>
            <td>{{ item.modified }}</td>
            <td><div class="td-actions">{% if not item.is_dir %}<a href="/owner/download/{{ item.path }}" class="btn btn-info btn-sm"><i class="fas fa-download"></i></a><button onclick="editFile('{{ item.path }}')" class="btn btn-warning btn-sm"><i class="fas fa-edit"></i></button><button onclick="deleteFile('{{ item.path }}', this)" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button>{% endif %}</div></td>
          </tr>
          {% else %}<tr class="empty-row"><td colspan="4">Empty</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
<div id="editModal" class="modal-overlay">
  <div class="modal-box" id="editModalBox" style="max-width:820px;">
    <button class="modal-close" onclick="closeEditModal()">&times;</button>
    <button class="fs-btn" onclick="toggleFullscreen()" id="fsBtn" title="Fullscreen"><i class="fas fa-expand"></i></button>
    <div class="modal-title"><i class="fas fa-pen-fancy"></i> Edit: <span id="editFileName" style="color:var(--gold2);font-size:1rem;"></span></div>
    <textarea id="editContent" spellcheck="false" style="width:100%;height:380px;background:#05050c;color:var(--text);border:1px solid rgba(245,200,66,.12);border-radius:12px;padding:14px;font-family:var(--mono);font-size:.85rem;resize:vertical;"></textarea>
    <div class="flex" style="justify-content:flex-end;margin-top:14px;">
      <button onclick="saveEdit()" class="btn btn-success btn-sm"><i class="fas fa-save"></i> Save</button>
      <button onclick="closeEditModal()" class="btn btn-clear btn-sm"><i class="fas fa-times"></i> Cancel</button>
    </div>
    <div id="editStatus" style="margin-top:10px;text-align:center;font-size:.85rem;color:var(--gold2);"></div>
  </div>
</div>
<script>
document.getElementById('uploadForm').addEventListener('submit',function(){var b=document.getElementById('uploadBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Uploading...';});
var currentEditPath='';
function editFile(path){currentEditPath=path;document.getElementById('editFileName').textContent=path;document.getElementById('editContent').value='Loading...';document.getElementById('editStatus').textContent='';document.getElementById('editModal').classList.add('active');fetch('/owner/edit_file/'+encodeURIComponent(path)).then(function(r){return r.json();}).then(function(data){document.getElementById('editContent').value=data.error?('Error: '+data.error):data.content;});}
function closeEditModal(){document.getElementById('editModal').classList.remove('active');var box=document.getElementById('editModalBox');if(box)box.classList.remove('fullscreen');var fs=document.getElementById('fsBtn');if(fs)fs.innerHTML='<i class="fas fa-expand"></i>';}
function toggleFullscreen(){var box=document.getElementById('editModalBox');var btn=document.getElementById('fsBtn');box.classList.toggle('fullscreen');btn.innerHTML=box.classList.contains('fullscreen')?'<i class="fas fa-compress"></i>':'<i class="fas fa-expand"></i>';}
function saveEdit(){var c=document.getElementById('editContent').value;var s=document.getElementById('editStatus');s.textContent='Saving...';fetch('/owner/edit_file/'+encodeURIComponent(currentEditPath),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:c})}).then(function(r){return r.json();}).then(function(data){if(data.success){s.textContent='Saved!';s.style.color='#4ade80';setTimeout(function(){location.reload();},800);}else{s.textContent='Error: '+(data.error||'');s.style.color='#ff5a76';}});}
function deleteFile(path,btn){if(!confirm('Delete?'))return;if(btn)btn.disabled=true;fetch('/owner/delete_file/'+encodeURIComponent(path),{method:'POST'}).then(function(r){return r.json();}).then(function(data){if(data.success){location.reload();}else{alert('Error');if(btn)btn.disabled=false;}});}
document.addEventListener('keydown',function(e){if(e.key==='Escape'){var box=document.getElementById('editModalBox');if(box && box.classList.contains('fullscreen')){toggleFullscreen();return;}closeEditModal();}});
</script></body></html>'''


# ============================================================
#  USER PANEL
# ============================================================
USER_PANEL_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>MAHIR PREMIUM | Bot Controller</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''
.logout-btn{position:fixed;top:20px;right:20px;z-index:999}
.cover-section{position:relative;border-radius:22px;overflow:hidden;margin-bottom:24px;border:1px solid rgba(245,200,66,.15)}
.cover-section img.cover-image{width:100%;height:260px;object-fit:cover;display:block}
.cover-overlay{position:absolute;inset:0;background:linear-gradient(100deg,rgba(7,7,15,.92) 20%,rgba(7,7,15,.5) 60%,rgba(7,7,15,.25));display:flex;flex-direction:column;justify-content:center;padding:32px 40px}
.logo-row{display:flex;align-items:center;gap:18px}
.logo-row img{height:72px;width:72px;border-radius:16px;object-fit:cover;border:1px solid rgba(245,200,66,.25)}
.cover-title{font-size:2.2rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;line-height:1.1}
.cover-sub{color:rgba(233,233,248,.65);font-size:.85rem;letter-spacing:1.5px;margin-top:4px}
.cover-badge{position:absolute;top:18px;right:20px;background:rgba(7,7,15,.7);border:1px solid rgba(245,200,66,.25);padding:6px 16px;border-radius:999px;font-size:.66rem;font-weight:700;color:var(--gold);letter-spacing:2px;text-transform:uppercase}
.download-card{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}
.dc-left{display:flex;align-items:center;gap:16px}
.dc-icon{font-size:2rem;color:var(--gold);background:rgba(245,200,66,.07);width:60px;height:60px;border-radius:16px;display:flex;align-items:center;justify-content:center;border:1px solid rgba(245,200,66,.12)}
.dc-title{font-size:1.05rem;font-weight:700}
.dc-desc{color:var(--muted);font-size:.8rem;margin:2px 0 6px}
.version-tag{display:inline-block;background:rgba(245,200,66,.08);color:var(--gold);padding:2px 10px;border-radius:999px;font-size:.62rem;font-weight:700;margin-right:6px;border:1px solid rgba(245,200,66,.12)}
.tab-container{display:flex;gap:8px;margin-bottom:14px;border-bottom:1px solid rgba(245,200,66,.08);padding-bottom:10px;flex-wrap:wrap}
.tab-btn{background:transparent;border:1px solid transparent;padding:8px 18px;border-radius:10px;color:var(--muted);cursor:pointer;font-weight:600;font-size:.82rem;font-family:inherit}
.tab-btn.active{background:rgba(245,200,66,.08);color:var(--gold);border-color:rgba(245,200,66,.25)}
.tab-content{display:none}.tab-content.active{display:block}
.log-box{background:#05050c;border:1px solid rgba(245,200,66,.08);border-radius:14px;padding:14px;height:400px;overflow-y:auto;font-family:var(--mono);font-size:.78rem}
.log-line{padding:4px 8px;border-left:3px solid var(--gold);margin-bottom:2px;color:#c5c5e5;word-wrap:break-word;white-space:pre-wrap}
.error-line{border-left-color:var(--red);color:#fca5a5;background:rgba(212,42,58,.05)}
.message-card{background:rgba(245,200,66,.03);border:1px solid rgba(245,200,66,.08);border-radius:12px;padding:12px 14px;margin-bottom:10px}
.message-header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding-bottom:8px;margin-bottom:8px;border-bottom:1px solid rgba(245,200,66,.06)}
.message-sender{font-weight:800;color:var(--gold2)}
.message-time{font-size:.65rem;color:var(--muted)}
.message-meta{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;font-size:.8rem}
.message-label{color:var(--gold2);font-weight:600}
.message-value{color:#c5c5e5;word-break:break-all}
.control-bar{display:flex;gap:8px;margin-bottom:10px;align-items:center;flex-wrap:wrap}
.pause-btn{background:rgba(255,255,255,.06);border:1px solid rgba(245,200,66,.15);padding:7px 14px;border-radius:10px;color:var(--text);cursor:pointer;font-size:.78rem;font-family:inherit}
.button-group{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.button-group .btn{flex:1;min-width:110px}
.chart-container{position:relative;height:250px}
.config-form{max-width:560px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
.config-status{padding:13px 16px;background:rgba(245,200,66,.05);border:1px solid rgba(245,200,66,.12);border-left:4px solid var(--gold);border-radius:10px;color:var(--gold2);font-size:.85rem;margin-bottom:18px}
.subscription-hero{background:linear-gradient(135deg,rgba(15,10,30,.95),rgba(10,5,20,.95));border:1px solid rgba(245,200,66,.2);border-radius:20px;padding:22px;margin-bottom:20px;box-shadow:0 15px 40px rgba(0,0,0,.4)}
.sub-hero-title{font-size:.85rem;font-weight:700;color:var(--gold);text-transform:uppercase;letter-spacing:2px;margin-bottom:16px;display:flex;align-items:center;gap:10px}
.sub-hero-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
.sub-hero-item{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:14px;padding:16px;text-align:center}
.sub-hero-label{font-size:.62rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);font-weight:700;margin-bottom:8px}
.sub-hero-value{font-size:1.4rem;font-weight:800;color:#fff;word-break:break-word;line-height:1.2}
.sub-hero-value.small{font-size:1rem}
.sub-hero-value.green{color:#4ade80}
.sub-hero-value.red{color:#ff5a76}
.sub-hero-value.gold{color:#F5C842}
@media(max-width:768px){.download-card{flex-direction:column}.button-group .btn{flex:1 1 45%}.chart-container{height:190px}.sub-hero-value{font-size:1.1rem}}
</style></head><body>

<div class="notice-indicator" id="noticeBtn" onclick="showNotice()" style="display:none;"><i class="fas fa-bullhorn"></i> Owner Notice</div>

<div class="notice-overlay" id="noticePopup">
  <div class="notice-box">
    <button class="modal-close" onclick="hideNotice()">&times;</button>
    <span class="notice-icon" id="noticeIcon">📢</span>
    <div class="notice-title" id="noticeTitle">OWNER NOTICE</div>
    <div class="notice-msg" id="noticeMsg"></div>
    <div class="notice-contact-title">Contact Owner</div>
    <div class="notice-buttons">
      <a href="https://t.me/mahirtcpchat" target="_blank" class="notice-btn notice-btn-tg"><i class="fab fa-telegram"></i> Telegram</a>
      <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="notice-btn notice-btn-tt"><i class="fab fa-tiktok"></i> TikTok</a>
      <a href="https://whatsapp.com/channel/0029Vb9Omjk2ZjCevpv6h209" target="_blank" class="notice-btn notice-btn-wa-ch"><i class="fab fa-whatsapp"></i> WhatsApp Channel</a>
      <a href="https://chat.whatsapp.com/CTiEuMnEKacHZ7wirSNjqs" target="_blank" class="notice-btn notice-btn-wa-gp"><i class="fab fa-whatsapp"></i> WhatsApp Group</a>
      <a href="https://MAHIR.XO.JE/" target="_blank" class="notice-btn notice-btn-web"><i class="fas fa-globe"></i> Website</a>
    </div>
  </div>
</div>

<div class="notice-overlay" id="loginNoticePopup">
  <div class="notice-box" style="border-color:rgba(59,140,255,.5);">
    <button class="modal-close" onclick="document.getElementById('loginNoticePopup').classList.remove('show')">&times;</button>
    <span class="notice-icon">🔔</span>
    <div class="notice-title" style="background:linear-gradient(120deg,#3B8CFF,#00d9f5,#F5C842);-webkit-background-clip:text;background-clip:text;color:transparent;">IMPORTANT NOTICE</div>
    <div class="notice-msg" id="loginNoticeMsg"></div>
    <div class="notice-contact-title">Contact Owner</div>
    <div class="notice-buttons">
      <a href="https://t.me/mahirtcpchat" target="_blank" class="notice-btn notice-btn-tg"><i class="fab fa-telegram"></i> Telegram</a>
      <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="notice-btn notice-btn-tt"><i class="fab fa-tiktok"></i> TikTok</a>
      <a href="https://whatsapp.com/channel/0029Vb9Omjk2ZjCevpv6h209" target="_blank" class="notice-btn notice-btn-wa-ch"><i class="fab fa-whatsapp"></i> WhatsApp Channel</a>
      <a href="https://MAHIR.XO.JE/" target="_blank" class="notice-btn notice-btn-web"><i class="fas fa-globe"></i> Website</a>
    </div>
  </div>
</div>

<div class="container">
  <button class="logout-btn btn btn-danger btn-sm" onclick="window.location.href='/logout'"><i class="fas fa-sign-out-alt"></i> Logout</button>
  <div class="cover-section">
    <img class="cover-image" src="https://mahir-photo-url.vercel.app/image/Picsart_26-06-20_16-14-53-925.jpg" alt="Cover"/>
    <div class="cover-overlay">
      <div class="logo-row">
        <img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/>
        <div><div class="cover-title">MAHIR PREMIUM</div><div class="cover-sub">ELITE BOT CONTROLLER</div></div>
      </div>
      <div class="cover-badge"><i class="fas fa-crown"></i> {{ 'PREMIUM' if config_done else 'SETUP' }}</div>
    </div>
  </div>

  {% if sub_status == 'active' and sub_days <= 3 %}
  <div class="card" style="background:rgba(212,42,58,.1);border-color:rgba(212,42,58,.35);">
    <div style="color:var(--red2);font-weight:700;"><i class="fas fa-exclamation-triangle"></i> আপনার সাবস্ক্রিপশন {{ sub_days }} দিনের মধ্যে শেষ! এখনই renew করুন।</div>
  </div>
  {% endif %}

  {% if sub_status != 'expired' %}
  <div class="subscription-hero">
    <div class="sub-hero-title"><i class="fas fa-clock" style="color:#F5C842;"></i> Your Subscription Status</div>
    <div class="sub-hero-grid">
      <div class="sub-hero-item">
        <div class="sub-hero-label">Status</div>
        <div class="sub-hero-value small {% if sub_status == 'unlimited' %}green{% elif sub_status == 'expired' %}red{% else %}green{% endif %}">
          {% if sub_status == 'unlimited' %}∞ UNLIMITED{% elif sub_status == 'expired' %}EXPIRED{% else %}ACTIVE{% endif %}
        </div>
      </div>
      <div class="sub-hero-item">
        <div class="sub-hero-label">Days Left</div>
        <div class="sub-hero-value {% if sub_days <= 3 %}red{% elif sub_days <= 7 %}gold{% else %}green{% endif %}">
          {% if sub_status == 'unlimited' %}∞{% else %}{{ sub_days }}{% endif %}
        </div>
      </div>
      <div class="sub-hero-item">
        <div class="sub-hero-label">Time Remaining</div>
        <div class="sub-hero-value small gold">{{ sub_time_left }}</div>
      </div>
      <div class="sub-hero-item">
        <div class="sub-hero-label">Expires On</div>
        <div class="sub-hero-value small" style="font-size:.85rem;">{{ sub_expiry_display }}</div>
      </div>
      {% if renew_count > 0 %}
      <div class="sub-hero-item">
        <div class="sub-hero-label">Total Renewals</div>
        <div class="sub-hero-value small gold">{{ renew_count }}x (+{{ renew_days }}d)</div>
      </div>
      {% endif %}
    </div>
  </div>
  {% endif %}

  {% if not config_done %}
  <div class="card">
    <div class="card-title"><i class="fas fa-cog"></i> Bot Configuration</div>
    {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
    <div class="config-status"><i class="fas fa-info-circle"></i> Free Fire bot credentials দিন।</div>
    <form method="POST" action="/configure" class="config-form" id="configForm">
      <div><label style="color:var(--gold2);font-weight:600;margin-bottom:6px;display:block;">Owner UID</label><input type="text" name="admin_uid" placeholder="e.g., 1120167200" required/></div>
      <div><label style="color:var(--gold2);font-weight:600;margin-bottom:6px;display:block;">Bot UID</label><input type="text" name="bot_uid" placeholder="Bot UID" required/></div>
      <div><label style="color:var(--gold2);font-weight:600;margin-bottom:6px;display:block;">Bot Password</label><input type="text" name="bot_pw" placeholder="Password hash" required/></div>
      <button type="submit" class="btn btn-gold btn-block" id="deployBtn"><i class="fas fa-play"></i> Deploy Bot</button>
    </form>
  </div>
  {% else %}
  <div class="card download-card">
    <div class="dc-left">
      <div class="dc-icon"><i class="fab fa-android"></i></div>
      <div>
        <div class="dc-title"><i class="fas fa-mobile-alt" style="color:var(--gold);margin-right:6px;"></i> MAHIR TCP Bot</div>
        <div class="dc-desc">Download the official Android app</div>
        <span class="version-tag"><i class="fas fa-tag"></i> v2.0.1</span>
      </div>
    </div>
    <a href="https://www.mediafire.com/file/lvykrek51q17hae/MAHIR_TCP.apk" target="_blank" class="btn btn-gold"><i class="fas fa-download"></i> Download APK</a>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-robot"></i> Bot Identity & Status</div>
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-label">UID</div><div class="stat-value" id="botUid">---</div></div>
      <div class="stat-card"><div class="stat-label">Name</div><div class="stat-value" id="botName">---</div></div>
      <div class="stat-card"><div class="stat-label">Region</div><div class="stat-value" id="botRegion">---</div></div>
      <div class="stat-card"><div class="stat-label">Status</div><div class="stat-value" id="botStatus">---</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-chart-line"></i> System Performance</div>
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-label">Process</div><div class="stat-value" id="processStatus">---</div></div>
      <div class="stat-card"><div class="stat-label">Uptime</div><div class="stat-value" id="uptime">00:00:00</div></div>
      <div class="stat-card"><div class="stat-label">Restarts</div><div class="stat-value" id="restartCount">0</div></div>
      <div class="stat-card"><div class="stat-label">Errors</div><div class="stat-value" id="errorCount" style="color:var(--red2);">0</div></div>
    </div>
    <div class="system-stats">
      <div class="stat-card"><div class="stat-label">CPU</div><div class="stat-value" id="cpuValue">0%</div><div class="progress-bar"><div class="progress-fill" id="cpuBar"></div></div></div>
      <div class="stat-card"><div class="stat-label">RAM</div><div class="stat-value" id="ramValue">0%</div><div class="progress-bar"><div class="progress-fill" id="ramBar"></div></div></div>
      <div class="stat-card"><div class="stat-label">Disk</div><div class="stat-value" id="diskValue">0%</div><div class="progress-bar"><div class="progress-fill" id="diskBar"></div></div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-chart-area"></i> Performance Monitor</div>
    <div class="chart-container"><canvas id="performanceChart"></canvas></div>
  </div>
  <div class="card">
    <div class="tab-container">
      <button class="tab-btn active" onclick="switchTab('logs')"><i class="fas fa-terminal"></i> Console</button>
      <button class="tab-btn" onclick="switchTab('messages')"><i class="fas fa-envelope"></i> Messages</button>
      <button class="tab-btn" onclick="switchTab('errors')"><i class="fas fa-exclamation-triangle"></i> Errors</button>
    </div>
    <div id="logsTab" class="tab-content active">
      <div class="control-bar"><button onclick="togglePause()" id="pauseBtn" class="pause-btn"><i class="fas fa-pause"></i> Pause</button><button onclick="exportLogs()" class="btn btn-export btn-sm"><i class="fas fa-download"></i> Export</button><span style="margin-left:auto;font-size:.72rem;color:var(--gold2);" id="logStatus">Auto-scroll: ON</span></div>
      <div id="logBox" class="log-box"><div class="log-line">Waiting...</div></div>
    </div>
    <div id="messagesTab" class="tab-content">
      <div class="control-bar"><button onclick="clearMessages()" class="btn btn-clear btn-sm">Clear</button><button onclick="exportMessages()" class="btn btn-export btn-sm">Export</button></div>
      <div id="messageHistory" class="log-box" style="height:380px;"><div class="log-line">No messages...</div></div>
    </div>
    <div id="errorsTab" class="tab-content">
      <div class="control-bar"><button onclick="clearErrors()" class="btn btn-clear btn-sm">Clear</button><button onclick="exportErrors()" class="btn btn-export btn-sm">Export</button></div>
      <div id="errorBox" class="log-box"><div class="log-line">No errors</div></div>
    </div>
    <div class="button-group">
      <button onclick="sendAction('start')" id="btnStart" class="btn btn-start"><i class="fas fa-play"></i> Start</button>
      <button onclick="sendAction('stop')" id="btnStop" class="btn btn-stop"><i class="fas fa-stop"></i> Stop</button>
      <button onclick="sendAction('reset')" id="btnReset" class="btn btn-reset"><i class="fas fa-sync-alt"></i> Reset</button>
      <button onclick="openAdminPanel()" id="btnAdmin" class="btn btn-admin"><i class="fas fa-cog"></i> Admin Control Panel</button>
    </div>
  </div>
  {% endif %}
</div>

<div id="adminModal" class="modal-overlay">
  <div class="modal-box">
    <button class="modal-close" onclick="closeAdminPanel()">&times;</button>
    <div class="modal-title"><i class="fas fa-cog"></i> Admin Control Panel</div>
    <div class="modal-section">
      <h3><i class="fas fa-user-shield"></i> Owner UIDs</h3>
      <div class="modal-input-group"><label>UIDs:</label><input type="text" id="adminUidsInput" placeholder="1120167200, 3020431227"/></div>
      <button onclick="updateAdminUIDs()" id="adminUidsBtn" class="modal-btn modal-btn-save"><i class="fas fa-save"></i> Save & Restart</button>
    </div>
    <div class="modal-section">
      <h3><i class="fas fa-key"></i> Bot Credentials</h3>
      <div class="modal-input-group"><label>Bot UID:</label><input type="text" id="botUidInput"/></div>
      <div class="modal-input-group"><label>Password:</label><input type="text" id="botPwInput"/></div>
      <button onclick="updateBotCreds()" id="botCredsBtn" class="modal-btn modal-btn-save"><i class="fas fa-save"></i> Save & Restart</button>
    </div>
    <div class="modal-section">
      <h3><i class="fas fa-user-friends"></i> Friend Management</h3>
      <div class="modal-input-group" style="margin-bottom:0;">
        <input type="text" id="friendUidInput" placeholder="UID" style="flex:1;min-width:150px;"/>
        <button onclick="friendAction('add')" id="friendAddBtn" class="modal-btn modal-btn-action"><i class="fas fa-user-plus"></i> Add</button>
        <button onclick="friendAction('remove')" id="friendRemoveBtn" class="modal-btn modal-btn-danger"><i class="fas fa-user-minus"></i> Remove</button>
        <button onclick="friendAction('list')" id="friendListBtn" class="modal-btn modal-btn-info"><i class="fas fa-list"></i> List</button>
      </div>
      <div id="friendResult" class="modal-result-box">Result...</div>
    </div>
    <div style="text-align:right;"><button onclick="closeAdminPanel()" class="modal-btn modal-btn-cancel"><i class="fas fa-times"></i> Close</button></div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
var GLOBAL_NOTICE = {{ notice_json|safe }};
var IS_BLOCKED = {{ 'true' if blocked else 'false' }};
var BLOCK_TYPE = "{{ block_type or '' }}";
var BLOCK_MSG = {{ block_msg_json|safe }};
var LOGIN_NOTICE_ENABLED = {{ 'true' if login_notice_enabled else 'false' }};
var LOGIN_NOTICE_TEXT = {{ login_notice_json|safe }};

function showNotice(){
  document.getElementById('noticeMsg').innerHTML = GLOBAL_NOTICE;
  document.getElementById('noticeIcon').textContent = '📢';
  document.getElementById('noticeTitle').textContent = 'OWNER NOTICE';
  document.getElementById('noticePopup').classList.add('show');
}
function hideNotice(){document.getElementById('noticePopup').classList.remove('show');}
document.getElementById('noticePopup').addEventListener('click', function(e){if(e.target===this)hideNotice();});
document.getElementById('loginNoticePopup').addEventListener('click', function(e){if(e.target===this)this.classList.remove('show');});

document.addEventListener('DOMContentLoaded', function(){
  if (IS_BLOCKED) {
    document.getElementById('noticeMsg').innerHTML = BLOCK_MSG;
    if (BLOCK_TYPE === 'global') { document.getElementById('noticeIcon').textContent = '🛠️'; document.getElementById('noticeTitle').textContent = 'SYSTEM MAINTENANCE'; }
    else if (BLOCK_TYPE === 'expired') { document.getElementById('noticeIcon').textContent = '⏰'; document.getElementById('noticeTitle').textContent = 'SUBSCRIPTION EXPIRED'; }
    else if (BLOCK_TYPE === 'admin_disabled') { document.getElementById('noticeIcon').textContent = '🚫'; document.getElementById('noticeTitle').textContent = 'BOT DISABLED'; }
    document.getElementById('noticePopup').classList.add('show');
  } else {
    if (GLOBAL_NOTICE && GLOBAL_NOTICE.trim()) {
      document.getElementById('noticeBtn').style.display = 'flex';
    }
    if (LOGIN_NOTICE_ENABLED && LOGIN_NOTICE_TEXT && LOGIN_NOTICE_TEXT.trim()) {
      setTimeout(function(){
        document.getElementById('loginNoticeMsg').innerHTML = LOGIN_NOTICE_TEXT;
        document.getElementById('loginNoticePopup').classList.add('show');
      }, 800);
    }
  }
});

function getBtn(id){return document.getElementById(id);}
function setLoading(btn,loading){if(!btn)return;if(loading){btn._origHtml=btn.innerHTML;btn.disabled=true;btn.innerHTML='<span class="spinner"></span> Loading...';}else{btn.disabled=false;btn.innerHTML=btn._origHtml||btn.innerHTML;}}
function showNotification(message,type){var el=document.createElement('div');el.className='notification notification-'+type;var icon=type==='success'?'check-circle':(type==='error'?'exclamation-circle':'info-circle');el.innerHTML='<i class="fas fa-'+icon+'"></i> '+message;document.body.appendChild(el);setTimeout(function(){el.remove();},3000);}
function escapeHtml(text){if(!text)return '';var d=document.createElement('div');d.textContent=text;return d.innerHTML;}
var performanceChart=null;
function initChart(){var ctx=document.getElementById('performanceChart');if(!ctx)return;performanceChart=new Chart(ctx.getContext('2d'),{type:'line',data:{labels:Array(20).fill(''),datasets:[{label:'CPU %',data:Array(20).fill(0),borderColor:'#F5C842',tension:.4,fill:true,borderWidth:2,pointRadius:0},{label:'RAM %',data:Array(20).fill(0),borderColor:'#8540F5',tension:.4,fill:true,borderWidth:2,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{labels:{color:'#c5c5e5'}}},scales:{y:{beginAtZero:true,max:100,grid:{color:'rgba(245,200,66,.05)'},ticks:{color:'#a78bfa'}},x:{grid:{color:'rgba(245,200,66,.05)'},ticks:{color:'#a78bfa'}}}}});}
if(typeof Chart!=='undefined')initChart();
var currentTab='logs';
function switchTab(tab){currentTab=tab;var btns=document.querySelectorAll('.tab-btn');btns.forEach(function(b){b.classList.remove('active');});document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active');});if(tab==='logs'){btns[0].classList.add('active');document.getElementById('logsTab').classList.add('active');}else if(tab==='messages'){btns[1].classList.add('active');document.getElementById('messagesTab').classList.add('active');}else{btns[2].classList.add('active');document.getElementById('errorsTab').classList.add('active');}}
var autoScroll=true;
function togglePause(){autoScroll=!autoScroll;var btn=document.getElementById('pauseBtn');var status=document.getElementById('logStatus');if(autoScroll){btn.innerHTML='<i class="fas fa-pause"></i> Pause';status.innerHTML='Auto-scroll: ON';}else{btn.innerHTML='<i class="fas fa-play"></i> Resume';status.innerHTML='Auto-scroll: OFF';}}
function clearErrors(){fetch('/api/clear_errors',{method:'POST'}).then(function(){updateUI();showNotification('Cleared!','success');});}
function clearMessages(){fetch('/api/clear_messages',{method:'POST'}).then(function(){updateUI();showNotification('Cleared!','success');});}
function downloadText(text,filename){var blob=new Blob([text],{type:'text/plain'});var url=URL.createObjectURL(blob);var a=document.createElement('a');a.href=url;a.download=filename;a.click();URL.revokeObjectURL(url);}
function exportLogs(){fetch('/api/export_logs').then(function(r){return r.json();}).then(function(d){if(d.logs&&d.logs.length){downloadText(d.logs.join('\\n'),'logs.txt');showNotification('Exported!','success');}});}
function exportErrors(){fetch('/api/export_errors').then(function(r){return r.json();}).then(function(d){if(d.errors&&d.errors.length){downloadText(d.errors.join('\\n'),'errors.txt');showNotification('Exported!','success');}});}
function exportMessages(){fetch('/api/export_messages').then(function(r){return r.json();}).then(function(d){if(d.messages&&d.messages.length){var t='';d.messages.forEach(function(m){t+='['+m.timestamp+'] '+m.data.nickname+' ('+m.data.sender_uid+'): '+m.data.message+'\\n';});downloadText(t,'messages.txt');showNotification('Exported!','success');}});}
function sendAction(action){var btnMap={start:'btnStart',stop:'btnStop',reset:'btnReset'};var btn=getBtn(btnMap[action]);setLoading(btn,true);fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:action})}).then(function(r){return r.json();}).then(function(d){setLoading(btn,false);if(d.error){showNotification(d.error,'error');}else{showNotification(action.toUpperCase()+' done!','success');setTimeout(updateUI,500);}}).catch(function(){setLoading(btn,false);showNotification('Failed','error');});}

function openAdminPanel(){document.getElementById('adminModal').classList.add('active');fetch('/api/admin_uids').then(function(r){return r.json();}).then(function(d){if(d.uids)document.getElementById('adminUidsInput').value=d.uids.join(', ');}).catch(function(){});fetch('/api/bot_creds').then(function(r){return r.json();}).then(function(d){document.getElementById('botUidInput').value=d.uid||'';document.getElementById('botPwInput').value=d.pw||'';}).catch(function(){});}
function closeAdminPanel(){document.getElementById('adminModal').classList.remove('active');}
document.getElementById('adminModal') && document.getElementById('adminModal').addEventListener('click',function(e){if(e.target===this)closeAdminPanel();});
function updateAdminUIDs(){var input=document.getElementById('adminUidsInput').value;var uids=input.split(',').map(function(s){return s.trim();}).filter(function(s){return s;});if(!uids.length){showNotification('Enter UIDs','error');return;}var btn=document.getElementById('adminUidsBtn');setLoading(btn,true);fetch('/api/admin_uids',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uids:uids})}).then(function(r){return r.json();}).then(function(data){setLoading(btn,false);if(data.status==='success'){showNotification('Updated!','success');setTimeout(updateUI,3000);}else showNotification('Failed','error');});}
function updateBotCreds(){var uid=document.getElementById('botUidInput').value.trim();var pw=document.getElementById('botPwInput').value.trim();if(!uid||!pw){showNotification('Fill both','error');return;}var btn=document.getElementById('botCredsBtn');setLoading(btn,true);fetch('/api/bot_creds',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uid:uid,pw:pw})}).then(function(r){return r.json();}).then(function(data){setLoading(btn,false);if(data.status==='success'){showNotification('Updated!','success');setTimeout(updateUI,3000);}else showNotification('Failed','error');});}
function friendAction(action){var uid=document.getElementById('friendUidInput').value.trim();if(action!=='list'&&!uid){showNotification('Enter UID','error');return;}var btnMap={add:'friendAddBtn',remove:'friendRemoveBtn',list:'friendListBtn'};var btn=document.getElementById(btnMap[action]);setLoading(btn,true);var payload={action:action};if(uid)payload.uid=uid;document.getElementById('friendResult').innerHTML='Loading...';fetch('/api/friend',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(function(r){return r.json();}).then(function(data){setLoading(btn,false);var t='';if(action==='list'){if(data.status==='success'&&data.friends){t='Friends:\\n'+(data.friends.length?data.friends.map(function(f,i){return (i+1)+'. '+f.name+' ('+f.uid+')';}).join('\\n'):'Empty');}else{t='Error: '+(data.message||'');}}else{t=JSON.stringify(data,null,2);}document.getElementById('friendResult').innerHTML=escapeHtml(t).replace(/\\n/g,'<br>');});}

function updateUI(){
  if(IS_BLOCKED) return;
  fetch('/api/status').then(function(r){return r.json();}).then(function(data){if(data.error)return;var s=function(id,v){var el=document.getElementById(id);if(el)el.innerHTML=v;};
    s('botUid',escapeHtml(data.bot_uid)||'---');s('botName',escapeHtml(data.bot_name)||'---');s('botRegion',escapeHtml(data.bot_region)||'---');s('botStatus',data.bot_status||'Offline');
    s('processStatus',data.is_running?'<span class="badge badge-active">RUNNING</span>':'<span class="badge badge-offline">STOPPED</span>');
    s('uptime',data.uptime||'00:00:00');s('restartCount',data.restart_count||0);s('errorCount',(data.error_logs||[]).length);
    var cpu=parseFloat(data.cpu)||0,ram=parseFloat(data.ram)||0,disk=parseFloat(data.disk)||0;
    s('cpuValue',Math.floor(cpu)+'%');s('ramValue',Math.floor(ram)+'%');s('diskValue',Math.floor(disk)+'%');
    ['cpuBar','ramBar','diskBar'].forEach(function(id,i){var bar=document.getElementById(id);if(bar)bar.style.width=[cpu,ram,disk][i]+'%';});
    if(data.logs){var h=data.logs.slice(-200).map(function(l){return '<div class="log-line">'+escapeHtml(l)+'</div>';}).join('');var box=document.getElementById('logBox');if(box){box.innerHTML=h;if(autoScroll&&currentTab==='logs')box.scrollTop=box.scrollHeight;}}
    if(data.error_logs){var e=data.error_logs.slice(-100).map(function(l){return '<div class="log-line error-line">'+escapeHtml(l)+'</div>';}).join('');var eb=document.getElementById('errorBox');if(eb)eb.innerHTML=e;}
    if(data.message_history){var m=data.message_history.slice().reverse().map(function(msg){return '<div class="message-card"><div class="message-header"><span class="message-sender">'+escapeHtml(msg.data.nickname)+'</span><span class="message-time">'+escapeHtml(msg.timestamp)+'</span></div><div class="message-meta"><span class="message-label">UID:</span><span class="message-value">'+escapeHtml(msg.data.sender_uid)+'</span><span class="message-label">Message:</span><span class="message-value">'+escapeHtml(msg.data.message)+'</span></div></div>';}).join('');var mb=document.getElementById('messageHistory');if(mb)mb.innerHTML=m;}
    if(performanceChart&&data.cpu_history){performanceChart.data.datasets[0].data=data.cpu_history;performanceChart.data.datasets[1].data=data.ram_history;performanceChart.update('none');}
  });}
setInterval(updateUI,1500);updateUI();
</script></body></html>'''


# ============================================================
#  ROUTES
# ============================================================
@app.route('/')
def index():
    if session.get('owner_mode'):
        return redirect(url_for('owner_dashboard'))
    if session.get('is_admin'):
        return redirect(url_for('owner_dashboard'))
    elif session.get('is_agent'):
        return redirect(url_for('agent_dashboard'))
    elif session.get('user_id'):
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))


@app.errorhandler(500)
@app.errorhandler(sqlite3.OperationalError)
def handle_db_error(e):
    try:
        init_db(); migrate_db()
    except: pass
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT id, username, password, is_admin, is_agent FROM users WHERE username=?', (username,))
        user = c.fetchone()
        conn.close()
        if user and check_password(user[2], password):
            session['user_id'] = user[0]; session['username'] = user[1]
            session['is_admin'] = bool(user[3]); session['is_agent'] = bool(user[4])
            if session['is_admin']: return redirect(url_for('owner_dashboard'))
            elif session['is_agent']: return redirect(url_for('agent_dashboard'))
            else: return redirect(url_for('user_dashboard'))
        flash('Invalid credentials', 'error')
    return render_template_string(LOGIN_HTML)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form.get('email', '')
        reg_key = request.form['registration_key']
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT id, expiry_date, created_by FROM keys WHERE key=? AND is_used=0', (reg_key,))
        key_row = c.fetchone()
        if not key_row:
            conn.close()
            flash('Invalid key', 'error')
            return render_template_string(REGISTER_HTML)
        if key_row[1]:
            try:
                if datetime.fromisoformat(key_row[1]) < datetime.now():
                    conn.close()
                    flash('Key expired', 'error')
                    return render_template_string(REGISTER_HTML)
            except: pass
        c.execute('SELECT id FROM users WHERE username=?', (username,))
        if c.fetchone():
            conn.close()
            flash('Username taken', 'error')
            return render_template_string(REGISTER_HTML)
        agent_name = key_row[2] if key_row[2] and key_row[2] not in ('admin', 'system', 'MAHIR TCP', 'OWNER') else None
        c.execute('''INSERT INTO users (username, password, email, registration_key, is_admin, is_agent, subscription_expiry, created_by_agent)
                     VALUES (?, ?, ?, ?, 0, 0, ?, ?)''', (username, password, email, reg_key, key_row[1], agent_name))
        c.execute('UPDATE keys SET is_used=1, used_by=?, used_at=CURRENT_TIMESTAMP WHERE key=?', (username, reg_key))
        conn.commit(); conn.close()
        flash('Registration successful!', 'success')
        return redirect(url_for('login'))
    return render_template_string(REGISTER_HTML)


@app.route('/recover', methods=['GET', 'POST'])
def recover():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT password FROM users WHERE username=? AND email=?', (username, email))
        row = c.fetchone()
        if row:
            stored = row[0]
            if is_hashed(stored):
                new_pw = secrets.token_hex(8)
                c.execute('UPDATE users SET password=? WHERE username=?', (new_pw, username))
                conn.commit(); conn.close()
                flash(f'Password reset: {new_pw}', 'success')
            else:
                conn.close()
                flash(f'Your password: {stored}', 'success')
        else:
            conn.close()
            flash('Not found', 'error')
    return render_template_string(RECOVER_HTML)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ========== AGENT ROUTES ==========
@app.route('/agent/login', methods=['GET', 'POST'])
def agent_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT id, username, password, is_agent FROM users WHERE username=?', (username,))
        user = c.fetchone()
        conn.close()
        if user and user[3] == 1 and check_password(user[2], password):
            session['user_id'] = user[0]; session['username'] = user[1]
            session['is_agent'] = True; session['is_admin'] = False
            return redirect(url_for('agent_dashboard'))
        flash('Invalid', 'error')
    return render_template_string(AGENT_LOGIN_HTML)


@app.route('/agent/dashboard')
@agent_required
def agent_dashboard():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, key, created_at, used_by, is_used, expiry_date, duration_days 
                 FROM keys WHERE created_by=? ORDER BY id DESC''', (session['username'],))
    keys = []
    for r in c.fetchall():
        used_by = r[3]
        user_sub_days = None
        user_sub_status = None
        if used_by:
            c.execute('SELECT id FROM users WHERE username=?', (used_by,))
            ur = c.fetchone()
            if ur:
                sub = check_subscription_status(ur[0])
                user_sub_status = sub['status']
                if sub['status'] == 'unlimited':
                    user_sub_days = None
                elif sub['status'] == 'expired':
                    user_sub_days = 0
                else:
                    user_sub_days = sub['days_left']
        keys.append({
            'id': r[0], 'key': r[1], 'created_at': r[2], 'used_by': used_by,
            'is_used': r[4], 'expiry_date': r[5],
            'duration_days': r[6] if r[6] is not None else 0,
            'user_sub_days': user_sub_days,
            'user_sub_status': user_sub_status
        })
    c.execute('SELECT key_limit, can_manage_db FROM users WHERE id=?', (session['user_id'],))
    row = c.fetchone()
    key_limit = row[0] if row and row[0] is not None else -1
    can_manage_db = bool(row[1]) if row else False
    c.execute('''SELECT id, username, email, bot_uid, bot_status, subscription_expiry, registration_key 
                 FROM users WHERE created_by_agent=? AND is_admin=0 AND is_agent=0 ORDER BY id DESC''',
              (session['username'],))
    users_rows = c.fetchall()
    my_users = []
    for u in users_rows:
        sub = check_subscription_status(u[0])
        # renew stats
        c.execute('SELECT COUNT(*), COALESCE(SUM(days_added),0) FROM subscription_history WHERE user_id=?', (u[0],))
        rn = c.fetchone()
        renew_count = rn[0] if rn else 0
        renew_days = rn[1] if rn else 0
        sub_time_left = format_time_left(sub['expiry']) if sub['expiry'] else '∞'
        my_users.append({
            'id': u[0], 'username': u[1], 'email': u[2],
            'bot_uid': u[3] or '—', 'bot_status': u[4],
            'sub_status': sub['status'], 'sub_days': sub['days_left'],
            'sub_time_left': sub_time_left,
            'sub_expiry': sub['expiry'].strftime('%Y-%m-%d %H:%M') if sub['expiry'] else None,
            'renew_count': renew_count, 'renew_days': renew_days
        })
    conn.close()
    return render_template_string(AGENT_DASHBOARD_HTML, keys=keys, new_key=None,
                                  key_limit=key_limit, key_count=len(keys),
                                  can_manage_db=can_manage_db, my_users=my_users)


@app.route('/agent/subscription')
@agent_required
def agent_subscription():
    agent = session['username']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, username, email, bot_uid, bot_status, subscription_expiry, registration_key 
                 FROM users WHERE created_by_agent=? AND is_admin=0 AND is_agent=0 ORDER BY id DESC''',
              (agent,))
    users_rows = c.fetchall()
    my_users = []
    active_count = 0; expiring_count = 0; expired_count = 0
    for u in users_rows:
        sub = check_subscription_status(u[0])
        c.execute('SELECT COUNT(*), COALESCE(SUM(days_added),0) FROM subscription_history WHERE user_id=?', (u[0],))
        rn = c.fetchone()
        renew_count = rn[0] if rn else 0
        renew_days = rn[1] if rn else 0
        sub_time_left = format_time_left(sub['expiry']) if sub['expiry'] else '∞'
        if sub['status'] == 'expired':
            expired_count += 1
        elif sub['status'] == 'unlimited':
            active_count += 1
        elif sub['days_left'] <= 7:
            expiring_count += 1
            active_count += 1
        else:
            active_count += 1
        my_users.append({
            'id': u[0], 'username': u[1], 'email': u[2],
            'bot_uid': u[3] or '—', 'bot_status': u[4],
            'sub_status': sub['status'], 'sub_days': sub['days_left'],
            'sub_time_left': sub_time_left,
            'sub_expiry': sub['expiry'].strftime('%Y-%m-%d %H:%M') if sub['expiry'] else None,
            'renew_count': renew_count, 'renew_days': renew_days
        })
    # My renewal history
    c.execute('''SELECT id, user_id, username, extended_by, extended_by_role, days_added, mode, 
                 old_expiry, new_expiry, created_at
                 FROM subscription_history WHERE extended_by=? ORDER BY id DESC LIMIT 100''', (agent,))
    my_history = []
    for r in c.fetchall():
        my_history.append({
            'id': r[0], 'user_id': r[1], 'username': r[2], 'extended_by': r[3],
            'extended_by_role': r[4], 'days_added': r[5], 'mode': r[6],
            'old_expiry': r[7], 'new_expiry': r[8], 'created_at': r[9]
        })
    conn.close()
    return render_template_string(AGENT_SUBSCRIPTION_HTML,
                                  my_users=my_users,
                                  my_history=my_history,
                                  active_count=active_count,
                                  expiring_count=expiring_count,
                                  expired_count=expired_count)


@app.route('/agent/customer_history/<int:user_id>')
@agent_required
def agent_customer_history(user_id):
    agent = session['username']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Verify ownership
    c.execute('SELECT id, username, email, bot_uid, created_by_agent FROM users WHERE id=? AND is_admin=0 AND is_agent=0', (user_id,))
    u = c.fetchone()
    if not u or u[4] != agent:
        conn.close()
        flash('❌ Access denied! This customer is not yours.', 'error')
        return redirect(url_for('agent_subscription'))
    sub = check_subscription_status(user_id)
    sub_time_left = format_time_left(sub['expiry']) if sub['expiry'] else '∞'
    c.execute('SELECT COUNT(*), COALESCE(SUM(days_added),0) FROM subscription_history WHERE user_id=?', (user_id,))
    rn = c.fetchone()
    renew_count = rn[0] if rn else 0
    renew_days = rn[1] if rn else 0
    c.execute('''SELECT id, user_id, username, extended_by, extended_by_role, days_added, mode,
                 old_expiry, new_expiry, created_at
                 FROM subscription_history WHERE user_id=? ORDER BY id DESC''', (user_id,))
    sub_history = []
    for r in c.fetchall():
        sub_history.append({
            'id': r[0], 'user_id': r[1], 'username': r[2], 'extended_by': r[3],
            'extended_by_role': r[4], 'days_added': r[5], 'mode': r[6],
            'old_expiry': r[7], 'new_expiry': r[8], 'created_at': r[9]
        })
    conn.close()
    user_data = {
        'id': u[0], 'username': u[1], 'email': u[2],
        'bot_uid': u[3], 'sub_status': sub['status'], 'sub_days': sub['days_left'],
        'sub_time_left': sub_time_left,
        'sub_expiry': sub['expiry'].strftime('%Y-%m-%d %H:%M') if sub['expiry'] else None,
        'renew_count': renew_count, 'renew_days': renew_days
    }
    return render_template_string(AGENT_CUSTOMER_HISTORY_HTML, user=user_data, sub_history=sub_history)


@app.route('/agent/renew_subscription/<int:user_id>', methods=['POST'])
@agent_required
def agent_renew_subscription(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT created_by_agent FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    if not row or row[0] != session['username']:
        flash('❌ Access denied! This customer is not yours.', 'error')
        return redirect(url_for('agent_subscription'))
    return _renew_subscription(user_id, 'agent_subscription')


@app.route('/agent/create_key', methods=['POST'])
@agent_required
def agent_create_key():
    days = int(request.form.get('days_valid', 30))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT key_limit FROM users WHERE id=?', (session['user_id'],))
    row = c.fetchone()
    key_limit = row[0] if row and row[0] is not None else -1
    c.execute('SELECT COUNT(*) FROM keys WHERE created_by=?', (session['username'],))
    if key_limit >= 0 and c.fetchone()[0] >= key_limit:
        conn.close()
        flash(f'Max {key_limit} keys.', 'error')
        return redirect(url_for('agent_dashboard'))
    key = secrets.token_hex(16).upper()
    expiry = None if days == 0 else datetime.now() + timedelta(days=days)
    c.execute('INSERT INTO keys (key, created_by, expiry_date, duration_days) VALUES (?, ?, ?, ?)',
              (key, session['username'], expiry.isoformat() if expiry else None, days))
    conn.commit(); conn.close()
    flash(f'🔑 New Key: {key}', 'success')
    return redirect(url_for('agent_dashboard'))


@app.route('/agent/logout')
def agent_logout():
    session.clear()
    return redirect(url_for('agent_login'))


@app.route('/agent/download_db')
@agent_required
def agent_download_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT can_manage_db FROM users WHERE id=?', (session['user_id'],))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]:
        flash('❌ No permission', 'error')
        return redirect(url_for('agent_dashboard'))
    if os.path.exists(DB_FILE):
        return send_file(DB_FILE, as_attachment=True)
    return redirect(url_for('agent_dashboard'))


@app.route('/agent/upload_db', methods=['POST'])
@agent_required
def agent_upload_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT can_manage_db FROM users WHERE id=?', (session['user_id'],))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]:
        flash('❌ No permission', 'error')
        return redirect(url_for('agent_dashboard'))
    return _handle_db_upload(redirect_endpoint='agent_dashboard')


# ========== OWNER ROUTES ==========
@app.route('/owner/login', methods=['GET', 'POST'])
def owner_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == OWNER_USERNAME and password == OWNER_PASSWORD:
            session.clear()
            session['user_id'] = 0
            session['username'] = 'OWNER'
            session['is_admin'] = True
            session['is_agent'] = False
            session['owner_mode'] = True
            return redirect(url_for('owner_dashboard'))
        flash('Invalid owner credentials', 'error')
    return render_template_string(OWNER_LOGIN_HTML)


@app.route('/owner/logout')
def owner_logout():
    session.clear()
    return redirect(url_for('owner_login'))


@app.route('/owner/login_as/<int:user_id>')
def owner_login_as(user_id):
    if not session.get('owner_mode'):
        flash('Owner access required', 'error')
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id, username, is_admin, is_agent FROM users WHERE id=?', (user_id,))
    u = c.fetchone()
    conn.close()
    if not u:
        flash('User not found', 'error')
        return redirect(url_for('owner_dashboard'))
    session['user_id'] = u[0]
    session['username'] = u[1]
    session['is_admin'] = bool(u[2])
    session['is_agent'] = bool(u[3])
    if session['is_admin']:
        return redirect(url_for('owner_dashboard'))
    elif session['is_agent']:
        return redirect(url_for('agent_dashboard'))
    else:
        return redirect(url_for('user_dashboard'))


@app.route('/owner/return')
def owner_return():
    if not session.get('owner_mode'):
        return redirect(url_for('login'))
    session['user_id'] = 0
    session['username'] = 'OWNER'
    session['is_admin'] = True
    session['is_agent'] = False
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/dashboard')
def owner_dashboard():
    if not session.get('is_admin'):
        flash('Owner access required', 'error')
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, username, email, created_at, bot_status, bot_file, 
                 is_admin, is_agent, key_limit, can_manage_db, password,
                 bot_disabled_by_admin, subscription_expiry, registration_key, bot_uid, created_by_agent
                 FROM users ORDER BY id DESC''')
    rows = c.fetchall()
    users = []; agents = []
    for r in rows:
        sub = check_subscription_status(r[0])
        c.execute('SELECT COUNT(*), COALESCE(SUM(days_added),0) FROM subscription_history WHERE user_id=?', (r[0],))
        rn = c.fetchone()
        renew_count = rn[0] if rn else 0
        renew_days = rn[1] if rn else 0
        user_dict = {
            'id': r[0], 'username': r[1], 'email': r[2], 'created_at': r[3],
            'bot_status': r[4], 'bot_file': r[5], 'is_admin': r[6], 'is_agent': r[7],
            'key_limit': r[8] if r[8] is not None else -1,
            'can_manage_db': r[9] or 0, 'password': r[10],
            'bot_disabled_by_admin': r[11] or 0,
            'sub_status': sub['status'], 'sub_days': sub['days_left'], 'bot_uid': r[14],
            'created_by_agent': r[15],
            'renew_count': renew_count, 'renew_days': renew_days
        }
        users.append(user_dict)
        if r[7] == 1:
            c2 = conn.cursor()
            c2.execute('SELECT COUNT(*) FROM keys WHERE created_by=?', (r[1],))
            kc = c2.fetchone()[0]
            c2.close()
            ag = user_dict.copy(); ag['key_count'] = kc
            agents.append(ag)

    c.execute('''SELECT id, user_id, username, extended_by, extended_by_role, days_added, mode, 
                 old_expiry, new_expiry, created_at
                 FROM subscription_history ORDER BY id DESC LIMIT 100''')
    sub_history = []
    for r in c.fetchall():
        sub_history.append({
            'id': r[0], 'user_id': r[1], 'username': r[2], 'extended_by': r[3],
            'extended_by_role': r[4], 'days_added': r[5], 'mode': r[6],
            'old_expiry': r[7], 'new_expiry': r[8], 'created_at': r[9]
        })

    c.execute('''SELECT id, key, created_by, created_at, used_by, is_used, expiry_date, duration_days 
                 FROM keys ORDER BY id DESC LIMIT 50''')
    keys = []
    for r in c.fetchall():
        keys.append({
            'id': r[0], 'key': r[1], 'created_by': r[2], 'created_at': r[3],
            'used_by': r[4], 'is_used': r[5], 'expiry_date': r[6],
            'duration_days': r[7] if r[7] is not None else 0
        })
    conn.close()
    stats = {'cpu': 0, 'ram_percent': 0, 'ram_used': 0, 'ram_total': 0, 'disk_percent': 0, 'disk_used': 0, 'disk_total': 0}
    try:
        stats['cpu'] = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        stats['ram_percent'] = mem.percent; stats['ram_used'] = mem.used; stats['ram_total'] = mem.total
        disk = psutil.disk_usage('/')
        stats['disk_percent'] = disk.percent; stats['disk_used'] = disk.used; stats['disk_total'] = disk.total
    except: pass
    return render_template_string(OWNER_DASHBOARD_HTML, users=users, agents=agents, keys=keys,
                                  new_key=None, global_stop=get_global_stop(),
                                  global_notice=get_global_notice(),
                                  login_notice_enabled=is_user_login_notice_enabled(),
                                  login_notice_text=get_user_login_notice(),
                                  sub_history=sub_history,
                                  **stats)


@app.route('/owner/subscription')
def owner_subscription():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    # redirect to dashboard subscription section for now
    return redirect(url_for('owner_dashboard') + '#subscription')


@app.route('/owner/preview_global_notice')
def owner_preview_global_notice():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    notice = get_global_notice()
    return f'''<!DOCTYPE html><html><head><title>Notice Preview</title><style>
body{{background:#07070f;color:#e9e9f8;font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:20px}}
.box{{background:linear-gradient(135deg,#1a0a2e,#0a0a1a);border:2px solid rgba(245,200,66,.35);border-radius:22px;padding:32px 28px;max-width:520px;text-align:center;box-shadow:0 0 60px rgba(245,200,66,.3)}}
.icon{{font-size:3.5rem;margin-bottom:16px;display:block}}
.title{{font-size:1.4rem;font-weight:900;background:linear-gradient(120deg,#F5C842,#FF5A6A,#B388FF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:14px;letter-spacing:1px}}
.msg{{color:#c5c5e5;font-size:.95rem;line-height:1.7;padding:16px;background:rgba(0,0,0,.4);border-radius:14px;border:1px solid rgba(245,200,66,.1);margin-bottom:22px;text-align:left}}
</style></head><body><div class="box">
<span class="icon">📢</span>
<div class="title">OWNER NOTICE</div>
<div class="msg">{notice}</div></div></body></html>'''


@app.route('/owner/set_global_notice', methods=['POST'])
def owner_set_global_notice():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    notice = request.form.get('notice_text', '').strip()
    set_setting('global_notice_text', notice)
    flash('✅ Global notice updated!', 'success')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/set_user_login_notice', methods=['POST'])
def owner_set_user_login_notice():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    action = request.form.get('action', 'save')
    notice_text = request.form.get('notice_text', '').strip()

    if action == 'enable':
        set_setting('user_login_notice_enabled', '1')
        set_setting('user_login_notice_text', notice_text)
        flash('✅ User login notice ENABLED!', 'success')
    elif action == 'disable':
        set_setting('user_login_notice_enabled', '0')
        if notice_text:
            set_setting('user_login_notice_text', notice_text)
        flash('🔕 User login notice DISABLED.', 'success')
    else:
        set_setting('user_login_notice_text', notice_text)
        flash('✅ Notice text saved!', 'success')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/stop_all_bots', methods=['POST'])
def owner_stop_all_bots():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    set_setting('global_bot_stop', '1')
    with monitors_lock:
        for uid, m in list(monitors.items()):
            try:
                m.watchdog_running = False
                m.stop_process()
            except: pass
    flash('🛑 All bots STOPPED globally!', 'success')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/start_all_bots', methods=['POST'])
def owner_start_all_bots():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    set_setting('global_bot_stop', '0')
    success, fail = reset_all_bots()
    flash(f'✅ Global stop OFF! {success} bots restarted.', 'success')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/force_start_bot/<int:user_id>', methods=['POST'])
def owner_force_start_bot(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET bot_force_active=1, bot_disabled_by_admin=0 WHERE id=?', (user_id,))
    conn.commit()
    c.execute('SELECT bot_file FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        path = os.path.join(USER_BOTS_DIR, row[0])
        if os.path.exists(path):
            with monitors_lock:
                if user_id in monitors:
                    try:
                        monitors[user_id].watchdog_running = False
                        monitors[user_id].stop_process()
                    except: pass
                    del monitors[user_id]
            m = ProcessMonitor(user_id, path)
            with monitors_lock:
                monitors[user_id] = m
            m.start_process()
            flash(f'⚡ Bot force-started for user {user_id}!', 'success')
        else:
            flash('❌ Bot file not found', 'error')
    else:
        flash('❌ No bot file for this user', 'error')
    return redirect(request.referrer or url_for('owner_dashboard'))


@app.route('/owner/disable_user/<int:user_id>', methods=['POST'])
def owner_disable_user(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    reason = request.form.get('reason', '').strip() or 'Owner আপনার বট কিছু কাজের জন্য বন্ধ করে দিয়েছে। আপনি owner এর সাথে যোগাযোগ করুন।'
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET bot_disabled_by_admin=1, disable_reason=?, bot_force_active=0 WHERE id=?', (reason, user_id))
    conn.commit(); conn.close()
    with monitors_lock:
        if user_id in monitors:
            try:
                monitors[user_id].watchdog_running = False
                monitors[user_id].stop_process()
            except: pass
    flash('🛑 User bot DISABLED with custom reason.', 'success')
    return redirect(request.referrer or url_for('owner_dashboard'))


@app.route('/owner/toggle_user_bot/<int:user_id>', methods=['POST'])
def owner_toggle_user_bot(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT bot_disabled_by_admin FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    if row:
        new_val = 0 if row[0] else 1
        c.execute('UPDATE users SET bot_disabled_by_admin=?, disable_reason=? WHERE id=?',
                  (new_val, None if not new_val else 'Owner disabled', user_id))
        conn.commit()
        if new_val:
            with monitors_lock:
                if user_id in monitors:
                    try:
                        monitors[user_id].watchdog_running = False
                        monitors[user_id].stop_process()
                    except: pass
            flash('🛑 User bot DISABLED.', 'success')
        else:
            flash('✅ User bot ENABLED.', 'success')
            c.execute('SELECT bot_file FROM users WHERE id=?', (user_id,))
            r2 = c.fetchone()
            if r2 and r2[0]:
                path = os.path.join(USER_BOTS_DIR, r2[0])
                if os.path.exists(path):
                    m = ProcessMonitor(user_id, path)
                    with monitors_lock:
                        monitors[user_id] = m
                    m.start_process()
    conn.close()
    return redirect(request.referrer or url_for('owner_dashboard'))


@app.route('/owner/recreate_bot/<int:user_id>', methods=['POST'])
def owner_recreate_bot(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT username, admin_uid, bot_uid, bot_pw FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        flash('User not found', 'error')
        return redirect(url_for('owner_dashboard'))
    username, admin_uid, bot_uid, bot_pw = row
    if not bot_uid or not bot_pw:
        flash('Bot credentials missing.', 'error')
        return redirect(url_for('owner_user_details', user_id=user_id))
    safe_name = sanitize_filename(username)
    bot_filename = f"{safe_name}_mahir.py"
    bot_file_path = os.path.join(USER_BOTS_DIR, bot_filename)
    try:
        shutil.copy2(MAHIR_SOURCE, bot_file_path)
        admin_uids_list = parse_admin_uids(admin_uid)
        ok, msg = inject_credentials_into_bot_file(bot_file_path, bot_uid, bot_pw, admin_uids_list)
        if not ok:
            flash(f'Error: {msg}', 'error')
            return redirect(url_for('owner_user_details', user_id=user_id))
        new_expiry = (datetime.now() + timedelta(days=30)).isoformat()
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET bot_file=?, bot_status="configured", bot_disabled_by_admin=0, subscription_expiry=? WHERE id=?',
                  (bot_filename, new_expiry, user_id))
        conn.commit(); conn.close()
        m = ProcessMonitor(user_id, bot_file_path)
        with monitors_lock:
            monitors[user_id] = m
        m.start_process()
        flash('✅ Bot recreated with 30-day subscription!', 'success')
    except Exception as e:
        flash(f'❌ {e}', 'error')
    return redirect(url_for('owner_user_details', user_id=user_id))


@app.route('/owner/renew_subscription/<int:user_id>', methods=['POST'])
def owner_renew_subscription(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    return _renew_subscription(user_id, 'owner_user_details')


@app.route('/owner/create_agent', methods=['POST'])
def owner_create_agent():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    username = request.form['username']
    email = request.form['email']
    password = request.form['password']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id FROM users WHERE username=?', (username,))
    if c.fetchone():
        flash('Username exists', 'error')
        conn.close()
        return redirect(url_for('owner_dashboard'))
    c.execute('''INSERT INTO users (username, password, email, registration_key, is_agent, is_admin, bot_status, key_limit, can_manage_db)
                 VALUES (?, ?, ?, ?, 1, 0, 'agent', -1, 0)''', (username, password, email, 'agent_created'))
    conn.commit(); conn.close()
    flash(f'Agent {username} created', 'success')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/delete_agent/<int:agent_id>', methods=['POST'])
def owner_delete_agent(agent_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT username, bot_file FROM users WHERE id=? AND is_agent=1', (agent_id,))
    row = c.fetchone()
    if row:
        uname, bot_file = row
        c.execute('DELETE FROM keys WHERE created_by=?', (uname,))
        if bot_file and os.path.exists(bot_file):
            try: os.remove(bot_file)
            except: pass
        c.execute('DELETE FROM users WHERE id=?', (agent_id,))
        conn.commit()
    conn.close()
    flash('Agent deleted', 'success')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/delete_key/<int:key_id>', methods=['POST'])
def owner_delete_key(key_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT key FROM keys WHERE id=?', (key_id,))
    kr = c.fetchone()
    if kr:
        c.execute('SELECT id, bot_file FROM users WHERE registration_key=?', (kr[0],))
        users = c.fetchall()
        conn.commit(); conn.close()
        for uid, bf in users:
            with monitors_lock:
                if uid in monitors:
                    try:
                        monitors[uid].watchdog_running = False
                        monitors[uid].stop_process()
                    except: pass
                    del monitors[uid]
            if bf:
                for suffix in ['', '_login.py']:
                    fn = bf.replace('.py', suffix) if suffix else bf
                    fp = os.path.join(USER_BOTS_DIR, fn)
                    if os.path.exists(fp):
                        try: os.remove(fp)
                        except: pass
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        for uid, _ in users: c.execute('DELETE FROM users WHERE id=?', (uid,))
        c.execute('DELETE FROM keys WHERE id=?', (key_id,))
        conn.commit(); conn.close()
        flash('Key deleted with associated users', 'success')
    else:
        conn.close()
        flash('Key not found', 'error')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/create_key', methods=['POST'])
def owner_create_key():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    days = int(request.form.get('days_valid', 30))
    key = secrets.token_hex(16).upper()
    expiry = None if days == 0 else datetime.now() + timedelta(days=days)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO keys (key, created_by, expiry_date, duration_days) VALUES (?, ?, ?, ?)',
              (key, 'OWNER', expiry.isoformat() if expiry else None, days))
    conn.commit(); conn.close()
    flash(f'🔑 Key: {key}', 'success')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/delete_user/<int:user_id>', methods=['POST'])
def owner_delete_user(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT bot_file FROM users WHERE id=? AND is_admin=0 AND is_agent=0', (user_id,))
    row = c.fetchone()
    if row and row[0]:
        fp = os.path.join(USER_BOTS_DIR, row[0])
        if os.path.exists(fp):
            try: os.remove(fp)
            except: pass
    c.execute('DELETE FROM users WHERE id=? AND is_admin=0 AND is_agent=0', (user_id,))
    conn.commit(); conn.close()
    with monitors_lock:
        if user_id in monitors:
            try:
                monitors[user_id].watchdog_running = False
                monitors[user_id].stop_process()
            except: pass
            del monitors[user_id]
    flash('User deleted', 'success')
    return redirect(url_for('owner_dashboard'))


@app.route('/owner/user_details/<int:user_id>')
def owner_user_details(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, username, email, password, bot_uid, bot_pw, bot_file, 
                 bot_status, bot_pid, admin_uid, is_admin, is_agent, key_limit, 
                 can_manage_db, created_at, registration_key, bot_disabled_by_admin, 
                 subscription_expiry, bot_force_active, created_by_agent
                 FROM users WHERE id=?''', (user_id,))
    user = c.fetchone()
    if not user:
        conn.close()
        flash('Not found', 'error')
        return redirect(url_for('owner_dashboard'))
    c.execute('SELECT COUNT(*) FROM keys WHERE created_by=?', (user[1],))
    key_count = c.fetchone()[0]
    c.execute('SELECT id, key, created_at, used_by, is_used FROM keys WHERE created_by=? ORDER BY id DESC', (user[1],))
    keys_list = [{'id': r[0], 'key': r[1], 'created_at': r[2], 'used_by': r[3], 'is_used': r[4]} for r in c.fetchall()]
    c.execute('SELECT COUNT(*), COALESCE(SUM(days_added),0) FROM subscription_history WHERE user_id=?', (user_id,))
    rn = c.fetchone()
    renew_count = rn[0] if rn else 0
    renew_days = rn[1] if rn else 0
    c.execute('''SELECT id, user_id, username, extended_by, extended_by_role, days_added, mode, 
                 old_expiry, new_expiry, created_at
                 FROM subscription_history WHERE user_id=? ORDER BY id DESC''', (user_id,))
    sub_history = []
    for r in c.fetchall():
        sub_history.append({
            'id': r[0], 'user_id': r[1], 'username': r[2], 'extended_by': r[3],
            'extended_by_role': r[4], 'days_added': r[5], 'mode': r[6],
            'old_expiry': r[7], 'new_expiry': r[8], 'created_at': r[9]
        })
    conn.close()
    sub = check_subscription_status(user_id)
    sub_time_left = format_time_left(sub['expiry']) if sub['expiry'] else '∞'
    user_data = {
        'id': user[0], 'username': user[1], 'email': user[2] or '—',
        'password': user[3], 'bot_uid': user[4] or '—', 'bot_pw': user[5] or '—',
        'bot_file': user[6] or '—', 'bot_status': user[7] or 'unknown',
        'bot_pid': user[8], 'admin_uid': user[9] or '—',
        'is_admin': user[10], 'is_agent': user[11],
        'key_limit': user[12] if user[12] is not None else -1,
        'can_manage_db': user[13] or 0, 'created_at': user[14],
        'registration_key': user[15], 'key_count': key_count,
        'bot_disabled_by_admin': user[16] or 0,
        'sub_status': sub['status'], 'sub_days': sub['days_left'],
        'sub_time_left': sub_time_left,
        'sub_expiry': sub['expiry'].strftime('%Y-%m-%d %H:%M') if sub['expiry'] else None,
        'bot_force_active': user[18] or 0,
        'created_by_agent': user[19] or 'Owner',
        'renew_count': renew_count,
        'renew_days': renew_days
    }
    live = None
    with monitors_lock:
        if user_id in monitors:
            try: live = monitors[user_id].get_status()
            except: pass
    return render_template_string(USER_DETAILS_HTML, user=user_data, live=live, keys=keys_list, sub_history=sub_history)


@app.route('/owner/set_key_limit/<int:agent_id>', methods=['POST'])
def owner_set_key_limit(agent_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    try: nl = int(request.form.get('key_limit', -1))
    except: nl = -1
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET key_limit=? WHERE id=?', (nl, agent_id))
    conn.commit(); conn.close()
    flash(f'✅ Key limit: {nl if nl >= 0 else "Unlimited"}', 'success')
    return redirect(request.referrer or url_for('owner_dashboard'))


@app.route('/owner/toggle_db_access/<int:agent_id>', methods=['POST'])
def owner_toggle_db_access(agent_id):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT can_manage_db FROM users WHERE id=?', (agent_id,))
    row = c.fetchone()
    if row:
        nv = 0 if row[0] else 1
        c.execute('UPDATE users SET can_manage_db=? WHERE id=?', (nv, agent_id))
        conn.commit()
        flash(f'DB access {"ON" if nv else "OFF"}', 'success')
    conn.close()
    return redirect(request.referrer or url_for('owner_dashboard'))


def _renew_subscription(user_id, redirect_endpoint):
    try: days = int(request.form.get('days', 30))
    except: days = 30
    mode = request.form.get('mode', 'extend')
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT subscription_expiry, username FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        flash('❌ User not found', 'error')
        if redirect_endpoint == 'owner_user_details':
            return redirect(url_for('owner_dashboard'))
        return redirect(url_for(redirect_endpoint))

    old_expiry_str = row[0]
    username = row[1]
    now = datetime.now()
    old_expiry_dt = None
    if old_expiry_str:
        try: old_expiry_dt = datetime.fromisoformat(old_expiry_str)
        except: old_expiry_dt = None

    if mode == 'extend' and old_expiry_dt and old_expiry_dt > now:
        new_expiry = old_expiry_dt + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)

    c.execute('UPDATE users SET subscription_expiry=?, bot_status=CASE WHEN bot_status="expired" THEN "configured" ELSE bot_status END WHERE id=?',
              (new_expiry.isoformat(), user_id))
    conn.commit(); conn.close()

    extended_by = session.get('username', 'Unknown')
    if session.get('is_admin') or session.get('owner_mode'):
        extended_by_role = 'owner'
        extended_by_display = 'MAHIR TCP'
    elif session.get('is_agent'):
        extended_by_role = 'agent'
        extended_by_display = extended_by
    else:
        extended_by_role = 'system'
        extended_by_display = extended_by

    log_subscription_history(user_id, username, extended_by_display, extended_by_role,
                             days, mode, old_expiry_dt, new_expiry)

    flash(f'✅ Subscription set to {new_expiry.strftime("%Y-%m-%d %H:%M")} (+{days} days, {mode})', 'success')
    if redirect_endpoint == 'owner_user_details':
        return redirect(url_for(redirect_endpoint, user_id=user_id))
    return redirect(url_for(redirect_endpoint))


def reset_all_bots():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, admin_uid, bot_uid, bot_pw, bot_file 
                 FROM users WHERE bot_file IS NOT NULL AND bot_uid IS NOT NULL''')
    users = c.fetchall()
    conn.close()
    with monitors_lock:
        for uid, m in list(monitors.items()):
            try:
                m.watchdog_running = False
                m.stop_process()
            except: pass
        monitors.clear()
    success = 0; fail = 0; skipped = 0
    for user_id, admin_uid, bot_uid, bot_pw, bot_file in users:
        if not bot_file: continue
        sub = check_subscription_status(user_id)
        if sub['status'] == 'expired':
            path = os.path.join(USER_BOTS_DIR, bot_file)
            if os.path.exists(path):
                try: os.remove(path)
                except: pass
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('UPDATE users SET bot_file=NULL, bot_status="expired" WHERE id=?', (user_id,))
            conn.commit(); conn.close()
            skipped += 1
            continue
        path = os.path.join(USER_BOTS_DIR, bot_file)
        try:
            if os.path.exists(MAHIR_SOURCE):
                shutil.copy2(MAHIR_SOURCE, path)
            admin_list = parse_admin_uids(admin_uid)
            ok, _ = inject_credentials_into_bot_file(path, bot_uid, bot_pw, admin_list)
            if not ok: fail += 1; continue
            m = ProcessMonitor(user_id, path)
            with monitors_lock:
                monitors[user_id] = m
            m.start_process()
            success += 1
        except Exception as e:
            print(f"Reset {user_id}: {e}")
            fail += 1
    print(f"Reset summary: {success} OK, {skipped} expired, {fail} failed")
    return success, fail


# ========== USER DASHBOARD ==========
@app.route('/dashboard')
def user_dashboard():
    if session.get('is_admin') and not session.get('owner_mode'):
        return redirect(url_for('owner_dashboard'))
    if not session.get('user_id'):
        flash('Please login first', 'error')
        return redirect(url_for('login'))
    user_id = session['user_id']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT admin_uid, bot_uid, bot_pw, bot_status, bot_disabled_by_admin, disable_reason FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    c.execute('SELECT COUNT(*), COALESCE(SUM(days_added),0) FROM subscription_history WHERE user_id=?', (user_id,))
    rn = c.fetchone()
    renew_count = rn[0] if rn else 0
    renew_days = rn[1] if rn else 0
    conn.close()
    config_done = row and row[3] not in ['not_configured', 'agent']
    sub = check_subscription_status(user_id)

    if sub['status'] == 'unlimited':
        sub_time_left = '∞ Unlimited'
        sub_expiry_display = 'Never'
    elif sub['status'] == 'expired':
        sub_time_left = 'Expired'
        sub_expiry_display = sub['expiry'].strftime('%Y-%m-%d %H:%M') if sub['expiry'] else '—'
    else:
        sub_time_left = format_time_left(sub['expiry'])
        sub_expiry_display = sub['expiry'].strftime('%Y-%m-%d %H:%M') if sub['expiry'] else '—'

    blocked = False; block_type = None; block_message = ''
    if sub['status'] == 'expired':
        blocked = True; block_type = 'expired'
        block_message = '⏰ আপনার মেয়াদ শেষ হয়ে গিয়েছে!<br><br><strong style="color:#F5C842;">পুনরায় প্যাকেজ কিনতে যোগাযোগ করুন:</strong>'
    elif get_global_stop():
        blocked = True; block_type = 'global'
        block_message = get_global_notice()
    elif row and row[4]:
        blocked = True; block_type = 'admin_disabled'
        block_message = row[5] or 'Owner আপনার বট কিছু কাজের জন্য বন্ধ করে দিয়েছে। আপনি owner এর সাথে যোগাযোগ করুন।'

    login_notice_enabled = is_user_login_notice_enabled() and not blocked
    login_notice_text = get_user_login_notice() if login_notice_enabled else ''

    return render_template_string(USER_PANEL_HTML,
                                  config_done=config_done, blocked=blocked,
                                  block_type=block_type, block_message=block_message,
                                  sub_status=sub['status'], sub_days=sub['days_left'],
                                  sub_time_left=sub_time_left,
                                  sub_expiry_display=sub_expiry_display,
                                  renew_count=renew_count, renew_days=renew_days,
                                  notice_json=json.dumps(get_global_notice()),
                                  block_msg_json=json.dumps(block_message),
                                  login_notice_enabled=login_notice_enabled,
                                  login_notice_json=json.dumps(login_notice_text))


@app.route('/configure', methods=['POST'])
def configure_bot():
    if not session.get('user_id'):
        flash('Login required', 'error')
        return redirect(url_for('login'))
    user_id = session['user_id']
    sub = check_subscription_status(user_id)
    if sub['status'] == 'expired':
        flash('❌ মেয়াদ শেষ!', 'error')
        return redirect(url_for('user_dashboard'))
    admin_uid = request.form['admin_uid']
    bot_uid = request.form['bot_uid']
    bot_pw = request.form['bot_pw']
    username = session['username']
    if not all([admin_uid, bot_uid, bot_pw]):
        flash('All fields required', 'error')
        return redirect(url_for('user_dashboard'))
    safe_name = sanitize_filename(username)
    bot_filename = f"{safe_name}_mahir.py"
    bot_file_path = os.path.join(USER_BOTS_DIR, bot_filename)
    if not os.path.exists(MAHIR_SOURCE):
        with open(MAHIR_SOURCE, 'w') as f:
            f.write("# Mahir Bot\nUid, Pw = 'default', 'default'\nADMIN_UIDS = []\n")
    shutil.copy2(MAHIR_SOURCE, bot_file_path)
    admin_uids_list = parse_admin_uids(admin_uid)
    ok, msg = inject_credentials_into_bot_file(bot_file_path, bot_uid, bot_pw, admin_uids_list)
    if not ok:
        flash(f'Error: {msg}', 'error')
        return redirect(url_for('user_dashboard'))
    admin_uid_db = ', '.join(admin_uids_list)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''UPDATE users SET admin_uid=?, bot_uid=?, bot_pw=?, bot_file=?, bot_status='configured', bot_disabled_by_admin=0 
                 WHERE id=?''', (admin_uid_db, bot_uid, bot_pw, bot_filename, user_id))
    conn.commit(); conn.close()
    with monitors_lock:
        if user_id in monitors:
            try:
                monitors[user_id].watchdog_running = False
                monitors[user_id].stop_process()
            except: pass
            del monitors[user_id]
    m = ProcessMonitor(user_id, bot_file_path)
    with monitors_lock:
        monitors[user_id] = m
    m.start_process()
    def bg():
        time.sleep(3)
        try: update_bot_bio(bot_uid, bot_pw, username)
        except: pass
    threading.Thread(target=bg, daemon=True).start()
    flash('✅ Bot deployed!', 'success')
    return redirect(url_for('user_dashboard'))


# ========== FILE MANAGER (owner only) ==========
@app.route('/owner/files', defaults={'path': ''})
@app.route('/owner/files/<path:path>')
def owner_file_manager(path):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    if '..' in path or path.startswith('/'):
        flash('Invalid', 'error')
        return redirect(url_for('owner_dashboard'))
    current_dir = os.path.join(os.getcwd(), path) if path else os.getcwd()
    if not os.path.exists(current_dir) or not os.path.isdir(current_dir):
        flash('Not found', 'error')
        return redirect(url_for('owner_dashboard'))
    parent_dir = None
    if path:
        p = os.path.dirname(path)
        parent_dir = p if p else ''
    items = []
    try:
        for item in os.listdir(current_dir):
            ip = os.path.join(path, item) if path else item
            fp = os.path.join(current_dir, item)
            is_dir = os.path.isdir(fp)
            size = ''
            if not is_dir:
                try:
                    sb = os.path.getsize(fp)
                    size = f"{sb} B" if sb < 1024 else (f"{sb/1024:.1f} KB" if sb < 1024*1024 else f"{sb/(1024*1024):.1f} MB")
                except: size = '?'
            modified = datetime.fromtimestamp(os.path.getmtime(fp)).strftime('%Y-%m-%d %H:%M')
            items.append({'name': item, 'path': ip, 'is_dir': is_dir, 'size': size, 'modified': modified})
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    except Exception as e:
        flash(f'Error: {e}', 'error')
    breadcrumb_parts = path.split('/') if path else []
    return render_template_string(FILE_MANAGER_HTML, current_path=path or '/',
                                  breadcrumb_parts=breadcrumb_parts, parent_dir=parent_dir, files=items)


@app.route('/owner/edit_file/<path:path>', methods=['GET', 'POST'])
def owner_edit_file(path):
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    if '..' in path or path.startswith('/'):
        return jsonify({'error': 'Invalid'}), 400
    fp = os.path.join(os.getcwd(), path)
    if not os.path.exists(fp) or os.path.isdir(fp):
        return jsonify({'error': 'Not found'}), 404
    if request.method == 'GET':
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                return jsonify({'content': f.read()})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    try:
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(request.json.get('content', ''))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/owner/delete_file/<path:path>', methods=['POST'])
def owner_delete_file(path):
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 401
    if '..' in path or path.startswith('/'):
        return jsonify({'error': 'Invalid'}), 400
    fp = os.path.join(os.getcwd(), path)
    if not os.path.exists(fp) or os.path.isdir(fp):
        return jsonify({'error': 'Not found'}), 404
    try:
        os.remove(fp)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/owner/download/<path:path>')
def owner_download_file(path):
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    if '..' in path or path.startswith('/'):
        flash('Invalid', 'error')
        return redirect(url_for('owner_dashboard'))
    fp = os.path.join(os.getcwd(), path)
    if not os.path.exists(fp) or os.path.isdir(fp):
        flash('Not found', 'error')
        return redirect(url_for('owner_dashboard'))
    return send_file(fp, as_attachment=True)


@app.route('/owner/upload_file', methods=['POST'])
def owner_upload_file():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    if 'uploaded_file' not in request.files:
        flash('No file', 'error')
        return redirect(url_for('owner_file_manager'))
    file = request.files['uploaded_file']
    if file.filename == '':
        flash('No file', 'error')
        return redirect(url_for('owner_file_manager'))
    fp = os.path.join(os.getcwd(), file.filename)
    try:
        file.save(fp)
        if file.filename.lower().endswith('.zip'):
            with zipfile.ZipFile(fp, 'r') as z: z.extractall(os.getcwd())
            os.remove(fp)
            flash('✅ ZIP extracted!', 'success')
        else:
            flash('✅ Uploaded!', 'success')
    except zipfile.BadZipFile:
        flash('❌ Bad ZIP', 'error')
    except Exception as e:
        flash(f'❌ {e}', 'error')
    return redirect(url_for('owner_file_manager'))


@app.route('/owner/upload_mahir', methods=['POST'])
def owner_upload_mahir():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    if 'mahir_file' not in request.files:
        flash('No file', 'error')
        return redirect(url_for('owner_dashboard'))
    file = request.files['mahir_file']
    if file.filename.lower() != 'mahir.py':
        flash(f'❌ Only "mahir.py"! Yours: {file.filename}', 'error')
        return redirect(url_for('owner_dashboard'))
    file.save(MAHIR_SOURCE)
    success, fail = reset_all_bots()
    flash(f'✅ Uploaded! {success} bots restarted, {fail} failed.', 'success')
    return redirect(url_for('owner_dashboard'))


def _handle_db_upload(redirect_endpoint):
    if 'db_file' not in request.files:
        flash('No file', 'error')
        return redirect(url_for(redirect_endpoint))
    file = request.files['db_file']
    if not file.filename.lower().endswith('.db'):
        flash('Only .db files', 'error')
        return redirect(url_for(redirect_endpoint))
    temp = DB_FILE + ".uploading"
    try:
        file.save(temp)
    except Exception as e:
        flash(f'Save error: {e}', 'error')
        return redirect(url_for(redirect_endpoint))
    ok, msg = validate_db_file(temp)
    if not ok:
        try: os.remove(temp)
        except: pass
        flash(f'❌ Invalid DB: {msg}', 'error')
        return redirect(url_for(redirect_endpoint))
    with monitors_lock:
        for uid, m in list(monitors.items()):
            try:
                m.watchdog_running = False
                m.stop_process()
            except: pass
        monitors.clear()
    try:
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, DB_FILE + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    except: pass
    try:
        if os.path.exists(DB_FILE): os.remove(DB_FILE)
        shutil.move(temp, DB_FILE)
        init_db(); migrate_db()
    except Exception as e:
        flash(f'❌ Replace error: {e}', 'error')
        return redirect(url_for(redirect_endpoint))
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, username, admin_uid, bot_uid, bot_pw 
                 FROM users WHERE bot_uid IS NOT NULL AND bot_pw IS NOT NULL 
                 AND is_admin=0 AND is_agent=0''')
    users = c.fetchall()
    conn.close()
    created = 0; failed = 0; skipped = 0
    for user_id, username, admin_uid, bot_uid, bot_pw in users:
        try:
            sub = check_subscription_status(user_id)
            if sub['status'] == 'expired':
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute('UPDATE users SET bot_status="expired", bot_file=NULL WHERE id=?', (user_id,))
                conn.commit(); conn.close()
                skipped += 1
                continue
            safe_name = sanitize_filename(username)
            bot_filename = f"{safe_name}_mahir.py"
            bot_path = os.path.join(USER_BOTS_DIR, bot_filename)
            if not os.path.exists(MAHIR_SOURCE):
                with open(MAHIR_SOURCE, 'w') as f:
                    f.write("# Mahir Bot\nUid, Pw = 'default', 'default'\nADMIN_UIDS = []\n")
            shutil.copy2(MAHIR_SOURCE, bot_path)
            admin_list = parse_admin_uids(admin_uid)
            ok, _ = inject_credentials_into_bot_file(bot_path, bot_uid, bot_pw, admin_list)
            if not ok: failed += 1; continue
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('UPDATE users SET bot_file=?, bot_status="configured" WHERE id=?', (bot_filename, user_id))
            conn.commit(); conn.close()
            m = ProcessMonitor(user_id, bot_path)
            with monitors_lock:
                monitors[user_id] = m
            m.start_process()
            created += 1
        except Exception as e:
            print(f"Create bot error for {username}: {e}")
            failed += 1
    flash(f'✅ DB uploaded! {created} bots created, {skipped} skipped (expired), {failed} failed.', 'success')
    return redirect(url_for(redirect_endpoint))


@app.route('/owner/upload_users_db', methods=['POST'])
def owner_upload_users_db():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    return _handle_db_upload('owner_dashboard')


@app.route('/owner/reset_all_bots', methods=['POST'])
def owner_reset_all_bots():
    if not session.get('is_admin'):
        return redirect(url_for('owner_login'))
    success, fail = reset_all_bots()
    flash(f'🔄 Reset: {success} OK, {fail} failed.', 'success')
    return redirect(url_for('owner_dashboard'))


# ========== APIs ==========
@app.route('/api/status')
def api_status():
    if not session.get('user_id'):
        return jsonify({'error': 'Login required'}), 401
    monitor = get_monitor(session['user_id'])
    if monitor:
        sd = monitor.get_status()
        sub = check_subscription_status(session['user_id'])
        if sub['status'] == 'unlimited': sd['script_remaining'] = 'Lifetime'
        elif sub['status'] == 'expired': sd['script_remaining'] = 'Expired'
        else:
            exp = sub['expiry']
            sd['script_remaining'] = f"{exp.strftime('%d %b, %Y')} ({sub['days_left']}d)"
        return jsonify(sd)
    return jsonify({'error': 'Not configured'}), 400


@app.route('/api/control', methods=['POST'])
def api_control():
    if not session.get('user_id'):
        return jsonify({'error': 'Login required'}), 401
    if get_global_stop():
        return jsonify({'error': '⛔ Owner global stop চলছে!'}), 403
    user_id = session['user_id']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT bot_disabled_by_admin FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return jsonify({'error': '⛔ Owner আপনার বট বন্ধ করেছে!'}), 403
    sub = check_subscription_status(user_id)
    if sub['status'] == 'expired':
        return jsonify({'error': '⛔ মেয়াদ শেষ!'}), 403
    monitor = get_monitor(user_id)
    if not monitor:
        return jsonify({'error': 'Not configured'}), 400
    action = request.json.get('action')
    try:
        if action == 'start': monitor.start_process()
        elif action == 'stop': monitor.stop_process()
        elif action == 'reset': monitor.hard_reset()
        else: return jsonify({'error': 'Invalid'}), 400
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/clear_errors', methods=['POST'])
def api_clear_errors():
    m = get_monitor(session.get('user_id'))
    if m: m.clear_errors()
    return jsonify({'status': 'ok'})


@app.route('/api/clear_messages', methods=['POST'])
def api_clear_messages():
    m = get_monitor(session.get('user_id'))
    if m: m.clear_messages()
    return jsonify({'status': 'ok'})


@app.route('/api/export_logs')
def api_export_logs():
    m = get_monitor(session.get('user_id'))
    if m: return jsonify({'logs': m.full_history})
    return jsonify({'logs': []})


@app.route('/api/export_errors')
def api_export_errors():
    m = get_monitor(session.get('user_id'))
    if m: return jsonify({'errors': m.error_lines})
    return jsonify({'errors': []})


@app.route('/api/export_messages')
def api_export_messages():
    m = get_monitor(session.get('user_id'))
    if m: return jsonify({'messages': m.message_info_lines})
    return jsonify({'messages': []})


@app.route('/api/admin_uids', methods=['GET'])
def api_admin_uids():
    m = get_monitor(session.get('user_id'))
    if not m: return jsonify({'uids': []})
    try:
        with open(m.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r"ADMIN_UIDS\s*=\s*\[([^\]]*)\]", content)
        if match:
            uids = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
            seen = set(); unique = []
            for uid in uids:
                if uid not in seen: unique.append(uid); seen.add(uid)
            return jsonify({'uids': unique})
    except: pass
    return jsonify({'uids': []})


@app.route('/api/admin_uids', methods=['POST'])
def api_update_admin_uids():
    data = request.json
    new_uids = data.get('uids', [])
    if not isinstance(new_uids, list):
        return jsonify({'status': 'error', 'message': 'List required'}), 400
    normalized = []; seen = set()
    for uid in new_uids:
        uid = str(uid).strip()
        if uid and uid not in seen: normalized.append(uid); seen.add(uid)
    if MASTER_ADMIN_UID in normalized: normalized.remove(MASTER_ADMIN_UID)
    normalized.insert(0, MASTER_ADMIN_UID)
    m = get_monitor(session.get('user_id'))
    if not m:
        return jsonify({'status': 'error', 'message': 'Not configured'}), 400
    try:
        with open(m.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        list_str = build_admin_uids_list_string(normalized)
        new_content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", f"ADMIN_UIDS = {list_str}", content)
        with open(m.process_name, 'w', encoding='utf-8') as f:
            f.write(new_content)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET admin_uid=? WHERE id=?', (', '.join(normalized), session['user_id']))
        conn.commit(); conn.close()
        m.restart_logic()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/bot_creds', methods=['GET'])
def api_bot_creds():
    m = get_monitor(session.get('user_id'))
    if not m: return jsonify({'uid': '', 'pw': ''})
    try:
        with open(m.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r"Uid,\s*Pw\s*=\s*'([^']+)',\s*'([^']+)'", content)
        if match: return jsonify({'uid': match.group(1), 'pw': match.group(2)})
    except: pass
    return jsonify({'uid': '', 'pw': ''})


@app.route('/api/bot_creds', methods=['POST'])
def api_update_bot_creds():
    data = request.json
    new_uid = data.get('uid'); new_pw = data.get('pw')
    m = get_monitor(session.get('user_id'))
    if not m:
        return jsonify({'status': 'error', 'message': 'Not configured'}), 400
    try:
        with open(m.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        new_line = f"Uid, Pw = '{new_uid}', '{new_pw}'"
        new_content = re.sub(r"Uid,\s*Pw\s*=\s*'[^']*',\s*'[^']*'", new_line, content)
        with open(m.process_name, 'w', encoding='utf-8') as f:
            f.write(new_content)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET bot_uid=?, bot_pw=? WHERE id=?', (new_uid, new_pw, session['user_id']))
        conn.commit(); conn.close()
        m.restart_logic()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/friend', methods=['POST'])
def api_friend():
    data = request.json
    action = data.get('action')
    target_uid = data.get('uid')
    m = get_monitor(session.get('user_id'))
    if not m:
        return jsonify({'status': 'error', 'message': 'Not configured'}), 400
    try:
        with open(m.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r"Uid,\s*Pw\s*=\s*'([^']+)',\s*'([^']+)'", content)
        if not match:
            return jsonify({'status': 'error', 'message': 'Creds not found'}), 400
        bot_uid, bot_pw = match.group(1), match.group(2)
    except:
        return jsonify({'status': 'error', 'message': 'Read error'}), 500
    safe_uid = requests.utils.quote(str(bot_uid), safe='')
    safe_pw = requests.utils.quote(str(bot_pw), safe='')
    if action == 'list':
        try:
            r = requests.get(f"https://mahir-friend-web.vercel.app/friend_list?uid={safe_uid}&password={safe_pw}", timeout=25)
            return jsonify(r.json()) if r.status_code == 200 else jsonify({'status': 'error', 'message': f'HTTP {r.status_code}'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
    elif action in ['add', 'remove']:
        if not target_uid:
            return jsonify({'status': 'error', 'message': 'Missing UID'}), 400
        safe_t = requests.utils.quote(str(target_uid), safe='')
        endpoint = 'add_friend' if action == 'add' else 'remove_friend'
        try:
            r = requests.get(f"https://mahir-friend-web.vercel.app/{endpoint}?uid={safe_uid}&password={safe_pw}&friend_uid={safe_t}", timeout=15)
            return jsonify(r.json()) if r.status_code == 200 else jsonify({'status': 'error', 'message': f'HTTP {r.status_code}'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
    return jsonify({'status': 'error', 'message': 'Invalid action'}), 400


# ============================================================
#  MAIN
# ============================================================
if __name__ == '__main__':
    if not os.path.exists(MAHIR_SOURCE):
        with open(MAHIR_SOURCE, 'w') as f:
            f.write("# Mahir Bot\nUid, Pw = 'default', 'default'\nADMIN_UIDS = []\n")

    threading.Thread(target=lambda: (time.sleep(2), startup_launch_all_bots()), daemon=True).start()

    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║       MAHIR PANEL — OWNER EDITION                        ║
    ║       Port: 8080  |  Owner Panel: /owner/login           ║
    ║       Agent Panel: /agent/login                          ║
    ║       Owner: MAHIR TCP / MAHIR0208@                      ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)