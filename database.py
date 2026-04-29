import sqlite3
import os
import bcrypt
from cryptography.fernet import Fernet
from datetime import datetime

DB_PATH = os.path.expanduser("~/diagnostic-tool/nexdeploy.db")
KEY_PATH = os.path.expanduser("~/diagnostic-tool/.secret_key")

def get_encryption_key():
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(KEY_PATH, "wb") as f:
            f.write(key)
        os.chmod(KEY_PATH, 0o600)
        return key

def encrypt_password(password: str) -> str:
    key = get_encryption_key()
    f = Fernet(key)
    return f.encrypt(password.encode()).decode()

def decrypt_password(encrypted: str) -> str:
    key = get_encryption_key()
    f = Fernet(key)
    return f.decrypt(encrypted.encode()).decode()

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'engineer',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        ip TEXT NOT NULL,
        username TEXT,
        password_encrypted TEXT,
        brand TEXT,
        model TEXT,
        added_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        token TEXT UNIQUE,
        expires_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    conn.commit()
    conn.close()
    print("Database initialized successfully")

def create_user(username: str, password: str, role: str = "engineer"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, hash_password(password), role)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_user(username: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "username": row[1],
            "password_hash": row[2],
            "role": row[3]
        }
    return None

def add_device(name, device_type, ip, username, password, brand="", model="", added_by="admin"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    encrypted = encrypt_password(password) if password else ""
    c.execute(
        "INSERT INTO devices (name, type, ip, username, password_encrypted, brand, model, added_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, device_type, ip, username, encrypted, brand, model, added_by)
    )
    conn.commit()
    conn.close()

def get_devices(device_type=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if device_type:
        c.execute("SELECT * FROM devices WHERE type = ?", (device_type,))
    else:
        c.execute("SELECT * FROM devices")
    rows = c.fetchall()
    conn.close()
    devices = []
    for row in rows:
        devices.append({
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "ip": row[3],
            "username": row[4],
            "password": decrypt_password(row[5]) if row[5] else "",
            "brand": row[6],
            "model": row[7],
            "added_by": row[8]
        })
    return devices

def log_action(username, action, details=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO audit_log (username, action, details) VALUES (?, ?, ?)",
        (username, action, details)
    )
    conn.commit()
    conn.close()

def is_first_run():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    count = c.fetchone()[0]
    conn.close()
    return count == 0

if __name__ == "__main__":
    init_db()
    print(f"Database created at: {DB_PATH}")
    print(f"Encryption key at: {KEY_PATH}")

    if is_first_run():
        print("\nFirst run detected — creating admin account...")
        create_user("admin", "admin123", "admin")
        print("Admin account created:")
        print("  Username: admin")
        print("  Password: admin123")
        print("  CHANGE THIS PASSWORD AFTER FIRST LOGIN")

        print("\nAdding lab devices...")
        add_device(
            name="S2D-Node1",
            device_type="ilo",
            ip="192.168.99.104",
            username="cdfadmin",
            password="QweAsd!23cdf",
            brand="HPE",
            model="ProLiant DL380 Gen10"
        )
        add_device(
            name="Server 2",
            device_type="ilo",
            ip="192.168.99.102",
            username="cdfadmin",
            password="QweAsd!23cdf",
            brand="HPE",
            model="ProLiant DL380 Gen10"
        )
        print("Lab devices added successfully")

    print("\nAll done!")
