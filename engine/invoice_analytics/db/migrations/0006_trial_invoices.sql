-- 0006_trial_invoices: clinical-trial site invoices identify patients by study subject ID and
-- bill protocol visits (Screening, Cohort 1: D-8, M2 ...) rather than CPT codes.
ALTER TABLE invoice_lines ADD COLUMN visit_label TEXT;
-- The subject ID is displayed to reviewers, so it is stored field-encrypted (not only as an HMAC).
ALTER TABLE patients ADD COLUMN subject_id_enc TEXT;
