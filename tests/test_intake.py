# Intake form v2 (screen2care proforma): validation, risk tier, plan dates and the save API.
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app.database import engine
from app.main import app
from app.plan_dates import plan_schedule
from app.risk_engine import calculate_risk, plan_disposition
from app.schemas import IntakeForm, PlanOption
from app.seed_users import DEV_PASSWORD

PID = "BC-TS-901"
NID = "NID-TEST-INTAKE-901"
ROUTINE = PlanOption.ROUTINE_RESCREEN.value
URGENT = PlanOption.URGENT_BIOPSY.value
NORMAL = {"inspection": "Normal", "mass": "Normal", "axillary_nodes": "Normal"}


def base(**over) -> dict:
    form = {"full_name": "Test Intake", "date_of_birth": "1976-04-02", "patient_id": PID, "phone": "03001234567",
            "national_id": NID, "evaluation_date": "2026-11-08", "presentation": False,
            "marital_status": "Married", "parity": 2, "breastfeeding_history": "Yes",
            "breastfeeding_duration_years": 1.5, "height_cm": 160, "weight_kg": 64,
            "chest_radiation": "No", "genetic_status": "Never tested", "family_history_none": True,
            "cbe_right": dict(NORMAL), "cbe_left": dict(NORMAL),
            "imaging": [{"date": "2026-11-08", "modality": "Mammogram", "density": "B", "birads": 1}],
            "plan": [ROUTINE], "examiner_name": "Dr. Test", "signed_date": "2026-11-08"}
    form.update(over)
    return form


# ---------- Validation (mirrors the proforma's rules) ---------------------------------

def test_valid_form_and_derived_values():
    f = IntakeForm(**base())
    assert f.age == 50 and f.bmi == 25.0


@pytest.mark.parametrize("over, message", [
    ({"presentation": None}, "symptomatic or asymptomatic"),
    ({"chest_radiation": "Yes"}, "age or years for chest radiation"),
    ({"chest_radiation": "Yes", "chest_radiation_age": 60}, "cannot be greater"),
    ({"genetic_status": "Positive"}, "gene"),
    ({"genetic_status": "Positive", "genetic_gene": "Other"}, "other gene"),
    ({"imaging": []}, "Record imaging"),
    ({"medical_history_present": True}, "medical history details"),
    ({"date_of_birth": "1946-01-01"}, "Geriatric assessment is required"),
    ({"plan": []}, "at least 1"),
    ({"cbe_right": {"inspection": "Abnormal", "mass": "Normal", "axillary_nodes": "Normal"}}, "abnormal inspection"),
    ({"cbe_left": {"inspection": "Normal", "mass": "Abnormal", "axillary_nodes": "Normal"}}, "size of the mass"),
    ({"patient_id": "XX-1"}, "pattern"),
    ({"surprise": 1}, "Extra inputs"),
])
def test_invalid_forms_are_refused(over, message):
    with pytest.raises(ValidationError, match=message):
        IntakeForm(**base(**over))


def test_unused_answers_are_cleared():
    f = IntakeForm(**base(
        marital_status="Unmarried", lump_side="Left",                       # asymptomatic: no lump
        hrt_use="Yes", hrt_duration_years=3,                               # age 50 keeps HRT
        imaging=[{"date": "2026-11-01", "modality": "MRI", "density": "C", "birads": 2}]))
    assert f.parity is None and f.breastfeeding_history == "Unknown" and f.breastfeeding_duration_years is None
    assert f.lump_side is None
    assert f.hrt_use == "Yes" and f.hrt_duration_years == 3
    assert f.imaging[0].density is None                                     # no density for MRI
    young = IntakeForm(**base(date_of_birth="1996-01-01", hrt_use="Yes", menopausal_status="Postmenopausal"))
    assert young.hrt_use is None and young.menopausal_status is None


def test_warnings_do_not_block_saving():
    f = IntakeForm(**base(imaging=[{"date": "2026-11-08", "modality": "DBT", "density": "D", "birads": 4}]))
    w = f.warnings()
    assert any("urgent biopsy" in x for x in w) and any("Dense breast" in x for x in w)


# ---------- Risk tier, disposition and plan dates -------------------------------------

def submitted(**over):
    f = IntakeForm(**base(**over))
    return {**f.model_dump(mode="json"), "age": f.age}


def test_risk_tiers():
    high = calculate_risk(submitted(genetic_status="Positive", genetic_gene="BRCA1"))
    assert high.tier == "High" and "BRCA1" in high.reasons[0]
    fam = calculate_risk(submitted(family_history_none=False, family_history=[
        {"relation": "Mother", "relation_degree": 1, "cancer_type": "Breast", "age_at_diagnosis": 44}]))
    assert fam.tier == "High"
    medium = calculate_risk(submitted(imaging=[{"date": "2026-11-08", "modality": "Mammogram", "density": "C", "birads": 1}]))
    assert medium.tier == "Medium"
    low = calculate_risk(submitted())
    assert low.tier == "Low" and low.label == "Low (lifetime risk not assessed)"
    assert calculate_risk(submitted(imaging_not_done=True, imaging=[])).tier == "Incomplete"


def test_plan_disposition_mapping():
    assert plan_disposition([URGENT, ROUTINE]) == "UrgentBiopsyReferral"
    assert plan_disposition([PlanOption.ANNUAL_DBT_MRI_6M]) == "HighRiskSurveillance"
    assert plan_disposition([ROUTINE]) == "RoutineRescreen"
    assert plan_disposition([PlanOption.STANDARD_WELLNESS]) is None


def test_plan_schedule_dates():
    sched = plan_schedule([PlanOption.SHORT_TERM_FOLLOW_UP, PlanOption.ANNUAL_DBT_MRI_6M,
                           PlanOption.GENETICS_REFERRAL], date(2026, 11, 8), 6)
    assert sched[0]["due_date"] == "2027-05-08"
    assert [s["due_date"] for s in sched[1:3]] == ["2026-11-08", "2027-05-08"]
    assert sched[3] == {"option": PlanOption.GENETICS_REFERRAL.value, "due_date": None, "recurrence": None}


# ---------- Save API ---------------------------------------------------------------

def sql(q, **p):
    with engine.begin() as conn:
        return conn.execute(text(q), p)


def cleanup():
    ep = "(SELECT episode_id FROM care_episode WHERE patient_id = :p)"
    enc = "(SELECT encounter_id FROM encounter WHERE patient_id = :p)"
    for q in ["DELETE FROM event_log WHERE patient_id = :p",
              "DELETE FROM team_notification WHERE patient_id = :p",
              "DELETE FROM sla_alert WHERE patient_id = :p",
              f"DELETE FROM sla_task WHERE episode_id IN {ep}",
              f"DELETE FROM patient_contact WHERE episode_id IN {ep}",
              "DELETE FROM care_episode WHERE patient_id = :p",
              "DELETE FROM lane_assignment WHERE patient_id = :p",
              "DELETE FROM event_log WHERE patient_id = :p",
              "DELETE FROM risk_assessment WHERE patient_id = :p",
              "DELETE FROM encounter_snapshot WHERE patient_id = :p",
              "DELETE FROM plan_items WHERE patient_id = :p",
              f"DELETE FROM geriatric_assessment WHERE encounter_id IN {enc}",
              f"DELETE FROM clinical_breast_exam WHERE encounter_id IN {enc}",
              "DELETE FROM imaging_result WHERE patient_id = :p",
              "DELETE FROM encounter WHERE patient_id = :p",
              "DELETE FROM family_history WHERE patient_id = :p",
              "DELETE FROM prior_breast_procedure WHERE patient_id = :p",
              "DELETE FROM patient_risk_profile WHERE patient_id = :p",
              "DELETE FROM patient WHERE patient_id = :p"]:
        sql(q, p=PID)


@pytest.fixture
def admin():
    cleanup()
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "admin1", "password": DEV_PASSWORD}).status_code == 200
    yield c
    cleanup()


def test_save_new_patient_then_returning_visit(admin):
    form = base(
        family_history_none=False,
        family_history=[{"relation": "Sister", "relation_degree": 1, "cancer_type": "Ovarian",
                         "age_at_diagnosis": 52, "known_genetic_mutations": "BRCA2"}],
        breast_history=[{"type": "Biopsy", "details": "Benign fibroadenoma 2019"}],
        cbe_left={"inspection": "Not answered", "mass": "Normal", "axillary_nodes": "Not answered"},
        plan=[ROUTINE, PlanOption.GENETICS_REFERRAL.value], idempotency_key="test-intake-key-1")
    res = admin.post("/api/intake", json=form)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["patient_id"] == PID and body["returning_patient"] is False
    assert body["lane"] == "RoutineScreening"
    assert body["risk_assessment"]["tier"] == "High"                  # relative with a BRCA2 mutation
    assert [p["option"] for p in body["doctor_plan"]] == [ROUTINE, PlanOption.GENETICS_REFERRAL.value]

    # Stored as the proforma said, with "Not answered" kept as unknown (NULL), not normal
    left = sql("""SELECT inspection_normal, axillary_node_palpable FROM clinical_breast_exam c
                  JOIN encounter e USING (encounter_id) WHERE e.patient_id = :p AND c.laterality = 'L'""",
               p=PID).mappings().one()
    assert left["inspection_normal"] is None and left["axillary_node_palpable"] is None
    assert sql("SELECT known_genetic_mutations FROM family_history WHERE patient_id = :p", p=PID).scalar() == "BRCA2"
    assert sql("SELECT marital_status FROM patient_risk_profile WHERE patient_id = :p", p=PID).scalar() == "Married"
    assert sql("SELECT due_date FROM plan_items WHERE patient_id = :p", p=PID).scalar() == date(2027, 11, 8)

    # A double submit returns the same visit and saves nothing new
    again = admin.post("/api/intake", json=form).json()
    assert again["duplicate"] is True and again["encounter_id"] == body["encounter_id"]
    assert sql("SELECT COUNT(*) FROM encounter WHERE patient_id = :p", p=PID).scalar() == 1

    # Lookup prefills demographics and lists earlier intakes
    found = admin.get(f"/api/intake/patients/{PID.lower()}").json()
    assert found["patient"]["phone"] == "03001234567" and len(found["intake_history"]) == 1

    # Second visit for the same Patient ID: returning patient, BI-RADS 5 opens the care loop
    visit2 = base(evaluation_date="2026-11-09", signed_date="2026-11-09", plan=[URGENT],
                  imaging=[{"date": "2026-11-09", "modality": "DBT", "density": "C", "birads": 5}])
    res2 = admin.post("/api/intake", json=visit2).json()
    assert res2["returning_patient"] is True and res2["lane"] == "UrgentWorkup"
    assert sql("SELECT status FROM care_episode WHERE patient_id = :p", p=PID).scalar() == "Open"
    assert sql("""SELECT disposition FROM encounter WHERE patient_id = :p
                  ORDER BY encounter_date DESC LIMIT 1""", p=PID).scalar() == "UrgentBiopsyReferral"
    assert len(admin.get(f"/api/intake/patients/{PID}").json()["intake_history"]) == 2
    # Family history is not duplicated by the second visit
    assert sql("SELECT COUNT(*) FROM family_history WHERE patient_id = :p", p=PID).scalar() == 1


def test_national_id_of_another_patient_is_refused(admin):
    res = admin.post("/api/intake", json=base(national_id=sql(
        "SELECT national_id FROM patient WHERE national_id IS NOT NULL AND patient_id <> :p LIMIT 1",
        p=PID).scalar()))
    assert res.status_code == 409


def test_lookup_is_admin_only_and_unknown_id_is_404(admin):
    assert admin.get("/api/intake/patients/BC-ZZ-999").status_code == 404
    clinician = TestClient(app)
    clinician.post("/api/login", json={"username": "clinician1", "password": DEV_PASSWORD})
    assert clinician.get("/api/intake/patients/BC-HP-001").status_code == 403
