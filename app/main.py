import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text

import json

from app import accounts, care_loop, llm
from app.access import LIST_TITLES, ensure_patient_access, fetch_patients, scope_filter
from app.auth import (
    COOKIE_NAME, TOKEN_HOURS, authenticate, create_token, get_current_user, require_role,
)
from app.database import engine
from app.intake import clinical_form, lookup_patient, save_intake
from app import followup, registration
from app.patient_portal import get_portal
from app.plan import (
    DISPOSITION_LABELS, PlanIn, ReleaseNoteIn, get_plan_page, note_source_text, release_patient_note, save_plan,
)
from app.rules_runner import run_engine

BASE_DIR = Path(__file__).parent

log = logging.getLogger("care_loop")
SLA_CHECK_SECONDS = int(os.getenv("SLA_CHECK_SECONDS", "60"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def sla_watch():
        while True:
            try:
                breached = await asyncio.to_thread(care_loop.check_sla)
                if breached:
                    log.warning("SLA check: %d new breach(es) escalated", breached)
            except Exception:
                log.exception("SLA check failed")
            await asyncio.sleep(SLA_CHECK_SECONDS)

    task = asyncio.create_task(sla_watch())
    yield
    task.cancel()


app = FastAPI(title="Screening-to-Care Loop", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

STAFF_ROLES = ("Admin", "Clinician", "Radiologist", "Pathologist", "Navigator", "CMO",
               "SchedulingManager", "LabDirector", "TumorBoard")


ROLE_LABELS = {"SchedulingManager": "Scheduling Manager", "LabDirector": "Lab Director",
               "TumorBoard": "Tumor Board", "CMO": "Chief Medical Officer"}


def nav_for(user: dict) -> list[tuple[str, str, str]]:
    """Sidebar links for this role: (label, href, icon)."""
    role = user["role"]
    if role == "Patient":
        return [("My record", "/", "home")]
    items = [("Dashboard", "/", "home")]
    if role == "Admin":
        items += [("Register patient", "/register", "plus"), ("Account requests", "/admin/accounts", "users")]
    if role == "Clinician":
        items.append(("Clinical intake", "/intake", "plus"))
    if role in llm.ASSISTANT_ROLES:
        items.append(("Care Assistant", "/assistant", "chat"))
    return items


def page(request: Request, name: str, user: dict | None, **ctx):
    """Render a page inside the shared layout (sidebar, user chip, theme)."""
    return templates.TemplateResponse(request, name, {
        "user": user, "nav": nav_for(user) if user else [], "path": request.url.path,
        "role_label": ROLE_LABELS.get(user["role"], user["role"]) if user else "", "role_labels": ROLE_LABELS, **ctx})


def log_ai(patient_id: str | None, kind: str, staff_id: int | None, **details):
    if patient_id is None:
        return
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO event_log (patient_id, event_type, actor_id, details)
            VALUES (:p, 'AIDraftCreated', :a, CAST(:d AS JSONB))
        """), {"p": patient_id, "a": staff_id,
               "d": json.dumps({"kind": kind, "model": llm.OLLAMA_MODEL, **details})})


class LoginIn(BaseModel):
    username: str
    password: str


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/login")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return page(request, "login.html", None)


@app.post("/api/login")
def login(data: LoginIn):
    user = authenticate(data.username, data.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong username or password")
    response = JSONResponse({"role": user["role"]})
    response.set_cookie(
        COOKIE_NAME, create_token(user),
        httponly=True, samesite="lax", max_age=TOKEN_HOURS * 3600,
    )
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"status": "logged out"})
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: dict = Depends(get_current_user)):
    if user["role"] == "Patient":
        return page(request, "patient.html", user, **get_portal(user["patient_id"]),
                    ack_needed=care_loop.s1_pending(user["patient_id"]),
                    followup=followup.patient_view(user["patient_id"]))
    care_loop.check_sla()
    return page(request, "dashboard.html", user,
                patients=fetch_patients(user), alerts=care_loop.open_alerts(user["role"]),
                notifications=care_loop.open_notifications(user["role"]),
                results_due=care_loop.results_to_discuss(*scope_filter(user), role=user["role"])
                if user["role"] in care_loop.STEP_ROLES["book_results"] else [],
                referrals_due=care_loop.referrals_to_make(*scope_filter(user))
                if user["role"] in care_loop.STEP_ROLES["assign_referral"] else [],
                list_title=LIST_TITLES.get(user["role"], "Patients"),
                waiting_intake=registration.waiting_for_intake(user.get("staff_id"))
                if user["role"] == "Clinician" else [],
                closed_loops=care_loop.closed_without_treatment() if user["role"] == "CMO" else [],
                closed_notice=care_loop.closure_notice(request.query_params["closed"],
                                                       request.query_params.get("kind", "care"))
                if request.query_params.get("closed") else None,
                followups=followup.worklist(user, *scope_filter(user)),
                followup_upcoming=followup.upcoming_count() if user["role"] in ("Navigator", "CMO") else 0,
                pending_accounts=len(accounts.pending_requests()) if user["role"] == "Admin" else 0)


# ---------- Front desk: registration (Admin, demographics only) ----------

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user: dict = Depends(require_role("Admin"))):
    return page(request, "register.html", user, clinicians=registration.active_clinicians())


@app.post("/api/registration", status_code=201)
def post_registration(data: registration.RegistrationIn, user: dict = Depends(require_role("Admin"))):
    return registration.register_patient(data, user.get("staff_id"))


@app.get("/api/registration/patients/{patient_id}")
def registration_lookup(patient_id: str, user: dict = Depends(require_role("Admin"))):
    return registration.lookup_registration(patient_id)


# ---------- Clinician: clinical proforma, including the imaging report and BI-RADS ----------

@app.get("/intake", response_class=HTMLResponse)
def intake_form(request: Request, user: dict = Depends(require_role("Clinician"))):
    return page(request, "intake.html", user, waiting=registration.waiting_for_intake(user.get("staff_id")))


@app.post("/api/intake", status_code=201)
def submit_intake(payload: dict = Body(...), user: dict = Depends(require_role("Clinician"))):
    pid = str(payload.get("patient_id") or "").strip().upper()
    if pid:
        ensure_patient_access(user, pid)
    form = clinical_form(payload, user)
    saved = save_intake(form, user.get("staff_id"))
    # Evaluate straight away: BI-RADS 4/5 opens the care loop (biopsy) and starts the S1 clock
    if not saved.get("duplicate"):
        saved["lane"] = run_engine(saved["patient_id"])["lane"]
    return saved


@app.get("/api/intake/patients/{patient_id}")
def intake_lookup(patient_id: str, user: dict = Depends(require_role("Clinician"))):
    """Load a registered patient into the proforma: demographics and earlier intakes."""
    ensure_patient_access(user, patient_id.strip().upper())
    return lookup_patient(patient_id)


@app.get("/api/patients")
def list_patients(user: dict = Depends(require_role(*STAFF_ROLES))):
    return fetch_patients(user)


@app.get("/api/patients/{patient_id}")
def get_patient(patient_id: str, user: dict = Depends(get_current_user)):
    ensure_patient_access(user, patient_id)
    if user["role"] not in (*STAFF_ROLES, "Patient"):
        raise HTTPException(status_code=403, detail="Not allowed for this role")

    with engine.connect() as conn:
        patient = conn.execute(
            text("SELECT * FROM patient WHERE patient_id = :pid"), {"pid": patient_id}
        ).mappings().first()
        if patient is None:
            raise HTTPException(status_code=404, detail="Patient not found")
        basic = {k: v for k, v in patient.items() if k != "national_id"}

        def rows(sql):
            return [dict(r) for r in conn.execute(text(sql), {"pid": patient_id}).mappings()]

        if user["role"] == "Admin":
            return {"patient": basic}

        if user["role"] == "Patient":
            return {"patient": basic,
                    "released_notes": rows("SELECT * FROM vw_patient_released_notes WHERE patient_id = :pid")}

        return {
            "patient": basic,
            "risk_profile": rows("SELECT * FROM patient_risk_profile WHERE patient_id = :pid"),
            "family_history": rows("SELECT * FROM family_history WHERE patient_id = :pid"),
            "encounters": rows("SELECT * FROM encounter WHERE patient_id = :pid ORDER BY encounter_date"),
            "imaging": rows("SELECT * FROM imaging_result WHERE patient_id = :pid ORDER BY result_released_at"),
            "flags": rows("SELECT flag FROM vw_data_quality_flags WHERE patient_id = :pid"),
        }


@app.get("/api/data-quality-flags")
def data_quality_flags(user: dict = Depends(require_role(*STAFF_ROLES))):
    cond, params = scope_filter(user)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(f"""
            SELECT f.* FROM vw_data_quality_flags f JOIN patient p ON p.patient_id = f.patient_id
            WHERE {cond} ORDER BY f.patient_id
        """), params).mappings()]


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/plan/{patient_id}", response_class=HTMLResponse)
def plan_page(request: Request, patient_id: str, user: dict = Depends(require_role("Clinician"))):
    ensure_patient_access(user, patient_id)
    ctx = get_plan_page(patient_id)
    return page(request, "plan.html", user, **ctx, labels=DISPOSITION_LABELS)


@app.post("/api/patients/{patient_id}/plan")
def post_plan(patient_id: str, data: PlanIn, user: dict = Depends(require_role("Clinician"))):
    ensure_patient_access(user, patient_id)
    return save_plan(patient_id, data, clinician_id=user["staff_id"])


@app.post("/api/patients/{patient_id}/run-engine")
def run_patient_engine(patient_id: str, user: dict = Depends(require_role("Clinician", "Admin"))):
    ensure_patient_access(user, patient_id)
    return run_engine(patient_id)


# ---------- Care loop and SLA ---------------------------------------------------

@app.get("/loop/{patient_id}", response_class=HTMLResponse)
def loop_page(request: Request, patient_id: str, user: dict = Depends(require_role(*STAFF_ROLES))):
    if user["role"] == "Admin":
        raise HTTPException(status_code=403, detail="Not allowed for this role")
    ensure_patient_access(user, patient_id)
    care_loop.check_sla()
    return page(request, "loop.html", user, **care_loop.get_loop(patient_id),
                can={step: user["role"] in roles for step, roles in care_loop.STEP_ROLES.items()},
                my_close_reasons=[r for r, roles in care_loop.CLOSE_REASONS.items() if user["role"] in roles])


@app.get("/api/alerts")
def list_alerts(user: dict = Depends(require_role(*STAFF_ROLES))):
    return care_loop.open_alerts(user["role"])


@app.post("/api/alerts/{alert_id}/seen")
def alert_seen(alert_id: int, user: dict = Depends(require_role(*STAFF_ROLES))):
    return care_loop.mark_alert_seen(alert_id, user)


@app.get("/api/notifications")
def list_notifications(user: dict = Depends(require_role(*STAFF_ROLES))):
    return care_loop.open_notifications(user["role"])


@app.post("/api/notifications/{notification_id}/seen")
def notification_seen(notification_id: int, user: dict = Depends(require_role(*STAFF_ROLES))):
    return care_loop.mark_notification_seen(notification_id, user)


@app.post("/api/sla/check")
def sla_check(user: dict = Depends(require_role("Admin", "CMO"))):
    return {"new_breaches": care_loop.check_sla()}


@app.post("/api/me/acknowledge")
def acknowledge_result(user: dict = Depends(require_role("Patient"))):
    return care_loop.patient_acknowledge(user["patient_id"])


def step_user(step: str):
    """Logged-in user allowed to do this loop step, and allowed to see this patient."""
    def checker(patient_id: str, user: dict = Depends(require_role(*care_loop.STEP_ROLES[step]))) -> dict:
        ensure_patient_access(user, patient_id)
        return user
    return checker


# ---------- Imaging follow-up loop (BI-RADS 0/3, annual, dense, high risk) ----------

def followup_user(step: str):
    def checker(patient_id: str, user: dict = Depends(require_role(*followup.STEP_ROLES[step]))) -> dict:
        ensure_patient_access(user, patient_id)
        return user
    return checker


@app.get("/followup/{patient_id}", response_class=HTMLResponse)
def followup_page(request: Request, patient_id: str, user: dict = Depends(require_role(*STAFF_ROLES))):
    if user["role"] == "Admin":
        raise HTTPException(status_code=403, detail="Not allowed for this role")
    ensure_patient_access(user, patient_id)
    care_loop.check_sla()
    return page(request, "followup.html", user, **followup.get_followup(patient_id),
                can={step: user["role"] in roles for step, roles in followup.STEP_ROLES.items()},
                my_close_reasons=[r for r, roles in care_loop.CLOSE_REASONS.items() if user["role"] in roles])


@app.post("/api/patients/{patient_id}/followup/contact")
def post_followup_contact(patient_id: str, data: followup.FollowupContactIn,
                          user: dict = Depends(followup_user("contact"))):
    return followup.record_contact(patient_id, data, user.get("staff_id"))


@app.post("/api/patients/{patient_id}/followup/book")
def post_followup_book(patient_id: str, data: followup.FollowupBookIn, user: dict = Depends(followup_user("book"))):
    return followup.book(patient_id, data, user.get("staff_id"))


@app.post("/api/patients/{patient_id}/followup/performed")
def post_followup_performed(patient_id: str, user: dict = Depends(followup_user("performed"))):
    return followup.performed(patient_id, user.get("staff_id"))


@app.post("/api/patients/{patient_id}/followup/report")
def post_followup_report(patient_id: str, data: followup.FollowupReportIn,
                         user: dict = Depends(followup_user("report"))):
    return followup.report(patient_id, data, user.get("staff_id"))


@app.post("/api/patients/{patient_id}/followup/close")
def post_followup_close(patient_id: str, data: followup.FollowupCloseIn, user: dict = Depends(followup_user("close"))):
    return followup.close(patient_id, data, user)


@app.post("/api/patients/{patient_id}/followup/reopen")
def post_followup_reopen(patient_id: str, data: followup.FollowupReopenIn,
                         user: dict = Depends(followup_user("reopen"))):
    return followup.reopen(patient_id, data, user.get("staff_id"))


@app.post("/api/me/followup-confirm")
def confirm_followup(user: dict = Depends(require_role("Patient"))):
    return followup.patient_confirm(user["patient_id"])


@app.post("/api/patients/{patient_id}/contact")
def post_contact(patient_id: str, data: care_loop.ContactIn, user: dict = Depends(step_user("contact"))):
    return care_loop.record_contact(patient_id, data, user["staff_id"])


@app.post("/api/patients/{patient_id}/biopsy")
def post_biopsy(patient_id: str, data: care_loop.BiopsyIn, user: dict = Depends(step_user("book_biopsy"))):
    return care_loop.book_biopsy(patient_id, data, user["staff_id"])


@app.post("/api/patients/{patient_id}/biopsy/performed")
def post_biopsy_performed(patient_id: str, user: dict = Depends(step_user("biopsy_performed"))):
    return care_loop.biopsy_performed(patient_id, user["staff_id"])


@app.post("/api/patients/{patient_id}/pathology")
def post_pathology(patient_id: str, data: care_loop.PathologyIn, user: dict = Depends(step_user("pathology"))):
    return care_loop.record_pathology(patient_id, data, user["staff_id"])


@app.post("/api/patients/{patient_id}/referral/assign")
def post_assign(patient_id: str, data: care_loop.AssignIn, user: dict = Depends(step_user("assign_referral"))):
    return care_loop.assign_referral(patient_id, data, user["staff_id"])


@app.post("/api/patients/{patient_id}/treatment")
def post_treatment(patient_id: str, data: care_loop.TreatmentIn, user: dict = Depends(step_user("treatment"))):
    return care_loop.start_treatment(patient_id, data, user["staff_id"])


@app.post("/api/patients/{patient_id}/results-appointment")
def post_results_appointment(patient_id: str, data: care_loop.ResultsAppointmentIn,
                             user: dict = Depends(step_user("book_results"))):
    return care_loop.book_results_appointment(patient_id, data, user["staff_id"], user["role"])


@app.post("/api/patients/{patient_id}/close-loop")
def post_close_loop(patient_id: str, data: care_loop.CloseLoopIn, user: dict = Depends(step_user("close_loop"))):
    return care_loop.close_loop(patient_id, data, user)


@app.post("/api/patients/{patient_id}/reopen-loop")
def post_reopen_loop(patient_id: str, data: care_loop.ReopenLoopIn, user: dict = Depends(step_user("reopen_loop"))):
    return care_loop.reopen_loop(patient_id, data, user.get("staff_id"))


@app.post("/api/patients/{patient_id}/result-disclosed")
def post_result_disclosed(patient_id: str, data: care_loop.DiscloseIn,
                          user: dict = Depends(step_user("disclose_result"))):
    return care_loop.disclose_result(patient_id, data, user["staff_id"])


# ---------- Sign-up and account approval ---------------------------------------------

@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return page(request, "signup.html", None, staff_roles=[
        (r, ROLE_LABELS.get(r, r)) for r in accounts.STAFF_SIGNUP_ROLES])


@app.post("/api/signup/patient", status_code=201)
def post_signup_patient(data: accounts.PatientSignupIn):
    return accounts.signup_patient(data)


@app.post("/api/signup/staff", status_code=201)
def post_signup_staff(data: accounts.StaffSignupIn):
    return accounts.signup_staff(data)


@app.get("/admin/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, user: dict = Depends(require_role("Admin"))):
    return page(request, "accounts.html", user, requests=accounts.pending_requests(), labels=ROLE_LABELS)


@app.post("/api/admin/signup-requests/{request_id}/{decision}")
def decide_signup(request_id: int, decision: str, user: dict = Depends(require_role("Admin"))):
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=404, detail="Unknown decision")
    return accounts.decide_request(request_id, decision == "approve", user["staff_id"])


# ---------- Local AI (Ollama): drafts and the Care Assistant agent -------------------

class AssistantIn(BaseModel):
    question: str
    history: list[dict] = []


@app.get("/api/ai/status")
def ai_status(user: dict = Depends(require_role(*STAFF_ROLES))):
    return llm.status()


@app.get("/assistant", response_class=HTMLResponse)
def assistant_page(request: Request, user: dict = Depends(require_role(*llm.ASSISTANT_ROLES))):
    return page(request, "assistant.html", user, ai=llm.status())


@app.post("/api/assistant")
def ask_assistant(data: AssistantIn, user: dict = Depends(require_role(*llm.ASSISTANT_ROLES))):
    question = data.question.strip()
    if not 2 <= len(question) <= 1000:
        raise HTTPException(status_code=422, detail="Ask a question between 2 and 1000 characters.")
    return llm.run_assistant(user, question, data.history)


@app.post("/api/patients/{patient_id}/draft-note")
def draft_note(patient_id: str, user: dict = Depends(require_role("Clinician"))):
    ensure_patient_access(user, patient_id)
    source = note_source_text(patient_id)
    draft = llm.draft_patient_note(source)
    log_ai(patient_id, "patient_note", user["staff_id"])
    return {"draft": draft, "source": source, "model": llm.OLLAMA_MODEL}


@app.post("/api/patients/{patient_id}/release-note")
def release_note(patient_id: str, data: ReleaseNoteIn, user: dict = Depends(require_role("Clinician"))):
    ensure_patient_access(user, patient_id)
    return release_patient_note(patient_id, data, user["staff_id"])


@app.post("/api/patients/{patient_id}/call-script")
def call_script(patient_id: str, user: dict = Depends(require_role("Navigator", "Clinician"))):
    ensure_patient_access(user, patient_id)
    loop = care_loop.get_loop(patient_id)
    s1 = loop["sections"][0] if loop["sections"] else None
    if not s1 or not s1["task"] or s1["task"]["completed_at"]:
        raise HTTPException(status_code=409, detail="This patient has no unanswered result notification.")
    first_name = loop["patient"]["full_name"].split()[0]
    overdue = -s1["hours_left"] if s1["hours_left"] is not None and s1["hours_left"] < 0 else None
    script = llm.draft_call_script(first_name, overdue)
    log_ai(patient_id, "call_script", user["staff_id"])
    return {"script": script, "model": llm.OLLAMA_MODEL}
