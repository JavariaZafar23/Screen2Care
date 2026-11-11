-- =====================================================================
-- Biopsy booking: the navigator or scheduling manager books only the appointment
-- (date and time). The biopsy type is no longer asked for, and who booked it is kept.
-- Safe to run again on an existing database:
--   docker exec -i screening_db psql -U loop -d screening_care_loop < db/14_biopsy_booking.sql
-- =====================================================================
ALTER TABLE biopsy ALTER COLUMN biopsy_type DROP NOT NULL;
ALTER TABLE biopsy ADD COLUMN IF NOT EXISTS booked_by INT REFERENCES staff(staff_id);
