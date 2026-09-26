-- 0003_nlq_views: curated read-only views for natural-language query (T3) and reports.
-- No patient names or keyed hashes are exposed: patients appear only as cluster ids.
CREATE VIEW v_invoices AS
SELECT i.id AS invoice_id, i.direction, p.display_name AS party_name, p.party_type, p.cluster_id AS party_cluster,
       i.invoice_number_raw AS invoice_number, i.invoice_date, i.due_date, i.total_cents, i.currency, i.po_number,
       i.claim_type, i.claim_frequency_code, i.status, i.is_credit,
       (SELECT COUNT(*) FROM invoice_lines l WHERE l.invoice_id = i.id) AS line_count
FROM invoices i JOIN parties p ON p.id = i.party_id WHERE i.deleted_at IS NULL;

CREATE VIEW v_invoice_lines AS
SELECT l.id AS line_id, l.invoice_id, l.line_no, pt.cluster_id AS patient_cluster, l.dos_from, l.dos_to,
       l.code_system, l.code, l.modifiers, l.units, l.charge_cents, l.allowed_cents, l.paid_cents, l.rendering_npi,
       l.place_of_service, l.description_norm AS description
FROM invoice_lines l LEFT JOIN patients pt ON pt.id = l.patient_id WHERE l.deleted_at IS NULL;

CREATE VIEW v_flags AS
SELECT f.id AS flag_id, f.rule_id, f.tier, f.score, f.status, f.subject_type, f.subject_invoice_id AS invoice_id,
       f.party_id, f.amount_at_risk_cents, f.suppressed_by, f.created_at
FROM flags f WHERE f.active = 1;

CREATE VIEW v_reviews AS
SELECT r.id AS review_id, r.flag_id, r.decision, r.reason_code, r.decided_at, r.recovered_cents,
       u.display_name AS reviewer
FROM reviews r LEFT JOIN users u ON u.id = r.reviewer_user_id;

CREATE VIEW v_parties AS
SELECT p.id AS party_id, p.party_type, p.display_name, p.cluster_id, p.npi, p.active FROM parties p;
