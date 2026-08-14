import { z } from 'zod';

import { lateralitySchema } from './scan';

/** Mirrors `services/forms/app/schemas.py`. */

export const fieldTypeSchema = z.enum([
  'text',
  'textarea',
  'date',
  'number',
  'select',
  'checkbox',
  'phone',
  'npi',
]);

export const submissionChannelSchema = z.enum([
  'portal',
  'fax',
  'x12_278',
  'fhir_crd',
  'export',
]);

export const submissionStatusSchema = z.enum([
  'draft',
  'ready',
  'submitted',
  'exported',
  'blocked',
]);

export const mappingConfidenceSchema = z.enum(['exact', 'derived', 'inferred', 'missing']);

export const formFieldSchema = z.object({
  key: z.string(),
  label: z.string(),
  type: fieldTypeSchema.default('text'),
  required: z.boolean().default(false),
  source: z.string().nullish(),
  transform: z.string().nullish(),
  options: z.array(z.string()).default([]),
  max_length: z.number().int().nullish(),
  help_text: z.string().nullish(),
  section: z.string().default('General'),
});

export const formDefinitionSchema = z.object({
  form_key: z.string(),
  payer_slug: z.string(),
  display_name: z.string(),
  version: z.string(),
  cpt_codes: z.array(z.string()).default([]),
  channel: submissionChannelSchema.default('export'),
  fields: z.array(formFieldSchema).default([]),
  notes: z.string().nullish(),
});

export const mappedFieldSchema = z.object({
  key: z.string(),
  label: z.string(),
  type: fieldTypeSchema,
  section: z.string(),
  required: z.boolean(),
  value: z.unknown().nullish(),
  confidence: mappingConfidenceSchema.default('missing'),
  source: z.string().nullish(),
  note: z.string().nullish(),
});

export const mappingResultSchema = z.object({
  form_key: z.string(),
  payer_slug: z.string(),
  display_name: z.string(),
  channel: submissionChannelSchema,
  fields: z.array(mappedFieldSchema).default([]),
  values: z.record(z.unknown()).default({}),
  missing_required: z.array(z.string()).default([]),
  needs_review: z.array(z.string()).default([]),
  completeness: z.number().min(0).max(1).default(0),
  ready_to_submit: z.boolean().default(false),
});

export const submissionResultSchema = z.object({
  pa_request_id: z.string(),
  form_key: z.string(),
  payer_slug: z.string(),
  status: submissionStatusSchema,
  channel: submissionChannelSchema,
  mapping: mappingResultSchema,
  submission_ref: z.string().nullish(),
  export_url: z.string().nullish(),
  message: z.string(),
});

// --- Request payloads ------------------------------------------------------

export const patientPayloadSchema = z.object({
  first_name: z.string().nullish(),
  last_name: z.string().nullish(),
  date_of_birth: z.string().nullish(),
  sex: z.enum(['male', 'female', 'other', 'unknown']).default('unknown'),
  member_id: z.string().nullish(),
  group_number: z.string().nullish(),
  phone: z.string().nullish(),
  address_line1: z.string().nullish(),
  city: z.string().nullish(),
  state: z.string().nullish(),
  postal_code: z.string().nullish(),
});

export const providerPayloadSchema = z.object({
  name: z.string().nullish(),
  npi: z.string().nullish(),
  tax_id: z.string().nullish(),
  specialty: z.string().nullish(),
  phone: z.string().nullish(),
  fax: z.string().nullish(),
  facility_name: z.string().nullish(),
});

export const clinicalPayloadSchema = z.object({
  primary_icd10: z.string().nullish(),
  icd10_codes: z.array(z.string()).default([]),
  diagnosis_description: z.string().nullish(),
  cpt_code: z.string().nullish(),
  procedure_description: z.string().nullish(),
  laterality: lateralitySchema.nullish(),
  symptom_duration_weeks: z.number().nullish(),
  conservative_care: z.array(z.string()).default([]),
  prior_imaging: z.array(z.string()).default([]),
  functional_impairment: z.array(z.string()).default([]),
  red_flags: z.array(z.string()).default([]),
  clinical_justification: z.string().nullish(),
  requested_start_date: z.string().nullish(),
  units_requested: z.number().int().default(1),
  place_of_service: z.string().nullish(),
  urgency: z.enum(['routine', 'urgent']).default('routine'),
});

export const paPayloadSchema = z.object({
  payer_slug: z.string(),
  patient: patientPayloadSchema.default({ sex: 'unknown' }),
  provider: providerPayloadSchema.default({}),
  clinical: clinicalPayloadSchema.default({
    icd10_codes: [],
    conservative_care: [],
    prior_imaging: [],
    functional_impairment: [],
    red_flags: [],
    units_requested: 1,
    urgency: 'routine',
  }),
  scan_id: z.string().nullish(),
});

export type FieldType = z.infer<typeof fieldTypeSchema>;
export type SubmissionChannel = z.infer<typeof submissionChannelSchema>;
export type SubmissionStatus = z.infer<typeof submissionStatusSchema>;
export type MappingConfidence = z.infer<typeof mappingConfidenceSchema>;
export type FormField = z.infer<typeof formFieldSchema>;
export type FormDefinition = z.infer<typeof formDefinitionSchema>;
export type MappedField = z.infer<typeof mappedFieldSchema>;
export type MappingResult = z.infer<typeof mappingResultSchema>;
export type SubmissionResult = z.infer<typeof submissionResultSchema>;
export type PatientPayload = z.infer<typeof patientPayloadSchema>;
export type ProviderPayload = z.infer<typeof providerPayloadSchema>;
export type ClinicalPayload = z.infer<typeof clinicalPayloadSchema>;
export type PaPayload = z.infer<typeof paPayloadSchema>;
