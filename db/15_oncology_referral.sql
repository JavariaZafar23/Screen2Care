-- =====================================================================
-- Oncology referral after a malignant biopsy: the navigator reads the pathology report
-- and refers the patient to an oncologist (oncology team staff, role TumorBoard).
-- Benign results stay with the clinician. Safe to run again:
--   docker exec -i screening_db psql -U loop -d screening_care_loop < db/15_oncology_referral.sql
-- =====================================================================
INSERT INTO staff (full_name, role)
SELECT v.name, 'TumorBoard' FROM (VALUES ('Dr. Surgical Oncologist'), ('Dr. Medical Oncologist')) v(name)
WHERE NOT EXISTS (SELECT 1 FROM staff s WHERE s.full_name = v.name);

ALTER TABLE referral ADD COLUMN IF NOT EXISTS referred_by INT REFERENCES staff(staff_id);
ALTER TABLE referral ADD COLUMN IF NOT EXISTS referral_note VARCHAR(400);
