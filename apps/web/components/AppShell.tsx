import Link from 'next/link';
import { Activity, FileCheck2, LayoutDashboard, LogOut } from 'lucide-react';

import { cn } from '@/lib/utils';
import { DevModeBanner } from '@/components/DevModeBanner';
import { Button } from '@/components/ui/button';

/**
 * Application chrome.
 *
 * Kept deliberately spare: Clinchec runs inside an EHR frame, where competing
 * navigation is the fastest way to make a clinician lose their place.
 */
export function AppShell({
  children,
  clinicianName,
  practiceName,
  active,
  devSession = false,
}: {
  children: React.ReactNode;
  clinicianName?: string;
  practiceName?: string;
  active?: 'dashboard' | 'pa-requests';
  /** True when the session came from the dev auth bypass rather than an EHR. */
  devSession?: boolean;
}) {
  return (
    <div className="flex min-h-dvh flex-col">
      {devSession ? <DevModeBanner /> : null}

      <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70">
        <div className="container flex h-14 items-center gap-6">
          <Link href="/dashboard" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <Activity className="size-4" aria-hidden="true" />
            </span>
            Clinchec
          </Link>

          <nav aria-label="Main" className="flex items-center gap-1">
            <NavLink href="/dashboard" active={active === 'dashboard'}>
              <LayoutDashboard className="size-4" aria-hidden="true" />
              Scan
            </NavLink>
            <NavLink href="/pa-requests" active={active === 'pa-requests'}>
              <FileCheck2 className="size-4" aria-hidden="true" />
              PA requests
            </NavLink>
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {clinicianName ? (
              <div className="hidden text-right sm:block">
                <p className="text-sm font-medium leading-tight">{clinicianName}</p>
                {practiceName ? (
                  <p className="text-xs leading-tight text-muted-foreground">{practiceName}</p>
                ) : null}
              </div>
            ) : null}
            <form action="/api/auth/logout" method="post">
              <Button
                type="submit"
                variant="ghost"
                size="icon"
                aria-label="Sign out"
                title="Sign out"
              >
                <LogOut className="size-4" aria-hidden="true" />
              </Button>
            </form>
          </div>
        </div>
      </header>

      <main id="main" className="container flex-1 py-8">
        {children}
      </main>

      <footer className="border-t py-4">
        <div className="container flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <p>Clinchec · Prior authorization pre-screening</p>
          <p>Decision support only — not a coverage determination.</p>
        </div>
      </footer>
    </div>
  );
}

function NavLink({
  href,
  active,
  children,
}: {
  href: '/dashboard' | '/pa-requests';
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
        active
          ? 'bg-secondary text-secondary-foreground'
          : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
      )}
    >
      {children}
    </Link>
  );
}
