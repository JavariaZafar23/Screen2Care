from fastapi.testclient import TestClient

from app.main import app
from app.seed_users import DEV_PASSWORD


def logged_in(username: str) -> TestClient:
    client = TestClient(app)
    res = client.post("/api/login", json={"username": username, "password": DEV_PASSWORD})
    assert res.status_code == 200, f"login failed for {username}"
    return client


def test_no_login_gets_401():
    assert TestClient(app).get("/api/patients").status_code == 401


def test_wrong_password_is_rejected():
    res = TestClient(app).post("/api/login", json={"username": "admin1", "password": "wrong"})
    assert res.status_code == 401


def test_patient_cannot_read_another_patient():
    assert logged_in("patient1").get("/api/patients/BC-HP-002").status_code == 403


def test_patient_can_read_own_record():
    assert logged_in("patient1").get("/api/patients/BC-HP-001").status_code == 200


def test_admin_cannot_post_clinical_plan():
    assert logged_in("admin1").post("/api/patients/BC-HP-001/plan", json={}).status_code == 403


def test_admin_sees_no_clinical_data():
    body = logged_in("admin1").get("/api/patients/BC-HP-001").json()
    assert "patient" in body
    assert "encounters" not in body and "imaging" not in body


def test_patient_sees_only_released_notes():
    body = logged_in("patient1").get("/api/patients/BC-HP-001").json()
    assert set(body.keys()) == {"patient", "released_notes"}


def test_wrong_role_cannot_open_intake():
    assert logged_in("radiology1").post("/api/intake", json={}).status_code == 403
