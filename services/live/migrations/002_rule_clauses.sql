-- ---------------------------------------------------------------------------
-- Indication-scoped rule clauses
--
-- `payer_rules` carries one `requires_pa` boolean and one flat criteria set per
-- (payer, cpt, plan_type). Crawl validation showed that shape is wrong for a
-- large share of real bulletins: 4 of 10 verified payer/procedure pairs are
-- MIXED — the same CPT is approved for one indication and explicitly excluded
-- for another.
--
--   Aetna CPB 0673, CPT 29881
--     covered  : arthroscopic knee surgery (with or without partial
--                meniscectomy) for knee pain plus mechanical symptoms and no
--                more than mild osteoarthritis (Kellgren-Lawrence 0-2)
--     excluded : meniscectomy for medial or lateral meniscal root tears
--
-- Collapsing that to one boolean makes Clinchec score a root-tear request
-- against mechanical-symptom criteria and return a confident green on a request
-- the bulletin explicitly calls experimental. Same failure shape as mapping
-- lumbar MRI to the spinal-fusion bulletin, one layer up.
--
-- So criteria are (payer, cpt, INDICATION), and a rule owns clauses.
-- `payer_rules` remains the container and the answer to "does this need PA at
-- all"; the clause decides what evidence approves it.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS payer_rule_clauses (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_id         UUID NOT NULL REFERENCES payer_rules(id) ON DELETE CASCADE,

    -- 'covered'  : this indication is approvable when the criteria are met
    -- 'excluded' : this indication is denied regardless of evidence
    polarity        TEXT NOT NULL CHECK (polarity IN ('covered', 'excluded')),

    -- What the clause applies to. `indication_text` is the payer's own wording,
    -- kept verbatim for display and for appeals — quoting an insurer's bulletin
    -- back at them is the single most effective appeal argument.
    indication_text            TEXT NOT NULL,
    indication_icd10_prefixes  TEXT[] NOT NULL DEFAULT '{}',

    -- Evidence this clause requires. Only meaningful when polarity='covered';
    -- an exclusion denies on indication alone, so no amount of documentation
    -- changes the outcome.
    required_duration_weeks     INTEGER,
    required_conservative_care  TEXT[] NOT NULL DEFAULT '{}',
    required_imaging            TEXT[] NOT NULL DEFAULT '{}',

    -- Provenance. `source_pattern` names the adjudication pattern that matched,
    -- so a wrong clause can be traced to the extraction rule that produced it
    -- rather than guessed at.
    source_pattern  TEXT NOT NULL,
    source_snippet  TEXT NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rule_clauses_rule ON payer_rule_clauses (rule_id);

-- Exclusions are evaluated before coverage, so they are read first and often.
CREATE INDEX IF NOT EXISTS idx_rule_clauses_polarity
    ON payer_rule_clauses (rule_id, polarity);

CREATE INDEX IF NOT EXISTS idx_rule_clauses_icd
    ON payer_rule_clauses USING GIN (indication_icd10_prefixes);

-- A clause is identified by what it adjudicates, so re-crawling identical
-- criteria updates in place instead of accumulating duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_clause_identity
    ON payer_rule_clauses (rule_id, polarity, md5(indication_text));

COMMENT ON TABLE payer_rule_clauses IS
    'Indication-scoped adjudication clauses. A rule may both cover and exclude '
    'the same CPT depending on indication; see infra/sql/002_rule_clauses.sql.';

COMMENT ON COLUMN payer_rule_clauses.source_snippet IS
    'The payer''s own sentence. Never paraphrase: this is what gets quoted back '
    'on appeal.';

-- ---------------------------------------------------------------------------
-- The flat criteria columns on payer_rules become the payer-level DEFAULT,
-- applied when no clause matches the patient''s indication. They are not
-- dropped: a bulletin that adjudicates a procedure unconditionally has no
-- indication to scope on, and that is a legitimate shape.
-- ---------------------------------------------------------------------------

COMMENT ON COLUMN payer_rules.required_duration_weeks IS
    'Payer-level default. A matching clause overrides it; see payer_rule_clauses.';
