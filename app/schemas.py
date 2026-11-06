from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

Side = Literal["Right", "Left", "Both"]


class CbeSide(BaseModel):
    inspection_normal: bool
    inspection_finding: Optional[str] = Field(None, max_length=100)
    mass_present: bool
    mass_size_mm: Optional[int] = Field(None, ge=1, le=300)
    mass_mobility: Optional[Literal["Mobile", "Fixed"]] = None
    mass_consistency: Optional[Literal["Soft", "Firm", "Hard"]] = None
    axillary_node_palpable: bool

    @model_validator(mode="after")
    def clear_unused(self):
        if self.inspection_normal:
            self.inspection_finding = None
        if not self.mass_present:
            self.mass_size_mm = self.mass_mobility = self.mass_consistency = None
        return self


class Relative(BaseModel):
    relation: Literal["Mother", "Sister", "Daughter", "Maternal Aunt", "Paternal Aunt"]
    age_at_diagnosis: Optional[int] = Field(None, ge=1, le=120)


class IntakeForm(BaseModel):
    # 1. Demographics
    full_name: str = Field(min_length=2, max_length=150)
    date_of_birth: date
    national_id: Optional[str] = Field(None, max_length=30)
    evaluation_date: date

    # 2. Symptoms
    is_symptomatic: bool
    lump_side: Optional[Side] = None
    lump_duration: Optional[str] = Field(None, max_length=50)
    nipple_discharge_side: Optional[Side] = None
    nipple_discharge_color: Optional[Literal["Bloody", "Clear", "Milky"]] = None
    skin_changes: list[Literal["Dimpling", "Retraction", "Scaling / Rash"]] = []
    pain_type: Optional[Literal["Cyclical", "Non-cyclical"]] = None
    pain_severity: Optional[int] = Field(None, ge=1, le=10)

    # 3. Obstetric & gynaecological
    menarche_age: Optional[int] = Field(None, ge=6, le=25)
    menopausal_status: Optional[Literal["Premenopausal", "Perimenopausal", "Postmenopausal"]] = None
    menopause_age: Optional[int] = Field(None, ge=20, le=70)
    parity: Optional[int] = Field(None, ge=0, le=20)
    age_first_birth: Optional[int] = Field(None, ge=10, le=60)
    ocp_use: Optional[Literal["Current", "Past", "Never"]] = None
    hrt_use: Optional[bool] = None
    hrt_duration_years: Optional[float] = Field(None, ge=0, le=60)

    # 4. Personal & family history
    prior_breast_history: list[Literal["Biopsy", "Lumpectomy", "CystAspiration", "Implants"]] = []
    prior_breast_details: Optional[str] = Field(None, max_length=200)
    breast_cancer_relatives: list[Relative] = []
    ovarian_cancer: bool = False
    ovarian_relation: Optional[str] = Field(None, max_length=30)
    genetic_result: Literal["BRCA1", "BRCA2", "Negative", "Unknown"] = "Unknown"
    height_cm: Optional[float] = Field(None, ge=100, le=230)
    weight_kg: Optional[float] = Field(None, ge=25, le=300)
    geriatric_end_stage: Optional[bool] = None
    geriatric_independent_adl: Optional[bool] = None

    # 5. Clinical breast exam
    cbe_right: CbeSide
    cbe_left: CbeSide

    # 6. Imaging
    modalities: list[Literal["Mammogram", "DBT", "Ultrasound", "MRI"]] = []
    density: Optional[Literal["A", "B", "C", "D"]] = None
    birads: Optional[int] = Field(None, ge=0, le=5)

    # 7. Plan
    disposition: Optional[Literal["RoutineRescreen", "ShortTermFollowUp", "UrgentBiopsyReferral"]] = None
    followup_months: Optional[int] = Field(None, ge=1, le=24)
    examiner_name: Optional[str] = Field(None, max_length=150)
    signed_date: Optional[date] = None

    @property
    def age(self) -> int:
        d, e = self.date_of_birth, self.evaluation_date
        return e.year - d.year - ((e.month, e.day) < (d.month, d.day))

    @model_validator(mode="after")
    def check_rules(self):
        if self.date_of_birth >= self.evaluation_date:
            raise ValueError("Date of birth must be before the date of evaluation.")
        if not self.is_symptomatic:
            self.lump_side = self.lump_duration = None
            self.nipple_discharge_side = self.nipple_discharge_color = None
            self.skin_changes = []
            self.pain_type = self.pain_severity = None
        if self.menopausal_status != "Postmenopausal":
            self.menopause_age = None
        if not self.hrt_use:
            self.hrt_duration_years = None
        if not self.ovarian_cancer:
            self.ovarian_relation = None
        if self.age >= 75 and (self.geriatric_end_stage is None or self.geriatric_independent_adl is None):
            raise ValueError("Geriatric assessment is required for patients aged 75 or over.")
        if self.birads is not None and not self.modalities:
            raise ValueError("Select the imaging modality used for the BI-RADS assessment.")
        if self.disposition == "ShortTermFollowUp" and self.followup_months is None:
            raise ValueError("Enter the follow-up interval in months.")
        if self.disposition != "ShortTermFollowUp":
            self.followup_months = None
        return self
