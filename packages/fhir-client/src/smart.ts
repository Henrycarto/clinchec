/**
 * SMART on FHIR authorization — real OAuth 2.0, Epic and Cerner compatible.
 *
 * Implements the SMART App Launch Framework 2.0 EHR-launch and standalone-launch
 * flows with PKCE (S256), which both Epic and Cerner now require for public
 * clients and which Epic requires even for confidential ones.
 *
 * The flow this file implements:
 *
 *   1. The EHR launches us at `/launch?iss=<fhir-base>&launch=<opaque>`.
 *   2. We fetch `<iss>/.well-known/smart-configuration` to discover the
 *      authorize and token endpoints. Discovery is mandatory — endpoints are
 *      never hardcoded, because a single build serves every customer's EHR.
 *   3. We redirect to `authorize` with the `launch` token, our scopes, a
 *      cryptographically random `state`, and a PKCE challenge.
 *   4. The EHR redirects back with a code; we exchange it at `token` together
 *      with the PKCE verifier.
 *   5. The token response carries `access_token`, `patient`, `encounter` and
 *      (with `offline_access`) a `refresh_token`.
 *
 * Everything here runs server-side in Next.js route handlers. The access token
 * is PHI-adjacent and never reaches the browser.
 */

export interface SmartConfiguration {
  authorization_endpoint: string;
  token_endpoint: string;
  introspection_endpoint?: string;
  revocation_endpoint?: string;
  capabilities?: string[];
  code_challenge_methods_supported?: string[];
  scopes_supported?: string[];
  issuer?: string;
  jwks_uri?: string;
}

export interface SmartLaunchConfig {
  /** FHIR base URL of the EHR — the `iss` parameter from the launch. */
  iss: string;
  clientId: string;
  /** Only set for confidential clients (Epic backend-registered apps). */
  clientSecret?: string;
  redirectUri: string;
  scopes: string[];
}

export interface AuthorizeRedirect {
  url: string;
  state: string;
  codeVerifier: string;
}

export interface SmartTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  scope: string;
  refresh_token?: string;
  id_token?: string;
  /** SMART launch context — the patient this session is scoped to. */
  patient?: string;
  encounter?: string;
  fhirUser?: string;
  need_patient_banner?: boolean;
  smart_style_url?: string;
}

export interface SmartSession {
  accessToken: string;
  refreshToken?: string;
  /** Absolute expiry as epoch milliseconds. */
  expiresAt: number;
  scope: string;
  iss: string;
  patientId?: string;
  encounterId?: string;
  fhirUser?: string;
  tokenEndpoint: string;
}

export class SmartAuthError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'SmartAuthError';
  }
}

/** Refresh this many ms before actual expiry so a call never races the clock. */
const EXPIRY_SKEW_MS = 60_000;

const DISCOVERY_PATH = '/.well-known/smart-configuration';

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

/**
 * Fetch the EHR's SMART configuration.
 *
 * Falls back to the OAuth 2.0 authorization-server metadata document, which
 * some Cerner tenants serve instead, and finally to the CapabilityStatement's
 * `oauth-uris` extension used by older Epic versions.
 */
export async function discoverSmartConfiguration(iss: string): Promise<SmartConfiguration> {
  const base = iss.replace(/\/+$/, '');

  const wellKnown = await tryJson(`${base}${DISCOVERY_PATH}`);
  if (wellKnown && wellKnown.authorization_endpoint && wellKnown.token_endpoint) {
    return wellKnown as unknown as SmartConfiguration;
  }

  const oauthMeta = await tryJson(`${base}/.well-known/oauth-authorization-server`);
  if (oauthMeta && oauthMeta.authorization_endpoint && oauthMeta.token_endpoint) {
    return oauthMeta as unknown as SmartConfiguration;
  }

  const fromCapability = await discoverFromCapabilityStatement(base);
  if (fromCapability) return fromCapability;

  throw new SmartAuthError(
    'discovery_failed',
    `Could not discover SMART endpoints for issuer ${iss}. The server returned no ` +
      `usable smart-configuration, oauth-authorization-server, or CapabilityStatement.`,
  );
}

async function tryJson(url: string): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch(url, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (!response.ok) return null;
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function discoverFromCapabilityStatement(base: string): Promise<SmartConfiguration | null> {
  const statement = await tryJson(`${base}/metadata`);
  if (!statement) return null;

  const rest = (statement as { rest?: unknown[] }).rest ?? [];
  for (const entry of rest) {
    const security = (entry as { security?: { extension?: unknown[] } }).security;
    const extensions = security?.extension ?? [];
    const oauthUris = extensions.find(
      (extension) =>
        (extension as { url?: string }).url ===
        'http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris',
    ) as { extension?: { url: string; valueUri: string }[] } | undefined;

    if (!oauthUris?.extension) continue;

    const authorize = oauthUris.extension.find((e) => e.url === 'authorize')?.valueUri;
    const token = oauthUris.extension.find((e) => e.url === 'token')?.valueUri;
    if (authorize && token) {
      return { authorization_endpoint: authorize, token_endpoint: token };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// PKCE
// ---------------------------------------------------------------------------

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** 96 bytes → 128 base64url characters, the maximum PKCE verifier length. */
export function generateCodeVerifier(): string {
  const bytes = new Uint8Array(96);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

export async function deriveCodeChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64UrlEncode(new Uint8Array(digest));
}

export function generateState(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

// ---------------------------------------------------------------------------
// Authorization
// ---------------------------------------------------------------------------

/**
 * Build the authorize redirect.
 *
 * `launch` is present for EHR launches and absent for standalone ones; the
 * `launch/patient` scope is what makes a standalone launch prompt for a
 * patient, so the caller decides scopes rather than this function.
 */
export async function buildAuthorizeUrl(
  config: SmartLaunchConfig,
  options: { launch?: string; aud?: string } = {},
): Promise<AuthorizeRedirect> {
  const smart = await discoverSmartConfiguration(config.iss);

  const supportsPkce =
    !smart.code_challenge_methods_supported ||
    smart.code_challenge_methods_supported.includes('S256');
  if (!supportsPkce) {
    throw new SmartAuthError(
      'pkce_unsupported',
      'The EHR does not advertise support for the S256 PKCE method, which Clinchec requires.',
    );
  }

  const codeVerifier = generateCodeVerifier();
  const codeChallenge = await deriveCodeChallenge(codeVerifier);
  const state = generateState();

  const params = new URLSearchParams({
    response_type: 'code',
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    scope: config.scopes.join(' '),
    state,
    // `aud` must be the FHIR base URL. Epic rejects the request without it.
    aud: options.aud ?? config.iss,
    code_challenge: codeChallenge,
    code_challenge_method: 'S256',
  });

  if (options.launch) params.set('launch', options.launch);

  return {
    url: `${smart.authorization_endpoint}?${params.toString()}`,
    state,
    codeVerifier,
  };
}

/** Exchange an authorization code for tokens. */
export async function exchangeCodeForToken(
  config: SmartLaunchConfig,
  code: string,
  codeVerifier: string,
): Promise<SmartSession> {
  const smart = await discoverSmartConfiguration(config.iss);

  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: config.redirectUri,
    code_verifier: codeVerifier,
  });

  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
    Accept: 'application/json',
  };

  // Confidential clients authenticate with HTTP Basic; public clients send
  // client_id in the body. Sending both is a spec violation Epic rejects.
  if (config.clientSecret) {
    headers.Authorization = `Basic ${btoa(`${config.clientId}:${config.clientSecret}`)}`;
  } else {
    body.set('client_id', config.clientId);
  }

  const response = await fetch(smart.token_endpoint, {
    method: 'POST',
    headers,
    body,
    cache: 'no-store',
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new SmartAuthError(
      'token_exchange_failed',
      `Token exchange failed (${response.status}): ${detail.slice(0, 400)}`,
      response.status,
    );
  }

  const token = (await response.json()) as SmartTokenResponse;
  return toSession(token, config.iss, smart.token_endpoint);
}

/** Exchange a refresh token for a fresh access token. */
export async function refreshSession(
  config: SmartLaunchConfig,
  session: SmartSession,
): Promise<SmartSession> {
  if (!session.refreshToken) {
    throw new SmartAuthError(
      'no_refresh_token',
      'This session has no refresh token; request the offline_access scope to enable refresh.',
    );
  }

  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: session.refreshToken,
    scope: session.scope,
  });

  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
    Accept: 'application/json',
  };
  if (config.clientSecret) {
    headers.Authorization = `Basic ${btoa(`${config.clientId}:${config.clientSecret}`)}`;
  } else {
    body.set('client_id', config.clientId);
  }

  const response = await fetch(session.tokenEndpoint, {
    method: 'POST',
    headers,
    body,
    cache: 'no-store',
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new SmartAuthError(
      'refresh_failed',
      `Refresh failed (${response.status}): ${detail.slice(0, 400)}`,
      response.status,
    );
  }

  const token = (await response.json()) as SmartTokenResponse;
  const refreshed = toSession(token, session.iss, session.tokenEndpoint);
  // Servers that rotate refresh tokens send a new one; those that do not
  // expect the original to keep working.
  refreshed.refreshToken = token.refresh_token ?? session.refreshToken;
  // Launch context is not re-issued on refresh — carry it forward.
  refreshed.patientId ??= session.patientId;
  refreshed.encounterId ??= session.encounterId;
  refreshed.fhirUser ??= session.fhirUser;
  return refreshed;
}

function toSession(
  token: SmartTokenResponse,
  iss: string,
  tokenEndpoint: string,
): SmartSession {
  return {
    accessToken: token.access_token,
    refreshToken: token.refresh_token,
    expiresAt: Date.now() + (token.expires_in ?? 3600) * 1000,
    scope: token.scope ?? '',
    iss,
    patientId: token.patient,
    encounterId: token.encounter,
    fhirUser: token.fhirUser ?? decodeFhirUser(token.id_token),
    tokenEndpoint,
  };
}

/**
 * Read `fhirUser` out of the id_token.
 *
 * This is a claim read for display, not an authentication decision — the access
 * token is what authorizes, and the EHR already validated it. A full JWKS
 * signature check belongs on any path that grants access based on the claim.
 */
function decodeFhirUser(idToken?: string): string | undefined {
  if (!idToken) return undefined;
  const payload = idToken.split('.')[1];
  if (!payload) return undefined;
  try {
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const decoded = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')));
    return decoded.fhirUser ?? decoded.profile;
  } catch {
    return undefined;
  }
}

export function isExpired(session: SmartSession): boolean {
  return Date.now() >= session.expiresAt - EXPIRY_SKEW_MS;
}

/** Return a valid session, refreshing in place when the current one has aged out. */
export async function ensureValidSession(
  config: SmartLaunchConfig,
  session: SmartSession,
): Promise<SmartSession> {
  return isExpired(session) ? refreshSession(config, session) : session;
}
