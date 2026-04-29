import sqlite3
import os
import secrets
from datetime import datetime, timedelta
from database import verify_password, get_user, log_action

DB_PATH = os.path.expanduser("~/diagnostic-tool/nexdeploy.db")

def create_session(username: str) -> str:
    token = secrets.token_hex(32)
    expires = datetime.now() + timedelta(hours=8)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO sessions (username, token, expires_at) VALUES (?, ?, ?)",
        (username, token, expires)
    )
    conn.commit()
    conn.close()
    log_action(username, "LOGIN", f"Session created")
    return token

def verify_session(token: str):
    if not token:
        return None
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT username, expires_at FROM sessions WHERE token = ?",
        (token,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    username, expires_at = row
    if datetime.now() > datetime.fromisoformat(expires_at):
        return None
    return username

def login(username: str, password: str):
    user = get_user(username)
    if not user:
        return None, "Invalid username or password"
    if not verify_password(password, user["password_hash"]):
        return None, "Invalid username or password"
    token = create_session(username)
    return token, None

def logout(token: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    token, error = login("admin", "admin123")
    if token:
        print(f"Login successful — token: {token[:20]}...")
        user = verify_session(token)
        print(f"Session valid for user: {user}")
    else:
        print(f"Login failed: {error}")
