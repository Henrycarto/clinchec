/**
 * Guard tests for the development auth bypass.
 *
 * Runs on the Node test runner with native type stripping, so it needs no test
 * framework and no bundler:
 *
 *     node --experimental-strip-types --test apps/web/lib/dev-auth-guard.test.ts
 *
 * The production cases are the point of this file. Everything else about the
 * bypass is convenience; those two assertions are the reason it is safe to
 * have at all.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  assertDevBypassAllowed,
  DevBypassForbiddenError,
  isDevBypassEnabled,
} from './dev-auth-guard.ts';

const env = (overrides: Record<string, string | undefined>) =>
  overrides as NodeJS.ProcessEnv;

// --- The controls that matter ----------------------------------------------

test('production refuses the bypass even when the flag is set', () => {
  const production = env({ NODE_ENV: 'production', DEV_AUTH_BYPASS: 'true' });

  assert.equal(isDevBypassEnabled(production), false);
  assert.throws(
    () => assertDevBypassAllowed(production),
    DevBypassForbiddenError,
    'a production deployment with the flag set must still refuse',
  );
});

test('the production refusal names the flag so the fix is obvious', () => {
  try {
    assertDevBypassAllowed(env({ NODE_ENV: 'production', DEV_AUTH_BYPASS: 'true' }));
    assert.fail('expected a refusal');
  } catch (error) {
    assert.match((error as Error).message, /DEV_AUTH_BYPASS/);
    assert.match((error as Error).message, /production/);
  }
});

// --- Default-off behaviour --------------------------------------------------

test('the bypass is off when the flag is absent', () => {
  const development = env({ NODE_ENV: 'development' });

  assert.equal(isDevBypassEnabled(development), false);
  assert.throws(() => assertDevBypassAllowed(development), DevBypassForbiddenError);
});

test('only the exact string "true" enables it', () => {
  for (const value of ['1', 'yes', 'TRUE', 'True', 'on', '']) {
    const candidate = env({ NODE_ENV: 'development', DEV_AUTH_BYPASS: value });
    assert.equal(
      isDevBypassEnabled(candidate),
      false,
      `DEV_AUTH_BYPASS=${JSON.stringify(value)} must not enable the bypass`,
    );
  }
});

// --- The one case that should work -----------------------------------------

test('development with the flag set explicitly is allowed', () => {
  const development = env({ NODE_ENV: 'development', DEV_AUTH_BYPASS: 'true' });

  assert.equal(isDevBypassEnabled(development), true);
  assert.doesNotThrow(() => assertDevBypassAllowed(development));
});

test('test environments behave like development', () => {
  const testEnv = env({ NODE_ENV: 'test', DEV_AUTH_BYPASS: 'true' });

  assert.equal(isDevBypassEnabled(testEnv), true);
  assert.doesNotThrow(() => assertDevBypassAllowed(testEnv));
});
