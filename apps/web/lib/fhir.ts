import 'server-only';

import {
  FhirClient,
  findIcd10Codes,
  findNpi,
  findPayerName,
  findTelecom,
  formatHumanName,
  payerSlugFromName,
  type SmartLaunchConfig,
  type SmartSession,
} from '@clinchec/fhir-client';
import type { PatientPayload, ProviderPayload } from '@clinchec/shared-types';

import { getSession, saveSession } from './session';

/**
 * SMART on FHIR wiring for the web app.
 *
 * Everything in this module is server-only. The launch config comes from the
 * environment rather than being hardcoded per customer, because a single
 * deployment serves multiple EHR tenants and `iss` arrives with the launch.
 */

export function getLaunchConfig(iss: string): SmartLaunchConfig {
  const clientId = process.env.FHIR_CLIENT_ID;
  const redirectUri = process.env.FHIR_REDIRECT_URI;

  if (!clientId) {
    throw new Error(
      'FHIR_CLIENT_ID is not set. Register the app in the Epic App Orchard or ' +
        'Cerner Code console and set the credentials in .env.',
    );
  }
  if (!redirectUri) {
    throw new Error('FHIR_REDIRECT_URI is not set.');
  }

  return {
    iss,
    clientId,
    clientSecret: process.env.FHIR_CLIENT_SECRET || undefined,
    redirectUri,
    scopes: (
      process.env.FHIR_SCOPES ??
      'openid fhirUser launch patient/Patient.read patient/Condition.read ' +
        'patient/Coverage.read patient/DocumentReference.read offline_access'
    )
      .split(/\s+/)
      .filter(Boolean),
  };
}

/**
 * Build a FHIR client from the current session.
 *
 * Refreshed tokens are written straight back to the session cookie, so a long
 * clinic session never surfaces an expiry to the user.
 */
export async function getFhirClient(): Promise<FhirClient | null> {
  const session = await getSession();
  if (!session) return null;

  // A dev-bypass session holds a placeholder token and an unresolvable issuer.
  // Returning null here means callers take their existing "no EHR context"
  // path rather than issuing a request that would fail at DNS.
  if (session.dev) return null;

  return new FhirClient({
    config: getLaunchConfig(session.smart.iss),
    session: session.smart,
    onSessionRefreshed: async (smart: SmartSession) => {
      await saveSession({ ...session, smart });
    },
  });
}

// ---------------------------------------------------------------------------
// Projections into the Forms payload shape
// ---------------------------------------------------------------------------

export interface LaunchContext {
  patient: PatientPayload;
  provider: ProviderPayload;
  payerSlug?: 'aetna' | 'bcbs' | 'uhc';
  payerName?: string;
  conditionCodes: string[];
  latestNote?: { id: string; title?: string; date?: string; text: string };
}

/**
 * Pull everything the PA form needs out of the EHR in one pass.
 *
 * Each resource is fetched independently and a failure on any one degrades
 * that section rather than the whole launch — a practice whose Coverage
 * resources are not exposed should still get patient demographics
 * auto-filled instead of an error page.
 */
export async function loadLaunchContext(client: FhirClient): Promise<LaunchContext> {
  const context: LaunchContext = {
    patient: { sex: 'unknown' },
    provider: {},
    conditionCodes: [],
  };

  const [patient, practitioner, conditions, coverages, notes] = await Promise.allSettled([
    client.getPatient(),
    client.getPractitioner(),
    client.getActiveConditions(),
    client.getCoverage(),
    client.getClinicalNotes(undefined, 5),
  ]);

  if (patient.status === 'fulfilled') {
    const name = formatHumanName(patient.value.name);
    const address = patient.value.address?.[0];
    context.patient = {
      first_name: name.first,
      last_name: name.last,
      date_of_birth: patient.value.birthDate,
      sex: patient.value.gender ?? 'unknown',
      phone: findTelecom(patient.value.telecom, 'phone'),
      address_line1: address?.line?.[0],
      city: address?.city,
      state: address?.state,
      postal_code: address?.postalCode,
    };
  }

  if (practitioner.status === 'fulfilled' && practitioner.value) {
    const name = formatHumanName(practitioner.value.name);
    context.provider = {
      name: name.full,
      npi: findNpi(practitioner.value.identifier),
      specialty: practitioner.value.qualification?.[0]?.code?.text,
      phone: findTelecom(practitioner.value.telecom, 'phone'),
      fax: findTelecom(practitioner.value.telecom, 'fax'),
    };
  }

  if (conditions.status === 'fulfilled') {
    context.conditionCodes = conditions.value.flatMap(findIcd10Codes);
  }

  if (coverages.status === 'fulfilled' && coverages.value.length > 0) {
    const coverage = coverages.value[0]!;
    context.payerName = findPayerName(coverage);
    context.payerSlug = payerSlugFromName(context.payerName);
    context.patient.member_id = coverage.subscriberId;
    context.patient.group_number = coverage.class?.find((entry) =>
      entry.type?.coding?.some((coding) => coding.code === 'group'),
    )?.value;
  }

  if (notes.status === 'fulfilled' && notes.value.length > 0) {
    const document = notes.value[0]!;
    const text = await client.getNoteText(document).catch(() => null);
    if (text) {
      context.latestNote = {
        id: document.id,
        title: document.content?.[0]?.attachment?.title ?? document.type?.text,
        date: document.date,
        text,
      };
    }
  }

  return context;
}
