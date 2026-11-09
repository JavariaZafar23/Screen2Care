# Self sign-up and admin approval of staff accounts.
import json
from datetime import date
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.auth import hash_password
from app.database import engine

STAFF_SIGNUP_ROLES = ("Clinician", "Radiologist", "Pathologist", "Navigator", "CMO",
                      "SchedulingManager", "LabDirector", "TumorBoard")
NO_MATCH = "We could not match these details to a patient record. Check them, or ask the front desk."


class _Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def strong_enough(cls, v: str) -> str:
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Use at least 8 characters with letters and numbers.")
        return v


class PatientSignupIn(_Credentials):
    patient_id: str = Field(min_length=3, max_length=20)
    date_of_birth: date
    national_id: str = Field(min_length=3, max_length=30)


class StaffSignupIn(_Credentials):
    full_name: str = Field(min_length=2, max_length=150)
    role: Literal["Clinician", "Radiologist", "Pathologist", "Navigator", "CMO",
                  "SchedulingManager", "LabDirector", "TumorBoard"]


def _username_taken(conn, username: str) -> bool:
    return conn.execute(text("""
        SELECT 1 FROM app_user WHERE lower(username) = lower(:u)
        UNION ALL
        SELECT 1 FROM signup_request WHERE lower(username) = lower(:u) AND status = 'Pending'
    """), {"u": username}).first() is not None


def signup_patient(data: PatientSignupIn) -> dict:
    pid = data.patient_id.strip().upper()
    with engine.begin() as conn:
        if _username_taken(conn, data.username):
            raise HTTPException(status_code=409, detail="That username is taken. Choose another.")
        match = conn.execute(text("""
            SELECT 1 FROM patient
            WHERE patient_id = :p AND date_of_birth = :d
              AND national_id IS NOT NULL AND upper(national_id) = upper(:n)
        """), {"p": pid, "d": data.date_of_birth, "n": data.national_id.strip()}).first()
        if not match:
            raise HTTPException(status_code=422, detail=NO_MATCH)
        if conn.execute(text("SELECT 1 FROM app_user WHERE patient_id = :p"), {"p": pid}).first():
            raise HTTPException(status_code=409, detail="This patient already has an account. Log in instead.")
        conn.execute(text("""
            INSERT INTO app_user (username, password_hash, role, patient_id) VALUES (:u, :h, 'Patient', :p)
        """), {"u": data.username, "h": hash_password(data.password), "p": pid})
        conn.execute(text("""
            INSERT INTO event_log (patient_id, event_type, details) VALUES (:p, 'PatientSignedUp', CAST(:d AS JSONB))
        """), {"p": pid, "d": json.dumps({"username": data.username})})
    return {"status": "active", "message": "Account created. You can log in now."}


def signup_staff(data: StaffSignupIn) -> dict:
    with engine.begin() as conn:
        if _username_taken(conn, data.username):
            raise HTTPException(status_code=409, detail="That username is taken. Choose another.")
        conn.execute(text("""
            INSERT INTO signup_request (username, password_hash, full_name, role) VALUES (:u, :h, :n, :r)
        """), {"u": data.username, "h": hash_password(data.password), "n": data.full_name.strip(), "r": data.role})
    return {"status": "pending", "message": "Request sent. An administrator must approve it before you can log in."}


def pending_requests() -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text("""
            SELECT request_id, username, full_name, role, requested_at FROM signup_request
            WHERE status = 'Pending' ORDER BY requested_at
        """)).mappings()]


def decide_request(request_id: int, approve: bool, admin_staff_id: int) -> dict:
    with engine.begin() as conn:
        req = conn.execute(text(
            "SELECT * FROM signup_request WHERE request_id = :r AND status = 'Pending' FOR UPDATE"),
            {"r": request_id}).mappings().first()
        if req is None:
            raise HTTPException(status_code=404, detail="Request not found or already decided")
        if approve:
            if conn.execute(text("SELECT 1 FROM app_user WHERE lower(username) = lower(:u)"),
                            {"u": req["username"]}).first():
                raise HTTPException(status_code=409, detail="That username is now taken; reject this request.")
            staff_id = conn.execute(text(
                "INSERT INTO staff (full_name, role) VALUES (:n, :r) RETURNING staff_id"),
                {"n": req["full_name"], "r": req["role"]}).scalar()
            conn.execute(text("""
                INSERT INTO app_user (username, password_hash, role, staff_id) VALUES (:u, :h, :r, :s)
            """), {"u": req["username"], "h": req["password_hash"], "r": req["role"], "s": staff_id})
        conn.execute(text("""
            UPDATE signup_request SET status = :st, decided_at = now(), decided_by = :a WHERE request_id = :r
        """), {"st": "Approved" if approve else "Rejected", "a": admin_staff_id, "r": request_id})
    return {"request_id": request_id, "approved": approve}
