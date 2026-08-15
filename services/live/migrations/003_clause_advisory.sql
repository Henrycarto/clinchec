-- ---------------------------------------------------------------------------
-- Advisory clauses: adjudications we can read but cannot yet scope.
--
-- Payer Policy prose names indications in words, not codes:
--
--   "Meniscectomy (arthroscopic or open; total or partial) for the treatment
--    of medial or lateral meniscal root tears"   -- Aetna CPB 0673
--
-- The adjudication registry extracts that reliably. Mapping "meniscal root
-- tears" onto ICD-10 prefixes is a separate inference, and it is not solved.
--
-- Which creates a trap. A clause with no `indication_icd10_prefixes` matches
-- every request by design — correct for a coverage statement that genuinely
-- applies unconditionally, and catastrophic for an exclusion, which would then
-- deny every request for that procedure.
--
-- So a clause whose indication could not be resolved to codes is marked
-- advisory: surfaced to the clinician as something to check, never used to
-- decide the score. Being unable to scope an exclusion is a reason to ask a
-- question, not a licence to guess in either direction.
-- ---------------------------------------------------------------------------

ALTER TABLE payer_rule_clauses
    ADD COLUMN IF NOT EXISTS advisory BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN payer_rule_clauses.advisory IS
    'True when the indication could not be resolved to ICD-10 prefixes. Shown '
    'to the clinician; never selected by the scoring engine, because an '
    'unscoped exclusion would otherwise deny every request for the procedure.';

-- Scoring only ever selects non-advisory clauses, so that is the hot filter.
DROP INDEX IF EXISTS idx_rule_clauses_polarity;
CREATE INDEX IF NOT EXISTS idx_rule_clauses_selectable
    ON payer_rule_clauses (rule_id, advisory, polarity);
