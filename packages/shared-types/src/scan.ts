import { z } from 'zod';

/**
 * Mirrors `services/scan/app/schemas.py`. Keep the two in step — the endpoint
 * tests assert the Python shape, and these schemas assert the wire shape the
 * frontend consumes.
 */

export const soapSectionSchema = z.enum([
  'subjective',
  'objective',
  'assessment',
  'plan',
  'unsectioned',
]);

export const entityLabelSchema = z.enum([
  'diagnosis',
  'procedure',
  'conservative_care',
  'red_flag',
  'imaging_evidence',
  'functional_impairment',
  'anatomy',
  'medication',
]);

export const approvalBandSchema = z.enum(['green', 'amber', 'red']);
export const sexSchema = z.enum(['male', 'female', 'other', 'unknown']);
export const lateralitySchema = z.enum(['left', 'right', 'bilateral']);

export const textSpanSchema = z.object({
  text: z.string(),
  start: z.number().int().nonnegative(),
  end: z.number().int().nonnegative(),
  section: soapSectionSchema.default('unsectioned'),
});

export const clinicalEntitySchema = textSpanSchema.extend({
  label: entityLabelSchema,
  normalized: z.string(),
  confidence: z.number().min(0).max(1),
  source: z.enum(['lexicon', 'ner', 'regex']).default('lexicon'),
  negated: z.boolean().default(false),
  uncertain: z.boolean().default(false),
});

export const demographicsSchema = z.object({
  age: z.number().int().min(0).max(130).nullable(),
  age_unit: z.enum(['years', 'months', 'days']).nullable(),
  sex: sexSchema.default('unknown'),
  confidence: z.number().min(0).max(1).default(0),
  evidence: textSpanSchema.nullish(),
});

export const conditionDurationSchema = z.object({
  value: z.number().positive(),
  unit: z.enum(['days', 'weeks', 'months', 'years']),
  normalized_weeks: z.number().positive(),
  confidence: z.number().min(0).max(1),
  evidence: textSpanSchema,
});

export const codeCandidateSchema = z.object({
  code: z.string(),
  system: z.enum(['ICD-10-CM', 'CPT']),
  description: z.string(),
  matched_text: z.string(),
  confidence: z.number().min(0).max(1),
  section: soapSectionSchema.default('unsectioned'),
  requires_laterality: z.boolean().default(false),
  laterality: lateralitySchema.nullish(),
});

export const scoreDriverSchema = z.object({
  key: z.string(),
  label: z.string(),
  delta: z.number(),
  detail: z.string(),
  satisfied: z.boolean(),
});

/**
 * How confidently the payer's own criteria answer this request.
 *
 * Deliberately separate from the approval band. The band stays green/amber/red,
 * while this records what the number rests on — "we evaluated this payer's
 * clause" and "this payer publishes nothing about this indication" are
 * indistinguishable in a score, and they call for different next steps.
 */
export const coverageStatusSchema = z.enum([
  'adjudicated',
  'excluded',
  'indication_not_addressed',
  'no_criteria_available',
]);

export const approvalAssessmentSchema = z.object({
  score: z.number().min(0).max(1),
  band: approvalBandSchema,
  rationale: z.string(),
  drivers: z.array(scoreDriverSchema).default([]),
  missing_elements: z.array(z.string()).default([]),
  basis: z.enum(['rule_engine', 'payer_rule', 'ml_model']).default('rule_engine'),
  payer_slug: z.string().nullish(),
  coverage_status: coverageStatusSchema.default('no_criteria_available'),
  matched_indication: z.string().nullish(),
  /** The payer's own sentence when a clause decided the outcome. */
  payer_quote: z.string().nullish(),
  /** Payer restrictions we could read but not scope: shown, never scored on. */
  advisories: z.array(z.string()).default([]),
  /** How the clause was selected: the note's language, its codes, or neither. */
  indication_match_method: z.enum(['text', 'icd10', 'none']).default('none'),
});

export const clinicalJustificationSchema = z.object({
  text: z.string(),
  generated_by: z.enum(['template', 'gpt-4o', 'gpt-4o-mini']).default('template'),
  citations: z.array(textSpanSchema).default([]),
});

export const soapSectionTextSchema = z.object({
  section: soapSectionSchema,
  text: z.string(),
  start: z.number().int(),
  end: z.number().int(),
});

export const extractionResultSchema = z.object({
  demographics: demographicsSchema,
  chief_complaint: z.string().nullish(),
  chief_complaint_span: textSpanSchema.nullish(),
  duration: conditionDurationSchema.nullish(),
  diagnoses: z.array(codeCandidateSchema).default([]),
  procedures: z.array(codeCandidateSchema).default([]),
  entities: z.array(clinicalEntitySchema).default([]),
  sections: z.array(soapSectionTextSchema).default([]),
  justification: clinicalJustificationSchema.nullish(),
});

export const scanResultSchema = z.object({
  scan_id: z.string(),
  extraction: extractionResultSchema,
  approval: approvalAssessmentSchema,
  model_version: z.string(),
  note_char_count: z.number().int(),
  note_sha256: z.string(),
});

export const extractRequestSchema = z.object({
  note: z.string().min(1, 'Paste or type a SOAP note first.'),
  payer_slug: z.string().nullish(),
  requested_cpt: z.string().nullish(),
  draft_justification: z.boolean().default(false),
});

export type SoapSection = z.infer<typeof soapSectionSchema>;
export type EntityLabel = z.infer<typeof entityLabelSchema>;
export type ApprovalBand = z.infer<typeof approvalBandSchema>;
export type Laterality = z.infer<typeof lateralitySchema>;
export type TextSpan = z.infer<typeof textSpanSchema>;
export type ClinicalEntity = z.infer<typeof clinicalEntitySchema>;
export type Demographics = z.infer<typeof demographicsSchema>;
export type ConditionDuration = z.infer<typeof conditionDurationSchema>;
export type CodeCandidate = z.infer<typeof codeCandidateSchema>;
export type ScoreDriver = z.infer<typeof scoreDriverSchema>;
export type CoverageStatus = z.infer<typeof coverageStatusSchema>;
export type ApprovalAssessment = z.infer<typeof approvalAssessmentSchema>;
export type ClinicalJustification = z.infer<typeof clinicalJustificationSchema>;
export type SoapSectionText = z.infer<typeof soapSectionTextSchema>;
export type ExtractionResult = z.infer<typeof extractionResultSchema>;
export type ScanResult = z.infer<typeof scanResultSchema>;
export type ExtractRequest = z.input<typeof extractRequestSchema>;

/**
 * The approval-band thresholds, defined once.
 *
 * Green ≥ 80%, amber 50–79%, red below 50% — the same cut points the Python
 * rule engine bands on, so the badge can never disagree with the service.
 */
export const APPROVAL_THRESHOLDS = { green: 0.8, amber: 0.5 } as const;

export function bandForScore(score: number): ApprovalBand {
  if (score >= APPROVAL_THRESHOLDS.green) return 'green';
  if (score >= APPROVAL_THRESHOLDS.amber) return 'amber';
  return 'red';
}
