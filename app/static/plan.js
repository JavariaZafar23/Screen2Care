const form = document.getElementById("plan");
const pid = form.dataset.pid;
const suggested = form.dataset.suggested;
const chosen = () => form.querySelector('input[name="disposition"]:checked')?.value || "";
const overrideWrap = document.getElementById("override_wrap");
const overrideInput = document.getElementById("override_reason");

function refresh() {
  const d = chosen();
  document.getElementById("fu_wrap").hidden = d !== "ShortTermFollowUp";
  const differs = d !== "" && d !== suggested;
  overrideWrap.hidden = !(differs && suggested !== "");
  overrideInput.required = !overrideWrap.hidden;
  const note = document.getElementById("differs_note");
  if (!d) note.textContent = "";
  else if (!suggested) note.textContent = "The engine has no suggestion yet. Your choice will be recorded as the current plan.";
  else if (differs) note.textContent = "This differs from the engine suggestion. Give a reason below.";
  else note.textContent = "Matches the engine suggestion.";
}
form.addEventListener("change", refresh);
refresh();

function show(messages) {
  const box = document.getElementById("form_error");
  if (!messages.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.innerHTML = "<ul>" + messages.map((m) => `<li>${m}</li>`).join("") + "</ul>";
  box.hidden = false;
}

function serverMessages(body) {
  if (typeof body?.detail === "string") return [body.detail];
  if (Array.isArray(body?.detail)) return body.detail.map((d) => (d.msg || "").replace(/^Value error, /, ""));
  return ["The plan could not be saved. Check the form and try again."];
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(form);
  const payload = {
    encounter_id: Number(fd.get("encounter_id")),
    disposition: chosen() || null,
    followup_months: fd.get("followup_months") ? Number(fd.get("followup_months")) : null,
    override_reason: (fd.get("override_reason") || "").trim() || null,
    clinical_note: (fd.get("clinical_note") || "").trim(),
    examiner_name: (fd.get("examiner_name") || "").trim(),
    signed_date: fd.get("signed_date") || null,
  };
  const errs = [];
  if (!payload.disposition) errs.push("Choose a disposition.");
  if (payload.disposition === "ShortTermFollowUp" && !payload.followup_months) errs.push("Enter the follow-up interval in months.");
  if (!overrideWrap.hidden && !payload.override_reason) errs.push("Give a reason for the override.");
  if (payload.clinical_note.length < 3) errs.push("Write the clinical note.");
  if (!payload.examiner_name) errs.push("Enter the examiner name.");
  if (!payload.signed_date) errs.push("Enter the date signed.");
  show(errs);
  if (errs.length) return;

  const btn = document.getElementById("save_btn");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const res = await fetch(`/api/patients/${pid}/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { show(serverMessages(body)); return; }
    document.getElementById("saved_msg").textContent = body.overridden
      ? `Saved. The clinician's plan overrides the engine. Current lane: ${body.lane}. Note version ${body.note_version}.`
      : `Saved. The plan matches the engine. Current lane: ${body.lane}. Note version ${body.note_version}.`;
    document.getElementById("saved_dialog").showModal();
  } catch {
    show(["The server could not be reached. Check that the app is running."]);
  } finally {
    btn.disabled = false;
    btn.textContent = "Save plan";
  }
});

document.getElementById("saved_dialog").addEventListener("close", () => location.reload());
