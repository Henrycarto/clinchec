-- ---------------------------------------------------------------------------
-- Clinchec — initial schema
-- Runs automatically on first boot of the postgres container.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- --- Tenancy ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS practices (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL,
    npi             TEXT UNIQUE,
    tax_id          TEXT,
    fhir_issuer     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clinicians (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    practice_id     UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    fhir_user_id    TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    npi             TEXT,
    email           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (practice_id, fhir_user_id)
);

-- --- Payers & rules (Clinchec Live) ----------------------------------------

CREATE TABLE IF NOT EXISTS payers (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug            TEXT NOT NULL UNIQUE,   -- 'aetna', 'bcbs', 'uhc'
    display_name    TEXT NOT NULL,
    portal_base_url TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payer_rules (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    payer_id             UUID NOT NULL REFERENCES payers(id) ON DELETE CASCADE,
    cpt_code             TEXT NOT NULL,
    icd10_codes          TEXT[] NOT NULL DEFAULT '{}',
    plan_type            TEXT,                       -- 'commercial' | 'medicare_advantage' | ...
    requires_pa          BOOLEAN NOT NULL DEFAULT TRUE,
    criteria_text        TEXT NOT NULL,
    -- Structured criteria the scoring engine consumes directly.
    required_duration_weeks      INTEGER,
    required_conservative_care   TEXT[] NOT NULL DEFAULT '{}',
    required_imaging             TEXT[] NOT NULL DEFAULT '{}',
    -- pgvector embedding of criteria_text (text-embedding-3-small, 1536 dims).
    criteria_embedding   vector(1536),
    source_url           TEXT,
    source_checksum      TEXT,
    effective_date       DATE,
    last_verified_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (payer_id, cpt_code, plan_type)
);

CREATE INDEX IF NOT EXISTS idx_payer_rules_cpt ON payer_rules (cpt_code);
CREATE INDEX IF NOT EXISTS idx_payer_rules_icd ON payer_rules USING GIN (icd10_codes);
CREATE INDEX IF NOT EXISTS idx_payer_rules_embedding
    ON payer_rules USING hnsw (criteria_embedding vector_cosine_ops);

-- Append-only audit of every rule change the crawler observes.
CREATE TABLE IF NOT EXISTS payer_rule_revisions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_id         UUID NOT NULL REFERENCES payer_rules(id) ON DELETE CASCADE,
    diff            JSONB NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payer_crawl_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    payer_id        UUID NOT NULL REFERENCES payers(id) ON DELETE CASCADE,
    status          TEXT NOT NULL,            -- 'running' | 'succeeded' | 'failed'
    rules_seen      INTEGER NOT NULL DEFAULT 0,
    rules_changed   INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

-- --- Scans (Clinchec Scan) --------------------------------------------------

CREATE TABLE IF NOT EXISTS scans (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    practice_id         UUID REFERENCES practices(id) ON DELETE SET NULL,
    clinician_id        UUID REFERENCES clinicians(id) ON DELETE SET NULL,
    -- PHI-bearing note text is never persisted in plaintext; we store a
    -- SHA-256 digest for dedupe plus the de-identified structured extraction.
    note_sha256         TEXT NOT NULL,
    note_char_count     INTEGER NOT NULL,
    extraction          JSONB NOT NULL,
    approval_score      NUMERIC(5, 4) NOT NULL,
    approval_band       TEXT NOT NULL,        -- 'green' | 'amber' | 'red'
    model_version       TEXT NOT NULL,
    latency_ms          INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scans_practice_created ON scans (practice_id, created_at DESC);

-- --- Prior authorization requests (Clinchec Forms) --------------------------

CREATE TABLE IF NOT EXISTS pa_requests (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    practice_id         UUID REFERENCES practices(id) ON DELETE SET NULL,
    clinician_id        UUID REFERENCES clinicians(id) ON DELETE SET NULL,
    scan_id             UUID REFERENCES scans(id) ON DELETE SET NULL,
    payer_id            UUID REFERENCES payers(id) ON DELETE SET NULL,
    member_id_last4     TEXT,
    cpt_code            TEXT NOT NULL,
    icd10_codes         TEXT[] NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'draft',
        -- draft | ready | submitted | approved | denied | withdrawn
    form_key            TEXT,
    field_values        JSONB NOT NULL DEFAULT '{}'::jsonb,
    unmapped_fields     TEXT[] NOT NULL DEFAULT '{}',
    submission_ref      TEXT,
    export_s3_key       TEXT,
    submitted_at        TIMESTAMPTZ,
    decided_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pa_requests_status ON pa_requests (status, created_at DESC);

-- --- Audit trail (HIPAA §164.312(b)) ---------------------------------------

CREATE TABLE IF NOT EXISTS audit_events (
    id              BIGSERIAL PRIMARY KEY,
    actor_id        UUID,
    actor_type      TEXT NOT NULL,          -- 'clinician' | 'system' | 'service'
    action          TEXT NOT NULL,          -- 'scan.create' | 'pa.submit' | ...
    resource_type   TEXT NOT NULL,
    resource_id     TEXT,
    ip_address      INET,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_actor_time ON audit_events (actor_id, occurred_at DESC);

-- --- Seed the three launch payers ------------------------------------------

INSERT INTO payers (slug, display_name, portal_base_url) VALUES
    ('aetna', 'Aetna',                     'https://www.aetna.com'),
    ('bcbs',  'Blue Cross Blue Shield',    'https://www.bcbs.com'),
    ('uhc',   'UnitedHealthcare',          'https://www.uhcprovider.com')
ON CONFLICT (slug) DO NOTHING;
