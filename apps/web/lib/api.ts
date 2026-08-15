import {
  ClinchecApiError,
  envelopeSchema,
  formDefinitionSchema,
  mappingResultSchema,
  payerRuleSchema,
  payerSummarySchema,
  scanResultSchema,
  submissionResultSchema,
  type Envelope,
  type ExtractRequest,
  type FormDefinition,
  type MappingResult,
  type PaPayload,
  type PayerRule,
  type PayerSummary,
  type ScanResult,
  type SubmissionResult,
} from '@clinchec/shared-types';
import { z } from 'zod';

/**
 * Typed client for the three Clinchec services.
 *
 * Every response is parsed through the Zod mirror of the service's Pydantic
 * model. A shape mismatch fails loudly at the boundary with the request id
 * attached, rather than propagating `undefined` into a component that renders
 * a clinical judgement.
 */

/**
 * Where a service lives, which depends on who is asking.
 *
 * A browser reaches the services through their public URL. A server component
 * reaches them from inside the network, where the public URL is wrong in a way
 * that fails quietly: `http://localhost:8002` resolves to the web container
 * itself, the fetch is refused, and a page that degrades gracefully on error
 * renders as though the service had simply nothing to say.
 */
function serviceUrl(internal: string | undefined, browser: string | undefined, fallback: string) {
  if (typeof window === 'undefined' && internal) return internal;
  return browser ?? fallback;
}

const SCAN_URL = serviceUrl(
  process.env.SCAN_SERVICE_URL,
  process.env.NEXT_PUBLIC_SCAN_API_URL,
  'http://localhost:8001',
);
const LIVE_URL = serviceUrl(
  process.env.LIVE_SERVICE_URL,
  process.env.NEXT_PUBLIC_LIVE_API_URL,
  'http://localhost:8002',
);
const FORMS_URL = serviceUrl(
  process.env.FORMS_SERVICE_URL,
  process.env.NEXT_PUBLIC_FORMS_API_URL,
  'http://localhost:8003',
);

export interface RequestOptions {
  signal?: AbortSignal;
  /** Propagated to the services so one clinician action traces end to end. */
  requestId?: string;
}

async function call<T extends z.ZodTypeAny>(
  url: string,
  schema: T,
  init: RequestInit,
  options: RequestOptions = {},
): Promise<z.infer<T>> {
  let response: Response;

  try {
    response = await fetch(url, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        ...(options.requestId ? { 'X-Request-ID': options.requestId } : {}),
        ...init.headers,
      },
      signal: options.signal,
      cache: 'no-store',
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ClinchecApiError(
      'network_error',
      'Could not reach the Clinchec service. Check that the local stack is running.',
      { url },
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ClinchecApiError(
      'invalid_response',
      `The service returned a non-JSON response (${response.status}).`,
      { url },
      response.status,
    );
  }

  const parsed = envelopeSchema(schema).safeParse(body);

  if (!parsed.success) {
    // An enveloped error whose `data` is null still parses as an error shape;
    // only surface a schema complaint when the payload is genuinely unexpected.
    const envelope = body as Partial<Envelope<unknown>>;
    if (envelope?.error) {
      throw new ClinchecApiError(
        envelope.error.code,
        envelope.error.message,
        envelope.error.details,
        response.status,
        envelope.meta?.request_id,
      );
    }
    throw new ClinchecApiError(
      'schema_mismatch',
      'The service returned a response Clinchec does not recognise. ' +
        'This usually means the frontend and a service are on different versions.',
      { issues: parsed.error.issues.slice(0, 5) },
      response.status,
    );
  }

  const envelope = parsed.data as Envelope<z.infer<T>>;

  if (envelope.error) {
    throw new ClinchecApiError(
      envelope.error.code,
      envelope.error.message,
      envelope.error.details,
      response.status,
      envelope.meta.request_id,
    );
  }

  if (envelope.data === null) {
    throw new ClinchecApiError(
      'empty_response',
      'The service returned no data and no error.',
      undefined,
      response.status,
      envelope.meta.request_id,
    );
  }

  return envelope.data;
}

// ---------------------------------------------------------------------------
// Clinchec Scan
// ---------------------------------------------------------------------------

export async function extractSoapNote(
  request: ExtractRequest,
  options?: RequestOptions,
): Promise<ScanResult> {
  return call(
    `${SCAN_URL}/extract`,
    scanResultSchema,
    { method: 'POST', body: JSON.stringify(request) },
    options,
  );
}

// ---------------------------------------------------------------------------
// Clinchec Live
// ---------------------------------------------------------------------------

export async function listPayers(options?: RequestOptions): Promise<PayerSummary[]> {
  return call(`${LIVE_URL}/payers`, z.array(payerSummarySchema), { method: 'GET' }, options);
}

export async function getPayerRule(
  payerSlug: string,
  cptCode: string,
  options?: RequestOptions,
): Promise<PayerRule> {
  return call(
    `${LIVE_URL}/rules/${encodeURIComponent(payerSlug)}/${encodeURIComponent(cptCode)}`,
    payerRuleSchema,
    { method: 'GET' },
    options,
  );
}

// ---------------------------------------------------------------------------
// Clinchec Forms
// ---------------------------------------------------------------------------

export async function resolveForm(
  payerSlug: string,
  cptCode?: string,
  options?: RequestOptions,
): Promise<FormDefinition> {
  const params = new URLSearchParams({ payer_slug: payerSlug });
  if (cptCode) params.set('cpt_code', cptCode);
  return call(
    `${FORMS_URL}/forms/resolve?${params}`,
    formDefinitionSchema,
    { method: 'GET' },
    options,
  );
}

export async function populateForm(
  payload: PaPayload,
  formKey?: string,
  options?: RequestOptions,
): Promise<MappingResult> {
  return call(
    `${FORMS_URL}/populate`,
    mappingResultSchema,
    { method: 'POST', body: JSON.stringify({ payload, form_key: formKey ?? null }) },
    options,
  );
}

export async function submitForm(
  payload: PaPayload,
  args: {
    formKey?: string;
    overrides?: Record<string, unknown>;
    allowIncomplete?: boolean;
  } = {},
  options?: RequestOptions,
): Promise<SubmissionResult> {
  return call(
    `${FORMS_URL}/submit`,
    submissionResultSchema,
    {
      method: 'POST',
      body: JSON.stringify({
        payload,
        form_key: args.formKey ?? null,
        overrides: args.overrides ?? {},
        allow_incomplete: args.allowIncomplete ?? false,
      }),
    },
    options,
  );
}

export { ClinchecApiError };
