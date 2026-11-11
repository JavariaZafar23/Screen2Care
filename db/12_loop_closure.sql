-- =====================================================================
-- Closing a care loop without treatment, with a reason (never deleting it):
-- lost to follow-up, declined, transferred or deceased. Also lets staff log a letter
-- as a contact attempt. Safe to run again on an existing database:
--   docker exec -i screening_db psql -U loop -d screening_care_loop < db/12_loop_closure.sql
-- =====================================================================
SET TIME ZONE 'UTC';

ALTER TABLE care_episode DROP CONSTRAINT IF EXISTS care_episode_status_check;
ALTER TABLE care_episode ADD CONSTRAINT care_episode_status_check CHECK (status IN (
    'Open','Paused','Closed-TreatmentStarted','Closed-Benign',
    'Closed-Declined','Closed-Transferred','Closed-Deceased','Closed-LostToFollowUp'));

-- Who closed the loop (closed_at and closure_note already exist), and which loop a reopened one continues
ALTER TABLE care_episode ADD COLUMN IF NOT EXISTS closed_by INT REFERENCES staff(staff_id);
ALTER TABLE care_episode ADD COLUMN IF NOT EXISTS reopened_from INT REFERENCES care_episode(episode_id);

ALTER TABLE patient_contact DROP CONSTRAINT IF EXISTS patient_contact_channel_check;
ALTER TABLE patient_contact ADD CONSTRAINT patient_contact_channel_check
    CHECK (channel IN ('SMS','Phone','Email','InPerson','Letter'));
