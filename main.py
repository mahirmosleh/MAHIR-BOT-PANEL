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
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB limit

DB_FILE = "users.db"
MAHIR_SOURCE = "mahir.py"
USER_BOTS_DIR = "."

# ========== Database ==========
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Users টেবিল তৈরি
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
        bot_status TEXT DEFAULT 'not_configured'
    )''')
    
    # Keys টেবিল তৈরি
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

    # চেক করুন অ্যাডমিন ইউজার আছে কিনা, না থাকলে তৈরি করুন
    c.execute('SELECT * FROM users WHERE username = ?', ('MAHIR TCP',))
    if not c.fetchone():
        # এখানে অ্যাডমিন ইউজার অটো তৈরি করে রাখা ভালো যাতে 500 এরর না আসে
        c.execute('''INSERT INTO users (username, password, registration_key, is_admin, is_agent, bot_status) 
                     VALUES (?, ?, ?, 1, 0, 'admin')''', ('MAHIR TCP', 'MAHIR0208@', 'SYSTEM_ADMIN'))
    
    conn.commit()
    conn.close()

init_db()

# ========== Helper: sanitize username for filename ==========
def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)

def is_hashed(pw):
    return len(pw) == 64 and all(c in '0123456789abcdefABCDEF' for c in pw)

def check_password(stored, provided):
    if is_hashed(stored):
        return stored == hashlib.sha256(provided.encode()).hexdigest()
    else:
        return stored == provided

# ========== Bio Update Function ==========
def update_bot_bio(uid, password, username):
    """Update bot bio via MAHIR long-bio API."""
    bio_text = f"[c][b][i][00BFFF]{username} [00FF00]বটে আপনাকে স্বাগতম। [FFFF00]নিজের জন্য এমন একটি Bot কিনতে চাইলে যোগাযোগ করুন আমাদের [7CFC00]WEBSITE NAME: [00FFFF]MAHIR.XO.JE [00FF00]TIKTOK [00FFFF]: [00FFFF]MAHIR__222"
    encoded = requests.utils.quote(bio_text)
    url = f"https://mahir-long-bio.vercel.app/bio_upload?bio={encoded}&uid={uid}&pass={password}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            print(f"✅ Bio updated for {uid}: {resp.text}")
            return True
        else:
            print(f"❌ Bio update failed for {uid}: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Bio update exception for {uid}: {e}")
        return False

# ========== ProcessMonitor Class ==========
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
        self.monitor_thread = None
        self.cpu_history = [0] * 20
        self.ram_history = [0] * 20
        
        # Bot info (parsed from logs)
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
        
        # Internal state for parsing
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
        if not text:
            return ""
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
            jwt = re.search(r'JWT TOKEN\s*[:：]\s*([a-zA-Z0-9_.-]+)', clean, re.IGNORECASE)
            if jwt: tokens['jwt_token'] = jwt.group(1)
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

        # USER INFO
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

        # TOKENS
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

        # SECURITY
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

        # SYSTEM STATUS
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

        # MESSAGE INFO
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

        # LOGIN SUCCESSFUL / Connected
        if 'LOGIN SUCCESSFUL' in clean:
            with self.lock:
                self.bot_status = "🟢 ACTIVE & ONLINE"
                self.account_info_found = True
            return
        if 'Connected' in clean or 'connected' in clean.lower():
            with self.lock:
                if not self.account_info_found:
                    self.bot_status = "🟡 CONNECTING..."
            return

    def is_error_line(self, line):
        if not line: return False
        lower = line.lower()
        patterns = ['error','exception','failed','traceback','critical','fatal','timeout',
                    'connection refused','permission denied','not found','invalid','crash',
                    'keyerror','attributeerror','typeerror','valueerror','indexerror',
                    'login failed','authentication failed','unable to connect','disconnected',
                    'ssl error','certificate']
        for p in patterns:
            if p in lower: return True
        return False

    def start_process(self):
        with self.lock:
            if self.process:
                self.stop_process_internal()
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
                # Update DB status
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute('UPDATE users SET bot_status="running", bot_pid=? WHERE id=?', (self.process.pid, self.user_id))
                conn.commit()
                conn.close()

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

    def stop_process_internal(self):
        if self.process:
            try:
                p = psutil.Process(self.process.pid)
                for child in p.children(recursive=True):
                    child.kill()
                p.kill()
            except:
                try: self.process.kill()
                except: pass
            self.process = None
        self.is_running = False
        self.bot_status = "🔴 OFFLINE"
        # Update DB
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET bot_pid=NULL, bot_status="stopped" WHERE id=?', (self.user_id,))
        conn.commit()
        conn.close()

    def stop_process(self):
        with self.lock:
            self.stop_process_internal()

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
        # system stats
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory().percent
            try:
                disk = psutil.disk_usage('/').percent
            except:
                disk = psutil.disk_usage(os.path.expanduser("~")).percent
        except:
            cpu, ram, disk = 0, 0, 0
        # update histories
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

# ========== Global dictionary to store monitors per user ==========
monitors = {}

def get_monitor(user_id):
    """Return the ProcessMonitor instance for a user, create if not exists."""
    if user_id not in monitors:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT bot_file, bot_status FROM users WHERE id=?', (user_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            bot_file = row[0]
            if not os.path.dirname(bot_file):
                bot_file = os.path.join(USER_BOTS_DIR, bot_file)
            monitor = ProcessMonitor(user_id, bot_file)
            monitors[user_id] = monitor
            # Always try to start if bot file exists and process is not running
            # But check if process already running from DB
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('SELECT bot_pid, bot_status FROM users WHERE id=?', (user_id,))
            row2 = c.fetchone()
            if row2:
                pid, status = row2
                if pid:
                    try:
                        p = psutil.Process(pid)
                        if p.is_running():
                            # Process is running, just attach monitor
                            monitor.is_running = True
                            monitor.start_time = datetime.now()
                            monitor.bot_status = "🟢 ACTIVE & ONLINE"
                            # Start reading output
                            # Since we can't attach to existing process easily, we restart
                            # To be safe, we restart
                            monitor.start_process()
                        else:
                            monitor.start_process()
                    except:
                        monitor.start_process()
                else:
                    monitor.start_process()
            conn.close()
        else:
            return None
    return monitors.get(user_id)

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
        if not session.get('user_id'):
            flash('Please login first', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# =============================================================================
# HTML Templates (embedded as strings)
# =============================================================================
LOGIN_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>Welcome Back - MAHIR PREMIUM</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n\n.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}\n.auth-card{width:100%;max-width:440px;padding:38px 34px;border-radius:24px}\n.logo-wrap{width:96px;height:96px;margin:0 auto 18px;border-radius:50%;padding:6px;background:linear-gradient(135deg,rgba(59,140,255,.15),rgba(133,64,245,.12));border:1px solid rgba(59,140,255,.25);box-shadow:0 0 50px rgba(59,140,255,.10)}\n.logo-wrap img{width:100%;height:100%;border-radius:50%;object-fit:cover}\n.auth-title{text-align:center;font-size:1.7rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:6px}\n.auth-sub{text-align:center;color:var(--muted);font-size:.85rem;margin-bottom:22px}\n.field{position:relative;margin-bottom:14px}\n.field i{position:absolute;left:16px;top:50%;transform:translateY(-50%);color:rgba(233,233,248,.35);font-size:.95rem}\n.field input{width:100%;padding:14px 16px 14px 46px}\n.btn-block{width:100%;padding:14px;font-size:1rem}\n.auth-links{text-align:center;margin-top:18px;padding-top:16px;border-top:1px solid rgba(245,200,66,.08);display:flex;flex-direction:column;gap:8px}\n.auth-links a{color:var(--gold2);text-decoration:none;font-size:.88rem;font-weight:500}\n.auth-links a:hover{color:var(--gold)}\n.alert{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border-radius:12px;margin-bottom:16px;font-size:.88rem;border:1px solid;text-align:center}\n.alert.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.alert.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.hamburger-menu{position:fixed;top:20px;left:20px;z-index:1000}\n.hamburger-btn{background:rgba(14,14,28,.92);border:1px solid rgba(245,200,66,.2);color:var(--gold);width:46px;height:46px;border-radius:12px;cursor:pointer;font-size:1.2rem;transition:.2s}\n.hamburger-btn:hover{border-color:var(--gold)}\n.menu-dropdown{display:none;position:absolute;top:56px;left:0;background:rgba(12,12,24,.97);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:8px 0;min-width:230px;box-shadow:0 20px 50px rgba(0,0,0,.6)}\n.menu-dropdown.active{display:block}\n.menu-title{padding:8px 20px 4px;color:rgba(233,233,248,.35);font-size:.62rem;text-transform:uppercase;letter-spacing:2px;font-weight:700}\n.menu-item{display:flex;align-items:center;gap:12px;padding:9px 20px;color:rgba(233,233,248,.75);text-decoration:none;font-size:.88rem;border-left:3px solid transparent}\n.menu-item:hover{background:rgba(245,200,66,.05);border-left-color:var(--gold);color:var(--gold)}\n.menu-item i{width:18px;text-align:center;color:rgba(233,233,248,.35)}\n.menu-item:hover i{color:var(--gold)}\n.menu-divider{border-top:1px solid rgba(245,200,66,.07);margin:6px 14px}\n.sidebar-download{display:flex;align-items:center;justify-content:center;gap:10px;margin:8px 14px;padding:11px;border-radius:12px;background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14 !important;text-decoration:none;font-weight:800;font-size:.82rem;border-left:none !important}\n</style></head><body>\n<div class="hamburger-menu">\n  <button class="hamburger-btn" onclick="toggleMenu()" aria-label="Menu"><i class="fas fa-bars"></i></button>\n  <div class="menu-dropdown" id="menuDropdown">\n    <div class="menu-title">Premium App</div>\n    <a href="https://www.mediafire.com/file/lvykrek51q17hae/MAHIR_TCP.apk" target="_blank" class="sidebar-download"><i class="fas fa-download"></i> DOWNLOAD APK</a>\n    <a href="https://youtube.com/shorts/1GuAuml8WRU?si=qQHAwCTblRJE7T9Q" target="_blank" class="menu-item"><i class="fas fa-play-circle" style="color:#4ade80"></i> Watch Video</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Authentication</div>\n    <a href="{{ url_for(\'login\') }}" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>\n    <a href="{{ url_for(\'register\') }}" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>\n    <a href="{{ url_for(\'recover\') }}" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Roles</div>\n    <a href="{{ url_for(\'admin_login\') }}" class="menu-item"><i class="fas fa-shield-alt"></i> Admin Login</a>\n    <a href="{{ url_for(\'agent_login\') }}" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Social</div>\n    <a href="https://t.me/mahirtcpchat" target="_blank" class="menu-item"><i class="fab fa-telegram"></i> Telegram</a>\n    <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="menu-item"><i class="fab fa-tiktok"></i> TikTok</a>\n    <div class="menu-divider"></div>\n    <a href="https://MAHIR.XO.JE/" target="_blank" class="menu-item"><i class="fas fa-globe"></i> Website</a>\n  </div>\n</div>\n<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">Welcome Back</div><div class="auth-sub">Sign in to your account</div>\n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n    <form method="POST" id="loginForm" class="form-row">\n      <div class="field"><i class="fas fa-user"></i><input type="text" name="username" placeholder="Username" required autocomplete="username"/></div>\n      <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required autocomplete="current-password"/></div>\n      <button type="submit" class="btn btn-gold btn-block" id="loginBtn"><i class="fas fa-sign-in-alt"></i> Login</button>\n    </form>\n    <script>document.getElementById(\'loginForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'loginBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Authenticating...\';});</script>\n    <div class="auth-links">\n    <a href="{{ url_for(\'register\') }}"><i class="fas fa-user-plus"></i> Don\'t have an account? Register</a>\n    <a href="{{ url_for(\'recover\') }}"><i class="fas fa-key"></i> Forgot Password?</a>\n    </div></div></div><script>\nfunction toggleMenu(){document.getElementById(\'menuDropdown\').classList.toggle(\'active\');}\ndocument.addEventListener(\'click\',function(e){var m=document.querySelector(\'.hamburger-menu\');if(m&&!m.contains(e.target)){document.getElementById(\'menuDropdown\').classList.remove(\'active\');}});\ndocument.addEventListener(\'keydown\',function(e){if(e.key===\'Escape\'){var d=document.getElementById(\'menuDropdown\');if(d)d.classList.remove(\'active\');}});\n</script></body></html>'

REGISTER_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>Create Account - MAHIR PREMIUM</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n\n.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}\n.auth-card{width:100%;max-width:440px;padding:38px 34px;border-radius:24px}\n.logo-wrap{width:96px;height:96px;margin:0 auto 18px;border-radius:50%;padding:6px;background:linear-gradient(135deg,rgba(59,140,255,.15),rgba(133,64,245,.12));border:1px solid rgba(59,140,255,.25);box-shadow:0 0 50px rgba(59,140,255,.10)}\n.logo-wrap img{width:100%;height:100%;border-radius:50%;object-fit:cover}\n.auth-title{text-align:center;font-size:1.7rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:6px}\n.auth-sub{text-align:center;color:var(--muted);font-size:.85rem;margin-bottom:22px}\n.field{position:relative;margin-bottom:14px}\n.field i{position:absolute;left:16px;top:50%;transform:translateY(-50%);color:rgba(233,233,248,.35);font-size:.95rem}\n.field input{width:100%;padding:14px 16px 14px 46px}\n.btn-block{width:100%;padding:14px;font-size:1rem}\n.auth-links{text-align:center;margin-top:18px;padding-top:16px;border-top:1px solid rgba(245,200,66,.08);display:flex;flex-direction:column;gap:8px}\n.auth-links a{color:var(--gold2);text-decoration:none;font-size:.88rem;font-weight:500}\n.auth-links a:hover{color:var(--gold)}\n.alert{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border-radius:12px;margin-bottom:16px;font-size:.88rem;border:1px solid;text-align:center}\n.alert.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.alert.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.hamburger-menu{position:fixed;top:20px;left:20px;z-index:1000}\n.hamburger-btn{background:rgba(14,14,28,.92);border:1px solid rgba(245,200,66,.2);color:var(--gold);width:46px;height:46px;border-radius:12px;cursor:pointer;font-size:1.2rem;transition:.2s}\n.hamburger-btn:hover{border-color:var(--gold)}\n.menu-dropdown{display:none;position:absolute;top:56px;left:0;background:rgba(12,12,24,.97);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:8px 0;min-width:230px;box-shadow:0 20px 50px rgba(0,0,0,.6)}\n.menu-dropdown.active{display:block}\n.menu-title{padding:8px 20px 4px;color:rgba(233,233,248,.35);font-size:.62rem;text-transform:uppercase;letter-spacing:2px;font-weight:700}\n.menu-item{display:flex;align-items:center;gap:12px;padding:9px 20px;color:rgba(233,233,248,.75);text-decoration:none;font-size:.88rem;border-left:3px solid transparent}\n.menu-item:hover{background:rgba(245,200,66,.05);border-left-color:var(--gold);color:var(--gold)}\n.menu-item i{width:18px;text-align:center;color:rgba(233,233,248,.35)}\n.menu-item:hover i{color:var(--gold)}\n.menu-divider{border-top:1px solid rgba(245,200,66,.07);margin:6px 14px}\n.sidebar-download{display:flex;align-items:center;justify-content:center;gap:10px;margin:8px 14px;padding:11px;border-radius:12px;background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14 !important;text-decoration:none;font-weight:800;font-size:.82rem;border-left:none !important}\n</style></head><body>\n<div class="hamburger-menu">\n  <button class="hamburger-btn" onclick="toggleMenu()" aria-label="Menu"><i class="fas fa-bars"></i></button>\n  <div class="menu-dropdown" id="menuDropdown">\n    <div class="menu-title">Premium App</div>\n    <a href="https://www.mediafire.com/file/lvykrek51q17hae/MAHIR_TCP.apk" target="_blank" class="sidebar-download"><i class="fas fa-download"></i> DOWNLOAD APK</a>\n    <a href="https://youtube.com/shorts/1GuAuml8WRU?si=qQHAwCTblRJE7T9Q" target="_blank" class="menu-item"><i class="fas fa-play-circle" style="color:#4ade80"></i> Watch Video</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Authentication</div>\n    <a href="{{ url_for(\'login\') }}" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>\n    <a href="{{ url_for(\'register\') }}" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>\n    <a href="{{ url_for(\'recover\') }}" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Roles</div>\n    <a href="{{ url_for(\'admin_login\') }}" class="menu-item"><i class="fas fa-shield-alt"></i> Admin Login</a>\n    <a href="{{ url_for(\'agent_login\') }}" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Social</div>\n    <a href="https://t.me/mahirtcpchat" target="_blank" class="menu-item"><i class="fab fa-telegram"></i> Telegram</a>\n    <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="menu-item"><i class="fab fa-tiktok"></i> TikTok</a>\n    <div class="menu-divider"></div>\n    <a href="https://MAHIR.XO.JE/" target="_blank" class="menu-item"><i class="fas fa-globe"></i> Website</a>\n  </div>\n</div>\n<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">Create Account</div><div class="auth-sub">Join the MAHIR network</div>\n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n    <form method="POST" id="registerForm" class="form-row">\n      <div class="field"><i class="fas fa-user"></i><input type="text" name="username" placeholder="Username" required autocomplete="username"/></div>\n      <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required autocomplete="new-password"/></div>\n      <div class="field"><i class="fas fa-envelope"></i><input type="email" name="email" placeholder="Email (optional)" autocomplete="email"/></div>\n      <div class="field"><i class="fas fa-key"></i><input type="text" name="registration_key" placeholder="Registration Key" required/></div>\n      <button type="submit" class="btn btn-gold btn-block" id="registerBtn"><i class="fas fa-paper-plane"></i> Register</button>\n    </form>\n    <script>document.getElementById(\'registerForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'registerBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Processing...\';});</script>\n    <div class="auth-links"><a href="{{ url_for(\'login\') }}"><i class="fas fa-arrow-left"></i> Already have an account? Login</a></div></div></div><script>\nfunction toggleMenu(){document.getElementById(\'menuDropdown\').classList.toggle(\'active\');}\ndocument.addEventListener(\'click\',function(e){var m=document.querySelector(\'.hamburger-menu\');if(m&&!m.contains(e.target)){document.getElementById(\'menuDropdown\').classList.remove(\'active\');}});\ndocument.addEventListener(\'keydown\',function(e){if(e.key===\'Escape\'){var d=document.getElementById(\'menuDropdown\');if(d)d.classList.remove(\'active\');}});\n</script></body></html>'

RECOVER_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>Recover Password - MAHIR PREMIUM</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n\n.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}\n.auth-card{width:100%;max-width:440px;padding:38px 34px;border-radius:24px}\n.logo-wrap{width:96px;height:96px;margin:0 auto 18px;border-radius:50%;padding:6px;background:linear-gradient(135deg,rgba(59,140,255,.15),rgba(133,64,245,.12));border:1px solid rgba(59,140,255,.25);box-shadow:0 0 50px rgba(59,140,255,.10)}\n.logo-wrap img{width:100%;height:100%;border-radius:50%;object-fit:cover}\n.auth-title{text-align:center;font-size:1.7rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:6px}\n.auth-sub{text-align:center;color:var(--muted);font-size:.85rem;margin-bottom:22px}\n.field{position:relative;margin-bottom:14px}\n.field i{position:absolute;left:16px;top:50%;transform:translateY(-50%);color:rgba(233,233,248,.35);font-size:.95rem}\n.field input{width:100%;padding:14px 16px 14px 46px}\n.btn-block{width:100%;padding:14px;font-size:1rem}\n.auth-links{text-align:center;margin-top:18px;padding-top:16px;border-top:1px solid rgba(245,200,66,.08);display:flex;flex-direction:column;gap:8px}\n.auth-links a{color:var(--gold2);text-decoration:none;font-size:.88rem;font-weight:500}\n.auth-links a:hover{color:var(--gold)}\n.alert{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border-radius:12px;margin-bottom:16px;font-size:.88rem;border:1px solid;text-align:center}\n.alert.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.alert.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.hamburger-menu{position:fixed;top:20px;left:20px;z-index:1000}\n.hamburger-btn{background:rgba(14,14,28,.92);border:1px solid rgba(245,200,66,.2);color:var(--gold);width:46px;height:46px;border-radius:12px;cursor:pointer;font-size:1.2rem;transition:.2s}\n.hamburger-btn:hover{border-color:var(--gold)}\n.menu-dropdown{display:none;position:absolute;top:56px;left:0;background:rgba(12,12,24,.97);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:8px 0;min-width:230px;box-shadow:0 20px 50px rgba(0,0,0,.6)}\n.menu-dropdown.active{display:block}\n.menu-title{padding:8px 20px 4px;color:rgba(233,233,248,.35);font-size:.62rem;text-transform:uppercase;letter-spacing:2px;font-weight:700}\n.menu-item{display:flex;align-items:center;gap:12px;padding:9px 20px;color:rgba(233,233,248,.75);text-decoration:none;font-size:.88rem;border-left:3px solid transparent}\n.menu-item:hover{background:rgba(245,200,66,.05);border-left-color:var(--gold);color:var(--gold)}\n.menu-item i{width:18px;text-align:center;color:rgba(233,233,248,.35)}\n.menu-item:hover i{color:var(--gold)}\n.menu-divider{border-top:1px solid rgba(245,200,66,.07);margin:6px 14px}\n.sidebar-download{display:flex;align-items:center;justify-content:center;gap:10px;margin:8px 14px;padding:11px;border-radius:12px;background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14 !important;text-decoration:none;font-weight:800;font-size:.82rem;border-left:none !important}\n</style></head><body>\n<div class="hamburger-menu">\n  <button class="hamburger-btn" onclick="toggleMenu()" aria-label="Menu"><i class="fas fa-bars"></i></button>\n  <div class="menu-dropdown" id="menuDropdown">\n    <div class="menu-title">Premium App</div>\n    <a href="https://www.mediafire.com/file/lvykrek51q17hae/MAHIR_TCP.apk" target="_blank" class="sidebar-download"><i class="fas fa-download"></i> DOWNLOAD APK</a>\n    <a href="https://youtube.com/shorts/1GuAuml8WRU?si=qQHAwCTblRJE7T9Q" target="_blank" class="menu-item"><i class="fas fa-play-circle" style="color:#4ade80"></i> Watch Video</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Authentication</div>\n    <a href="{{ url_for(\'login\') }}" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>\n    <a href="{{ url_for(\'register\') }}" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>\n    <a href="{{ url_for(\'recover\') }}" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Roles</div>\n    <a href="{{ url_for(\'admin_login\') }}" class="menu-item"><i class="fas fa-shield-alt"></i> Admin Login</a>\n    <a href="{{ url_for(\'agent_login\') }}" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Social</div>\n    <a href="https://t.me/mahirtcpchat" target="_blank" class="menu-item"><i class="fab fa-telegram"></i> Telegram</a>\n    <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="menu-item"><i class="fab fa-tiktok"></i> TikTok</a>\n    <div class="menu-divider"></div>\n    <a href="https://MAHIR.XO.JE/" target="_blank" class="menu-item"><i class="fas fa-globe"></i> Website</a>\n  </div>\n</div>\n<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap" style="display:flex;align-items:center;justify-content:center;"><i class="fas fa-key" style="font-size:2rem;color:var(--gold);"></i></div><div class="auth-title">Recover Password</div><div class="auth-sub">Enter your credentials to reset your password</div>\n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n    <form method="POST" id="recoverForm" class="form-row">\n      <div class="field"><i class="fas fa-user"></i><input type="text" name="username" placeholder="Username" required autocomplete="username"/></div>\n      <div class="field"><i class="fas fa-envelope"></i><input type="email" name="email" placeholder="Email Address" required autocomplete="email"/></div>\n      <button type="submit" class="btn btn-gold btn-block" id="recoverBtn"><i class="fas fa-paper-plane"></i> Recover Password</button>\n    </form>\n    <script>document.getElementById(\'recoverForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'recoverBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Processing...\';});</script>\n    <div class="auth-links"><a href="{{ url_for(\'login\') }}"><i class="fas fa-arrow-left"></i> Back to Login</a></div></div></div><script>\nfunction toggleMenu(){document.getElementById(\'menuDropdown\').classList.toggle(\'active\');}\ndocument.addEventListener(\'click\',function(e){var m=document.querySelector(\'.hamburger-menu\');if(m&&!m.contains(e.target)){document.getElementById(\'menuDropdown\').classList.remove(\'active\');}});\ndocument.addEventListener(\'keydown\',function(e){if(e.key===\'Escape\'){var d=document.getElementById(\'menuDropdown\');if(d)d.classList.remove(\'active\');}});\n</script></body></html>'

ADMIN_LOGIN_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>Admin Access - MAHIR PREMIUM</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n\n.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}\n.auth-card{width:100%;max-width:440px;padding:38px 34px;border-radius:24px}\n.logo-wrap{width:96px;height:96px;margin:0 auto 18px;border-radius:50%;padding:6px;background:linear-gradient(135deg,rgba(59,140,255,.15),rgba(133,64,245,.12));border:1px solid rgba(59,140,255,.25);box-shadow:0 0 50px rgba(59,140,255,.10)}\n.logo-wrap img{width:100%;height:100%;border-radius:50%;object-fit:cover}\n.auth-title{text-align:center;font-size:1.7rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:6px}\n.auth-sub{text-align:center;color:var(--muted);font-size:.85rem;margin-bottom:22px}\n.field{position:relative;margin-bottom:14px}\n.field i{position:absolute;left:16px;top:50%;transform:translateY(-50%);color:rgba(233,233,248,.35);font-size:.95rem}\n.field input{width:100%;padding:14px 16px 14px 46px}\n.btn-block{width:100%;padding:14px;font-size:1rem}\n.auth-links{text-align:center;margin-top:18px;padding-top:16px;border-top:1px solid rgba(245,200,66,.08);display:flex;flex-direction:column;gap:8px}\n.auth-links a{color:var(--gold2);text-decoration:none;font-size:.88rem;font-weight:500}\n.auth-links a:hover{color:var(--gold)}\n.alert{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border-radius:12px;margin-bottom:16px;font-size:.88rem;border:1px solid;text-align:center}\n.alert.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.alert.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.hamburger-menu{position:fixed;top:20px;left:20px;z-index:1000}\n.hamburger-btn{background:rgba(14,14,28,.92);border:1px solid rgba(245,200,66,.2);color:var(--gold);width:46px;height:46px;border-radius:12px;cursor:pointer;font-size:1.2rem;transition:.2s}\n.hamburger-btn:hover{border-color:var(--gold)}\n.menu-dropdown{display:none;position:absolute;top:56px;left:0;background:rgba(12,12,24,.97);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:8px 0;min-width:230px;box-shadow:0 20px 50px rgba(0,0,0,.6)}\n.menu-dropdown.active{display:block}\n.menu-title{padding:8px 20px 4px;color:rgba(233,233,248,.35);font-size:.62rem;text-transform:uppercase;letter-spacing:2px;font-weight:700}\n.menu-item{display:flex;align-items:center;gap:12px;padding:9px 20px;color:rgba(233,233,248,.75);text-decoration:none;font-size:.88rem;border-left:3px solid transparent}\n.menu-item:hover{background:rgba(245,200,66,.05);border-left-color:var(--gold);color:var(--gold)}\n.menu-item i{width:18px;text-align:center;color:rgba(233,233,248,.35)}\n.menu-item:hover i{color:var(--gold)}\n.menu-divider{border-top:1px solid rgba(245,200,66,.07);margin:6px 14px}\n.sidebar-download{display:flex;align-items:center;justify-content:center;gap:10px;margin:8px 14px;padding:11px;border-radius:12px;background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14 !important;text-decoration:none;font-weight:800;font-size:.82rem;border-left:none !important}\n</style></head><body>\n<div class="hamburger-menu">\n  <button class="hamburger-btn" onclick="toggleMenu()" aria-label="Menu"><i class="fas fa-bars"></i></button>\n  <div class="menu-dropdown" id="menuDropdown">\n    <div class="menu-title">Premium App</div>\n    <a href="https://www.mediafire.com/file/lvykrek51q17hae/MAHIR_TCP.apk" target="_blank" class="sidebar-download"><i class="fas fa-download"></i> DOWNLOAD APK</a>\n    <a href="https://youtube.com/shorts/1GuAuml8WRU?si=qQHAwCTblRJE7T9Q" target="_blank" class="menu-item"><i class="fas fa-play-circle" style="color:#4ade80"></i> Watch Video</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Authentication</div>\n    <a href="{{ url_for(\'login\') }}" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>\n    <a href="{{ url_for(\'register\') }}" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>\n    <a href="{{ url_for(\'recover\') }}" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Roles</div>\n    <a href="{{ url_for(\'admin_login\') }}" class="menu-item"><i class="fas fa-shield-alt"></i> Admin Login</a>\n    <a href="{{ url_for(\'agent_login\') }}" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>\n    <div class="menu-divider"></div>\n    <div class="menu-title">Social</div>\n    <a href="https://t.me/mahirtcpchat" target="_blank" class="menu-item"><i class="fab fa-telegram"></i> Telegram</a>\n    <a href="https://www.tiktok.com/@MAHIR__22" target="_blank" class="menu-item"><i class="fab fa-tiktok"></i> TikTok</a>\n    <div class="menu-divider"></div>\n    <a href="https://MAHIR.XO.JE/" target="_blank" class="menu-item"><i class="fas fa-globe"></i> Website</a>\n  </div>\n</div>\n<div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">Admin Access</div><div class="auth-sub">Secure admin panel login</div>\n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n    <form method="POST" id="adminLoginForm" class="form-row">\n      <div class="field"><i class="fas fa-user-shield"></i><input type="text" name="username" placeholder="Admin Username" required autocomplete="username"/></div>\n      <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required autocomplete="current-password"/></div>\n      <button type="submit" class="btn btn-gold btn-block" id="loginBtn"><i class="fas fa-sign-in-alt"></i> Login</button>\n    </form>\n    <script>document.getElementById(\'adminLoginForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'loginBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Authenticating...\';});</script>\n    <div class="auth-links"><a href="{{ url_for(\'login\') }}"><i class="fas fa-arrow-left"></i> Back to Main Site</a></div></div></div><script>\nfunction toggleMenu(){document.getElementById(\'menuDropdown\').classList.toggle(\'active\');}\ndocument.addEventListener(\'click\',function(e){var m=document.querySelector(\'.hamburger-menu\');if(m&&!m.contains(e.target)){document.getElementById(\'menuDropdown\').classList.remove(\'active\');}});\ndocument.addEventListener(\'keydown\',function(e){if(e.key===\'Escape\'){var d=document.getElementById(\'menuDropdown\');if(d)d.classList.remove(\'active\');}});\n</script></body></html>'

AGENT_LOGIN_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>Agent Login - MAHIR PREMIUM</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n\n.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}\n.auth-card{width:100%;max-width:440px;padding:38px 34px;border-radius:24px}\n.logo-wrap{width:96px;height:96px;margin:0 auto 18px;border-radius:50%;padding:6px;background:linear-gradient(135deg,rgba(59,140,255,.15),rgba(133,64,245,.12));border:1px solid rgba(59,140,255,.25);box-shadow:0 0 50px rgba(59,140,255,.10)}\n.logo-wrap img{width:100%;height:100%;border-radius:50%;object-fit:cover}\n.auth-title{text-align:center;font-size:1.7rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:6px}\n.auth-sub{text-align:center;color:var(--muted);font-size:.85rem;margin-bottom:22px}\n.field{position:relative;margin-bottom:14px}\n.field i{position:absolute;left:16px;top:50%;transform:translateY(-50%);color:rgba(233,233,248,.35);font-size:.95rem}\n.field input{width:100%;padding:14px 16px 14px 46px}\n.btn-block{width:100%;padding:14px;font-size:1rem}\n.auth-links{text-align:center;margin-top:18px;padding-top:16px;border-top:1px solid rgba(245,200,66,.08);display:flex;flex-direction:column;gap:8px}\n.auth-links a{color:var(--gold2);text-decoration:none;font-size:.88rem;font-weight:500}\n.auth-links a:hover{color:var(--gold)}\n.alert{display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border-radius:12px;margin-bottom:16px;font-size:.88rem;border:1px solid;text-align:center}\n.alert.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.alert.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.hamburger-menu{position:fixed;top:20px;left:20px;z-index:1000}\n.hamburger-btn{background:rgba(14,14,28,.92);border:1px solid rgba(245,200,66,.2);color:var(--gold);width:46px;height:46px;border-radius:12px;cursor:pointer;font-size:1.2rem;transition:.2s}\n.hamburger-btn:hover{border-color:var(--gold)}\n.menu-dropdown{display:none;position:absolute;top:56px;left:0;background:rgba(12,12,24,.97);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:8px 0;min-width:230px;box-shadow:0 20px 50px rgba(0,0,0,.6)}\n.menu-dropdown.active{display:block}\n.menu-title{padding:8px 20px 4px;color:rgba(233,233,248,.35);font-size:.62rem;text-transform:uppercase;letter-spacing:2px;font-weight:700}\n.menu-item{display:flex;align-items:center;gap:12px;padding:9px 20px;color:rgba(233,233,248,.75);text-decoration:none;font-size:.88rem;border-left:3px solid transparent}\n.menu-item:hover{background:rgba(245,200,66,.05);border-left-color:var(--gold);color:var(--gold)}\n.menu-item i{width:18px;text-align:center;color:rgba(233,233,248,.35)}\n.menu-item:hover i{color:var(--gold)}\n.menu-divider{border-top:1px solid rgba(245,200,66,.07);margin:6px 14px}\n.sidebar-download{display:flex;align-items:center;justify-content:center;gap:10px;margin:8px 14px;padding:11px;border-radius:12px;background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14 !important;text-decoration:none;font-weight:800;font-size:.82rem;border-left:none !important}\n</style></head><body><div class="auth-wrap"><div class="card auth-card"><div class="logo-wrap"><img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/></div><div class="auth-title">Agent Login</div><div class="auth-sub">Agent panel access</div>\n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n    <form method="POST" id="agentLoginForm" class="form-row">\n      <div class="field"><i class="fas fa-user-tie"></i><input type="text" name="username" placeholder="Agent Username" required/></div>\n      <div class="field"><i class="fas fa-lock"></i><input type="password" name="password" placeholder="Password" required/></div>\n      <button type="submit" class="btn btn-gold btn-block" id="loginBtn"><i class="fas fa-sign-in-alt"></i> Login</button>\n    </form>\n    <script>document.getElementById(\'agentLoginForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'loginBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Authenticating...\';});</script>\n    <div class="auth-links"><a href="{{ url_for(\'login\') }}"><i class="fas fa-arrow-left"></i> Back to Main</a></div></div></div><script></script></body></html>'

AGENT_DASHBOARD_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>Agent Dashboard - MAHIR PREMIUM</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n</style></head><body>\n<div class="container">\n  <div class="card header">\n    <h1><i class="fas fa-user-tie"></i> Agent Dashboard</h1>\n    <div class="flex">\n      <span class="welcome-text"><i class="fas fa-user-circle" style="color:var(--purple);"></i> Welcome, <strong>{{ session.username }}</strong></span>\n      <a href="{{ url_for(\'agent_logout\') }}" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>\n    </div>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-database"></i> Database Management <span style="font-size:.72rem;font-weight:400;color:var(--muted);">(users.db)</span></div>\n    <div class="flex">\n      <a href="{{ url_for(\'agent_download_db\') }}" class="btn btn-primary"><i class="fas fa-download"></i> Download users.db</a>\n      <form method="POST" action="{{ url_for(\'agent_upload_db\') }}" enctype="multipart/form-data" class="upload-form" id="uploadDbForm">\n        <input type="file" name="db_file" accept=".db" required/>\n        <button type="submit" class="btn btn-warning" id="uploadDbBtn" onclick="return confirm(\'This will REPLACE the current database. Are you sure?\')"><i class="fas fa-upload"></i> Upload &amp; Replace</button>\n      </form>\n    </div>\n    \n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-chart-simple"></i> Your Key Stats</div>\n    <p style="color:var(--muted);">Total Keys Created: <span class="stat-box">{{ keys|length }}</span></p>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-key"></i> Generate Registration Key</div>\n    <form method="POST" action="{{ url_for(\'agent_create_key\') }}" class="input-group" id="generateKeyForm">\n      <label><i class="far fa-calendar-alt"></i> Valid days (0 = Permanent):</label>\n      <input type="number" name="days_valid" value="30" min="0" max="365" style="width:110px;"/>\n      <button type="submit" class="btn btn-gold" id="generateKeyBtn"><i class="fas fa-plus-circle"></i> Generate Key</button>\n    </form>\n    {% if new_key %}\n    <div class="key-display">\n      <strong style="color:var(--muted);font-size:.85rem;"><i class="fas fa-key" style="color:var(--gold);"></i> New Key:</strong>\n      <code>{{ new_key }}</code>\n      {% if days == 0 %}<span class="badge badge-permanent"><i class="fas fa-infinity"></i> Permanent</span>\n      {% else %}<span style="color:var(--muted);font-size:.8rem;"><i class="far fa-clock"></i> valid {{ days }} days</span>{% endif %}\n    </div>\n    {% endif %}\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-list"></i> Your Keys</div>\n    <div class="table-wrapper">\n      <table>\n        <thead><tr><th>Key</th><th>Created</th><th>Used By</th><th>Status</th></tr></thead>\n        <tbody>\n          {% for key in keys %}\n          <tr>\n            <td><code>{{ key.key }}</code></td>\n            <td>{{ key.created_at[:10] }}</td>\n            <td>{{ key.used_by or \'—\' }}</td>\n            <td>{% if key.is_used %}<span class="badge badge-used"><i class="fas fa-check-circle"></i> Used</span>{% else %}<span class="badge badge-unused"><i class="fas fa-clock"></i> Available</span>{% endif %}</td>\n          </tr>\n          {% else %}\n          <tr class="empty-row"><td colspan="4">No keys created yet.</td></tr>\n          {% endfor %}\n        </tbody>\n      </table>\n    </div>\n  </div>\n  <a href="{{ url_for(\'login\') }}" class="back-link"><i class="fas fa-arrow-left"></i> Back to Main Site</a>\n</div>\n<script>\ndocument.getElementById(\'uploadDbForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'uploadDbBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Uploading...\';});\ndocument.getElementById(\'generateKeyForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'generateKeyBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Generating...\';});\n</script>\n</body></html>'

ADMIN_DASHBOARD_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>Admin Dashboard - MAHIR PREMIUM</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n</style></head><body>\n<div class="container">\n  <div class="card header">\n    <h1><i class="fas fa-shield-alt"></i> Admin Dashboard</h1>\n    <div class="flex">\n      <span class="welcome-text"><i class="fas fa-user-circle" style="color:var(--purple);"></i> Welcome, <strong>{{ session.username }}</strong></span>\n      <a href="{{ url_for(\'admin_logout\') }}" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>\n    </div>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-server"></i> Server Resources</div>\n    <div class="system-stats">\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-microchip"></i> CPU</div><div class="stat-value">{{ cpu }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ cpu }}%;"></div></div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-memory"></i> RAM</div><div class="stat-value">{{ ram_percent }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ ram_percent }}%;"></div></div><div class="stat-sub">{{ (ram_used / (1024**3))|round(1) }} GB / {{ (ram_total / (1024**3))|round(1) }} GB</div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-hdd"></i> Disk</div><div class="stat-value">{{ disk_percent }}%</div><div class="progress-bar"><div class="progress-fill" style="width:{{ disk_percent }}%;"></div></div><div class="stat-sub">{{ (disk_used / (1024**3))|round(1) }} GB / {{ (disk_total / (1024**3))|round(1) }} GB</div></div>\n    </div>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-folder-open"></i> File Manager</div>\n    <a href="{{ url_for(\'admin_file_manager\') }}" class="btn btn-gold"><i class="fas fa-folder"></i> Open File Manager</a>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-upload"></i> Upload New <code style="background:rgba(0,0,0,.4);padding:2px 10px;border-radius:8px;color:var(--gold);font-size:.85rem;">mahir.py</code></div>\n    <form method="POST" action="{{ url_for(\'admin_upload_mahir\') }}" enctype="multipart/form-data" class="upload-form" id="uploadMahirForm">\n      <input type="file" name="mahir_file" accept=".py" required style="flex:1;min-width:200px;"/>\n      <button type="submit" class="btn btn-warning" id="uploadMahirBtn"><i class="fas fa-cloud-upload-alt"></i> Upload &amp; Update All Bots</button>\n    </form>\n    \n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-user-tie"></i> Agent Management</div>\n    <form method="POST" action="{{ url_for(\'admin_create_agent\') }}" class="flex" id="createAgentForm">\n      <input type="text" name="username" placeholder="Username" required class="flex-grow"/>\n      <input type="email" name="email" placeholder="Email" required class="flex-grow"/>\n      <input type="password" name="password" placeholder="Password" required class="flex-grow"/>\n      <button type="submit" class="btn btn-success" id="createAgentBtn"><i class="fas fa-user-plus"></i> Create Agent</button>\n    </form>\n    <div class="table-wrapper">\n      <table>\n        <thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Created</th><th>Keys</th><th style="text-align:right;">Action</th></tr></thead>\n        <tbody>\n          {% for agent in agents %}\n          <tr>\n            <td>{{ agent.id }}</td>\n            <td><strong style="color:var(--gold2);">{{ agent.username }}</strong></td>\n            <td>{{ agent.email or \'-\' }}</td>\n            <td>{{ agent.created_at[:10] }}</td>\n            <td><span class="badge badge-admin">{{ agent.key_count }}</span></td>\n            <td><div class="td-actions"><form method="POST" action="{{ url_for(\'admin_delete_agent\', agent_id=agent.id) }}" onsubmit="return confirm(\'Delete this agent and all their keys?\');"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button></form></div></td>\n          </tr>\n          {% else %}\n          <tr class="empty-row"><td colspan="6">No agents created yet.</td></tr>\n          {% endfor %}\n        </tbody>\n      </table>\n    </div>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-key"></i> Generate Registration Key</div>\n    <form method="POST" action="{{ url_for(\'admin_create_key\') }}" class="flex" id="generateKeyForm">\n      <div class="input-group">\n        <label><i class="far fa-calendar-alt"></i> Valid days (0 = Permanent):</label>\n        <input type="number" name="days_valid" value="30" min="0" max="365" style="width:110px;"/>\n      </div>\n      <button type="submit" class="btn btn-gold" id="generateKeyBtn"><i class="fas fa-plus-circle"></i> Generate Key</button>\n    </form>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-users"></i> Registered Users</div>\n    <div class="table-wrapper">\n      <table>\n        <thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Created</th><th>Bot Status</th><th>Bot File</th><th>Role</th><th style="text-align:right;">Action</th></tr></thead>\n        <tbody>\n          {% for user in users %}\n          <tr>\n            <td>{{ user.id }}</td>\n            <td><strong style="color:var(--gold2);">{{ user.username }}</strong></td>\n            <td>{{ user.email or \'-\' }}</td>\n            <td>{{ user.created_at[:10] }}</td>\n            <td>{% if user.bot_status == \'running\' %}<span class="badge badge-running"><i class="fas fa-circle"></i> Running</span>{% elif user.bot_status == \'stopped\' %}<span class="badge badge-stopped"><i class="fas fa-circle"></i> Stopped</span>{% else %}<span class="badge badge-unused">{{ user.bot_status or \'Unknown\' }}</span>{% endif %}</td>\n            <td><code style="font-size:.72rem;">{{ user.bot_file or \'-\' }}</code></td>\n            <td>{% if user.is_admin %}<span class="badge badge-admin"><i class="fas fa-crown"></i> Admin</span>{% elif user.is_agent %}<span class="badge badge-agent"><i class="fas fa-user-tie"></i> Agent</span>{% else %}<span class="badge badge-user">User</span>{% endif %}</td>\n            <td>{% if not user.is_admin and not user.is_agent %}<div class="td-actions"><form method="POST" action="{{ url_for(\'admin_delete_user\', user_id=user.id) }}" onsubmit="return confirm(\'Delete this user?\');"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button></form></div>{% endif %}</td>\n          </tr>\n          {% else %}\n          <tr class="empty-row"><td colspan="8">No users registered yet.</td></tr>\n          {% endfor %}\n        </tbody>\n      </table>\n    </div>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-key"></i> Recent Keys</div>\n    <div class="table-wrapper">\n      <table>\n        <thead><tr><th>Key</th><th>Created By</th><th>Created</th><th>Used By</th><th>Status</th><th style="text-align:right;">Action</th></tr></thead>\n        <tbody>\n          {% for key in keys %}\n          <tr>\n            <td><code>{{ key.key }}</code></td>\n            <td>{{ key.created_by or \'—\' }}</td>\n            <td>{{ key.created_at[:10] }}</td>\n            <td>{{ key.used_by or \'—\' }}</td>\n            <td>{% if key.is_used %}<span class="badge badge-used"><i class="fas fa-check-circle"></i> Used</span>{% elif key.expiry_date is none %}<span class="badge badge-permanent"><i class="fas fa-infinity"></i> Permanent</span>{% else %}<span class="badge badge-unused"><i class="fas fa-clock"></i> Available</span>{% endif %}</td>\n            <td><div class="td-actions"><form method="POST" action="{{ url_for(\'admin_delete_key\', key_id=key.id) }}" onsubmit="return confirm(\'Delete this key?\');"><button type="submit" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button></form></div></td>\n          </tr>\n          {% else %}\n          <tr class="empty-row"><td colspan="6">No keys generated yet.</td></tr>\n          {% endfor %}\n        </tbody>\n      </table>\n    </div>\n  </div>\n  <a href="{{ url_for(\'logout\') }}" class="back-link"><i class="fas fa-arrow-left"></i> Back to Main Site</a>\n</div>\n<script>\ndocument.getElementById(\'uploadMahirForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'uploadMahirBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Uploading...\';});\ndocument.getElementById(\'createAgentForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'createAgentBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Creating...\';});\ndocument.getElementById(\'generateKeyForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'generateKeyBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Generating...\';});\n</script>\n</body></html>'

FILE_MANAGER_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>File Manager - MAHIR PREMIUM</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n</style></head><body>\n<div class="container">\n  <div class="card header">\n    <h1><i class="fas fa-folder-open"></i> File Manager</h1>\n    <div class="flex">\n      <a href="{{ url_for(\'admin_dashboard\') }}" class="btn btn-primary btn-sm"><i class="fas fa-th-large"></i> Dashboard</a>\n      <a href="{{ url_for(\'admin_logout\') }}" class="btn btn-danger btn-sm"><i class="fas fa-sign-out-alt"></i> Logout</a>\n    </div>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-upload"></i> Upload Any File <span style="font-size:.72rem;font-weight:400;color:var(--muted);">(ZIP auto-extracted)</span></div>\n    <form method="POST" action="{{ url_for(\'admin_upload_file\') }}" enctype="multipart/form-data" class="upload-form" id="uploadForm">\n      <input type="file" name="uploaded_file" required id="fileInput" style="flex:1;min-width:200px;"/>\n      <button type="submit" class="btn btn-gold" id="uploadBtn"><i class="fas fa-cloud-upload-alt"></i> Upload</button>\n    </form>\n    \n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-folder"></i> Current Directory: <span class="current-dir">{{ current_path }}</span></div>\n    <div class="breadcrumb">\n      <i class="fas fa-home" style="margin-right:6px;"></i><a href="{{ url_for(\'admin_file_manager\') }}">/</a>\n      {% for part in breadcrumb_parts %} / <a href="{{ url_for(\'admin_file_manager\', path=part) }}">{{ part }}</a>{% endfor %}\n    </div>\n    <div class="table-wrapper">\n      <table>\n        <thead><tr><th>Name</th><th>Size</th><th>Modified</th><th style="text-align:right;">Actions</th></tr></thead>\n        <tbody>\n          {% if parent_dir is not none %}\n          <tr><td><a href="{{ url_for(\'admin_file_manager\', path=parent_dir) }}" class="folder-link"><i class="fas fa-arrow-up" style="font-size:.75rem;"></i> ..</a></td><td>—</td><td>—</td><td>—</td></tr>\n          {% endif %}\n          {% for item in files %}\n          <tr>\n            <td>{% if item.is_dir %}<a href="{{ url_for(\'admin_file_manager\', path=item.path) }}" class="folder-link"><i class="fas fa-folder"></i> {{ item.name }}</a>{% else %}<span class="file-name"><i class="fas fa-file"></i> {{ item.name }}</span>{% endif %}</td>\n            <td>{{ item.size if not item.is_dir else \'—\' }}</td>\n            <td>{{ item.modified }}</td>\n            <td><div class="td-actions">{% if not item.is_dir %}\n              <a href="{{ url_for(\'admin_download_file\', path=item.path) }}" class="btn btn-info btn-sm"><i class="fas fa-download"></i></a>\n              <button onclick="editFile(\'{{ item.path }}\')" class="btn btn-warning btn-sm"><i class="fas fa-edit"></i></button>\n              <button onclick="deleteFile(\'{{ item.path }}\', this)" class="btn btn-danger btn-sm"><i class="fas fa-trash"></i></button>\n            {% endif %}</div></td>\n          </tr>\n          {% else %}\n          <tr class="empty-row"><td colspan="4">This directory is empty</td></tr>\n          {% endfor %}\n        </tbody>\n      </table>\n    </div>\n  </div>\n  <a href="{{ url_for(\'admin_dashboard\') }}" class="back-link"><i class="fas fa-arrow-left"></i> Back to Dashboard</a>\n</div>\n\n<div id="editModal" class="modal-overlay">\n  <div class="modal-box" style="max-width:820px;">\n    <button class="modal-close" onclick="closeEditModal()">&times;</button>\n    <div class="modal-title"><i class="fas fa-pen-fancy"></i> Edit File: <span id="editFileName" style="color:var(--gold2);font-size:1rem;"></span></div>\n    <textarea id="editContent" spellcheck="false" style="width:100%;height:380px;background:#05050c;color:var(--text);border:1px solid rgba(245,200,66,.12);border-radius:12px;padding:14px;font-family:var(--mono);font-size:.85rem;resize:vertical;"></textarea>\n    <div class="flex" style="justify-content:flex-end;margin-top:14px;">\n      <button onclick="saveEdit()" class="btn btn-success btn-sm"><i class="fas fa-save"></i> Save</button>\n      <button onclick="closeEditModal()" class="btn btn-clear btn-sm"><i class="fas fa-times"></i> Cancel</button>\n    </div>\n    <div id="editStatus" style="margin-top:10px;text-align:center;font-size:.85rem;color:var(--gold2);"></div>\n  </div>\n</div>\n\n<style>\n.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:10000;justify-content:center;align-items:center;padding:16px}\n.modal-overlay.active{display:flex}\n.modal-box{background:#101022;border:1px solid rgba(245,200,66,.14);border-radius:18px;padding:24px;width:100%;position:relative}\n.modal-close{position:absolute;top:12px;right:16px;font-size:1.7rem;color:var(--gold2);cursor:pointer;background:none;border:none}\n.modal-title{font-size:1.25rem;font-weight:800;color:var(--gold);margin-bottom:14px;display:flex;align-items:center;gap:10px}\n</style>\n\n<script>\ndocument.getElementById(\'uploadForm\').addEventListener(\'submit\',function(){var b=document.getElementById(\'uploadBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Uploading...\';});\nvar currentEditPath=\'\';\nfunction editFile(path){currentEditPath=path;document.getElementById(\'editFileName\').textContent=path;document.getElementById(\'editContent\').value=\'Loading...\';document.getElementById(\'editStatus\').textContent=\'\';document.getElementById(\'editModal\').classList.add(\'active\');fetch(\'/admin/edit_file/\'+encodeURIComponent(path)).then(function(r){return r.json();}).then(function(data){document.getElementById(\'editContent\').value=data.error?(\'Error: \'+data.error):data.content;}).catch(function(err){document.getElementById(\'editContent\').value=\'Error loading file: \'+err;});}\nfunction closeEditModal(){document.getElementById(\'editModal\').classList.remove(\'active\');}\nfunction saveEdit(){var content=document.getElementById(\'editContent\').value;var status=document.getElementById(\'editStatus\');status.textContent=\'Saving...\';fetch(\'/admin/edit_file/\'+encodeURIComponent(currentEditPath),{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({content:content})}).then(function(r){return r.json();}).then(function(data){if(data.success){status.textContent=\'Saved successfully!\';status.style.color=\'#4ade80\';setTimeout(function(){location.reload();},800);}else{status.textContent=\'Error: \'+(data.error||\'\');status.style.color=\'var(--red2)\';}}).catch(function(err){status.textContent=\'Error: \'+err;status.style.color=\'var(--red2)\';});}\nfunction deleteFile(path,btn){if(!confirm(\'Are you sure you want to delete "\'+path+\'"?\'))return;if(btn){btn.disabled=true;}fetch(\'/admin/delete_file/\'+encodeURIComponent(path),{method:\'POST\'}).then(function(r){return r.json();}).then(function(data){if(data.success){location.reload();}else{alert(\'Error: \'+(data.error||\'\'));if(btn){btn.disabled=false;}}}).catch(function(err){alert(\'Error: \'+err);if(btn){btn.disabled=false;}});}\ndocument.addEventListener(\'keydown\',function(e){if(e.key===\'Escape\')closeEditModal();});\ndocument.getElementById(\'editModal\').addEventListener(\'click\',function(e){if(e.target===this)closeEditModal();});\n</script>\n</body></html>'

USER_PANEL_HTML = '<!DOCTYPE html><html lang=\'en\'><head><meta charset=\'UTF-8\'/><meta name=\'viewport\' content=\'width=device-width, initial-scale=1.0\'/><title>MAHIR PREMIUM | Bot Controller</title><link rel=\'preconnect\' href=\'https://fonts.googleapis.com\'/><link href=\'https://fonts.googleapis.com/css2?family=Inter:wght@300..900&family=JetBrains+Mono:wght@400;600&display=swap\' rel=\'stylesheet\'/><link rel=\'stylesheet\' href=\'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css\'/><style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{--gold:#F5C842;--gold2:#FFE28A;--purple:#8540F5;--blue:#3B8CFF;--red:#D42A3A;--red2:#FF5A6A;--green:#4ade80;--bg:#07070f;--card:rgba(14,14,28,.88);--line:rgba(245,200,66,.12);--text:#e9e9f8;--muted:rgba(233,233,248,.55);--mono:\'JetBrains Mono\',monospace}\nbody{font-family:\'Inter\',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}\nbody::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(1000px 620px at 12% -10%,rgba(245,200,66,.10),transparent 60%),radial-gradient(900px 620px at 105% 115%,rgba(133,64,245,.13),transparent 60%),radial-gradient(760px 520px at 85% 0%,rgba(59,140,255,.07),transparent 60%),#07070f}\na{color:var(--gold2)}\n.container{width:100%;max-width:1200px;margin:0 auto;padding:20px;position:relative;z-index:1}\n.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.35);transition:border-color .25s ease,transform .25s ease}\n.card:hover{border-color:rgba(245,200,66,.25)}\n.card-title{font-size:1.05rem;font-weight:700;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.card-title i{color:var(--purple)}\n.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:20px 24px;margin-bottom:22px}\n.header h1{font-size:1.5rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;display:flex;align-items:center;gap:12px}\n.welcome-text{color:var(--muted);font-size:.9rem}.welcome-text strong{color:var(--gold2)}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 20px;border:none;border-radius:12px;font-family:inherit;font-weight:700;font-size:.85rem;cursor:pointer;text-decoration:none;transition:transform .2s ease,filter .2s ease;box-shadow:0 4px 16px rgba(0,0,0,.3)}\n.btn:hover:not(:disabled){transform:translateY(-2px);filter:brightness(1.1)}\n.btn:disabled{opacity:.55;cursor:not-allowed}\n.btn-sm{padding:6px 12px;font-size:.75rem;border-radius:10px}\n.btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-primary{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-warning{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-gold{background:linear-gradient(135deg,#F5C842,#8540F5 60%,#3B8CFF);color:#0a0a14}\n.btn-clear{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-start{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.btn-stop{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-reset{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.btn-admin{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.btn-export{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.btn-fullscreen{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.spinner{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}\n.btn-warning .spinner,.btn-gold .spinner,.btn-reset .spinner{border-color:rgba(0,0,0,.25);border-top-color:#111}\n@keyframes spin{to{transform:rotate(360deg)}}\n.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;border:1px solid transparent}\n.badge-used{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-unused{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.3)}\n.badge-permanent{background:rgba(74,222,128,.12);color:var(--green);border-color:rgba(74,222,128,.3)}\n.badge-admin{background:rgba(133,64,245,.14);color:#c9a7ff;border-color:rgba(133,64,245,.35)}\n.badge-agent{background:rgba(59,140,255,.12);color:#7ab5ff;border-color:rgba(59,140,255,.3)}\n.badge-user{background:rgba(255,255,255,.05);color:var(--muted);border-color:rgba(255,255,255,.1)}\n.badge-running{background:rgba(57,255,20,.08);color:var(--green);border-color:rgba(57,255,20,.3)}\n.badge-stopped{background:rgba(255,23,68,.08);color:#ff5a76;border-color:rgba(255,23,68,.3)}\n.badge-active{background:rgba(59,140,255,.10);color:#6db2ff;border-color:rgba(59,140,255,.35)}\n.badge-offline{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.35)}\n.badge-warning{background:rgba(245,200,66,.10);color:var(--gold);border-color:rgba(245,200,66,.35)}\n.flash-msg{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:12px;margin-top:14px;font-size:.88rem;border:1px solid}\n.flash-msg.success{background:rgba(59,140,255,.08);color:#8fc0ff;border-color:rgba(59,140,255,.25)}\n.flash-msg.error{background:rgba(212,42,58,.10);color:var(--red2);border-color:rgba(212,42,58,.3)}\n.input-group{display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n.input-group label{color:var(--muted);font-size:.85rem;font-weight:600}\ninput[type=text],input[type=password],input[type=email],input[type=number]{padding:12px 16px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.95rem;transition:border-color .2s ease,box-shadow .2s ease}\ninput:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(245,200,66,.10)}\ninput::placeholder{color:rgba(233,233,248,.3)}\ninput[type=file]{padding:10px 14px;border-radius:12px;border:1px solid rgba(245,200,66,.14);background:rgba(0,0,0,.45);color:var(--text);font-family:inherit;font-size:.88rem;cursor:pointer}\ninput[type=file]::file-selector-button{padding:6px 14px;border:none;border-radius:8px;background:rgba(245,200,66,.15);color:var(--gold2);font-weight:600;margin-right:10px;cursor:pointer}\n.table-wrapper{overflow-x:auto;margin-top:12px}\ntable{width:100%;border-collapse:collapse;font-size:.88rem}\nth,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(245,200,66,.06)}\nth{color:var(--gold2);font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;background:rgba(0,0,0,.3);font-weight:700}\ntr:hover td{background:rgba(245,200,66,.03)}\ntd code,.key-display code{background:rgba(0,0,0,.5);padding:4px 10px;border-radius:8px;color:var(--gold);font-family:var(--mono);font-size:.82rem;border:1px solid rgba(245,200,66,.10)}\n.empty-row td{text-align:center;color:var(--muted);padding:28px 0;font-style:italic}\n.stat-card{background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.08);border-radius:16px;padding:18px;text-align:center;transition:transform .2s ease,border-color .2s ease}\n.stat-card:hover{transform:translateY(-3px);border-color:rgba(245,200,66,.22)}\n.stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:2px;color:var(--gold2);margin-bottom:8px;font-weight:700}\n.stat-value{font-size:1.25rem;font-weight:800;color:#fff;word-break:break-all}\n.stat-sub{font-size:.72rem;color:var(--muted);margin-top:4px}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}\n.system-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}\n.progress-bar{background:#15152a;border-radius:999px;height:7px;overflow:hidden;margin-top:10px;border:1px solid rgba(245,200,66,.06)}\n.progress-fill{background:linear-gradient(90deg,#F5C842,#8540F5,#3B8CFF);height:100%;width:0%;border-radius:999px;transition:width .5s ease}\n.info-row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid rgba(245,200,66,.06);flex-wrap:wrap;font-size:.9rem}\n.info-label{color:var(--gold2);font-weight:600}\n.key-display{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:rgba(0,0,0,.4);border:1px solid rgba(245,200,66,.12);border-radius:14px;padding:14px 18px;margin-top:14px}\n.back-link{color:var(--gold2);text-decoration:none;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;padding:10px 0}\n.back-link:hover{color:var(--gold)}\n.breadcrumb{color:var(--muted);font-size:.85rem;margin-bottom:14px;padding:10px 14px;background:rgba(0,0,0,.3);border-radius:10px;border:1px solid rgba(245,200,66,.05)}\n.breadcrumb a{color:var(--gold2);text-decoration:none}\n.current-dir{color:var(--gold);font-weight:600}\n.folder-link{color:var(--gold);text-decoration:none;font-weight:600}\n.file-name{color:#8fc0ff}\n.td-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}\n.flex{display:flex;gap:12px;flex-wrap:wrap;align-items:center}\n.flex-grow{flex:1;min-width:150px}\n.stat-box{display:inline-block;background:rgba(0,0,0,.4);padding:6px 18px;border-radius:999px;color:var(--gold);font-weight:700;border:1px solid rgba(245,200,66,.12)}\n.upload-form{display:flex;gap:14px;align-items:center;flex-wrap:wrap}\n@media (max-width:768px){.container{padding:14px}.header{padding:16px;flex-direction:column;text-align:center}.system-stats{grid-template-columns:1fr}.input-group{flex-direction:column;align-items:stretch}.input-group input{width:100%}th,td{padding:9px 10px;font-size:.78rem}}\n@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important}}\n\n.logout-btn{position:fixed;top:20px;right:20px;z-index:999}\n.cover-section{position:relative;border-radius:22px;overflow:hidden;margin-bottom:24px;border:1px solid rgba(245,200,66,.15);box-shadow:0 15px 40px rgba(0,0,0,.5)}\n.cover-section img.cover-image{width:100%;height:260px;object-fit:cover;display:block}\n.cover-overlay{position:absolute;inset:0;background:linear-gradient(100deg,rgba(7,7,15,.92) 20%,rgba(7,7,15,.5) 60%,rgba(7,7,15,.25));display:flex;flex-direction:column;justify-content:center;padding:32px 40px}\n.logo-row{display:flex;align-items:center;gap:18px}\n.logo-row img{height:72px;width:72px;border-radius:16px;object-fit:cover;border:1px solid rgba(245,200,66,.25)}\n.cover-title{font-size:2.2rem;font-weight:800;background:linear-gradient(120deg,#F5C842,#FFE28A 35%,#B388FF 70%,#3B8CFF);-webkit-background-clip:text;background-clip:text;color:transparent;line-height:1.1}\n.cover-sub{color:rgba(233,233,248,.65);font-size:.85rem;letter-spacing:1.5px;margin-top:4px}\n.cover-badge{position:absolute;top:18px;right:20px;background:rgba(7,7,15,.7);border:1px solid rgba(245,200,66,.25);padding:6px 16px;border-radius:999px;font-size:.66rem;font-weight:700;color:var(--gold);letter-spacing:2px;text-transform:uppercase}\n@media(max-width:768px){.cover-section img.cover-image{height:190px}.cover-overlay{padding:18px}.cover-title{font-size:1.4rem}.logo-row img{height:48px;width:48px}}\n.download-card{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}\n.dc-left{display:flex;align-items:center;gap:16px}\n.dc-icon{font-size:2rem;color:var(--gold);background:rgba(245,200,66,.07);width:60px;height:60px;border-radius:16px;display:flex;align-items:center;justify-content:center;border:1px solid rgba(245,200,66,.12)}\n.dc-title{font-size:1.05rem;font-weight:700}\n.dc-desc{color:var(--muted);font-size:.8rem;margin:2px 0 6px}\n.version-tag{display:inline-block;background:rgba(245,200,66,.08);color:var(--gold);padding:2px 10px;border-radius:999px;font-size:.62rem;font-weight:700;margin-right:6px;border:1px solid rgba(245,200,66,.12)}\n.tab-container{display:flex;gap:8px;margin-bottom:14px;border-bottom:1px solid rgba(245,200,66,.08);padding-bottom:10px;flex-wrap:wrap}\n.tab-btn{background:transparent;border:1px solid transparent;padding:8px 18px;border-radius:10px;color:var(--muted);cursor:pointer;font-weight:600;font-size:.82rem;font-family:inherit;transition:.2s}\n.tab-btn:hover{color:var(--gold2)}\n.tab-btn.active{background:rgba(245,200,66,.08);color:var(--gold);border-color:rgba(245,200,66,.25)}\n.tab-content{display:none}\n.tab-content.active{display:block}\n.log-box{background:#05050c;border:1px solid rgba(245,200,66,.08);border-radius:14px;padding:14px;height:400px;overflow-y:auto;font-family:var(--mono);font-size:.78rem}\n.log-box::-webkit-scrollbar{width:6px}\n.log-box::-webkit-scrollbar-thumb{background:rgba(245,200,66,.25);border-radius:99px}\n.log-line{padding:4px 8px;border-left:3px solid var(--gold);margin-bottom:2px;color:#c5c5e5;word-wrap:break-word;white-space:pre-wrap}\n.error-line{border-left-color:var(--red);color:#fca5a5;background:rgba(212,42,58,.05)}\n.message-card{background:rgba(245,200,66,.03);border:1px solid rgba(245,200,66,.08);border-radius:12px;padding:12px 14px;margin-bottom:10px}\n.message-header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding-bottom:8px;margin-bottom:8px;border-bottom:1px solid rgba(245,200,66,.06)}\n.message-sender{font-weight:800;color:var(--gold2)}\n.message-time{font-size:.65rem;color:var(--muted)}\n.message-meta{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;font-size:.8rem}\n.message-label{color:var(--gold2);font-weight:600}\n.message-value{color:#c5c5e5;word-break:break-all}\n.control-bar{display:flex;gap:8px;margin-bottom:10px;align-items:center;flex-wrap:wrap}\n.pause-btn{background:rgba(255,255,255,.06);border:1px solid rgba(245,200,66,.15);padding:7px 14px;border-radius:10px;color:var(--text);cursor:pointer;font-size:.78rem;font-family:inherit}\n.pause-btn.paused{border-color:var(--red);color:var(--red2)}\n.button-group{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}\n.button-group .btn{flex:1;min-width:110px}\n.chart-container{position:relative;height:250px}\n.config-form{max-width:560px;margin:0 auto;display:flex;flex-direction:column;gap:14px}\n.config-form label{display:block;color:var(--gold2);font-weight:600;margin-bottom:6px;font-size:.85rem}\n.config-form input{width:100%}\n.config-status{padding:13px 16px;background:rgba(245,200,66,.05);border:1px solid rgba(245,200,66,.12);border-left:4px solid var(--gold);border-radius:10px;color:var(--gold2);font-size:.85rem;margin-bottom:18px}\n.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:10000;justify-content:center;align-items:center;padding:16px}\n.modal-overlay.active{display:flex}\n.modal-box{background:#101022;border:1px solid rgba(245,200,66,.14);border-radius:18px;padding:24px;max-width:700px;width:100%;max-height:88vh;overflow-y:auto;position:relative}\n.modal-close{position:absolute;top:12px;right:16px;font-size:1.7rem;color:var(--gold2);cursor:pointer;background:none;border:none}\n.modal-title{font-size:1.3rem;font-weight:800;color:var(--gold);margin-bottom:16px;display:flex;align-items:center;gap:10px}\n.modal-section{background:rgba(0,0,0,.3);border:1px solid rgba(245,200,66,.07);border-radius:14px;padding:16px;margin-bottom:14px}\n.modal-section h3{color:var(--gold);font-size:.95rem;margin-bottom:12px;display:flex;align-items:center;gap:8px}\n.modal-input-group{display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap}\n.modal-input-group label{min-width:90px;color:var(--gold2);font-weight:600;font-size:.82rem}\n.modal-input-group input{flex:1;min-width:160px}\n.modal-btn{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border:none;border-radius:10px;font-weight:700;font-size:.78rem;cursor:pointer;font-family:inherit}\n.modal-btn-save{background:linear-gradient(135deg,#F5C842,#C99A1A);color:#141400}\n.modal-btn-cancel{background:rgba(255,255,255,.08);color:var(--text)}\n.modal-btn-action{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n.modal-btn-danger{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.modal-btn-info{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.modal-result-box{background:rgba(0,0,0,.45);border:1px solid rgba(245,200,66,.08);border-radius:10px;padding:12px;margin-top:10px;max-height:160px;overflow-y:auto;font-size:.78rem;color:#c5c5e5;white-space:pre-wrap;word-break:break-word}\n.notification{position:fixed;top:20px;right:20px;padding:12px 18px;border-radius:12px;z-index:99999;font-weight:600;font-size:.85rem;box-shadow:0 10px 30px rgba(0,0,0,.5)}\n.notification-success{background:linear-gradient(135deg,#3B8CFF,#0F4CBF);color:#fff}\n.notification-error{background:linear-gradient(135deg,#D42A3A,#8A1A28);color:#fff}\n.notification-info{background:linear-gradient(135deg,#8540F5,#5A1A9A);color:#fff}\n@media(max-width:768px){.download-card{flex-direction:column;align-items:stretch;text-align:center}.dc-left{flex-direction:column}.button-group .btn{flex:1 1 45%}.chart-container{height:190px}.modal-input-group{flex-direction:column;align-items:stretch}.modal-input-group label{min-width:auto}}\n</style></head><body>\n<div class="container">\n  <button class="logout-btn btn btn-danger btn-sm" onclick="window.location.href=\'/logout\'"><i class="fas fa-sign-out-alt"></i> Logout</button>\n  <div class="cover-section">\n    <img class="cover-image" src="https://mahir-photo-url.vercel.app/image/Picsart_26-06-20_16-14-53-925.jpg" alt="Cover"/>\n    <div class="cover-overlay">\n      <div class="logo-row">\n        <img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR"/>\n        <div><div class="cover-title">MAHIR PREMIUM</div><div class="cover-sub">ELITE BOT CONTROLLER</div></div>\n      </div>\n      <div class="cover-badge"><i class="fas fa-crown"></i> {{ \'PREMIUM\' if config_done else \'SETUP\' }}</div>\n    </div>\n  </div>\n\n  {% if not config_done %}\n  <div class="card">\n    <div class="card-title"><i class="fas fa-cog"></i> Bot Configuration</div>\n    \n{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert {{ category }}"><i class="fas fa-{% if category == \'error\' %}exclamation-circle{% else %}check-circle{% endif %}"></i> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}\n\n    <div class="config-status"><i class="fas fa-info-circle"></i> Enter your Free Fire bot credentials to deploy.</div>\n    <form method="POST" action="{{ url_for(\'configure_bot\') }}" class="config-form" id="configForm">\n      <div><label><i class="fas fa-user-shield"></i> Admin UID</label><input type="text" name="admin_uid" placeholder="e.g., 1120167200" required/></div>\n      <div><label><i class="fas fa-robot"></i> Bot UID</label><input type="text" name="bot_uid" placeholder="Enter bot UID" required/></div>\n      <div><label><i class="fas fa-key"></i> Bot Password</label><input type="text" name="bot_pw" placeholder="Enter bot password hash" required/></div>\n      <button type="submit" class="btn btn-gold btn-block" id="deployBtn"><i class="fas fa-play"></i> Deploy Bot</button>\n    </form>\n    <div style="margin-top:14px;font-size:.8rem;color:var(--gold2);"><i class="fas fa-shield-alt"></i> Master Admin UID (1120167200) will be auto-added.</div>\n  </div>\n  {% else %}\n\n  <div class="card download-card">\n    <div class="dc-left">\n      <div class="dc-icon"><i class="fab fa-android"></i></div>\n      <div>\n        <div class="dc-title"><i class="fas fa-mobile-alt" style="color:var(--gold);margin-right:6px;"></i> MAHIR TCP Bot</div>\n        <div class="dc-desc">Download the official Android app to control your bot on the go</div>\n        <span class="version-tag"><i class="fas fa-tag"></i> v2.0.1</span>\n        <span class="version-tag" style="background:rgba(59,140,255,.08);color:#6db2ff;"><i class="fas fa-check-circle"></i> Latest</span>\n      </div>\n    </div>\n    <a href="https://www.mediafire.com/file/lvykrek51q17hae/MAHIR_TCP.apk" target="_blank" class="btn btn-gold" id="downloadApkBtn"><i class="fas fa-download"></i> Download APK <span style="font-size:.65rem;opacity:.75;">18.4 MB</span></a>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-robot"></i> Bot Identity &amp; Status</div>\n    <div class="stats-grid">\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-id-card"></i> UID</div><div class="stat-value" id="botUid">---</div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-user-astronaut"></i> Name</div><div class="stat-value" id="botName">---</div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-globe-asia"></i> Region</div><div class="stat-value" id="botRegion">---</div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-heartbeat"></i> Status</div><div class="stat-value" id="botStatus">---</div></div>\n    </div>\n    <div style="margin-top:16px;padding-top:14px;border-top:1px solid rgba(245,200,66,.06);">\n      <div style="font-size:.88rem;font-weight:700;color:var(--gold2);margin-bottom:10px;"><i class="fas fa-comment-dots"></i> Last Message Activity</div>\n      <div class="stats-grid">\n        <div class="stat-card"><div class="stat-label"><i class="fas fa-user"></i> Sender UID</div><div class="stat-value" id="lastSenderUid" style="font-size:.9rem;">---</div></div>\n        <div class="stat-card"><div class="stat-label"><i class="fas fa-users"></i> Guild</div><div class="stat-value" id="lastGuildName" style="font-size:.9rem;">---</div></div>\n        <div class="stat-card"><div class="stat-label"><i class="fas fa-comment"></i> Message</div><div class="stat-value" id="lastMessage" style="font-size:.8rem;">---</div></div>\n      </div>\n    </div>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-chart-line"></i> System Performance</div>\n    <div class="stats-grid">\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-microchip"></i> Process</div><div class="stat-value" id="processStatus">---</div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-clock"></i> Uptime</div><div class="stat-value" id="uptime">00:00:00</div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-sync-alt"></i> Restarts</div><div class="stat-value" id="restartCount">0</div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-exclamation-triangle"></i> Errors</div><div class="stat-value" id="errorCount" style="color:var(--red2);">0</div></div>\n    </div>\n    <div class="system-stats">\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-tachometer-alt"></i> CPU</div><div class="stat-value" id="cpuValue">0%</div><div class="progress-bar"><div class="progress-fill" id="cpuBar"></div></div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-memory"></i> RAM</div><div class="stat-value" id="ramValue">0%</div><div class="progress-bar"><div class="progress-fill" id="ramBar"></div></div></div>\n      <div class="stat-card"><div class="stat-label"><i class="fas fa-hdd"></i> Disk</div><div class="stat-value" id="diskValue">0%</div><div class="progress-bar"><div class="progress-fill" id="diskBar"></div></div></div>\n    </div>\n    <div class="info-row"><span class="info-label"><i class="fas fa-hourglass-half"></i> Script Expiry:</span><span class="info-value" id="expiryInfo">No Limit</span></div>\n    <div class="info-row"><span class="info-label"><i class="fas fa-redo-alt"></i> Auto-Restart:</span><span class="info-value" id="autoRestartInfo">Disabled</span></div>\n  </div>\n\n  <div class="card">\n    <div class="card-title"><i class="fas fa-chart-area"></i> Performance Monitor</div>\n    <div class="chart-container"><canvas id="performanceChart"></canvas></div>\n  </div>\n\n  <div class="card">\n    <div class="tab-container">\n      <button class="tab-btn active" onclick="switchTab(\'logs\')"><i class="fas fa-terminal"></i> Console</button>\n      <button class="tab-btn" onclick="switchTab(\'messages\')"><i class="fas fa-envelope"></i> Messages</button>\n      <button class="tab-btn" onclick="switchTab(\'errors\')"><i class="fas fa-exclamation-triangle"></i> Errors</button>\n    </div>\n    <div id="logsTab" class="tab-content active">\n      <div class="control-bar">\n        <button onclick="togglePause()" id="pauseBtn" class="pause-btn"><i class="fas fa-pause"></i> Pause</button>\n        <button onclick="exportLogs()" class="btn btn-export btn-sm"><i class="fas fa-download"></i> Export</button>\n        <span style="margin-left:auto;font-size:.72rem;color:var(--gold2);" id="logStatus">Auto-scroll: ON</span>\n      </div>\n      <div id="logBox" class="log-box"><div class="log-line"><i class="fas fa-info-circle"></i> Waiting for logs...</div></div>\n    </div>\n    <div id="messagesTab" class="tab-content">\n      <div class="control-bar">\n        <button onclick="clearMessages()" class="btn btn-clear btn-sm"><i class="fas fa-trash-alt"></i> Clear</button>\n        <button onclick="exportMessages()" class="btn btn-export btn-sm"><i class="fas fa-download"></i> Export</button>\n      </div>\n      <div id="messageHistory" class="log-box" style="height:380px;"><div class="log-line"><i class="fas fa-info-circle"></i> No messages received...</div></div>\n    </div>\n    <div id="errorsTab" class="tab-content">\n      <div class="control-bar">\n        <button onclick="clearErrors()" class="btn btn-clear btn-sm"><i class="fas fa-trash-alt"></i> Clear</button>\n        <button onclick="exportErrors()" class="btn btn-export btn-sm"><i class="fas fa-download"></i> Export</button>\n      </div>\n      <div id="errorBox" class="log-box"><div class="log-line"><i class="fas fa-check-circle"></i> No errors detected</div></div>\n    </div>\n    <div class="button-group">\n      <button onclick="sendAction(\'start\')" id="btnStart" class="btn btn-start"><i class="fas fa-play"></i> Start</button>\n      <button onclick="sendAction(\'stop\')" id="btnStop" class="btn btn-stop"><i class="fas fa-stop"></i> Stop</button>\n      <button onclick="sendAction(\'reset\')" id="btnReset" class="btn btn-reset"><i class="fas fa-sync-alt"></i> Reset</button>\n      <button onclick="openAdminPanel()" id="btnAdmin" class="btn btn-admin"><i class="fas fa-cog"></i> Admin</button>\n    </div>\n  </div>\n  {% endif %}\n</div>\n\n<div id="adminModal" class="modal-overlay">\n  <div class="modal-box">\n    <button class="modal-close" onclick="closeAdminPanel()">&times;</button>\n    <div class="modal-title"><i class="fas fa-crown"></i> Admin Control Panel</div>\n    <div class="modal-section">\n      <h3><i class="fas fa-user-shield"></i> Admin UIDs</h3>\n      <div class="modal-input-group">\n        <label>UIDs (comma separated):</label>\n        <input type="text" id="adminUidsInput" placeholder="e.g. 1120167200, 3020431227"/>\n      </div>\n      <button onclick="updateAdminUIDs()" id="adminUidsBtn" class="modal-btn modal-btn-save"><i class="fas fa-save"></i> Save &amp; Restart</button>\n    </div>\n    <div class="modal-section">\n      <h3><i class="fas fa-key"></i> Bot Credentials</h3>\n      <div class="modal-input-group"><label>Bot UID:</label><input type="text" id="botUidInput" placeholder="Enter new UID"/></div>\n      <div class="modal-input-group"><label>Password:</label><input type="text" id="botPwInput" placeholder="Enter new password hash"/></div>\n      <button onclick="updateBotCreds()" id="botCredsBtn" class="modal-btn modal-btn-save"><i class="fas fa-save"></i> Save &amp; Restart</button>\n    </div>\n    <div class="modal-section">\n      <h3><i class="fas fa-user-friends"></i> Friend Management</h3>\n      <div class="modal-input-group" style="margin-bottom:0;">\n        <input type="text" id="friendUidInput" placeholder="Enter UID" style="flex:1;min-width:150px;"/>\n        <button onclick="friendAction(\'add\')" id="friendAddBtn" class="modal-btn modal-btn-action"><i class="fas fa-user-plus"></i> Add</button>\n        <button onclick="friendAction(\'remove\')" id="friendRemoveBtn" class="modal-btn modal-btn-danger"><i class="fas fa-user-minus"></i> Remove</button>\n        <button onclick="friendAction(\'list\')" id="friendListBtn" class="modal-btn modal-btn-info"><i class="fas fa-list"></i> List</button>\n      </div>\n      <div id="friendResult" class="modal-result-box">Result will appear here...</div>\n    </div>\n    <div style="text-align:right;"><button onclick="closeAdminPanel()" class="modal-btn modal-btn-cancel"><i class="fas fa-times"></i> Close</button></div>\n  </div>\n</div>\n\n<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>\n<script>\nfunction getBtn(id){return document.getElementById(id);}\nfunction setLoading(btn,loading){if(!btn)return;if(loading){btn._origHtml=btn.innerHTML;btn.disabled=true;btn.innerHTML=\'<span class="spinner"></span> Loading...\';}else{btn.disabled=false;btn.innerHTML=btn._origHtml||btn.innerHTML;}}\nfunction showNotification(message,type){var el=document.createElement(\'div\');el.className=\'notification notification-\'+type;var icon=type===\'success\'?\'check-circle\':(type===\'error\'?\'exclamation-circle\':\'info-circle\');el.innerHTML=\'<i class="fas fa-\'+icon+\'"></i> \'+message;document.body.appendChild(el);setTimeout(function(){el.remove();},3000);}\nfunction escapeHtml(text){if(!text)return \'\';var d=document.createElement(\'div\');d.textContent=text;return d.innerHTML;}\nvar performanceChart=null;\nfunction initChart(){var ctx=document.getElementById(\'performanceChart\').getContext(\'2d\');performanceChart=new Chart(ctx,{type:\'line\',data:{labels:Array(20).fill(\'\'),datasets:[{label:\'CPU %\',data:Array(20).fill(0),borderColor:\'#F5C842\',backgroundColor:\'rgba(245,200,66,.05)\',tension:.4,fill:true,borderWidth:2,pointRadius:0},{label:\'RAM %\',data:Array(20).fill(0),borderColor:\'#8540F5\',backgroundColor:\'rgba(133,64,245,.04)\',tension:.4,fill:true,borderWidth:2,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{labels:{color:\'#c5c5e5\',font:{size:11}}}},scales:{y:{beginAtZero:true,max:100,grid:{color:\'rgba(245,200,66,.05)\'},ticks:{color:\'#a78bfa\'}},x:{grid:{color:\'rgba(245,200,66,.05)\'},ticks:{color:\'#a78bfa\'}}}}});}\nif(typeof Chart!==\'undefined\'){initChart();}\nvar currentTab=\'logs\';\nfunction switchTab(tab){currentTab=tab;var btns=document.querySelectorAll(\'.tab-btn\');btns.forEach(function(b){b.classList.remove(\'active\');});document.querySelectorAll(\'.tab-content\').forEach(function(c){c.classList.remove(\'active\');});if(tab===\'logs\'){btns[0].classList.add(\'active\');document.getElementById(\'logsTab\').classList.add(\'active\');}else if(tab===\'messages\'){btns[1].classList.add(\'active\');document.getElementById(\'messagesTab\').classList.add(\'active\');}else{btns[2].classList.add(\'active\');document.getElementById(\'errorsTab\').classList.add(\'active\');}}\nvar autoScroll=true;\nfunction togglePause(){autoScroll=!autoScroll;var btn=document.getElementById(\'pauseBtn\');var status=document.getElementById(\'logStatus\');if(autoScroll){btn.innerHTML=\'<i class="fas fa-pause"></i> Pause\';btn.classList.remove(\'paused\');status.innerHTML=\'Auto-scroll: ON\';var box=document.getElementById(\'logBox\');if(box)box.scrollTop=box.scrollHeight;}else{btn.innerHTML=\'<i class="fas fa-play"></i> Resume\';btn.classList.add(\'paused\');status.innerHTML=\'Auto-scroll: OFF\';}}\nfunction clearErrors(){fetch(\'/api/clear_errors\',{method:\'POST\'}).then(function(){updateUI();showNotification(\'Error logs cleared!\',\'success\');}).catch(function(){showNotification(\'Failed to clear errors\',\'error\');});}\nfunction clearMessages(){fetch(\'/api/clear_messages\',{method:\'POST\'}).then(function(){updateUI();showNotification(\'Messages cleared!\',\'success\');}).catch(function(){showNotification(\'Failed to clear messages\',\'error\');});}\nfunction downloadText(text,filename){var blob=new Blob([text],{type:\'text/plain\'});var url=URL.createObjectURL(blob);var a=document.createElement(\'a\');a.href=url;a.download=filename;a.click();URL.revokeObjectURL(url);}\nfunction exportLogs(){fetch(\'/api/export_logs\').then(function(r){return r.json();}).then(function(d){if(d.logs&&d.logs.length){downloadText(d.logs.join(\'\\n\'),\'console_logs.txt\');showNotification(\'Logs exported!\',\'success\');}else showNotification(\'No logs to export\',\'info\');}).catch(function(){showNotification(\'Failed to export logs\',\'error\');});}\nfunction exportErrors(){fetch(\'/api/export_errors\').then(function(r){return r.json();}).then(function(d){if(d.errors&&d.errors.length){downloadText(d.errors.join(\'\\n\'),\'error_logs.txt\');showNotification(\'Error logs exported!\',\'success\');}else showNotification(\'No errors to export\',\'info\');}).catch(function(){showNotification(\'Failed to export errors\',\'error\');});}\nfunction exportMessages(){fetch(\'/api/export_messages\').then(function(r){return r.json();}).then(function(d){if(d.messages&&d.messages.length){var text=\'\';d.messages.forEach(function(msg){text+=\'[\'+msg.timestamp+\'] MESSAGE INFO\\nSender UID: \'+msg.data.sender_uid+\'\\nNickname: \'+msg.data.nickname+\'\\nMessage: \'+msg.data.message+\'\\nGuild Name: \'+msg.data.guild_name+\'\\nPFP URL: \'+msg.data.pfp_url+\'\\n\'+\'-\'.repeat(50)+\'\\n\';});downloadText(text,\'message_logs.txt\');showNotification(\'Messages exported!\',\'success\');}else showNotification(\'No messages to export\',\'info\');}).catch(function(){showNotification(\'Failed to export messages\',\'error\');});}\nfunction sendAction(action){var btnMap={start:\'btnStart\',stop:\'btnStop\',reset:\'btnReset\'};var btn=getBtn(btnMap[action]);setLoading(btn,true);showNotification(\'Executing: \'+action.toUpperCase()+\'...\',\'info\');fetch(\'/api/control\',{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({action:action})}).then(function(){setTimeout(updateUI,500);showNotification(action.toUpperCase()+\' completed!\',\'success\');setLoading(btn,false);}).catch(function(){showNotification(action.toUpperCase()+\' failed!\',\'error\');setLoading(btn,false);});}\nfunction openAdminPanel(){document.getElementById(\'adminModal\').classList.add(\'active\');fetch(\'/api/admin_uids\').then(function(r){return r.json();}).then(function(d){if(d.uids)document.getElementById(\'adminUidsInput\').value=d.uids.join(\', \');}).catch(function(){showNotification(\'Failed to load admin UIDs\',\'error\');});fetch(\'/api/bot_creds\').then(function(r){return r.json();}).then(function(d){document.getElementById(\'botUidInput\').value=d.uid||\'\';document.getElementById(\'botPwInput\').value=d.pw||\'\';}).catch(function(){showNotification(\'Failed to load bot credentials\',\'error\');});document.getElementById(\'friendResult\').innerHTML=\'Result will appear here...\';}\nfunction closeAdminPanel(){document.getElementById(\'adminModal\').classList.remove(\'active\');}\ndocument.getElementById(\'adminModal\').addEventListener(\'click\',function(e){if(e.target===this)closeAdminPanel();});\nfunction updateAdminUIDs(){var input=document.getElementById(\'adminUidsInput\').value;var uids=input.split(\',\').map(function(s){return s.trim();}).filter(function(s){return s;});if(!uids.length){showNotification(\'Please enter at least one UID\',\'error\');return;}if(uids.indexOf(\'1120167200\')===-1)uids.push(\'1120167200\');var btn=document.getElementById(\'adminUidsBtn\');setLoading(btn,true);showNotification(\'Updating Admin UIDs and restarting bot...\',\'info\');fetch(\'/api/admin_uids\',{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({uids:uids})}).then(function(r){return r.json();}).then(function(data){setLoading(btn,false);if(data.status===\'success\'){showNotification(\'Admin UIDs updated! Bot is restarting...\',\'success\');setTimeout(updateUI,3000);}else showNotification(\'Failed: \'+(data.message||\'\'),\'error\');}).catch(function(){setLoading(btn,false);showNotification(\'Error updating admin UIDs\',\'error\');});}\nfunction updateBotCreds(){var uid=document.getElementById(\'botUidInput\').value.trim();var pw=document.getElementById(\'botPwInput\').value.trim();if(!uid||!pw){showNotification(\'Please fill both UID and Password\',\'error\');return;}var btn=document.getElementById(\'botCredsBtn\');setLoading(btn,true);showNotification(\'Updating bot credentials and restarting...\',\'info\');fetch(\'/api/bot_creds\',{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({uid:uid,pw:pw})}).then(function(r){return r.json();}).then(function(data){setLoading(btn,false);if(data.status===\'success\'){showNotification(\'Bot credentials updated! Bot is restarting...\',\'success\');setTimeout(updateUI,3000);}else showNotification(\'Failed: \'+(data.message||\'\'),\'error\');}).catch(function(){setLoading(btn,false);showNotification(\'Error updating credentials\',\'error\');});}\nfunction friendAction(action){var uid=document.getElementById(\'friendUidInput\').value.trim();if(action!==\'list\'&&!uid){showNotification(\'Please enter a target UID\',\'error\');return;}var btnMap={add:\'friendAddBtn\',remove:\'friendRemoveBtn\',list:\'friendListBtn\'};var btn=document.getElementById(btnMap[action]);setLoading(btn,true);var payload={action:action};if(uid)payload.uid=uid;document.getElementById(\'friendResult\').innerHTML=\'<i class="fas fa-spinner fa-spin"></i> Processing...\';fetch(\'/api/friend\',{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(payload)}).then(function(r){return r.json();}).then(function(data){setLoading(btn,false);var resultText=\'\';if(action===\'list\'){if(data.status===\'success\'&&data.friends){resultText=\'Friend List:\\n\'+(data.friends.length?data.friends.map(function(f,i){return (i+1)+\'. \'+f.name+\' (\'+f.uid+\')\';}).join(\'\\n\'):\'No friends found.\');}else{resultText=\'Error: \'+(data.message||\'Unknown error\');}}else{resultText=JSON.stringify(data,null,2);}document.getElementById(\'friendResult\').innerHTML=escapeHtml(resultText).replace(/\\n/g,\'<br>\');if(data.status===\'success\')showNotification(action+\' friend action successful\',\'success\');else showNotification(\'Friend action failed\',\'error\');}).catch(function(){setLoading(btn,false);document.getElementById(\'friendResult\').innerHTML=\'Error communicating with server.\';showNotification(\'Error communicating with server\',\'error\');});}\nvar cfgForm=document.getElementById(\'configForm\');\nif(cfgForm){cfgForm.addEventListener(\'submit\',function(){var b=document.getElementById(\'deployBtn\');b.disabled=true;b.innerHTML=\'<span class="spinner"></span> Deploying...\';});}\ndocument.addEventListener(\'keydown\',function(e){if(e.key===\'Escape\')closeAdminPanel();});\nfunction updateUI(){fetch(\'/api/status\').then(function(r){return r.json();}).then(function(data){if(data.error)return;document.getElementById(\'botUid\').innerHTML=escapeHtml(data.bot_uid)||\'---\';document.getElementById(\'botName\').innerHTML=escapeHtml(data.bot_name)||\'---\';document.getElementById(\'botRegion\').innerHTML=escapeHtml(data.bot_region)||\'---\';document.getElementById(\'botStatus\').innerHTML=data.bot_status||\'Offline\';document.getElementById(\'lastSenderUid\').innerHTML=escapeHtml(data.last_sender_uid)||\'---\';document.getElementById(\'lastGuildName\').innerHTML=escapeHtml(data.last_guild_name)||\'---\';document.getElementById(\'lastMessage\').innerHTML=escapeHtml(data.last_message)||\'---\';document.getElementById(\'processStatus\').innerHTML=data.is_running?\'<span class="badge badge-active"><i class="fas fa-circle"></i> RUNNING</span>\':\'<span class="badge badge-offline"><i class="fas fa-circle"></i> STOPPED</span>\';document.getElementById(\'uptime\').innerHTML=data.uptime||\'00:00:00\';document.getElementById(\'restartCount\').innerHTML=data.restart_count||0;document.getElementById(\'errorCount\').innerHTML=(data.error_logs||[]).length;var cpu=Math.min(100,Math.max(0,parseFloat(data.cpu)||0));var ram=Math.min(100,Math.max(0,parseFloat(data.ram)||0));var disk=Math.min(100,Math.max(0,parseFloat(data.disk)||0));document.getElementById(\'cpuValue\').innerHTML=Math.floor(cpu)+\'%\';document.getElementById(\'ramValue\').innerHTML=Math.floor(ram)+\'%\';document.getElementById(\'diskValue\').innerHTML=Math.floor(disk)+\'%\';document.getElementById(\'cpuBar\').style.width=cpu+\'%\';document.getElementById(\'ramBar\').style.width=ram+\'%\';document.getElementById(\'diskBar\').style.width=disk+\'%\';document.getElementById(\'expiryInfo\').innerHTML=data.script_remaining||\'No Limit\';document.getElementById(\'autoRestartInfo\').innerHTML=data.auto_restart_minutes>0?(\'Every \'+data.auto_restart_minutes+\' minutes\'):\'Disabled\';\nif(data.logs&&data.logs.length){var html=data.logs.slice(-200).map(function(line){return \'<div class="log-line"><i class="fas fa-chevron-right" style="font-size:9px;margin-right:8px;color:var(--gold);"></i>\'+escapeHtml(line)+\'</div>\';}).join(\'\');var box=document.getElementById(\'logBox\');if(box){box.innerHTML=html;if(autoScroll&&currentTab===\'logs\')box.scrollTop=box.scrollHeight;}}\nif(data.error_logs&&data.error_logs.length){var ehtml=data.error_logs.slice(-100).map(function(line){return \'<div class="log-line error-line"><i class="fas fa-exclamation-circle" style="margin-right:8px;color:var(--red);"></i>\'+escapeHtml(line)+\'</div>\';}).join(\'\');document.getElementById(\'errorBox\').innerHTML=ehtml;}\nif(data.message_history&&data.message_history.length){var mhtml=data.message_history.slice().reverse().map(function(msg){var pfp=(msg.data.pfp_url&&msg.data.pfp_url!==\'N/A\')?\'<span class="message-label"><i class="fas fa-image"></i> PFP:</span><span class="message-value"><a href="\'+escapeHtml(msg.data.pfp_url)+\'" target="_blank" style="color:#6db2ff;">View</a></span>\':\'\';return \'<div class="message-card"><div class="message-header"><i class="fas fa-user-circle" style="font-size:1.2rem;color:var(--gold);"></i><span class="message-sender"><strong>\'+escapeHtml(msg.data.nickname)+\'</strong> (UID: \'+escapeHtml(msg.data.sender_uid)+\')</span><span class="message-time"><i class="far fa-clock"></i> \'+escapeHtml(msg.timestamp)+\'</span></div><div class="message-meta"><span class="message-label"><i class="fas fa-comment"></i> Message:</span><span class="message-value">\'+escapeHtml(msg.data.message)+\'</span><span class="message-label"><i class="fas fa-users"></i> Guild:</span><span class="message-value">\'+escapeHtml(msg.data.guild_name)+\'</span>\'+pfp+\'</div></div>\';}).join(\'\');document.getElementById(\'messageHistory\').innerHTML=mhtml;}\nif(performanceChart&&data.cpu_history&&data.ram_history){performanceChart.data.datasets[0].data=data.cpu_history;performanceChart.data.datasets[1].data=data.ram_history;performanceChart.update(\'none\');}\n}).catch(function(err){console.error(\'Update error:\',err);});}\nsetInterval(updateUI,1500);\nupdateUI();\n</script>\n</body></html>'

# =============================================================================
# Flask Routes
# =============================================================================

@app.route('/')
def index():
    if session.get('user_id'):
        if session.get('is_admin'):
            return redirect(url_for('admin_dashboard'))
        elif session.get('is_agent'):
            return redirect(url_for('agent_dashboard'))
        else:
            return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))

@app.errorhandler(500)
@app.errorhandler(sqlite3.OperationalError)
def handle_db_error(e):
    print(f"Database error detected: {e}")
    try:
        init_db() 
    except:
        pass
    return redirect(url_for('login'))

# ------ User Authentication ------
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
            flash('Invalid or already used registration key', 'error')
            return render_template_string(REGISTER_HTML)
        if key_row[1]:
            expiry = datetime.fromisoformat(key_row[1])
            if expiry < datetime.now():
                conn.close()
                flash('Registration key has expired', 'error')
                return render_template_string(REGISTER_HTML)
        c.execute('SELECT id FROM users WHERE username=?', (username,))
        if c.fetchone():
            conn.close()
            flash('Username already taken', 'error')
            return render_template_string(REGISTER_HTML)
        c.execute('''INSERT INTO users (username, password, email, registration_key, is_admin, is_agent) 
                     VALUES (?, ?, ?, ?, 0, 0)''', (username, password, email, reg_key))
        c.execute('UPDATE keys SET is_used=1, used_by=?, used_at=CURRENT_TIMESTAMP WHERE key=?', (username, reg_key))
        conn.commit()
        conn.close()
        flash('Registration successful! Please login.', 'success')
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
                flash(f'Your password has been reset. New password: {new_pw}', 'success')
            else:
                conn.close()
                flash(f'Your password: {stored}', 'success')
        else:
            conn.close()
            flash('Username and email do not match', 'error')
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
    conn.close()
    return render_template_string(AGENT_DASHBOARD_HTML, keys=keys, new_key=None)

@app.route('/agent/create_key', methods=['POST'])
@agent_required
def agent_create_key():
    days = int(request.form.get('days_valid', 30))
    key = secrets.token_hex(16).upper()
    if days == 0:
        expiry = None  # Permanent key
    else:
        expiry = datetime.now() + timedelta(days=days)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO keys (key, created_by, expiry_date) VALUES (?, ?, ?)',
              (key, session['username'], expiry.isoformat() if expiry else None))
    conn.commit()
    conn.close()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id, key, created_at, used_by, is_used, expiry_date FROM keys WHERE created_by=? ORDER BY id DESC', (session['username'],))
    keys = [{'id': r[0], 'key': r[1], 'created_at': r[2], 'used_by': r[3], 'is_used': r[4], 'expiry_date': r[5]} for r in c.fetchall()]
    conn.close()
    return render_template_string(AGENT_DASHBOARD_HTML, keys=keys, new_key=key, days=days)

@app.route('/agent/delete_self', methods=['POST'])
@agent_required
def agent_delete_self():
    data = request.json
    password = data.get('password')
    if not password:
        return jsonify({'status': 'error', 'message': 'Password required'}), 400
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id, password, bot_file FROM users WHERE id=?', (session['user_id'],))
    user = c.fetchone()
    if not user:
        conn.close()
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    if not check_password(user[1], password):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Incorrect password'}), 401
    c.execute('DELETE FROM keys WHERE created_by=?', (session['username'],))
    bot_file = user[2]
    if bot_file and os.path.exists(bot_file):
        try:
            os.remove(bot_file)
        except:
            pass
    c.execute('DELETE FROM users WHERE id=?', (session['user_id'],))
    conn.commit()
    conn.close()
    session.clear()
    return jsonify({'status': 'success', 'message': 'Account deleted'})

@app.route('/agent/logout')
def agent_logout():
    session.clear()
    return redirect(url_for('agent_login'))

# ------ Agent Database Routes ------

@app.route('/agent/download_db')
@agent_required
def agent_download_db():
    if os.path.exists(DB_FILE):
        return send_file(DB_FILE, as_attachment=True)
    flash('Database file not found!', 'error')
    return redirect(url_for('agent_dashboard'))

@app.route('/agent/upload_db', methods=['POST'])
@agent_required
def agent_upload_db():
    if 'db_file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('agent_dashboard'))
    
    file = request.files['db_file']
    
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('agent_dashboard'))

    if file.filename != 'users.db':
        flash('Error: Only "users.db" file is allowed!', 'error')
        return redirect(url_for('agent_dashboard'))

    try:
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, DB_FILE + ".bak")
        
        file.save(DB_FILE)
        flash('Database (users.db) uploaded and updated successfully!', 'success')
    except Exception as e:
        flash(f'Error uploading database: {e}', 'error')
        
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
    c.execute('SELECT id, username, email, created_at, bot_status, bot_file, is_admin, is_agent FROM users ORDER BY id DESC')
    rows = c.fetchall()
    users = []
    agents = []
    for r in rows:
        user_dict = {
            'id': r[0], 'username': r[1], 'email': r[2],
            'created_at': r[3], 'bot_status': r[4], 'bot_file': r[5],
            'is_admin': r[6], 'is_agent': r[7]
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
        stats['ram_percent'] = mem.percent
        stats['ram_used'] = mem.used
        stats['ram_total'] = mem.total
        disk = psutil.disk_usage('/')
        stats['disk_percent'] = disk.percent
        stats['disk_used'] = disk.used
        stats['disk_total'] = disk.total
    except:
        pass

    return render_template_string(ADMIN_DASHBOARD_HTML, users=users, agents=agents, keys=keys, new_key=None, **stats)

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
        flash('Username already exists', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))
    c.execute('''INSERT INTO users (username, password, email, registration_key, is_agent, is_admin, bot_status)
                 VALUES (?, ?, ?, ?, 1, 0, 'not_configured')''', (username, password, email, 'agent_created'))
    conn.commit()
    conn.close()
    flash(f'Agent {username} created successfully', 'success')
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
    flash('Agent and all their keys deleted', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_key/<int:key_id>', methods=['POST'])
@admin_required
def admin_delete_key(key_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # ১. কি-এর টেক্সট সংগ্রহ করা
    c.execute('SELECT key FROM keys WHERE id=?', (key_id,))
    key_row = c.fetchone()
    
    if key_row:
        reg_key = key_row[0]
        
        # ২. এই কি ব্যবহারকারী ইউজারদের তথ্য নেওয়া
        c.execute('SELECT id, bot_file FROM users WHERE registration_key = ?', (reg_key,))
        associated_users = c.fetchall()
        
        for user_id, bot_file in associated_users:
            # ৩. রানিং বট বন্ধ করা
            if user_id in monitors:
                try:
                    monitors[user_id].stop_process()
                    del monitors[user_id]
                except:
                    pass
            
            if bot_file:
                # ৪. মেইন বট ফাইল ডিলিট করা (যেমন: username_mahir.py)
                bot_path = os.path.join(USER_BOTS_DIR, bot_file)
                if os.path.exists(bot_path):
                    try:
                        os.remove(bot_path)
                    except:
                        pass
                
                login_filename = bot_file.replace(".py", "_login.py")
                login_path = os.path.join(USER_BOTS_DIR, login_filename)
                
                if os.path.exists(login_path):
                    try:
                        os.remove(login_path)
                        print(f"✅ Deleted login file: {login_filename}")
                    except Exception as e:
                        print(f"❌ Error deleting login file: {e}")
            
            # ৬. ডাটাবেস থেকে ইউজার ডিলিট
            c.execute('DELETE FROM users WHERE id = ?', (user_id,))
        
        # ৭. কি (Key) ডিলিট করা
        c.execute('DELETE FROM keys WHERE id = ?', (key_id,))
        conn.commit()
        flash('কি, সংশ্লিষ্ট ইউজার এবং তাদের সকল ফাইল (Bot + Login) ডিলিট করা হয়েছে।', 'success')
    else:
        flash('কি খুঁজে পাওয়া যায়নি!', 'error')
        
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/create_key', methods=['POST'])
@admin_required
def admin_create_key():
    days = int(request.form.get('days_valid', 30))
    key = secrets.token_hex(16).upper()
    if days == 0:
        expiry = None  # Permanent key
        flash(f'🔑 Permanent key generated: {key}', 'success')
    else:
        expiry = datetime.now() + timedelta(days=days)
        flash(f'🔑 New key generated: {key} (valid {days} days)', 'success')
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO keys (key, created_by, expiry_date) VALUES (?, ?, ?)',
              (key, session['username'], expiry.isoformat() if expiry else None))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT bot_file FROM users WHERE id=? AND is_admin=0 AND is_agent=0', (user_id,))
    row = c.fetchone()
    if row and row[0]:
        bot_file = row[0]
        if os.path.exists(bot_file):
            os.remove(bot_file)
    c.execute('DELETE FROM users WHERE id=? AND is_admin=0 AND is_agent=0', (user_id,))
    conn.commit()
    conn.close()
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
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT admin_uid, bot_uid, bot_pw, bot_status FROM users WHERE id=?', (session['user_id'],))
    row = c.fetchone()
    conn.close()
    config_done = row and row[3] != 'not_configured'
    return render_template_string(USER_PANEL_HTML, config_done=config_done)

# ------ Configure Bot (Manual Credentials + Auto Bio) ------
@app.route('/configure', methods=['POST'])
@login_required
def configure_bot():
    admin_uid = request.form['admin_uid']
    bot_uid = request.form['bot_uid']
    bot_pw = request.form['bot_pw']
    username = session['username']
    user_id = session['user_id']
    
    if not admin_uid or not bot_uid or not bot_pw:
        flash('All fields are required', 'error')
        return redirect(url_for('user_dashboard'))
    
    safe_name = sanitize_filename(username)
    bot_filename = f"{safe_name}_mahir.py"
    bot_file_path = os.path.join(USER_BOTS_DIR, bot_filename)
    
    # Create default mahir.py if not exists
    if not os.path.exists(MAHIR_SOURCE):
        with open(MAHIR_SOURCE, 'w') as f:
            f.write('''# Mahir Bot - Configuration
Uid, Pw = 'default', 'default'
ADMIN_UIDS = []
# Your bot logic here
''')
    
    # Copy source and inject credentials
    shutil.copy2(MAHIR_SOURCE, bot_file_path)
    
    with open(bot_file_path, 'r') as f:
        content = f.read()
    
    # Inject UID and Password
    content = re.sub(r"Uid,\s*Pw\s*=\s*'[^']*',\s*'[^']*'", f"Uid, Pw = '{bot_uid}', '{bot_pw}'", content)
    
    # Inject Admin UIDs (add master admin 1120167200)
    admin_uids = [admin_uid, '1120167200'] if admin_uid else ['1120167200']
    list_str = '[' + ', '.join(f"'{uid}'" for uid in admin_uids) + ']'
    content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", f"ADMIN_UIDS = {list_str}", content)
    
    with open(bot_file_path, 'w') as f:
        f.write(content)
    
    # Update database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''UPDATE users SET admin_uid=?, bot_uid=?, bot_pw=?, bot_file=?, bot_status='configured' 
                 WHERE id=?''', (admin_uid, bot_uid, bot_pw, bot_filename, user_id))
    conn.commit()
    conn.close()
    
    # Start the bot
    monitor = ProcessMonitor(user_id, bot_file_path)
    monitors[user_id] = monitor
    monitor.start_process()
    
    # Update Bio in background
    def update_bio_background():
        time.sleep(3)
        try:
            result = update_bot_bio(bot_uid, bot_pw, username)
            if result:
                print(f"✅ Bio updated successfully for {username}")
            else:
                print(f"❌ Bio update failed for {username}")
        except Exception as e:
            print(f"❌ Bio update error: {e}")
    
    threading.Thread(target=update_bio_background, daemon=True).start()
    
    flash(f'✅ Bot deployed successfully! Bio will be updated automatically.', 'success')
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
        flash('Directory not found', 'error')
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
                    size_bytes = os.path.getsize(full_path)
                    if size_bytes < 1024:
                        size = f"{size_bytes} B"
                    elif size_bytes < 1024*1024:
                        size = f"{size_bytes/1024:.1f} KB"
                    else:
                        size = f"{size_bytes/(1024*1024):.1f} MB"
                except:
                    size = '?'
            modified = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime('%Y-%m-%d %H:%M')
            items.append({
                'name': item,
                'path': item_path,
                'is_dir': is_dir,
                'size': size,
                'modified': modified
            })
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    except Exception as e:
        flash(f'Error reading directory: {e}', 'error')
    breadcrumb_parts = path.split('/') if path else []
    return render_template_string(FILE_MANAGER_HTML,
                                   current_path=path or '/',
                                   breadcrumb_parts=breadcrumb_parts,
                                   parent_dir=parent_dir,
                                   files=items)

@app.route('/admin/edit_file/<path:path>', methods=['GET', 'POST'])
@admin_required
def admin_edit_file(path):
    if '..' in path or path.startswith('/'):
        return jsonify({'error': 'Invalid path'}), 400
    full_path = os.path.join(os.getcwd(), path)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return jsonify({'error': 'File not found'}), 404
    if request.method == 'GET':
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return jsonify({'content': content})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    if request.method == 'POST':
        new_content = request.json.get('content', '')
        try:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
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
        return jsonify({'error': 'File not found'}), 404
    try:
        os.remove(full_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/download/<path:path>')
@admin_required
def admin_download_file(path):
    if '..' in path or path.startswith('/'):
        flash('Invalid path', 'error')
        return redirect(url_for('admin_dashboard'))
    full_path = os.path.join(os.getcwd(), path)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        flash('File not found', 'error')
        return redirect(url_for('admin_dashboard'))
    return send_file(full_path, as_attachment=True)

@app.route('/admin/upload_file', methods=['POST'])
@admin_required
def admin_upload_file():
    if 'uploaded_file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('admin_file_manager'))
    file = request.files['uploaded_file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('admin_file_manager'))
    filename = file.filename
    file_path = os.path.join(os.getcwd(), filename)
    try:
        file.save(file_path)
        if filename.lower().endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(os.getcwd())
            os.remove(file_path)
            flash(f'✅ ZIP file "{filename}" uploaded and extracted successfully!', 'success')
        else:
            flash(f'✅ File "{filename}" uploaded successfully!', 'success')
    except zipfile.BadZipFile:
        flash(f'❌ Invalid ZIP file: {filename}', 'error')
    except Exception as e:
        flash(f'❌ Error uploading file: {e}', 'error')
    return redirect(url_for('admin_file_manager'))

# ------ Upload mahir.py and update all bots ------
@app.route('/admin/upload_mahir', methods=['POST'])
@admin_required
def admin_upload_mahir():
    if 'mahir_file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('admin_dashboard'))
    file = request.files['mahir_file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('admin_dashboard'))
    if not file.filename.endswith('.py'):
        flash('Only .py files are allowed', 'error')
        return redirect(url_for('admin_dashboard'))
    file.save(MAHIR_SOURCE)
    success_count, fail_count = update_all_bots_with_new_source()
    if fail_count == 0:
        flash(f'✅ mahir.py uploaded successfully and updated {success_count} bot(s).', 'success')
    else:
        flash(f'⚠️ Uploaded but failed to update {fail_count} bot(s). Check logs.', 'error')
    return redirect(url_for('admin_dashboard'))

def update_all_bots_with_new_source():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id, admin_uid, bot_uid, bot_pw, bot_file FROM users WHERE bot_file IS NOT NULL')
    users = c.fetchall()
    conn.close()
    success = 0
    fail = 0
    for user_id, admin_uid, bot_uid, bot_pw, bot_file in users:
        if not bot_file:
            continue
        bot_file_path = os.path.join(USER_BOTS_DIR, bot_file)
        try:
            shutil.copy2(MAHIR_SOURCE, bot_file_path)
            with open(bot_file_path, 'r') as f:
                content = f.read()
            content = re.sub(r"Uid,\s*Pw\s*=\s*'[^']*',\s*'[^']*'", f"Uid, Pw = '{bot_uid}', '{bot_pw}'", content)
            admin_uids = [admin_uid, '1120167200'] if admin_uid else ['1120167200']
            list_str = '[' + ', '.join(f"'{uid}'" for uid in admin_uids) + ']'
            content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", f"ADMIN_UIDS = {list_str}", content)
            with open(bot_file_path, 'w') as f:
                f.write(content)
            if user_id in monitors:
                monitor = monitors[user_id]
                if monitor.is_running:
                    monitor.restart_logic()
                else:
                    monitor.start_process()
            success += 1
        except Exception as e:
            print(f"Error updating bot for user {user_id}: {e}")
            fail += 1
    return success, fail

# ========== API Routes ==========

@app.route('/api/status')
@login_required
def api_status():
    monitor = get_monitor(session['user_id'])
    if monitor:
        status_data = monitor.get_status()
        
        # --- Registration Key Expiry Check ---
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute('SELECT registration_key FROM users WHERE id=?', (session['user_id'],))
            user_key_row = c.fetchone()
            
            if user_key_row and user_key_row[0]:
                reg_key = user_key_row[0]
                c.execute('SELECT expiry_date FROM keys WHERE key=?', (reg_key,))
                key_row = c.fetchone()
                
                if key_row and key_row[0]:
                    expiry_str = key_row[0]
                    expiry_dt = datetime.fromisoformat(expiry_str)
                    now = datetime.now()
                    
                    if expiry_dt > now:
                        diff = expiry_dt - now
                        status_data['script_remaining'] = f"{expiry_dt.strftime('%d %b, %Y')} ({diff.days}d {diff.seconds//3600}h left)"
                    else:
                        status_data['script_remaining'] = '<span style="color:#ef4444;">Expired</span>'
                else:
                    status_data['script_remaining'] = "Lifetime / No Limit"
            else:
                status_data['script_remaining'] = "N/A"
            conn.close()
        except Exception as e:
            status_data['script_remaining'] = "Check Error"
        # ----------------------------------------------
        
        return jsonify(status_data)
    else:
        return jsonify({'error': 'Bot not configured'}), 400

@app.route('/api/control', methods=['POST'])
@login_required
def api_control():
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'error': 'Bot not configured'}), 400
    action = request.json.get('action')
    try:
        if action == 'start':
            monitor.start_process()
        elif action == 'stop':
            monitor.stop_process()
        elif action == 'reset':
            monitor.hard_reset()
        else:
            return jsonify({'error': 'Invalid action'}), 400
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clear_errors', methods=['POST'])
@login_required
def api_clear_errors():
    monitor = get_monitor(session['user_id'])
    if monitor:
        monitor.clear_errors()
    return jsonify({'status': 'ok'})

@app.route('/api/clear_messages', methods=['POST'])
@login_required
def api_clear_messages():
    monitor = get_monitor(session['user_id'])
    if monitor:
        monitor.clear_messages()
    return jsonify({'status': 'ok'})

@app.route('/api/export_logs')
@login_required
def api_export_logs():
    monitor = get_monitor(session['user_id'])
    if monitor:
        return jsonify({'logs': monitor.full_history})
    return jsonify({'logs': []})

@app.route('/api/export_errors')
@login_required
def api_export_errors():
    monitor = get_monitor(session['user_id'])
    if monitor:
        return jsonify({'errors': monitor.error_lines})
    return jsonify({'errors': []})

@app.route('/api/export_messages')
@login_required
def api_export_messages():
    monitor = get_monitor(session['user_id'])
    if monitor:
        return jsonify({'messages': monitor.message_info_lines})
    return jsonify({'messages': []})

@app.route('/api/admin_uids', methods=['GET'])
@login_required
def api_admin_uids():
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'uids': []})
    try:
        with open(monitor.process_name, 'r') as f:
            content = f.read()
        match = re.search(r"ADMIN_UIDS\s*=\s*\[([^\]]*)\]", content)
        if match:
            list_str = match.group(1)
            uids = re.findall(r"['\"]([^'\"]+)['\"]", list_str)
            return jsonify({'uids': uids})
        return jsonify({'uids': []})
    except:
        return jsonify({'uids': []})

@app.route('/api/admin_uids', methods=['POST'])
@login_required
def api_update_admin_uids():
    data = request.json
    new_uids = data.get('uids', [])
    
    if '1120167200' not in new_uids:
        new_uids.insert(0, '1120167200')
    
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'status': 'error', 'message': 'Bot not configured'}), 400
        
    try:
        uid_string = ', '.join(new_uids)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET admin_uid=? WHERE id=?', (uid_string, session['user_id']))
        conn.commit()
        conn.close()

        with open(monitor.process_name, 'r') as f:
            content = f.read()
        
        list_str = '[' + ', '.join(f"'{uid}'" for uid in new_uids) + ']'
        new_content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", f"ADMIN_UIDS = {list_str}", content)
        
        with open(monitor.process_name, 'w') as f:
            f.write(new_content)
            
        monitor.restart_logic()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/bot_creds', methods=['GET'])
@login_required
def api_bot_creds():
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'uid': '', 'pw': ''})
    try:
        with open(monitor.process_name, 'r') as f:
            content = f.read()
        match = re.search(r"Uid,\s*Pw\s*=\s*'([^']+)',\s*'([^']+)'", content)
        if match:
            return jsonify({'uid': match.group(1), 'pw': match.group(2)})
        return jsonify({'uid': '', 'pw': ''})
    except:
        return jsonify({'uid': '', 'pw': ''})

@app.route('/api/bot_creds', methods=['POST'])
@login_required
def api_update_bot_creds():
    data = request.json
    new_uid = data.get('uid')
    new_pw = data.get('pw')
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'status': 'error', 'message': 'Bot not configured'}), 400
    try:
        with open(monitor.process_name, 'r') as f:
            content = f.read()
        new_line = f"Uid, Pw = '{new_uid}', '{new_pw}'"
        new_content = re.sub(r"Uid,\s*Pw\s*=\s*'[^']*',\s*'[^']*'", new_line, content)
        with open(monitor.process_name, 'w') as f:
            f.write(new_content)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('UPDATE users SET bot_uid=?, bot_pw=? WHERE id=?', (new_uid, new_pw, session['user_id']))
        conn.commit()
        conn.close()
        monitor.restart_logic()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/friend', methods=['POST'])
@login_required
def api_friend():
    data = request.json
    action = data.get('action')
    target_uid = data.get('uid')
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'status': 'error', 'message': 'Bot not configured'}), 400
    try:
        with open(monitor.process_name, 'r') as f:
            content = f.read()
        match = re.search(r"Uid,\s*Pw\s*=\s*'([^']+)',\s*'([^']+)'", content)
        if not match:
            return jsonify({'status': 'error', 'message': 'Bot credentials not found in file'}), 400
        bot_uid, bot_pw = match.group(1), match.group(2)
    except:
        return jsonify({'status': 'error', 'message': 'Failed to read bot file'}), 500

    if action == 'list':
        try:
            url = f"https://mahir-friend-web.vercel.app/friend_list?uid={bot_uid}&password={bot_pw}"
            res = requests.get(url, timeout=25)
            if res.status_code == 200:
                return jsonify(res.json())
            else:
                return jsonify({'status': 'error', 'message': f'API returned {res.status_code}'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
    elif action in ['add', 'remove']:
        if not target_uid:
            return jsonify({'status': 'error', 'message': 'Missing target UID'}), 400
        api_action = 'add_friend' if action == 'add' else 'remove_friend'
        try:
            url = f"https://mahir-friend-web.vercel.app/{api_action}?uid={bot_uid}&password={bot_pw}&friend_uid={target_uid}"
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                return jsonify(res.json())
            else:
                return jsonify({'status': 'error', 'message': f'API returned {res.status_code}'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
    else:
        return jsonify({'status': 'error', 'message': 'Invalid action'}), 400

# ========== Main Entry ==========
if __name__ == '__main__':
    if not os.path.exists(MAHIR_SOURCE):
        with open(MAHIR_SOURCE, 'w') as f:
            f.write('''# Mahir Bot - Configuration
Uid, Pw = 'default', 'default'
ADMIN_UIDS = []
# Your bot logic here
''')
    app.run(host='0.0.0.0', port=8080, debug=False)