-- 0001_init: core, detection, reference data, security and audit tables (spec section 4).
-- Money is integer cents. Dates are ISO YYYY-MM-DD text. Timestamps are ISO-8601 UTC text.

-- ---------------------------------------------------------------- security
CREATE TABLE users (
    id              INTEGER PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('ADMIN','REVIEWER','VIEWER')),
    password_hash   TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    last_login_at   TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    user_id     INTEGER,
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    before      TEXT,
    after       TEXT,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL UNIQUE
);
CREATE INDEX ix_audit_ts ON audit_log(ts);
CREATE INDEX ix_audit_entity ON audit_log(entity_type, entity_id);
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    updated_by  INTEGER,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE settings_history (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    version     INTEGER NOT NULL,
    updated_by  INTEGER,
    updated_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------- core
CREATE TABLE parties (
    id                  INTEGER PRIMARY KEY,
    party_type          TEXT NOT NULL CHECK (party_type IN ('VENDOR','CUSTOMER','PAYER')),
    display_name        TEXT NOT NULL,
    name_norm           TEXT NOT NULL,
    tax_id_hmac         TEXT,
    npi                 TEXT,
    remit_address_norm  TEXT,
    phone_norm          TEXT,
    cluster_id          INTEGER,
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_parties_cluster ON parties(cluster_id);
CREATE INDEX ix_parties_tax ON parties(tax_id_hmac);
CREATE INDEX ix_parties_npi ON parties(npi);
CREATE INDEX ix_parties_name ON parties(party_type, name_norm);

CREATE TABLE party_aliases (
    id          INTEGER PRIMARY KEY,
    party_id    INTEGER NOT NULL REFERENCES parties(id),
    alias_type  TEXT NOT NULL CHECK (alias_type IN ('NAME','TAX_ID','NPI','ADDRESS')),
    value_norm  TEXT NOT NULL,
    source      TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (party_id, alias_type, value_norm)
);
CREATE INDEX ix_alias_value ON party_aliases(alias_type, value_norm);

CREATE TABLE patients (
    id                  INTEGER PRIMARY KEY,
    patient_key         TEXT NOT NULL UNIQUE,   -- HMAC(first|last|dob) of normalized values
    first_name_enc      TEXT,
    last_name_enc       TEXT,
    first_phonetic_hmac TEXT,
    last_phonetic_hmac  TEXT,
    dob                 TEXT,
    sex                 TEXT,
    zip5                TEXT,
    cluster_id          INTEGER,
    deleted_at          TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_patients_cluster ON patients(cluster_id);
CREATE INDEX ix_patients_dob ON patients(dob);

CREATE TABLE patient_source_ids (
    id              INTEGER PRIMARY KEY,
    patient_id      INTEGER NOT NULL REFERENCES patients(id),
    source_party_id INTEGER REFERENCES parties(id),
    id_type         TEXT NOT NULL CHECK (id_type IN ('MRN','MEMBER_ID','ACCOUNT')),
    value_hmac      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (patient_id, id_type, value_hmac)
);
CREATE INDEX ix_psi_value ON patient_source_ids(id_type, value_hmac);

CREATE TABLE documents (
    id                  INTEGER PRIMARY KEY,
    source_path         TEXT,
    sha256              TEXT NOT NULL,
    mime                TEXT,
    page_count          INTEGER,
    ingest_method       TEXT CHECK (ingest_method IN ('CSV','X12','PDF_TEXT','OCR','LLM')),
    ingest_status       TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (ingest_status IN ('PENDING','OK','NEEDS_REVIEW','FAILED')),
    ingest_confidence   REAL,
    ingest_error        TEXT,
    ingested_at         TEXT,
    ingested_by         INTEGER,
    deleted_at          TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_documents_sha ON documents(sha256);

CREATE TABLE invoices (
    id                      INTEGER PRIMARY KEY,
    document_id             INTEGER REFERENCES documents(id),
    direction               TEXT NOT NULL CHECK (direction IN ('AP','AR')),
    party_id                INTEGER NOT NULL REFERENCES parties(id),
    invoice_number_raw      TEXT,
    invoice_number_norm     TEXT,
    invoice_number_ocr      TEXT,
    invoice_date            TEXT,
    invoice_date_raw        TEXT,
    due_date                TEXT,
    total_cents             INTEGER NOT NULL,
    currency                TEXT NOT NULL DEFAULT 'USD',
    po_number               TEXT,
    claim_type              TEXT CHECK (claim_type IN ('PROFESSIONAL','INSTITUTIONAL')),
    claim_frequency_code    TEXT CHECK (claim_frequency_code IN ('1','7','8')),
    original_invoice_ref    TEXT,
    status                  TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','PAID','VOID','CREDIT')),
    is_credit               INTEGER NOT NULL DEFAULT 0,
    needs_attention         TEXT,          -- e.g. ambiguous date; shown to the user
    deleted_at              TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_invoices_party_num ON invoices(party_id, invoice_number_norm);
CREATE INDEX ix_invoices_party_total ON invoices(party_id, total_cents);
CREATE INDEX ix_invoices_doc ON invoices(document_id);
CREATE INDEX ix_invoices_date ON invoices(invoice_date);

CREATE TABLE invoice_lines (
    id                      INTEGER PRIMARY KEY,
    invoice_id              INTEGER NOT NULL REFERENCES invoices(id),
    line_no                 INTEGER NOT NULL,
    patient_id              INTEGER REFERENCES patients(id),
    dos_from                TEXT,
    dos_to                  TEXT,
    code_system             TEXT CHECK (code_system IN ('CPT','HCPCS','REV','OTHER')),
    code                    TEXT,
    modifiers               TEXT NOT NULL DEFAULT '[]',   -- JSON sorted array
    modifier_1              TEXT,
    modifier_2              TEXT,
    modifier_3              TEXT,
    modifier_4              TEXT,
    units                   REAL NOT NULL DEFAULT 1,
    charge_cents            INTEGER NOT NULL DEFAULT 0,
    allowed_cents           INTEGER,
    paid_cents              INTEGER,
    rendering_npi           TEXT,
    place_of_service        TEXT,
    revenue_code            TEXT,
    dx_codes                TEXT NOT NULL DEFAULT '[]',
    description_raw         TEXT,
    description_norm        TEXT,
    description_embedding   BLOB,
    deleted_at              TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (invoice_id, line_no)
);
CREATE INDEX ix_lines_patient_dos ON invoice_lines(patient_id, dos_from);
CREATE INDEX ix_lines_code ON invoice_lines(code);

CREATE TABLE invoice_links (
    id              INTEGER PRIMARY KEY,
    from_invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    to_invoice_id   INTEGER NOT NULL REFERENCES invoices(id),
    link_type       TEXT NOT NULL CHECK (link_type IN ('REPLACES','VOIDS','CREDITS')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (from_invoice_id, to_invoice_id, link_type)
);

-- ---------------------------------------------------------------- detection and review
CREATE TABLE detection_runs (
    id              INTEGER PRIMARY KEY,
    mode            TEXT NOT NULL CHECK (mode IN ('INCREMENTAL','FULL','PREVIEW')),
    status          TEXT NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','DONE','FAILED')),
    engine_version  TEXT NOT NULL,
    refdata_version TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    stats           TEXT,
    error           TEXT
);

CREATE TABLE flags (
    id                      INTEGER PRIMARY KEY,
    rule_id                 TEXT NOT NULL,
    rule_version            TEXT NOT NULL,
    engine_version          TEXT NOT NULL,
    refdata_version         TEXT,
    subject_type            TEXT NOT NULL CHECK (subject_type IN ('INVOICE','LINE')),
    subject_id              INTEGER NOT NULL,
    subject_invoice_id      INTEGER NOT NULL,
    counterpart_ids         TEXT NOT NULL,       -- JSON array
    counterpart_ids_hash    TEXT NOT NULL,
    counterpart_invoice_ids TEXT NOT NULL DEFAULT '[]',
    party_id                INTEGER,
    tier                    TEXT NOT NULL CHECK (tier IN ('HARD','PROBABLE','WEAK','INFO')),
    base_tier               TEXT NOT NULL CHECK (base_tier IN ('HARD','PROBABLE','WEAK','INFO')),
    score                   REAL NOT NULL,
    suppressed_by           TEXT,
    downgraded_by           TEXT,
    evidence                TEXT NOT NULL,
    amount_at_risk_cents    INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN','CONFIRMED','DISMISSED','NEEDS_INFO')),
    active                  INTEGER NOT NULL DEFAULT 1,
    last_run_id             INTEGER,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (rule_id, subject_type, subject_id, counterpart_ids_hash)
);
CREATE INDEX ix_flags_queue ON flags(status, active, tier, score);
CREATE INDEX ix_flags_subject_invoice ON flags(subject_invoice_id);
CREATE INDEX ix_flags_party ON flags(party_id);

CREATE TABLE reviews (
    id                  INTEGER PRIMARY KEY,
    flag_id             INTEGER NOT NULL REFERENCES flags(id),
    reviewer_user_id    INTEGER NOT NULL REFERENCES users(id),
    decision            TEXT NOT NULL CHECK (decision IN ('CONFIRMED_DUPLICATE','NOT_DUPLICATE','NEEDS_INFO')),
    reason_code         TEXT,
    note                TEXT,
    pair_key            TEXT NOT NULL,     -- subject_type:sorted ids, used by SUP-005
    decided_at          TEXT NOT NULL,
    recovered_cents     INTEGER,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_reviews_pair ON reviews(pair_key, decision);
CREATE INDEX ix_reviews_flag ON reviews(flag_id);

CREATE TABLE identity_suggestions (
    id              INTEGER PRIMARY KEY,
    entity_type     TEXT NOT NULL CHECK (entity_type IN ('PARTY','PATIENT')),
    left_id         INTEGER NOT NULL,
    right_id        INTEGER NOT NULL,
    match_probability REAL NOT NULL,
    evidence        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACCEPTED','REJECTED','AUTO')),
    decided_by      INTEGER,
    decided_at      TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (entity_type, left_id, right_id)
);

CREATE TABLE ai_suggestions (
    id              INTEGER PRIMARY KEY,
    target_type     TEXT NOT NULL,
    target_id       INTEGER,
    task            TEXT NOT NULL CHECK (task IN ('EXTRACT','EXPLAIN','TRIAGE','NLQ')),
    model_id        TEXT NOT NULL,
    model_sha256    TEXT,
    prompt_version  TEXT NOT NULL,
    input_hash      TEXT NOT NULL,
    output          TEXT,
    trace           TEXT,
    latency_ms      INTEGER,
    status          TEXT NOT NULL CHECK (status IN ('OK','INVALID','TIMEOUT','ERROR')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_ai_target ON ai_suggestions(target_type, target_id, task);

CREATE TABLE jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    priority    INTEGER NOT NULL DEFAULT 100,   -- lower runs first
    status      TEXT NOT NULL DEFAULT 'QUEUED'
                CHECK (status IN ('QUEUED','RUNNING','DONE','FAILED','CANCELLED')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    not_before  TEXT,
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX ix_jobs_queue ON jobs(status, priority, id);

-- ---------------------------------------------------------------- ingest templates
CREATE TABLE csv_mapping_templates (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    header_sig  TEXT,                   -- sha256 of normalized header row, for auto-match
    mapping     TEXT NOT NULL,          -- JSON {canonical_field: source_column}
    options     TEXT NOT NULL DEFAULT '{}',
    created_by  INTEGER,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE vendor_templates (
    id          INTEGER PRIMARY KEY,
    party_id    INTEGER REFERENCES parties(id),
    fingerprint TEXT NOT NULL,          -- layout fingerprint of the vendor's documents
    template    TEXT NOT NULL,          -- JSON field -> anchor/regex/region
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_vendor_templates_fp ON vendor_templates(fingerprint);

CREATE TABLE extraction_drafts (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    method      TEXT NOT NULL,
    fields      TEXT NOT NULL,          -- JSON {invoice:{...}, lines:[...], confidence:{...}}
    status      TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACCEPTED','REJECTED')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ---------------------------------------------------------------- reference data (read-only at runtime)
CREATE TABLE ref_dataset_versions (
    dataset         TEXT NOT NULL,
    version         TEXT NOT NULL,
    effective_from  TEXT,
    effective_to    TEXT,
    sha256          TEXT NOT NULL,
    row_count       INTEGER,
    imported_at     TEXT NOT NULL,
    imported_by     INTEGER,
    PRIMARY KEY (dataset, version)
);

CREATE TABLE ref_ncci_ptp (
    column1_code        TEXT NOT NULL,
    column2_code        TEXT NOT NULL,
    effective_from      TEXT NOT NULL,
    effective_to        TEXT,
    modifier_indicator  TEXT NOT NULL CHECK (modifier_indicator IN ('0','1','9')),
    setting             TEXT NOT NULL CHECK (setting IN ('PRACTITIONER','HOSPITAL')),
    version             TEXT NOT NULL
);
CREATE INDEX ix_ptp ON ref_ncci_ptp(column1_code, column2_code, setting);

CREATE TABLE ref_mue (
    code            TEXT NOT NULL,
    mue_value       INTEGER NOT NULL,
    mai             INTEGER NOT NULL CHECK (mai IN (1,2,3)),
    setting         TEXT NOT NULL CHECK (setting IN ('PRACTITIONER','HOSPITAL','DME')),
    effective_from  TEXT NOT NULL,
    effective_to    TEXT,
    version         TEXT NOT NULL
);
CREATE INDEX ix_mue ON ref_mue(code, setting);

CREATE TABLE ref_global_days (
    code            TEXT NOT NULL,
    global_days     TEXT NOT NULL CHECK (global_days IN ('000','010','090','XXX','YYY','ZZZ','MMM')),
    effective_year  INTEGER NOT NULL,
    version         TEXT NOT NULL,
    PRIMARY KEY (code, effective_year)
);

CREATE TABLE ref_hcpcs (
    code            TEXT NOT NULL,
    short_desc      TEXT,
    effective_from  TEXT,
    effective_to    TEXT,
    version         TEXT NOT NULL
);
CREATE INDEX ix_hcpcs ON ref_hcpcs(code);

CREATE TABLE ref_frequency_limits (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL,
    max_count   INTEGER NOT NULL,
    period      TEXT NOT NULL CHECK (period IN ('DAY','WEEK','MONTH','YEAR','LIFETIME')),
    scope       TEXT NOT NULL CHECK (scope IN ('PATIENT','PATIENT_PROVIDER')),
    source      TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE ref_modifiers (
    modifier    TEXT PRIMARY KEY,
    category    TEXT NOT NULL CHECK (category IN ('DISTINCT','REPEAT','BILATERAL','GLOBAL_BYPASS','OTHER')),
    ncci_bypass INTEGER NOT NULL DEFAULT 0,
    description TEXT
);

CREATE TABLE ref_recurring_series (
    id                  INTEGER PRIMARY KEY,
    code                TEXT NOT NULL,
    typical_frequency   TEXT NOT NULL,   -- DAILY | NX_WEEK | WEEKLY | EVERY_<n>D | MONTHLY
    note                TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
