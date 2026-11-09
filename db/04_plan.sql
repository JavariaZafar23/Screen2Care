-- Clinical plan: allow the new engine lanes and a high-risk surveillance disposition.
ALTER TABLE lane_assignment DROP CONSTRAINT IF EXISTS lane_assignment_lane_check;
ALTER TABLE lane_assignment ADD CONSTRAINT lane_assignment_lane_check CHECK (lane IN (
  'DataPending','Diagnostic','HighRisk-Waiting','HighRisk-MRIOnly','HighRisk-DBT+MRI',
  'HighRisk-DBT+ConsiderMRI','WellnessHold','RoutineScreening','DenseSupplemental',
  'ShortInterval6M','RecallBirads0','UrgentWorkup','Treatment','SymptomaticCareOnly'));

ALTER TABLE encounter DROP CONSTRAINT IF EXISTS encounter_disposition_check;
ALTER TABLE encounter ADD CONSTRAINT encounter_disposition_check CHECK (disposition IN (
  'RoutineRescreen','ShortTermFollowUp','UrgentBiopsyReferral','HighRiskSurveillance'));
