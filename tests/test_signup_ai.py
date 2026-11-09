# Sign-up, admin approval, patient-note release and the AI endpoints.
# The local model is replaced by a fake here, so these tests run without Ollama.
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import llm
from app.database import engine
from app.main import app
from app.seed_users import DEV_PASSWORD

TEST_PID = "TEST-SIGNUP-01"
STAFF_USER = "test_signup_staff"
PATIENT_USER = "test_signup_patient"


def sql(q, **p):
    with engine.begin() as conn:
        return conn.execute(text(q), p)


def logged_in(username: str, password: str = DEV_PASSWORD) -> TestClient:
    c = TestClient(app)
    assert c.post("/api/login", json={"username": username, "password": password}).status_code == 200
    return c


def can_login(username: str, password: str) -> bool:
    return TestClient(app).post("/api/login", json={"username": username, "password": password}).status_code == 200


@pytest.fixture
def signup_cleanup():
    def clean():
        sql("DELETE FROM event_log WHERE patient_id = :p", p=TEST_PID)
        sql("DELETE FROM app_user WHERE username IN (:a, :b) OR patient_id = :p", a=STAFF_USER, b=PATIENT_USER, p=TEST_PID)
        sql("DELETE FROM signup_request WHERE username = :a", a=STAFF_USER)
        sql("DELETE FROM staff WHERE full_name = 'Test Signup Clinician'")
        sql("DELETE FROM patient WHERE patient_id = :p", p=TEST_PID)
    clean()
    sql("""INSERT INTO patient (patient_id, full_name, date_of_birth, national_id, created_by)
           VALUES (:p, 'Test Signup', '1980-02-03', 'NID-TEST-777', 1)""", p=TEST_PID)
    yield
    clean()


# ---------- Patient sign-up ---------------------------------------------------------

def test_patient_signup_matches_record_and_can_log_in(signup_cleanup):
    body = {"username": PATIENT_USER, "password": "Secret123", "patient_id": TEST_PID.lower(),
            "date_of_birth": "1980-02-03", "national_id": "nid-test-777"}
    res = TestClient(app).post("/api/signup/patient", json=body)
    assert res.status_code == 201 and res.json()["status"] == "active"
    page = logged_in(PATIENT_USER, "Secret123").get("/").text
    assert TEST_PID in page

    # A second account for the same patient is refused
    again = TestClient(app).post("/api/signup/patient", json={**body, "username": PATIENT_USER + "2"})
    assert again.status_code == 409


def test_patient_signup_rejects_wrong_details(signup_cleanup):
    res = TestClient(app).post("/api/signup/patient", json={
        "username": PATIENT_USER, "password": "Secret123", "patient_id": TEST_PID,
        "date_of_birth": "1980-02-04", "national_id": "NID-TEST-777"})
    assert res.status_code == 422
    assert "could not match" in res.json()["detail"]
    assert not can_login(PATIENT_USER, "Secret123")


def test_weak_password_and_taken_username_are_refused(signup_cleanup):
    base = {"patient_id": TEST_PID, "date_of_birth": "1980-02-03", "national_id": "NID-TEST-777"}
    weak = TestClient(app).post("/api/signup/patient", json={**base, "username": PATIENT_USER, "password": "short"})
    assert weak.status_code == 422
    taken = TestClient(app).post("/api/signup/patient", json={**base, "username": "admin1", "password": "Secret123"})
    assert taken.status_code == 409


# ---------- Staff sign-up needs admin approval ---------------------------------------

def test_staff_signup_waits_for_admin_approval(signup_cleanup):
    res = TestClient(app).post("/api/signup/staff", json={
        "username": STAFF_USER, "password": "Secret123", "full_name": "Test Signup Clinician", "role": "Clinician"})
    assert res.status_code == 201 and res.json()["status"] == "pending"
    assert not can_login(STAFF_USER, "Secret123")

    assert logged_in("clinician1").get("/admin/accounts").status_code == 403
    admin = logged_in("admin1")
    assert "Test Signup Clinician" in admin.get("/admin/accounts").text
    rid = sql("SELECT request_id FROM signup_request WHERE username = :u AND status = 'Pending'", u=STAFF_USER).scalar()
    assert admin.post(f"/api/admin/signup-requests/{rid}/approve").status_code == 200

    new = logged_in(STAFF_USER, "Secret123")
    assert "Patients assigned to you" in new.get("/").text
    assert new.get("/api/patients").json() == []          # no patients until assigned


def test_admin_role_cannot_be_requested(signup_cleanup):
    res = TestClient(app).post("/api/signup/staff", json={
        "username": STAFF_USER, "password": "Secret123", "full_name": "Test Signup Clinician", "role": "Admin"})
    assert res.status_code == 422


# ---------- Patient note: AI draft, clinician release ---------------------------------

def fake_chat_echo(messages, tools=None, temperature=0.2):
    return {"role": "assistant", "content": "Here is the rewritten note:\n\nYou need a small test called a biopsy."}


def test_draft_and_release_patient_note(monkeypatch):
    monkeypatch.setattr(llm, "ollama_chat", fake_chat_echo)
    clinician = logged_in("clinician1")
    draft = clinician.post("/api/patients/BC-HP-004/draft-note")
    assert draft.status_code == 200
    assert draft.json()["draft"] == "You need a small test called a biopsy."     # chatty lead-in removed

    text_ = "TEST RELEASE: your mammogram showed dense breast tissue; we will discuss extra imaging."
    try:
        assert clinician.post("/api/patients/BC-HP-004/release-note",
                              json={"note_text": text_, "ai_drafted": True}).status_code == 200
        assert text_ in logged_in("patient4").get("/").text
        assert text_ not in logged_in("patient5").get("/").text
        # A clinician can only release notes for their own patients
        assert logged_in("clinician2").post("/api/patients/BC-HP-004/release-note",
                                            json={"note_text": text_}).status_code == 403
    finally:
        sql("DELETE FROM clinical_note WHERE note_text = :t", t=text_)
        sql("DELETE FROM event_log WHERE patient_id = 'BC-HP-004' AND event_type IN ('NoteReleasedToPatient','AIDraftCreated')")


def test_ai_offline_gives_clear_message(monkeypatch):
    monkeypatch.setattr(llm, "OLLAMA_URL", "http://127.0.0.1:9")   # nothing listens here
    res = logged_in("clinician1").post("/api/patients/BC-HP-004/draft-note")
    assert res.status_code == 503
    assert "not reachable" in res.json()["detail"]


# ---------- Care Assistant agent: tools are scoped to the user ------------------------

def fake_agent(messages, tools=None, temperature=0.2):
    """First call: ask for BC-HP-001's care loop. Second call: answer with what the tool returned."""
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    if not tool_msgs:
        return {"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": "get_care_loop", "arguments": {"patient_id": "bc-hp-001"}}}]}
    return {"role": "assistant", "content": "TOOL SAID: " + tool_msgs[-1]["content"]}


def test_assistant_uses_tools_within_the_users_access(monkeypatch):
    monkeypatch.setattr(llm, "ollama_chat", fake_agent)
    own = logged_in("clinician1").post("/api/assistant", json={"question": "Status of BC-HP-001?"}).json()
    assert own["tools_used"][0]["tool"] == "get_care_loop"
    result = json.loads(own["answer"].removeprefix("TOOL SAID: "))
    assert result["patient_id"] == "BC-HP-001" and "sla" in result

    other = logged_in("clinician2").post("/api/assistant", json={"question": "Status of BC-HP-001?"}).json()
    assert "access denied" in other["answer"]
    assert "Sarah" not in other["answer"]


def test_assistant_is_staff_only():
    assert logged_in("patient1").post("/api/assistant", json={"question": "hello"}).status_code == 403
    assert logged_in("admin1").get("/assistant").status_code == 403
