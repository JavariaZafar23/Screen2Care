// Breast screening intake form: conditional fields, live checks, submit
const form = document.getElementById("intake");
const $ = (id) => document.getElementById(id);
const radio = (name) => form.querySelector(`input[name="${name}"]:checked`)?.value ?? null;
const checked = (name) => [...form.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => i.value);
const num = (id) => ($(id).value === "" ? null : Number($(id).value));
const str = (id) => $(id).value.trim() || null;
const yesNo = (name) => (radio(name) === null ? null : radio(name) === "yes");

const todayIso = () => new Date().toISOString().slice(0, 10);
$("evaluation_date").value = todayIso();

// ---------- Derived values ----------
function ageAt(dobIso, onIso) {
  if (!dobIso || !onIso) return null;
  const d = new Date(dobIso), e = new Date(onIso);
  let a = e.getFullYear() - d.getFullYear();
  if (e.getMonth() < d.getMonth() || (e.getMonth() === d.getMonth() && e.getDate() < d.getDate())) a--;
  return a >= 0 && a < 130 ? a : null;
}
const currentAge = () => ageAt($("date_of_birth").value, $("evaluation_date").value);

function bmi() {
  const h = num("height_cm"), w = num("weight_kg");
  return h && w ? Math.round((w / (h / 100) ** 2) * 10) / 10 : null;
}

// ---------- Show / hide dependent fields ----------
function updateVisibility() {
  const age = currentAge();
  $("age_out").textContent = age ?? "—";
  $("bmi_out").textContent = bmi() ?? "—";

  $("symptoms").hidden = radio("presentation") !== "symptomatic";
  $("menopause_age_wrap").hidden = radio("menopausal_status") !== "Postmenopausal";
  $("hrt_duration_wrap").hidden = radio("hrt") !== "yes";
  $("ovarian_rel_wrap").hidden = radio("ovarian") !== "yes";
  $("geriatric").hidden = !(age !== null && age >= 75);

  const prior = checked("prior");
  $("prior_details_wrap").hidden = prior.length === 0;

  form.querySelectorAll(".relative").forEach((row) => {
    row.querySelector(".age").hidden = !row.querySelector("input[type=checkbox]").checked;
  });

  for (const s of ["R", "L"]) {
    $(`cbe_${s}_insp_text`).hidden = radio(`cbe_${s}_insp`) !== "abnormal";
    const mass = radio(`cbe_${s}_mass`) === "yes";
    $(`cbe_${s}_details`).hidden = !mass;
    $(`cbe_${s}_nodetail`).hidden = mass;
  }
  $("fu_wrap").hidden = radio("disposition") !== "ShortTermFollowUp";
}

// "None" and the procedure boxes exclude each other
$("prior_none").addEventListener("change", (e) => {
  if (e.target.checked) form.querySelectorAll('input[name="prior"]').forEach((i) => (i.checked = false));
});
form.querySelectorAll('input[name="prior"]').forEach((i) =>
  i.addEventListener("change", () => { if (i.checked) $("prior_none").checked = false; })
);

$("clear_birads").addEventListener("click", () => {
  form.querySelectorAll('input[name="birads"]').forEach((i) => (i.checked = false));
  refresh();
});

// ---------- Live checks (guidance only; the rules engine makes the routing decision) ----------
function cbeAbnormal() {
  return ["R", "L"].some((s) =>
    radio(`cbe_${s}_insp`) === "abnormal" || radio(`cbe_${s}_mass`) === "yes" || radio(`cbe_${s}_node`) === "yes");
}

function buildChecks() {
  const out = [];
  const age = currentAge();
  const birads = radio("birads") === null ? null : Number(radio("birads"));
  const disp = radio("disposition");
  const density = radio("density");
  const genetic = radio("genetic");

  if (radio("presentation") === "asymptomatic" && cbeAbnormal())
    out.push(["warn", "The exam has abnormal findings but the visit is marked asymptomatic. If the patient has symptoms, mark it symptomatic so she follows the diagnostic pathway."]);
  if (age !== null && age >= 75)
    out.push(["info", "Age 75 or over: complete the geriatric assessment in section 4."]);
  if (genetic === "BRCA1" || genetic === "BRCA2")
    out.push(["info", `${genetic} mutation: the patient will be routed to the high-risk screening lane.`]);
  if (radio("ovarian") === "yes" && !$("ovarian_relation").value)
    out.push(["warn", "Select which relative had ovarian cancer."]);
  if (birads !== null && checked("modality").length === 0)
    out.push(["crit", "Select the imaging modality used for this BI-RADS result."]);
  if (birads === 0)
    out.push(["info", "BI-RADS 0: the patient needs recall for additional imaging before a final category."]);
  if ((birads === 1 || birads === 2) && (density === "C" || density === "D"))
    out.push(["info", `Density ${density}: tell the patient about dense tissue and discuss supplemental imaging.`]);
  if ((birads === 4 || birads === 5) && disp !== "UrgentBiopsyReferral")
    out.push(["crit", `BI-RADS ${birads} usually needs an urgent biopsy referral. Check the plan in section 7.`]);
  if (birads === 3 && disp === "RoutineRescreen")
    out.push(["warn", "BI-RADS 3 needs short-interval follow-up, not routine rescreening."]);
  if ((birads === 1 || birads === 2) && disp === "UrgentBiopsyReferral")
    out.push(["warn", `Urgent biopsy chosen for BI-RADS ${birads}. Confirm this is intended.`]);
  return out;
}

function renderChecks() {
  const list = $("checks_list");
  const items = buildChecks();
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<li class="empty">No issues found.</li>';
    return;
  }
  for (const [level, text] of items) {
    const li = document.createElement("li");
    li.className = level;
    li.textContent = text;
    list.appendChild(li);
  }
}

function refresh() { updateVisibility(); renderChecks(); }
form.addEventListener("input", refresh);
form.addEventListener("change", refresh);
refresh();

// ---------- Build the payload ----------
function cbeSide(s) {
  const mass = radio(`cbe_${s}_mass`) === "yes";
  return {
    inspection_normal: radio(`cbe_${s}_insp`) !== "abnormal",
    inspection_finding: str(`cbe_${s}_insp_text`),
    mass_present: mass,
    mass_size_mm: mass ? num(`cbe_${s}_size`) : null,
    mass_mobility: mass ? radio(`cbe_${s}_mob`) : null,
    mass_consistency: mass ? ($(`cbe_${s}_cons`).value || null) : null,
    axillary_node_palpable: radio(`cbe_${s}_node`) === "yes",
  };
}

function buildPayload() {
  const relatives = [...form.querySelectorAll(".relative")]
    .filter((r) => r.querySelector("input[type=checkbox]").checked)
    .map((r) => {
      const ageVal = r.querySelector(".age input").value;
      return { relation: r.querySelector("input[type=checkbox]").value, age_at_diagnosis: ageVal ? Number(ageVal) : null };
    });
  const birads = radio("birads");
  return {
    full_name: str("full_name"),
    date_of_birth: $("date_of_birth").value || null,
    national_id: str("national_id"),
    evaluation_date: $("evaluation_date").value || null,

    is_symptomatic: radio("presentation") === "symptomatic",
    lump_side: radio("lump_side") || null,
    lump_duration: str("lump_duration"),
    nipple_discharge_side: radio("nd_side") || null,
    nipple_discharge_color: radio("nd_side") ? radio("nd_color") : null,
    skin_changes: checked("skin"),
    pain_type: radio("pain_type") || null,
    pain_severity: radio("pain_type") ? num("pain_severity") : null,

    menarche_age: num("menarche_age"),
    menopausal_status: radio("menopausal_status"),
    menopause_age: num("menopause_age"),
    parity: num("parity"),
    age_first_birth: num("age_first_birth"),
    ocp_use: radio("ocp"),
    hrt_use: yesNo("hrt"),
    hrt_duration_years: num("hrt_duration_years"),

    prior_breast_history: checked("prior"),
    prior_breast_details: str("prior_breast_details"),
    breast_cancer_relatives: relatives,
    ovarian_cancer: radio("ovarian") === "yes",
    ovarian_relation: $("ovarian_relation").value || null,
    genetic_result: radio("genetic") || "Unknown",
    height_cm: num("height_cm"),
    weight_kg: num("weight_kg"),
    geriatric_end_stage: yesNo("ger_end"),
    geriatric_independent_adl: yesNo("ger_adl"),

    cbe_right: cbeSide("R"),
    cbe_left: cbeSide("L"),

    modalities: checked("modality"),
    density: radio("density"),
    birads: birads === null ? null : Number(birads),

    disposition: radio("disposition"),
    followup_months: num("followup_months"),
    examiner_name: str("examiner_name"),
    signed_date: $("signed_date").value || null,
  };
}

// ---------- Validate, submit, show result ----------
function showErrors(messages) {
  const box = $("form_error");
  if (!messages.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.innerHTML = "<ul>" + messages.map((m) => `<li>${m}</li>`).join("") + "</ul>";
  box.hidden = false;
}

function clientErrors(p) {
  const errs = [];
  form.querySelectorAll(".invalid").forEach((el) => el.classList.remove("invalid"));
  const need = [["full_name", "Enter the patient’s full name."],
                ["date_of_birth", "Enter the date of birth."],
                ["evaluation_date", "Enter the date of evaluation."]];
  for (const [id, msg] of need) if (!$(id).value.trim()) { errs.push(msg); $(id).classList.add("invalid"); }
  const age = currentAge();
  if (age !== null && age >= 75 && (p.geriatric_end_stage === null || p.geriatric_independent_adl === null))
    errs.push("Answer both geriatric assessment questions (age 75 or over).");
  if (p.birads !== null && !p.modalities.length) errs.push("Select the imaging modality for the BI-RADS result.");
  if (p.disposition === "ShortTermFollowUp" && !p.followup_months) {
    errs.push("Enter the follow-up interval in months."); $("followup_months").classList.add("invalid");
  }
  return errs;
}

function serverErrors(body) {
  if (Array.isArray(body?.detail))
    return body.detail.map((d) => {
      const field = d.loc?.filter((x) => x !== "body").join(" › ");
      const msg = (d.msg || "").replace(/^Value error, /, "");
      return field ? `${field.replaceAll("_", " ")}: ${msg}` : msg;
    });
  return [body?.detail || "The intake could not be saved. Check the form and try again."];
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = buildPayload();
  const errs = clientErrors(payload);
  showErrors(errs);
  if (errs.length) return;

  const btn = $("save_btn");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const res = await fetch("/api/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { showErrors(serverErrors(body)); return; }
    $("saved_text").textContent = body.returning_patient
      ? `New visit added to existing patient ${body.patient_id}.`
      : `New patient created with ID ${body.patient_id}.`;
    $("saved_link").href = `/api/patients/${body.patient_id}`;
    $("saved_dialog").showModal();
  } catch {
    showErrors(["The server could not be reached. Check that the app is running, then save again."]);
  } finally {
    btn.disabled = false;
    btn.textContent = "Save intake";
  }
});

$("new_intake").addEventListener("click", () => {
  $("saved_dialog").close();
  form.reset();
  $("evaluation_date").value = todayIso();
  showErrors([]);
  refresh();
  window.scrollTo(0, 0);
});

// Highlight the section in view
const tocLinks = [...document.querySelectorAll(".toc a")];
const observer = new IntersectionObserver((entries) => {
  entries.forEach((en) => {
    if (en.isIntersecting)
      tocLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${en.target.id}`));
  });
}, { rootMargin: "-20% 0px -70% 0px" });
document.querySelectorAll(".block").forEach((s) => observer.observe(s));
