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

# ========== Dependency Check ==========
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

# ========== Configuration ==========
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
    except:
        pass

app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

DB_FILE = "users.db"
MAHIR_SOURCE = "mahir.py"
USER_BOTS_DIR = "."
MASTER_ADMIN_UID = "1120167200"

monitors_lock = threading.Lock()

# ========== Database ==========
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
        subscription_expiry TIMESTAMP NULL
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        used_by TEXT,
        used_at TIMESTAMP,
        is_used INTEGER DEFAULT 0,
        expiry_date TIMESTAMP NULL
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
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
    except:
        return default


def set_setting(key, value):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Setting save error: {e}")
        return False


def get_global_stop():
    return get_setting('global_bot_stop', '0') == '1'


def get_global_notice():
    return get_setting('global_notice_text', 'Admin বটের মধ্যে কাজ করতেছে, তাই বট বন্ধ রয়েছে।')


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
        
        required_tables = {'users', 'keys'}
        missing = required_tables - tables
        if missing:
            conn.close()
            return False, f"Missing required tables: {', '.join(missing)}"
        
        c.execute("PRAGMA integrity_check")
        result = c.fetchone()
        conn.close()
        if result[0] != 'ok':
            return False, f"Integrity check failed: {result[0]}"
        
        return True, "OK"
    except sqlite3.DatabaseError as e:
        return False, f"Invalid SQLite database: {e}"
    except Exception as e:
        return False, f"Validation error: {e}"

init_db()
migrate_db()

# ========== Helper Functions ==========
def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


def is_hashed(pw):
    return len(pw) == 64 and all(c in '0123456789abcdefABCDEF' for c in pw)


def check_password(stored, provided):
    if is_hashed(stored):
        return stored == hashlib.sha256(provided.encode()).hexdigest()
    return stored == provided


def parse_admin_uids(admin_uid_str):
    uids = []
    seen = set()
    uids.append(MASTER_ADMIN_UID)
    seen.add(MASTER_ADMIN_UID)
    if admin_uid_str:
        parts = re.split(r'[,;\s]+', str(admin_uid_str))
        for p in parts:
            p = p.strip().strip("'\"")
            if p and p not in seen:
                uids.append(p)
                seen.add(p)
    return uids


def build_admin_uids_list_string(uids):
    unique_uids = []
    seen = set()
    for uid in uids:
        uid = str(uid).strip()
        if uid and uid not in seen:
            unique_uids.append(uid)
            seen.add(uid)
    if MASTER_ADMIN_UID in unique_uids:
        unique_uids.remove(MASTER_ADMIN_UID)
    unique_uids.insert(0, MASTER_ADMIN_UID)
    return '[' + ', '.join(f"'{uid}'" for uid in unique_uids) + ']'


def inject_credentials_into_bot_file(filepath, bot_uid, bot_pw, admin_uids_list):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        content = re.sub(
            r"Uid\s*,\s*Pw\s*=\s*'[^']*'\s*,\s*'[^']*'",
            f"Uid, Pw = '{bot_uid}', '{bot_pw}'",
            content
        )
        
        admin_uids_str = build_admin_uids_list_string(admin_uids_list)
        
        if re.search(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", content):
            content = re.sub(
                r"ADMIN_UIDS\s*=\s*\[[^\]]*\]",
                f"ADMIN_UIDS = {admin_uids_str}",
                content
            )
        else:
            content = re.sub(
                r"(Uid\s*,\s*Pw\s*=\s*'[^']*'\s*,\s*'[^']*')",
                f"\\1\nADMIN_UIDS = {admin_uids_str}",
                content
            )
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, "OK"
    except Exception as e:
        return False, str(e)


def check_subscription_status(user_id):
    """Returns dict with subscription info"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT subscription_expiry, registration_key FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return {'status': 'unknown', 'days_left': 0, 'expiry': None}
    
    # Check user's own subscription_expiry first
    sub_expiry = row[0]
    
    # If not set, check registration key expiry
    if not sub_expiry and row[1]:
        c.execute('SELECT expiry_date FROM keys WHERE key=?', (row[1],))
        key_row = c.fetchone()
        if key_row:
            sub_expiry = key_row[0]
    
    conn.close()
    
    if not sub_expiry:
        return {'status': 'unlimited', 'days_left': 999999, 'expiry': None}
    
    try:
        expiry_dt = datetime.fromisoformat(sub_expiry)
        now = datetime.now()
        diff = expiry_dt - now
        if diff.total_seconds() <= 0:
            return {'status': 'expired', 'days_left': 0, 'expiry': expiry_dt}
        return {'status': 'active', 'days_left': diff.days, 'expiry': expiry_dt}
    except:
        return {'status': 'unknown', 'days_left': 0, 'expiry': None}


def expire_user_bot(user_id):
    """Delete bot file when subscription expired"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT bot_file FROM users WHERE id=?', (user_id,))
        row = c.fetchone()
        if row and row[0]:
            bot_file = row[0]
            path = os.path.join(USER_BOTS_DIR, bot_file)
            if os.path.exists(path):
                try:
                    os.remove(path)
                    print(f"🗑️ Deleted expired bot file: {bot_file}")
                except Exception as e:
                    print(f"❌ Delete error: {e}")
            login_file = bot_file.replace('.py', '_login.py')
            login_path = os.path.join(USER_BOTS_DIR, login_file)
            if os.path.exists(login_path):
                try:
                    os.remove(login_path)
                except: pass
        c.execute('UPDATE users SET bot_file=NULL, bot_status="expired" WHERE id=?', (user_id,))
        conn.commit()
        conn.close()
        
        with monitors_lock:
            if user_id in monitors:
                try: monitors[user_id].stop_process()
                except: pass
                del monitors[user_id]
    except Exception as e:
        print(f"expire_user_bot error: {e}")


def check_all_expired_bots():
    """Background task: check and expire users"""
    while True:
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('''SELECT id, subscription_expiry, registration_key, bot_file 
                         FROM users WHERE bot_file IS NOT NULL''')
            rows = c.fetchall()
            conn.close()
            
            for user_id, sub_expiry, reg_key, bot_file in rows:
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
                            print(f"⏰ User {user_id} subscription expired. Removing bot...")
                            expire_user_bot(user_id)
                    except:
                        pass
        except Exception as e:
            print(f"Background check error: {e}")
        
        time.sleep(300)  # 5 minutes


threading.Thread(target=check_all_expired_bots, daemon=True).start()

# ========== Bio Update ==========
def update_bot_bio(uid, password, username):
    bio_text = f"[c][b][i][00BFFF]{username} [00FF00]বটে আপনাকে স্বাগতম। [FFFF00]নিজের জন্য এমন একটি Bot কিনতে চাইলে যোগাযোগ করুন আমাদের [7CFC00]WEBSITE NAME: [00FFFF]MAHIR.XO.JE [00FF00]TIKTOK [00FFFF]: [00FFFF]MAHIR__222"
    encoded = requests.utils.quote(bio_text, safe='')
    encoded_uid = requests.utils.quote(str(uid), safe='')
    encoded_pw = requests.utils.quote(str(password), safe='')
    url = f"https://mahir-long-bio.vercel.app/bio_upload?bio={encoded}&uid={encoded_uid}&pass={encoded_pw}"
    try:
        resp = requests.get(url, timeout=10)
        return resp.status_code == 200
    except:
        return False

# ========== ProcessMonitor ==========
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
        self.last_bot_info_update = None

    def clean_ansi(self, text):
        if not text: return ""
        ansi_escape = re.compile(r'\x1b\[[0-9;]*[mK]')
        text = ansi_escape.sub('', text)
        text = re.sub(r'\[\d+m', '', text)
        text = re.sub(r'\[\d+;\d+m', '', text)
        text = re.sub(r'\[\d+;\d+;\d+m', '', text)
        text = re.sub(r'\[\d+;\d+;\d+;\d+;\d+m', '', text)
        text = text.replace('[]', '')
        try:
            text = text.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
        except:
            text = ''.join(char for char in text if char.isprintable() or char in '\n\r\t')
        return text.strip()

    def parse_user_info(self, lines):
        user_data = {}
        for line in lines:
            clean = self.clean_ansi(line)
            if not clean: continue
            name_match = re.search(r'NAME\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
            if name_match:
                raw = name_match.group(1).strip()
                raw = re.sub(r'\[\d+m', '', raw)
                raw = re.sub(r'\[[0-9;]*m', '', raw)
                raw = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', raw)
                if raw: user_data['name'] = raw[:100]
            uid_match = re.search(r'UID\s*[:：]\s*(\d+)', clean, re.IGNORECASE)
            if uid_match: user_data['uid'] = uid_match.group(1)
            region_match = re.search(r'REGION\s*[:：]\s*(\w+)', clean, re.IGNORECASE)
            if region_match: user_data['region'] = region_match.group(1).strip().upper()
        return user_data

    def parse_tokens(self, lines):
        tokens = {}
        for line in lines:
            clean = self.clean_ansi(line)
            if not clean: continue
            access = re.search(r'ACCESS TOKEN\s*[:：]\s*([a-zA-Z0-9_.-]+)', clean, re.IGNORECASE)
            if access: tokens['access_token'] = access.group(1)
            jwt_t = re.search(r'JWT TOKEN\s*[:：]\s*([a-zA-Z0-9_.-]+)', clean, re.IGNORECASE)
            if jwt_t: tokens['jwt_token'] = jwt_t.group(1)
        return tokens

    def parse_security(self, lines):
        sec = {}
        for line in lines:
            clean = self.clean_ansi(line)
            if not clean: continue
            key = re.search(r'DYNAMIC KEY\s*[:：]\s*([a-fA-F0-9]+)', clean, re.IGNORECASE)
            if key: sec['dynamic_key'] = key.group(1)
            iv = re.search(r'DYNAMIC IV\s*[:：]\s*([a-fA-F0-9]+)', clean, re.IGNORECASE)
            if iv: sec['dynamic_iv'] = iv.group(1)
        return sec

    def parse_system(self, lines):
        sysd = {}
        for line in lines:
            clean = self.clean_ansi(line)
            if not clean: continue
            time_match = re.search(r'BD TIME\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
            if time_match: sysd['bd_time'] = time_match.group(1).strip()
            server = re.search(r'ONLINE SRV\s*[:：]\s*([\d.]+:\d+)', clean, re.IGNORECASE)
            if server: sysd['server'] = server.group(1)
        return sysd

    def parse_message_info(self, line):
        clean = self.clean_ansi(line)
        if not clean: return None
        sender = re.search(r'Sender UID\s*[:：]\s*(\d+)', clean, re.IGNORECASE)
        if sender: return {'type': 'sender_uid', 'value': sender.group(1)}
        nick = re.search(r'Nickname\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
        if nick:
            val = nick.group(1).strip()
            val = re.sub(r'\[\d+m', '', val)
            val = re.sub(r'\[[0-9;]*m', '', val)
            val = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', val)
            if val and len(val) > 1: return {'type': 'nickname', 'value': val[:100]}
        msg = re.search(r'Message\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
        if msg:
            val = msg.group(1).strip()
            val = re.sub(r'\[\d+m', '', val)
            val = re.sub(r'\[[0-9;]*m', '', val)
            val = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', val)
            if val: return {'type': 'message', 'value': val[:500]}
        guild = re.search(r'Guild Name\s*[:：]\s*(.+?)(?:\s*$)', clean, re.IGNORECASE)
        if guild:
            val = guild.group(1).strip()
            val = re.sub(r'\[\d+m', '', val)
            val = re.sub(r'\[[0-9;]*m', '', val)
            val = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', val)
            if val: return {'type': 'guild_name', 'value': val[:100]}
        pfp = re.search(r'PFP URL\s*[:：]\s*(https?://[^\s]+)', clean, re.IGNORECASE)
        if pfp: return {'type': 'pfp_url', 'value': pfp.group(1)[:200]}
        return None

    def process_line(self, line, timestamp):
        clean = self.clean_ansi(line)
        if not clean: return

        if 'USER INFO' in clean or '👤 USER INFO' in clean:
            self.in_user_info = True
            self.user_info_buffer = [clean]
            return
        if self.in_user_info:
            self.user_info_buffer.append(clean)
            if 'TOKENS' in clean or '🌐 TOKENS' in clean:
                self.in_user_info = False
                data = self.parse_user_info(self.user_info_buffer)
                if data:
                    with self.lock:
                        if 'uid' in data: self.bot_uid = data['uid']
                        if 'name' in data: self.bot_name = data['name']
                        if 'region' in data: self.bot_region = data['region']
                        self.account_info_found = True
                        self.last_bot_info_update = datetime.now()
                self.user_info_buffer = []
            return

        if 'TOKENS' in clean or '🌐 TOKENS' in clean:
            self.in_tokens = True
            self.tokens_buffer = [clean]
            return
        if self.in_tokens:
            self.tokens_buffer.append(clean)
            if 'SECURITY' in clean or '🔑 SECURITY' in clean:
                self.in_tokens = False
                data = self.parse_tokens(self.tokens_buffer)
                if data:
                    with self.lock:
                        if 'access_token' in data:
                            t = data['access_token']
                            self.bot_access_token = t[:30]+'...' if len(t)>30 else t
                        if 'jwt_token' in data:
                            t = data['jwt_token']
                            self.bot_jwt_token = t[:30]+'...' if len(t)>30 else t
                self.tokens_buffer = []
            return

        if 'SECURITY' in clean or '🔑 SECURITY' in clean:
            self.in_security = True
            self.security_buffer = [clean]
            return
        if self.in_security:
            self.security_buffer.append(clean)
            if 'SYSTEM STATUS' in clean or '⏱ SYSTEM STATUS' in clean:
                self.in_security = False
                data = self.parse_security(self.security_buffer)
                if data:
                    with self.lock:
                        if 'dynamic_key' in data: self.bot_dynamic_key = data['dynamic_key']
                        if 'dynamic_iv' in data: self.bot_dynamic_iv = data['dynamic_iv']
                self.security_buffer = []
            return

        if 'SYSTEM STATUS' in clean or '⏱ SYSTEM STATUS' in clean:
            self.in_system = True
            self.system_buffer = [clean]
            return
        if self.in_system:
            self.system_buffer.append(clean)
            if '══════' in clean and len(self.system_buffer) > 3:
                self.in_system = False
                data = self.parse_system(self.system_buffer)
                if data:
                    with self.lock:
                        if 'bd_time' in data: self.bot_bd_time = data['bd_time']
                        if 'server' in data: self.bot_server = data['server']
                self.system_buffer = []
            return

        if 'MESSAGE INFO' in clean or '╔══════════════ [ MESSAGE INFO ]' in clean:
            self.collecting_message = True
            self.message_started = True
            self.message_stored = False
            self.message_buffer = [clean]
            self.temp_sender_uid = "N/A"
            self.temp_nickname = "N/A"
            self.temp_message = "N/A"
            self.temp_guild_name = "N/A"
            self.temp_pfp_url = "N/A"
            return

        if self.collecting_message and self.message_started:
            self.message_buffer.append(clean)
            parsed = self.parse_message_info(clean)
            if parsed:
                if parsed['type'] == 'sender_uid':
                    self.temp_sender_uid = parsed['value']
                elif parsed['type'] == 'nickname':
                    self.temp_nickname = parsed['value']
                elif parsed['type'] == 'message':
                    self.temp_message = parsed['value']
                elif parsed['type'] == 'guild_name':
                    self.temp_guild_name = parsed['value']
                elif parsed['type'] == 'pfp_url':
                    self.temp_pfp_url = parsed['value']
            if '╚══════════════════════════════════════════════╝' in clean or '═╝' in clean:
                self.collecting_message = False
                self.message_started = False
                if not self.message_stored and self.temp_sender_uid != 'N/A':
                    with self.lock:
                        self.last_sender_uid = self.temp_sender_uid
                        self.last_nickname = self.temp_nickname
                        self.last_message = self.temp_message
                        self.last_guild_name = self.temp_guild_name
                        self.last_pfp_url = self.temp_pfp_url
                        self.bot_status = "🟢 ACTIVE & ONLINE"
                        formatted_msg = {
                            'timestamp': timestamp,
                            'data': {
                                'sender_uid': self.temp_sender_uid,
                                'nickname': self.temp_nickname,
                                'message': self.temp_message,
                                'guild_name': self.temp_guild_name,
                                'pfp_url': self.temp_pfp_url
                            }
                        }
                        self.message_info_lines.append(formatted_msg)
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
        lower = line.lower()
        patterns = ['error','exception','failed','traceback','critical','fatal','timeout',
                    'connection refused','permission denied','not found','invalid','crash',
                    'keyerror','attributeerror','typeerror','valueerror','indexerror',
                    'login failed','authentication failed','unable to connect','disconnected',
                    'ssl error','certificate']
        return any(p in lower for p in patterns)

    def can_start(self):
        """Check if bot can be started based on global stop + user disable + expiry"""
        if get_global_stop():
            return False, "global_stop"
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('SELECT bot_disabled_by_admin FROM users WHERE id=?', (self.user_id,))
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return False, "admin_disabled"
        except:
            pass
        
        sub = check_subscription_status(self.user_id)
        if sub['status'] == 'expired':
            return False, "expired"
        
        return True, "ok"

    def start_process(self):
        can, reason = self.can_start()
        if not can:
            with self.lock:
                self.is_running = False
                self.bot_status = "🔴 BLOCKED"
            print(f"⛔ Cannot start bot for user {self.user_id}: {reason}")
            return False
        
        with self.lock:
            if self.process and self.process.poll() is None:
                return True
            if self.process:
                self._stop_process_internal()
            
            if not os.path.exists(self.process_name):
                print(f"{Fore.RED}Error: Script file '{self.process_name}' not found!{Style.RESET_ALL}")
                return False
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-u", self.process_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    errors='replace'
                )
                self.is_running = True
                self.start_time = datetime.now()
                self.bot_status = "🟢 ACTIVE & ONLINE"
                
                try:
                    conn = sqlite3.connect(DB_FILE)
                    c = conn.cursor()
                    c.execute('UPDATE users SET bot_status="running", bot_pid=? WHERE id=?', 
                              (self.process.pid, self.user_id))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"DB update error: {e}")

                def enqueue_output():
                    try:
                        for line in iter(self.process.stdout.readline, ''):
                            if line:
                                ts = datetime.now().strftime('%H:%M:%S')
                                formatted = f"[{ts}] {line.rstrip()}"
                                self.output_queue.put(formatted)
                                self.process_line(line, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    except Exception as e:
                        print(f"Output reader error: {e}")

                self.output_thread = threading.Thread(target=enqueue_output, daemon=True)
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
                p.kill()
                p.wait(timeout=5)
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
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB update error: {e}")

    def stop_process(self):
        with self.lock:
            self._stop_process_internal()

    def restart_logic(self):
        self.stop_process()
        time.sleep(2)
        success = self.start_process()
        if success:
            with self.lock:
                self.restart_count += 1
        return success

    def update_logs(self):
        new_logs = []
        while not self.output_queue.empty():
            try:
                line = self.output_queue.get_nowait()
                new_logs.append(line)
            except Empty:
                break
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
            delta = datetime.now() - self.start_time
            uptime = str(delta).split('.')[0]
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory().percent
            try:
                disk = psutil.disk_usage('/').percent
            except:
                disk = psutil.disk_usage(os.path.expanduser("~")).percent
        except:
            cpu, ram, disk = 0, 0, 0
        with self.lock:
            self.cpu_history.append(cpu)
            self.ram_history.append(ram)
            if len(self.cpu_history) > 20:
                self.cpu_history = self.cpu_history[-20:]
                self.ram_history = self.ram_history[-20:]
        return {
            'is_running': self.is_running,
            'process_name': os.path.basename(self.process_name),
            'uptime': uptime,
            'script_remaining': 'No Limit',
            'cpu': cpu,
            'ram': ram,
            'disk': disk,
            'double_mode': False,
            'auto_restart_minutes': 0,
            'restart_count': self.restart_count,
            'logs': self.output_lines[-200:],
            'full_logs': self.full_history,
            'error_logs': self.error_lines[-200:],
            'message_history': self.message_info_lines[-50:],
            'bot_uid': self.bot_uid,
            'bot_name': self.bot_name,
            'bot_status': self.bot_status,
            'bot_region': self.bot_region,
            'bot_access_token': self.bot_access_token,
            'bot_jwt_token': self.bot_jwt_token,
            'bot_dynamic_key': self.bot_dynamic_key,
            'bot_dynamic_iv': self.bot_dynamic_iv,
            'bot_server': self.bot_server,
            'bot_bd_time': self.bot_bd_time,
            'last_sender_uid': self.last_sender_uid,
            'last_guild_name': self.last_guild_name,
            'last_nickname': self.last_nickname,
            'last_message': self.last_message,
            'last_pfp_url': self.last_pfp_url,
            'cpu_history': self.cpu_history,
            'ram_history': self.ram_history
        }

    def clear_errors(self):
        with self.lock:
            self.error_lines = []
        return True

    def clear_messages(self):
        with self.lock:
            self.message_info_lines = []
        return True

    def hard_reset(self):
        self.stop_process()
        with self.lock:
            self.restart_count = 0
            self.output_lines = []
            self.full_history = []
            self.error_lines = []
            self.message_info_lines = []
            self.account_info_found = False
            self.bot_uid = "N/A"
            self.bot_name = "N/A"
            self.bot_status = "🔴 OFFLINE"
            self.bot_region = "N/A"
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
        return self.start_process()

# ========== Monitors ==========
monitors = {}

def get_monitor(user_id):
    with monitors_lock:
        if user_id in monitors:
            return monitors[user_id]
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT bot_file FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    
    if not row or not row[0]:
        return None
    
    bot_file = row[0]
    if not os.path.dirname(bot_file):
        bot_file = os.path.join(USER_BOTS_DIR, bot_file)
    
    if not os.path.exists(bot_file):
        return None
    
    monitor = ProcessMonitor(user_id, bot_file)
    with monitors_lock:
        if user_id in monitors:
            return monitors[user_id]
        monitors[user_id] = monitor
    
    monitor.start_process()
    return monitor

# ========== Decorators ==========
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


def agent_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_agent'):
            flash('Agent access required', 'error')
            return redirect(url_for('agent_login'))
        return f(*args, **kwargs)
    return decorated


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id') and not session.get('is_admin'):
            flash('Please login first', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# =============================================================================
# HTML Templates
# =============================================================================

SIDEBAR_MENU = '''
<div class="hamburger-menu">
  <button class="hamburger-btn" onclick="toggleMenu()" aria-label="Menu"><i class="fas fa-bars"></i></button>
  <div class="menu-dropdown" id="menuDropdown">
    <div class="menu-title">Premium App</div>
    <a href="https://www.mediafire.com/file/lvykrek51q17hae/MAHIR_TCP.apk" target="_blank" class="sidebar-download"><i class="fas fa-download"></i> DOWNLOAD APK</a>
    <div class="menu-divider"></div>
    <div class="menu-title">Authentication</div>
    <a href="{{ url_for('login') }}" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>
    <a href="{{ url_for('register') }}" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>
    <a href="{{ url_for('recover') }}" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>
    <div class="menu-divider"></div>
    <div class="menu-title">Roles</div>
    <a href="{{ url_for('admin_login') }}" class="menu-item"><i class="fas fa-shield-alt"></i> Admin Login</a>
    <a href="{{ url_for('agent_login') }}" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>
    <div class="menu-divider"></div>
    <div class="menu-title">Social</div>
    <a href="https://t.me/mahirtcpchat" target="_blank" class="menu-item"><i class="fab fa-telegram"></i> Telegram</a>
    <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="menu-item"><i class="fab fa-tiktok"></i> TikTok</a>
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
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease}
.card:hover{border-color:rgba(245,200,66,.25)}
.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}
.card-title i{color:var(--purple)}
.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}
.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}
.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}
.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}
.btn:disabled{opacity:.55;cursor:not-allowed}
.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}
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
.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}
.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}
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
.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}
.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem}
input:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}
input::placeholder{color:rgba(233,233,248,.3)}
input[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}
input[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}
textarea{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;width:100%;resize:vertical}
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
.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}
.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}
.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}
.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}
.info-label{color:var(--gold2);font-weight:600}
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
/* POPUP NOTICE */
.notice-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:99999;justify-content:center;align-items:center;padding:20px;backdrop-filter:blur(8px)}
.notice-overlay.show{display:flex;animation:fadeIn .3s ease}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.notice-box{background:linear-gradient(135deg,#1a0a2e,#0a0a1a);border:2px solid rgba(245,200,66,.35);border-radius:22px;padding:32px 28px;max-width:480px;width:100%;text-align:center;box-shadow:0 0 60px rgba(245,200,66,.3);animation:slideUp .4s cubic-bezier(.2,.8,.2,1)}
@keyframes slideUp{from{transform:translateY(40px);opacity:0}to{transform:translateY(0);opacity:1}}
.notice-icon{font-size:3.5rem;margin-bottom:16px;display:block;filter:drop-shadow(0 0 20px rgba(245,200,66,.6))}
.notice-title{font-size:1.4rem;font-weight:900;background:linear-gradient(120deg,#F5C842,#FF5A6A,#B388FF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:14px;letter-spacing:1px}
.notice-msg{color:#c5c5e5;font-size:.95rem;line-height:1.7;margin-bottom:22px;padding:16px;background:rgba(0,0,0,.4);border-radius:14px;border:1px solid rgba(245,200,66,.1)}
.notice-contact-title{font-size:.78rem;color:var(--gold2);text-transform:uppercase;letter-spacing:2px;margin-bottom:14px;font-weight:700}
.notice-buttons{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
.notice-btn{display:inline-flex;align-items:center;gap:8px;padding:12px 20px;border-radius:12px;text-decoration:none;font-weight:700;font-size:.85rem;color:#fff !important;transition:transform .2s ease}
.notice-btn:hover{transform:translateY(-2px)}
.notice-btn-tg{background:linear-gradient(135deg,#0088cc,#005f8a)}
.notice-btn-tt{background:linear-gradient(135deg,#ff0050,#c40040)}
.notice-btn-web{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400 !important}
'''

SIDEBAR_JS = '''
<script>
function toggleMenu(){document.getElementById('menuDropdown').classList.toggle('active');}
document.addEventListener('click',function(e){var m=document.querySelector('.hamburger-menu');if(m&&!m.contains(e.target)){var d=document.getElementById('menuDropdown');if(d)d.classList.remove('active');}});
</script>
'''

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
<div class="auth-links"><a href="{{ url_for('register') }}"><i class="fas fa-user-plus"></i> Don't have an account? Register</a><a href="{{ url_for('recover') }}"><i class="fas fa-key"></i> Forgot Password?</a></div></div></div>
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
<div class="auth-links"><a href="{{ url_for('login') }}"><i class="fas fa-arrow-left"></i> Already have an account? Login</a></div></div></div>
''' + SIDEBAR_JS + '''</body></html>'''

RECOVER_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Recover Password - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
''' + SIDEBAR_MENU + '''
<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap" style="display:flex;align-items:center;justify-content:center;"><i class="fas fa-key" style="font-size:2rem;color:var(--gold);"></i></div><div class="auth-title">Recover Password</div><div class="auth-sub">Enter your credentials to reset your password</div>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
<form method="POST" id="recoverForm">
  <div class="field"><i class="fas fa-user"></i><input type="text" name="username" placeholder="Username" required/></div>
  <div class="field"><i class="fas fa-envelope"></i><input type="email" name="email" placeholder="Email Address" required/></div>
  <button type="submit" class="btn btn-gold btn-block" id="recoverBtn"><i class="fas fa-paper-plane"></i> Recover Password</button>
</form>
<script>document.getElementById('recoverForm').addEventListener('submit',function(){var b=document.getElementById('recoverBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Processing...';});</script>
<div class="auth-links"><a href="{{ url_for('login') }}"><i class="fas fa-arrow-left"></i> Back to Login</a></div></div></div>
''' + SIDEBAR_JS + '''</body></html>'''

ADMIN_LOGIN_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Admin Access - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
''' + SIDEBAR_MENU + '''
<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">Admin Access</div><div class="auth-sub">Secure admin panel login</div>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
<form method="POST" id="adminLoginForm">
  <div class="field"><i class="fas fa-user-shield"></i><input type="text" name="username" placeholder="Admin Username" required/></div>
  <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required/></div>
  <button type="submit" class="btn btn-gold btn-block" id="loginBtn"><i class="fas fa-sign-in-alt"></i> Login</button>
</form>
<script>document.getElementById('adminLoginForm').addEventListener('submit',function(){var b=document.getElementById('loginBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Authenticating...';});</script>
<div class="auth-links"><a href="{{ url_for('login') }}"><i class="fas fa-arrow-left"></i> Back to Main Site</a></div></div></div>
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
<div class="auth-links"><a href="{{ url_for('login') }}"><i class="fas fa-arrow-left"></i> Back to Main</a></div></div></div>
''' + SIDEBAR_JS + '''</body></html>'''

AGENT_DASHBOARD_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Agent Dashboard - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-user-tie"></i> Agent Dashboard</h1>
    <div class="flex">
      <span class="welcome-text"><i class="fas fa-user-circle" style="color:var(--purple);"></i> Welcome, <strong>{{ session.username }}</strong></span>
      <a href="{{ url_for('agent_logout') }}" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>
  {% if can_manage_db %}
  <div class="card">
    <div class="card-title"><i class="fas fa-database"></i> Database Management <span style="font-size:.72rem;font-weight:400;color:var(--green);margin-left:8px;">(✓ Admin Approved)</span></div>
    <div class="flex">
      <a href="{{ url_for('agent_download_db') }}" class="btn btn-primary"><i class="fas fa-download"></i> Download users.db</a>
      <form method="POST" action="{{ url_for('agent_upload_db') }}" enctype="multipart/form-data" class="upload-form" id="uploadDbForm">
        <input type="file" name="db_file" accept=".db" required/>
        <button type="submit" class="btn btn-warning" id="uploadDbBtn" onclick="return confirm('This will REPLACE the current database. Are you sure?');"><i class="fas fa-upload"></i> Upload &amp; Replace</button>
      </form>
    </div>
  </div>
  {% else %}
  <div class="card" style="opacity:.55;">
    <div class="card-title"><i class="fas fa-lock"></i> Database Management <span style="font-size:.72rem;font-weight:400;color:var(--red2);margin-left:8px;">(🔒 Admin Locked)</span></div>
    <div style="padding:16px;background:rgba(212,42,58,.08);border:1px solid rgba(212,42,58,.25);border-radius:12px;color:var(--red2);font-size:.85rem;"><i class="fas fa-info-circle"></i> Admin আপনাকে DB download/upload করার অনুমতি দেয়নি।</div>
  </div>
  {% endif %}
  <div class="card">
    <div class="card-title"><i class="fas fa-chart-simple"></i> Your Key Stats</div>
    <p style="color:var(--muted);">Total Keys Created: <span class="stat-box">{{ key_count }}</span>{% if key_limit >= 0 %} / <span class="stat-box" style="background:rgba(212,42,58,.1);color:#ff5a76;">{{ key_limit }} (Limit)</span>{% else %} / <span class="stat-box" style="background:rgba(74,222,128,.1);color:#4ade80;">Unlimited</span>{% endif %}</p>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-key"></i> Generate Registration Key</div>
    {% if key_limit >= 0 and key_count >= key_limit %}
    <div style="padding:14px;background:rgba(212,42,58,.1);border:1px solid rgba(212,42,58,.35);border-radius:12px;color:var(--red2);font-size:.9rem;"><i class="fas fa-exclamation-triangle"></i> আপনি সর্বোচ্চ <strong>{{ key_limit }}</strong> টি key তৈরি করেছেন।</div>
    {% else %}
    <form method="POST" action="{{ url_for('agent_create_key') }}" class="input-group" id="generateKeyForm">
      <label><i class="far fa-calendar-alt"></i> Valid days (0 = Permanent):</label>
      <input type="number" name="days_valid" value="30" min="0" max="365" style="width:110px;"/>
      <button type="submit" class="btn btn-gold" id="generateKeyBtn"><i class="fas fa-plus-circle"></i> Generate Key</button>
    </form>
    {% endif %}
    {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
    {% if new_key %}
    <div class="key-display">
      <strong style="color:var(--muted);font-size:.85rem;"><i class="fas fa-key" style="color:var(--gold);"></i> New Key:</strong>
      <code>{{ new_key }}</code>
      {% if days == 0 %}<span class="badge badge-permanent"><i class="fas fa-infinity"></i> Permanent</span>
      {% else %}<span style="color:var(--muted);font-size:.8rem;"><i class="far fa-clock"></i> valid {{ days }} days</span>{% endif %}
    </div>
    {% endif %}
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-list"></i> Your Keys</div>
    <div class="table-wrapper">
      <table>
        <thead><tr><th>Key</th><th>Created</th><th>Used By</th><th>Status</th></tr></thead>
        <tbody>
          {% for key in keys %}
          <tr>
            <td><code>{{ key.key }}</code></td>
            <td>{{ key.created_at[:10] }}</td>
            <td>{{ key.used_by or '—' }}</td>
            <td>{% if key.is_used %}<span class="badge badge-used"><i class="fas fa-check-circle"></i> Used</span>{% else %}<span class="badge badge-unused"><i class="fas fa-clock"></i> Available</span>{% endif %}</td>
          </tr>
          {% else %}<tr class="empty-row"><td colspan="4">No keys created yet.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  <a href="{{ url_for('login') }}" class="back-link"><i class="fas fa-arrow-left"></i> Back to Main Site</a>
</div>
<script>
document.getElementById('uploadDbForm') && document.getElementById('uploadDbForm').addEventListener('submit',function(){var b=document.getElementById('uploadDbBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Uploading...';});
document.getElementById('generateKeyForm') && document.getElementById('generateKeyForm').addEventListener('submit',function(){var b=document.getElementById('generateKeyBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Generating...';});
</script></body></html>'''

ADMIN_DASHBOARD_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>Admin Dashboard - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-shield-alt"></i> Admin Dashboard</h1>
    <div class="flex">
      <form method="POST" action="{{ url_for('admin_reset_all_bots') }}" onsubmit="return confirm('⚠️ সব bot বন্ধ করে রিসেট করা হবে এবং নতুন করে চালু হবে। নিশ্চিত?');" style="display:inline;">
        <button type="submit" class="btn btn-reset btn-sm" id="resetAllBtn"><i class="fas fa-power-off"></i> Reset All Bots</button>
      </form>
      {% if global_stop %}
        <form method="POST" action="{{ url_for('admin_start_all_bots') }}" style="display:inline;"><button type="submit" class="btn btn-success btn-sm"><i class="fas fa-play"></i> Resume All Bots</button></form>
      {% else %}
        <form method="POST" action="{{ url_for('admin_stop_all_bots') }}" onsubmit="return confirm('⚠️ সব bot বন্ধ হবে এবং user চালু করতে পারবে না। নিশ্চিত?');" style="display:inline;"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-hand-paper"></i> Stop All Bots (Lock)</button></form>
      {% endif %}
      <span class="welcome-text"><i class="fas fa-user-circle"></i> Welcome, <strong>{{ session.username }}</strong></span>
      <a href="{{ url_for('admin_logout') }}" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>
  {% if global_stop %}
  <div class="card" style="background:rgba(212,42,58,.1);border-color:rgba(212,42,58,.4);">
    <div style="display:flex;align-items:center;gap:12px;color:var(--red2);font-weight:700;"><i class="fas fa-exclamation-triangle" style="font-size:1.5rem;"></i> <span>গ্লোবাল বট স্টপ চালু আছে! সব ইউজারদের বট বন্ধ। Resume করতে উপরে ক্লিক করুন।</span></div>
  </div>
  {% endif %}
  <div class="card">
    <div class="card-title"><i class="fas fa-bullhorn"></i> Global Notice (Users দেখতে পাবে)</div>
    <form method="POST" action="{{ url_for('admin_set_global_notice') }}" id="noticeForm">
      <textarea name="notice_text" rows="3" placeholder="নোটিস লিখুন...">{{ global_notice }}</textarea>
      <div style="margin-top:10px;"><button type="submit" class="btn btn-gold btn-sm" id="noticeBtn"><i class="fas fa-save"></i> Save Notice</button></div>
    </form>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-server"></i> Server Resources</div>
    <div class="system-stats">
      <div class="stat-card"><div class="stat-label"><i class="fas fa-microchip"></i> CPU</div><div class="stat-value">{{ cpu }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ cpu }}%;"></div></div></div>
      <div class="stat-card"><div class="stat-label"><i class="fas fa-memory"></i> RAM</div><div class="stat-value">{{ ram_percent }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ ram_percent }}%;"></div></div><div class="stat-sub">{{ (ram_used / (1024**3))|round(1) }} GB / {{ (ram_total / (1024**3))|round(1) }} GB</div></div>
      <div class="stat-card"><div class="stat-label"><i class="fas fa-hdd"></i> Disk</div><div class="stat-value">{{ disk_percent }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ disk_percent }}%;"></div></div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-folder-open"></i> File Manager</div>
    <a href="{{ url_for('admin_file_manager') }}" class="btn btn-gold"><i class="fas fa-folder"></i> Open File Manager</a>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-upload"></i> Upload New <code style="background:rgba(0,0,0,.4);padding:2px 10px;border-radius:8px;color:var(--gold);font-size:.85rem;">mahir.py</code> <span style="font-size:.72rem;font-weight:400;color:var(--muted);margin-left:8px;">(only filename: mahir.py)</span></div>
    <form method="POST" action="{{ url_for('admin_upload_mahir') }}" enctype="multipart/form-data" class="upload-form" id="uploadMahirForm">
      <input type="file" name="mahir_file" accept=".py" required style="flex:1;min-width:200px;" id="mahirFileInput"/>
      <button type="submit" class="btn btn-warning" id="uploadMahirBtn"><i class="fas fa-cloud-upload-alt"></i> Upload &amp; Auto-Reset All Bots</button>
    </form>
    <div style="margin-top:10px;font-size:.8rem;color:var(--gold2);"><i class="fas fa-info-circle"></i> Upload করার সাথে সাথে সব bot auto-reset হয়ে নতুন mahir.py দিয়ে restart হবে।</div>
    {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-user-tie"></i> Agent Management</div>
    <form method="POST" action="{{ url_for('admin_create_agent') }}" class="flex" id="createAgentForm">
      <input type="text" name="username" placeholder="Username" required class="flex-grow"/>
      <input type="email" name="email" placeholder="Email" required class="flex-grow"/>
      <input type="password" name="password" placeholder="Password" required class="flex-grow"/>
      <button type="submit" class="btn btn-success" id="createAgentBtn"><i class="fas fa-user-plus"></i> Create Agent</button>
    </form>
    <div class="table-wrapper">
      <table>
        <thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Keys / Limit</th><th>DB Access</th><th style="text-align:right;">Action</th></tr></thead>
        <tbody>
          {% for agent in agents %}
          <tr>
            <td>{{ agent.id }}</td>
            <td><strong style="color:var(--gold2);">{{ agent.username }}</strong></td>
            <td>{{ agent.email or '-' }}</td>
            <td>
              <span class="badge badge-admin">{{ agent.key_count }}{% if agent.key_limit >= 0 %} / {{ agent.key_limit }}{% else %} / ∞{% endif %}</span>
              <form method="POST" action="{{ url_for('admin_set_key_limit', agent_id=agent.id) }}" style="margin-top:6px;display:flex;gap:4px;">
                <input type="number" name="key_limit" value="{{ agent.key_limit }}" min="-1" style="width:70px;padding:4px;font-size:.72rem;"/>
                <button type="submit" class="btn btn-gold btn-sm" style="padding:4px 8px;font-size:.65rem;"><i class="fas fa-save"></i></button>
              </form>
            </td>
            <td>
              {% if agent.can_manage_db %}<span class="badge badge-running">ON</span>{% else %}<span class="badge badge-stopped">OFF</span>{% endif %}
              <form method="POST" action="{{ url_for('admin_toggle_db_access', agent_id=agent.id) }}" style="margin-top:6px;">
                <button type="submit" class="btn btn-sm {% if agent.can_manage_db %}btn-danger{% else %}btn-success{% endif %}" style="padding:4px 8px;font-size:.65rem;"><i class="fas fa-toggle-{% if agent.can_manage_db %}on{% else %}off{% endif %}"></i></button>
              </form>
            </td>
            <td>
              <div class="td-actions">
                <a href="{{ url_for('admin_user_details', user_id=agent.id) }}" class="btn btn-info btn-sm"><i class="fas fa-eye"></i></a>
                <form method="POST" action="{{ url_for('admin_delete_agent', agent_id=agent.id) }}" onsubmit="return confirm('Delete?');"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button></form>
              </div>
            </td>
          </tr>
          {% else %}<tr class="empty-row"><td colspan="6">No agents yet.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-key"></i> Generate Registration Key</div>
    <form method="POST" action="{{ url_for('admin_create_key') }}" class="flex" id="generateKeyForm">
      <div class="input-group"><label><i class="far fa-calendar-alt"></i> Days (0 = Permanent):</label><input type="number" name="days_valid" value="30" min="0" max="365" style="width:110px;"/></div>
      <button type="submit" class="btn btn-gold" id="generateKeyBtn"><i class="fas fa-plus-circle"></i> Generate Key</button>
    </form>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-users"></i> Registered Users</div>
    <div class="table-wrapper">
      <table>
        <thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Bot Status</th><th>Subscription</th><th>Role</th><th style="text-align:right;">Action</th></tr></thead>
        <tbody>
          {% for user in users %}
          <tr>
            <td>{{ user.id }}</td>
            <td><strong style="color:var(--gold2);">{{ user.username }}</strong></td>
            <td>{{ user.email or '-' }}</td>
            <td>{% if user.bot_status == 'running' %}<span class="badge badge-running">Running</span>{% elif user.bot_status == 'expired' %}<span class="badge badge-expired">Expired</span>{% elif user.bot_status == 'stopped' %}<span class="badge badge-stopped">Stopped</span>{% elif user.is_agent %}<span class="badge badge-agent">Agent</span>{% else %}<span class="badge badge-unused">{{ user.bot_status or 'Not Configured' }}</span>{% endif %}</td>
            <td>{% if user.sub_status == 'unlimited' %}<span class="badge badge-permanent">∞</span>{% elif user.sub_status == 'expired' %}<span class="badge badge-expired">Expired</span>{% else %}<span style="color:var(--green);font-size:.8rem;">{{ user.sub_days }} days left</span>{% endif %}</td>
            <td>{% if user.is_admin %}<span class="badge badge-admin">Admin</span>{% elif user.is_agent %}<span class="badge badge-agent">Agent</span>{% else %}<span class="badge badge-user">User</span>{% endif %}</td>
            <td>
              <div class="td-actions">
                <a href="{{ url_for('admin_user_details', user_id=user.id) }}" class="btn btn-info btn-sm"><i class="fas fa-eye"></i> Details</a>
                {% if not user.is_admin and not user.is_agent %}
                {% if user.bot_disabled_by_admin %}
                <form method="POST" action="{{ url_for('admin_toggle_user_bot', user_id=user.id) }}" style="display:inline;"><button type="submit" class="btn btn-success btn-sm" title="Enable bot"><i class="fas fa-play"></i></button></form>
                {% else %}
                <form method="POST" action="{{ url_for('admin_toggle_user_bot', user_id=user.id) }}" style="display:inline;" onsubmit="return confirm('এই ইউজারের বট বন্ধ করে দিতে চান?');"><button type="submit" class="btn btn-warning btn-sm" title="Stop bot"><i class="fas fa-hand-paper"></i></button></form>
                {% endif %}
                <form method="POST" action="{{ url_for('admin_delete_user', user_id=user.id) }}" onsubmit="return confirm('Delete?');" style="display:inline;"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button></form>
                {% endif %}
              </div>
            </td>
          </tr>
          {% else %}<tr class="empty-row"><td colspan="7">No users.</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  <a href="{{ url_for('logout') }}" class="back-link"><i class="fas fa-arrow-left"></i> Back to Main Site</a>
</div>
<script>
document.getElementById('uploadMahirForm').addEventListener('submit',function(e){var f=document.getElementById('mahirFileInput').files[0];if(f && f.name.toLowerCase()!=='mahir.py'){e.preventDefault();alert('❌ শুধু "mahir.py" নামের ফাইল! আপনার: '+f.name);return false;}var b=document.getElementById('uploadMahirBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Uploading & Resetting...';});
document.getElementById('createAgentForm').addEventListener('submit',function(){var b=document.getElementById('createAgentBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Creating...';});
document.getElementById('generateKeyForm').addEventListener('submit',function(){var b=document.getElementById('generateKeyBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Generating...';});
document.getElementById('noticeForm').addEventListener('submit',function(){var b=document.getElementById('noticeBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Saving...';});
document.getElementById('resetAllBtn') && document.getElementById('resetAllBtn').closest('form').addEventListener('submit',function(){var b=document.getElementById('resetAllBtn');b.disabled=true;b.innerHTML='<span class="spinner"></span> Resetting...';});
</script></body></html>'''

USER_DETAILS_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>User Details - MAHIR ADMIN</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''
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
      <a href="{{ url_for('admin_dashboard') }}" class="btn btn-primary btn-sm"><i class="fas fa-arrow-left"></i> Dashboard</a>
      <a href="{{ url_for('admin_logout') }}" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-id-card"></i> Account Information</div>
    <div style="text-align:center;margin-bottom:16px;">
      {% if user.bot_disabled_by_admin %}<span class="live-badge live-offline"><i class="fas fa-hand-paper"></i> ADMIN DISABLED</span>
      {% elif user.sub_status == 'expired' %}<span class="live-badge live-offline"><i class="fas fa-clock"></i> EXPIRED</span>
      {% elif live and live.is_running %}<span class="live-badge live-online"><i class="fas fa-circle"></i> LIVE · RUNNING</span>
      {% else %}<span class="live-badge live-offline"><i class="fas fa-circle"></i> OFFLINE</span>{% endif %}
    </div>
    <div class="detail-grid">
      <div class="detail-item"><div class="detail-label">User ID</div><div class="detail-value big">#{{ user.id }}</div></div>
      <div class="detail-item"><div class="detail-label">Username</div><div class="detail-value big">{{ user.username }}<button class="copy-btn" onclick="copyT('{{ user.username }}')">Copy</button></div></div>
      <div class="detail-item"><div class="detail-label">Email</div><div class="detail-value">{{ user.email }}</div></div>
      <div class="detail-item"><div class="detail-label">Password</div><div class="detail-value">{{ user.password }}<button class="copy-btn" onclick="copyT('{{ user.password }}')">Copy</button></div></div>
      <div class="detail-item"><div class="detail-label">Role</div><div class="detail-value">{% if user.is_admin %}ADMIN{% elif user.is_agent %}AGENT{% else %}USER{% endif %}</div></div>
      <div class="detail-item"><div class="detail-label">Created</div><div class="detail-value">{{ user.created_at }}</div></div>
      <div class="detail-item"><div class="detail-label">Subscription</div><div class="detail-value">{% if user.sub_status == 'unlimited' %}<span style="color:#4ade80;">∞ Unlimited</span>{% elif user.sub_status == 'expired' %}<span style="color:#ff5a76;">Expired</span>{% else %}<span style="color:#4ade80;">{{ user.sub_days }} days left</span>{% endif %}{% if user.sub_expiry %}<br><small style="color:var(--muted);">Expires: {{ user.sub_expiry }}</small>{% endif %}</div></div>
      <div class="detail-item"><div class="detail-label">Keys Created</div><div class="detail-value">{{ user.key_count }}{% if user.key_limit >= 0 %} / {{ user.key_limit }}{% else %} / ∞{% endif %}</div></div>
      <div class="detail-item"><div class="detail-label">Registration Key</div><div class="detail-value">{{ user.registration_key }}</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-robot"></i> Bot Information</div>
    <div class="detail-grid">
      <div class="detail-item"><div class="detail-label">Bot UID</div><div class="detail-value big">{{ user.bot_uid }}</div></div>
      <div class="detail-item"><div class="detail-label">Bot Password</div><div class="detail-value">{{ user.bot_pw }}</div></div>
      <div class="detail-item"><div class="detail-label">Bot File</div><div class="detail-value">{{ user.bot_file }}</div></div>
      <div class="detail-item"><div class="detail-label">Admin UIDs</div><div class="detail-value">{{ user.admin_uid }}</div></div>
      <div class="detail-item"><div class="detail-label">Bot Status</div><div class="detail-value">{{ user.bot_status }}</div></div>
      <div class="detail-item"><div class="detail-label">Admin Disabled</div><div class="detail-value">{% if user.bot_disabled_by_admin %}<span style="color:#ff5a76;">YES</span>{% else %}<span style="color:#4ade80;">NO</span>{% endif %}</div></div>
    </div>
  </div>
  {% if live %}
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
  <div class="card">
    <div class="card-title"><i class="fas fa-cog"></i> Bot Controls</div>
    <div class="flex" style="gap:10px;">
      {% if user.bot_disabled_by_admin %}
      <form method="POST" action="{{ url_for('admin_toggle_user_bot', user_id=user.id) }}"><button type="submit" class="btn btn-success"><i class="fas fa-play"></i> Enable Bot</button></form>
      {% else %}
      <form method="POST" action="{{ url_for('admin_toggle_user_bot', user_id=user.id) }}" onsubmit="return confirm('এই ইউজারের বট বন্ধ?');"><button type="submit" class="btn btn-warning"><i class="fas fa-hand-paper"></i> Disable Bot</button></form>
      {% endif %}
      {% if user.sub_status == 'expired' or not user.bot_file %}
      <form method="POST" action="{{ url_for('admin_recreate_bot', user_id=user.id) }}"><button type="submit" class="btn btn-gold"><i class="fas fa-plus-circle"></i> Recreate Bot File</button></form>
      {% endif %}
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-key"></i> Key Limit</div>
    <form method="POST" action="{{ url_for('admin_set_key_limit', agent_id=user.id) }}" class="flex">
      <label style="color:var(--gold2);font-weight:600;">Key Limit (-1 = ∞):</label>
      <input type="number" name="key_limit" value="{{ user.key_limit }}" min="-1" style="width:140px;"/>
      <button type="submit" class="btn btn-gold btn-sm"><i class="fas fa-save"></i> Save</button>
    </form>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-database"></i> DB Access</div>
    <div class="flex">
      <span>Status: {% if user.can_manage_db %}<span class="badge badge-running">ON</span>{% else %}<span class="badge badge-stopped">OFF</span>{% endif %}</span>
      <form method="POST" action="{{ url_for('admin_toggle_db_access', agent_id=user.id) }}"><button type="submit" class="btn {% if user.can_manage_db %}btn-danger{% else %}btn-success{% endif %} btn-sm"><i class="fas fa-toggle-{% if user.can_manage_db %}on{% else %}off{% endif %}"></i> Toggle</button></form>
    </div>
  </div>
  <a href="{{ url_for('admin_dashboard') }}" class="back-link"><i class="fas fa-arrow-left"></i> Back</a>
</div>
<script>
function copyT(t){navigator.clipboard.writeText(t).then(function(){alert('Copied!');}).catch(function(){var x=document.createElement('textarea');x.value=t;document.body.appendChild(x);x.select();document.execCommand('copy');x.remove();alert('Copied!');});}
</script></body></html>'''

FILE_MANAGER_HTML = '''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/><title>File Manager - MAHIR PREMIUM</title><link rel="preconnect" href="https://fonts.googleapis.com"/><link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet"/><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"/><style>''' + COMMON_CSS + '''
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:10000;justify-content:center;align-items:center;padding:16px}
.modal-overlay.active{display:flex}
.modal-box{background:#101022;border:1px solid rgba(245,200,66,.14);border-radius:18px;padding:24px;width:100%;position:relative}
.modal-box.fullscreen{max-width:96vw !important;width:96vw;height:94vh;max-height:94vh}
.modal-box.fullscreen #editContent{height:calc(94vh - 200px) !important}
.modal-close{position:absolute;top:12px;right:16px;font-size:1.7rem;color:var(--gold2);cursor:pointer;background:none;border:none}
.fs-btn{position:absolute;top:12px;right:60px;background:rgba(59,140,255,.15);border:1px solid rgba(59,140,255,.35);color:#6db2ff;width:34px;height:34px;border-radius:8px;cursor:pointer;font-size:1rem;display:flex;align-items:center;justify-content:center}
.modal-title{font-size:1.25rem;font-weight:800;color:var(--gold);margin-bottom:14px;display:flex;align-items:center;gap:10px}
</style></head><body>
<div class="container">
  <div class="card header">
    <h1><i class="fas fa-folder-open"></i> File Manager</h1>
    <div class="flex">
      <a href="{{ url_for('admin_dashboard') }}" class="btn btn-primary btn-sm"><i class="fas fa-th-large"></i> Dashboard</a>
      <a href="{{ url_for('admin_logout') }}" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>
    </div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-upload"></i> Upload Any File (ZIP auto-extracted)</div>
    <form method="POST" action="{{ url_for('admin_upload_file') }}" enctype="multipart/form-data" class="upload-form" id="uploadForm">
      <input type="file" name="uploaded_file" required style="flex:1;min-width:200px;"/>
      <button type="submit" class="btn btn-gold" id="uploadBtn"><i class="fas fa-cloud-upload-alt"></i> Upload</button>
    </form>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-folder"></i> Directory: <span class="current-dir">{{ current_path }}</span></div>
    <div class="breadcrumb"><a href="{{ url_for('admin_file_manager') }}">/</a>{% for part in breadcrumb_parts %} / <a href="{{ url_for('admin_file_manager', path=part) }}">{{ part }}</a>{% endfor %}</div>
    <div class="table-wrapper">
      <table>
        <thead><tr><th>Name</th><th>Size</th><th>Modified</th><th style="text-align:right;">Actions</th></tr></thead>
        <tbody>
          {% if parent_dir is not none %}<tr><td><a href="{{ url_for('admin_file_manager', path=parent_dir) }}" class="folder-link"><i class="fas fa-arrow-up"></i> ..</a></td><td>—</td><td>—</td><td>—</td></tr>{% endif %}
          {% for item in files %}
          <tr>
            <td>{% if item.is_dir %}<a href="{{ url_for('admin_file_manager', path=item.path) }}" class="folder-link"><i class="fas fa-folder"></i> {{ item.name }}</a>{% else %}<span class="file-name"><i class="fas fa-file"></i> {{ item.name }}</span>{% endif %}</td>
            <td>{{ item.size if not item.is_dir else '—' }}</td>
            <td>{{ item.modified }}</td>
            <td><div class="td-actions">{% if not item.is_dir %}<a href="{{ url_for('admin_download_file', path=item.path) }}" class="btn btn-info btn-sm"><i class="fas fa-download"></i></a><button onclick="editFile('{{ item.path }}')" class="btn btn-warning btn-sm"><i class="fas fa-edit"></i></button><button onclick="deleteFile('{{ item.path }}', this)" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button>{% endif %}</div></td>
          </tr>
          {% else %}<tr class="empty-row"><td colspan="4">Empty</td></tr>{% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  <a href="{{ url_for('admin_dashboard') }}" class="back-link"><i class="fas fa-arrow-left"></i> Back</a>
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
function editFile(path){currentEditPath=path;document.getElementById('editFileName').textContent=path;document.getElementById('editContent').value='Loading...';document.getElementById('editStatus').textContent='';document.getElementById('editModal').classList.add('active');fetch('/admin/edit_file/'+encodeURIComponent(path)).then(function(r){return r.json();}).then(function(data){document.getElementById('editContent').value=data.error?('Error: '+data.error):data.content;});}
function closeEditModal(){document.getElementById('editModal').classList.remove('active');var box=document.getElementById('editModalBox');if(box)box.classList.remove('fullscreen');var fs=document.getElementById('fsBtn');if(fs)fs.innerHTML='<i class="fas fa-expand"></i>';}
function toggleFullscreen(){var box=document.getElementById('editModalBox');var btn=document.getElementById('fsBtn');box.classList.toggle('fullscreen');btn.innerHTML=box.classList.contains('fullscreen')?'<i class="fas fa-compress"></i>':'<i class="fas fa-expand"></i>';}
function saveEdit(){var content=document.getElementById('editContent').value;var status=document.getElementById('editStatus');status.textContent='Saving...';fetch('/admin/edit_file/'+encodeURIComponent(currentEditPath),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:content})}).then(function(r){return r.json();}).then(function(data){if(data.success){status.textContent='Saved!';status.style.color='#4ade80';setTimeout(function(){location.reload();},800);}else{status.textContent='Error: '+(data.error||'');status.style.color='#ff5a76';}});}
function deleteFile(path,btn){if(!confirm('Delete "'+path+'"?'))return;if(btn)btn.disabled=true;fetch('/admin/delete_file/'+encodeURIComponent(path),{method:'POST'}).then(function(r){return r.json();}).then(function(data){if(data.success){location.reload();}else{alert('Error: '+(data.error||''));if(btn)btn.disabled=false;}});}
document.addEventListener('keydown',function(e){if(e.key==='Escape'){var box=document.getElementById('editModalBox');if(box && box.classList.contains('fullscreen')){toggleFullscreen();return;}closeEditModal();}});
document.getElementById('editModal').addEventListener('click',function(e){if(e.target===this)closeEditModal();});
</script></body></html>'''

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
.notification{position:fixed;top:20px;right:20px;padding:12px 18px;border-radius:12px;z-index:99999;font-weight:600;font-size:.85rem;box-shadow:0 10px 30px rgba(0,0,0,.5)}
.notification-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}
.notification-error{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}
.notification-info{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}
@media(max-width:768px){.download-card{flex-direction:column}.button-group .btn{flex:1 1 45%}.chart-container{height:190px}}
</style></head><body>
{% if blocked %}
<div class="notice-overlay show" id="blockOverlay">
  <div class="notice-box">
    <span class="notice-icon">{% if block_type == 'global' %}🛠️{% elif block_type == 'expired' %}⏰{% else %}🚫{% endif %}</span>
    <div class="notice-title">{% if block_type == 'global' %}SYSTEM MAINTENANCE{% elif block_type == 'expired' %}SUBSCRIPTION EXPIRED{% else %}BOT DISABLED{% endif %}</div>
    <div class="notice-msg">{{ block_message | safe }}</div>
    <div class="notice-contact-title">Contact Admin</div>
    <div class="notice-buttons">
      <a href="https://t.me/mahirtcpchat" target="_blank" class="notice-btn notice-btn-tg"><i class="fab fa-telegram"></i> Telegram</a>
      <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="notice-btn notice-btn-tt"><i class="fab fa-tiktok"></i> TikTok</a>
      <a href="https://MAHIR.XO.JE/" target="_blank" class="notice-btn notice-btn-web"><i class="fas fa-globe"></i> Website</a>
    </div>
  </div>
</div>
{% endif %}
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
    <div style="color:var(--red2);font-weight:700;"><i class="fas fa-exclamation-triangle"></i> আপনার সাবস্ক্রিপশন মাত্র {{ sub_days }} দিনের মধ্যে শেষ হয়ে যাবে! এখনই renew করুন।</div>
  </div>
  {% endif %}
  {% if not config_done %}
  <div class="card">
    <div class="card-title"><i class="fas fa-cog"></i> Bot Configuration</div>
    {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == 'error' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}
    <div class="config-status"><i class="fas fa-info-circle"></i> Enter your Free Fire bot credentials to deploy.</div>
    <form method="POST" action="{{ url_for('configure_bot') }}" class="config-form" id="configForm">
      <div><label><i class="fas fa-user-shield"></i> Admin UID</label><input type="text" name="admin_uid" placeholder="e.g., 1120167200" required/></div>
      <div><label><i class="fas fa-robot"></i> Bot UID</label><input type="text" name="bot_uid" placeholder="Enter bot UID" required/></div>
      <div><label><i class="fas fa-key"></i> Bot Password</label><input type="text" name="bot_pw" placeholder="Enter bot password hash" required/></div>
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
    <div class="card-title"><i class="fas fa-robot"></i> Bot Identity &amp; Status</div>
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-label"><i class="fas fa-id-card"></i> UID</div><div class="stat-value" id="botUid">---</div></div>
      <div class="stat-card"><div class="stat-label"><i class="fas fa-user-astronaut"></i> Name</div><div class="stat-value" id="botName">---</div></div>
      <div class="stat-card"><div class="stat-label"><i class="fas fa-globe-asia"></i> Region</div><div class="stat-value" id="botRegion">---</div></div>
      <div class="stat-card"><div class="stat-label"><i class="fas fa-heartbeat"></i> Status</div><div class="stat-value" id="botStatus">---</div></div>
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
    </div>
  </div>
  {% endif %}
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
function getBtn(id){return document.getElementById(id);}
function setLoading(btn,loading){if(!btn)return;if(loading){btn._origHtml=btn.innerHTML;btn.disabled=true;btn.innerHTML='<span class="spinner"></span> Loading...';}else{btn.disabled=false;btn.innerHTML=btn._origHtml||btn.innerHTML;}}
function showNotification(message,type){var el=document.createElement('div');el.className='notification notification-'+type;var icon=type==='success'?'check-circle':(type==='error'?'exclamation-circle':'info-circle');el.innerHTML='<i class="fas fa-'+icon+'"></i> '+message;document.body.appendChild(el);setTimeout(function(){el.remove();},3000);}
function escapeHtml(text){if(!text)return '';var d=document.createElement('div');d.textContent=text;return d.innerHTML;}
var performanceChart=null;
function initChart(){var ctx=document.getElementById('performanceChart');if(!ctx)return;performanceChart=new Chart(ctx.getContext('2d'),{type:'line',data:{labels:Array(20).fill(''),datasets:[{label:'CPU %',data:Array(20).fill(0),borderColor:'#F5C842',backgroundColor:'rgba(245,200,66,.05)',tension:.4,fill:true,borderWidth:2,pointRadius:0},{label:'RAM %',data:Array(20).fill(0),borderColor:'#8540F5',backgroundColor:'rgba(133,64,245,.04)',tension:.4,fill:true,borderWidth:2,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{labels:{color:'#c5c5e5'}}},scales:{y:{beginAtZero:true,max:100,grid:{color:'rgba(245,200,66,.05)'},ticks:{color:'#a78bfa'}},x:{grid:{color:'rgba(245,200,66,.05)'},ticks:{color:'#a78bfa'}}}}});}
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
function sendAction(action){var btnMap={start:'btnStart',stop:'btnStop',reset:'btnReset'};var btn=getBtn(btnMap[action]);setLoading(btn,true);fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:action})}).then(function(r){return r.json();}).then(function(d){setLoading(btn,false);if(d.error){showNotification(d.error,'error');}else{showNotification(action.toUpperCase()+' executed!','success');setTimeout(updateUI,500);}}).catch(function(){setLoading(btn,false);showNotification('Failed!','error');});}
function updateUI(){fetch('/api/status').then(function(r){return r.json();}).then(function(data){if(data.error)return;var s=function(id,v){var el=document.getElementById(id);if(el)el.innerHTML=v;};
s('botUid',escapeHtml(data.bot_uid)||'---');s('botName',escapeHtml(data.bot_name)||'---');s('botRegion',escapeHtml(data.bot_region)||'---');s('botStatus',data.bot_status||'Offline');
s('processStatus',data.is_running?'<span class="badge badge-active">RUNNING</span>':'<span class="badge badge-offline">STOPPED</span>');
s('uptime',data.uptime||'00:00:00');s('restartCount',data.restart_count||0);s('errorCount',(data.error_logs||[]).length);
var cpu=parseFloat(data.cpu)||0,ram=parseFloat(data.ram)||0,disk=parseFloat(data.disk)||0;
s('cpuValue',Math.floor(cpu)+'%');s('ramValue',Math.floor(ram)+'%');s('diskValue',Math.floor(disk)+'%');
['cpuBar','ramBar','diskBar'].forEach(function(id,i){var bar=document.getElementById(id);if(bar)bar.style.width=[cpu,ram,disk][i]+'%';});
if(data.logs){var h=data.logs.slice(-200).map(function(l){return '<div class="log-line">'+escapeHtml(l)+'</div>';}).join('');var box=document.getElementById('logBox');if(box){box.innerHTML=h;if(autoScroll&&currentTab==='logs')box.scrollTop=box.scrollHeight;}}
if(data.error_logs){var e=data.error_logs.slice(-100).map(function(l){return '<div class="log-line error-line">'+escapeHtml(l)+'</div>';}).join('');var eb=document.getElementById('errorBox');if(eb)eb.innerHTML=e;}
if(data.message_history){var m=data.message_history.slice().reverse().map(function(msg){return '<div class="message-card"><div class="message-header"><span class="message-sender">'+escapeHtml(msg.data.nickname)+' (UID: '+escapeHtml(msg.data.sender_uid)+')</span><span class="message-time">'+escapeHtml(msg.timestamp)+'</span></div><div class="message-meta"><span class="message-label">Message:</span><span class="message-value">'+escapeHtml(msg.data.message)+'</span><span class="message-label">Guild:</span><span class="message-value">'+escapeHtml(msg.data.guild_name)+'</span></div></div>';}).join('');var mb=document.getElementById('messageHistory');if(mb)mb.innerHTML=m;}
if(performanceChart&&data.cpu_history){performanceChart.data.datasets[0].data=data.cpu_history;performanceChart.data.datasets[1].data=data.ram_history;performanceChart.update('none');}
});}
setInterval(updateUI,1500);updateUI();
</script></body></html>'''

# =============================================================================
# Flask Routes
# =============================================================================

@app.route('/')
def index():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
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
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['is_admin'] = bool(user[3])
            session['is_agent'] = bool(user[4])
            if session['is_admin']:
                return redirect(url_for('admin_dashboard'))
            elif session['is_agent']:
                return redirect(url_for('agent_dashboard'))
            else:
                return redirect(url_for('user_dashboard'))
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
        c.execute('SELECT id, expiry_date FROM keys WHERE key=? AND is_used=0', (reg_key,))
        key_row = c.fetchone()
        if not key_row:
            conn.close()
            flash('Invalid or already used key', 'error')
            return render_template_string(REGISTER_HTML)
        if key_row[1]:
            try:
                expiry = datetime.fromisoformat(key_row[1])
                if expiry < datetime.now():
                    conn.close()
                    flash('Key expired', 'error')
                    return render_template_string(REGISTER_HTML)
            except: pass
        c.execute('SELECT id FROM users WHERE username=?', (username,))
        if c.fetchone():
            conn.close()
            flash('Username taken', 'error')
            return render_template_string(REGISTER_HTML)
        c.execute('''INSERT INTO users (username, password, email, registration_key, is_admin, is_agent, subscription_expiry)
                     VALUES (?, ?, ?, ?, 0, 0, ?)''', (username, password, email, reg_key, key_row[1]))
        c.execute('UPDATE keys SET is_used=1, used_by=?, used_at=CURRENT_TIMESTAMP WHERE key=?', (username, reg_key))
        conn.commit()
        conn.close()
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
                conn.commit()
                conn.close()
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

# ------ Agent Routes ------
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
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['is_agent'] = True
            session['is_admin'] = False
            return redirect(url_for('agent_dashboard'))
        flash('Invalid agent credentials', 'error')
    return render_template_string(AGENT_LOGIN_HTML)


@app.route('/agent/dashboard')
@agent_required
def agent_dashboard():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id, key, created_at, used_by, is_used, expiry_date FROM keys WHERE created_by=? ORDER BY id DESC', (session['username'],))
    keys = [{'id': r[0], 'key': r[1], 'created_at': r[2], 'used_by': r[3], 'is_used': r[4], 'expiry_date': r[5]} for r in c.fetchall()]
    c.execute('SELECT key_limit, can_manage_db FROM users WHERE id=?', (session['user_id'],))
    row = c.fetchone()
    key_limit = row[0] if row and row[0] is not None else -1
    can_manage_db = bool(row[1]) if row else False
    conn.close()
    return render_template_string(AGENT_DASHBOARD_HTML, keys=keys, new_key=None, key_limit=key_limit, key_count=len(keys), can_manage_db=can_manage_db)


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
    current_count = c.fetchone()[0]
    if key_limit >= 0 and current_count >= key_limit:
        conn.close()
        flash(f'❌ Max {key_limit} keys reached.', 'error')
        return redirect(url_for('agent_dashboard'))
    key = secrets.token_hex(16).upper()
    expiry = None if days == 0 else datetime.now() + timedelta(days=days)
    c.execute('INSERT INTO keys (key, created_by, expiry_date) VALUES (?, ?, ?)', (key, session['username'], expiry.isoformat() if expiry else None))
    conn.commit()
    c.execute('SELECT id, key, created_at, used_by, is_used, expiry_date FROM keys WHERE created_by=? ORDER BY id DESC', (session['username'],))
    keys = [{'id': r[0], 'key': r[1], 'created_at': r[2], 'used_by': r[3], 'is_used': r[4], 'expiry_date': r[5]} for r in c.fetchall()]
    c.execute('SELECT can_manage_db FROM users WHERE id=?', (session['user_id'],))
    row2 = c.fetchone()
    can_manage_db = bool(row2[0]) if row2 else False
    conn.close()
    return render_template_string(AGENT_DASHBOARD_HTML, keys=keys, new_key=key, days=days, key_limit=key_limit, key_count=len(keys), can_manage_db=can_manage_db)


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
        flash('❌ No DB permission', 'error')
        return redirect(url_for('agent_dashboard'))
    if os.path.exists(DB_FILE):
        return send_file(DB_FILE, as_attachment=True)
    flash('DB not found', 'error')
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
        flash('❌ No DB permission', 'error')
        return redirect(url_for('agent_dashboard'))
    if 'db_file' not in request.files:
        flash('No file', 'error')
        return redirect(url_for('agent_dashboard'))
    file = request.files['db_file']
    if not file.filename.lower().endswith('.db'):
        flash('Only .db files', 'error')
        return redirect(url_for('agent_dashboard'))
    temp_path = DB_FILE + ".uploading"
    try:
        file.save(temp_path)
    except Exception as e:
        flash(f'Save error: {e}', 'error')
        return redirect(url_for('agent_dashboard'))
    is_valid, error_msg = validate_db_file(temp_path)
    if not is_valid:
        try: os.remove(temp_path)
        except: pass
        flash(f'❌ Invalid DB: {error_msg}', 'error')
        return redirect(url_for('agent_dashboard'))
    with monitors_lock:
        for uid, monitor in list(monitors.items()):
            try: monitor.stop_process()
            except: pass
        monitors.clear()
    try:
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, DB_FILE + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    except: pass
    try:
        if os.path.exists(DB_FILE): os.remove(DB_FILE)
        shutil.move(temp_path, DB_FILE)
        init_db(); migrate_db()
        flash('✅ DB uploaded!', 'success')
    except Exception as e:
        flash(f'❌ Error: {e}', 'error')
    return redirect(url_for('agent_dashboard'))

# ------ Admin Routes ------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'MAHIR TCP' and password == 'MAHIR0208@':
            session['user_id'] = 0
            session['username'] = 'admin'
            session['is_admin'] = True
            session['is_agent'] = False
            return redirect(url_for('admin_dashboard'))
        flash('Invalid admin credentials', 'error')
    return render_template_string(ADMIN_LOGIN_HTML)


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, username, email, created_at, bot_status, bot_file, 
                 is_admin, is_agent, key_limit, can_manage_db, password,
                 bot_disabled_by_admin, subscription_expiry, registration_key
                 FROM users ORDER BY id DESC''')
    rows = c.fetchall()
    users = []
    agents = []
    for r in rows:
        sub = check_subscription_status(r[0])
        user_dict = {
            'id': r[0], 'username': r[1], 'email': r[2],
            'created_at': r[3], 'bot_status': r[4], 'bot_file': r[5],
            'is_admin': r[6], 'is_agent': r[7],
            'key_limit': r[8] if r[8] is not None else -1,
            'can_manage_db': r[9] or 0, 'password': r[10],
            'bot_disabled_by_admin': r[11] or 0,
            'sub_status': sub['status'], 'sub_days': sub['days_left']
        }
        users.append(user_dict)
        if r[7] == 1:
            c2 = conn.cursor()
            c2.execute('SELECT COUNT(*) FROM keys WHERE created_by=?', (r[1],))
            key_count = c2.fetchone()[0]
            c2.close()
            agent = user_dict.copy()
            agent['key_count'] = key_count
            agents.append(agent)
    c.execute('SELECT id, key, created_by, created_at, used_by, is_used, expiry_date FROM keys ORDER BY id DESC LIMIT 30')
    keys = [{'id': r[0], 'key': r[1], 'created_by': r[2], 'created_at': r[3], 'used_by': r[4], 'is_used': r[5], 'expiry_date': r[6]} for r in c.fetchall()]
    conn.close()
    stats = {'cpu': 0, 'ram_percent': 0, 'ram_used': 0, 'ram_total': 0, 'disk_percent': 0, 'disk_used': 0, 'disk_total': 0}
    try:
        stats['cpu'] = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        stats['ram_percent'] = mem.percent; stats['ram_used'] = mem.used; stats['ram_total'] = mem.total
        disk = psutil.disk_usage('/')
        stats['disk_percent'] = disk.percent; stats['disk_used'] = disk.used; stats['disk_total'] = disk.total
    except: pass
    return render_template_string(
        ADMIN_DASHBOARD_HTML,
        users=users, agents=agents, keys=keys, new_key=None,
        global_stop=get_global_stop(),
        global_notice=get_global_notice(),
        **stats
    )


@app.route('/admin/set_global_notice', methods=['POST'])
@admin_required
def admin_set_global_notice():
    notice = request.form.get('notice_text', '').strip()
    set_setting('global_notice_text', notice)
    flash('✅ Global notice updated!', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/stop_all_bots', methods=['POST'])
@admin_required
def admin_stop_all_bots():
    set_setting('global_bot_stop', '1')
    with monitors_lock:
        for uid, monitor in list(monitors.items()):
            try: monitor.stop_process()
            except: pass
    flash('🛑 All bots STOPPED globally. Users cannot start.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/start_all_bots', methods=['POST'])
@admin_required
def admin_start_all_bots():
    set_setting('global_bot_stop', '0')
    success, fail = reset_all_bots()
    flash(f'✅ Global stop OFF. {success} bots restarted.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/toggle_user_bot/<int:user_id>', methods=['POST'])
@admin_required
def admin_toggle_user_bot(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT bot_disabled_by_admin FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    if row:
        new_val = 0 if row[0] else 1
        c.execute('UPDATE users SET bot_disabled_by_admin=? WHERE id=?', (new_val, user_id))
        conn.commit()
        if new_val:
            with monitors_lock:
                if user_id in monitors:
                    try: monitors[user_id].stop_process()
                    except: pass
            flash('🛑 User bot DISABLED.', 'success')
        else:
            flash('✅ User bot ENABLED.', 'success')
            with monitors_lock:
                if user_id in monitors:
                    try: monitors[user_id].start_process()
                    except: pass
    conn.close()
    return redirect(request.referrer or url_for('admin_dashboard'))


@app.route('/admin/recreate_bot/<int:user_id>', methods=['POST'])
@admin_required
def admin_recreate_bot(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT username, admin_uid, bot_uid, bot_pw FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        flash('User not found', 'error')
        return redirect(url_for('admin_dashboard'))
    username, admin_uid, bot_uid, bot_pw = row
    if not bot_uid or not bot_pw:
        flash('Bot credentials missing', 'error')
        return redirect(url_for('admin_dashboard'))
    safe_name = sanitize_filename(username)
    bot_filename = f"{safe_name}_mahir.py"
    bot_file_path = os.path.join(USER_BOTS_DIR, bot_filename)
    try:
        shutil.copy2(MAHIR_SOURCE, bot_file_path)
        admin_uids_list = parse_admin_uids(admin_uid)
        ok, msg = inject_credentials_into_bot_file(bot_file_path, bot_uid, bot_pw, admin_uids_list)
        if not ok:
            flash(f'Error: {msg}', 'error')
            return redirect(url_for('admin_dashboard'))
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # Extend subscription by 30 days from now
        new_expiry = (datetime.now() + timedelta(days=30)).isoformat()
        c.execute('UPDATE users SET bot_file=?, bot_status="configured", bot_disabled_by_admin=0, subscription_expiry=? WHERE id=?',
                  (bot_filename, new_expiry, user_id))
        conn.commit()
        conn.close()
        monitor = ProcessMonitor(user_id, bot_file_path)
        with monitors_lock:
            monitors[user_id] = monitor
        monitor.start_process()
        flash(f'✅ Bot recreated with 30-day subscription!', 'success')
    except Exception as e:
        flash(f'❌ Error: {e}', 'error')
    return redirect(url_for('admin_user_details', user_id=user_id))


@app.route('/admin/create_agent', methods=['POST'])
@admin_required
def admin_create_agent():
    username = request.form['username']
    email = request.form['email']
    password = request.form['password']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id FROM users WHERE username=?', (username,))
    if c.fetchone():
        flash('Username exists', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))
    c.execute('''INSERT INTO users (username, password, email, registration_key, is_agent, is_admin, bot_status, key_limit, can_manage_db)
                 VALUES (?, ?, ?, ?, 1, 0, 'agent', -1, 0)''', (username, password, email, 'agent_created'))
    conn.commit()
    conn.close()
    flash(f'Agent {username} created', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_agent/<int:agent_id>', methods=['POST'])
@admin_required
def admin_delete_agent(agent_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT username, bot_file FROM users WHERE id=? AND is_agent=1', (agent_id,))
    row = c.fetchone()
    if row:
        username, bot_file = row
        c.execute('DELETE FROM keys WHERE created_by=?', (username,))
        if bot_file and os.path.exists(bot_file):
            try: os.remove(bot_file)
            except: pass
        c.execute('DELETE FROM users WHERE id=?', (agent_id,))
        conn.commit()
    conn.close()
    flash('Agent deleted', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_key/<int:key_id>', methods=['POST'])
@admin_required
def admin_delete_key(key_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT key FROM keys WHERE id=?', (key_id,))
    key_row = c.fetchone()
    if key_row:
        reg_key = key_row[0]
        c.execute('SELECT id, bot_file FROM users WHERE registration_key=?', (reg_key,))
        users_to_remove = c.fetchall()
        conn.commit(); conn.close()
        for user_id, bot_file in users_to_remove:
            with monitors_lock:
                if user_id in monitors:
                    try: monitors[user_id].stop_process()
                    except: pass
                    del monitors[user_id]
            if bot_file:
                for ext in ['', '_login.py']:
                    p = bot_file.replace('.py', ext) if ext else bot_file
                    fp = os.path.join(USER_BOTS_DIR, p)
                    if os.path.exists(fp):
                        try: os.remove(fp)
                        except: pass
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        for user_id, _ in users_to_remove:
            c.execute('DELETE FROM users WHERE id=?', (user_id,))
        c.execute('DELETE FROM keys WHERE id=?', (key_id,))
        conn.commit()
        conn.close()
        flash('Key and users deleted', 'success')
    else:
        conn.close()
        flash('Key not found', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/create_key', methods=['POST'])
@admin_required
def admin_create_key():
    days = int(request.form.get('days_valid', 30))
    key = secrets.token_hex(16).upper()
    expiry = None if days == 0 else datetime.now() + timedelta(days=days)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO keys (key, created_by, expiry_date) VALUES (?, ?, ?)',
              (key, session['username'], expiry.isoformat() if expiry else None))
    conn.commit()
    conn.close()
    flash(f'🔑 Key: {key}', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
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
    conn.commit()
    conn.close()
    with monitors_lock:
        if user_id in monitors:
            try: monitors[user_id].stop_process()
            except: pass
            del monitors[user_id]
    flash('User deleted', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

# ------ User Dashboard ------
@app.route('/dashboard')
@login_required
def user_dashboard():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    if session.get('is_agent'):
        return redirect(url_for('agent_dashboard'))
    
    user_id = session['user_id']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT admin_uid, bot_uid, bot_pw, bot_status, bot_disabled_by_admin FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    
    config_done = row and row[3] not in ['not_configured', 'agent']
    sub = check_subscription_status(user_id)
    
    # Determine blocking reason
    blocked = False
    block_type = None
    block_message = ''
    
    if sub['status'] == 'expired':
        blocked = True
        block_type = 'expired'
        block_message = (
            f'আপনার মেয়াদ শেষ হয়ে গিয়েছে!<br><br>'
            f'<strong style="color:#F5C842;">পুনরায় প্যাকেজ কিনতে যোগাযোগ করুন:</strong>'
        )
    elif get_global_stop():
        blocked = True
        block_type = 'global'
        block_message = get_global_notice()
    elif row and row[4]:  # bot_disabled_by_admin
        blocked = True
        block_type = 'admin_disabled'
        block_message = 'Admin আপনার বট কিছু কাজের জন্য বন্ধ করে দিয়েছে। আপনি admin এর সাথে যোগাযোগ করুন।'
    
    return render_template_string(
        USER_PANEL_HTML,
        config_done=config_done,
        blocked=blocked,
        block_type=block_type,
        block_message=block_message,
        sub_status=sub['status'],
        sub_days=sub['days_left']
    )


@app.route('/configure', methods=['POST'])
@login_required
def configure_bot():
    user_id = session['user_id']
    
    # Check subscription
    sub = check_subscription_status(user_id)
    if sub['status'] == 'expired':
        flash('❌ আপনার মেয়াদ শেষ হয়ে গিয়েছে!', 'error')
        return redirect(url_for('user_dashboard'))
    
    if get_global_stop():
        flash('⛔ System maintenance চলছে। পরে চেষ্টা করুন।', 'error')
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
    success, msg = inject_credentials_into_bot_file(bot_file_path, bot_uid, bot_pw, admin_uids_list)
    if not success:
        flash(f'Error: {msg}', 'error')
        return redirect(url_for('user_dashboard'))
    
    admin_uid_db_string = ', '.join(admin_uids_list)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''UPDATE users SET admin_uid=?, bot_uid=?, bot_pw=?, bot_file=?, bot_status='configured', bot_disabled_by_admin=0 
                 WHERE id=?''', (admin_uid_db_string, bot_uid, bot_pw, bot_filename, user_id))
    conn.commit()
    conn.close()
    
    with monitors_lock:
        if user_id in monitors:
            try: monitors[user_id].stop_process()
            except: pass
            del monitors[user_id]
    
    monitor = ProcessMonitor(user_id, bot_file_path)
    with monitors_lock:
        monitors[user_id] = monitor
    monitor.start_process()
    
    def update_bio_bg():
        time.sleep(3)
        try: update_bot_bio(bot_uid, bot_pw, username)
        except: pass
    threading.Thread(target=update_bio_bg, daemon=True).start()
    
    flash('✅ Bot deployed!', 'success')
    return redirect(url_for('user_dashboard'))

# ------ Admin File Manager ------
@app.route('/admin/files', defaults={'path': ''})
@app.route('/admin/files/<path:path>')
@admin_required
def admin_file_manager(path):
    if '..' in path or path.startswith('/'):
        flash('Invalid path', 'error')
        return redirect(url_for('admin_dashboard'))
    current_dir = os.path.join(os.getcwd(), path) if path else os.getcwd()
    if not os.path.exists(current_dir) or not os.path.isdir(current_dir):
        flash('Not found', 'error')
        return redirect(url_for('admin_dashboard'))
    parent_dir = None
    if path:
        parent = os.path.dirname(path)
        parent_dir = parent if parent else ''
    items = []
    try:
        for item in os.listdir(current_dir):
            item_path = os.path.join(path, item) if path else item
            full_path = os.path.join(current_dir, item)
            is_dir = os.path.isdir(full_path)
            size = ''
            if not is_dir:
                try:
                    sb = os.path.getsize(full_path)
                    size = f"{sb} B" if sb < 1024 else (f"{sb/1024:.1f} KB" if sb < 1024*1024 else f"{sb/(1024*1024):.1f} MB")
                except: size = '?'
            modified = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime('%Y-%m-%d %H:%M')
            items.append({'name': item, 'path': item_path, 'is_dir': is_dir, 'size': size, 'modified': modified})
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    except Exception as e:
        flash(f'Error: {e}', 'error')
    breadcrumb_parts = path.split('/') if path else []
    return render_template_string(FILE_MANAGER_HTML, current_path=path or '/', breadcrumb_parts=breadcrumb_parts, parent_dir=parent_dir, files=items)


@app.route('/admin/edit_file/<path:path>', methods=['GET', 'POST'])
@admin_required
def admin_edit_file(path):
    if '..' in path or path.startswith('/'):
        return jsonify({'error': 'Invalid path'}), 400
    full_path = os.path.join(os.getcwd(), path)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return jsonify({'error': 'Not found'}), 404
    if request.method == 'GET':
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                return jsonify({'content': f.read()})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(request.json.get('content', ''))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/delete_file/<path:path>', methods=['POST'])
@admin_required
def admin_delete_file(path):
    if '..' in path or path.startswith('/'):
        return jsonify({'error': 'Invalid path'}), 400
    full_path = os.path.join(os.getcwd(), path)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return jsonify({'error': 'Not found'}), 404
    try:
        os.remove(full_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/download/<path:path>')
@admin_required
def admin_download_file(path):
    if '..' in path or path.startswith('/'):
        flash('Invalid', 'error')
        return redirect(url_for('admin_dashboard'))
    full_path = os.path.join(os.getcwd(), path)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        flash('Not found', 'error')
        return redirect(url_for('admin_dashboard'))
    return send_file(full_path, as_attachment=True)


@app.route('/admin/upload_file', methods=['POST'])
@admin_required
def admin_upload_file():
    if 'uploaded_file' not in request.files:
        flash('No file', 'error')
        return redirect(url_for('admin_file_manager'))
    file = request.files['uploaded_file']
    if file.filename == '':
        flash('No file', 'error')
        return redirect(url_for('admin_file_manager'))
    filename = file.filename
    file_path = os.path.join(os.getcwd(), filename)
    try:
        file.save(file_path)
        if filename.lower().endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as z:
                z.extractall(os.getcwd())
            os.remove(file_path)
            flash(f'✅ ZIP extracted!', 'success')
        else:
            flash(f'✅ Uploaded!', 'success')
    except zipfile.BadZipFile:
        flash(f'❌ Bad ZIP', 'error')
    except Exception as e:
        flash(f'❌ {e}', 'error')
    return redirect(url_for('admin_file_manager'))


@app.route('/admin/upload_mahir', methods=['POST'])
@admin_required
def admin_upload_mahir():
    if 'mahir_file' not in request.files:
        flash('No file', 'error')
        return redirect(url_for('admin_dashboard'))
    file = request.files['mahir_file']
    if file.filename == '':
        flash('No file', 'error')
        return redirect(url_for('admin_dashboard'))
    if file.filename.lower() != 'mahir.py':
        flash(f'❌ শুধু "mahir.py" নামের ফাইল! আপনার: {file.filename}', 'error')
        return redirect(url_for('admin_dashboard'))
    file.save(MAHIR_SOURCE)
    success_count, fail_count = reset_all_bots()
    if fail_count == 0:
        flash(f'✅ Uploaded! {success_count} bots restarted.', 'success')
    else:
        flash(f'⚠️ {success_count} OK, {fail_count} failed.', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/reset_all_bots', methods=['POST'])
@admin_required
def admin_reset_all_bots():
    success, fail = reset_all_bots()
    if fail == 0:
        flash(f'🔄 All {success} bots reset!', 'success')
    else:
        flash(f'⚠️ {success} OK, {fail} failed.', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/user_details/<int:user_id>')
@admin_required
def admin_user_details(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, username, email, password, bot_uid, bot_pw, bot_file, 
                 bot_status, bot_pid, admin_uid, is_admin, is_agent, key_limit, 
                 can_manage_db, created_at, registration_key, bot_disabled_by_admin, subscription_expiry
                 FROM users WHERE id=?''', (user_id,))
    user = c.fetchone()
    if not user:
        conn.close()
        flash('Not found', 'error')
        return redirect(url_for('admin_dashboard'))
    
    c.execute('SELECT COUNT(*) FROM keys WHERE created_by=?', (user[1],))
    key_count = c.fetchone()[0]
    c.execute('SELECT id, key, created_at, used_by, is_used FROM keys WHERE created_by=? ORDER BY id DESC', (user[1],))
    keys_list = [{'id': r[0], 'key': r[1], 'created_at': r[2], 'used_by': r[3], 'is_used': r[4]} for r in c.fetchall()]
    conn.close()
    
    sub = check_subscription_status(user_id)
    
    user_data = {
        'id': user[0], 'username': user[1], 'email': user[2] or '—',
        'password': user[3], 'bot_uid': user[4] or '—', 'bot_pw': user[5] or '—',
        'bot_file': user[6] or '—', 'bot_status': user[7] or 'unknown',
        'bot_pid': user[8], 'admin_uid': user[9] or '—',
        'is_admin': user[10], 'is_agent': user[11],
        'key_limit': user[12] if user[12] is not None else -1,
        'can_manage_db': user[13] or 0,
        'created_at': user[14], 'registration_key': user[15],
        'key_count': key_count,
        'bot_disabled_by_admin': user[16] or 0,
        'sub_status': sub['status'], 'sub_days': sub['days_left'],
        'sub_expiry': sub['expiry'].strftime('%Y-%m-%d %H:%M') if sub['expiry'] else None
    }
    
    live = None
    with monitors_lock:
        if user_id in monitors:
            try: live = monitors[user_id].get_status()
            except: pass
    
    return render_template_string(USER_DETAILS_HTML, user=user_data, live=live, keys=keys_list)


@app.route('/admin/set_key_limit/<int:agent_id>', methods=['POST'])
@admin_required
def admin_set_key_limit(agent_id):
    try: new_limit = int(request.form.get('key_limit', -1))
    except: new_limit = -1
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET key_limit=? WHERE id=?', (new_limit, agent_id))
    conn.commit()
    conn.close()
    flash(f'✅ Key limit set to {new_limit if new_limit >= 0 else "Unlimited"}.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))


@app.route('/admin/toggle_db_access/<int:agent_id>', methods=['POST'])
@admin_required
def admin_toggle_db_access(agent_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT can_manage_db FROM users WHERE id=?', (agent_id,))
    row = c.fetchone()
    if row:
        new_val = 0 if row[0] else 1
        c.execute('UPDATE users SET can_manage_db=? WHERE id=?', (new_val, agent_id))
        conn.commit()
        flash(f'DB access {"ON" if new_val else "OFF"}.', 'success')
    conn.close()
    return redirect(request.referrer or url_for('admin_dashboard'))


def reset_all_bots():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, admin_uid, bot_uid, bot_pw, bot_file 
                 FROM users WHERE bot_file IS NOT NULL AND bot_uid IS NOT NULL''')
    users = c.fetchall()
    conn.close()
    
    with monitors_lock:
        for uid, m in list(monitors.items()):
            try: m.stop_process()
            except: pass
        monitors.clear()
    
    success = 0; fail = 0
    for user_id, admin_uid, bot_uid, bot_pw, bot_file in users:
        if not bot_file: continue
        bot_file_path = os.path.join(USER_BOTS_DIR, bot_file)
        try:
            if os.path.exists(MAHIR_SOURCE):
                shutil.copy2(MAHIR_SOURCE, bot_file_path)
            admin_uids_list = parse_admin_uids(admin_uid)
            ok, msg = inject_credentials_into_bot_file(bot_file_path, bot_uid, bot_pw, admin_uids_list)
            if not ok:
                fail += 1; continue
            monitor = ProcessMonitor(user_id, bot_file_path)
            with monitors_lock:
                monitors[user_id] = monitor
            monitor.start_process()
            success += 1
        except Exception as e:
            print(f"❌ Reset {user_id}: {e}")
            fail += 1
    return success, fail

# ========== API Routes ==========

@app.route('/api/status')
@login_required
def api_status():
    monitor = get_monitor(session['user_id'])
    if monitor:
        status_data = monitor.get_status()
        sub = check_subscription_status(session['user_id'])
        if sub['status'] == 'unlimited':
            status_data['script_remaining'] = 'Lifetime / No Limit'
        elif sub['status'] == 'expired':
            status_data['script_remaining'] = 'Expired'
        else:
            exp = sub['expiry']
            status_data['script_remaining'] = f"{exp.strftime('%d %b, %Y')} ({sub['days_left']}d left)"
        return jsonify(status_data)
    return jsonify({'error': 'Not configured'}), 400


@app.route('/api/control', methods=['POST'])
@login_required
def api_control():
    # Check block
    if get_global_stop():
        return jsonify({'error': '⛔ Admin global stop চলছে!'}), 403
    user_id = session['user_id']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT bot_disabled_by_admin FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return jsonify({'error': '⛔ Admin আপনার বট বন্ধ করে দিয়েছে!'}), 403
    sub = check_subscription_status(user_id)
    if sub['status'] == 'expired':
        return jsonify({'error': '⛔ আপনার মেয়াদ শেষ!'}), 403
    
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
@login_required
def api_clear_errors():
    monitor = get_monitor(session['user_id'])
    if monitor: monitor.clear_errors()
    return jsonify({'status': 'ok'})


@app.route('/api/clear_messages', methods=['POST'])
@login_required
def api_clear_messages():
    monitor = get_monitor(session['user_id'])
    if monitor: monitor.clear_messages()
    return jsonify({'status': 'ok'})


@app.route('/api/export_logs')
@login_required
def api_export_logs():
    monitor = get_monitor(session['user_id'])
    if monitor: return jsonify({'logs': monitor.full_history})
    return jsonify({'logs': []})


@app.route('/api/export_errors')
@login_required
def api_export_errors():
    monitor = get_monitor(session['user_id'])
    if monitor: return jsonify({'errors': monitor.error_lines})
    return jsonify({'errors': []})


@app.route('/api/export_messages')
@login_required
def api_export_messages():
    monitor = get_monitor(session['user_id'])
    if monitor: return jsonify({'messages': monitor.message_info_lines})
    return jsonify({'messages': []})


@app.route('/api/admin_uids', methods=['GET'])
@login_required
def api_admin_uids():
    monitor = get_monitor(session['user_id'])
    if not monitor: return jsonify({'uids': []})
    try:
        with open(monitor.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r"ADMIN_UIDS\s*=\s*\[([^\]]*)\]", content)
        if match:
            uids = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
            seen = set(); unique = []
            for uid in uids:
                if uid not in seen:
                    unique.append(uid); seen.add(uid)
            return jsonify({'uids': unique})
    except: pass
    return jsonify({'uids': []})


@app.route('/api/admin_uids', methods=['POST'])
@login_required
def api_update_admin_uids():
    data = request.json
    new_uids = data.get('uids', [])
    if not isinstance(new_uids, list):
        return jsonify({'status': 'error', 'message': 'uids must be list'}), 400
    normalized = []; seen = set()
    for uid in new_uids:
        uid = str(uid).strip()
        if uid and uid not in seen:
            normalized.append(uid); seen.add(uid)
    if MASTER_ADMIN_UID in normalized:
        normalized.remove(MASTER_ADMIN_UID)
    normalized.insert(0, MASTER_ADMIN_UID)
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'status': 'error', 'message': 'Bot not configured'}), 400
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET admin_uid=? WHERE id=?', (', '.join(normalized), session['user_id']))
        conn.commit(); conn.close()
        with open(monitor.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        list_str = build_admin_uids_list_string(normalized)
        new_content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", f"ADMIN_UIDS = {list_str}", content)
        with open(monitor.process_name, 'w', encoding='utf-8') as f:
            f.write(new_content)
        monitor.restart_logic()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/bot_creds', methods=['GET'])
@login_required
def api_bot_creds():
    monitor = get_monitor(session['user_id'])
    if not monitor: return jsonify({'uid': '', 'pw': ''})
    try:
        with open(monitor.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        match = re.search(r"Uid,\s*Pw\s*=\s*'([^']+)',\s*'([^']+)'", content)
        if match: return jsonify({'uid': match.group(1), 'pw': match.group(2)})
    except: pass
    return jsonify({'uid': '', 'pw': ''})


@app.route('/api/bot_creds', methods=['POST'])
@login_required
def api_update_bot_creds():
    data = request.json
    new_uid = data.get('uid'); new_pw = data.get('pw')
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'status': 'error', 'message': 'Not configured'}), 400
    try:
        with open(monitor.process_name, 'r', encoding='utf-8') as f:
            content = f.read()
        new_line = f"Uid, Pw = '{new_uid}', '{new_pw}'"
        new_content = re.sub(r"Uid,\s*Pw\s*=\s*'[^']*',\s*'[^']*'", new_line, content)
        with open(monitor.process_name, 'w', encoding='utf-8') as f:
            f.write(new_content)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET bot_uid=?, bot_pw=? WHERE id=?', (new_uid, new_pw, session['user_id']))
        conn.commit(); conn.close()
        monitor.restart_logic()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ========== Main Entry ==========
if __name__ == '__main__':
    if not os.path.exists(MAHIR_SOURCE):
        with open(MAHIR_SOURCE, 'w') as f:
            f.write("# Mahir Bot\nUid, Pw = 'default', 'default'\nADMIN_UIDS = []\n")
    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)