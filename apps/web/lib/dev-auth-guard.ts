/**
 * The guard for the development auth bypass.
 *
 * Deliberately a standalone module with no imports: this is the security
 * control that decides whether an unauthenticated session may be created, so
 * it should be readable in full on one screen and testable without booting
 * Next.js. `lib/dev-auth.ts` holds everything that touches a session.
 */

export const DEV_BYPASS_ENV_FLAG = 'DEV_AUTH_BYPASS';

export class DevBypassForbiddenError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DevBypassForbiddenError';
  }
}

/**
 * Whether the bypass is available in this environment.
 *
 * Two conditions, both required. Production is checked first and independently
 * of the flag, so setting `DEV_AUTH_BYPASS=true` on a production deployment
 * does nothing.
 */
export function isDevBypassEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  if (env.NODE_ENV === 'production') return false;
  return env[DEV_BYPASS_ENV_FLAG] === 'true';
}

/**
 * Throw unless the bypass is legitimately available.
 *
 * The production check is repeated here rather than delegated to
 * `isDevBypassEnabled`, so a future change that loosens that predicate cannot
 * silently make unauthenticated sessions mintable in production.
 */
export function assertDevBypassAllowed(env: NodeJS.ProcessEnv = process.env): void {
  if (env.NODE_ENV === 'production') {
    throw new DevBypassForbiddenError(
      `${DEV_BYPASS_ENV_FLAG} is not permitted when NODE_ENV=production. ` +
        'Remove it from the production environment and use SMART on FHIR.',
    );
  }
  if (env[DEV_BYPASS_ENV_FLAG] !== 'true') {
    throw new DevBypassForbiddenError(
      `${DEV_BYPASS_ENV_FLAG} is not enabled in this environment.`,
    );
  }
}
