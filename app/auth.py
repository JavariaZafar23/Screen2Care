import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import text

from app.database import engine

JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-change-me")
JWT_ALG = "HS256"
TOKEN_HOURS = 8
COOKIE_NAME = "access_token"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_user(username, password, role, staff_id=None, patient_id=None) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO app_user (username, password_hash, role, staff_id, patient_id)
            VALUES (:u, :h, :r, :s, :p)
            ON CONFLICT (username) DO NOTHING
        """), {"u": username, "h": hash_password(password), "r": role, "s": staff_id, "p": patient_id})


def authenticate(username: str, password: str):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM app_user WHERE username = :u AND is_active"), {"u": username}
        ).mappings().first()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    user = dict(row)
    with engine.connect() as conn:
        user["display_name"] = conn.execute(text("""
            SELECT COALESCE((SELECT full_name FROM staff WHERE staff_id = :s),
                            (SELECT full_name FROM patient WHERE patient_id = :p))
        """), {"s": user["staff_id"], "p": user["patient_id"]}).scalar() or user["username"]
    return user


def create_token(user: dict) -> str:
    payload = {
        "sub": str(user["user_id"]),
        "role": user["role"],
        "staff_id": user["staff_id"],
        "patient_id": user["patient_id"],
        "name": user.get("display_name") or user["username"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def get_current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
    if not token:
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid")


def require_role(*roles: str):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Not allowed for this role")
        return user
    return checker

