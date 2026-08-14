/**
 * Minimal FHIR R4 client scoped to what Clinchec reads.
 *
 * Deliberately not a general-purpose FHIR library: Clinchec reads a patient,
 * their active conditions, their coverage, and their clinical notes. A narrow
 * client is auditable — you can see on one screen exactly which PHI the app is
 * capable of requesting, which is the first question every hospital security
 * review asks.
 */

import { ensureValidSession, type SmartLaunchConfig, type SmartSession } from './smart';
import type {
  FhirBundle,
  FhirCondition,
  FhirCoverage,
  FhirDocumentReference,
  FhirPatient,
  FhirPractitioner,
} from './types';

export class FhirRequestError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly operationOutcome?: unknown,
  ) {
    super(message);
    this.name = 'FhirRequestError';
  }
}

export interface FhirClientOptions {
  config: SmartLaunchConfig;
  session: SmartSession;
  /** Called whenever a refresh produces a new session, so it can be persisted. */
  onSessionRefreshed?: (session: SmartSession) => void | Promise<void>;
}

export class FhirClient {
  private session: SmartSession;

  constructor(private readonly options: FhirClientOptions) {
    this.session = options.session;
  }

  get patientId(): string | undefined {
    return this.session.patientId;
  }

  get currentSession(): SmartSession {
    return this.session;
  }

  /** Raw resource read. Refreshes the token first when it has aged out. */
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const refreshed = await ensureValidSession(this.options.config, this.session);
    if (refreshed !== this.session) {
      this.session = refreshed;
      await this.options.onSessionRefreshed?.(refreshed);
    }

    const url = path.startsWith('http')
      ? path
      : `${this.session.iss.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;

    const response = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/fhir+json',
        Authorization: `Bearer ${this.session.accessToken}`,
        ...init.headers,
      },
      cache: 'no-store',
    });

    if (!response.ok) {
      let outcome: unknown;
      try {
        outcome = await response.json();
      } catch {
        outcome = await response.text().catch(() => undefined);
      }
      throw new FhirRequestError(
        response.status,
        `FHIR request failed: ${init.method ?? 'GET'} ${path} → ${response.status}`,
        outcome,
      );
    }

    return (await response.json()) as T;
  }

  // -- Reads ------------------------------------------------------------

  async getPatient(patientId = this.session.patientId): Promise<FhirPatient> {
    if (!patientId) {
      throw new FhirRequestError(400, 'No patient in the launch context and none supplied.');
    }
    return this.request<FhirPatient>(`Patient/${patientId}`);
  }

  /** The launching clinician, resolved from the `fhirUser` claim. */
  async getPractitioner(): Promise<FhirPractitioner | null> {
    const fhirUser = this.session.fhirUser;
    if (!fhirUser) return null;
    const reference = fhirUser.startsWith('http')
      ? fhirUser
      : fhirUser.replace(/^\/+/, '');
    return this.request<FhirPractitioner>(reference);
  }

  /**
   * Active problem-list conditions.
   *
   * Scoped to `active` clinical status on purpose: a resolved condition from
   * 2014 is not an indication, and including it would inflate the diagnosis
   * list Scan reconciles against.
   */
  async getActiveConditions(patientId = this.session.patientId): Promise<FhirCondition[]> {
    if (!patientId) return [];
    const bundle = await this.request<FhirBundle<FhirCondition>>(
      `Condition?patient=${encodeURIComponent(patientId)}` +
        `&clinical-status=active&_count=100`,
    );
    return unwrapBundle(bundle);
  }

  async getCoverage(patientId = this.session.patientId): Promise<FhirCoverage[]> {
    if (!patientId) return [];
    const bundle = await this.request<FhirBundle<FhirCoverage>>(
      `Coverage?patient=${encodeURIComponent(patientId)}&status=active`,
    );
    return unwrapBundle(bundle);
  }

  /**
   * Clinical notes, newest first.
   *
   * This is the SOAP-note source for Scan. `type` filters to progress notes
   * (LOINC 11506-3) and consult notes (11488-4) — pulling every
   * DocumentReference would return scanned faxes and discharge paperwork.
   */
  async getClinicalNotes(
    patientId = this.session.patientId,
    limit = 10,
  ): Promise<FhirDocumentReference[]> {
    if (!patientId) return [];
    const bundle = await this.request<FhirBundle<FhirDocumentReference>>(
      `DocumentReference?patient=${encodeURIComponent(patientId)}` +
        `&type=http://loinc.org|11506-3,http://loinc.org|11488-4` +
        `&_sort=-date&_count=${limit}`,
    );
    return unwrapBundle(bundle);
  }

  /** Fetch and decode a note's text content. */
  async getNoteText(document: FhirDocumentReference): Promise<string | null> {
    const attachment = document.content?.[0]?.attachment;
    if (!attachment) return null;

    if (attachment.data) {
      // FHIR inlines base64; Epic usually does for text/plain notes.
      try {
        return decodeBase64(attachment.data);
      } catch {
        return null;
      }
    }

    if (attachment.url) {
      const refreshed = await ensureValidSession(this.options.config, this.session);
      this.session = refreshed;
      const response = await fetch(
        attachment.url.startsWith('http')
          ? attachment.url
          : `${this.session.iss.replace(/\/+$/, '')}/${attachment.url.replace(/^\/+/, '')}`,
        {
          headers: {
            Authorization: `Bearer ${this.session.accessToken}`,
            Accept: attachment.contentType ?? 'text/plain',
          },
          cache: 'no-store',
        },
      );
      if (!response.ok) return null;
      return response.text();
    }

    return null;
  }
}

function unwrapBundle<T>(bundle: FhirBundle<T>): T[] {
  return (bundle.entry ?? [])
    .map((entry) => entry.resource)
    .filter((resource): resource is T => Boolean(resource));
}

function decodeBase64(value: string): string {
  if (typeof Buffer !== 'undefined') {
    return Buffer.from(value, 'base64').toString('utf-8');
  }
  return new TextDecoder().decode(
    Uint8Array.from(atob(value), (char) => char.charCodeAt(0)),
  );
}
