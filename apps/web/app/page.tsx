import Link from 'next/link';
import { redirect } from 'next/navigation';

import { listPayers } from '@/lib/api';
import { getSession } from '@/lib/session';
import { Button } from '@/components/ui/button';

export const dynamic = 'force-dynamic';

/**
 * The signed-out surface.
 *
 * Deliberately not a marketing page. Clinchec is launched from inside an EHR
 * over SMART on FHIR, so nobody arrives here to be persuaded — the two people
 * who reach this URL are a clinician whose session died between patients, who
 * needs to get back in and not read anything, and somebody evaluating the
 * product, who needs evidence rather than adjectives.
 *
 * Both are better served by the rules table's actual state than by a
 * description of it. "Aetna — 4 rules, verified 2 hours ago" is a checkable
 * claim; "polls payer portals continuously" is a sentence anyone can write.
 */
export default async function HomePage() {
  const session = await getSession();
  if (session) redirect('/dashboard');

  // Logged, not swallowed. The table degrading to nothing is the right
  // behaviour for a clinician, and it is also exactly what a misconfigured
  // service URL looks like — which is how this page shipped once already
  // fetching the browser's localhost from inside the container.
  const payers = await listPayers().catch((error) => {
    console.error('Could not read payer coverage for the signed-out page', error);
    return null;
  });

  return (
    // Header pinned to the top, disclaimer to the bottom, and the content
    // centred in whatever is left. The page carries four elements; letting it
    // stretch to fill a desktop viewport left a void under the table that read
    // as an unfinished page rather than a deliberately quiet one.
    <div className="mx-auto flex min-h-dvh w-full max-w-3xl flex-col px-6">
      <header className="flex items-baseline justify-between border-b py-6">
        <span className="text-sm font-semibold tracking-tight">Clinchec</span>
        <span className="text-xs text-muted-foreground">
          Prior authorization pre-screening
        </span>
      </header>

      <main id="main" className="flex flex-1 flex-col justify-center py-12">
        <section>
          <h1 className="max-w-xl text-2xl font-semibold leading-snug tracking-tight">
            Know what the payer will ask for while the patient is still in the room.
          </h1>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-muted-foreground">
            Clinchec reads the note you already wrote, checks it against the
            insurer&rsquo;s current published criteria, and names what is missing
            before the request is submitted.
          </p>

          <div className="mt-8 flex items-center gap-4">
            <Button asChild>
              <Link href="/login">Launch from your EHR</Link>
            </Button>
            <span className="text-xs text-muted-foreground">
              SMART on FHIR · Epic and Cerner
            </span>
          </div>
        </section>

        <PayerTable payers={payers} />
      </main>

      <footer className="border-t py-6 text-xs leading-relaxed text-muted-foreground">
        Clinical decision support. Clinchec does not make coverage
        determinations, and the ordering clinician remains responsible for the
        content of every submitted request.
      </footer>
    </div>
  );
}

/**
 * What Clinchec currently holds, read live from the rules service.
 *
 * The freshness column is the point. A rules feed is only worth anything if
 * somebody can see when it was last confirmed, and a stale one should be
 * obvious here rather than discovered mid-submission.
 */
async function PayerTable({
  payers,
}: {
  payers: Awaited<ReturnType<typeof listPayers>> | null;
}) {
  if (!payers) {
    // Live being unreachable is not an error worth shouting about on a sign-in
    // page — it says nothing about whether the clinician can work.
    return null;
  }

  return (
    <section aria-labelledby="coverage" className="mt-14 border-t pt-8">
      <h2
        id="coverage"
        className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
      >
        Criteria currently held
      </h2>

      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs font-normal text-muted-foreground">
            <th scope="col" className="pb-2 font-normal">
              Payer
            </th>
            <th scope="col" className="pb-2 text-right font-normal">
              Rules
            </th>
            <th scope="col" className="pb-2 text-right font-normal">
              Last verified
            </th>
          </tr>
        </thead>
        <tbody>
          {payers.map((payer) => (
            <tr key={payer.slug} className="border-b last:border-0">
              <td className="py-2.5">{payer.display_name}</td>
              <td className="py-2.5 text-right font-mono tabular-nums">
                {payer.rule_count}
              </td>
              <td className="py-2.5 text-right text-muted-foreground">
                {formatVerified(payer.last_crawled_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function formatVerified(at: string | null | undefined): string {
  if (!at) return 'not yet';

  const elapsedHours = (Date.now() - new Date(at).getTime()) / 3_600_000;
  if (!Number.isFinite(elapsedHours)) return 'unknown';
  if (elapsedHours < 1) return 'within the hour';
  if (elapsedHours < 24) {
    const hours = Math.round(elapsedHours);
    return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  }
  const days = Math.round(elapsedHours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}
