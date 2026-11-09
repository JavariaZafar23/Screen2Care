# Pure logic: no database, no web code. Same input always gives the same output.
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional


@dataclass
class Inputs:
    eval_date: date
    age: int
    genetic_status: str                      # Positive / Negative / Unknown / Pending
    chest_radiation_age: Optional[int]       # age when chest radiation was given
    lifetime_risk_pct: Optional[float]       # from a risk model (Tyrer-Cuzick / Gail)
    high_risk_lesion: Optional[str]          # ADH / ALH / LCIS
    birads: Optional[int]                    # latest imaging BI-RADS (0-6)
    density: Optional[str]                   # A / B / C / D
    life_expectancy_over_10y: Optional[bool] # clinician judgement, needed for age 75+
    severe_comorbidity: bool


@dataclass
class Decision:
    lane: str
    reason: str
    next_due: Optional[date] = None
    flags: list = field(default_factory=list)


# ---------- STEP 1: high-risk factors (any age) ----------
def step1_high_risk(i: Inputs) -> Optional[Decision]:
    radiation_window = i.chest_radiation_age is not None and 10 <= i.chest_radiation_age <= 30

    if i.genetic_status == "Positive" or radiation_window:
        # 8 years after the radiation, but never before age 25
        start = max(25, i.chest_radiation_age + 8) if radiation_window else 25
        why = ("gene mutation positive" if i.genetic_status == "Positive"
               else f"chest radiation at age {i.chest_radiation_age}")
        if i.age < start:
            return Decision("HighRisk-Waiting", f"{why}; screening starts at age {start}", None)
        if i.age <= 29:
            return Decision("HighRisk-MRIOnly", f"{why}; age 25-29: annual MRI only",
                            i.eval_date + timedelta(days=365))
        return Decision("HighRisk-DBT+MRI", f"{why}; age 30+: DBT and MRI alternating every 6 months",
                        i.eval_date + timedelta(days=182))

    if (i.lifetime_risk_pct is not None and i.lifetime_risk_pct >= 20) or i.high_risk_lesion:
        why = (f"lifetime risk {i.lifetime_risk_pct}%" if (i.lifetime_risk_pct or 0) >= 20
               else f"history of {i.high_risk_lesion}")
        if i.age < 30:
            return Decision("HighRisk-Waiting", f"{why}; screening starts at age 30", None)
        return Decision("HighRisk-DBT+ConsiderMRI", f"{why}; annual DBT, consider annual MRI",
                        i.eval_date + timedelta(days=365))

    return None  # no high-risk factors: go to step 2


# ---------- STEP 2: age groups for average risk ----------
def step2_age(i: Inputs) -> Optional[Decision]:
    if i.age < 40:
        return Decision("WellnessHold", "under 40, no routine screening unless symptomatic", None)
    if i.age >= 75:
        return step4_over75(i)
    return None  # 40-74: go to step 3


# ---------- STEP 4: age 75+ ----------
def step4_over75(i: Inputs) -> Decision:
    # A serious condition ends routine screening whatever the life-expectancy answer is
    if i.severe_comorbidity:
        return Decision("SymptomaticCareOnly", "age 75+ with a serious medical condition", None)
    if i.life_expectancy_over_10y is None:
        return Decision("DataPending", "age 75+: life expectancy not yet recorded", None,
                        ["Clinician must record life expectancy"])
    if i.life_expectancy_over_10y:
        return Decision("RoutineScreening", "age 75+, life expectancy over 10 years, no serious comorbidity",
                        i.eval_date + timedelta(days=365))
    return Decision("SymptomaticCareOnly", "age 75+, life expectancy under 10 years", None)


# ---------- STEP 3: BI-RADS matrix (applies to every lane's imaging result) ----------
def step3_birads(i: Inputs) -> Optional[Decision]:
    if i.birads is None:
        return Decision("DataPending", "no imaging result yet", None)
    b = i.birads
    if b == 0:
        return Decision("RecallBirads0", "BI-RADS 0: recall for additional imaging",
                        i.eval_date + timedelta(days=30))
    if b in (1, 2):
        if i.density in ("C", "D"):
            return Decision("DenseSupplemental",
                            f"BI-RADS {b} with density {i.density}: tell patient, discuss supplemental imaging",
                            i.eval_date + timedelta(days=365), ["Dense tissue: supplemental imaging discussion"])
        return Decision("RoutineScreening", f"BI-RADS {b}, density {i.density}: routine hold",
                        i.eval_date + timedelta(days=365))
    if b == 3:
        return Decision("ShortInterval6M", "BI-RADS 3: 6-month recall, 1-year extension blocked",
                        i.eval_date + timedelta(days=182))
    if b in (4, 5):
        return Decision("UrgentWorkup", f"BI-RADS {b}: critical loop, biopsy needed",
                        i.eval_date, ["SLA S1 started: notify patient within 24 hours"])
    if b == 6:
        return Decision("Treatment", "BI-RADS 6: known cancer, go to treatment track", None)
    raise ValueError(f"Unknown BI-RADS {b}")


# ---------- Full run ----------
def evaluate(i: Inputs) -> Decision:
    # Urgent findings always win, even in a high-risk lane
    if i.birads in (4, 5):
        return step3_birads(i)

    d = step1_high_risk(i)
    if d is None:
        d = step2_age(i)
    if d is None:
        d = step3_birads(i)                  # ages 40-74 with no high-risk factors
    elif d.lane.startswith("HighRisk") and i.birads is not None:
        imaging = step3_birads(i)            # high-risk lane still checks the BI-RADS result
        if imaging.lane in ("RecallBirads0", "ShortInterval6M", "Treatment", "DataPending"):
            d = imaging
    return d
