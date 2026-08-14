import { NextResponse, type NextRequest } from 'next/server';

import { buildAuthorizeUrl, SmartAuthError } from '@clinchec/fhir-client';

import { getLaunchConfig } from '@/lib/fhir';
import { savePkceState } from '@/lib/session';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

/**
 * SMART on FHIR launch endpoint.
 *
 * Registered with the EHR as the app's launch URL. Epic and Cerner call it as
 *
 *     GET /api/auth/launch?iss=<fhir-base>&launch=<opaque-token>
 *
 * for an EHR launch, and without `launch` for a standalone launch from our own
 * login page.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const iss = params.get('iss') ?? process.env.FHIR_ISSUER;
  const launch = params.get('launch') ?? undefined;
  const returnTo = params.get('return_to') ?? '/dashboard';

  if (!iss) {
    return problem(
      'missing_iss',
      'No FHIR issuer supplied. An EHR launch must include ?iss=<fhir-base-url>, ' +
        'or FHIR_ISSUER must be configured for standalone launch.',
      400,
    );
  }

  if (!isAllowedIssuer(iss)) {
    // An attacker-supplied `iss` would send our client credentials and the
    // resulting code to a server they control. Only registered EHRs are valid.
    return problem(
      'issuer_not_allowed',
      'That FHIR issuer is not registered with this Clinchec deployment.',
      403,
    );
  }

  try {
    const config = getLaunchConfig(iss);
    const scopes = launch
      ? config.scopes
      : // Standalone launch has no EHR-supplied context, so the patient is
        // chosen at the authorize screen instead.
        config.scopes.map((scope) => (scope === 'launch' ? 'launch/patient' : scope));

    const redirect = await buildAuthorizeUrl({ ...config, scopes }, { launch });

    await savePkceState({
      state: redirect.state,
      codeVerifier: redirect.codeVerifier,
      iss,
      returnTo,
    });

    return NextResponse.redirect(redirect.url);
  } catch (error) {
    if (error instanceof SmartAuthError) {
      return problem(error.code, error.message, error.status ?? 502);
    }
    console.error('SMART launch failed', error);
    return problem('launch_failed', 'Could not start the SMART on FHIR launch.', 500);
  }
}

/**
 * Issuer allowlist.
 *
 * `FHIR_ALLOWED_ISSUERS` is a comma-separated list of FHIR base URLs or host
 * suffixes. When unset, only the configured `FHIR_ISSUER` is accepted, which
 * is the correct default for a single-tenant deployment.
 */
function isAllowedIssuer(iss: string): boolean {
  const configured = process.env.FHIR_ALLOWED_ISSUERS ?? process.env.FHIR_ISSUER ?? '';
  const allowed = configured
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);

  if (allowed.length === 0) return false;

  let host: string;
  try {
    const url = new URL(iss);
    if (url.protocol !== 'https:' && url.hostname !== 'localhost') return false;
    host = url.hostname;
  } catch {
    return false;
  }

  return allowed.some((entry) => {
    try {
      return new URL(entry).hostname === host;
    } catch {
      return host === entry || host.endsWith(`.${entry.replace(/^\./, '')}`);
    }
  });
}

function problem(code: string, message: string, status: number) {
  return NextResponse.json(
    { data: null, error: { code, message }, meta: { service: 'clinchec-web' } },
    { status },
  );
}
