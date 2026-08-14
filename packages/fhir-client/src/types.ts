/**
 * FHIR R4 resource shapes, narrowed to the fields Clinchec reads.
 *
 * Hand-written rather than generated from the full R4 schema: the generated
 * types are enormous and every field they add is a field a reviewer has to
 * confirm we do not touch.
 */

export interface FhirCoding {
  system?: string;
  code?: string;
  display?: string;
}

export interface FhirCodeableConcept {
  coding?: FhirCoding[];
  text?: string;
}

export interface FhirReference {
  reference?: string;
  display?: string;
  identifier?: FhirIdentifier;
}

export interface FhirIdentifier {
  system?: string;
  value?: string;
  type?: FhirCodeableConcept;
}

export interface FhirHumanName {
  use?: string;
  text?: string;
  family?: string;
  given?: string[];
  prefix?: string[];
  suffix?: string[];
}

export interface FhirContactPoint {
  system?: 'phone' | 'fax' | 'email' | 'url' | 'sms' | 'other';
  value?: string;
  use?: string;
}

export interface FhirAddress {
  line?: string[];
  city?: string;
  state?: string;
  postalCode?: string;
  country?: string;
}

export interface FhirPeriod {
  start?: string;
  end?: string;
}

export interface FhirPatient {
  resourceType: 'Patient';
  id: string;
  identifier?: FhirIdentifier[];
  name?: FhirHumanName[];
  telecom?: FhirContactPoint[];
  gender?: 'male' | 'female' | 'other' | 'unknown';
  birthDate?: string;
  address?: FhirAddress[];
}

export interface FhirPractitioner {
  resourceType: 'Practitioner';
  id: string;
  identifier?: FhirIdentifier[];
  name?: FhirHumanName[];
  telecom?: FhirContactPoint[];
  qualification?: { code?: FhirCodeableConcept }[];
}

export interface FhirCondition {
  resourceType: 'Condition';
  id: string;
  clinicalStatus?: FhirCodeableConcept;
  verificationStatus?: FhirCodeableConcept;
  category?: FhirCodeableConcept[];
  code?: FhirCodeableConcept;
  subject?: FhirReference;
  onsetDateTime?: string;
  onsetPeriod?: FhirPeriod;
  recordedDate?: string;
}

export interface FhirCoverage {
  resourceType: 'Coverage';
  id: string;
  status?: string;
  type?: FhirCodeableConcept;
  subscriberId?: string;
  beneficiary?: FhirReference;
  payor?: FhirReference[];
  class?: { type?: FhirCodeableConcept; value?: string; name?: string }[];
  period?: FhirPeriod;
}

export interface FhirAttachment {
  contentType?: string;
  data?: string;
  url?: string;
  title?: string;
  creation?: string;
}

export interface FhirDocumentReference {
  resourceType: 'DocumentReference';
  id: string;
  status?: string;
  type?: FhirCodeableConcept;
  category?: FhirCodeableConcept[];
  subject?: FhirReference;
  date?: string;
  author?: FhirReference[];
  content?: { attachment: FhirAttachment }[];
  context?: { encounter?: FhirReference[]; period?: FhirPeriod };
}

export interface FhirBundleEntry<T> {
  fullUrl?: string;
  resource?: T;
}

export interface FhirBundle<T> {
  resourceType: 'Bundle';
  type?: string;
  total?: number;
  link?: { relation: string; url: string }[];
  entry?: FhirBundleEntry<T>[];
}

// ---------------------------------------------------------------------------
// Projection helpers — FHIR resource → the flat shapes Clinchec Forms wants
// ---------------------------------------------------------------------------

export function formatHumanName(name?: FhirHumanName[]): {
  first?: string;
  last?: string;
  full?: string;
} {
  const official = name?.find((entry) => entry.use === 'official') ?? name?.[0];
  if (!official) return {};
  const first = official.given?.[0];
  const last = official.family;
  return {
    first,
    last,
    full: official.text ?? ([first, last].filter(Boolean).join(' ') || undefined),
  };
}

export function findTelecom(
  telecom: FhirContactPoint[] | undefined,
  system: FhirContactPoint['system'],
): string | undefined {
  return telecom?.find((entry) => entry.system === system)?.value;
}

/** Pull the NPI out of a Practitioner's identifiers. */
export function findNpi(identifiers?: FhirIdentifier[]): string | undefined {
  return identifiers?.find(
    (identifier) =>
      identifier.system === 'http://hl7.org/fhir/sid/us-npi' ||
      identifier.type?.coding?.some((coding) => coding.code === 'NPI'),
  )?.value;
}

/** Pull ICD-10-CM codes out of a Condition, ignoring SNOMED and local codes. */
export function findIcd10Codes(condition: FhirCondition): string[] {
  return (condition.code?.coding ?? [])
    .filter((coding) => coding.system?.includes('icd-10') || coding.system?.includes('sid/icd-10'))
    .map((coding) => coding.code)
    .filter((code): code is string => Boolean(code));
}

/** The payer name on a Coverage, for pre-selecting the right rule set. */
export function findPayerName(coverage: FhirCoverage): string | undefined {
  return coverage.payor?.[0]?.display;
}

/**
 * Best-effort map from a payer's display name to a Clinchec payer slug.
 *
 * Deliberately conservative — returning `undefined` makes the UI ask the
 * clinician which payer this is, which is far better than silently scoring a
 * request against the wrong insurer's criteria.
 */
export function payerSlugFromName(name?: string): 'aetna' | 'bcbs' | 'uhc' | undefined {
  if (!name) return undefined;
  const normalized = name.toLowerCase();
  if (normalized.includes('aetna')) return 'aetna';
  if (normalized.includes('unitedhealth') || normalized.includes('uhc')) return 'uhc';
  if (
    normalized.includes('blue cross') ||
    normalized.includes('bluecross') ||
    normalized.includes('anthem') ||
    /\bbcbs\b/.test(normalized)
  ) {
    return 'bcbs';
  }
  return undefined;
}
