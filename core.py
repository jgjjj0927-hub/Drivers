import sqlite3
import hashlib
import os
import secrets
import base64
import platform
import uuid
import wmi
from pathlib import Path
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
import requests

BASE = Path(__file__).parent / "base"
BASE.mkdir(exist_ok=True)
DB = BASE / "licenses.db"
SECRET_FILE = BASE / ".secret"
TG_TOKEN = BASE / "tg_token.txt"
TG_CHAT = BASE / "tg_chat.txt"
BOT_TOKEN = BASE / "bot_token.txt"
BOT_ADMIN = BASE / "bot_admin.txt"

PREFIX_POOL = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
KEY_DAYS = {"trial": 7, "month": 30, "year": 365, "for_good": 0}


def get_secret() -> bytes:
    if not SECRET_FILE.exists():
        SECRET_FILE.write_bytes(os.urandom(32))
    return SECRET_FILE.read_bytes()


def machine_id() -> str:
    try:
        mac = uuid.getnode()
        raw = f"{platform.node()}-{mac}-{platform.processor()}"
    except Exception:
        raw = f"{platform.node()}-{platform.processor()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _aes_encrypt(data: bytes) -> str:
    key = get_secret()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.urlsafe_b64encode(iv + ct).decode().rstrip("=")


def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            key_enc TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            days INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            activated_at TEXT,
            machine_id TEXT,
            active INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def _make_key() -> str:
    return (
        f"{secrets.randbelow(10000):04d}-"
        f"{''.join(secrets.choice(PREFIX_POOL) for _ in range(4))}-"
        f"{secrets.randbelow(100):02d}"
    )


def create_key(key_type: str, days: int = None) -> str:
    init_db()
    if days is None:
        days = KEY_DAYS.get(key_type, 0)
    if key_type == "custom" and days <= 0:
        raise ValueError("custom дни должны быть > 0")
    conn = sqlite3.connect(DB)
    for _ in range(10000):
        key = _make_key()
        enc = _aes_encrypt(key.encode())
        if not conn.execute("SELECT 1 FROM licenses WHERE key_enc=?", (enc,)).fetchone():
            break
    else:
        conn.close()
        raise RuntimeError("не удалось сгенерировать ключ")
    conn.execute(
        "INSERT INTO licenses (key_enc, type, days, created_at) VALUES (?, ?, ?, ?)",
        (enc, key_type, days, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return key


def activate(key: str) -> tuple[bool, str]:
    key = key.strip().upper()
    if len(key.split("-")) != 3:
        return False, "неверный формат ключа"
    init_db()
    enc = _aes_encrypt(key.encode())
    mid = machine_id()
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT type, days, activated_at, machine_id, active FROM licenses WHERE key_enc=?",
        (enc,),
    ).fetchone()
    if not row:
        conn.close()
        return False, "ключ не найден"
    ltype, days, activated_at, saved_mid, active = row
    if active:
        conn.close()
        if saved_mid == mid:
            return False, "ключ уже активирован на этой машине"
        return False, "ключ одноразовый"
    conn.execute(
        "UPDATE licenses SET activated_at=?, machine_id=?, active=1 WHERE key_enc=?",
        (datetime.now().isoformat(), mid, enc),
    )
    conn.commit()
    conn.close()
    return True, f"активирован: {ltype}"


def is_activated() -> bool:
    init_db()
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT type, days, activated_at FROM licenses WHERE active=1 AND machine_id=?",
        (machine_id(),),
    ).fetchone()
    conn.close()
    if not row:
        return False
    ltype, days, activated_at = row
    if ltype == "for_good":
        return True
    if activated_at:
        expiry = datetime.fromisoformat(activated_at) + timedelta(days=days)
        return datetime.now() <= expiry
    return False


class DriverInfo:
    def __init__(self, name, device_id, version, date, provider):
        self.name = name
        self.device_id = device_id
        self.version = version
        self.date = date
        self.provider = provider


def _parse_date(raw):
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt)], fmt)
        except ValueError:
            continue
    return None


def scan_drivers():
    c = wmi.WMI()
    out = []
    for dev in c.Win32_PnPSignedDriver():
        if not dev.DeviceName:
            continue
        out.append(DriverInfo(
            name=dev.DeviceName,
            device_id=dev.DeviceID or "—",
            version=dev.DriverVersion or "—",
            date=dev.DriverDate or "—",
            provider=dev.DriverProviderName or "—",
        ))
    return out


def find_old_drivers(drivers, years_old=5):
    now = datetime.now()
    threshold = timedelta(days=365 * years_old)
    return [d for d in drivers if (dt := _parse_date(d.date)) and (now - dt) > threshold]


def set_tg(token: str, chat: str) -> bool:
    token, chat = token.strip(), chat.strip()
    if not token or ":" not in token or not chat:
        return False
    TG_TOKEN.write_text(token, encoding="utf-8")
    TG_CHAT.write_text(chat, encoding="utf-8")
    return True


def send_report(text: str) -> tuple[bool, str]:
    if not TG_TOKEN.exists() or not TG_CHAT.exists():
        return False, "токен или chat_id не заданы"
    token = TG_TOKEN.read_text(encoding="utf-8").strip()
    chat = TG_CHAT.read_text(encoding="utf-8").strip()
    if not token or not chat:
        return False, "токен или chat_id пусты"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        try:
            r = requests.post(url, data={"chat_id": chat, "text": chunk}, timeout=15)
        except Exception as e:
            return False, str(e)
        if r.status_code != 200:
            return False, f"ошибка: {r.status_code}"
    return True, "отправлено"
