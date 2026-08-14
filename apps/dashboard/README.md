# Clinchec Dashboard — practice admin view

**Status: not started.** This workspace is a placeholder so the monorepo layout
and CI matrix already account for it.

## What it will be

`apps/web` is the clinician surface — one note, one request, in the exam room.
This app is the *practice manager* surface, and the questions it answers are
different:

- **Denial analytics.** Which payer, procedure and missing-element combinations
  actually get denied, measured against what Clinchec predicted. This is also
  the labelled dataset that replaces the rule engine in `services/scan` with a
  trained model.
- **Payer performance.** Turnaround time and approval rate per payer, per
  procedure — the number a practice uses when negotiating a contract.
- **Rule change feed.** What Clinchec Live saw change this month and which of a
  practice&rsquo;s in-flight requests it affects.
- **User and role management.** Delegating PA submission to staff without
  giving them a clinician&rsquo;s chart access.

## Why it is separate from `apps/web`

Different audience, different session model, and different data access. The
clinician app is scoped to one patient by the SMART launch context; this one is
scoped to a practice and reads aggregates, never individual charts. Keeping
that boundary at the application level makes it enforceable rather than
conventional.

## When it gets built

After the submission path in `services/forms` is transmitting to at least one
payer end to end. Denial analytics with no submitted denials to analyse would
be a dashboard of zeroes.