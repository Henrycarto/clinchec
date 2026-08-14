import Link from 'next/link';
import { redirect } from 'next/navigation';
import { Activity, ArrowRight, FileCheck2, RadioTower, ScanLine } from 'lucide-react';

import { getSession } from '@/lib/session';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export const dynamic = 'force-dynamic';

const FEATURES = [
  {
    icon: ScanLine,
    name: 'Clinchec Scan',
    description:
      'Reads the unstructured SOAP note and extracts diagnosis codes, procedure ' +
      'codes and the clinical language that supports medical necessity.',
  },
  {
    icon: RadioTower,
    name: 'Clinchec Live',
    description:
      'Polls payer portals continuously and keeps a per-insurer, per-procedure ' +
      'rules database, so the criteria you are scored against are current.',
  },
  {
    icon: FileCheck2,
    name: 'Clinchec Forms',
    description:
      'Maps the extracted data onto the right prior authorization form for that ' +
      'payer and procedure, then submits or exports it in one click.',
  },
] as const;

export default async function HomePage() {
  const session = await getSession();
  if (session) redirect('/dashboard');

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b">
        <div className="container flex h-14 items-center justify-between">
          <span className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <Activity className="size-4" aria-hidden="true" />
            </span>
            Clinchec
          </span>
          <Button asChild variant="ghost" size="sm">
            <Link href="/login">Sign in</Link>
          </Button>
        </div>
      </header>

      <main id="main" className="flex-1">
        <section className="container py-20 text-center">
          <p className="text-sm font-medium uppercase tracking-widest text-primary">
            Prior authorization pre-screening
          </p>
          <h1 className="mx-auto mt-4 max-w-3xl text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
            Know whether a prior auth will be approved before you submit it
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-pretty text-lg text-muted-foreground">
            Clinchec sits inside the EHR workflow, reads the note you already wrote,
            and tells you what the payer is going to ask for — while the patient is
            still in the room.
          </p>
          <div className="mt-8 flex justify-center gap-3">
            <Button asChild size="lg">
              <Link href="/login">
                Launch from your EHR
                <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
          </div>
        </section>

        <section className="container pb-24">
          <div className="grid gap-5 md:grid-cols-3">
            {FEATURES.map((feature) => (
              <Card key={feature.name}>
                <CardHeader className="pb-3">
                  <span className="flex size-9 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                    <feature.icon className="size-4" aria-hidden="true" />
                  </span>
                  <CardTitle className="pt-2 text-base">{feature.name}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {feature.description}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t py-6">
        <div className="container text-center text-xs text-muted-foreground">
          Clinchec is clinical decision support. It does not make coverage
          determinations, and the ordering clinician remains responsible for the
          content of every submitted request.
        </div>
      </footer>
    </div>
  );
}
