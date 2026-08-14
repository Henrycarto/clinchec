import { TriangleAlert } from 'lucide-react';

/**
 * Permanent warning for a session created by the development auth bypass.
 *
 * Deliberately not dismissible. The entire risk of an auth bypass is somebody
 * forgetting it is on, so the reminder has to outlast their attention rather
 * than their first click.
 */
export function DevModeBanner() {
  return (
    <div
      role="alert"
      className="border-b border-caution-border bg-caution-surface text-caution-foreground"
    >
      <div className="container flex items-center gap-2.5 py-2 text-xs sm:text-sm">
        <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
        <p>
          <span className="font-semibold">Development session — not authenticated.</span>{' '}
          You are signed in through <code className="font-mono">DEV_AUTH_BYPASS</code>, not
          SMART on FHIR. No EHR data is available and nothing here is a real patient.
        </p>
      </div>
    </div>
  );
}
