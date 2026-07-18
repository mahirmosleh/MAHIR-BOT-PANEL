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
import hmac
import base64
import random
import string
import asyncio
from datetime import datetime, timedelta
from queue import Queue, Empty
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, flash, send_file
from functools import wraps
import psutil
import requests

# ========== Dependency Check ==========
def install_dependencies():
    required_pkgs = ["colorama", "psutil", "flask", "requests", "pycryptodome", "protobuf"]
    for pkg in required_pkgs:
        try:
            __import__(pkg)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

install_dependencies()

from colorama import init, Fore, Style
init(autoreset=True)

# ========== Additional Imports ==========
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    AES_AVAILABLE = True
except:
    AES_AVAILABLE = False

# Protobuf modules – if missing, fallback to text parsing
try:
    import MajoRLoGinrEq_pb2
    import MajoRLoGinrEs_pb2
    import PorTs_pb2
    NEW_PROTO_AVAILABLE = True
except ImportError:
    NEW_PROTO_AVAILABLE = False

# ========== Flask Configuration ==========
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

DB_FILE = "users.db"
MAHIR_SOURCE = "mahir.py"
USER_BOTS_DIR = "."

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
        bot_status TEXT DEFAULT 'not_configured'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        used_by TEXT,
        used_at TIMESTAMP,
        is_used INTEGER DEFAULT 0,
        expiry_date TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

# ========== Helper Functions ==========
def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)

def is_hashed(pw):
    return len(pw) == 64 and all(c in '0123456789abcdefABCDEF' for c in pw)

def check_password(stored, provided):
    if is_hashed(stored):
        return stored == hashlib.sha256(provided.encode()).hexdigest()
    else:
        return stored == provided

# ========== Debug Print Function ==========
def debug_print(msg):
    """Print debug messages with timestamp."""
    if True:  # Set to False to disable debug output
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"[{timestamp}] 🔍 {msg}")

# ========== Bio Update (via MAHIR API) ==========
def update_bot_bio(uid, password, username):
    """Update bot bio via MAHIR long-bio API."""
    bio_text = f"[c][b][i][00BFFF]{username} [00FF00]বটে আপনাকে স্বাগতম। [FFFF00]নিজের জন্য এমন একটি Bot কিনতে চাইলে যোগাযোগ করুন আমাদের WEBSITE NAME: [00FFFF]MAHIR.XO.JE "
    encoded = requests.utils.quote(bio_text)
    url = f"https://mahir-long-bio.vercel.app/bio_upload?bio={encoded}&uid={uid}&pass={password}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            print(f"Bio updated for {uid}: {resp.text}")
        else:
            print(f"Bio update failed for {uid}: {resp.status_code}")
    except Exception as e:
        print(f"Bio update exception for {uid}: {e}")

# =============================================================================
# 🔥 ACCOUNT GENERATION LOGIC (FULL – from mahir.py)
# =============================================================================

# ---- Configuration ----
GEN_CONFIG = {
    "HEX_KEY": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
    "API_KEY": bytes.fromhex("2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"),
    "REGION": "BD",
    "REGION_LANG": {"BD": "bn"},
    "ACTIVATION_REGIONS": {
        'BD': {
            'guest_url': 'https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant',
            'major_login_url': 'https://loginbp.ggpolarbear.com/MajorLogin',
            'get_login_data_url': 'https://clientbp.ggpolarbear.com/GetLoginData',
            'client_host': 'clientbp.ggpolarbear.com'
        }
    },
    "REGISTER_URL": "https://100067.connect.garena.com/api/v2/oauth/guest:register",
    "TOKEN_URL": "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant",
    "MAJOR_REGISTER_URL": "https://loginbp.ggpolarbear.com/MajorRegister",
    "MAJOR_LOGIN_URL": "https://loginbp.ggpolarbear.com/MajorLogin",
}

# ---- Helper functions (copied from mahir.py) ----
def generate_custom_password():
    return ''.join(random.choice('0123456789ABCDEF') for _ in range(64))

def generate_random_name(prefix="MAHIR"):
    designs = ['▲','ℳ','☆','°','ℛ','『','ツ','◇','༺','◆','웃','꧁','彡','★','ン',
               '•','乂','⍤','유','ヅ','Ø','♪','Ƹ','⌂','シ','⊹','·','∞','♡','✦',
               '✧','◈','▸','꧂','༻','࿐','ʜ','ɪ','ᴋ','ᴍ','ɴ','ꪆ','ꪀ','』','「','」',
               '〖','〗','【','】','《','》','ッ','ジ','ヅ','亗','ℳ','ℛ','Ɽ','Ƈ','Ƨ',
               'Ƴ','Ʀ','Ƶ','⋆','⋈']
    count = random.randint(3, 4)
    suffix = ''.join(random.choices(designs, k=count))
    return f"{prefix}{suffix}"

def smart_delay():
    time.sleep(random.uniform(0.01, 0.05))

def decode_jwt_token(jwt_token):
    try:
        parts = jwt_token.split('.')
        if len(parts) >= 2:
            payload_part = parts[1]
            padding = 4 - len(payload_part) % 4
            if padding != 4:
                payload_part += '=' * padding
            decoded = base64.urlsafe_b64decode(payload_part)
            data = json.loads(decoded)
            account_id = data.get('account_id') or data.get('external_id')
            if account_id:
                return str(account_id)
    except:
        pass
    return "N/A"

def encrypt_api(plain_text):
    if not AES_AVAILABLE:
        return plain_text
    Z = bytes.fromhex(plain_text)
    key = bytes([89,103,38,116,99,37,68,69,117,104,54,37,90,99,94,56])
    iv  = bytes([54,111,121,90,68,114,50,50,69,51,121,99,104,106,77,37])
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad(Z, AES.block_size)).hex()

# ---- Async protobuf helpers ----
async def EnC_Vr(N):
    if N < 0: return b''
    H = []
    while True:
        BesTo = N & 0x7F
        N >>= 7
        if N: BesTo |= 0x80
        H.append(BesTo)
        if not N: break
    return bytes(H)

async def CrEaTe_VarianT(field_number, value):
    return await EnC_Vr((field_number << 3) | 0) + await EnC_Vr(value)

async def CrEaTe_LenGTh(field_number, value):
    h = await EnC_Vr((field_number << 3) | 2)
    e = value.encode() if isinstance(value, str) else value
    return h + await EnC_Vr(len(e)) + e

async def CrEaTe_ProTo(fields):
    p = bytearray()
    for f, v in fields.items():
        if isinstance(v, dict):
            p.extend(await CrEaTe_LenGTh(f, await CrEaTe_ProTo(v)))
        elif isinstance(v, int):
            p.extend(await CrEaTe_VarianT(f, v))
        elif isinstance(v, (str, bytes)):
            p.extend(await CrEaTe_LenGTh(f, v))
    return p

def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

def E_AEs(Pc):
    if not AES_AVAILABLE:
        return bytes.fromhex(Pc)
    Z = bytes.fromhex(Pc)
    key = bytes([89,103,38,116,99,37,68,69,117,104,54,37,90,99,94,56])
    iv  = bytes([54,111,121,90,68,114,50,50,69,51,121,99,104,106,77,37])
    K = AES.new(key, AES.MODE_CBC, iv)
    return K.encrypt(pad(Z, AES.block_size))

# ---- Protobuf MajorLogin builder ----
def _encrypt_major_login_proto(open_id, access_token):
    if not NEW_PROTO_AVAILABLE or not AES_AVAILABLE:
        return None
    try:
        major_login = MajoRLoGinrEq_pb2.MajorLogin()
        major_login.event_time = str(datetime.now())[:-7]
        major_login.game_name = "free fire"
        major_login.platform_id = 2
        major_login.client_version = "1.128.2"
        major_login.client_version_code = "2024010012"
        major_login.system_software = "Android OS 11 / API-30 (RQ3A.210805.001)"
        major_login.system_hardware = "Handheld"
        major_login.device_type = "Handheld"
        major_login.telecom_operator = "Verizon"
        major_login.network_operator_a = "Verizon"
        major_login.network_type = "WIFI"
        major_login.network_type_a = "WIFI"
        major_login.screen_width = 1080
        major_login.screen_height = 2400
        major_login.screen_dpi = "440"
        major_login.processor_details = "ARMv8"
        major_login.memory = 6144
        major_login.gpu_renderer = "Adreno (TM) 650"
        major_login.gpu_version = "OpenGL ES 3.2 V@1.50"
        major_login.graphics_api = "OpenGLES3"
        major_login.unique_device_id = "Google|34a7dcdf-a7d5-4cb6-8d7e-3b0e448a0c57"
        major_login.language = "en"
        major_login.open_id = open_id
        major_login.open_id_type = "4"
        major_login.login_open_id_type = 4
        major_login.access_token = access_token
        major_login.login_by = 3
        major_login.platform_sdk_id = 2
        major_login.origin_platform_type = "4"
        major_login.primary_platform_type = "4"
        major_login.memory_available.version = 55
        major_login.memory_available.hidden_value = 81
        major_login.external_storage_total = 128512
        major_login.external_storage_available = random.randint(38000, 52000)
        major_login.internal_storage_total = 110731
        major_login.internal_storage_available = random.randint(18000, 32000)
        major_login.game_disk_storage_total = 26628
        major_login.game_disk_storage_available = random.randint(18000, 25000)
        major_login.external_sdcard_total_storage = 119234
        major_login.external_sdcard_avail_storage = random.randint(25000, 60000)
        major_login.library_path = "/data/app/com.dts.freefireth/base.apk"
        major_login.library_token = "5b892aaabd688e571f688053118a162b|/data/app/com.dts.freefireth/base.apk"
        major_login.client_using_version = "7428b253defc164018c604a1ebbfebdf"
        major_login.supported_astc_bitset = 16383
        major_login.analytics_detail = b"FwQVTgUPX1UaUllDDwcWCRBpWAUOUgsvA1snWlBaO1kFYg=="
        major_login.loading_time = random.randint(9000, 18000)
        major_login.release_channel = "android"
        major_login.if_push = 1
        major_login.is_vpn = 0
        major_login.cpu_type = 2
        major_login.cpu_architecture = "64"
        major_login.android_engine_init_flag = 110009

        serialized = major_login.SerializeToString()
        key_b = bytes([89,103,38,116,99,37,68,69,117,104,54,37,90,99,94,56])
        iv_b  = bytes([54,111,121,90,68,114,50,50,69,51,121,99,104,106,77,37])
        cipher = AES.new(key_b, AES.MODE_CBC, iv_b)
        return cipher.encrypt(pad(serialized, AES.block_size))
    except Exception as e:
        print(f"Proto MajorLogin error: {e}")
        return None

# ---- MajorLogin sync ----
def _perform_major_login_sync(uid, password, access_token, open_id, region, session):
    url = GEN_CONFIG["MAJOR_LOGIN_URL"]
    headers = {
        "Accept-Encoding": "gzip",
        "Authorization": "Bearer",
        "Connection": "Keep-Alive",
        "Content-Type": "application/x-www-form-urlencoded",
        "Expect": "100-continue",
        "Host": "loginbp.ggpolarbear.com",
        "ReleaseVersion": "OB54",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; SM-G973F Build/RP1A.200720.012)",
        "X-GA": "v1 1",
        "X-Unity-Version": "2018.4.11f1",
    }

    final_payload = None
    if NEW_PROTO_AVAILABLE and AES_AVAILABLE:
        try:
            final_payload = _encrypt_major_login_proto(open_id, access_token)
        except:
            final_payload = None

    if final_payload is None:
        # Fallback to legacy static payload (copied from mahir.py)
        try:
            lang = "bn"
            payload_parts = [
                b'\x1a\x132025-08-30 05:19:21\"\tfree fire(\x01:\x081.114.13B2Android OS 9 / API-28 (PI/rel.cjw.20220518.114133)J\x08HandheldR\nATM MobilsZ\x04WIFI`\xb6\nh\xee\x05r\x03300z\x1fARMv7 VFPv3 NEON VMH | 2400 | 2\x80\x01\xc9\x0f\x8a\x01\x0fAdreno (TM) 640\x92\x01\rOpenGL ES 3.2\x9a\x01+Google|dfa4ab4b-9dc4-454e-8065-e70c733fa53f\xa2\x01\x0e105.235.139.91\xaa\x01\x02',
                lang.encode("ascii"),
                b'\xb2\x01 1d8ec0240ede109973f3321b9354b44d\xba\x01\x014\xc2\x01\x08Handheld\xca\x01\x10Asus ASUS_I005DA\xea\x01@afcfbf13334be42036e4f742c80b956344bed760ac91b3aff9b607a610ab4390\xf0\x01\x01\xca\x02\nATM Mobils\xd2\x02\x04WIFI\xca\x03 7428b253defc164018c604a1ebbfebdf\xe0\x03\xa8\x81\x02\xe8\x03\xf6\xe5\x01\xf0\x03\xaf\x13\xf8\x03\x84\x07\x80\x04\xe7\xf0\x01\x88\x04\xa8\x81\x02\x90\x04\xe7\xf0\x01\x98\x04\xa8\x81\x02\xc8\x04\x01\xd2\x04=/data/app/com.dts.freefireth-PdeDnOilCSFn37p1AH_FLg==/lib/arm\xe0\x04\x01\xea\x04_2087f61c19f57f2af4e7feff0b24d9d9|/data/app/com.dts.freefireth-PdeDnOilCSFn37p1AH_FLg==/base.apk\xf0\x04\x03\xf8\x04\x01\x8a\x05\x0232\x9a\x05\n2019118693\xb2\x05\tOpenGLES2\xb8\x05\xff\x7f\xc0\x05\x04\xe0\x05\xf3F\xea\x05\x07android\xf2\x05pKqsHT5ZLWrYljNb5Vqh//yFRlaPHSO9NWSQsVvOmdhEEn7W+VHNUK+Q+fduA3ptNrGB0Ll0LRz3WW0jOwesLj6aiU7sZ40p8BfUE/FI/jzSTwRe2\xf8\x05\xfb\xe4\x06\x88\x06\x01\x90\x06\x01\x9a\x06\x014\xa2\x06\x014\xb2\x06"GQ@O\x00\x0e^\x00D\x06UA\x0ePM\r\x13hZ\x07T\x06\x0cm\\V\x0ejYV;\x0bU5'
            ]
            payload_bytes = b''.join(payload_parts)
            payload_bytes = payload_bytes.replace(
                b'afcfbf13334be42036e4f742c80b956344bed760ac91b3aff9b607a610ab4390',
                access_token.encode()
            )
            payload_bytes = payload_bytes.replace(b'1d8ec0240ede109973f3321b9354b44d', open_id.encode())
            d = encrypt_api(payload_bytes.hex())
            if d:
                final_payload = bytes.fromhex(d)
        except:
            final_payload = None

    if final_payload is None:
        return {"account_id": "N/A", "jwt_token": "", "ml_key": None, "ml_iv": None, "ml_timestamp": None, "ml_url": None}

    try:
        response = session.post(url, headers=headers, data=final_payload, verify=False, timeout=15)
        if response.status_code == 200 and len(response.content) > 10:
            if NEW_PROTO_AVAILABLE:
                try:
                    res = MajoRLoGinrEs_pb2.MajorLoginRes()
                    res.ParseFromString(response.content)
                    if res.token:
                        account_id = str(res.account_uid) if res.account_uid else decode_jwt_token(res.token)
                        key_bytes = bytes(res.key) if res.key else None
                        iv_bytes  = bytes(res.iv)  if res.iv  else None
                        return {
                            "account_id": account_id,
                            "jwt_token":  res.token,
                            "ml_key":     key_bytes,
                            "ml_iv":      iv_bytes,
                            "ml_timestamp": str(res.timestamp) if res.timestamp else None,
                            "ml_url":     res.url if res.url else None,
                        }
                except:
                    pass
            # Text fallback
            text = response.text
            jwt_start = text.find("eyJ")
            if jwt_start != -1:
                jwt_token = text[jwt_start:]
                second_dot = jwt_token.find(".", jwt_token.find(".") + 1)
                if second_dot != -1:
                    jwt_token = jwt_token[:second_dot + 44]
                    account_id = decode_jwt_token(jwt_token)
                    return {"account_id": account_id, "jwt_token": jwt_token,
                            "ml_key": None, "ml_iv": None,
                            "ml_timestamp": None, "ml_url": None}
    except:
        pass
    return {"account_id": "N/A", "jwt_token": "", "ml_key": None, "ml_iv": None, "ml_timestamp": None, "ml_url": None}

# ---- TCP Activation helpers ----
def _build_auth_token_hex(account_id, jwt_token, timestamp, key_bytes, iv_bytes):
    try:
        uid = int(account_id)
        uid_hex = hex(uid)[2:]
        uid_length = len(uid_hex)
        ts = int(timestamp)
        ts_hex = hex(ts)[2:]
        if len(ts_hex) == 1:
            ts_hex = "0" + ts_hex

        cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        encrypted_token = cipher.encrypt(pad(jwt_token.encode(), AES.block_size))
        encrypted_hex   = encrypted_token.hex()
        enc_len_hex     = hex(len(encrypted_hex) // 2)[2:]

        if uid_length == 9:
            headers_str = '0000000'
        elif uid_length == 8:
            headers_str = '00000000'
        elif uid_length == 10:
            headers_str = '000000'
        elif uid_length == 7:
            headers_str = '000000000'
        else:
            headers_str = '0000000'

        return f"0115{headers_str}{uid_hex}{ts_hex}00000{enc_len_hex}{encrypted_hex}"
    except Exception as e:
        print(f"Auth token build error: {e}")
        return None

def _get_login_data_sync(base_url, open_id, access_token, jwt_token, session):
    try:
        if not NEW_PROTO_AVAILABLE:
            return None, None
        payload = _encrypt_major_login_proto(open_id, access_token)
        if payload is None:
            return None, None
        url = f"{base_url}/GetLoginData"
        host = base_url.replace("https://", "").replace("http://", "")
        headers = {
            "Accept-Encoding": "gzip",
            "Authorization": f"Bearer {jwt_token}",
            "Connection": "Keep-Alive",
            "Content-Type": "application/x-www-form-urlencoded",
            "Expect": "100-continue",
            "Host": host,
            "ReleaseVersion": "OB54",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_I005DA Build/PI)",
            "X-GA": "v1 1",
            "X-Unity-Version": "2018.4.11f1",
        }
        resp = session.post(url, headers=headers, data=payload, verify=False, timeout=15)
        if resp.status_code == 200 and resp.content:
            data = PorTs_pb2.GetLoginData()
            data.ParseFromString(resp.content)
            online_ip_port = data.Online_IP_Port if data.Online_IP_Port else None
            chat_ip_port   = data.AccountIP_Port if data.AccountIP_Port else None
            return online_ip_port, chat_ip_port
    except Exception as e:
        print(f"GetLoginData error: {e}")
    return None, None

async def _tcp_connect_and_activate(ip, port, auth_hex, server_name, duration=0.8):
    try:
        reader, writer = await asyncio.open_connection(ip, int(port), ssl=False)
        writer.write(bytes.fromhex(auth_hex))
        await writer.drain()
        await asyncio.sleep(duration)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception as e:
        print(f"TCP error {server_name} {ip}:{port} — {e}")
        return False

def _activate_via_tcp(account_id, jwt_token, timestamp, key_bytes, iv_bytes,
                      open_id, access_token, ml_url, session):
    try:
        if not (key_bytes and iv_bytes and timestamp and ml_url):
            return False

        online_ip_port, chat_ip_port = _get_login_data_sync(
            ml_url, open_id, access_token, jwt_token, session
        )
        if not online_ip_port and not chat_ip_port:
            return False

        auth_hex = _build_auth_token_hex(account_id, jwt_token, timestamp, key_bytes, iv_bytes)
        if not auth_hex:
            return False

        async def _run():
            tasks = []
            if online_ip_port and ":" in online_ip_port:
                ip, port = online_ip_port.split(":")
                tasks.append(_tcp_connect_and_activate(ip, port, auth_hex, "Online"))
            if chat_ip_port and ":" in chat_ip_port:
                ip, port = chat_ip_port.split(":")
                tasks.append(_tcp_connect_and_activate(ip, port, auth_hex, "Chat"))
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                return any(r is True for r in results)
            return False

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_run())
        finally:
            loop.close()
        return result
    except Exception as e:
        print(f"TCP activation error: {e}")
        return False

def _force_region_binding(region, jwt_token, session):
    try:
        url = ("https://loginbp.common.ggpolarbear.com/ChooseRegion"
               if region.upper() in ["ME","TH"]
               else "https://loginbp.ggpolarbear.com/ChooseRegion")
        region_code = "RU" if region.upper() == "CIS" else region.upper()
        fields = {1: region_code}
        proto_data = run_async(CrEaTe_ProTo(fields))
        encrypted_data = encrypt_api(bytes(proto_data).hex())
        payload = bytes.fromhex(encrypted_data)
        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 12; M2101K7AG Build/SKQ1.210908.001)",
            'Connection': "Keep-Alive", 'Accept-Encoding': "gzip",
            'Content-Type': "application/x-www-form-urlencoded", 'Expect': "100-continue",
            'Authorization': f"Bearer {jwt_token}", 'X-Unity-Version': "1.128.2",
            'X-GA': "v1 1", 'ReleaseVersion': "OB54",
        }
        response = session.post(url, data=payload, headers=headers, verify=False, timeout=15)
        return response.status_code == 200
    except:
        return False

def _select_veteran(region, jwt_token, session):
    try:
        url = ("https://clientbp.common.ggpolarbear.com/ActiveBeginnerGuide"
               if region.upper() in ["ME","TH"]
               else "https://clientbp.ggpolarbear.com/ActiveBeginnerGuide")
        fields = {1: 3}
        proto_data = run_async(CrEaTe_ProTo(fields))
        encrypted_data = encrypt_api(bytes(proto_data).hex())
        payload = bytes.fromhex(encrypted_data)
        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 12; M2101K7AG Build/SKQ1.210908.001)",
            'Connection': "Keep-Alive", 'Accept-Encoding': "gzip",
            'Content-Type': "application/x-www-form-urlencoded", 'Expect': "100-continue",
            'Authorization': f"Bearer {jwt_token}", 'X-Unity-Version': "1.128.2",
            'X-GA': "v1 1", 'ReleaseVersion': "OB54",
        }
        response = session.post(url, data=payload, headers=headers, verify=False, timeout=15)
        return response.status_code == 200
    except:
        return False

# ---- Main account creation (full flow) ----
def create_acc(region, session, custom_name=None):
    """
    Full OB54 account creation with TCP activation.
    Returns dict with uid, password, name, account_id, jwt_token, tcp_activated.
    """
    max_attempts = 10  # Increased from 5 to 10 for better chance
    for attempt in range(max_attempts):
        try:
            password = generate_custom_password()

            # Step 1: Register
            payload_register = json.dumps(
                {"app_id": 100067, "client_type": 2, "password": password, "source": 2},
                separators=(',', ':')
            )
            signature = hmac.new(GEN_CONFIG["API_KEY"], payload_register.encode(), hashlib.sha256).hexdigest()
            headers_reg = {
                "User-Agent": "GarenaMSDK/4.0.39(SM-A325M ;Android 13;en;HK;)",
                "Authorization": f"Signature {signature}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "Connection": "Keep-Alive",
                "Host": "100067.connect.garena.com",
            }
            resp_reg = session.post(
                GEN_CONFIG["REGISTER_URL"],
                headers=headers_reg,
                data=payload_register,
                timeout=15,
                verify=False
            )
            if resp_reg.status_code != 200:
                if resp_reg.status_code == 429:
                    time.sleep(1.0)  # Increased delay for rate limit
                continue
            reg_json = resp_reg.json()
            if reg_json.get("code") != 0:
                debug_print(f"Register error code: {reg_json.get('code')}")
                continue
            uid = reg_json['data']['uid']
            smart_delay()

            # Step 2: Token
            payload_token = json.dumps({
                "client_id": 100067,
                "client_secret": GEN_CONFIG["HEX_KEY"],
                "client_type": 2,
                "password": password,
                "response_type": "token",
                "uid": uid,
            }, separators=(',', ':'))
            signature2 = hmac.new(GEN_CONFIG["API_KEY"], payload_token.encode(), hashlib.sha256).hexdigest()
            headers_tok = {
                "User-Agent": "GarenaMSDK/4.0.39(SM-A325M ;Android 13;en;HK;)",
                "Authorization": f"Signature {signature2}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "Connection": "Keep-Alive",
                "Host": "100067.connect.garena.com",
            }
            resp_tok = session.post(
                GEN_CONFIG["TOKEN_URL"],
                headers=headers_tok,
                data=payload_token,
                timeout=15,
                verify=False
            )
            if resp_tok.status_code != 200:
                continue
            tok_json = resp_tok.json()
            if tok_json.get("code") != 0:
                debug_print(f"Token error: {tok_json.get('code')}")
                continue
            access_token = tok_json['data']['access_token']
            open_id = tok_json['data']['open_id']
            smart_delay()

            # Step 3: MajorRegister + MajorLogin + TCP activation
            # Use custom_name if provided, otherwise generate random name
            if custom_name:
                name = custom_name
                debug_print(f"Attempt {attempt+1}/{max_attempts}: Trying to create account with name: {name}")
            else:
                name = generate_random_name()
            
            # XOR encode open_id for MajorRegister
            keystream = [0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30,0x31,0x37,
                         0x30,0x30,0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30]
            encoded_open_id = ""
            for i, ch in enumerate(open_id):
                encoded_open_id += chr(ord(ch) ^ keystream[i % len(keystream)])
            field14 = encoded_open_id.encode('latin1')

            lang_code = "bn"
            payload_fields = {
                1: name, 2: access_token, 3: open_id,
                5: 102000007, 6: 4, 7: 1, 13: 1,
                14: field14, 15: lang_code, 16: 1, 17: 1
            }
            proto_bytes = run_async(CrEaTe_ProTo(payload_fields))
            encrypted_payload = E_AEs(bytes(proto_bytes).hex())

            host = "loginbp.ggpolarbear.com"
            register_url = GEN_CONFIG["MAJOR_REGISTER_URL"]
            headers_reg2 = {
                "Accept-Encoding": "gzip", "Authorization": "Bearer",
                "Connection": "Keep-Alive", "Content-Type": "application/x-www-form-urlencoded",
                "Expect": "100-continue", "Host": host,
                "ReleaseVersion": "OB54",
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_I005DA Build/PI)",
                "X-GA": "v1 1", "X-Unity-Version": "1.128.2",
            }
            resp_reg2 = session.post(register_url, headers=headers_reg2,
                                     data=encrypted_payload, verify=False, timeout=15)
            if resp_reg2.status_code != 200:
                debug_print(f"MajorRegister failed with status: {resp_reg2.status_code}")
                continue

            # MajorLogin
            login_result = _perform_major_login_sync(uid, password, access_token, open_id, region, session)
            account_id = login_result.get("account_id", "N/A")
            jwt_token = login_result.get("jwt_token", "")
            ml_key = login_result.get("ml_key")
            ml_iv = login_result.get("ml_iv")
            ml_ts = login_result.get("ml_timestamp")
            ml_url = login_result.get("ml_url")

            if account_id == "N/A":
                debug_print(f"MajorLogin failed - account_id is N/A")
                continue

            # Region binding and veteran selection
            if jwt_token and account_id != "N/A":
                _force_region_binding(region, jwt_token, session)
                _select_veteran(region, jwt_token, session)

            # TCP activation
            tcp_ok = False
            if jwt_token and account_id != "N/A":
                tcp_ok = _activate_via_tcp(
                    account_id, jwt_token, ml_ts, ml_key, ml_iv,
                    open_id, access_token, ml_url, session
                )

            # Account creation successful
            print(f"✅ Account created successfully with name: {name} (Attempt {attempt+1})")
            return {
                "uid": uid,
                "password": password,
                "name": name,
                "region": region,
                "account_id": account_id,
                "jwt_token": jwt_token,
                "tcp_activated": tcp_ok,
            }
        except Exception as e:
            print(f"create_acc error (attempt {attempt+1}): {e}")
        
        # Wait before retry (with jitter)
        if attempt < max_attempts - 1:
            wait_time = random.uniform(1.0, 3.0)  # Wait 1-3 seconds between retries
            print(f"🔄 Retrying account creation... (Attempt {attempt+2}/{max_attempts})")
            time.sleep(wait_time)
    
    # All attempts failed
    print(f"❌ Failed to create account after {max_attempts} attempts")
    return None

# =============================================================================
# 🚀 ProcessMonitor (keeps bot running and updates logs)
# =============================================================================

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
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute('UPDATE users SET bot_status="running", bot_pid=? WHERE id=?', (self.process.pid, self.user_id))
                conn.commit()
                conn.close()

                def update_bio_delayed():
                    time.sleep(5)
                    conn = sqlite3.connect(DB_FILE)
                    c = conn.cursor()
                    c.execute('SELECT username, bot_uid, bot_pw FROM users WHERE id=?', (self.user_id,))
                    row = c.fetchone()
                    conn.close()
                    if row and row[1] and row[2]:
                        update_bot_bio(row[1], row[2], row[0])

                threading.Thread(target=update_bio_delayed, daemon=True).start()

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

# ========== Global monitor dictionary ==========
monitors = {}

def get_monitor(user_id):
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
                            monitor.is_running = True
                            monitor.start_time = datetime.now()
                            monitor.bot_status = "🟢 ACTIVE & ONLINE"
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

ADMIN_LOGIN_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Admin Login - MAHIR</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; display: flex; justify-content: center; align-items: center; height: 100vh; }
        .container { background: rgba(15,12,41,0.9); padding: 40px; border-radius: 30px; width: 400px; border: 1px solid #7c3aed; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
        h2 { text-align: center; color: #c084fc; margin-bottom: 20px; }
        input { width: 100%; padding: 14px; margin: 10px 0; border-radius: 12px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; font-size: 1rem; }
        input:focus { outline: none; border-color: #7c3aed; }
        button { width: 100%; padding: 14px; background: linear-gradient(135deg, #7c3aed, #2563eb); border: none; border-radius: 12px; color: #fff; font-weight: bold; font-size: 1rem; cursor: pointer; transition: 0.3s; }
        button:hover { transform: scale(1.02); }
        .error { color: #f87171; text-align: center; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🔐 Admin Login</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="error">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <input type="text" name="username" placeholder="Admin Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Login</button>
        </form>
        <div style="text-align:center;margin-top:15px;">
            <a href="{{ url_for('login') }}" style="color:#c084fc;text-decoration:none;">← Back to Main</a>
        </div>
    </div>
</body>
</html>
'''

AGENT_LOGIN_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Agent Login - MAHIR</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; display: flex; justify-content: center; align-items: center; height: 100vh; }
        .container { background: rgba(15,12,41,0.9); padding: 40px; border-radius: 30px; width: 400px; border: 1px solid #7c3aed; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
        h2 { text-align: center; color: #c084fc; margin-bottom: 20px; }
        input { width: 100%; padding: 14px; margin: 10px 0; border-radius: 12px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; font-size: 1rem; }
        input:focus { outline: none; border-color: #7c3aed; }
        button { width: 100%; padding: 14px; background: linear-gradient(135deg, #7c3aed, #2563eb); border: none; border-radius: 12px; color: #fff; font-weight: bold; font-size: 1rem; cursor: pointer; transition: 0.3s; }
        button:hover { transform: scale(1.02); }
        .error { color: #f87171; text-align: center; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🔑 Agent Login</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="error">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <input type="text" name="username" placeholder="Agent Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Login</button>
        </form>
        <div style="text-align:center;margin-top:15px;">
            <a href="{{ url_for('login') }}" style="color:#c084fc;text-decoration:none;">← Back to Main</a>
        </div>
    </div>
</body>
</html>
'''

AGENT_DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Agent Dashboard - MAHIR</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px; background: rgba(15,12,41,0.8); border-radius: 20px; margin-bottom: 30px; }
        .header h1 { color: #c084fc; }
        .btn { padding: 10px 20px; border-radius: 30px; border: none; font-weight: bold; cursor: pointer; transition: 0.3s; }
        .btn-danger { background: #ef4444; color: #fff; }
        .btn-success { background: #10b981; color: #fff; }
        .btn-primary { background: #2563eb; color: #fff; }
        .btn-warning { background: #f59e0b; color: #fff; }
        .card { background: rgba(15,12,41,0.7); padding: 25px; border-radius: 20px; border: 1px solid #302b63; margin-bottom: 20px; }
        .card h3 { color: #c084fc; margin-top: 0; }
        .input-group { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
        .input-group input { padding: 10px 16px; border-radius: 30px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; flex: 1; min-width: 180px; }
        .badge { padding: 3px 12px; border-radius: 20px; font-size: 12px; }
        .badge-used { background: #10b98120; color: #10b981; border: 1px solid #10b981; }
        .badge-unused { background: #fbbf2420; color: #fbbf24; border: 1px solid #fbbf24; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #302b63; }
        th { color: #c084fc; }
        code { background: #1a1a3e; padding: 3px 8px; border-radius: 6px; color: #fbbf24; font-size: 0.8rem; }
        .stat-box { display: inline-block; background: rgba(0,0,0,0.4); padding: 6px 18px; border-radius: 40px; color: #c084fc; font-weight: bold; }
        .back-link { color: #c084fc; text-decoration: none; }
        .back-link:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔑 Agent Dashboard</h1>
            <div>
                <span>Welcome, {{ session.username }}</span>
                <a href="{{ url_for('agent_logout') }}" class="btn btn-danger">Logout</a>
            </div>
        </div>

        <div class="card">
            <h3>📊 Your Key Stats</h3>
            <p>Total Keys Created: <span class="stat-box">{{ keys|length }}</span></p>
        </div>

        <div class="card">
            <h3>🔑 Generate Registration Key</h3>
            <form method="POST" action="{{ url_for('agent_create_key') }}" class="input-group">
                <label>Valid days:</label>
                <input type="number" name="days_valid" value="30" min="1" max="365" style="width:100px;">
                <button type="submit" class="btn btn-success">Generate Key</button>
            </form>
            {% if new_key %}
                <div style="margin-top:15px;background:#1a1a3e;padding:15px;border-radius:15px;border:1px solid #7c3aed;">
                    <strong>New Key:</strong> <code style="font-size:18px;color:#fbbf24;">{{ new_key }}</code>
                    <span style="margin-left:20px;color:#a78bfa;">(valid {{ days }} days)</span>
                </div>
            {% endif %}
        </div>

        <div class="card">
            <h3>📋 Your Keys</h3>
            <table>
                <tr><th>Key</th><th>Created</th><th>Used By</th><th>Status</th></tr>
                {% for key in keys %}
                <tr>
                    <td><code>{{ key.key }}</code></td>
                    <td>{{ key.created_at[:10] }}</td>
                    <td>{{ key.used_by or '—' }}</td>
                    <td><span class="badge {{ 'badge-used' if key.is_used else 'badge-unused' }}">{{ 'Used' if key.is_used else 'Available' }}</span></td>
                </tr>
                {% else %}
                <tr><td colspan="4" style="text-align:center;color:#a78bfa;">No keys created yet.</td></tr>
                {% endfor %}
            </table>
        </div>

        <div style="margin-top:20px;">
            <a href="{{ url_for('login') }}" class="back-link">← Back to Main Site</a>
        </div>
    </div>
</body>
</html>
'''

ADMIN_DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Admin Dashboard - MAHIR</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px; background: rgba(15,12,41,0.8); border-radius: 20px; margin-bottom: 30px; }
        .header h1 { color: #c084fc; }
        .user-info { display: flex; align-items: center; gap: 20px; }
        .btn { padding: 10px 20px; border-radius: 30px; border: none; font-weight: bold; cursor: pointer; transition: 0.3s; }
        .btn-danger { background: #ef4444; color: #fff; }
        .btn-success { background: #10b981; color: #fff; }
        .btn-primary { background: #2563eb; color: #fff; }
        .btn-warning { background: #f59e0b; color: #fff; }
        .btn-info { background: #06b6d4; color: #fff; }
        .btn-sm { padding: 5px 12px; font-size: 12px; }
        .card { background: rgba(15,12,41,0.7); padding: 25px; border-radius: 20px; border: 1px solid #302b63; margin-bottom: 20px; }
        .card h3 { color: #c084fc; margin-top: 0; }
        .flex { display: flex; gap: 15px; flex-wrap: wrap; align-items: center; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #302b63; }
        th { color: #c084fc; }
        code { background: #1a1a3e; padding: 3px 8px; border-radius: 6px; color: #fbbf24; }
        .badge { padding: 3px 10px; border-radius: 20px; font-size: 12px; }
        .badge-used { background: #10b98120; color: #10b981; border: 1px solid #10b981; }
        .badge-unused { background: #fbbf2420; color: #fbbf24; border: 1px solid #fbbf24; }
        .badge-admin { background: #7c3aed20; color: #c084fc; border: 1px solid #7c3aed; }
        .badge-agent { background: #3b82f620; color: #60a5fa; border: 1px solid #3b82f6; }
        .input-group { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
        .input-group input[type="text"], .input-group input[type="password"] { padding: 8px 14px; border-radius: 30px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; }
        .upload-form { display: flex; gap: 20px; align-items: center; flex-wrap: wrap; }
        .stat-card { background: rgba(0,0,0,0.5); border-radius: 18px; padding: 1.25rem; text-align: center; border: 1px solid rgba(124,58,237,0.15); }
        .stat-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 2px; color: #a78bfa; margin-bottom: 0.5rem; font-weight: 600; }
        .stat-value { font-size: 1.3rem; font-weight: 800; color: #f0f0f0; }
        .progress-bar { background: #1e1b3b; border-radius: 1rem; height: 0.6rem; overflow: hidden; margin-top: 0.5rem; }
        .progress-fill { background: linear-gradient(90deg, #c084fc, #60a5fa, #34d399); height: 100%; width: 0%; border-radius: 1rem; transition: width 0.5s ease; }
        .system-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-top: 1rem; }
        .back-link { color: #c084fc; text-decoration: none; }
        .back-link:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🛡️ Admin Dashboard</h1>
            <div class="user-info">
                <span>Welcome, {{ session.username }}</span>
                <a href="{{ url_for('admin_logout') }}" class="btn btn-danger">Logout</a>
            </div>
        </div>

        <div class="card">
            <h3>🖥️ Server Resources</h3>
            <div class="system-stats">
                <div class="stat-card">
                    <div class="stat-label">CPU</div>
                    <div class="stat-value">{{ cpu }}%</div>
                    <div class="progress-bar"><div class="progress-fill" style="width: {{ cpu }}%;"></div></div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">RAM</div>
                    <div class="stat-value">{{ ram_percent }}%</div>
                    <div class="progress-bar"><div class="progress-fill" style="width: {{ ram_percent }}%;"></div></div>
                    <div style="font-size:0.8rem;color:#a78bfa;">{{ (ram_used / (1024**3))|round(1) }} GB / {{ (ram_total / (1024**3))|round(1) }} GB</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Disk</div>
                    <div class="stat-value">{{ disk_percent }}%</div>
                    <div class="progress-bar"><div class="progress-fill" style="width: {{ disk_percent }}%;"></div></div>
                    <div style="font-size:0.8rem;color:#a78bfa;">{{ (disk_used / (1024**3))|round(1) }} GB / {{ (disk_total / (1024**3))|round(1) }} GB</div>
                </div>
            </div>
        </div>

        <div class="card">
            <h3>📁 File Manager</h3>
            <a href="{{ url_for('admin_file_manager') }}" class="btn btn-info">Open File Manager</a>
        </div>

        <div class="card">
            <h3>📤 Upload New mahir.py</h3>
            <form method="POST" action="{{ url_for('admin_upload_mahir') }}" enctype="multipart/form-data" class="upload-form">
                <div class="input-group">
                    <input type="file" name="mahir_file" accept=".py" required>
                </div>
                <button type="submit" class="btn btn-warning">Upload & Update All Bots</button>
            </form>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div style="color:{{ 'green' if category=='success' else 'red' }}; margin-top:10px;">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
        </div>

        <div class="card">
            <h3>👤 Agent Management</h3>
            <form method="POST" action="{{ url_for('admin_create_agent') }}" class="flex">
                <input type="text" name="username" placeholder="Username" required style="padding:8px 14px;border-radius:30px;border:1px solid #302b63;background:#1a1a3e;color:#fff;">
                <input type="email" name="email" placeholder="Email" required style="padding:8px 14px;border-radius:30px;border:1px solid #302b63;background:#1a1a3e;color:#fff;">
                <input type="password" name="password" placeholder="Password" required style="padding:8px 14px;border-radius:30px;border:1px solid #302b63;background:#1a1a3e;color:#fff;">
                <button type="submit" class="btn btn-success">Create Agent</button>
            </form>
            <table>
                <tr><th>ID</th><th>Username</th><th>Email</th><th>Created</th><th>Keys Created</th><th>Action</th></tr>
                {% for agent in agents %}
                <tr>
                    <td>{{ agent.id }}</td>
                    <td>{{ agent.username }}</td>
                    <td>{{ agent.email or '-' }}</td>
                    <td>{{ agent.created_at[:10] }}</td>
                    <td>{{ agent.key_count }}</td>
                    <td>
                        <form method="POST" action="{{ url_for('admin_delete_agent', agent_id=agent.id) }}" style="display:inline;" onsubmit="return confirm('Delete this agent and all their keys?');">
                            <button type="submit" class="btn btn-danger btn-sm">Delete</button>
                        </form>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="6" style="text-align:center;color:#a78bfa;">No agents created yet.</td></tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h3>🔑 Generate Registration Key (Admin)</h3>
            <form method="POST" action="{{ url_for('admin_create_key') }}" class="flex">
                <div class="input-group">
                    <label>Valid days:</label>
                    <input type="number" name="days_valid" value="30" min="1" max="365" style="width:100px;">
                </div>
                <button type="submit" class="btn btn-success">Generate Key</button>
            </form>
            {% if new_key %}
                <div style="margin-top:15px;background:#1a1a3e;padding:15px;border-radius:15px;border:1px solid #7c3aed;">
                    <strong>New Key:</strong> <code style="font-size:18px;color:#fbbf24;">{{ new_key }}</code>
                    <span style="margin-left:20px;color:#a78bfa;">(valid {{ days }} days)</span>
                </div>
            {% endif %}
        </div>

        <div class="card">
            <h3>📋 Registered Users</h3>
            <table>
                <tr>
                    <th>ID</th><th>Username</th><th>Email</th><th>Created</th><th>Bot Status</th><th>Bot File</th><th>Role</th><th>Action</th>
                </tr>
                {% for user in users %}
                <tr>
                    <td>{{ user.id }}</td>
                    <td>{{ user.username }}</td>
                    <td>{{ user.email or '-' }}</td>
                    <td>{{ user.created_at[:10] }}</td>
                    <td>{{ user.bot_status }}</td>
                    <td>{{ user.bot_file or '-' }}</td>
                    <td>
                        {% if user.is_admin %}<span class="badge badge-admin">Admin</span>
                        {% elif user.is_agent %}<span class="badge badge-agent">Agent</span>
                        {% else %}User{% endif %}
                    </td>
                    <td>
                        {% if not user.is_admin and not user.is_agent %}
                        <form method="POST" action="{{ url_for('admin_delete_user', user_id=user.id) }}" style="display:inline;" onsubmit="return confirm('Delete this user?');">
                            <button type="submit" class="btn btn-danger btn-sm">Delete</button>
                        </form>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <div class="card">
            <h3>🔑 Recent Keys</h3>
            <table>
                <tr><th>Key</th><th>Created By</th><th>Created</th><th>Used By</th><th>Status</th><th>Action</th></tr>
                {% for key in keys %}
                <tr>
                    <td><code>{{ key.key }}</code></td>
                    <td>{{ key.created_by or '—' }}</td>
                    <td>{{ key.created_at[:10] }}</td>
                    <td>{{ key.used_by or '—' }}</td>
                    <td><span class="badge {{ 'badge-used' if key.is_used else 'badge-unused' }}">{{ 'Used' if key.is_used else 'Available' }}</span></td>
                    <td>
                        <form method="POST" action="{{ url_for('admin_delete_key', key_id=key.id) }}" style="display:inline;" onsubmit="return confirm('Delete this key?');">
                            <button type="submit" class="btn btn-danger btn-sm">Delete</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <a href="{{ url_for('logout') }}" class="back-link">← Back to Main Site</a>
    </div>
</body>
</html>
'''

FILE_MANAGER_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>File Manager - MAHIR Admin</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px; background: rgba(15,12,41,0.8); border-radius: 20px; margin-bottom: 30px; }
        .header h1 { color: #c084fc; }
        .btn { padding: 10px 20px; border-radius: 30px; border: none; font-weight: bold; cursor: pointer; transition: 0.3s; }
        .btn-danger { background: #ef4444; color: #fff; }
        .btn-success { background: #10b981; color: #fff; }
        .btn-primary { background: #2563eb; color: #fff; }
        .btn-warning { background: #f59e0b; color: #fff; }
        .btn-info { background: #06b6d4; color: #fff; }
        .btn-sm { padding: 5px 12px; font-size: 12px; }
        .card { background: rgba(15,12,41,0.7); padding: 25px; border-radius: 20px; border: 1px solid #302b63; margin-bottom: 20px; }
        .card h3 { color: #c084fc; margin-top: 0; }
        .upload-form { display: flex; gap: 20px; align-items: center; flex-wrap: wrap; margin-bottom: 20px; }
        .input-group input[type="file"] { padding: 8px; border-radius: 30px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #302b63; }
        th { color: #c084fc; }
        .file-icon { margin-right: 10px; }
        .folder { color: #fbbf24; }
        .file { color: #60a5fa; }
        .back-link { color: #c084fc; text-decoration: none; display: inline-block; margin-top: 20px; }
        .breadcrumb { color: #a78bfa; margin-bottom: 15px; }
        .breadcrumb a { color: #c084fc; text-decoration: none; }
        .breadcrumb a:hover { text-decoration: underline; }
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); backdrop-filter: blur(8px); z-index: 10000; justify-content: center; align-items: center; }
        .modal-overlay.active { display: flex; }
        .modal-box { background: #1a1a3e; border-radius: 30px; padding: 2rem; max-width: 800px; width: 95%; max-height: 90vh; overflow-y: auto; border: 1px solid rgba(124,58,237,0.4); }
        .modal-close { float: right; font-size: 2rem; cursor: pointer; color: #a78bfa; background: none; border: none; }
        .modal-title { color: #c084fc; font-size: 1.5rem; margin-bottom: 1rem; }
        .edit-textarea { width: 100%; height: 400px; background: #0a0a1a; color: #e2e8f0; border: 1px solid #302b63; border-radius: 12px; padding: 1rem; font-family: 'JetBrains Mono', monospace; font-size: 0.9rem; resize: vertical; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📁 File Manager</h1>
            <div>
                <a href="{{ url_for('admin_dashboard') }}" class="btn btn-primary">Dashboard</a>
                <a href="{{ url_for('admin_logout') }}" class="btn btn-danger">Logout</a>
            </div>
        </div>

        <div class="card">
            <h3>📤 Upload Any File (Auto-extract ZIP)</h3>
            <form method="POST" action="{{ url_for('admin_upload_file') }}" enctype="multipart/form-data" class="upload-form">
                <div class="input-group">
                    <input type="file" name="uploaded_file" required>
                </div>
                <button type="submit" class="btn btn-warning">Upload File</button>
            </form>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div style="color:{{ 'green' if category=='success' else 'red' }}; margin-top:10px;">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
        </div>

        <div class="card">
            <h3>📂 Current Directory: <span style="color:#fbbf24;">{{ current_path }}</span></h3>
            <div class="breadcrumb">
                <a href="{{ url_for('admin_file_manager') }}">/</a>
                {% for part in breadcrumb_parts %}
                    / <a href="{{ url_for('admin_file_manager', path=part) }}">{{ part }}</a>
                {% endfor %}
            </div>
            <table>
                <tr>
                    <th>Name</th>
                    <th>Size</th>
                    <th>Modified</th>
                    <th>Action</th>
                </tr>
                {% if parent_dir %}
                <tr>
                    <td><a href="{{ url_for('admin_file_manager', path=parent_dir) }}"><span class="folder">📁 ..</span></a></td>
                    <td>—</td>
                    <td>—</td>
                    <td>—</td>
                </tr>
                {% endif %}
                {% for item in files %}
                <tr>
                    <td>
                        {% if item.is_dir %}
                            <a href="{{ url_for('admin_file_manager', path=item.path) }}" class="folder">📁 {{ item.name }}</a>
                        {% else %}
                            <span class="file">📄 {{ item.name }}</span>
                        {% endif %}
                    </td>
                    <td>{{ item.size if not item.is_dir else '—' }}</td>
                    <td>{{ item.modified }}</td>
                    <td>
                        {% if not item.is_dir %}
                            <a href="{{ url_for('admin_download_file', path=item.path) }}" class="btn btn-info btn-sm">Download</a>
                            <button onclick="editFile('{{ item.path }}')" class="btn btn-warning btn-sm">Edit</button>
                            <button onclick="deleteFile('{{ item.path }}')" class="btn btn-danger btn-sm">Delete</button>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <a href="{{ url_for('admin_dashboard') }}" class="back-link">← Back to Dashboard</a>
    </div>

    <div id="editModal" class="modal-overlay">
        <div class="modal-box">
            <button class="modal-close" onclick="closeEditModal()">&times;</button>
            <div class="modal-title">✏️ Edit File: <span id="editFileName"></span></div>
            <textarea id="editContent" class="edit-textarea" spellcheck="false"></textarea>
            <div style="margin-top:1rem; display:flex; gap:1rem; justify-content:flex-end;">
                <button onclick="saveEdit()" class="btn btn-success">Save</button>
                <button onclick="closeEditModal()" class="btn btn-danger">Cancel</button>
            </div>
            <div id="editStatus" style="margin-top:0.5rem;color:#fbbf24;"></div>
        </div>
    </div>

    <script>
        let currentEditPath = '';

        function editFile(path) {
            currentEditPath = path;
            document.getElementById('editFileName').textContent = path;
            document.getElementById('editContent').value = 'Loading...';
            document.getElementById('editStatus').textContent = '';
            document.getElementById('editModal').classList.add('active');
            fetch(`/admin/edit_file/${encodeURIComponent(path)}`)
                .then(res => res.json())
                .then(data => {
                    if (data.error) {
                        document.getElementById('editContent').value = 'Error: ' + data.error;
                    } else {
                        document.getElementById('editContent').value = data.content;
                    }
                })
                .catch(err => {
                    document.getElementById('editContent').value = 'Error loading file: ' + err;
                });
        }

        function closeEditModal() {
            document.getElementById('editModal').classList.remove('active');
        }

        function saveEdit() {
            const content = document.getElementById('editContent').value;
            document.getElementById('editStatus').textContent = 'Saving...';
            fetch(`/admin/edit_file/${encodeURIComponent(currentEditPath)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: content })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('editStatus').textContent = '✅ Saved successfully!';
                    setTimeout(() => location.reload(), 1000);
                } else {
                    document.getElementById('editStatus').textContent = '❌ Error: ' + data.error;
                }
            })
            .catch(err => {
                document.getElementById('editStatus').textContent = '❌ Error: ' + err;
            });
        }

        function deleteFile(path) {
            if (!confirm(`Are you sure you want to delete "${path}"?`)) return;
            fetch(`/admin/delete_file/${encodeURIComponent(path)}`, { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        alert('✅ File deleted!');
                        location.reload();
                    } else {
                        alert('❌ Error: ' + data.error);
                    }
                })
                .catch(err => alert('Error: ' + err));
        }
    </script>
</body>
</html>
'''

LOGIN_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>MAHIR Bot Deployer - Login</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .main-container { width: 100%; max-width: 450px; padding: 20px; }
        .logo-container { text-align: center; margin-bottom: 20px; }
        .logo-container img { height: 100px; border-radius: 20px; box-shadow: 0 10px 30px rgba(124,58,237,0.3); }
        .container { background: rgba(15,12,41,0.9); padding: 40px; border-radius: 30px; border: 1px solid #7c3aed; box-shadow: 0 20px 60px rgba(0,0,0,0.5); position: relative; }
        h2 { text-align: center; color: #c084fc; margin-bottom: 20px; }
        input { width: 100%; padding: 14px; margin: 10px 0; border-radius: 12px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; font-size: 1rem; }
        input:focus { outline: none; border-color: #7c3aed; }
        button { width: 100%; padding: 14px; background: linear-gradient(135deg, #7c3aed, #2563eb); border: none; border-radius: 12px; color: #fff; font-weight: bold; font-size: 1rem; cursor: pointer; transition: 0.3s; }
        button:hover { transform: scale(1.02); }
        .error { color: #f87171; text-align: center; margin: 10px 0; }
        .success { color: #4ade80; text-align: center; margin: 10px 0; }

        .hamburger-menu { position: fixed; top: 20px; left: 20px; z-index: 1000; }
        .hamburger-btn { background: rgba(124,58,237,0.3); border: 1px solid rgba(124,58,237,0.5); color: #c084fc; padding: 12px 16px; border-radius: 12px; cursor: pointer; font-size: 1.5rem; transition: 0.3s; backdrop-filter: blur(10px); }
        .hamburger-btn:hover { background: rgba(124,58,237,0.5); transform: scale(1.05); }
        .menu-dropdown { display: none; position: absolute; top: 70px; left: 0; background: rgba(15,12,41,0.95); backdrop-filter: blur(20px); border: 1px solid #7c3aed; border-radius: 16px; padding: 12px 0; min-width: 220px; box-shadow: 0 20px 60px rgba(0,0,0,0.6); }
        .menu-dropdown.active { display: block; animation: slideDown 0.3s ease; }
        @keyframes slideDown { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
        .menu-item { display: block; padding: 12px 24px; color: #e2e8f0; text-decoration: none; transition: 0.3s; font-size: 0.95rem; border-left: 3px solid transparent; }
        .menu-item:hover { background: rgba(124,58,237,0.15); border-left-color: #7c3aed; color: #c084fc; }
        .menu-item i { width: 24px; margin-right: 12px; color: #a78bfa; }
        .menu-divider { border-top: 1px solid #302b63; margin: 6px 12px; }
        .menu-title { padding: 8px 24px; color: #a78bfa; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 2px; font-weight: 600; }

        .link { text-align: center; margin-top: 15px; }
        .link a { color: #c084fc; text-decoration: none; }
        .link a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="hamburger-menu">
        <button class="hamburger-btn" onclick="toggleMenu()">☰</button>
        <div class="menu-dropdown" id="menuDropdown">
            <div class="menu-title">🔐 Authentication</div>
            <a href="{{ url_for('login') }}" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>
            <a href="{{ url_for('register') }}" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>
            <a href="{{ url_for('recover') }}" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>
            <div class="menu-divider"></div>
            <div class="menu-title">👥 Roles</div>
            <a href="{{ url_for('admin_login') }}" class="menu-item"><i class="fas fa-shield-alt"></i> Admin Login</a>
            <a href="{{ url_for('agent_login') }}" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>
            <div class="menu-divider"></div>
            <a href="https://MAHIR.XO.JE/" target="_blank" class="menu-item"><i class="fas fa-globe"></i> Website</a>
        </div>
    </div>

    <div class="main-container">
        <div class="logo-container">
            <img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR Logo">
        </div>
        <div class="container">
            <h2>🔐 Login</h2>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            <form method="POST">
                <input type="text" name="username" placeholder="Username" required>
                <input type="password" name="password" placeholder="Password" required>
                <button type="submit">Login</button>
            </form>
        </div>
    </div>

    <script>
        function toggleMenu() {
            document.getElementById('menuDropdown').classList.toggle('active');
        }
        document.addEventListener('click', function(e) {
            const menu = document.querySelector('.hamburger-menu');
            if (!menu.contains(e.target)) {
                document.getElementById('menuDropdown').classList.remove('active');
            }
        });
    </script>
</body>
</html>
'''

REGISTER_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>MAHIR Bot Deployer - Register</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .main-container { width: 100%; max-width: 450px; padding: 20px; }
        .logo-container { text-align: center; margin-bottom: 20px; }
        .logo-container img { height: 100px; border-radius: 20px; box-shadow: 0 10px 30px rgba(124,58,237,0.3); }
        .container { background: rgba(15,12,41,0.9); padding: 40px; border-radius: 30px; border: 1px solid #7c3aed; box-shadow: 0 20px 60px rgba(0,0,0,0.5); position: relative; }
        h2 { text-align: center; color: #c084fc; margin-bottom: 20px; }
        input { width: 100%; padding: 14px; margin: 10px 0; border-radius: 12px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; font-size: 1rem; }
        input:focus { outline: none; border-color: #7c3aed; }
        button { width: 100%; padding: 14px; background: linear-gradient(135deg, #7c3aed, #2563eb); border: none; border-radius: 12px; color: #fff; font-weight: bold; font-size: 1rem; cursor: pointer; transition: 0.3s; }
        button:hover { transform: scale(1.02); }
        .error { color: #f87171; text-align: center; margin: 10px 0; }
        .success { color: #4ade80; text-align: center; margin: 10px 0; }
        .link { text-align: center; margin-top: 15px; }
        .link a { color: #c084fc; text-decoration: none; }
        .link a:hover { text-decoration: underline; }

        .hamburger-menu { position: fixed; top: 20px; left: 20px; z-index: 1000; }
        .hamburger-btn { background: rgba(124,58,237,0.3); border: 1px solid rgba(124,58,237,0.5); color: #c084fc; padding: 12px 16px; border-radius: 12px; cursor: pointer; font-size: 1.5rem; transition: 0.3s; backdrop-filter: blur(10px); }
        .hamburger-btn:hover { background: rgba(124,58,237,0.5); transform: scale(1.05); }
        .menu-dropdown { display: none; position: absolute; top: 70px; left: 0; background: rgba(15,12,41,0.95); backdrop-filter: blur(20px); border: 1px solid #7c3aed; border-radius: 16px; padding: 12px 0; min-width: 220px; box-shadow: 0 20px 60px rgba(0,0,0,0.6); }
        .menu-dropdown.active { display: block; animation: slideDown 0.3s ease; }
        @keyframes slideDown { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
        .menu-item { display: block; padding: 12px 24px; color: #e2e8f0; text-decoration: none; transition: 0.3s; font-size: 0.95rem; border-left: 3px solid transparent; }
        .menu-item:hover { background: rgba(124,58,237,0.15); border-left-color: #7c3aed; color: #c084fc; }
        .menu-item i { width: 24px; margin-right: 12px; color: #a78bfa; }
        .menu-divider { border-top: 1px solid #302b63; margin: 6px 12px; }
        .menu-title { padding: 8px 24px; color: #a78bfa; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 2px; font-weight: 600; }
    </style>
</head>
<body>
    <div class="hamburger-menu">
        <button class="hamburger-btn" onclick="toggleMenu()">☰</button>
        <div class="menu-dropdown" id="menuDropdown">
            <div class="menu-title">🔐 Authentication</div>
            <a href="{{ url_for('login') }}" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>
            <a href="{{ url_for('register') }}" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>
            <a href="{{ url_for('recover') }}" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>
            <div class="menu-divider"></div>
            <div class="menu-title">👥 Roles</div>
            <a href="{{ url_for('admin_login') }}" class="menu-item"><i class="fas fa-shield-alt"></i> Admin Login</a>
            <a href="{{ url_for('agent_login') }}" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>
            <div class="menu-divider"></div>
            <a href="https://MAHIR.XO.JE/" target="_blank" class="menu-item"><i class="fas fa-globe"></i> Website</a>
        </div>
    </div>

    <div class="main-container">
        <div class="logo-container">
            <img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR Logo">
        </div>
        <div class="container">
            <h2>📝 Register</h2>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            <form method="POST">
                <input type="text" name="username" placeholder="Username" required>
                <input type="password" name="password" placeholder="Password" required>
                <input type="email" name="email" placeholder="Email (optional)">
                <input type="text" name="registration_key" placeholder="Registration Key" required>
                <button type="submit">Register</button>
            </form>
            <div class="link"><a href="{{ url_for('login') }}">Already have account? Login</a></div>
        </div>
    </div>

    <script>
        function toggleMenu() {
            document.getElementById('menuDropdown').classList.toggle('active');
        }
        document.addEventListener('click', function(e) {
            const menu = document.querySelector('.hamburger-menu');
            if (!menu.contains(e.target)) {
                document.getElementById('menuDropdown').classList.remove('active');
            }
        });
    </script>
</body>
</html>
'''

RECOVER_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Recover Password - MAHIR</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .container { background: rgba(15,12,41,0.9); padding: 40px; border-radius: 30px; width: 400px; border: 1px solid #7c3aed; box-shadow: 0 20px 60px rgba(0,0,0,0.5); position: relative; }
        h2 { text-align: center; color: #c084fc; margin-bottom: 20px; }
        input { width: 100%; padding: 14px; margin: 10px 0; border-radius: 12px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; font-size: 1rem; }
        input:focus { outline: none; border-color: #7c3aed; }
        button { width: 100%; padding: 14px; background: linear-gradient(135deg, #7c3aed, #2563eb); border: none; border-radius: 12px; color: #fff; font-weight: bold; font-size: 1rem; cursor: pointer; transition: 0.3s; }
        button:hover { transform: scale(1.02); }
        .error { color: #f87171; text-align: center; margin: 10px 0; }
        .success { color: #4ade80; text-align: center; margin: 10px 0; }
        .link { text-align: center; margin-top: 15px; }
        .link a { color: #c084fc; text-decoration: none; }
        .password-box { background: #1a1a3e; padding: 15px; border-radius: 12px; border: 1px solid #7c3aed; margin: 10px 0; word-break: break-all; }
        .password-box code { color: #fbbf24; font-size: 1.1rem; }

        .hamburger-menu { position: fixed; top: 20px; left: 20px; z-index: 1000; }
        .hamburger-btn { background: rgba(124,58,237,0.3); border: 1px solid rgba(124,58,237,0.5); color: #c084fc; padding: 12px 16px; border-radius: 12px; cursor: pointer; font-size: 1.5rem; transition: 0.3s; backdrop-filter: blur(10px); }
        .hamburger-btn:hover { background: rgba(124,58,237,0.5); transform: scale(1.05); }
        .menu-dropdown { display: none; position: absolute; top: 70px; left: 0; background: rgba(15,12,41,0.95); backdrop-filter: blur(20px); border: 1px solid #7c3aed; border-radius: 16px; padding: 12px 0; min-width: 220px; box-shadow: 0 20px 60px rgba(0,0,0,0.6); }
        .menu-dropdown.active { display: block; animation: slideDown 0.3s ease; }
        @keyframes slideDown { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
        .menu-item { display: block; padding: 12px 24px; color: #e2e8f0; text-decoration: none; transition: 0.3s; font-size: 0.95rem; border-left: 3px solid transparent; }
        .menu-item:hover { background: rgba(124,58,237,0.15); border-left-color: #7c3aed; color: #c084fc; }
        .menu-item i { width: 24px; margin-right: 12px; color: #a78bfa; }
        .menu-divider { border-top: 1px solid #302b63; margin: 6px 12px; }
        .menu-title { padding: 8px 24px; color: #a78bfa; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 2px; font-weight: 600; }
    </style>
</head>
<body>
    <div class="hamburger-menu">
        <button class="hamburger-btn" onclick="toggleMenu()">☰</button>
        <div class="menu-dropdown" id="menuDropdown">
            <div class="menu-title">🔐 Authentication</div>
            <a href="{{ url_for('login') }}" class="menu-item"><i class="fas fa-sign-in-alt"></i> User Login</a>
            <a href="{{ url_for('register') }}" class="menu-item"><i class="fas fa-user-plus"></i> Create Account</a>
            <a href="{{ url_for('recover') }}" class="menu-item"><i class="fas fa-key"></i> Forgot Password</a>
            <div class="menu-divider"></div>
            <div class="menu-title">👥 Roles</div>
            <a href="{{ url_for('admin_login') }}" class="menu-item"><i class="fas fa-shield-alt"></i> Admin Login</a>
            <a href="{{ url_for('agent_login') }}" class="menu-item"><i class="fas fa-user-tie"></i> Agent Login</a>
            <div class="menu-divider"></div>
            <a href="https://MAHIR.XO.JE/" target="_blank" class="menu-item"><i class="fas fa-globe"></i> Website</a>
        </div>
    </div>

    <div class="container">
        <h2>🔑 Recover Password</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <form method="POST">
            <input type="text" name="username" placeholder="Username" required>
            <input type="email" name="email" placeholder="Email" required>
            <button type="submit">Recover Password</button>
        </form>
        <div class="link"><a href="{{ url_for('login') }}">Back to Login</a></div>
    </div>

    <script>
        function toggleMenu() {
            document.getElementById('menuDropdown').classList.toggle('active');
        }
        document.addEventListener('click', function(e) {
            const menu = document.querySelector('.hamburger-menu');
            if (!menu.contains(e.target)) {
                document.getElementById('menuDropdown').classList.remove('active');
            }
        });
    </script>
</body>
</html>
'''

# =============================================================================
# UPDATED USER PANEL (with Regenerate button, no credential fields)
# =============================================================================
USER_PANEL_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MAHIR PREMIUM | Bot Controller v5.1</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@100..900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', sans-serif; background: #0a0a1a; color: #fff; min-height: 100vh; position: relative; overflow-x: hidden; }
        .bg-animation { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; overflow: hidden; }
        .bg-animation .orb { position: absolute; border-radius: 50%; filter: blur(80px); opacity: 0.3; animation: orbFloat 25s infinite ease-in-out; }
        .bg-animation .orb:nth-child(1) { width: 600px; height: 600px; background: #7c3aed; top: -10%; left: -10%; animation-delay: 0s; }
        .bg-animation .orb:nth-child(2) { width: 500px; height: 500px; background: #2563eb; bottom: -10%; right: -10%; animation-delay: -8s; }
        .bg-animation .orb:nth-child(3) { width: 400px; height: 400px; background: #8b5cf6; top: 50%; left: 50%; transform: translate(-50%, -50%); animation-delay: -15s; }
        @keyframes orbFloat { 0%,100% { transform: translate(0,0) scale(1); } 25% { transform: translate(50px,-80px) scale(1.1); } 50% { transform: translate(-30px,60px) scale(0.9); } 75% { transform: translate(80px,30px) scale(1.05); } }
        .particles { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none; }
        .particle { position: absolute; width: 3px; height: 3px; background: rgba(255,255,255,0.15); border-radius: 50%; animation: particleFloat 15s infinite ease-in-out; }
        @keyframes particleFloat { 0%,100% { transform: translateY(0px) translateX(0px); opacity: 0.1; } 25% { transform: translateY(-200px) translateX(50px); opacity: 0.5; } 50% { transform: translateY(-100px) translateX(100px); opacity: 0.3; } 75% { transform: translateY(50px) translateX(50px); opacity: 0.6; } }
        .container { max-width: 1600px; margin: 0 auto; padding: 20px; position: relative; z-index: 1; }
        .cover-section { position: relative; border-radius: 32px; overflow: hidden; margin-bottom: 30px; box-shadow: 0 30px 60px rgba(0,0,0,0.6); }
        .cover-section .cover-image { width: 100%; height: 320px; object-fit: cover; display: block; transition: transform 0.6s ease; }
        .cover-section:hover .cover-image { transform: scale(1.02); }
        .cover-section .cover-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: linear-gradient(135deg, rgba(10,10,26,0.7) 0%, rgba(124,58,237,0.2) 50%, rgba(37,99,235,0.1) 100%); display: flex; flex-direction: column; justify-content: center; padding: 40px 50px; }
        .cover-section .cover-overlay .logo-container { display: flex; align-items: center; gap: 25px; margin-bottom: 10px; }
        .cover-section .cover-overlay .logo-container img { height: 90px; width: auto; border-radius: 20px; box-shadow: 0 10px 30px rgba(124,58,237,0.4); animation: logoGlow 3s ease-in-out infinite; }
        @keyframes logoGlow { 0%,100% { filter: drop-shadow(0 0 15px rgba(124,58,237,0.3)); } 50% { filter: drop-shadow(0 0 35px rgba(124,58,237,0.6)); } }
        .cover-section .cover-overlay .title-group h1 { font-size: 3rem; font-weight: 800; background: linear-gradient(125deg, #c084fc, #60a5fa, #34d399, #f472b6); -webkit-background-clip: text; background-clip: text; color: transparent; line-height: 1.1; letter-spacing: -0.02em; }
        .cover-section .cover-overlay .title-group p { font-size: 1rem; color: rgba(255,255,255,0.7); font-weight: 300; letter-spacing: 1px; }
        .cover-section .cover-overlay .cover-badge { position: absolute; top: 25px; right: 30px; background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.15); padding: 8px 20px; border-radius: 40px; font-size: 0.75rem; font-weight: 600; color: #c084fc; letter-spacing: 2px; text-transform: uppercase; }
        @media (max-width: 768px) { .cover-section .cover-image { height: 220px; } .cover-section .cover-overlay { padding: 25px; } .cover-section .cover-overlay .logo-container img { height: 55px; } .cover-section .cover-overlay .title-group h1 { font-size: 1.8rem; } .cover-section .cover-overlay .cover-badge { top: 15px; right: 15px; font-size: 0.6rem; padding: 5px 12px; } }
        .card { background: rgba(15,12,41,0.65); backdrop-filter: blur(20px); border-radius: 24px; border: 1px solid rgba(124,58,237,0.2); padding: 1.5rem; margin-bottom: 1.5rem; transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275); position: relative; overflow: hidden; }
        .card::before { content: ''; position: absolute; top: 0; left: -100%; width: 100%; height: 100%; background: linear-gradient(90deg, transparent, rgba(124,58,237,0.08), transparent); transition: left 0.6s; }
        .card:hover::before { left: 100%; }
        .card:hover { transform: translateY(-4px); border-color: rgba(124,58,237,0.4); box-shadow: 0 20px 40px rgba(0,0,0,0.4); }
        .card-title { font-size: 1.1rem; font-weight: 700; color: #c084fc; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.75rem; }
        .card-title i { font-size: 1.2rem; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }
        .stat-card { background: rgba(0,0,0,0.5); border-radius: 18px; padding: 1.25rem; text-align: center; transition: all 0.3s ease; border: 1px solid rgba(124,58,237,0.15); position: relative; overflow: hidden; }
        .stat-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: linear-gradient(90deg, #c084fc, #60a5fa, #34d399); }
        .stat-card:hover { transform: scale(1.02); background: rgba(0,0,0,0.7); border-color: rgba(124,58,237,0.4); }
        .stat-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 2px; color: #a78bfa; margin-bottom: 0.5rem; font-weight: 600; }
        .stat-value { font-size: 1.3rem; font-weight: 800; background: linear-gradient(135deg, #f0f0f0, #cbd5e1); -webkit-background-clip: text; background-clip: text; color: transparent; word-break: break-all; }
        .progress-bar { background: #1e1b3b; border-radius: 1rem; height: 0.6rem; overflow: hidden; margin-top: 0.5rem; }
        .progress-fill { background: linear-gradient(90deg, #c084fc, #60a5fa, #34d399); height: 100%; width: 0%; transition: width 0.5s ease; border-radius: 1rem; }
        .system-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-top: 1rem; }
        @media (max-width: 768px) { .system-stats { grid-template-columns: 1fr; } }
        .tab-container { display: flex; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 2px solid rgba(124,58,237,0.2); padding-bottom: 0.75rem; flex-wrap: wrap; }
        .tab-btn { background: transparent; border: none; padding: 0.6rem 1.8rem; border-radius: 40px; color: #a78bfa; cursor: pointer; transition: all 0.3s ease; font-weight: 600; font-size: 0.85rem; position: relative; }
        .tab-btn:hover { color: #c084fc; transform: translateY(-2px); }
        .tab-btn.active { background: linear-gradient(135deg, rgba(124,58,237,0.2), rgba(96,165,250,0.1)); color: #c084fc; border: 1px solid rgba(124,58,237,0.4); }
        .tab-content { display: none; animation: fadeInUp 0.4s ease-out; }
        .tab-content.active { display: block; }
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .log-box { background: rgba(0,0,0,0.85); border-radius: 18px; padding: 1rem; height: 450px; overflow-y: auto; font-family: 'JetBrains Mono', 'Courier New', monospace; font-size: 0.8rem; border: 1px solid #302b63; }
        .log-box::-webkit-scrollbar { width: 6px; }
        .log-box::-webkit-scrollbar-track { background: #1e1b3b; border-radius: 10px; }
        .log-box::-webkit-scrollbar-thumb { background: #c084fc; border-radius: 10px; }
        .log-line { padding: 0.4rem 0.6rem; border-left: 3px solid #c084fc; margin-bottom: 0.15rem; color: #d1d5db; font-size: 0.75rem; word-wrap: break-word; white-space: pre-wrap; transition: all 0.2s ease; }
        .log-line:hover { background: rgba(124,58,237,0.05); transform: translateX(4px); }
        .error-line { border-left-color: #ef4444; color: #fca5a5; background: rgba(239,68,68,0.05); }
        .message-card { background: linear-gradient(135deg, rgba(124,58,237,0.08), rgba(96,165,250,0.05)); border: 1px solid rgba(124,58,237,0.2); border-radius: 18px; padding: 1rem; margin-bottom: 0.75rem; transition: all 0.3s ease; }
        .message-card:hover { transform: translateX(5px); border-color: #c084fc; }
        .message-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.5rem; padding-bottom: 0.4rem; border-bottom: 1px solid rgba(124,58,237,0.2); flex-wrap: wrap; }
        .message-sender { font-weight: 800; color: #c084fc; }
        .message-time { font-size: 0.65rem; color: #8b5cf6; }
        .message-meta { display: grid; grid-template-columns: auto 1fr; gap: 0.3rem 0.8rem; font-size: 0.8rem; }
        .message-label { font-weight: 600; color: #a78bfa; }
        .message-value { color: #e2e8f0; word-break: break-all; }
        .button-group { display: flex; gap: 0.75rem; margin-top: 1.5rem; flex-wrap: wrap; }
        .btn { flex: 1; padding: 0.75rem 1.5rem; border: none; border-radius: 40px; font-weight: 700; cursor: pointer; transition: all 0.3s ease; font-size: 0.85rem; position: relative; overflow: hidden; min-width: 120px; }
        .btn::before { content: ''; position: absolute; top: 50%; left: 50%; width: 0; height: 0; border-radius: 50%; background: rgba(255,255,255,0.15); transform: translate(-50%, -50%); transition: width 0.6s, height 0.6s; }
        .btn:hover::before { width: 300px; height: 300px; }
        .btn:hover { transform: translateY(-3px); box-shadow: 0 10px 25px rgba(0,0,0,0.3); }
        .btn-start { background: linear-gradient(135deg, #10b981, #059669); color: white; }
        .btn-stop { background: linear-gradient(135deg, #ef4444, #b91c1c); color: white; }
        .btn-reset { background: linear-gradient(135deg, #f59e0b, #d97706); color: white; }
        .btn-clear { background: linear-gradient(135deg, #475569, #1e293b); color: white; }
        .btn-export { background: linear-gradient(135deg, #0891b2, #06b6d4); color: white; }
        .btn-fullscreen { background: linear-gradient(135deg, #8b5cf6, #6d28d9); color: white; }
        .btn-admin { background: linear-gradient(135deg, #f472b6, #be185d); color: white; }
        .btn-regenerate { background: linear-gradient(135deg, #f97316, #ea580c); color: white; }
        .btn-sm { flex: 0 0 auto; padding: 0.4rem 1rem; font-size: 0.75rem; min-width: auto; }
        .badge { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.3rem 1rem; border-radius: 40px; font-size: 0.75rem; font-weight: 700; }
        .badge-active { background: rgba(34,197,94,0.2); color: #4ade80; border: 1px solid #22c55e; animation: pulse 2s ease-in-out infinite; }
        .badge-offline { background: rgba(239,68,68,0.2); color: #f87171; border: 1px solid #ef4444; }
        .badge-warning { background: rgba(251,191,36,0.2); color: #fbbf24; border: 1px solid #fbbf24; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.6; } }
        .chart-container { position: relative; height: 280px; margin-top: 0.5rem; }
        .info-row { display: flex; justify-content: space-between; padding: 0.6rem 0; border-bottom: 1px solid #302b63; flex-wrap: wrap; gap: 0.3rem; }
        .info-label { font-weight: 600; color: #a78bfa; }
        .info-value { font-weight: 700; color: #e2e8f0; }
        .control-bar { display: flex; gap: 0.5rem; margin-bottom: 0.75rem; align-items: center; flex-wrap: wrap; }
        .pause-btn { background: rgba(100,100,100,0.2); border: 1px solid #a78bfa; padding: 0.4rem 1rem; border-radius: 40px; color: #e2e8f0; cursor: pointer; transition: all 0.3s ease; font-size: 0.8rem; }
        .pause-btn:hover { background: rgba(124,58,237,0.2); }
        .pause-btn.paused { background: rgba(239,68,68,0.2); border-color: #ef4444; color: #fca5a5; }
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); backdrop-filter: blur(8px); z-index: 10000; justify-content: center; align-items: center; animation: fadeIn 0.3s ease; }
        .modal-overlay.active { display: flex; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        .modal-box { background: linear-gradient(135deg, #1a1a3e, #2d2b55); border-radius: 30px; padding: 2rem; max-width: 800px; width: 95%; max-height: 90vh; overflow-y: auto; border: 1px solid rgba(124,58,237,0.4); box-shadow: 0 30px 60px rgba(0,0,0,0.8); animation: slideUp 0.4s ease; position: relative; }
        @keyframes slideUp { from { transform: translateY(50px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        .modal-close { position: absolute; top: 15px; right: 20px; font-size: 2rem; cursor: pointer; color: #a78bfa; transition: all 0.3s ease; background: none; border: none; }
        .modal-close:hover { color: #c084fc; transform: rotate(90deg); }
        .modal-title { font-size: 1.8rem; font-weight: 800; background: linear-gradient(125deg, #c084fc, #60a5fa); -webkit-background-clip: text; background-clip: text; color: transparent; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; }
        .modal-section { background: rgba(0,0,0,0.4); border-radius: 18px; padding: 1.25rem; margin-bottom: 1.25rem; border: 1px solid rgba(124,58,237,0.15); }
        .modal-section h3 { color: #c084fc; font-size: 1.1rem; margin-bottom: 0.75rem; display: flex; align-items: center; gap: 0.5rem; }
        .modal-input-group { display: flex; gap: 0.75rem; margin-bottom: 0.75rem; flex-wrap: wrap; align-items: center; }
        .modal-input-group label { min-width: 100px; color: #a78bfa; font-weight: 600; }
        .modal-input-group input { flex: 1; padding: 0.6rem 1rem; border-radius: 40px; border: 1px solid #302b63; background: rgba(0,0,0,0.6); color: #e2e8f0; font-family: 'Inter', sans-serif; transition: all 0.3s ease; min-width: 180px; }
        .modal-input-group input:focus { outline: none; border-color: #c084fc; box-shadow: 0 0 15px rgba(124,58,237,0.3); }
        .modal-btn { padding: 0.5rem 1.5rem; border: none; border-radius: 40px; font-weight: 700; cursor: pointer; transition: all 0.3s ease; font-size: 0.8rem; }
        .modal-btn:hover { transform: scale(1.05); }
        .modal-btn-save { background: linear-gradient(135deg, #10b981, #059669); color: white; }
        .modal-btn-cancel { background: linear-gradient(135deg, #475569, #1e293b); color: white; }
        .modal-btn-action { background: linear-gradient(135deg, #8b5cf6, #6d28d9); color: white; }
        .modal-btn-danger { background: linear-gradient(135deg, #ef4444, #b91c1c); color: white; }
        .modal-btn-info { background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; }
        .modal-flex-row { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; }
        .modal-result-box { background: rgba(0,0,0,0.6); border-radius: 14px; padding: 0.8rem; margin-top: 0.8rem; max-height: 180px; overflow-y: auto; font-size: 0.8rem; color: #d1d5db; border: 1px solid #302b63; white-space: pre-wrap; word-wrap: break-word; }
        .notification { position: fixed; top: 20px; right: 20px; padding: 0.8rem 1.5rem; border-radius: 14px; z-index: 9999; animation: fadeInUp 0.3s ease-out; box-shadow: 0 10px 30px rgba(0,0,0,0.4); font-weight: 500; backdrop-filter: blur(10px); }
        .notification-success { background: linear-gradient(135deg, #10b981, #059669); color: white; }
        .notification-error { background: linear-gradient(135deg, #ef4444, #b91c1c); color: white; }
        .notification-info { background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; }
        .config-form { max-width: 600px; margin: 0 auto; }
        .config-form input { width: 100%; padding: 14px; margin: 12px 0; border-radius: 12px; border: 1px solid #302b63; background: #1a1a3e; color: #fff; font-size: 1rem; }
        .config-form input:focus { outline: none; border-color: #7c3aed; }
        .config-form button { width: 100%; padding: 14px; background: linear-gradient(135deg, #10b981, #059669); border: none; border-radius: 12px; color: #fff; font-weight: bold; font-size: 1rem; cursor: pointer; transition: 0.3s; }
        .config-form button:hover { transform: scale(1.02); }
        .config-status { text-align: center; padding: 15px; background: #1a1a3e; border-radius: 12px; margin-bottom: 20px; border-left: 4px solid #fbbf24; }
        .logout-btn { position: fixed; top: 20px; right: 20px; z-index: 999; background: rgba(239,68,68,0.8); color: #fff; border: none; padding: 10px 20px; border-radius: 30px; font-weight: 600; cursor: pointer; backdrop-filter: blur(10px); }
        .logout-btn:hover { background: #ef4444; }
        @media (max-width: 768px) { .stats-grid { grid-template-columns: repeat(2, 1fr); } .stat-value { font-size: 1rem; } .btn { padding: 0.5rem 1rem; font-size: 0.75rem; min-width: 80px; } .button-group .btn { flex: 1 1 45%; } .tab-btn { padding: 0.4rem 1rem; font-size: 0.75rem; } .modal-box { padding: 1.25rem; } .modal-input-group { flex-direction: column; align-items: stretch; } .modal-input-group label { min-width: auto; } }
        @media (max-width: 480px) { .stats-grid { grid-template-columns: 1fr; } .button-group .btn { flex: 1 1 100%; } }
    </style>
</head>
<body>
    <div class="bg-animation"><div class="orb"></div><div class="orb"></div><div class="orb"></div></div>
    <div class="particles" id="particles"></div>
    <div class="container">
        <button class="logout-btn" onclick="window.location.href='/logout'"><i class="fas fa-sign-out-alt"></i> Logout</button>
        <div class="cover-section">
            <img class="cover-image" src="https://mahir-photo-url.vercel.app/image/Picsart_26-06-20_16-14-53-925.jpg" alt="Cover Photo">
            <div class="cover-overlay">
                <div class="logo-container">
                    <img src="https://mahir-photo-url.vercel.app/image/dbf54e35e2454c77a97d5cceaeeb4b59_20260531_194906.png" alt="MAHIR Logo">
                    <div class="title-group"><h1>MAHIR PREMIUM</h1><p>Enterprise Bot Controller • v5.1</p></div>
                </div>
                <div class="cover-badge"><i class="fas fa-crown"></i> {{ 'PREMIUM' if config_done else 'SETUP' }}</div>
            </div>
        </div>

        {% if not config_done %}
        <div class="card">
            <div class="card-title"><i class="fas fa-cog"></i> Bot Configuration</div>
            <div class="config-status"><i class="fas fa-info-circle"></i> Provide a name for your bot. A new Free Fire account will be automatically created (Bangladesh server).</div>
            <form method="POST" action="{{ url_for('configure_bot') }}" class="config-form">
                <input type="text" name="bot_name" placeholder="Enter Bot Name (e.g., MyBot)" required>
                <button type="submit"><i class="fas fa-play"></i> Deploy Bot</button>
            </form>
            <div style="margin-top:15px;font-size:0.8rem;color:#a78bfa;">
                <i class="fas fa-shield-alt"></i> Admin UIDs will be auto-added.
            </div>
        </div>
        {% else %}
        <div class="card">
            <div class="card-title"><i class="fas fa-robot"></i> Bot Identity & Status</div>
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-label"><i class="fas fa-id-card"></i> UID</div><div class="stat-value" id="botUid">---</div></div>
                <div class="stat-card"><div class="stat-label"><i class="fas fa-user-astronaut"></i> Name</div><div class="stat-value" id="botName">---</div></div>
                <div class="stat-card"><div class="stat-label"><i class="fas fa-globe-asia"></i> Region</div><div class="stat-value" id="botRegion">---</div></div>
                <div class="stat-card"><div class="stat-label"><i class="fas fa-heartbeat"></i> Status</div><div class="stat-value" id="botStatus">---</div></div>
            </div>
            <div style="margin-top:1rem;padding-top:1rem;border-top:1px solid rgba(124,58,237,0.15);">
                <div style="font-size:0.9rem;font-weight:600;color:#a78bfa;margin-bottom:0.5rem;"><i class="fas fa-comment-dots"></i> Last Message Activity</div>
                <div class="stats-grid">
                    <div class="stat-card"><div class="stat-label"><i class="fas fa-user"></i> Sender UID</div><div class="stat-value" id="lastSenderUid" style="font-size:0.9rem;">---</div></div>
                    <div class="stat-card"><div class="stat-label"><i class="fas fa-users"></i> Guild</div><div class="stat-value" id="lastGuildName" style="font-size:0.9rem;">---</div></div>
                    <div class="stat-card"><div class="stat-label"><i class="fas fa-comment"></i> Message</div><div class="stat-value" id="lastMessage" style="font-size:0.8rem;">---</div></div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-title"><i class="fas fa-chart-line"></i> System Performance</div>
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-label"><i class="fas fa-microchip"></i> Process</div><div class="stat-value" id="processStatus">---</div></div>
                <div class="stat-card"><div class="stat-label"><i class="fas fa-clock"></i> Uptime</div><div class="stat-value" id="uptime">00:00:00</div></div>
                <div class="stat-card"><div class="stat-label"><i class="fas fa-sync-alt"></i> Restarts</div><div class="stat-value" id="restartCount">0</div></div>
                <div class="stat-card"><div class="stat-label"><i class="fas fa-exclamation-triangle"></i> Errors</div><div class="stat-value" id="errorCount" style="color:#f87171;">0</div></div>
            </div>
            <div class="system-stats">
                <div class="stat-card"><div class="stat-label"><i class="fas fa-tachometer-alt"></i> CPU</div><div class="stat-value" id="cpuValue">0%</div><div class="progress-bar"><div class="progress-fill" id="cpuBar"></div></div></div>
                <div class="stat-card"><div class="stat-label"><i class="fas fa-memory"></i> RAM</div><div class="stat-value" id="ramValue">0%</div><div class="progress-bar"><div class="progress-fill" id="ramBar"></div></div></div>
                <div class="stat-card"><div class="stat-label"><i class="fas fa-hdd"></i> Disk</div><div class="stat-value" id="diskValue">0%</div><div class="progress-bar"><div class="progress-fill" id="diskBar"></div></div></div>
            </div>
            <div class="info-row"><span class="info-label"><i class="fas fa-hourglass-half"></i> Script Expiry:</span><span class="info-value" id="expiryInfo">No Limit</span></div>
            <div class="info-row"><span class="info-label"><i class="fas fa-redo-alt"></i> Auto-Restart:</span><span class="info-value" id="autoRestartInfo">Disabled</span></div>
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
                <div class="control-bar">
                    <button onclick="togglePause()" id="pauseBtn" class="pause-btn"><i class="fas fa-pause"></i> Pause</button>
                    <button onclick="openFullscreenLogs()" class="btn-fullscreen btn btn-sm"><i class="fas fa-expand"></i> Fullscreen</button>
                    <button onclick="exportLogs()" class="btn-export btn btn-sm"><i class="fas fa-download"></i> Export</button>
                    <span style="margin-left:auto;font-size:0.7rem;color:#a78bfa;" id="logStatus">Auto-scroll: ON</span>
                </div>
                <div id="logBox" class="log-box"><div class="log-line"><i class="fas fa-info-circle"></i> Waiting for logs...</div></div>
            </div>
            <div id="messagesTab" class="tab-content">
                <div class="control-bar">
                    <button onclick="clearMessages()" class="btn-clear btn btn-sm"><i class="fas fa-trash-alt"></i> Clear</button>
                    <button onclick="exportMessages()" class="btn-export btn btn-sm"><i class="fas fa-download"></i> Export</button>
                </div>
                <div id="messageHistory" class="log-box" style="height:400px;"><div class="log-line"><i class="fas fa-info-circle"></i> No messages received...</div></div>
            </div>
            <div id="errorsTab" class="tab-content">
                <div class="control-bar">
                    <button onclick="clearErrors()" class="btn-clear btn btn-sm"><i class="fas fa-trash-alt"></i> Clear</button>
                    <button onclick="exportErrors()" class="btn-export btn btn-sm"><i class="fas fa-download"></i> Export</button>
                </div>
                <div id="errorBox" class="log-box"><div class="log-line"><i class="fas fa-check-circle"></i> No errors detected</div></div>
            </div>
            <div class="button-group">
                <button onclick="sendAction('start')" class="btn btn-start"><i class="fas fa-play"></i> Start</button>
                <button onclick="sendAction('stop')" class="btn btn-stop"><i class="fas fa-stop"></i> Stop</button>
                <button onclick="sendAction('reset')" class="btn btn-reset"><i class="fas fa-sync-alt"></i> Reset</button>
                <button onclick="openAdminPanel()" class="btn btn-admin"><i class="fas fa-cog"></i> Admin</button>
                <button onclick="regenerateBot()" class="btn btn-regenerate"><i class="fas fa-sync-alt"></i> Regenerate Bot</button>
            </div>
        </div>
        {% endif %}
    </div>

    <div id="adminModal" class="modal-overlay">
        <div class="modal-box">
            <button class="modal-close" onclick="closeAdminPanel()">&times;</button>
            <div class="modal-title"><i class="fas fa-crown"></i> Admin Control Panel</div>
            <div class="modal-section">
                <h3><i class="fas fa-user-shield"></i> Admin UIDs</h3>
                <div class="modal-input-group">
                    <label>UIDs (comma separated):</label>
                    <input type="text" id="adminUidsInput" placeholder="e.g. 1120167200, 3020431227">
                </div>
                <button onclick="updateAdminUIDs()" class="modal-btn modal-btn-save"><i class="fas fa-save"></i> Save & Restart</button>
            </div>
            <div class="modal-section">
                <h3><i class="fas fa-key"></i> Bot Credentials</h3>
                <div class="modal-input-group">
                    <label>Bot UID:</label>
                    <input type="text" id="botUidInput" placeholder="Enter new UID">
                </div>
                <div class="modal-input-group">
                    <label>Password:</label>
                    <input type="text" id="botPwInput" placeholder="Enter new password hash">
                </div>
                <button onclick="updateBotCreds()" class="modal-btn modal-btn-save"><i class="fas fa-save"></i> Save & Restart</button>
            </div>
            <div class="modal-section">
                <h3><i class="fas fa-user-friends"></i> Friend Management</h3>
                <div class="modal-flex-row">
                    <input type="text" id="friendUidInput" placeholder="Enter UID" style="flex:1;padding:0.6rem 1rem;border-radius:40px;border:1px solid #302b63;background:rgba(0,0,0,0.6);color:#e2e8f0;">
                    <button onclick="friendAction('add')" class="modal-btn modal-btn-action"><i class="fas fa-user-plus"></i> Add</button>
                    <button onclick="friendAction('remove')" class="modal-btn modal-btn-danger"><i class="fas fa-user-minus"></i> Remove</button>
                    <button onclick="friendAction('list')" class="modal-btn modal-btn-info"><i class="fas fa-list"></i> List</button>
                </div>
                <div id="friendResult" class="modal-result-box">Result will appear here...</div>
            </div>
            <div style="text-align:right;margin-top:1rem;">
                <button onclick="closeAdminPanel()" class="modal-btn modal-btn-cancel"><i class="fas fa-times"></i> Close</button>
            </div>
        </div>
    </div>

    <script>
        let performanceChart = null;
        let currentTab = 'logs';
        let autoScroll = true;
        let updateInterval = null;
        let fullscreenActive = false;
        let fsAutoScroll = true;

        function createParticles() {
            const container = document.getElementById('particles');
            for (let i=0; i<40; i++) {
                const p = document.createElement('div');
                p.className = 'particle';
                const size = Math.random() * 4 + 2;
                p.style.width = size + 'px';
                p.style.height = size + 'px';
                p.style.left = Math.random() * 100 + '%';
                p.style.top = Math.random() * 100 + '%';
                p.style.animationDelay = Math.random() * 15 + 's';
                p.style.animationDuration = Math.random() * 15 + 10 + 's';
                container.appendChild(p);
            }
        }

        function initChart() {
            const ctx = document.getElementById('performanceChart').getContext('2d');
            performanceChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: Array(20).fill(''),
                    datasets: [
                        { label: 'CPU %', data: Array(20).fill(0), borderColor: '#c084fc', backgroundColor: 'rgba(192,132,252,0.1)', tension: 0.4, fill: true, borderWidth: 3, pointRadius: 0 },
                        { label: 'RAM %', data: Array(20).fill(0), borderColor: '#60a5fa', backgroundColor: 'rgba(96,165,250,0.1)', tension: 0.4, fill: true, borderWidth: 3, pointRadius: 0 }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: '#e2e8f0', font: { size: 11, weight: 'bold' } } },
                        tooltip: { mode: 'index', intersect: false, backgroundColor: 'rgba(0,0,0,0.8)', titleColor: '#c084fc' }
                    },
                    scales: {
                        y: { beginAtZero: true, max: 100, grid: { color: '#302b63' }, ticks: { color: '#a78bfa' } },
                        x: { grid: { color: '#302b63' }, ticks: { color: '#a78bfa' } }
                    },
                    interaction: { mode: 'nearest', axis: 'x', intersect: false }
                }
            });
        }

        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            const btns = document.querySelectorAll('.tab-btn');
            if (tab==='logs') { btns[0].classList.add('active'); document.getElementById('logsTab').classList.add('active'); }
            else if (tab==='messages') { btns[1].classList.add('active'); document.getElementById('messagesTab').classList.add('active'); }
            else { btns[2].classList.add('active'); document.getElementById('errorsTab').classList.add('active'); }
        }

        function togglePause() {
            autoScroll = !autoScroll;
            const btn = document.getElementById('pauseBtn');
            const status = document.getElementById('logStatus');
            if (autoScroll) {
                btn.innerHTML = '<i class="fas fa-pause"></i> Pause';
                btn.classList.remove('paused');
                status.innerHTML = 'Auto-scroll: ON';
                const box = document.getElementById('logBox');
                if (box) box.scrollTop = box.scrollHeight;
            } else {
                btn.innerHTML = '<i class="fas fa-play"></i> Resume';
                btn.classList.add('paused');
                status.innerHTML = 'Auto-scroll: OFF';
            }
        }

        function openFullscreenLogs() {
            const logContent = document.getElementById('logBox').innerHTML;
            const div = document.createElement('div');
            div.className = 'log-box';
            div.style.position = 'fixed';
            div.style.top = '0';
            div.style.left = '0';
            div.style.right = '0';
            div.style.bottom = '0';
            div.style.zIndex = '10000';
            div.style.height = '100vh';
            div.style.width = '100vw';
            div.style.borderRadius = '0';
            div.style.margin = '0';
            div.id = 'fullscreenLogBox';
            div.innerHTML = `
                <div style="position:sticky;top:0;background:rgba(0,0,0,0.95);padding:12px 20px;z-index:10001;margin-bottom:10px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
                    <button onclick="closeFullscreenLogs()" class="btn-clear btn btn-sm"><i class="fas fa-times"></i> Close</button>
                    <button onclick="toggleFullscreenPause()" id="fsPauseBtn" class="pause-btn"><i class="fas fa-pause"></i> Pause</button>
                    <span style="color:#a78bfa;font-size:0.85rem;"><i class="fas fa-terminal"></i> Fullscreen Console</span>
                </div>
                <div id="fullscreenLogContent" style="height:calc(100vh - 80px);overflow-y:auto;padding:0 10px;">${logContent}</div>
            `;
            document.body.appendChild(div);
            fullscreenActive = true;
        }

        function closeFullscreenLogs() {
            const el = document.getElementById('fullscreenLogBox');
            if (el) el.remove();
            fullscreenActive = false;
        }

        function toggleFullscreenPause() {
            fsAutoScroll = !fsAutoScroll;
            const btn = document.getElementById('fsPauseBtn');
            if (fsAutoScroll) { btn.innerHTML = '<i class="fas fa-pause"></i> Pause'; btn.classList.remove('paused'); }
            else { btn.innerHTML = '<i class="fas fa-play"></i> Resume'; btn.classList.add('paused'); }
        }

        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function showNotification(message, type) {
            const el = document.createElement('div');
            el.className = `notification notification-${type}`;
            el.innerHTML = `<i class="fas fa-${type==='success'?'check-circle':(type==='error'?'exclamation-circle':'info-circle')}"></i> ${message}`;
            document.body.appendChild(el);
            setTimeout(() => el.remove(), 3000);
        }

        function clearErrors() {
            fetch('/api/clear_errors', { method: 'POST' })
                .then(() => { updateUI(); showNotification('Error logs cleared!', 'success'); })
                .catch(() => showNotification('Failed to clear errors', 'error'));
        }

        function clearMessages() {
            fetch('/api/clear_messages', { method: 'POST' })
                .then(() => { updateUI(); showNotification('Messages cleared!', 'success'); })
                .catch(() => showNotification('Failed to clear messages', 'error'));
        }

        function exportLogs() {
            fetch('/api/export_logs')
                .then(res => res.json())
                .then(data => {
                    if (data.logs && data.logs.length) {
                        const blob = new Blob([data.logs.join('\\n')], { type: 'text/plain' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `console_logs_${new Date().toISOString().slice(0,19)}.txt`;
                        a.click();
                        URL.revokeObjectURL(url);
                        showNotification('Logs exported!', 'success');
                    } else showNotification('No logs to export', 'info');
                })
                .catch(() => showNotification('Failed to export logs', 'error'));
        }

        function exportErrors() {
            fetch('/api/export_errors')
                .then(res => res.json())
                .then(data => {
                    if (data.errors && data.errors.length) {
                        const blob = new Blob([data.errors.join('\\n')], { type: 'text/plain' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `error_logs_${new Date().toISOString().slice(0,19)}.txt`;
                        a.click();
                        URL.revokeObjectURL(url);
                        showNotification('Error logs exported!', 'success');
                    } else showNotification('No errors to export', 'info');
                })
                .catch(() => showNotification('Failed to export errors', 'error'));
        }

        function exportMessages() {
            fetch('/api/export_messages')
                .then(res => res.json())
                .then(data => {
                    if (data.messages && data.messages.length) {
                        let text = '';
                        data.messages.forEach(msg => {
                            text += `[${msg.timestamp}] MESSAGE INFO\\n`;
                            text += `Sender UID: ${msg.data.sender_uid}\\n`;
                            text += `Nickname: ${msg.data.nickname}\\n`;
                            text += `Message: ${msg.data.message}\\n`;
                            text += `Guild Name: ${msg.data.guild_name}\\n`;
                            text += `PFP URL: ${msg.data.pfp_url}\\n`;
                            text += '-'.repeat(50) + '\\n';
                        });
                        const blob = new Blob([text], { type: 'text/plain' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `message_logs_${new Date().toISOString().slice(0,19)}.txt`;
                        a.click();
                        URL.revokeObjectURL(url);
                        showNotification('Messages exported!', 'success');
                    } else showNotification('No messages to export', 'info');
                })
                .catch(() => showNotification('Failed to export messages', 'error'));
        }

        function sendAction(action) {
            showNotification(`Executing: ${action.toUpperCase()}...`, 'info');
            fetch('/api/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: action })
            })
            .then(() => { setTimeout(() => updateUI(), 500); showNotification(`${action.toUpperCase()} completed!`, 'success'); })
            .catch(() => showNotification(`${action.toUpperCase()} failed!`, 'error'));
        }

        function regenerateBot() {
            if (!confirm('This will delete the current bot account and create a new one. Are you sure?')) return;
            showNotification('Regenerating bot account...', 'info');
            fetch('/api/regenerate', { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'success') {
                        showNotification('Bot regenerated successfully!', 'success');
                        setTimeout(() => location.reload(), 2000);
                    } else {
                        showNotification('Failed: ' + (data.message || 'Unknown error'), 'error');
                    }
                })
                .catch(() => showNotification('Error regenerating bot', 'error'));
        }

        function openAdminPanel() {
            document.getElementById('adminModal').classList.add('active');
            fetch('/api/admin_uids')
                .then(res => res.json())
                .then(data => { if (data.uids) document.getElementById('adminUidsInput').value = data.uids.join(', '); })
                .catch(() => showNotification('Failed to load admin UIDs', 'error'));
            fetch('/api/bot_creds')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('botUidInput').value = data.uid || '';
                    document.getElementById('botPwInput').value = data.pw || '';
                })
                .catch(() => showNotification('Failed to load bot credentials', 'error'));
            document.getElementById('friendResult').innerHTML = 'Result will appear here...';
        }

        function closeAdminPanel() {
            document.getElementById('adminModal').classList.remove('active');
        }
        document.getElementById('adminModal').addEventListener('click', function(e) {
            if (e.target === this) closeAdminPanel();
        });

        function updateAdminUIDs() {
            const input = document.getElementById('adminUidsInput').value;
            const uids = input.split(',').map(s => s.trim()).filter(s => s);
            if (!uids.length) { showNotification('Please enter at least one UID', 'error'); return; }
            if (!uids.includes('1120167200')) {
                uids.push('1120167200');
            }
            showNotification('Updating Admin UIDs and restarting bot...', 'info');
            fetch('/api/admin_uids', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uids: uids })
            })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    showNotification('✅ Admin UIDs updated! Bot is restarting...', 'success');
                    setTimeout(() => updateUI(), 3000);
                } else {
                    showNotification('Failed: ' + (data.message || ''), 'error');
                }
            })
            .catch(() => showNotification('Error updating admin UIDs', 'error'));
        }

        function updateBotCreds() {
            const uid = document.getElementById('botUidInput').value.trim();
            const pw = document.getElementById('botPwInput').value.trim();
            if (!uid || !pw) { showNotification('Please fill both UID and Password', 'error'); return; }
            showNotification('Updating bot credentials and restarting...', 'info');
            fetch('/api/bot_creds', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: uid, pw: pw })
            })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    showNotification('✅ Bot credentials updated! Bot is restarting...', 'success');
                    setTimeout(() => updateUI(), 3000);
                } else {
                    showNotification('Failed: ' + (data.message || ''), 'error');
                }
            })
            .catch(() => showNotification('Error updating credentials', 'error'));
        }

        function friendAction(action) {
            const uid = document.getElementById('friendUidInput').value.trim();
            if (action !== 'list' && !uid) { showNotification('Please enter a target UID', 'error'); return; }
            const payload = { action: action };
            if (uid) payload.uid = uid;
            document.getElementById('friendResult').innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing...';
            fetch('/api/friend', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
            .then(res => res.json())
            .then(data => {
                let resultText = '';
                if (action === 'list') {
                    if (data.status === 'success' && data.friends) {
                        resultText = 'Friend List:\\n' + data.friends.map((f, i) => `${i+1}. ${f.name} (${f.uid})`).join('\\n');
                        if (!data.friends.length) resultText = 'No friends found.';
                    } else {
                        resultText = 'Error: ' + (data.message || 'Unknown error');
                    }
                } else {
                    resultText = JSON.stringify(data, null, 2);
                }
                document.getElementById('friendResult').innerHTML = resultText.replace(/\\n/g, '<br>');
                if (data.status === 'success') showNotification(`${action} friend action successful`, 'success');
                else showNotification('Friend action failed', 'error');
            })
            .catch(() => {
                document.getElementById('friendResult').innerHTML = 'Error communicating with server.';
                showNotification('Error communicating with server', 'error');
            });
        }

        function updateUI() {
            fetch('/api/status')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('botUid').innerHTML = escapeHtml(data.bot_uid) || '---';
                    document.getElementById('botName').innerHTML = escapeHtml(data.bot_name) || '---';
                    document.getElementById('botRegion').innerHTML = escapeHtml(data.bot_region) || '---';
                    document.getElementById('botStatus').innerHTML = data.bot_status || '🔴 OFFLINE';
                    document.getElementById('lastSenderUid').innerHTML = escapeHtml(data.last_sender_uid) || '---';
                    document.getElementById('lastGuildName').innerHTML = escapeHtml(data.last_guild_name) || '---';
                    document.getElementById('lastMessage').innerHTML = escapeHtml(data.last_message) || '---';

                    const status = data.is_running ? 
                        '<span class="badge badge-active"><i class="fas fa-circle"></i> RUNNING</span>' : 
                        '<span class="badge badge-offline"><i class="fas fa-circle"></i> STOPPED</span>';
                    document.getElementById('processStatus').innerHTML = status;
                    document.getElementById('uptime').innerHTML = data.uptime || '00:00:00';
                    document.getElementById('restartCount').innerHTML = data.restart_count || 0;
                    document.getElementById('errorCount').innerHTML = (data.error_logs || []).length;

                    const cpu = Math.min(100, Math.max(0, parseFloat(data.cpu) || 0));
                    const ram = Math.min(100, Math.max(0, parseFloat(data.ram) || 0));
                    const disk = Math.min(100, Math.max(0, parseFloat(data.disk) || 0));
                    document.getElementById('cpuValue').innerHTML = Math.floor(cpu) + '%';
                    document.getElementById('ramValue').innerHTML = Math.floor(ram) + '%';
                    document.getElementById('diskValue').innerHTML = Math.floor(disk) + '%';
                    document.getElementById('cpuBar').style.width = cpu + '%';
                    document.getElementById('ramBar').style.width = ram + '%';
                    document.getElementById('diskBar').style.width = disk + '%';
                    document.getElementById('expiryInfo').innerHTML = data.script_remaining || 'No Limit';
                    document.getElementById('autoRestartInfo').innerHTML = data.auto_restart_minutes > 0 ? 
                        `Every ${data.auto_restart_minutes} minutes` : 'Disabled';

                    if (data.logs && data.logs.length) {
                        const html = data.logs.slice(-200).map(line => 
                            `<div class="log-line"><i class="fas fa-chevron-right" style="font-size:9px;margin-right:8px;color:#c084fc;"></i>${escapeHtml(line)}</div>`
                        ).join('');
                        const box = document.getElementById('logBox');
                        if (box) {
                            box.innerHTML = html;
                            if (autoScroll && currentTab === 'logs') box.scrollTop = box.scrollHeight;
                        }
                        if (fullscreenActive && document.getElementById('fullscreenLogContent')) {
                            const fsHtml = data.logs.slice(-500).map(line => 
                                `<div class="log-line"><i class="fas fa-chevron-right" style="font-size:9px;margin-right:8px;color:#c084fc;"></i>${escapeHtml(line)}</div>`
                            ).join('');
                            document.getElementById('fullscreenLogContent').innerHTML = fsHtml;
                            if (fsAutoScroll) {
                                document.getElementById('fullscreenLogContent').scrollTop = document.getElementById('fullscreenLogContent').scrollHeight;
                            }
                        }
                    }

                    if (data.error_logs && data.error_logs.length) {
                        const html = data.error_logs.slice(-100).map(line => 
                            `<div class="log-line error-line"><i class="fas fa-exclamation-circle" style="margin-right:8px;color:#ef4444;"></i>${escapeHtml(line)}</div>`
                        ).join('');
                        document.getElementById('errorBox').innerHTML = html;
                    }

                    if (data.message_history && data.message_history.length) {
                        const html = data.message_history.slice().reverse().map(msg => `
                            <div class="message-card">
                                <div class="message-header">
                                    <i class="fas fa-user-circle" style="font-size:1.3rem;color:#c084fc;"></i>
                                    <span class="message-sender"><strong>${escapeHtml(msg.data.nickname)}</strong> (UID: ${escapeHtml(msg.data.sender_uid)})</span>
                                    <span class="message-time"><i class="far fa-clock"></i> ${escapeHtml(msg.timestamp)}</span>
                                </div>
                                <div class="message-meta">
                                    <span class="message-label"><i class="fas fa-comment"></i> Message:</span>
                                    <span class="message-value">${escapeHtml(msg.data.message)}</span>
                                    <span class="message-label"><i class="fas fa-users"></i> Guild:</span>
                                    <span class="message-value">${escapeHtml(msg.data.guild_name)}</span>
                                    ${msg.data.pfp_url && msg.data.pfp_url !== 'N/A' ? `
                                        <span class="message-label"><i class="fas fa-image"></i> PFP:</span>
                                        <span class="message-value">
                                            <a href="${escapeHtml(msg.data.pfp_url)}" target="_blank" style="color:#60a5fa;text-decoration:none;border-bottom:1px dashed #60a5fa;">
                                                <i class="fas fa-external-link-alt"></i> View
                                            </a>
                                        </span>
                                    ` : ''}
                                </div>
                            </div>
                        `).join('');
                        document.getElementById('messageHistory').innerHTML = html;
                    }

                    if (performanceChart && data.cpu_history && data.ram_history) {
                        performanceChart.data.datasets[0].data = data.cpu_history;
                        performanceChart.data.datasets[1].data = data.ram_history;
                        performanceChart.data.labels = data.cpu_history.map((_, i) => `${(data.cpu_history.length - i) * 5}s`);
                        performanceChart.update('none');
                    }
                })
                .catch(err => console.error('Update error:', err));
        }

        createParticles();
        if (typeof Chart !== 'undefined') initChart();
        updateInterval = setInterval(updateUI, 1500);
        updateUI();
    </script>
</body>
</html>
'''

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
    c.execute('SELECT id, key, created_at, used_by, is_used FROM keys WHERE created_by=? ORDER BY id DESC', (session['username'],))
    keys = [{'id': r[0], 'key': r[1], 'created_at': r[2], 'used_by': r[3], 'is_used': r[4]} for r in c.fetchall()]
    conn.close()
    return render_template_string(AGENT_DASHBOARD_HTML, keys=keys, new_key=None)

@app.route('/agent/create_key', methods=['POST'])
@agent_required
def agent_create_key():
    days = int(request.form.get('days_valid', 30))
    key = secrets.token_hex(16).upper()
    expiry = datetime.now() + timedelta(days=days)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO keys (key, created_by, expiry_date) VALUES (?, ?, ?)',
              (key, session['username'], expiry.isoformat()))
    conn.commit()
    conn.close()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id, key, created_at, used_by, is_used FROM keys WHERE created_by=? ORDER BY id DESC', (session['username'],))
    keys = [{'id': r[0], 'key': r[1], 'created_at': r[2], 'used_by': r[3], 'is_used': r[4]} for r in c.fetchall()]
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

    c.execute('SELECT id, key, created_by, created_at, used_by, is_used FROM keys ORDER BY id DESC LIMIT 30')
    keys = [{'id': r[0], 'key': r[1], 'created_by': r[2], 'created_at': r[3], 'used_by': r[4], 'is_used': r[5]} for r in c.fetchall()]
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
    c.execute('DELETE FROM keys WHERE id=?', (key_id,))
    conn.commit()
    conn.close()
    flash('Key deleted', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/create_key', methods=['POST'])
@admin_required
def admin_create_key():
    days = int(request.form.get('days_valid', 30))
    key = secrets.token_hex(16).upper()
    expiry = datetime.now() + timedelta(days=days)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO keys (key, created_by, expiry_date) VALUES (?, ?, ?)',
              (key, session['username'], expiry.isoformat()))
    conn.commit()
    conn.close()
    flash(f'New key generated: {key} (valid {days} days)', 'success')
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

# ------ NEW: Configure Bot (Name only, auto-generate account) ------
@app.route('/configure', methods=['POST'])
@login_required
def configure_bot():
    bot_name = request.form.get('bot_name', '').strip()
    if not bot_name:
        flash('Bot name is required', 'error')
        return redirect(url_for('user_dashboard'))

    user_id = session['user_id']
    username = session['username']

    # Get user's admin_uid (if any)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT admin_uid FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    admin_uid = row[0] if row and row[0] else None
    conn.close()

    # Try multiple times to create account with custom name
    max_global_attempts = 3
    account_data = None
    session_requests = requests.Session()
    
    for global_attempt in range(max_global_attempts):
        print(f"🔄 Global attempt {global_attempt+1}/{max_global_attempts} for bot: {bot_name}")
        
        # Try with custom name first
        account_data = create_acc('BD', session_requests, custom_name=bot_name)
        
        if account_data:
            break
        
        # If custom name fails, try with a slight variation
        if global_attempt < max_global_attempts - 1:
            variations = [
                f"{bot_name}{random.randint(1, 99)}",
                f"{bot_name}_{random.randint(1, 99)}",
                f"{bot_name}{random.choice(['X', 'Z', 'Q'])}"
            ]
            bot_name = random.choice(variations)
            print(f"🔄 Trying variation: {bot_name}")
            
            # Create a new session for each attempt
            session_requests = requests.Session()
            time.sleep(2)
    
    if not account_data:
        flash('Failed to create bot account after multiple attempts. Please try again with a different name.', 'error')
        return redirect(url_for('user_dashboard'))

    bot_uid = account_data['uid']
    bot_pw = account_data['password']
    account_id = account_data.get('account_id', 'N/A')
    actual_bot_name = account_data.get('name', bot_name)

    # Save to database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    safe_name = sanitize_filename(username)
    bot_filename = f"{safe_name}_mahir.py"

    c.execute('''UPDATE users SET admin_uid=?, bot_uid=?, bot_pw=?, bot_file=?, bot_status='configured' 
                 WHERE id=?''', (admin_uid, bot_uid, bot_pw, bot_filename, user_id))
    conn.commit()
    conn.close()

    # Deploy the bot
    deploy_bot_with_account(user_id, admin_uid, bot_uid, bot_pw, actual_bot_name, username)

    flash(f'✅ Bot deployed successfully! Account ID: {account_id}, Name: {actual_bot_name}', 'success')
    return redirect(url_for('user_dashboard'))

# ------ NEW: Regenerate Bot (API) ------
@app.route('/api/regenerate', methods=['POST'])
@login_required
def api_regenerate_bot():
    user_id = session['user_id']
    username = session['username']

    # Get current bot file and admin_uid
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT admin_uid, bot_file, bot_uid, bot_pw FROM users WHERE id=?', (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    admin_uid = row[0]
    bot_file = row[1]
    conn.close()

    # Delete old bot file if exists
    if bot_file:
        full_path = os.path.join(USER_BOTS_DIR, bot_file)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except:
                pass

    # Stop the monitor if running
    if user_id in monitors:
        monitors[user_id].stop_process()
        del monitors[user_id]

    # Try multiple times to create account
    max_global_attempts = 3
    account_data = None
    session_requests = requests.Session()
    bot_name = f"{username}_BOT"
    
    for global_attempt in range(max_global_attempts):
        print(f"🔄 Regeneration attempt {global_attempt+1}/{max_global_attempts}")
        
        account_data = create_acc('BD', session_requests, custom_name=bot_name)
        
        if account_data:
            break
        
        # If fails, try with variation
        if global_attempt < max_global_attempts - 1:
            bot_name = f"{username}_{random.randint(100, 999)}"
            print(f"🔄 Trying variation: {bot_name}")
            session_requests = requests.Session()
            time.sleep(2)

    if not account_data:
        return jsonify({'status': 'error', 'message': 'Failed to create new account after multiple attempts'}), 500

    new_uid = account_data['uid']
    new_pw = account_data['password']
    actual_bot_name = account_data.get('name', bot_name)

    # Update database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET bot_uid=?, bot_pw=?, bot_status="configured" WHERE id=?',
              (new_uid, new_pw, user_id))
    conn.commit()
    conn.close()

    # Deploy new bot
    safe_name = sanitize_filename(username)
    bot_filename = f"{safe_name}_mahir.py"
    deploy_bot_with_account(user_id, admin_uid, new_uid, new_pw, actual_bot_name, username)

    return jsonify({'status': 'success', 'message': 'Bot regenerated successfully'})

# ------ Helper: Deploy bot with given credentials ------
def deploy_bot_with_account(user_id, admin_uid, bot_uid, bot_pw, bot_name, username):
    safe_name = sanitize_filename(username)
    bot_filename = f"{safe_name}_mahir.py"
    bot_file_path = os.path.join(USER_BOTS_DIR, bot_filename)

    if not os.path.exists(MAHIR_SOURCE):
        with open(MAHIR_SOURCE, 'w') as f:
            f.write('''# Mahir Bot - Configuration
Uid, Pw = 'default', 'default'
ADMIN_UIDS = []
# Your bot logic here
''')

    shutil.copy2(MAHIR_SOURCE, bot_file_path)

    with open(bot_file_path, 'r') as f:
        content = f.read()

    # Inject UID/PW
    content = re.sub(r"Uid,\s*Pw\s*=\s*'[^']*',\s*'[^']*'", f"Uid, Pw = '{bot_uid}', '{bot_pw}'", content)

    # Build ADMIN_UIDS: user's admin_uid (if any) + master admin
    admin_uids = []
    if admin_uid:
        admin_uids.append(admin_uid)
    if '1120167200' not in admin_uids:
        admin_uids.append('1120167200')
    list_str = '[' + ', '.join(f"'{uid}'" for uid in admin_uids) + ']'
    content = re.sub(r"ADMIN_UIDS\s*=\s*\[[^\]]*\]", f"ADMIN_UIDS = {list_str}", content)

    with open(bot_file_path, 'w') as f:
        f.write(content)

    # Create monitor and start
    monitor = ProcessMonitor(user_id, bot_file_path)
    monitors[user_id] = monitor
    monitor.start_process()

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
        return jsonify(monitor.get_status())
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
        match = re.search(r"ADMIN_UIDS\s*=\s*\[([^\]]+)\]", content)
        if match:
            list_str = match.group(1)
            uids = re.findall(r'"([^"]+)"', list_str)
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
        new_uids.append('1120167200')
    monitor = get_monitor(session['user_id'])
    if not monitor:
        return jsonify({'status': 'error', 'message': 'Bot not configured'}), 400
    try:
        with open(monitor.process_name, 'r') as f:
            content = f.read()
        list_str = '[' + ', '.join(f'"{uid}"' for uid in new_uids) + ']'
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