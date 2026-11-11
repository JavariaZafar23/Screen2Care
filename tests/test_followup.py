# Imaging follow-up loop (R1-R4), separate from the BI-RADS 4/5 care loop.
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.auth import create_user
from app.care_loop import check_sla
from app.database import engine
from app.rules_runner import run_engine
from tests.test_sla import logged_in

PID = "TEST-FU-01"
LOGIN = "test_fu_patient"


def sql(q, **p):
    with engine.begin() as conn:
        return conn.execute(text(q), p)


def cleanup():
    loops = "(SELECT loop_id FROM followup_loop WHERE patient_id = :p)"
    episodes = "(SELECT episode_id FROM care_episode WHERE patient_id = :p)"
    for q in ["DELETE FROM event_log WHERE patient_id = :p",
              "DELETE FROM followup_alert WHERE patient_id = :p",
              f"DELETE FROM followup_task WHERE loop_id IN {loops}",
              f"DELETE FROM followup_contact WHERE loop_id IN {loops}",
              f"DELETE FROM followup_appointment WHERE loop_id IN {loops}",
              "DELETE FROM followup_loop WHERE patient_id = :p",
              "DELETE FROM sla_alert WHERE patient_id = :p",
              "DELETE FROM team_notification WHERE patient_id = :p",
              f"DELETE FROM sla_task WHERE episode_id IN {episodes}",
              f"DELETE FROM patient_contact WHERE episode_id IN {episodes}",
              "DELETE FROM care_episode WHERE patient_id = :p",
              "DELETE FROM event_log WHERE patient_id = :p",
              "DELETE FROM lane_assignment WHERE patient_id = :p",
              "DELETE FROM imaging_result WHERE patient_id = :p",
              "DELETE FROM encounter WHERE patient_id = :p",
              "DELETE FROM care_team_assignment WHERE patient_id = :p",
              "DELETE FROM patient_risk_profile WHERE patient_id = :p",
              "DELETE FROM app_user WHERE patient_id = :p",
              "DELETE FROM patient WHERE patient_id = :p"]:
        sql(q, p=PID)


@pytest.fixture
def patient():
    """A 50-year-old assigned to clinician1. Returns a function that adds a mammogram result."""
    cleanup()
    sql("INSERT INTO patient (patient_id, full_name, date_of_birth, created_by) VALUES (:p, 'Test Followup', '1976-01-01', 1)", p=PID)
    sql("INSERT INTO patient_risk_profile (patient_id, genetic_status) VALUES (:p, 'Negative')", p=PID)
    sql("INSERT INTO care_team_assignment (patient_id, staff_id) VALUES (:p, 3)", p=PID)
    create_user(LOGIN, "Dev@12345", "Patient", None, PID)

    def result(birads, density="B"):
        eid = sql("""INSERT INTO encounter (patient_id, encounter_date, encounter_type, entered_by)
                     VALUES (:p, now(), 'Screening', 3) RETURNING encounter_id""", p=PID).scalar()
        sql("""INSERT INTO imaging_result (encounter_id, patient_id, modality, laterality, birads_category, density,
                                           result_released_at) VALUES (:e, :p, 'Mammogram', 'B', :b, :d, now())""",
            e=eid, p=PID, b=birads, d=density)
        return run_engine(PID)
    yield result
    cleanup()


def loops():
    return sql("SELECT * FROM followup_loop WHERE patient_id = :p ORDER BY loop_id", p=PID).mappings().all()


def task(step):
    return sql("""SELECT t.* FROM followup_task t JOIN followup_loop l USING (loop_id)
                  WHERE l.patient_id = :p AND l.status = 'Open' AND t.step = :s""", p=PID, s=step).mappings().first()


def walk_to_report(fu_patient_login=True):
    """R1 confirm (patient), R2 book (scheduling), R3 done (radiology)."""
    if fu_patient_login:
        assert logged_in(LOGIN).post("/api/me/followup-confirm").status_code == 200
    sched = logged_in("scheduling1")
    assert sched.post(f"/api/patients/{PID}/followup/book",
                      json={"modality": "Mammogram", "scheduled_for": "2026-12-01T10:00"}).status_code == 200
    assert logged_in("radiology1").post(f"/api/patients/{PID}/followup/performed").status_code == 200


def test_birads_0_opens_a_followup_not_the_care_loop(patient):
    assert patient(0)["lane"] == "RecallBirads0"
    [loop] = loops()
    assert loop["status"] == "Open" and loop["due_date"] == date.today() + timedelta(days=30)
    assert task(1) is not None                                     # reminder sent, R1 running
    assert sql("SELECT COUNT(*) FROM care_episode WHERE patient_id = :p", p=PID).scalar() == 0
    nav = logged_in("navigator1")
    assert nav.get(f"/followup/{PID}").status_code == 200
    assert "Imaging follow-up" in nav.get("/").text
    assert "Yes, I will come for this scan" in logged_in(LOGIN).get("/").text


def test_annual_screening_waits_until_the_reminder_date(patient):
    assert patient(1)["lane"] == "RoutineScreening"
    [loop] = loops()
    assert loop["status"] == "Scheduled" and loop["due_date"] == date.today() + timedelta(days=365)
    assert task(1) is None
    sql("UPDATE followup_loop SET due_date = current_date + 20 WHERE patient_id = :p", p=PID)
    check_sla()                                                    # within 30 days: the reminder goes out
    assert loops()[0]["status"] == "Open" and task(1) is not None


def test_full_followup_then_next_one_is_scheduled(patient):
    patient(0)
    assert logged_in("scheduling1").get(f"/followup/{PID}").status_code == 403   # nothing to book yet
    walk_to_report()
    assert task(2)["status"] == "Met" and task(3)["status"] == "Met" and task(4)["status"] == "Running"
    doctor = logged_in("clinician1")
    res = doctor.post(f"/api/patients/{PID}/followup/report", json={"birads": 2, "density": "B"})
    assert res.status_code == 200 and res.json()["care_loop_opened"] is False
    first, second = loops()
    assert first["status"] == "Closed-Completed" and first["result_imaging_id"] is not None
    assert second["status"] == "Scheduled" and second["lane"] == "RoutineScreening"
    assert second["source_imaging_id"] == first["result_imaging_id"]


def test_birads_4_on_followup_moves_the_patient_to_the_care_loop(patient):
    patient(3)                                                     # BI-RADS 3: six-month scan
    sql("UPDATE followup_loop SET due_date = current_date + 5 WHERE patient_id = :p", p=PID)
    check_sla()
    walk_to_report()
    res = logged_in("clinician1").post(f"/api/patients/{PID}/followup/report", json={"birads": 4})
    assert res.json()["care_loop_opened"] is True and res.json()["lane"] == "UrgentWorkup"
    assert [l["status"] for l in loops()] == ["Closed-Escalated"]  # no new follow-up while the care loop runs
    assert sql("SELECT status FROM care_episode WHERE patient_id = :p", p=PID).scalar() == "Open"


def test_overdue_reminder_alerts_the_navigator_and_lost_to_follow_up(patient):
    patient(0)
    sql("""UPDATE followup_task t SET due_at = now() - interval '1 hour' FROM followup_loop l
           WHERE l.loop_id = t.loop_id AND l.patient_id = :p AND t.step = 1""", p=PID)
    check_sla()
    assert sql("SELECT routed_to_role FROM followup_alert WHERE patient_id = :p AND resolved_at IS NULL",
               p=PID).scalar() == "Navigator"
    nav = logged_in("navigator1")
    assert "overdue" in nav.get("/").text

    close = {"reason": "LostToFollowUp", "note": "Phoned twice, number switched off."}
    res = nav.post(f"/api/patients/{PID}/followup/close", json=close)   # no minimum number of attempts
    assert res.status_code == 200
    assert loops()[0]["status"] == "Closed-LostToFollowUp"
    assert sql("SELECT COUNT(*) FROM followup_alert WHERE patient_id = :p AND resolved_at IS NULL", p=PID).scalar() == 0
    page = nav.get(res.json()["after"]).text
    assert "is no longer in the imaging follow-up" in page
    assert nav.get(f"/followup/{PID}").status_code == 403       # off the navigator's list
    run_engine(PID)                                                # the same result does not start a new loop
    assert len(loops()) == 1

    # The CMO sees it and restarts it when the patient comes back
    cmo = logged_in("cmo1")
    assert "Phoned twice, number switched off." in cmo.get("/").text
    assert nav.post(f"/api/patients/{PID}/followup/reopen", json={"note": "Patient is back now."}).status_code == 403
    assert cmo.post(f"/api/patients/{PID}/followup/reopen",
                    json={"note": "Patient walked in and wants the scan."}).status_code == 200
    first, second = loops()
    assert second["status"] == "Open" and second["reopened_from"] == first["loop_id"]
    assert nav.get(f"/followup/{PID}").status_code == 200       # back on the navigator's list


def test_who_does_which_step(patient):
    patient(0)
    assert logged_in("navigator1").post(f"/api/patients/{PID}/followup/performed").status_code in (403, 409)
    assert logged_in("pathology1").get(f"/followup/{PID}").status_code == 403
    assert logged_in("admin1").get(f"/followup/{PID}").status_code == 403
    assert logged_in("navigator1").post(f"/api/patients/{PID}/followup/report", json={"birads": 1}).status_code == 403
