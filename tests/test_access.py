import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import engine
from app.main import app
from app.seed_users import DEV_PASSWORD, PATIENT_USERS, SEED_PATIENT_IDS


def logged_in(username: str) -> TestClient:
    client = TestClient(app)
    res = client.post("/api/login", json={"username": username, "password": DEV_PASSWORD})
    assert res.status_code == 200, f"login failed for {username}"
    return client


def listed_ids(username: str) -> set[str]:
    return {p["patient_id"] for p in logged_in(username).get("/api/patients").json()}


def ids_from_db(sql: str, **params) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text(sql), params).scalars())


# ---- Each patient sees only their own record -----------------------------------

@pytest.mark.parametrize("username,pid", PATIENT_USERS)
def test_each_patient_sees_only_own_record(username, pid):
    client = logged_in(username)
    other = next(p for p in SEED_PATIENT_IDS if p != pid)

    assert client.get(f"/api/patients/{pid}").status_code == 200
    assert client.get(f"/api/patients/{other}").status_code == 403
    assert client.get("/api/patients").status_code == 403

    page = client.get("/", headers={"accept": "text/html"})
    assert page.status_code == 200
    assert pid in page.text
    assert other not in page.text


def test_patient_page_shows_own_released_note_only():
    page1 = logged_in("patient1").get("/").text
    page2 = logged_in("patient2").get("/").text
    assert "Further tests needed" in page1
    assert "Our patient navigator will call you" in page1
    assert "Our patient navigator will call you" not in page2
    assert "The navigator will contact you" in page2


def test_patient_without_released_note_sees_reviewing_status():
    page = logged_in("patient3").get("/").text
    assert "Results being reviewed" in page
    assert "No notes have been shared with you yet" in page


# ---- Dashboard / list filtered by role -----------------------------------------

def test_admin_sees_all_patients_without_clinical_fields():
    rows = logged_in("admin1").get("/api/patients").json()
    assert {r["patient_id"] for r in rows} == ids_from_db("SELECT patient_id FROM patient")
    assert all("birads_category" not in r for r in rows)


def test_cmo_sees_all_patients():
    assert listed_ids("cmo1") == ids_from_db("SELECT patient_id FROM patient")


@pytest.mark.parametrize("username,staff_id", [("clinician1", 3), ("clinician2", 11)])
def test_clinician_sees_only_assigned_patients(username, staff_id):
    assigned = ids_from_db("SELECT patient_id FROM care_team_assignment WHERE staff_id = :s", s=staff_id)
    assert assigned, "seed should assign patients to this clinician"
    assert listed_ids(username) == assigned


def test_clinicians_have_separate_lists():
    assert listed_ids("clinician1").isdisjoint(listed_ids("clinician2"))


def test_clinician_blocked_from_unassigned_patient():
    c2 = logged_in("clinician2")
    assert c2.get("/api/patients/BC-HP-001").status_code == 403
    assert c2.get("/plan/BC-HP-001").status_code == 403
    assert c2.post("/api/patients/BC-HP-001/run-engine").status_code == 403
    assert c2.get("/api/patients/BC-DL-006").status_code == 200


def test_radiologist_sees_patients_with_their_imaging():
    assert listed_ids("radiology1") == ids_from_db(
        "SELECT patient_id FROM imaging_result WHERE radiologist_id = 2")


def test_navigator_sees_only_open_care_loops():
    assert listed_ids("navigator1") == ids_from_db(
        "SELECT patient_id FROM care_episode WHERE status IN ('Open','Paused')")


def test_pathologist_sees_only_patients_with_a_biopsy():
    assert listed_ids("pathology1") == ids_from_db(
        "SELECT e.patient_id FROM care_episode e JOIN biopsy b ON b.episode_id = e.episode_id")


def test_dashboard_html_is_filtered():
    page = logged_in("clinician2").get("/").text
    assert "BC-DL-006" in page and "BC-HP-001" not in page
    assert "Patients assigned to you" in page
