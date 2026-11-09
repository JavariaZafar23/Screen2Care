// Breast screening clinical proforma (intake v2), filled in by the clinician for a
// patient registered at the front desk: conditional fields, repeat rows, live checks,
// patient selection and submit. Field rules follow app/schemas.py.
const form = document.getElementById("intake");
const $ = (id) => document.getElementById(id);
const val = (id) => $(id)?.value.trim() || null;
const num = (id) => (val(id) === null ? null : Number(val(id)));
const radio = (name) => form.querySelector(`input[name="${name}"]:checked`)?.value ?? null;
const show = (key, on) => form.querySelectorAll(`[data-show="${key}"]`).forEach((el) => {
  el.hidden = !on;
  if (!on) el.querySelectorAll("input, select").forEach((c) => { c.value = ""; });
});

const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
let idempotencyKey = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();

// ---------- Derived values ----------
function age() {
  const dob = val("date_of_birth"), on = val("evaluation_date");
  if (!dob || !on) return null;
  const d = new Date(dob), e = new Date(on);
  let a = e.getFullYear() - d.getFullYear();
  if (e.getMonth() < d.getMonth() || (e.getMonth() === d.getMonth() && e.getDate() < d.getDate())) a--;
  return a >= 0 && a < 130 ? a : null;
}
function bmi() {
  const h = num("height_cm"), w = num("weight_kg");
  return h && w ? (w / (h / 100) ** 2).toFixed(1) : null;
}

// ---------- Show / hide dependent fields ----------
function updateVisibility() {
  const a = age();
  $("age_out").textContent = a ?? "—";
  $("bmi_out").textContent = bmi() ?? "—";

  const symptomatic = radio("presentation") === "true";
  $("symptoms").hidden = !symptomatic;
  if (!symptomatic) ["lump_side", "nipple_discharge_side", "pain_type"].forEach((id) => { $(id).value = ""; });
  show("lump", symptomatic && !!val("lump_side"));
  show("discharge", symptomatic && !!val("nipple_discharge_side"));
  show("pain", symptomatic && !!val("pain_type"));

  const married = val("marital_status") === "Married";
  show("married", married);
  $("breastfeeding_history").required = married;
  show("breastfed-years", married && val("breastfeeding_history") === "Yes");
  show("menopause-status", a !== null && a >= 40);
  show("menopause-age", a !== null && a >= 40 && val("menopausal_status") === "Postmenopausal");
  const hrtAge = a !== null && a >= 45 && a <= 55;
  show("hrt", hrtAge);
  show("hrt-years", hrtAge && val("hrt_use") === "Yes");

  show("radiation", val("chest_radiation") === "Yes");
  const geneticStatus = val("genetic_status");
  const geneticResult = ["Positive", "Negative"].includes(geneticStatus);
  show("gene", geneticResult);
  $("genetic_gene").required = geneticStatus === "Positive";
  show("gene-other", geneticStatus === "Positive" && val("genetic_gene") === "Other");
  $("genetic_gene_other").required = geneticStatus === "Positive" && val("genetic_gene") === "Other";

  const geriatric = a !== null && a >= 75;
  $("geriatric").hidden = !geriatric;
  ["geriatric_end_stage_failure", "geriatric_adl_independent"].forEach((id) => {
    $(id).required = geriatric;
    if (!geriatric) $(id).value = "";
  });

  const mh = $("medical_history_yes").checked;
  $("medical_history_details_field").hidden = !mh;
  $("medical_history_details").required = mh;
  if (!mh) $("medical_history_details").value = "";

  const noFamily = $("family_history_none").checked;
  $("family_entries").hidden = noFamily;
  $("add_family").hidden = noFamily;
  const noImaging = $("imaging_not_done").checked;
  $("imaging_entries").hidden = noImaging;
  $("add_imaging").hidden = noImaging;

  for (const s of ["r", "l"]) {
    const insp = val(`${s}_inspection`) === "Abnormal";
    $(`${s}_inspection_more`).hidden = !insp;
    $(`${s}_inspection_finding`).required = insp;
    if (!insp) [`${s}_inspection_finding`, `${s}_nipple_discharge_color`, `${s}_skin_change`].forEach((id) => { $(id).value = ""; });
    const mass = val(`${s}_mass`) === "Abnormal";
    $(`${s}_mass_more`).hidden = !mass;
    $(`${s}_mass_size`).required = mass;
    if (!mass) [`${s}_mass_size`, `${s}_mass_mobility`, `${s}_mass_consistency`].forEach((id) => { $(id).value = ""; });
  }
  $("followup_wrap").hidden = !planChosen().includes("Short-Term Diagnostic Follow-up (Repeat Imaging at strict 6-Month mark)");
}

// ---------- Repeat rows ----------
function removable(row, list) {
  row.querySelector(".remove").addEventListener("click", () => { row.remove(); refresh(); });
  $(list).appendChild(row);
  row.querySelector("input, select")?.focus();
  return row;
}

function addBreastEntry(data = {}) {
  const row = document.createElement("div");
  row.className = "repeat-row";
  row.innerHTML = `
    <label class="field compact-field">Type <select data-field="type" required>
      <option value="">Select…</option><option>None</option><option>Biopsy</option><option>Lumpectomy</option>
      <option>Cyst Aspiration</option><option>Implants</option></select></label>
    <label class="field grow" data-details>Details / pathology findings
      <input data-field="details" type="text" maxlength="200" placeholder="e.g. benign fibroadenoma, left, 2015"></label>
    <button type="button" class="remove" aria-label="Remove this entry">Remove</button>`;
  const type = row.querySelector('[data-field="type"]');
  const sync = () => {
    const has = type.value && type.value !== "None";
    row.querySelector("[data-details]").hidden = !has;
    if (!has) row.querySelector('[data-field="details"]').value = "";
  };
  type.addEventListener("change", sync);
  type.value = data.type || "";
  row.querySelector('[data-field="details"]').value = data.details || "";
  sync();
  return removable(row, "breast_history_entries");
}

function addFamily() {
  const row = document.createElement("div");
  row.className = "repeat-row";
  row.innerHTML = `
    <label class="field">Blood relative <select data-field="relation_degree" data-int required>
      <option value="">Select…</option><option value="1">First degree</option><option value="2">Second degree</option></select></label>
    <label class="field">Relation <input data-field="relation" type="text" required maxlength="30" placeholder="e.g. Mother, sister, aunt"></label>
    <label class="field">Cancer type <select data-field="cancer_type" required>
      <option value="">Select…</option><option>Breast</option><option>Ovarian</option><option>Pancreatic</option><option>Prostate</option><option>Other</option></select></label>
    <label class="field narrow-field">Age at diagnosis <input data-field="age_at_diagnosis" data-int type="number" min="1" max="120" required></label>
    <label class="field narrow-field">Known genetic mutation <select data-field="known_genetic_mutations" required>
      <option value="">Select…</option><option>BRCA1</option><option>BRCA2</option><option>Negative</option><option>Unknown</option></select></label>
    <button type="button" class="remove" aria-label="Remove this relative">Remove</button>`;
  row.querySelectorAll('[data-field="cancer_type"] option').forEach((option) => {
    if (["Pancreatic", "Prostate", "Other"].includes(option.textContent)) option.remove();
  });
  return removable(row, "family_entries");
}

function addImaging() {
  const row = document.createElement("div");
  row.className = "repeat-row";
  row.innerHTML = `
    <label class="field">Scan date <input data-field="date" type="date" required></label>
    <label class="field">Modality <select data-field="modality" required>
      <option value="Mammogram">Mammogram</option><option value="DBT">DBT (3D mammogram)</option>
      <option value="Ultrasound">Ultrasound</option><option value="MRI">MRI</option></select></label>
    <label class="field" data-density>Density <select data-field="density">
      <option value="">Not recorded</option><option value="A">A (fatty)</option><option value="B">B (scattered)</option>
      <option value="C">C (heterogeneously dense)</option><option value="D">D (extremely dense)</option></select></label>
    <label class="field">BI-RADS <select data-field="birads" data-int required>
      <option value="">Select…</option><option value="0">0 Incomplete</option><option value="1">1 Negative</option>
      <option value="2">2 Benign</option><option value="3">3 Probably benign</option><option value="4">4 Suspicious</option>
      <option value="5">5 Highly suggestive</option><option value="6">6 Known cancer</option></select></label>
    <button type="button" class="remove" aria-label="Remove this scan">Remove</button>`;
  row.querySelector('[data-field="birads"] option[value="0"]')?.remove();
  row.querySelector('[data-field="birads"] option[value="6"]')?.remove();
  row.querySelector('[data-field="birads"]')?.closest(".field")?.classList.add("birads-field");
  const modality = row.querySelector('[data-field="modality"]');
  const sync = () => {
    const none = ["MRI", "Ultrasound"].includes(modality.value);
    row.querySelector("[data-density]").hidden = none;
    if (none) row.querySelector('[data-field="density"]').value = "";
  };
  modality.addEventListener("change", sync);
  row.querySelector('[data-field="date"]').value = val("evaluation_date") || todayIso();
  sync();
  return removable(row, "imaging_entries");
}

function rows(list) {
  return [...$(list).children].map((row) => Object.fromEntries(
    [...row.querySelectorAll("[data-field]")]
      .filter((el) => !el.closest("[hidden]"))
      .map((el) => [el.dataset.field, el.value === "" ? null : (el.hasAttribute("data-int") ? Number(el.value) : el.value)])));
}

$("add_breast_entry").addEventListener("click", () => { addBreastEntry(); refresh(); });
$("add_family").addEventListener("click", () => { addFamily(); refresh(); });
$("add_imaging").addEventListener("click", () => { addImaging(); refresh(); });
$("family_history_none").addEventListener("change", (e) => { if (e.target.checked) $("family_entries").replaceChildren(); });
$("imaging_not_done").addEventListener("change", (e) => { if (e.target.checked) $("imaging_entries").replaceChildren(); });

// ---------- Payload ----------
const planChosen = () => [...form.querySelectorAll('input[name="plan"]:checked')].map((i) => i.value);
const exam = (s) => ({
  inspection: val(`${s}_inspection`), inspection_finding: val(`${s}_inspection_finding`),
  nipple_discharge_color: val(`${s}_nipple_discharge_color`), skin_change: val(`${s}_skin_change`),
  mass: val(`${s}_mass`), mass_size_mm: num(`${s}_mass_size`),
  mass_mobility: val(`${s}_mass_mobility`), mass_consistency: val(`${s}_mass_consistency`),
  axillary_nodes: val(`${s}_nodes`),
});

function payload() {
  const pres = radio("presentation");
  const married = val("marital_status") === "Married";
  const family = rows("family_entries").map((r) => ({ ...r, family_side: "Unknown side" }));
  return {
    patient_id: val("patient_pick"), evaluation_date: val("evaluation_date"),
    idempotency_key: idempotencyKey,
    presentation: pres === null ? null : pres === "true",
    lump_side: val("lump_side"), lump_duration: val("lump_duration"),
    nipple_discharge_side: val("nipple_discharge_side"), nipple_discharge_color: val("nipple_discharge_color"),
    pain_type: val("pain_type"), pain_severity: num("pain_severity"),
    marital_status: val("marital_status"), menarche_age: num("menarche_age"),
    menopausal_status: val("menopausal_status"), menopause_age: num("menopause_age"),
    parity: married ? num("parity") : null, age_first_birth: married ? num("age_first_birth") : null,
    ocp_use: val("ocp_use"), hrt_use: val("hrt_use"), hrt_duration_years: num("hrt_duration_years"),
    breastfeeding_history: married ? (val("breastfeeding_history") || "Unknown") : "Unknown",
    breastfeeding_duration_years: married ? num("breastfeeding_duration_years") : null,
    breast_history: rows("breast_history_entries"), high_risk_biopsy: val("high_risk_biopsy"),
    medical_history_present: $("medical_history_yes").checked, medical_history_details: val("medical_history_details"),
    height_cm: num("height_cm"), weight_kg: num("weight_kg"),
    chest_radiation: val("chest_radiation"), chest_radiation_age: num("chest_radiation_age"),
    chest_radiation_years: num("chest_radiation_years"),
    genetic_status: val("genetic_status"), genetic_gene: val("genetic_gene"), genetic_gene_other: val("genetic_gene_other"),
    geriatric_end_stage_failure: val("geriatric_end_stage_failure"),
    geriatric_adl_independent: val("geriatric_adl_independent"),
    geriatric_life_expectancy_over_10y: null,
    family_history: family, family_history_none: $("family_history_none").checked,
    cbe_right: exam("r"), cbe_left: exam("l"),
    imaging: rows("imaging_entries"), imaging_not_done: $("imaging_not_done").checked,
    plan: planChosen(), followup_months: num("followup_months"),
    clinician_notes: val("clinician_notes"), signed_date: val("signed_date"),
  };
}

// ---------- Live checks (guidance only) ----------
function buildChecks(p) {
  const out = [];
  const a = age();
  const plan = p.plan;
  const has = (prefix) => plan.some((x) => x.startsWith(prefix));
  const latest = [...p.imaging].filter((s) => s.date && s.birads !== null).sort((x, y) => (x.date < y.date ? 1 : -1))[0];
  const anyBirads = (...v) => p.imaging.some((s) => v.includes(s.birads));
  const abnormal = [p.cbe_right, p.cbe_left].some((e) => [e.inspection, e.mass, e.axillary_nodes].includes("Abnormal"));

  if (p.presentation === false && abnormal)
    out.push(["warn", "Abnormal clinical examination recorded for an asymptomatic presentation. If the patient has symptoms, mark the visit symptomatic."]);
  if (anyBirads(4, 5, 6) && !has("Urgent Referral"))
    out.push(["crit", "BI-RADS 4–6 normally requires an urgent biopsy referral. Check the plan in section 8."]);
  if (anyBirads(3) && has("Routine Rescreening"))
    out.push(["warn", "BI-RADS 3 generally needs short-term follow-up rather than routine rescreening."]);
  if (p.imaging.some((s) => ["C", "D"].includes(s.density)) && has("Routine Rescreening"))
    out.push(["warn", "Dense breast tissue may require supplemental imaging review."]);
  if (latest?.birads === 0) out.push(["info", "Latest BI-RADS is 0: recall for additional imaging before a final category."]);
  if (latest && [1, 2].includes(latest.birads) && has("Urgent Referral"))
    out.push(["warn", `Urgent biopsy chosen while the latest BI-RADS is ${latest.birads}. Confirm this is intended.`]);
  if (p.genetic_status === "Positive" && ["BRCA1", "BRCA2", "TP53", "PTEN"].includes(p.genetic_gene))
    out.push(["info", `${p.genetic_gene} positive: the patient will be routed to the high-risk screening lane.`]);
  if (p.chest_radiation === "Yes" && p.chest_radiation_age >= 10 && p.chest_radiation_age <= 30)
    out.push(["info", "Chest radiation between ages 10 and 30: high-risk screening applies."]);
  if (["ADH", "ALH", "LCIS"].includes(p.high_risk_biopsy))
    out.push(["info", `${p.high_risk_biopsy} on a previous biopsy: high-risk screening from age 30.`]);
  if (a !== null && a >= 75) out.push(["info", "Age 75 or over: complete the geriatric assessment in section 4."]);
  if (!p.imaging_not_done && !p.imaging.length) out.push(["warn", "Add an imaging entry or tick “Imaging has not been done yet”."]);
  return out;
}

function renderChecks() {
  const list = $("checks_list");
  if (!list) return;
  const items = buildChecks(payload());
  list.replaceChildren();
  if (!items.length) { list.innerHTML = '<li class="empty">No issues found.</li>'; return; }
  for (const [level, msg] of items) {
    const li = document.createElement("li");
    li.className = level; li.textContent = msg; list.appendChild(li);
  }
}

function refresh() { updateVisibility(); renderChecks(); }
form.addEventListener("input", refresh);
form.addEventListener("change", refresh);

// ---------- Validation and submit ----------
function showErrors(messages) {
  const box = $("form_error");
  box.replaceChildren();
  if (messages.length) {
    const ul = document.createElement("ul");
    messages.forEach((m) => { const li = document.createElement("li"); li.textContent = m; ul.appendChild(li); });
    box.appendChild(ul);
  }
  box.hidden = !messages.length;
}

function labelOf(control) {
  const legend = control.closest("fieldset")?.querySelector("legend");
  if (legend && (control.type === "radio" || control.type === "checkbox")) return legend.textContent.replace(/required|select at least one/gi, "").trim();
  if (control.getAttribute("aria-label")) return control.getAttribute("aria-label");
  const label = control.closest("label");
  return label ? label.childNodes[0].textContent.trim() : control.id;
}

function clientErrors(p) {
  $("imaging_not_done").setCustomValidity(!p.imaging_not_done && !p.imaging.length
    ? "Add an imaging entry or tick 'Imaging has not been done yet'." : "");
  form.querySelector('input[name="plan"]').setCustomValidity(p.plan.length ? "" : "Select a plan option.");
  const bad = [...form.querySelectorAll("input, select, textarea")]
    .filter((c) => !c.disabled && !c.closest("[hidden]") && !c.checkValidity());
  form.querySelectorAll(".invalid").forEach((c) => c.classList.remove("invalid"));
  bad.forEach((c) => c.classList.add("invalid"));
  if (bad.length) { bad[0].scrollIntoView({ block: "center" }); bad[0].focus({ preventScroll: true }); }
  const labels = [...new Set(bad.map(labelOf))];
  return labels.length ? [`Please complete: ${labels.join(", ")}.`] : [];
}

function serverErrors(body) {
  if (Array.isArray(body?.detail))
    return body.detail.map((d) => {
      const field = (d.loc || []).filter((x) => x !== "body").join(" › ").replaceAll("_", " ");
      const msg = (d.msg || "").replace(/^Value error, /, "");
      return field ? `${field}: ${msg}` : msg;
    });
  return [body?.detail || "The intake could not be saved. Check the form and try again."];
}

function fillList(id, items, empty) {
  const ul = $(id);
  ul.replaceChildren();
  (items.length ? items : [empty]).forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
}

function showSaved(body) {
  const risk = body.risk_assessment || {};
  const loop = body.lane === "UrgentWorkup";
  $("saved_text").textContent = (body.duplicate
    ? `This form was already saved for patient ${body.patient_id}. Nothing new was added.`
    : body.returning_patient
      ? `New visit added for patient ${body.patient_id}. Age on the evaluation date: ${body.age}.`
      : `First clinical intake saved for patient ${body.patient_id}. Age on the evaluation date: ${body.age}.`)
    + (loop ? " BI-RADS 4/5: the care loop is open and the patient goes to the biopsy pathway." : "");
  $("open_loop").hidden = !loop;
  $("open_loop").href = `/loop/${encodeURIComponent(body.patient_id)}`;
  $("risk_label").textContent = risk.label || risk.tier || "Not calculated";
  $("risk_label").className = `risk-pill risk-${String(risk.tier || "incomplete").toLowerCase()}`;
  $("risk_alert").textContent = risk.alert_trigger || "";
  fillList("risk_reasons", risk.reasons || [], "No triggering factors recorded.");
  fillList("doctor_plan", (body.doctor_plan || []).map((i) =>
    i.option + (i.due_date ? ` — due ${i.due_date}` : "") + (i.recurrence ? ` (${i.recurrence})` : "")), "No plan selected.");
  $("saved_lane").textContent = body.lane || "Not evaluated";
  $("saved_dialog").showModal();
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const p = payload();
  const errs = clientErrors(p);
  showErrors(errs);
  if (errs.length) return;
  const btn = $("save_btn");
  btn.disabled = true; btn.textContent = "Saving…";
  try {
    const res = await fetch("/api/intake", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { showErrors(serverErrors(body)); return; }
    showSaved(body);
  } catch {
    showErrors(["The server could not be reached. Check that the app is running, then save again."]);
  } finally {
    btn.disabled = false; btn.textContent = "Save intake";
  }
});

function resetForm() {
  form.reset();
  ["breast_history_entries", "family_entries", "imaging_entries", "previous_intake_cards"].forEach((id) => $(id).replaceChildren());
  $("previous_intakes").hidden = true;
  $("evaluation_date").value = todayIso();
  $("signed_date").value = todayIso();
  $("patient_card").hidden = true;
  $("date_of_birth").value = "";
  idempotencyKey = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
  showErrors([]);
  refresh();
}
const PRETTY = (k) => k.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
const HIDE = new Set(["idempotency_key", "full_name", "date_of_birth", "phone", "national_id", "patient_id", "signed_date"]);
function describe(v) {
  if (Array.isArray(v)) return v.map(describe).filter(Boolean).join("; ");
  if (v && typeof v === "object") return Object.entries(v).filter(([, x]) => x !== null && x !== "" && x !== false)
    .map(([k, x]) => `${PRETTY(k)}: ${describe(x)}`).join(", ");
  return v === true ? "Yes" : String(v);
}
function renderHistory(history) {
  const cards = $("previous_intake_cards");
  cards.replaceChildren();
  history.forEach((h) => {
    const card = document.createElement("details");
    card.className = "history-card";
    const sum = document.createElement("summary");
    sum.textContent = `Visit ${h.submitted?.evaluation_date || ""} · saved ${new Date(h.created_at).toLocaleString()}` + (h.tier ? ` · risk ${h.tier}` : "");
    const dl = document.createElement("dl");
    Object.entries(h.submitted || {}).forEach(([k, v]) => {
      if (HIDE.has(k) || v === null || v === "" || v === false || (Array.isArray(v) && !v.length)) return;
      const dt = document.createElement("dt"); dt.textContent = PRETTY(k);
      const dd = document.createElement("dd"); dd.textContent = describe(v);
      dl.append(dt, dd);
    });
    card.append(sum, dl);
    cards.appendChild(card);
  });
  $("previous_intakes").hidden = !history.length;
}

async function loadPatient(id) {
  const res = await fetch(`/api/intake/patients/${encodeURIComponent(id.toUpperCase())}`);
  if (!res.ok) return res.status;
  const data = await res.json();
  resetForm();
  const pt = data.patient;
  const pick = $("patient_pick");
  if (![...pick.options].some((o) => o.value === pt.patient_id)) pick.add(new Option(`${pt.full_name} · ${pt.patient_id}`, pt.patient_id));
  pick.value = pt.patient_id;
  $("date_of_birth").value = pt.date_of_birth;
  $("pc_name").textContent = pt.full_name;
  $("pc_id").textContent = pt.patient_id;
  $("pc_dob").textContent = pt.date_of_birth;
  $("pc_phone").textContent = pt.phone || "Not recorded";
  $("patient_card").hidden = false;
  renderHistory(data.intake_history || []);
  refresh();
  return 200;
}

const lookup = $("patient_lookup");

async function findPatient() {
  const id = val("lookup_patient_id");
  const msg = $("lookup_message");
  msg.hidden = true;
  if (!id) { msg.textContent = "Enter a Patient ID."; msg.hidden = false; return; }
  const status = await loadPatient(id);
  if (status !== 200) {
    msg.textContent = status === 404 ? "No patient with that ID." : status === 403 ? "This patient is not on your list. Ask the front desk to assign them to you." : "The record could not be loaded.";
    msg.hidden = false; return;
  }
  lookup.close();
}

$("patient_pick").addEventListener("change", async (e) => {
  const id = e.target.value;
  if (!id) { resetForm(); return; }
  if (await loadPatient(id) !== 200) showErrors(["The patient record could not be loaded."]);
});

// ---------- Section highlight in the side menu ----------
const tocLinks = [...document.querySelectorAll(".toc a")];
const observer = new IntersectionObserver((entries) => entries.forEach((en) => {
  if (en.isIntersecting) tocLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${en.target.id}`));
}), { rootMargin: "-20% 0px -70% 0px" });
document.querySelectorAll(".block").forEach((s) => observer.observe(s));

// ---------- Start ----------
$("evaluation_date").value = todayIso();
$("signed_date").value = todayIso();
refresh();
const wanted = new URLSearchParams(location.search).get("patient");
if (wanted) {
  loadPatient(wanted).then((st) => { if (st !== 200) showErrors(["That patient could not be loaded. Choose one from the list."]); });
} else {
  $("cancel_lookup").addEventListener("click", () => { lookup.close(); window.location.href = "/"; });
  $("lookup_patient_id").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); findPatient(); } });
  $("find_patient").addEventListener("click", findPatient);
  lookup.showModal();
  $("lookup_patient_id").focus();
}
