-- =====================================================================
-- Role-based access: care-team assignments + test data for the patient page.
-- Safe to run again on an existing database:
--   docker exec -i screening_db psql -U loop -d screening_care_loop < db/06_access.sql
-- =====================================================================
SET TIME ZONE 'UTC';

-- A second clinician, so the "only my patients" filter can be tested
INSERT INTO staff (staff_id, full_name, role) VALUES (11, 'Dr. Second Clinician', 'Clinician')
ON CONFLICT (staff_id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('staff', 'staff_id'), (SELECT MAX(staff_id) FROM staff));

-- Which clinician looks after which patient (a patient can have more than one)
CREATE TABLE IF NOT EXISTS care_team_assignment (
    patient_id   VARCHAR(20) NOT NULL REFERENCES patient(patient_id),
    staff_id     INT NOT NULL REFERENCES staff(staff_id),
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by  INT REFERENCES staff(staff_id),
    PRIMARY KEY (patient_id, staff_id)
);
CREATE INDEX IF NOT EXISTS ix_care_team_staff ON care_team_assignment(staff_id);

-- Dr. Clinician (3): HP + HR patients.  Dr. Second Clinician (11): DL patients.
INSERT INTO care_team_assignment (patient_id, staff_id, assigned_by)
SELECT patient_id, CASE WHEN patient_id LIKE 'BC-DL-%' THEN 11 ELSE 3 END, 1
FROM patient
WHERE patient_id LIKE 'BC-HP-%' OR patient_id LIKE 'BC-DL-%' OR patient_id LIKE 'BC-HR-%'
ON CONFLICT DO NOTHING;

-- Signed plan for HP-001 and HP-002 (matches the disposition notes in 02_seed.sql)
UPDATE encounter
SET disposition = 'UrgentBiopsyReferral', examiner_name = 'Dr. Clinician', signed_date = '2026-10-02'
WHERE encounter_id IN (1, 2) AND disposition IS NULL;

-- Notes released to the patient, so each patient page has its own content.
-- HP-003 is left without a released note to show the "being reviewed" state.
INSERT INTO clinical_note (encounter_id, author_id, note_type, note_text,
                           is_released_to_patient, released_at, released_by)
SELECT v.encounter_id, 3, 'Clinical', v.note_text, TRUE, '2026-10-02 10:00', 3
FROM (VALUES
  (1, 'Your mammogram on 1 October showed an area that needs a closer look. '
      'We recommend a core needle biopsy. Our patient navigator will call you to book it.'),
  (2, 'Your mammogram on 1 October found a small area that needs further testing. '
      'We recommend a biopsy. The navigator will contact you to arrange a date.')
) AS v(encounter_id, note_text)
WHERE NOT EXISTS (SELECT 1 FROM clinical_note n
                  WHERE n.encounter_id = v.encounter_id AND n.is_released_to_patient);
