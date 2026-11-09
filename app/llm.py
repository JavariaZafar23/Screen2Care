# Local LLM features (Ollama: free, runs on this machine, patient data never leaves it).
#
# Everything here only DRAFTS or ANSWERS. Nothing the model writes is saved or shown to a
# patient until a staff member reads it and acts. The rules engine and SLA clocks never
# depend on the model.
import json
import os
import re

import httpx
from fastapi import HTTPException

from app import care_loop
from app.access import ensure_patient_access, fetch_patients

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "240"))
AGENT_MAX_STEPS = 5

ASSISTANT_ROLES = ("Clinician", "Radiologist", "Pathologist", "Navigator", "CMO",
                   "SchedulingManager", "LabDirector", "TumorBoard")


def ollama_chat(messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2) -> dict:
    """One call to the local model. Returns the assistant message dict."""
    payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": False,
               "options": {"temperature": temperature}}
    if tools:
        payload["tools"] = tools
    try:
        res = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail=(
            "The local AI model is not reachable. Start Ollama and try again "
            f"(expected at {OLLAMA_URL})."))
    if res.status_code == 404:
        raise HTTPException(status_code=503, detail=(
            f"Model '{OLLAMA_MODEL}' is not installed. Run: ollama pull {OLLAMA_MODEL}"))
    if res.status_code >= 400:
        raise HTTPException(status_code=502, detail="The local AI model returned an error.")
    return res.json().get("message", {})


_PREAMBLE = re.compile(r"^\s*(here(?:'s| is)[^\n]*?:|sure[^\n]*?:)\s*\n+", re.IGNORECASE)


def _clean(text: str) -> str:
    """Remove chatty lead-ins like 'Here is a rewritten version:'."""
    return _PREAMBLE.sub("", (text or "").strip()).strip().strip('"').strip()


# ---------- 1. Patient-friendly note -------------------------------------------------

NOTE_SYSTEM = (
    "You rewrite breast-screening clinical notes for the patient to read.\n"
    "Rules:\n"
    "- Plain, calm, kind language a 12-year-old can follow. Short sentences.\n"
    "- Keep every fact and every next step exactly as written. Do not change who does what or when.\n"
    "- Do not add any fact, diagnosis, number, date, reassurance or risk that is not in the note.\n"
    "- Explain medical words in brackets the first time, e.g. biopsy (taking a tiny sample of tissue).\n"
    "- Speak to the patient as 'you'. No names, no ID numbers, no headings.\n"
    "- Reply with the rewritten note only, 3 to 6 sentences."
)


def draft_patient_note(source_text: str) -> str:
    msg = ollama_chat([{"role": "system", "content": NOTE_SYSTEM},
                       {"role": "user", "content": source_text}])
    return _clean(msg.get("content", ""))


# ---------- 2. Navigator call script ---------------------------------------------------

CALL_SYSTEM = (
    "You write short phone-call scripts for a breast-screening patient navigator.\n"
    "The navigator must reach the patient about a result that needs follow-up.\n"
    "Rules:\n"
    "- Do NOT state or hint at the result, BI-RADS category or diagnosis. That is discussed only "
    "after identity is confirmed, by the care team.\n"
    "- Steps: greet and introduce yourself; confirm identity with date of birth; say the care team "
    "needs to talk about the recent screening and arrange a follow-up appointment; offer times; "
    "check the best number; close kindly.\n"
    "- Include a separate 2-sentence voicemail that gives no medical details.\n"
    "- Use the first name given. Plain words. Under 180 words in total."
)


def draft_call_script(first_name: str, hours_overdue: int | None) -> str:
    facts = f"Patient first name: {first_name}."
    if hours_overdue and hours_overdue > 0:
        facts += f" The patient has not replied to our text message for {hours_overdue} hours."
    msg = ollama_chat([{"role": "system", "content": CALL_SYSTEM}, {"role": "user", "content": facts}],
                      temperature=0.4)
    return _clean(msg.get("content", ""))


# ---------- 3. Care Assistant agent (read-only tools) ---------------------------------

AGENT_SYSTEM = (
    "You are the Care Assistant inside a breast-screening care-loop system. You help hospital "
    "staff find information about THEIR patients and SLA deadlines.\n"
    "- Use the tools to look things up. Never invent patients, numbers, dates or statuses.\n"
    "- Answer only from tool results. If the tools do not give the answer, say you could not find it.\n"
    "- If a tool says access is denied, reply exactly: 'That patient is not on your list.' Do not suggest pages for them.\n"
    "- Tool lists are already sorted (most urgent first). Keep that order and copy times exactly as given.\n"
    "- For questions about which patients are overdue or due soon, use list_my_patients.\n"
    "- You cannot change anything; you only read. Tell the user which page to use to act "
    "(the care-loop page for a patient is /loop/<patient_id>).\n"
    "- SLA sections: S1 notify patient (24 h), S2 book biopsy (7 days), S3 pathology report (5 days), "
    "S4 assign oncology referral (5 days), S5 start treatment (21 days).\n"
    "- Be brief: short sentences or a short list. Give patient IDs."
)

AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "list_my_patients",
        "description": "List the patients on the current user's worklist, most urgent first, with BI-RADS, lane and each patient's next SLA deadline (overdue or time left). Use this for 'which patients are overdue / due soon'.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "list_open_alerts",
        "description": "List open SLA breach alerts escalated to the current user's role, most overdue first, with the required action. Use this for 'what alerts do I have'.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_care_loop",
        "description": "Get one patient's care-loop status: each SLA section, contacts, biopsy, pathology, referral, treatment.",
        "parameters": {"type": "object", "properties": {
            "patient_id": {"type": "string", "description": "Patient ID such as BC-HP-001"}},
            "required": ["patient_id"]}}},
]


def _span(hours: int | None) -> str | None:
    """Time to a deadline as plain words, so the model never has to do arithmetic."""
    if hours is None:
        return None
    h = abs(int(hours))
    amount = f"{h // 24} days" if h >= 48 else f"{h} hours"
    return f"OVERDUE by {amount}" if hours < 0 else f"{amount} left"


def _when(v):
    return v.strftime("%d %b %Y %H:%M") if hasattr(v, "strftime") else v


def _tool_list_my_patients(user: dict) -> dict:
    rows = fetch_patients(user)
    out = []
    for p in rows:
        item = {"patient_id": p["patient_id"], "name": p["full_name"], "age": p["age"]}
        if "birads_category" in p:
            item.update(birads=p["birads_category"], lane=p.get("current_lane"))
        if p.get("sla_section"):
            item["next_deadline"] = f"S{p['sla_section']}: {_span(p['sla_hours_left'])}"
            item["_sort"] = p["sla_hours_left"]
        out.append(item)
    out.sort(key=lambda x: x.get("_sort", 10**9))   # most urgent first
    for x in out:
        x.pop("_sort", None)
    overdue = sum(1 for x in out if "OVERDUE" in x.get("next_deadline", ""))
    return {"count": len(out), "overdue_count": overdue, "sorted_by": "most urgent first", "patients": out}


def _tool_list_open_alerts(user: dict) -> dict:
    alerts = sorted(care_loop.open_alerts(user["role"]), key=lambda a: -a["hours_overdue"])
    return {"count": len(alerts), "sorted_by": "most overdue first", "alerts": [
        {"rank": i, "patient_id": a["patient_id"], "name": a["full_name"], "section": f"S{a['section']}",
         "what": a["section_name"], "overdue_by": _span(-a["hours_overdue"]).replace("OVERDUE by ", ""),
         "routed_to": a["routed_to_role"], "action": a["action_text"]}
        for i, a in enumerate(alerts[:25], start=1)]}


def _tool_get_care_loop(user: dict, patient_id: str) -> dict:
    patient_id = (patient_id or "").strip().upper()
    try:
        ensure_patient_access(user, patient_id)
        loop = care_loop.get_loop(patient_id)
    except HTTPException as e:
        return {"error": "access denied: not on your list" if e.status_code == 403 else e.detail}
    ep = loop["episode"]
    if ep is None:
        return {"patient_id": patient_id, "care_loop": "none (no BI-RADS 4/5 result has opened a loop)"}
    return {
        "patient_id": patient_id, "name": loop["patient"]["full_name"],
        "loop_status": ep["status"], "opened": _when(ep["opened_at"]), "trigger_birads": ep["birads_category"],
        "sla": [{"section": f"S{s['def']['section']}", "name": s["def"]["name"], "status": s["status"],
                 "due": _when(s["task"]["due_at"]) if s["task"] else None,
                 "time": _span(s["hours_left"]) if s["status"] in ("Running", "Breached") else None}
                for s in loop["sections"]],
        "biopsy": dict(type=loop["biopsy"]["biopsy_type"], status=loop["biopsy"]["status"],
                       scheduled_for=_when(loop["biopsy"]["scheduled_for"])) if loop["biopsy"] else None,
        "pathology": loop["pathology"]["diagnosis"] if loop["pathology"] else None,
        "referral": dict(status=loop["referral"]["status"], assignee=loop["referral"]["assignee"])
        if loop["referral"] else None,
        "treatment": loop["treatment"]["treatment_type"] if loop["treatment"] else None,
    }


def _run_tool(user: dict, name: str, args: dict) -> dict:
    if name == "list_my_patients":
        return _tool_list_my_patients(user)
    if name == "list_open_alerts":
        return _tool_list_open_alerts(user)
    if name == "get_care_loop":
        return _tool_get_care_loop(user, args.get("patient_id", ""))
    return {"error": f"unknown tool {name}"}


def run_assistant(user: dict, question: str, history: list[dict] | None = None) -> dict:
    """Small agent loop: the model may call read-only tools (scoped to this user) before answering."""
    messages = [{"role": "system", "content": AGENT_SYSTEM}]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and isinstance(turn.get("content"), str):
            messages.append({"role": turn["role"], "content": turn["content"][:2000]})
    messages.append({"role": "user", "content": question})

    used = []
    for _ in range(AGENT_MAX_STEPS):
        msg = ollama_chat(messages, tools=AGENT_TOOLS, temperature=0.1)
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"answer": _clean(msg.get("content", "")) or "I could not find an answer.", "tools_used": used}
        messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            result = _run_tool(user, name, args if isinstance(args, dict) else {})
            used.append({"tool": name, "args": args})
            messages.append({"role": "tool", "tool_name": name,
                             "content": json.dumps(result, default=str)[:6000]})
    return {"answer": "I needed too many steps to answer that. Try a more specific question.", "tools_used": used}


def status() -> dict:
    """Is the local model reachable, and is the configured model installed?"""
    try:
        tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3).json()
    except (httpx.HTTPError, ValueError):
        return {"online": False, "model": OLLAMA_MODEL, "installed": False}
    names = {m.get("name", "").split(":")[0] for m in tags.get("models", [])} | \
            {m.get("name", "") for m in tags.get("models", [])}
    return {"online": True, "model": OLLAMA_MODEL, "installed": OLLAMA_MODEL in names}
