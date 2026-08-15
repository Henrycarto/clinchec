import { z } from 'zod';

/** Mirrors `services/live/app/schemas.py`. */

export const planTypeSchema = z.enum([
  'commercial',
  'medicare_advantage',
  'medicaid',
  'exchange',
  'any',
]);

export const crawlStatusSchema = z.enum(['running', 'succeeded', 'failed', 'skipped']);

export const polaritySchema = z.enum(['covered', 'excluded']);

/**
 * One indication-scoped adjudication within a payer rule.
 *
 * A bulletin can both cover and exclude the same CPT depending on why it is
 * being requested — Aetna CPB 0673 approves partial meniscectomy for mechanical
 * symptoms with mild osteoarthritis and calls it experimental for meniscal root
 * tears. A single `requires_pa` flag cannot represent that.
 */
export const ruleClauseSchema = z.object({
  polarity: polaritySchema,
  /** The payer's own wording — quoted verbatim on appeal, never paraphrased. */
  indication_text: z.string(),
  indication_icd10_prefixes: z.array(z.string()).default([]),
  required_duration_weeks: z.number().int().nullish(),
  required_conservative_care: z.array(z.string()).default([]),
  required_imaging: z.array(z.string()).default([]),
  source_pattern: z.string().default('unknown'),
  source_snippet: z.string().default(''),
  /** Indication could not be resolved to codes: shown, never scored on. */
  advisory: z.boolean().default(false),
});

export const payerRuleSchema = z.object({
  payer_slug: z.string(),
  payer_name: z.string(),
  cpt_code: z.string(),
  icd10_codes: z.array(z.string()).default([]),
  plan_type: planTypeSchema.default('any'),
  requires_pa: z.boolean().default(true),
  criteria_text: z.string(),
  required_duration_weeks: z.number().int().nullish(),
  required_conservative_care: z.array(z.string()).default([]),
  required_imaging: z.array(z.string()).default([]),
  source_url: z.string().nullish(),
  effective_date: z.string().nullish(),
  clauses: z.array(ruleClauseSchema).default([]),
  last_verified_at: z.string(),
  staleness_hours: z.number().default(0),
});

export const payerSummarySchema = z.object({
  slug: z.string(),
  display_name: z.string(),
  portal_base_url: z.string().nullish(),
  rule_count: z.number().int().default(0),
  /** Rules withdrawn because their source document stopped being published. */
  retired_rule_count: z.number().int().default(0),
  last_crawled_at: z.string().nullish(),
  last_crawl_status: crawlStatusSchema.nullish(),
});

export type PlanType = z.infer<typeof planTypeSchema>;
export type CrawlStatus = z.infer<typeof crawlStatusSchema>;
export type Polarity = z.infer<typeof polaritySchema>;
export type RuleClause = z.infer<typeof ruleClauseSchema>;
export type PayerRule = z.infer<typeof payerRuleSchema>;
export type PayerSummary = z.infer<typeof payerSummarySchema>;

/** A rule nobody has re-verified in this long is shown with a staleness warning. */
export const STALE_RULE_HOURS = 24 * 30;

export function isStale(rule: Pick<PayerRule, 'staleness_hours'>): boolean {
  return rule.staleness_hours > STALE_RULE_HOURS;
}
