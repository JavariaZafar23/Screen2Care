-- =====================================================================
-- Additions for the Breast Screening Clinical Proforma (intake form)
-- Runs after 01_schema.sql and 02_seed.sql on first database start.
-- =====================================================================

-- Section 2 (symptoms) and Section 7 (plan, signature) live on the encounter
ALTER TABLE encounter
    ADD COLUMN lump_side               VARCHAR(5)  CHECK (lump_side IN ('Right','Left','Both')),
    ADD COLUMN lump_duration           VARCHAR(50),
    ADD COLUMN nipple_discharge_side   VARCHAR(5)  CHECK (nipple_discharge_side IN ('Right','Left','Both')),
    ADD COLUMN nipple_discharge_color  VARCHAR(6)  CHECK (nipple_discharge_color IN ('Bloody','Clear','Milky')),
    ADD COLUMN skin_changes            TEXT[],
    ADD COLUMN pain_type               VARCHAR(12) CHECK (pain_type IN ('Cyclical','Non-cyclical')),
    ADD COLUMN pain_severity           SMALLINT    CHECK (pain_severity BETWEEN 1 AND 10),
    ADD COLUMN disposition             VARCHAR(25) CHECK (disposition IN ('RoutineRescreen','ShortTermFollowUp','UrgentBiopsyReferral')),
    ADD COLUMN followup_months         SMALLINT    CHECK (followup_months BETWEEN 1 AND 24),
    ADD COLUMN examiner_name           VARCHAR(150),
    ADD COLUMN signed_date             DATE;

-- Section 4: the form also lists lumpectomy and implants
ALTER TABLE prior_breast_procedure DROP CONSTRAINT prior_breast_procedure_procedure_type_check;
ALTER TABLE prior_breast_procedure ADD CONSTRAINT prior_breast_procedure_procedure_type_check
    CHECK (procedure_type IN ('CystAspiration','Biopsy','Lumpectomy','Implants','Surgery','Other'));

-- Section 5: mass consistency
ALTER TABLE clinical_breast_exam
    ADD COLUMN mass_consistency VARCHAR(5) CHECK (mass_consistency IN ('Soft','Firm','Hard'));

-- Patient IDs for new patients: BC-00001, BC-00002, ...
CREATE SEQUENCE patient_number_seq START 1;
