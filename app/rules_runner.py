from datetime import datetime

from sqlalchemy import text

from app.care_loop import open_episode
from app.database import engine
from app.rules import Inputs, evaluate

def load_inputs(conn, patient_id: str) -> Inputs:
    p = conn.execute(text("""
        SELECT p.date_of_birth, r.genetic_status, r.chest_radiation_age, r.lifetime_risk_pct,
               r.high_risk_lesion AS lesion,
               (SELECT COUNT(*) > 0 FROM comorbidity c
                 WHERE c.patient_id = p.patient_id AND c.is_severe_active) AS severe,
               g.end_stage_organ_failure_or_dementia AS end_stage,
               g.life_expectancy_over_10y AS life_exp
        FROM patient p JOIN patient_risk_profile r USING (patient_id)
        LEFT JOIN LATERAL (                 -- latest geriatric assessment, if any
            SELECT ga.* FROM geriatric_assessment ga
            JOIN encounter en ON en.encounter_id = ga.encounter_id
            WHERE en.patient_id = p.patient_id
            ORDER BY en.encounter_date DESC LIMIT 1
        ) g ON TRUE
        WHERE p.patient_id = :pid
    """), {"pid": patient_id}).mappings().one()

    latest = conn.execute(text("""
        SELECT birads_category, density, result_released_at FROM imaging_result
        WHERE patient_id = :pid ORDER BY result_released_at DESC, birads_category DESC LIMIT 1
    """), {"pid": patient_id}).mappings().first()

    today = datetime.now().date()
    dob = p["date_of_birth"]
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    return Inputs(
        eval_date=today,
        age=age,
        genetic_status=p["genetic_status"],
        chest_radiation_age=p["chest_radiation_age"],
        lifetime_risk_pct=float(p["lifetime_risk_pct"]) if p["lifetime_risk_pct"] is not None else None,
        high_risk_lesion=p["lesion"],
        birads=latest["birads_category"] if latest else None,
        density=latest["density"] if latest else None,
        life_expectancy_over_10y=p["life_exp"],
        # end-stage organ failure or advanced dementia counts as a serious condition
        severe_comorbidity=bool(p["severe"] or p["end_stage"]),
    )


def run_engine(patient_id: str) -> dict:
    with engine.begin() as conn:
        inputs = load_inputs(conn, patient_id)
        d = evaluate(inputs)

        conn.execute(text("UPDATE lane_assignment SET is_current = FALSE WHERE patient_id = :pid AND is_current"),
                     {"pid": patient_id})
        conn.execute(text("""
            INSERT INTO lane_assignment (patient_id, lane, next_due_date, reason, assigned_by)
            VALUES (:pid, :lane, :due, :reason, 'Engine')
        """), {"pid": patient_id, "lane": d.lane, "due": d.next_due, "reason": d.reason})

        if d.lane == "UrgentWorkup":
            open_episode(conn, patient_id)   # starts S1 and sends the result SMS

    return {"patient_id": patient_id, "lane": d.lane, "reason": d.reason,
            "next_due": str(d.next_due) if d.next_due else None, "flags": d.flags}
