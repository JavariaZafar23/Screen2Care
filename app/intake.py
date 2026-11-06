import json

from sqlalchemy import text

from app.database import engine
from app.schemas import IntakeForm

# Until login exists, entries are attributed to the seeded admin and clinician accounts
ADMIN_STAFF_ID = 1
RADIOLOGIST_STAFF_ID = 2


def _chief_complaint(f: IntakeForm) -> str:
    if not f.is_symptomatic:
        return "Asymptomatic (Routine Screening)"
    parts = []
    if f.lump_side:
        parts.append(f"lump / mass: {f.lump_side.lower()}" + (f", {f.lump_duration}" if f.lump_duration else ""))
    if f.nipple_discharge_side:
        parts.append(f"nipple discharge: {f.nipple_discharge_side.lower()}"
                     + (f", {f.nipple_discharge_color.lower()}" if f.nipple_discharge_color else ""))
    if f.skin_changes:
        parts.append("skin changes: " + ", ".join(s.lower() for s in f.skin_changes))
    if f.pain_type:
        parts.append(f"pain: {f.pain_type.lower()}" + (f", severity {f.pain_severity}/10" if f.pain_severity else ""))
    return "Symptomatic: " + "; ".join(parts) if parts else "Symptomatic"


def save_intake(f: IntakeForm) -> dict:
    """Save one completed proforma in a single transaction."""
    with engine.begin() as conn:
        patient_id = None
        if f.national_id:
            patient_id = conn.execute(
                text("SELECT patient_id FROM patient WHERE national_id = :nid"), {"nid": f.national_id}
            ).scalar()
        returning = patient_id is not None

        if not returning:
            patient_id = conn.execute(
                text("SELECT 'BC-' || to_char(nextval('patient_number_seq'), 'FM00000')")
            ).scalar()
            conn.execute(text("""
                INSERT INTO patient (patient_id, full_name, date_of_birth, national_id, created_by)
                VALUES (:pid, :name, :dob, :nid, :by)
            """), {"pid": patient_id, "name": f.full_name, "dob": f.date_of_birth,
                   "nid": f.national_id, "by": ADMIN_STAFF_ID})

        genetic_status = {"BRCA1": "Positive", "BRCA2": "Positive"}.get(f.genetic_result, f.genetic_result)
        conn.execute(text("""
            INSERT INTO patient_risk_profile
              (patient_id, menarche_age, menopausal_status, menopause_age, parity, age_first_birth,
               ocp_use, hrt_use, hrt_duration_years, height_cm, weight_kg,
               genetic_status, genetic_mutation, risk_data_status)
            VALUES (:pid, :menarche, :mstatus, :mage, :parity, :afb, :ocp, :hrt, :hrtd, :h, :w,
                    :gstatus, :gmut, :rstatus)
            ON CONFLICT (patient_id) DO UPDATE SET
              menarche_age = EXCLUDED.menarche_age, menopausal_status = EXCLUDED.menopausal_status,
              menopause_age = EXCLUDED.menopause_age, parity = EXCLUDED.parity,
              age_first_birth = EXCLUDED.age_first_birth, ocp_use = EXCLUDED.ocp_use,
              hrt_use = EXCLUDED.hrt_use, hrt_duration_years = EXCLUDED.hrt_duration_years,
              height_cm = EXCLUDED.height_cm, weight_kg = EXCLUDED.weight_kg,
              genetic_status = EXCLUDED.genetic_status, genetic_mutation = EXCLUDED.genetic_mutation,
              updated_at = now()
        """), {"pid": patient_id, "menarche": f.menarche_age, "mstatus": f.menopausal_status,
               "mage": f.menopause_age, "parity": f.parity, "afb": f.age_first_birth,
               "ocp": f.ocp_use, "hrt": f.hrt_use, "hrtd": f.hrt_duration_years,
               "h": f.height_cm, "w": f.weight_kg, "gstatus": genetic_status,
               "gmut": f.genetic_result if genetic_status == "Positive" else None,
               "rstatus": "Complete" if genetic_status == "Positive" else "Pending"})

        # Family history and prior procedures: add only what is not already on file
        relatives = [(r.relation, 2 if "Aunt" in r.relation else 1, "Breast", r.age_at_diagnosis)
                     for r in f.breast_cancer_relatives]
        if f.ovarian_cancer:
            rel = f.ovarian_relation or "Unspecified"
            degree = None if rel == "Unspecified" else (2 if "Aunt" in rel else 1)
            relatives.append((rel, degree, "Ovarian", None))
        for relation, degree, cancer, age in relatives:
            conn.execute(text("""
                INSERT INTO family_history (patient_id, relation, relation_degree, cancer_type, age_at_diagnosis)
                SELECT CAST(:pid AS VARCHAR), CAST(:rel AS VARCHAR), CAST(:deg AS SMALLINT),
                       CAST(:cancer AS VARCHAR), CAST(:age AS SMALLINT)
                WHERE NOT EXISTS (SELECT 1 FROM family_history
                                  WHERE patient_id = CAST(:pid AS VARCHAR)
                                    AND relation = CAST(:rel AS VARCHAR)
                                    AND cancer_type = CAST(:cancer AS VARCHAR))
            """), {"pid": patient_id, "rel": relation, "deg": degree, "cancer": cancer, "age": age})

        for proc in f.prior_breast_history:
            conn.execute(text("""
                INSERT INTO prior_breast_procedure (patient_id, procedure_type, result_summary)
                SELECT CAST(:pid AS VARCHAR), CAST(:ptype AS VARCHAR), CAST(:details AS VARCHAR)
                WHERE NOT EXISTS (SELECT 1 FROM prior_breast_procedure
                                  WHERE patient_id = CAST(:pid AS VARCHAR)
                                    AND procedure_type = CAST(:ptype AS VARCHAR))
            """), {"pid": patient_id, "ptype": proc, "details": f.prior_breast_details})

        encounter_id = conn.execute(text("""
            INSERT INTO encounter
              (patient_id, encounter_date, encounter_type, is_symptomatic, chief_complaint, entered_by,
               lump_side, lump_duration, nipple_discharge_side, nipple_discharge_color, skin_changes,
               pain_type, pain_severity, disposition, followup_months, examiner_name, signed_date)
            VALUES (:pid, :edate, :etype, :sym, :cc, :by, :lump, :ldur, :nds, :ndc, :skin,
                    :ptype, :psev, :disp, :fum, :exam, :signed)
            RETURNING encounter_id
        """), {"pid": patient_id, "edate": f.evaluation_date,
               "etype": "Diagnostic" if f.is_symptomatic else "Screening",
               "sym": f.is_symptomatic, "cc": _chief_complaint(f), "by": ADMIN_STAFF_ID,
               "lump": f.lump_side, "ldur": f.lump_duration, "nds": f.nipple_discharge_side,
               "ndc": f.nipple_discharge_color, "skin": f.skin_changes or None,
               "ptype": f.pain_type, "psev": f.pain_severity, "disp": f.disposition,
               "fum": f.followup_months, "exam": f.examiner_name, "signed": f.signed_date}).scalar()

        if f.age >= 75:
            conn.execute(text("""
                INSERT INTO geriatric_assessment
                  (encounter_id, end_stage_organ_failure_or_dementia, independent_in_adl)
                VALUES (:eid, :es, :adl)
            """), {"eid": encounter_id, "es": f.geriatric_end_stage, "adl": f.geriatric_independent_adl})

        for side, cbe in (("R", f.cbe_right), ("L", f.cbe_left)):
            conn.execute(text("""
                INSERT INTO clinical_breast_exam
                  (encounter_id, laterality, inspection_normal, inspection_finding, mass_present,
                   mass_size_mm, mass_mobility, mass_consistency, axillary_node_palpable)
                VALUES (:eid, :side, :inorm, :ifind, :mass, :size, :mob, :cons, :node)
            """), {"eid": encounter_id, "side": side, "inorm": cbe.inspection_normal,
                   "ifind": cbe.inspection_finding, "mass": cbe.mass_present, "size": cbe.mass_size_mm,
                   "mob": cbe.mass_mobility, "cons": cbe.mass_consistency, "node": cbe.axillary_node_palpable})

        if f.birads is not None:
            for modality in f.modalities:
                conn.execute(text("""
                    INSERT INTO imaging_result
                      (encounter_id, patient_id, modality, laterality, birads_category, density,
                       result_released_at, radiologist_id)
                    VALUES (:eid, :pid, :mod, 'B', :birads, :density, now(), :rad)
                """), {"eid": encounter_id, "pid": patient_id, "mod": modality, "birads": f.birads,
                       "density": f.density if modality in ("Mammogram", "DBT") else None,
                       "rad": RADIOLOGIST_STAFF_ID})

        conn.execute(text("""
            INSERT INTO event_log (patient_id, event_type, actor_id, details)
            VALUES (:pid, 'IntakeSubmitted', :by, CAST(:details AS JSONB))
        """), {"pid": patient_id, "by": ADMIN_STAFF_ID,
               "details": json.dumps({"encounter_id": encounter_id, "returning_patient": returning})})

    return {"patient_id": patient_id, "encounter_id": encounter_id, "returning_patient": returning}
