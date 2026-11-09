# Save the breast screening proforma (intake form v2) in one transaction, then
# calculate the first-visit risk tier from the saved snapshot.
import json
from datetime import datetime, time, timezone

from fastapi import HTTPException
from sqlalchemy import text

from app.database import engine
from app.plan_dates import due_dates, plan_schedule
from app.risk_engine import calculate_risk, plan_disposition
from app.schemas import IntakeForm

ADMIN_STAFF_ID = 1          # intake is entered at the front desk
RADIOLOGIST_STAFF_ID = 2    # reporting radiologist for scans entered at intake

PROCEDURE_TYPES = {"Biopsy": "Biopsy", "Lumpectomy": "Lumpectomy",
                   "Cyst Aspiration": "CystAspiration", "Implants": "Implants"}
EXAM_FLAG = {"Normal": False, "Abnormal": True, "Not answered": None}
YES_NO = {"Yes": True, "No": False}


def _chief_complaint(f: IntakeForm) -> str:
    if not f.presentation:
        return "Asymptomatic (Routine Screening)"
    parts = []
    if f.lump_side:
        parts.append(f"lump / mass: {f.lump_side.lower()}" + (f", {f.lump_duration}" if f.lump_duration else ""))
    if f.nipple_discharge_side:
        parts.append(f"nipple discharge: {f.nipple_discharge_side.lower()}"
                     + (f", {f.nipple_discharge_color.lower()}" if f.nipple_discharge_color else ""))
    if f.pain_type:
        parts.append(f"pain: {f.pain_type.lower()}" + (f", severity {f.pain_severity}/10" if f.pain_severity else ""))
    return ("Symptomatic: " + "; ".join(parts) if parts else "Symptomatic")[:200]


def _released_at(scan_date):
    """When the result became available. Today's scans use the current time."""
    if scan_date == datetime.now(timezone.utc).date():
        return datetime.now(timezone.utc)
    return datetime.combine(scan_date, time(12, 0), tzinfo=timezone.utc)


def _find_patient(conn, f: IntakeForm) -> tuple[str | None, bool]:
    """Existing patient by Patient ID, else by national ID. Returns (patient_id, exists)."""
    by_nid = None
    if f.national_id:
        by_nid = conn.execute(text("SELECT patient_id FROM patient WHERE national_id = :n"),
                              {"n": f.national_id}).scalar()
    if f.patient_id:
        exists = conn.execute(text("SELECT 1 FROM patient WHERE patient_id = :p"), {"p": f.patient_id}).first()
        if by_nid and by_nid != f.patient_id:
            raise HTTPException(status_code=409,
                                detail=f"National ID {f.national_id} already belongs to patient {by_nid}.")
        return f.patient_id, exists is not None
    return by_nid, by_nid is not None


def _previous_result(conn, encounter_id: int) -> dict:
    snap = conn.execute(text("SELECT payload FROM encounter_snapshot WHERE encounter_id = :e"),
                        {"e": encounter_id}).scalar() or {}
    risk = conn.execute(text("SELECT tier, reasons, rules_version FROM risk_assessment WHERE encounter_id = :e"),
                        {"e": encounter_id}).mappings().first()
    return {"risk_assessment": dict(risk) if risk else None, "doctor_plan": snap.get("plan_schedule", [])}


def save_intake(f: IntakeForm) -> dict:
    with engine.begin() as conn:
        dup = conn.execute(text("SELECT patient_id, encounter_id FROM encounter WHERE idempotency_key = :k"),
                           {"k": f.idempotency_key}).mappings().first()
        if dup:   # the same submission arrived twice: answer as before, save nothing new
            return {"patient_id": dup["patient_id"], "encounter_id": dup["encounter_id"], "duplicate": True,
                    "returning_patient": True, "age": f.age, "bmi": f.bmi, "warnings": f.warnings(),
                    **_previous_result(conn, dup["encounter_id"])}

        patient_id, returning = _find_patient(conn, f)
        if returning:
            conn.execute(text("""
                UPDATE patient SET full_name = :n, date_of_birth = :d, phone = :ph,
                       national_id = COALESCE(:nid, national_id)
                WHERE patient_id = :p
            """), {"p": patient_id, "n": f.full_name, "d": f.date_of_birth, "ph": f.phone, "nid": f.national_id})
        else:
            if patient_id is None:
                patient_id = conn.execute(
                    text("SELECT 'BC-' || to_char(nextval('patient_number_seq'), 'FM00000')")).scalar()
            conn.execute(text("""
                INSERT INTO patient (patient_id, full_name, date_of_birth, national_id, phone, created_by)
                VALUES (:p, :n, :d, :nid, :ph, :by)
            """), {"p": patient_id, "n": f.full_name, "d": f.date_of_birth, "nid": f.national_id,
                   "ph": f.phone, "by": ADMIN_STAFF_ID})

        genetic_status = {"Never tested": "Unknown"}.get(f.genetic_status, f.genetic_status)
        lesion = f.high_risk_biopsy if f.high_risk_biopsy in ("ADH", "ALH", "LCIS") else None
        conn.execute(text("""
            INSERT INTO patient_risk_profile
              (patient_id, marital_status, menarche_age, menopausal_status, menopause_age, parity,
               age_first_birth, ocp_use, hrt_use, hrt_duration_years, breastfeeding_history_status,
               breastfeeding_duration_years, height_cm, weight_kg, genetic_status, genetic_mutation,
               chest_radiation_status, chest_radiation_age, chest_radiation_years, high_risk_lesion,
               risk_data_status)
            VALUES (:p, :ms, :men, :mst, :mage, :par, :afb, :ocp, :hrt, :hrtd, :bf, :bfd, :h, :w,
                    :gs, :gene, :rad, :rada, :rady, :les, :rds)
            ON CONFLICT (patient_id) DO UPDATE SET
              marital_status = EXCLUDED.marital_status, menarche_age = EXCLUDED.menarche_age,
              menopausal_status = EXCLUDED.menopausal_status, menopause_age = EXCLUDED.menopause_age,
              parity = EXCLUDED.parity, age_first_birth = EXCLUDED.age_first_birth,
              ocp_use = EXCLUDED.ocp_use, hrt_use = EXCLUDED.hrt_use,
              hrt_duration_years = EXCLUDED.hrt_duration_years,
              breastfeeding_history_status = EXCLUDED.breastfeeding_history_status,
              breastfeeding_duration_years = EXCLUDED.breastfeeding_duration_years,
              height_cm = EXCLUDED.height_cm, weight_kg = EXCLUDED.weight_kg,
              genetic_status = EXCLUDED.genetic_status, genetic_mutation = EXCLUDED.genetic_mutation,
              chest_radiation_status = EXCLUDED.chest_radiation_status,
              chest_radiation_age = EXCLUDED.chest_radiation_age,
              chest_radiation_years = EXCLUDED.chest_radiation_years,
              high_risk_lesion = COALESCE(EXCLUDED.high_risk_lesion, patient_risk_profile.high_risk_lesion),
              risk_data_status = EXCLUDED.risk_data_status, updated_at = now()
        """), {"p": patient_id, "ms": f.marital_status, "men": f.menarche_age, "mst": f.menopausal_status,
               "mage": f.menopause_age, "par": f.parity, "afb": f.age_first_birth, "ocp": f.ocp_use,
               "hrt": YES_NO.get(f.hrt_use), "hrtd": f.hrt_duration_years, "bf": f.breastfeeding_history,
               "bfd": f.breastfeeding_duration_years, "h": f.height_cm, "w": f.weight_kg,
               "gs": genetic_status, "gene": f.gene, "rad": f.chest_radiation, "rada": f.chest_radiation_age,
               "rady": f.chest_radiation_years, "les": lesion,
               "rds": "Pending" if f.genetic_status == "Never tested" else "Complete"})

        # Family history and prior procedures: add only what is not already on file
        for r in f.family_history:
            degree = r.relation_degree or (1 if r.relation.strip().lower() in
                                           {"mother", "father", "sister", "brother", "daughter", "son"} else 2)
            conn.execute(text("""
                INSERT INTO family_history
                  (patient_id, relation, relation_degree, cancer_type, age_at_diagnosis, known_genetic_mutations)
                SELECT CAST(:p AS VARCHAR), CAST(:rel AS VARCHAR), CAST(:deg AS SMALLINT), CAST(:c AS VARCHAR),
                       CAST(:age AS SMALLINT), CAST(:mut AS VARCHAR)
                WHERE NOT EXISTS (SELECT 1 FROM family_history WHERE patient_id = CAST(:p AS VARCHAR)
                                    AND lower(relation) = lower(CAST(:rel AS VARCHAR))
                                    AND cancer_type = CAST(:c AS VARCHAR))
            """), {"p": patient_id, "rel": r.relation.strip(), "deg": degree, "c": r.cancer_type,
                   "age": r.age_at_diagnosis, "mut": r.known_genetic_mutations})

        for entry in f.breast_history:
            if entry.type == "None":
                continue
            conn.execute(text("""
                INSERT INTO prior_breast_procedure (patient_id, procedure_type, result_summary)
                SELECT CAST(:p AS VARCHAR), CAST(:t AS VARCHAR), CAST(:d AS VARCHAR)
                WHERE NOT EXISTS (SELECT 1 FROM prior_breast_procedure WHERE patient_id = CAST(:p AS VARCHAR)
                                    AND procedure_type = CAST(:t AS VARCHAR)
                                    AND result_summary IS NOT DISTINCT FROM CAST(:d AS VARCHAR))
            """), {"p": patient_id, "t": PROCEDURE_TYPES[entry.type], "d": entry.details})

        encounter_id = conn.execute(text("""
            INSERT INTO encounter
              (patient_id, encounter_date, encounter_type, is_symptomatic, chief_complaint, entered_by,
               lump_side, lump_duration, nipple_discharge_side, nipple_discharge_color,
               pain_type, pain_severity, disposition, followup_months, examiner_name, signed_date,
               plan_options, clinician_notes, medical_history_details, idempotency_key)
            VALUES (:p, :ed, :et, :sym, :cc, :by, :lump, :ldur, :nds, :ndc, :pt, :ps, :disp, :fum,
                    :exam, :signed, :plan, :notes, :mh, :key)
            RETURNING encounter_id
        """), {"p": patient_id, "ed": f.evaluation_date, "et": "Diagnostic" if f.presentation else "Screening",
               "sym": f.presentation, "cc": _chief_complaint(f), "by": ADMIN_STAFF_ID,
               "lump": f.lump_side, "ldur": f.lump_duration, "nds": f.nipple_discharge_side,
               "ndc": f.nipple_discharge_color, "pt": f.pain_type, "ps": f.pain_severity,
               "disp": plan_disposition(f.plan), "fum": f.followup_months, "exam": f.examiner_name,
               "signed": f.signed_date, "plan": [p.value for p in f.plan], "notes": f.clinician_notes,
               "mh": f.medical_history_details, "key": f.idempotency_key}).scalar()

        if f.age >= 75:
            conn.execute(text("""
                INSERT INTO geriatric_assessment
                  (encounter_id, end_stage_organ_failure_or_dementia, independent_in_adl, life_expectancy_over_10y)
                VALUES (:e, :es, :adl, :le)
            """), {"e": encounter_id, "es": YES_NO[f.geriatric_end_stage_failure],
                   "adl": YES_NO[f.geriatric_adl_independent], "le": f.geriatric_life_expectancy_over_10y})

        for side, exam in (("R", f.cbe_right), ("L", f.cbe_left)):
            insp = EXAM_FLAG[exam.inspection]
            conn.execute(text("""
                INSERT INTO clinical_breast_exam
                  (encounter_id, laterality, inspection_normal, inspection_finding, nipple_discharge_color,
                   skin_change, mass_present, mass_size_mm, mass_mobility, mass_consistency, axillary_node_palpable)
                VALUES (:e, :side, :inorm, :ifind, :ndc, :skin, :mass, :size, :mob, :cons, :node)
            """), {"e": encounter_id, "side": side, "inorm": None if insp is None else not insp,
                   "ifind": exam.inspection_finding, "ndc": exam.nipple_discharge_color, "skin": exam.skin_change,
                   "mass": EXAM_FLAG[exam.mass], "size": exam.mass_size_mm, "mob": exam.mass_mobility,
                   "cons": exam.mass_consistency, "node": EXAM_FLAG[exam.axillary_nodes]})

        for scan in f.imaging:
            conn.execute(text("""
                INSERT INTO imaging_result
                  (encounter_id, patient_id, modality, laterality, birads_category, density,
                   result_released_at, performed_date, radiologist_id)
                VALUES (:e, :p, :mod, 'B', :b, :den, :rel, :perf, :rad)
            """), {"e": encounter_id, "p": patient_id, "mod": scan.modality, "b": scan.birads,
                   "den": scan.density, "rel": _released_at(scan.date), "perf": scan.date,
                   "rad": RADIOLOGIST_STAFF_ID})

        for option, due in due_dates(f.plan, f.evaluation_date, f.followup_months).items():
            conn.execute(text("""
                INSERT INTO plan_items (patient_id, encounter_id, plan_option, due_date) VALUES (:p, :e, :o, :d)
            """), {"p": patient_id, "e": encounter_id, "o": option, "d": due})

        submitted = f.model_dump(mode="json")
        submitted.update({"patient_id": patient_id, "age": f.age, "bmi": f.bmi})
        schedule = plan_schedule(f.plan, f.evaluation_date, f.followup_months)
        snapshot_id = conn.execute(text("""
            INSERT INTO encounter_snapshot (patient_id, encounter_id, payload)
            VALUES (:p, :e, CAST(:payload AS JSONB)) RETURNING snapshot_id
        """), {"p": patient_id, "e": encounter_id, "payload": json.dumps(
            {"schema_version": "intake.v2", "submitted": submitted, "plan_schedule": schedule})}).scalar()

        risk = calculate_risk(submitted)
        conn.execute(text("""
            INSERT INTO risk_assessment (encounter_id, patient_id, snapshot_id, tier, reasons, rules_version, examiner_name)
            VALUES (:e, :p, :s, :t, CAST(:r AS JSONB), :v, :x)
        """), {"e": encounter_id, "p": patient_id, "s": snapshot_id, "t": risk.tier,
               "r": json.dumps(list(risk.reasons)), "v": risk.rules_version, "x": f.examiner_name})

        conn.execute(text("""
            INSERT INTO event_log (patient_id, event_type, actor_id, details)
            VALUES (:p, 'IntakeSubmitted', :by, CAST(:d AS JSONB))
        """), {"p": patient_id, "by": ADMIN_STAFF_ID, "d": json.dumps(
            {"encounter_id": encounter_id, "returning_patient": returning, "snapshot_id": snapshot_id,
             "risk_tier": risk.tier})})

    return {"patient_id": patient_id, "encounter_id": encounter_id, "returning_patient": returning,
            "duplicate": False, "age": f.age, "bmi": f.bmi, "warnings": f.warnings(),
            "risk_assessment": risk.as_dict(), "doctor_plan": schedule}


def lookup_patient(patient_id: str) -> dict:
    """Demographics to prefill the form, and every earlier intake (read-only)."""
    with engine.connect() as conn:
        patient = conn.execute(text("""
            SELECT patient_id, full_name, date_of_birth, phone, national_id FROM patient WHERE patient_id = :p
        """), {"p": patient_id.strip().upper()}).mappings().first()
        if patient is None:
            raise HTTPException(status_code=404, detail="Record not found.")
        history = conn.execute(text("""
            SELECT s.encounter_id, s.created_at, s.payload -> 'submitted' AS submitted, r.tier
            FROM encounter_snapshot s LEFT JOIN risk_assessment r ON r.snapshot_id = s.snapshot_id
            WHERE s.patient_id = :p ORDER BY s.created_at DESC
        """), {"p": patient["patient_id"]}).mappings().all()
        visits = conn.execute(text("""
            SELECT encounter_id, encounter_date, encounter_type, chief_complaint FROM encounter
            WHERE patient_id = :p ORDER BY encounter_date DESC
        """), {"p": patient["patient_id"]}).mappings().all()
    return {"patient": {**patient, "date_of_birth": patient["date_of_birth"].isoformat()},
            "intake_history": [dict(h) for h in history],
            "visits": [dict(v) for v in visits]}
