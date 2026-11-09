"""Due dates for the plan options chosen on the intake form (ported from screen2care)."""
from calendar import monthrange
from datetime import date, timedelta

from app.schemas import PlanOption


def add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year, month = value.year + month // 12, month % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def due_dates(plan: list[PlanOption], evaluated: date, followup_months: int | None = None) -> dict[str, date]:
    dates: dict[str, date] = {}
    if PlanOption.SHORT_TERM_FOLLOW_UP in plan:
        dates[PlanOption.SHORT_TERM_FOLLOW_UP.value] = add_months(evaluated, followup_months or 6)
    if PlanOption.ANNUAL_MRI in plan:
        dates[PlanOption.ANNUAL_MRI.value] = evaluated + timedelta(days=365)
    if PlanOption.ANNUAL_DBT_MRI_6M in plan:
        dates[PlanOption.ANNUAL_DBT_MRI_6M.value] = evaluated + timedelta(days=182)
    if PlanOption.ROUTINE_RESCREEN in plan:
        dates[PlanOption.ROUTINE_RESCREEN.value] = evaluated + timedelta(days=365)
    return dates


def plan_schedule(plan: list[PlanOption], evaluated: date, followup_months: int | None = None) -> list[dict]:
    """Every chosen option with its due date (if it has one), for the 'saved' summary."""
    due = due_dates(plan, evaluated, followup_months)
    out = []
    for option in plan:
        if option == PlanOption.ANNUAL_DBT_MRI_6M:
            out += [
                {"option": "Annual mammogram (DBT)", "due_date": evaluated.isoformat(),
                 "recurrence": f"Then {add_months(evaluated, 12).isoformat()} and every year"},
                {"option": "Annual breast MRI (6 months after the mammogram)",
                 "due_date": add_months(evaluated, 6).isoformat(),
                 "recurrence": f"Then {add_months(evaluated, 18).isoformat()} and every year"},
            ]
        else:
            d = due.get(option.value)
            out.append({"option": option.value, "due_date": d.isoformat() if d else None, "recurrence": None})
    return out
