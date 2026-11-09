# Data for the patient's own page. Patients only see what a clinician has released:
# the plan (status) is shown once a note on that visit has been released to them.
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import text

from app.database import engine

PATIENT_STATUS = {
    "RoutineRescreen": ("ok", "Routine screening",
                        "No further tests are needed now. Your next screening is due in 1 to 2 years."),
    "ShortTermFollowUp": ("warn", "Follow-up check",
                          "Your care team would like a repeat check{months}."),
    "UrgentBiopsyReferral": ("crit", "Further tests needed",
                             "Your care team is arranging a biopsy to check an area seen on your scan."),
    "HighRiskSurveillance": ("warn", "Enhanced screening",
                             "You are on a high-risk screening plan with yearly imaging."),
}
REVIEWING = ("muted", "Results being reviewed",
             "Your care team is reviewing your results. They will share the outcome with you here.")

VISIT_TYPES = {
    "Screening": "Screening visit",
    "Diagnostic": "Diagnostic visit",
    "HighRiskSurveillance": "High-risk screening visit",
    "Recall": "Recall visit",
    "FollowUp": "Follow-up visit",
}
BIOPSY_TYPES = {
    "US-Core": "Ultrasound-guided biopsy",
    "Stereotactic": "Stereotactic biopsy",
    "MRI-Guided": "MRI-guided biopsy",
    "Surgical": "Surgical biopsy",
}


# Biopsy result in plain words. Shown only after a doctor has discussed it with the patient.
RESULT_STATUS = {
    "Benign": ("ok", "No cancer found",
               "Your biopsy showed no cancer. {doctor} went through the result with you on {date}."),
    "HighRiskLesion": ("warn", "Closer follow-up needed",
                       "Your biopsy found cell changes that are not cancer but need closer follow-up. "
                       "{doctor} went through the result with you on {date}."),
    "NonDiagnostic": ("warn", "Biopsy may need repeating",
                      "The sample did not give a clear answer, so the biopsy may need to be repeated. "
                      "{doctor} went through the result with you on {date}."),
    "Malignant": ("crit", "Specialist care",
                  "Your biopsy found breast cancer. {doctor} went through the result with you on {date}. "
                  "Your specialist team is planning your treatment."),
}
TREATMENTS = {"Surgery": "surgery", "NeoadjuvantChemo": "chemotherapy before surgery",
              "Chemotherapy": "chemotherapy", "Radiation": "radiotherapy",
              "Endocrine": "hormone therapy", "Other": "treatment"}


def _fmt(ts) -> str:
    return ts.strftime("%d %b %Y")


def _journey(conn, pid) -> Optional[dict]:
    """Where the patient is in the care loop, worded for the patient. None before a biopsy is booked
    (until then the status comes from the clinician's released plan, as before)."""
    ep = conn.execute(text("""
        SELECT episode_id, opened_at FROM care_episode WHERE patient_id = :p
        ORDER BY opened_at DESC, episode_id DESC LIMIT 1
    """), {"p": pid}).mappings().first()
    if ep is None:
        return None
    e = {"e": ep["episode_id"]}
    biopsy = conn.execute(text("""
        SELECT biopsy_id, biopsy_type, status, scheduled_for, performed_at FROM biopsy
        WHERE episode_id = :e AND status IN ('Booked','Performed') ORDER BY ordered_at DESC LIMIT 1
    """), e).mappings().first()
    if biopsy is None:
        return None
    path = conn.execute(text("""
        SELECT r.pathology_id, r.diagnosis, r.reported_at, r.disclosed_at, s.full_name AS told_by
        FROM pathology_result r LEFT JOIN staff s ON s.staff_id = r.disclosed_by
        WHERE r.biopsy_id = :b
    """), {"b": biopsy["biopsy_id"]}).mappings().first()
    appt = None
    if path:
        appt = conn.execute(text("""
            SELECT a.scheduled_for, a.status, a.completed_at, s.full_name AS doctor
            FROM results_appointment a JOIN staff s ON s.staff_id = a.clinician_id
            WHERE a.pathology_id = :r AND a.status <> 'Cancelled' ORDER BY a.booked_at DESC LIMIT 1
        """), {"r": path["pathology_id"]}).mappings().first()
    disclosed = bool(path and path["disclosed_at"])
    referral = treatment = None
    if disclosed and path["diagnosis"] == "Malignant":
        referral = conn.execute(text("""
            SELECT r.status, r.assigned_at, s.full_name AS specialist FROM referral r
            LEFT JOIN staff s ON s.staff_id = r.assigned_to_id
            WHERE r.episode_id = :e ORDER BY r.created_at DESC LIMIT 1
        """), e).mappings().first()
        treatment = conn.execute(text(
            "SELECT treatment_type, started_at FROM treatment WHERE episode_id = :e AND started_at IS NOT NULL"),
            e).mappings().first()

    biopsy_name = BIOPSY_TYPES.get(biopsy["biopsy_type"], "Biopsy")
    # Progress steps shown to the patient (no diagnosis until it has been discussed)
    steps = [
        {"label": "Scan result needed a closer look", "done": True, "when": ep["opened_at"]},
        {"label": f"{biopsy_name} booked", "done": True, "when": biopsy["scheduled_for"]},
        {"label": "Biopsy done", "done": biopsy["status"] == "Performed", "when": biopsy["performed_at"]},
        {"label": "Report ready", "done": bool(path), "when": path["reported_at"] if path else None},
        {"label": "Results discussed with your doctor", "done": disclosed,
         "when": path["disclosed_at"] if disclosed else None},
    ]
    if disclosed and path["diagnosis"] == "Malignant":
        steps.append({"label": "Specialist team assigned"
                      + (f" ({referral['specialist']})" if referral and referral["specialist"] else ""),
                      "done": bool(referral and referral["assigned_at"]),
                      "when": referral["assigned_at"] if referral else None})
        steps.append({"label": "Treatment started", "done": bool(treatment),
                      "when": treatment["started_at"] if treatment else None})
    for i, s in enumerate(steps):
        s["current"] = not s["done"] and all(x["done"] for x in steps[:i])

    # Status card for the current stage
    if treatment:
        status = ("ok", "Treatment started",
                  f"Your {TREATMENTS.get(treatment['treatment_type'], 'treatment')} started on "
                  f"{_fmt(treatment['started_at'])}. Your specialist team will guide you through each step.")
    elif disclosed:
        tone, title, detail = RESULT_STATUS.get(path["diagnosis"], RESULT_STATUS["NonDiagnostic"])
        status = (tone, title, detail.format(doctor=path["told_by"] or "Your doctor",
                                             date=_fmt(path["disclosed_at"])))
    elif path and appt and appt["status"] == "Booked":
        status = ("warn", "Your report is ready",
                  f"{appt['doctor']} will go through your biopsy result with you on "
                  f"{appt['scheduled_for'].strftime('%a %d %b at %H:%M')}.")
    elif path:
        status = ("warn", "Your report is ready",
                  "Your biopsy report has arrived. We will book an appointment with your doctor "
                  "to go through it with you.")
    elif biopsy["status"] == "Performed":
        status = ("warn", "Biopsy done",
                  "Your sample is being examined in the laboratory. Reports usually take up to 5 days.")
    else:
        status = ("warn", "Biopsy booked",
                  f"Your {biopsy_name.lower()} is booked for {biopsy['scheduled_for'].strftime('%a %d %b at %H:%M')}.")

    # Appointments and past events that come from the care loop
    upcoming, past = [], []
    if appt and appt["status"] == "Booked":
        upcoming.append({"when": appt["scheduled_for"], "what": f"Results appointment with {appt['doctor']}",
                         "exact": True})
    if biopsy["performed_at"]:
        past.append({"when": biopsy["performed_at"], "what": biopsy_name})
    if disclosed:
        past.append({"when": path["disclosed_at"], "what": f"Results discussed with {path['told_by'] or 'your doctor'}"})
    if treatment:
        past.append({"when": treatment["started_at"],
                     "what": f"Treatment started: {TREATMENTS.get(treatment['treatment_type'], 'treatment')}"})
    return {"status": dict(zip(("tone", "title", "detail"), status)), "steps": steps,
            "upcoming": upcoming, "past": past}


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def get_portal(pid: str) -> dict:
    with engine.connect() as conn:
        patient = conn.execute(text(
            "SELECT patient_id, full_name, date_of_birth FROM patient WHERE patient_id = :p"), {"p": pid}
        ).mappings().first()
        if patient is None:
            raise HTTPException(status_code=404, detail="Patient not found")

        notes = conn.execute(text("""
            SELECT encounter_date, note_text, released_at FROM vw_patient_released_notes
            WHERE patient_id = :p ORDER BY released_at DESC
        """), {"p": pid}).mappings().all()

        latest = conn.execute(text("""
            SELECT e.encounter_id, e.encounter_date, e.disposition, e.followup_months,
                   EXISTS (SELECT 1 FROM clinical_note n
                           WHERE n.encounter_id = e.encounter_id AND n.is_released_to_patient) AS released
            FROM encounter e WHERE e.patient_id = :p
            ORDER BY e.encounter_date DESC, e.encounter_id DESC LIMIT 1
        """), {"p": pid}).mappings().first()

        biopsies = conn.execute(text("""
            SELECT b.biopsy_type, b.scheduled_for FROM biopsy b
            JOIN care_episode ep ON ep.episode_id = b.episode_id
            WHERE ep.patient_id = :p AND b.status = 'Booked' AND b.scheduled_for >= now()
            ORDER BY b.scheduled_for
        """), {"p": pid}).mappings().all()

        visits = conn.execute(text("""
            SELECT encounter_date, encounter_type FROM encounter
            WHERE patient_id = :p ORDER BY encounter_date DESC
        """), {"p": pid}).mappings().all()

        journey = _journey(conn, pid)

    upcoming = [{"when": b["scheduled_for"], "what": BIOPSY_TYPES.get(b["biopsy_type"], "Biopsy"),
                 "exact": True} for b in biopsies]

    tone, title, detail = REVIEWING
    if latest and latest["released"] and latest["disposition"] in PATIENT_STATUS:
        tone, title, detail = PATIENT_STATUS[latest["disposition"]]
        months = latest["followup_months"]
        detail = detail.format(months=f" in about {months} months" if months else "")
        if latest["disposition"] == "ShortTermFollowUp" and months:
            upcoming.append({"when": _add_months(latest["encounter_date"].date(), months),
                             "what": "Follow-up imaging", "exact": False})

    status = {"tone": tone, "title": title, "detail": detail}
    past = [{"when": v["encounter_date"], "what": VISIT_TYPES.get(v["encounter_type"], v["encounter_type"])}
            for v in visits]
    steps = []
    if journey:                       # once a biopsy is booked, the care loop drives the page
        status, steps = journey["status"], journey["steps"]
        upcoming += journey["upcoming"]
        past += journey["past"]

    def _key(item):
        w = item["when"]
        return w if isinstance(w, datetime) else datetime.combine(w, datetime.min.time(), tzinfo=timezone.utc)

    return {
        "patient": patient,
        "status": status,
        "steps": steps,
        "notes": notes,
        "upcoming": sorted(upcoming, key=_key),
        "visits": sorted(past, key=_key, reverse=True),
    }
