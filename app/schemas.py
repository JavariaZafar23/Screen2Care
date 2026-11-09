# Breast screening clinical proforma (intake form v2).
# Field names and rules follow the screen2care proforma (branch feature/screening),
# plus three fields this app needs: national ID (patient sign-up), the high-risk
# biopsy finding and the clinician's life-expectancy judgement (rules engine).
from datetime import date
from enum import StrEnum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

Side = Literal["Right", "Left", "Both"]
YesNoUnknown = Literal["Yes", "No", "Unknown"]
ExamStatus = Literal["Not answered", "Normal", "Abnormal"]


class PlanOption(StrEnum):
    STANDARD_WELLNESS = "Standard Wellness & Awareness Tracking"
    ROUTINE_RESCREEN = "Routine Rescreening (Mammogram/DBT in 12–24 Months)"
    ANNUAL_MRI = "Annual Breast MRI Only"
    ANNUAL_DBT_MRI_6M = "High-Risk Staggered Screening (Annual Mammogram + Annual MRI alternating every 6 months)"
    GENETICS_REFERRAL = "Genetic Counseling & Testing Referral"
    SHORT_TERM_FOLLOW_UP = "Short-Term Diagnostic Follow-up (Repeat Imaging at strict 6-Month mark)"
    URGENT_BIOPSY = "Urgent Referral for Tissue Diagnosis (Core Needle Biopsy)"
    STOP_ROUTINE = "Stop Routine Screening (Switch to Symptomatic Care Plan only)"


class Relative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family_side: Literal["Mother's side", "Father's side", "Unknown side"] = "Unknown side"
    relation: str = Field(min_length=2, max_length=30)
    relation_degree: Optional[Literal[1, 2]] = None
    cancer_type: Literal["Breast", "Ovarian", "Pancreatic", "Prostate", "Other"]
    age_at_diagnosis: int = Field(ge=1, le=120)
    known_genetic_mutations: Literal["BRCA1", "BRCA2", "Negative", "Unknown"] = "Unknown"


class BreastExam(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inspection: ExamStatus
    inspection_finding: Optional[str] = Field(None, max_length=100)
    nipple_discharge_color: Optional[Literal["None", "Bloody", "Clear", "Milky"]] = None
    skin_change: Optional[Literal["Dimpling", "Retraction", "Scaling / rash"]] = None
    mass: ExamStatus
    mass_size_mm: Optional[int] = Field(None, ge=1, le=300)
    mass_mobility: Optional[Literal["Mobile", "Fixed"]] = None
    mass_consistency: Optional[Literal["Soft", "Firm", "Hard"]] = None
    axillary_nodes: ExamStatus

    @property
    def abnormal(self) -> bool:
        return "Abnormal" in (self.inspection, self.mass, self.axillary_nodes)

    @model_validator(mode="after")
    def validate_details(self):
        if self.inspection == "Abnormal" and not self.inspection_finding:
            raise ValueError("Describe the abnormal inspection finding.")
        if self.mass == "Abnormal" and not self.mass_size_mm:
            raise ValueError("Enter the size of the mass.")
        if self.mass != "Abnormal":
            self.mass_size_mm = self.mass_mobility = self.mass_consistency = None
        if self.inspection != "Abnormal":
            self.inspection_finding = self.nipple_discharge_color = self.skin_change = None
        return self


class ImagingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    modality: Literal["Mammogram", "DBT", "Ultrasound", "MRI"]
    density: Optional[Literal["A", "B", "C", "D"]] = None
    birads: int = Field(ge=0, le=6)

    @model_validator(mode="after")
    def no_density_for_mri_or_ultrasound(self):
        if self.modality in ("MRI", "Ultrasound"):
            self.density = None
        return self


class BreastHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["None", "Biopsy", "Lumpectomy", "Cyst Aspiration", "Implants"]
    details: Optional[str] = Field(None, max_length=200)


class IntakeForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 1. Patient demographics & baseline data
    full_name: str = Field(min_length=2, max_length=150)
    date_of_birth: date
    patient_id: Optional[str] = Field(None, pattern=r"^BC-([A-Z]{2}-\d{3}|\d{5})$")  # empty = new ID
    phone: Optional[str] = Field(None, min_length=7, max_length=30)   # required at registration
    national_id: Optional[str] = Field(None, max_length=30)
    evaluation_date: date
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=8, max_length=100)

    # 2. Chief complaint & presenting symptoms
    presentation: Optional[bool] = None          # True = symptomatic
    lump_side: Optional[Side] = None
    lump_duration: Optional[str] = Field(None, max_length=50)
    nipple_discharge_side: Optional[Side] = None
    nipple_discharge_color: Optional[Literal["Bloody", "Clear", "Milky"]] = None
    pain_type: Optional[Literal["Cyclical", "Non-cyclical"]] = None
    pain_severity: Optional[int] = Field(None, ge=1, le=10)

    # 3. Obstetric & gynaecological history
    marital_status: Literal["Married", "Unmarried"]
    menarche_age: Optional[int] = Field(None, ge=6, le=25)
    menopausal_status: Optional[Literal["Premenopausal", "Perimenopausal", "Postmenopausal"]] = None
    menopause_age: Optional[int] = Field(None, ge=20, le=70)
    parity: Optional[int] = Field(None, ge=0, le=20)
    age_first_birth: Optional[int] = Field(None, ge=10, le=60)
    ocp_use: Optional[Literal["Current", "Past", "Never"]] = None
    hrt_use: Optional[YesNoUnknown] = None
    hrt_duration_years: Optional[float] = Field(None, ge=0, le=60)
    breastfeeding_history: YesNoUnknown = "Unknown"
    breastfeeding_duration_years: Optional[float] = Field(None, ge=0, le=30)

    # 4. Personal medical history
    breast_history: list[BreastHistoryEntry] = Field(default_factory=list, max_length=20)
    high_risk_biopsy: Optional[Literal["None", "ADH", "ALH", "LCIS", "Unknown"]] = None
    medical_history_present: bool = False
    medical_history_details: Optional[str] = Field(None, max_length=2000)
    height_cm: float = Field(ge=100, le=230)
    weight_kg: float = Field(ge=25, le=300)
    chest_radiation: YesNoUnknown
    chest_radiation_age: Optional[int] = Field(None, ge=1, le=100)
    chest_radiation_years: Optional[int] = Field(None, ge=0, le=100)
    genetic_status: Literal["Never tested", "Negative", "Positive"]
    genetic_gene: Optional[Literal["BRCA1", "BRCA2", "TP53", "PTEN", "PALB2", "Other"]] = None
    genetic_gene_other: Optional[str] = Field(None, max_length=30)
    geriatric_end_stage_failure: Optional[Literal["Yes", "No"]] = None
    geriatric_adl_independent: Optional[Literal["Yes", "No"]] = None
    geriatric_life_expectancy_over_10y: Optional[bool] = None    # None = not assessed yet

    # 5. Family history of cancer
    family_history: list[Relative] = Field(default_factory=list, max_length=20)
    family_history_none: bool = False

    # 6. Clinical breast examination
    cbe_right: BreastExam
    cbe_left: BreastExam

    # 7. Imaging assessment
    imaging: list[ImagingEntry] = Field(default_factory=list, max_length=20)
    imaging_not_done: bool = False

    # 8. Clinical plan & disposition
    plan: list[PlanOption] = Field(min_length=1)
    followup_months: Optional[int] = Field(None, ge=1, le=24)
    clinician_notes: Optional[str] = Field(None, max_length=2000)
    examiner_name: str = Field(min_length=2, max_length=150)
    signed_date: date

    @property
    def age(self) -> int:
        d, e = self.date_of_birth, self.evaluation_date
        return e.year - d.year - ((e.month, e.day) < (d.month, d.day))

    @property
    def bmi(self) -> float:
        return round(self.weight_kg / ((self.height_cm / 100) ** 2), 1)

    @property
    def gene(self) -> Optional[str]:
        return self.genetic_gene_other if self.genetic_gene == "Other" else self.genetic_gene

    def warnings(self) -> list[str]:
        """Clinical consistency warnings. They never block saving."""
        out = []
        if self.presentation is False and (self.cbe_right.abnormal or self.cbe_left.abnormal):
            out.append("Abnormal clinical examination recorded for an asymptomatic presentation.")
        if any(s.birads in (4, 5, 6) for s in self.imaging) and PlanOption.URGENT_BIOPSY not in self.plan:
            out.append("BI-RADS 4–6 normally requires urgent biopsy referral.")
        if any(s.birads == 3 for s in self.imaging) and PlanOption.ROUTINE_RESCREEN in self.plan:
            out.append("BI-RADS 3 generally needs short-term follow-up rather than routine rescreening.")
        if any(s.density in ("C", "D") for s in self.imaging) and PlanOption.ROUTINE_RESCREEN in self.plan:
            out.append("Dense breast tissue may require supplemental imaging review.")
        return out

    @model_validator(mode="after")
    def check_rules(self):
        age = self.age
        if self.date_of_birth >= self.evaluation_date:
            raise ValueError("Date of birth must be before the evaluation date.")
        if self.signed_date < self.evaluation_date:
            raise ValueError("Date signed cannot be before the evaluation date.")
        if self.presentation is None:
            raise ValueError("Select whether the patient is symptomatic or asymptomatic.")
        if self.presentation is False:
            self.lump_side = self.lump_duration = None
            self.nipple_discharge_side = self.nipple_discharge_color = None
            self.pain_type = self.pain_severity = None
        if not self.nipple_discharge_side:
            self.nipple_discharge_color = None
        if not self.pain_type:
            self.pain_severity = None

        if self.marital_status == "Unmarried":
            self.parity = self.age_first_birth = None
            self.breastfeeding_history = "Unknown"
        if self.breastfeeding_history != "Yes":
            self.breastfeeding_duration_years = None
        if age < 40:
            self.menopausal_status = None
        if self.menopausal_status != "Postmenopausal":
            self.menopause_age = None
        if self.menopause_age is not None and self.menopause_age > age:
            raise ValueError("Age at menopause cannot be greater than the patient's age.")
        if not 45 <= age <= 55:
            self.hrt_use = self.hrt_duration_years = None
        elif self.hrt_use != "Yes":
            self.hrt_duration_years = None

        for entry in self.breast_history:
            if entry.type == "None":
                entry.details = None
        if not self.medical_history_present:
            self.medical_history_details = None
        elif not self.medical_history_details:
            raise ValueError("Enter medical history details when Medical History is marked Yes.")

        if self.chest_radiation != "Yes":
            self.chest_radiation_age = self.chest_radiation_years = None
        else:
            if self.chest_radiation_age is None and self.chest_radiation_years is None:
                raise ValueError("Enter the age or years for chest radiation.")
            if self.chest_radiation_age is not None and self.chest_radiation_age > age:
                raise ValueError("Chest-radiation age cannot be greater than the patient's current age.")
            if self.chest_radiation_years is not None and self.chest_radiation_years > age:
                raise ValueError("Chest-radiation duration cannot exceed the patient's current age.")
        if self.genetic_status != "Positive":
            self.genetic_gene = self.genetic_gene_other = None
        elif self.genetic_gene is None:
            raise ValueError("Select the gene for the positive genetic test.")
        if self.genetic_gene != "Other":
            self.genetic_gene_other = None
        elif not self.genetic_gene_other:
            raise ValueError("Enter the other gene name.")

        if age < 75:
            self.geriatric_end_stage_failure = self.geriatric_adl_independent = None
            self.geriatric_life_expectancy_over_10y = None
        elif self.geriatric_end_stage_failure is None or self.geriatric_adl_independent is None:
            raise ValueError("Geriatric assessment is required for patients aged 75 or over.")

        if self.family_history_none:
            self.family_history = []

        if self.imaging_not_done:
            self.imaging = []
        elif not self.imaging:
            raise ValueError("Record imaging or select 'Imaging has not been done yet'.")
        for scan in self.imaging:
            if scan.date > self.evaluation_date:
                raise ValueError("Imaging date cannot be after the evaluation date.")
            if scan.date < self.date_of_birth:
                raise ValueError("Imaging date cannot be before the date of birth.")

        if PlanOption.SHORT_TERM_FOLLOW_UP not in self.plan:
            self.followup_months = None
        return self
