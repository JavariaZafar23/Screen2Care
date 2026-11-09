# Screening-to-Care Loop

FastAPI + PostgreSQL (pgvector) + server-rendered HTML, with a free local LLM (Ollama).

## Run it

```bash
docker compose up -d                     # start PostgreSQL (loads db/*.sql on first start)
python -m venv .venv
# Windows:   .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:
- http://localhost:8000          dashboard
- http://localhost:8000/intake   new patient intake form (proforma)
- http://localhost:8000/docs     API docs (Swagger)
- http://localhost:8000/health   health check

## Reset the database

The SQL files only run when the database volume is empty. After changing them:

```bash
docker compose down -v
docker compose up -d
```

## Project layout

```
db/01_schema.sql      tables, indexes, views
db/02_seed.sql        staff, SLA rules, 15 test patients
db/03_intake_form.sql extra columns for the intake form
db/06_access.sql      care-team assignments, 2nd clinician, released test notes
db/07_sla.sql         SLA alerts table, escalation-role logins, S2 clock fix
db/08_signup.sql      staff sign-up requests, one login per patient
db/09_oncology_notice.sql  BI-RADS 4/5 heads-up to the surgical and oncology team (Tumor Board)
db/10_results_disclosure.sql  results appointment; biopsy result shown to the patient only after a doctor discusses it
db/11_intake_v2.sql   intake v2 (screen2care proforma): new fields, form snapshots, risk tier, plan due dates
app/main.py           FastAPI routes
app/access.py         which patients each role may see
app/care_loop.py      care-loop steps, SLA clocks and the breach checker
app/llm.py            local AI: patient-note drafts, call scripts, Care Assistant agent
app/accounts.py       sign-up and admin approval
app/templates/base.html  shared layout (sidebar per role); _ui.html icons
app/static/app.css    design system (light + dark); intake.css = form pages only
app/patient_portal.py data for the patient's own page
app/schemas.py        form validation (Pydantic)
app/intake.py         saves the form in one transaction
app/database.py       DB connection (reads .env)
app/templates/        HTML (Jinja2)
app/static/           CSS
```

`care_episode`, `sla_task` and `lane_assignment` are empty on purpose: the rules engine (next step) fills them.

## Test accounts and who sees what

Create them with `python -m app.seed_users` (password `Dev@12345`, development only).
To add `db/06_access.sql` to an existing database without a reset:
`docker exec -i screening_db psql -U loop -d screening_care_loop < db/06_access.sql`

| Login | Sees |
|---|---|
| admin1 | all patients, no clinical results |
| cmo1 | all patients, plus every open SLA breach (S5 red flags highlighted) |
| scheduling1 | patients waiting for a biopsy booking (S2), plus S2 breach alerts |
| labdirector1 | biopsies waiting for a pathology report (S3), plus S3 breach alerts |
| tumorboard1 | new BI-RADS 4/5 workups (heads-up as soon as the loop opens), malignant results waiting for a referral (S4), plus S4 breach alerts |
| clinician1 / clinician2 | only patients assigned to them in `care_team_assignment` (HP + HR / DL) |
| radiology1 | patients with imaging they reported |
| navigator1 | patients with an open or paused care episode, plus S1 breach alerts |
| pathology1 | patients with a biopsy on record |
| patient1 ... patient15 | their own page only (BC-HP-001 ... BC-HR-015) |

The same rule applies when opening a record directly: a patient not on your list returns 403.
Patients see their status and plan only after a clinician releases a note for that visit.

## SLA care loop

| SLA | Clock starts | Stops when | Target | Breach alert goes to | Who does the step |
|---|---|---|---|---|---|
| S1 | BI-RADS 4/5 result released | patient acknowledges | 24 h | Navigator (backup phone call) | patient (button on their page), Navigator/Clinician log a call |
| S2 | patient acknowledges | biopsy booked | 7 days | Scheduling Manager | Navigator, Scheduling Manager |
| S3 | biopsy performed | pathology report | 5 days | Lab Director | Radiologist marks done, Pathologist reports |
| S4 | malignant result | referral assigned | 5 days | Tumor Board | Tumor Board, Navigator, Clinician |
| S5 | malignant result | treatment started | 21 days | CMO (red flag) | Clinician; closes the loop |

- The rules engine opens a loop for BI-RADS 4/5 results; intake now runs the engine automatically.
- The server checks for breaches every 60 s (`SLA_CHECK_SECONDS`) and on every dashboard load.
- Each staff member works the loop at `/loop/<patient_id>` (click a patient on the dashboard).
- SMS and phone calls are recorded in `patient_contact` but not actually sent yet.
- Seeded patients start without a loop. Run the engine on them once:
  `python -m app.care_loop` (only touches patients the engine has never evaluated).

## Sign-up

- `/signup`, **patients**: enter patient ID + date of birth + national ID. If they match the hospital
  record, the account is active at once (one account per patient).
- `/signup`, **staff**: the request waits on **Account requests** (admin menu). Approving creates the
  staff record and login. New clinicians see no patients until they are added to `care_team_assignment`.
- Apply to an existing database: `docker exec -i screening_db psql -U loop -d screening_care_loop < db/08_signup.sql`

## Local AI (free, private)

Install [Ollama](https://ollama.com), then `ollama pull llama3.2`. Nothing else to configure.
The AI only drafts or answers; a person always reviews before anything is saved or shown to a patient.

| Feature | Where | Who |
|---|---|---|
| Patient-friendly note: AI drafts it from the saved clinical note, clinician edits, then **Release to patient** | Clinical plan page | Clinician |
| Call script for an unanswered result (never mentions the result) | Care-loop page, S1 step | Navigator, Clinician |
| **Care Assistant** agent: answers "which of my patients are overdue?", "what alerts do I have?", "status of BC-HP-001?" using read-only tools limited to the user's own patient list | Sidebar → Care Assistant | All clinical staff |

Every AI draft is logged in `event_log` (`AIDraftCreated`). On this laptop (CPU only) a draft takes
about 15–30 s and an assistant answer 10–40 s. A larger model is more accurate but slower:
`ollama pull qwen2.5:7b` and set `OLLAMA_MODEL=qwen2.5:7b`. If Ollama is not running the rest of
the app works normally and the AI buttons say so.
