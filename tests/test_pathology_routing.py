# After pathology: a benign result stays with the clinician (and the loop ends with a check
# scan in 6 months); a malignant result goes to the navigator, who refers to an oncologist.
from app.care_loop import referrals_to_make
from tests.test_results import to_report
from tests.test_sla import PID, alert_roles, birads5_patient, logged_in, sql  # noqa: F401  (fixture)


def episode_status():
    return sql("SELECT status FROM care_episode WHERE patient_id = :p ORDER BY episode_id DESC LIMIT 1",
               p=PID).scalar()


def test_benign_goes_to_the_clinician_and_closes_the_loop(birads5_patient):
    to_report(PID, "Benign")
    nav, doctor = logged_in("navigator1"), logged_in("clinician1")
    # Not on the navigator's side
    assert PID not in nav.get("/").text.split("Biopsy results to go through")[-1].split("Patients")[0]
    assert "Results for the patient" not in nav.get(f"/loop/{PID}").text
    assert nav.post(f"/api/patients/{PID}/results-appointment",
                    json={"clinician_staff_id": 3, "scheduled_for": "2030-01-05T10:30"}).status_code == 403
    # On the clinician's side
    assert "Benign" in doctor.get("/").text
    assert doctor.post(f"/api/patients/{PID}/results-appointment",
                       json={"clinician_staff_id": 3, "scheduled_for": "2030-01-05T10:30"}).status_code == 200
    res = doctor.post(f"/api/patients/{PID}/result-disclosed", json={"channel": "InPerson"})
    assert res.status_code == 200 and res.json()["loop_closed"] is True
    assert episode_status() == "Closed-Benign"
    assert sql("SELECT COUNT(*) FROM sla_task t JOIN care_episode e USING (episode_id) WHERE e.patient_id = :p "
               "AND t.completed_at IS NULL AND t.status <> 'Cancelled'", p=PID).scalar() == 0
    loop = sql("SELECT lane, status, due_date - current_date AS days FROM followup_loop WHERE patient_id = :p",
               p=PID).mappings().one()
    assert loop["lane"] == "PostBenignBiopsy" and loop["status"] == "Scheduled" and loop["days"] == 182


def test_malignant_goes_to_the_navigator_with_the_report(birads5_patient):
    to_report(PID, "Malignant")
    sql("""UPDATE pathology_result SET histology = 'Invasive ductal carcinoma', er_status = 'Positive'
           WHERE biopsy_id IN (SELECT biopsy_id FROM biopsy b JOIN care_episode e USING (episode_id)
                               WHERE e.patient_id = :p)""", p=PID)
    nav = logged_in("navigator1")
    dash = nav.get("/").text
    assert "Malignant results: refer to an oncologist" in dash and "Invasive ductal carcinoma" in dash
    page = nav.get(f"/loop/{PID}").text
    assert "Refer to an oncologist" in page and "Pathology report: Malignant" in page and "ER Positive" in page
    assert "Dr. Surgical Oncologist" in page

    # Only oncology-team staff can receive it, and only the navigator sends it
    assert nav.post(f"/api/patients/{PID}/referral/assign", json={"assignee_staff_id": 3}).status_code == 422
    assert logged_in("clinician1").post(f"/api/patients/{PID}/referral/assign",
                                        json={"assignee_staff_id": 8}).status_code == 403
    onc = sql("SELECT staff_id FROM staff WHERE full_name = 'Dr. Medical Oncologist'").scalar()
    assert nav.post(f"/api/patients/{PID}/referral/assign",
                    json={"assignee_staff_id": onc, "specialty": "MedicalOncology",
                          "note": "Prefers morning appointments"}).status_code == 200
    ref = sql("SELECT * FROM referral WHERE patient_id = :p", p=PID).mappings().one()
    assert (ref["status"], ref["specialty"], ref["assigned_to_id"], ref["referred_by"]) == \
        ("Assigned", "MedicalOncology", onc, 5)
    assert PID not in [r["patient_id"] for r in referrals_to_make("TRUE", {})]   # off the "to refer" list
    assert "referred by Patient Navigator" in nav.get(f"/loop/{PID}").text
