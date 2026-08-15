'use client';

import * as React from 'react';
import {
  Activity,
  AlertOctagon,
  CalendarClock,
  Check,
  ClipboardList,
  Minus,
  Quote,
  Scan,
  Stethoscope,
  User,
  X,
} from 'lucide-react';

import type {
  ClinicalEntity,
  CodeCandidate,
  EntityLabel,
  ScanResult,
  TextSpan,
} from '@clinchec/shared-types';

import { cn, formatDuration } from '@/lib/utils';
import { ApprovalScorePanel } from '@/components/scan/ApprovalScoreBadge';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

/**
 * Renders one scan.
 *
 * Two ideas drive the layout:
 *
 * **Confidence is shown, never hidden.** Every extracted value carries the
 * model's confidence as a visible band — high (≥ 85%), medium (60–84%), low
 * (< 60%). A clinician signing a prior authorization is accountable for what it
 * says, so a value the model is unsure about has to look different from one it
 * is certain of. Confidence bands use their own neutral-to-warm scale, distinct
 * from the green/amber/red approval palette, so "the model is unsure" is never
 * mistaken for "the payer will deny this".
 *
 * **The note stays the source of truth.** The Highlighted tab renders the
 * original note with extracted spans marked in place, so verifying an
 * extraction is a glance rather than a hunt.
 */

// --- Confidence presentation ------------------------------------------------

type ConfidenceTier = 'high' | 'medium' | 'low';

function tierFor(confidence: number): ConfidenceTier {
  if (confidence >= 0.85) return 'high';
  if (confidence >= 0.6) return 'medium';
  return 'low';
}

const TIER_STYLES: Record<ConfidenceTier, { dot: string; label: string; text: string }> = {
  high: { dot: 'bg-sky-600', label: 'High confidence', text: 'text-sky-700 dark:text-sky-400' },
  medium: {
    dot: 'bg-violet-500',
    label: 'Medium confidence — worth a glance',
    text: 'text-violet-700 dark:text-violet-400',
  },
  low: {
    dot: 'bg-zinc-400',
    label: 'Low confidence — verify before submitting',
    text: 'text-zinc-600 dark:text-zinc-400',
  },
};

function ConfidenceDot({ confidence }: { confidence: number }) {
  const tier = tierFor(confidence);
  const style = TIER_STYLES[tier];
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className="inline-flex items-center gap-1"
            aria-label={`${Math.round(confidence * 100)}% confidence. ${style.label}.`}
          >
            <span className={cn('size-1.5 rounded-full', style.dot)} aria-hidden="true" />
            <span className={cn('text-[11px] font-medium tabular-nums', style.text)}>
              {Math.round(confidence * 100)}%
            </span>
          </span>
        </TooltipTrigger>
        <TooltipContent>{style.label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// --- Entity presentation ----------------------------------------------------

const ENTITY_STYLES: Record<EntityLabel, { label: string; chip: string; mark: string }> = {
  diagnosis: {
    label: 'Diagnosis',
    chip: 'bg-rose-50 text-rose-800 border-rose-200 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-900',
    mark: 'bg-rose-100 dark:bg-rose-950/60',
  },
  procedure: {
    label: 'Procedure',
    chip: 'bg-sky-50 text-sky-800 border-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:border-sky-900',
    mark: 'bg-sky-100 dark:bg-sky-950/60',
  },
  conservative_care: {
    label: 'Conservative care',
    chip: 'bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900',
    mark: 'bg-emerald-100 dark:bg-emerald-950/60',
  },
  red_flag: {
    label: 'Red flag',
    chip: 'bg-orange-50 text-orange-800 border-orange-200 dark:bg-orange-950/40 dark:text-orange-300 dark:border-orange-900',
    mark: 'bg-orange-100 dark:bg-orange-950/60',
  },
  imaging_evidence: {
    label: 'Prior imaging',
    chip: 'bg-violet-50 text-violet-800 border-violet-200 dark:bg-violet-950/40 dark:text-violet-300 dark:border-violet-900',
    mark: 'bg-violet-100 dark:bg-violet-950/60',
  },
  functional_impairment: {
    label: 'Functional impact',
    chip: 'bg-amber-50 text-amber-900 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900',
    mark: 'bg-amber-100 dark:bg-amber-950/60',
  },
  anatomy: {
    label: 'Anatomy',
    chip: 'bg-slate-50 text-slate-700 border-slate-200 dark:bg-slate-900 dark:text-slate-300 dark:border-slate-800',
    mark: 'bg-slate-100 dark:bg-slate-900',
  },
  medication: {
    label: 'Medication',
    chip: 'bg-teal-50 text-teal-800 border-teal-200 dark:bg-teal-950/40 dark:text-teal-300 dark:border-teal-900',
    mark: 'bg-teal-100 dark:bg-teal-950/60',
  },
};

const ENTITY_ORDER: EntityLabel[] = [
  'diagnosis',
  'procedure',
  'conservative_care',
  'imaging_evidence',
  'functional_impairment',
  'red_flag',
  'medication',
  'anatomy',
];

// ---------------------------------------------------------------------------

export interface ExtractionResultProps {
  result: ScanResult;
  /** The exact text that was scanned, for in-place highlighting. */
  note: string;
  className?: string;
}

export function ExtractionResult({ result, note, className }: ExtractionResultProps) {
  const { extraction, approval } = result;

  const grouped = React.useMemo(() => {
    const map = new Map<EntityLabel, ClinicalEntity[]>();
    for (const entity of extraction.entities) {
      const bucket = map.get(entity.label) ?? [];
      bucket.push(entity);
      map.set(entity.label, bucket);
    }
    return map;
  }, [extraction.entities]);

  const affirmedCount = extraction.entities.filter((entity) => !entity.negated).length;


  return (
    <div className={cn('space-y-5', className)}>
      <ApprovalScorePanel approval={approval} />

      <Tabs defaultValue="extracted">
        <TabsList>
          <TabsTrigger value="extracted">Extracted</TabsTrigger>
          <TabsTrigger value="highlighted">Highlighted note</TabsTrigger>
          <TabsTrigger value="drivers">
            Why this score
            <Badge variant="muted" className="ml-2 px-1.5 py-0 text-[10px]">
              {approval.drivers.length}
            </Badge>
          </TabsTrigger>
        </TabsList>

        {/* --- Extracted ------------------------------------------------- */}
        <TabsContent value="extracted" className="space-y-5">
          {/* `items-start` so a short card does not stretch to match a tall
              neighbour. Patient & presentation has three rows and Suggested
              codes has as many as the note yields, and stretching left an
              empty half-card that read as content still loading. */}
          <div className="grid items-start gap-5 lg:grid-cols-2">
            <SummaryCard result={result} />
            <CodesCard
              diagnoses={extraction.diagnoses}
              procedures={extraction.procedures}
            />
          </div>

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Scan className="size-4 text-muted-foreground" aria-hidden="true" />
                Clinical evidence
              </CardTitle>
              <span className="text-xs text-muted-foreground">
                {affirmedCount} of {extraction.entities.length} affirmed
              </span>
            </CardHeader>
            <CardContent className="space-y-4">
              {extraction.entities.length === 0 ? (
                <EmptyState message="No clinical entities were recognised in this note." />
              ) : (
                ENTITY_ORDER.filter((label) => grouped.has(label)).map((label) => (
                  <EntityGroup key={label} label={label} entities={grouped.get(label)!} />
                ))
              )}
            </CardContent>
          </Card>

          {extraction.justification ? (
            <JustificationCard
              text={extraction.justification.text}
              generatedBy={extraction.justification.generated_by}
              citations={extraction.justification.citations}
            />
          ) : null}
        </TabsContent>

        {/* --- Highlighted note ------------------------------------------ */}
        <TabsContent value="highlighted">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Original note</CardTitle>
              <div className="flex flex-wrap gap-1.5 pt-2">
                {ENTITY_ORDER.filter((label) => grouped.has(label)).map((label) => (
                  <span
                    key={label}
                    className={cn(
                      'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium',
                      ENTITY_STYLES[label].chip,
                    )}
                  >
                    <span
                      className={cn('size-2 rounded-[3px]', ENTITY_STYLES[label].mark)}
                      aria-hidden="true"
                    />
                    {ENTITY_STYLES[label].label}
                  </span>
                ))}
              </div>
            </CardHeader>
            <CardContent>
              <HighlightedNote note={note} entities={extraction.entities} />
            </CardContent>
          </Card>
        </TabsContent>

        {/* --- Drivers ---------------------------------------------------- */}
        <TabsContent value="drivers" className="space-y-5">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Score breakdown</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              {approval.drivers.map((driver) => (
                <div
                  key={driver.key}
                  className="flex items-start gap-3 rounded-md px-2 py-2.5 hover:bg-muted/50"
                >
                  <span
                    className={cn(
                      'mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full',
                      driver.satisfied
                        ? 'bg-approve-surface text-approve-foreground'
                        : 'bg-deny-surface text-deny-foreground',
                    )}
                    aria-hidden="true"
                  >
                    {driver.satisfied ? (
                      <Check className="size-3" />
                    ) : (
                      <X className="size-3" />
                    )}
                  </span>

                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{driver.label}</p>
                    <p className="text-sm text-muted-foreground">{driver.detail}</p>
                    {/* What closing this gap is worth, in the same units as the
                        column on the right. The clinician's next question after
                        "what is missing" is "does fixing it matter", and the
                        score's own weights answer it exactly. */}
                    {driver.potential_delta ? (
                      <p className="mt-1 text-xs font-medium text-caution-foreground">
                        Documenting this adds {Math.round(driver.potential_delta * 100)}{' '}
                        points
                      </p>
                    ) : null}
                  </div>

                  <span
                    className={cn(
                      'shrink-0 text-sm font-semibold tabular-nums',
                      driver.delta > 0
                        ? 'text-approve-foreground'
                        : driver.delta < 0
                          ? 'text-deny-foreground'
                          : 'text-muted-foreground',
                    )}
                    aria-label={`${driver.delta > 0 ? 'Adds' : 'Subtracts'} ${Math.abs(
                      Math.round(driver.delta * 100),
                    )} points`}
                  >
                    {driver.delta > 0 ? '+' : ''}
                    {Math.round(driver.delta * 100)}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>

          {approval.gaps.length > 0 ? (
            <Card className="border-caution-border bg-caution-surface/40">
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base text-caution-foreground">
                  <ClipboardList className="size-4" aria-hidden="true" />
                  Add this to strengthen the request
                </CardTitle>
              </CardHeader>
              <CardContent>
                {/* The engine emits these already tied to their driver and
                    ordered by what closing each is worth, so the first line is
                    the one to write if the clinician only writes one. Pairing
                    them here by position would put the wrong number on the
                    wrong line — functional impairment records a gap from a
                    branch with no unmet driver — which is why the key travels
                    with the gap rather than being inferred. */}
                <ol className="space-y-2.5">
                  {approval.gaps.map((gap, index) => (
                    <li key={gap.text} className="flex items-baseline gap-3 text-sm">
                      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-caution/15 text-[11px] font-semibold tabular-nums text-caution-foreground">
                        {index + 1}
                      </span>
                      <span className="flex-1">
                        {gap.text}
                        {/* Who is asking. "UHC requires this" is something a
                            clinician can act on and quote back on appeal;
                            without the attribution every line reads as generic
                            advice they may reasonably skip. */}
                        {gap.required_by ? (
                          <span
                            className="ml-2 rounded border border-caution-border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
                            title={gap.payer_quote ?? undefined}
                          >
                            {gap.required_by} requires
                          </span>
                        ) : null}
                      </span>
                      {gap.potential_delta ? (
                        <span
                          className="shrink-0 text-xs font-semibold tabular-nums text-caution-foreground"
                          title={`Documenting this adds ${Math.round(
                            gap.potential_delta * 100,
                          )} points to the documentation score`}
                        >
                          +{Math.round(gap.potential_delta * 100)}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          ) : null}
        </TabsContent>
      </Tabs>

      <p className="text-center text-xs text-muted-foreground">
        Clinchec is a documentation aid, not a coverage determination. The clinician
        remains responsible for the codes and clinical content submitted.
        <span className="mx-1.5 opacity-40">·</span>
        <span className="font-mono">{result.model_version}</span>
      </p>
    </div>
  );
}

// --- Sub-components ---------------------------------------------------------

function SummaryCard({ result }: { result: ScanResult }) {
  const { demographics, chief_complaint, duration } = result.extraction;

  const age =
    demographics.age === null
      ? null
      : `${demographics.age} ${demographics.age_unit === 'years' || !demographics.age_unit ? 'y' : demographics.age_unit}`;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <User className="size-4 text-muted-foreground" aria-hidden="true" />
          Patient &amp; presentation
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-0">
        <Field
          icon={<User className="size-3.5" />}
          label="Age / sex"
          value={
            age || demographics.sex !== 'unknown'
              ? [age, demographics.sex !== 'unknown' ? demographics.sex : null]
                  .filter(Boolean)
                  .join(' · ')
              : null
          }
          confidence={demographics.confidence}
        />
        <Separator />
        <Field
          icon={<Stethoscope className="size-3.5" />}
          label="Chief complaint"
          value={chief_complaint ?? null}
        />
        <Separator />
        <Field
          icon={<CalendarClock className="size-3.5" />}
          label="Duration"
          value={duration ? formatDuration(duration.value, duration.unit) : null}
          confidence={duration?.confidence}
          hint={
            duration ? `${duration.normalized_weeks.toFixed(1)} weeks, normalised` : undefined
          }
        />
      </CardContent>
    </Card>
  );
}

function CodesCard({
  diagnoses,
  procedures,
}: {
  diagnoses: CodeCandidate[];
  procedures: CodeCandidate[];
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Activity className="size-4 text-muted-foreground" aria-hidden="true" />
          Suggested codes
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <CodeList title="Diagnosis (ICD-10-CM)" codes={diagnoses} />
        <CodeList title="Procedure (CPT / HCPCS)" codes={procedures} />
      </CardContent>
    </Card>
  );
}

function CodeList({ title, codes }: { title: string; codes: CodeCandidate[] }) {
  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      {codes.length === 0 ? (
        <p className="text-sm text-muted-foreground">None identified.</p>
      ) : (
        <ul className="space-y-1.5">
          {codes.map((code) => (
            <li
              key={`${code.system}-${code.code}`}
              className="flex items-start justify-between gap-3 rounded-md border bg-card px-2.5 py-2"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs font-semibold">
                    {code.code}
                  </code>
                  {code.laterality ? (
                    <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                      {code.laterality}
                    </Badge>
                  ) : code.requires_laterality ? (
                    <Badge variant="caution" className="px-1.5 py-0 text-[10px]">
                      side not documented
                    </Badge>
                  ) : null}
                </div>
                <p className="mt-1 truncate text-sm" title={code.description}>
                  {code.description}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  from “{code.matched_text}” in {code.section}
                </p>
              </div>
              <div className="shrink-0 pt-0.5">
                <ConfidenceDot confidence={code.confidence} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EntityGroup({ label, entities }: { label: EntityLabel; entities: ClinicalEntity[] }) {
  const style = ENTITY_STYLES[label];
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {style.label}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {entities.map((entity) => (
          <span
            key={`${entity.start}-${entity.end}-${entity.label}`}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs',
              style.chip,
              // A negated finding is documented absence. It stays visible —
              // "denies red flags" is clinically meaningful — but must never
              // read as an affirmative finding.
              entity.negated && 'opacity-60 line-through decoration-1',
            )}
            title={
              entity.negated
                ? 'Documented as absent'
                : entity.uncertain
                  ? 'Hedged in the note'
                  : undefined
            }
          >
            {entity.negated ? <Minus className="size-3" aria-hidden="true" /> : null}
            {entity.text}
            {entity.uncertain && !entity.negated ? (
              <span className="opacity-70" aria-label="uncertain">
                ?
              </span>
            ) : null}
            <ConfidenceDot confidence={entity.confidence} />
          </span>
        ))}
      </div>
    </div>
  );
}

function JustificationCard({
  text,
  generatedBy,
  citations,
}: {
  text: string;
  generatedBy: string;
  citations: TextSpan[];
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Quote className="size-4 text-muted-foreground" aria-hidden="true" />
          Draft medical-necessity statement
        </CardTitle>
        <Badge variant="muted">{generatedBy}</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p>
        {citations.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            Grounded in {citations.length} passage{citations.length === 1 ? '' : 's'} from the
            note. Review before submitting.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Field({
  icon,
  label,
  value,
  confidence,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | null;
  confidence?: number;
  hint?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <span aria-hidden="true">{icon}</span>
        {label}
      </div>
      <div className="min-w-0 text-right">
        {value ? (
          <>
            <p className="truncate text-sm font-medium capitalize">{value}</p>
            {hint ? <p className="text-[11px] text-muted-foreground">{hint}</p> : null}
          </>
        ) : (
          <p className="text-sm italic text-muted-foreground">not documented</p>
        )}
        {value && confidence !== undefined ? (
          <div className="mt-0.5 flex justify-end">
            <ConfidenceDot confidence={confidence} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center gap-2 py-8 text-center">
      <AlertOctagon className="size-5 text-muted-foreground" aria-hidden="true" />
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

/**
 * Render the note with extracted spans marked in place.
 *
 * Entities are sorted and any overlap is dropped, because a nested `<mark>`
 * would break the offset arithmetic and render duplicated text. The parser
 * already resolves overlaps via `filter_spans`, so this is a belt-and-braces
 * guard against a malformed response rather than an expected case.
 */
function HighlightedNote({ note, entities }: { note: string; entities: ClinicalEntity[] }) {
  const segments = React.useMemo(() => {
    const sorted = [...entities]
      .filter((entity) => entity.start >= 0 && entity.end <= note.length && entity.start < entity.end)
      .sort((a, b) => a.start - b.start || b.end - a.end);

    const output: Array<{ text: string; entity?: ClinicalEntity }> = [];
    let cursor = 0;

    for (const entity of sorted) {
      if (entity.start < cursor) continue;
      if (entity.start > cursor) {
        output.push({ text: note.slice(cursor, entity.start) });
      }
      output.push({ text: note.slice(entity.start, entity.end), entity });
      cursor = entity.end;
    }

    if (cursor < note.length) output.push({ text: note.slice(cursor) });
    return output;
  }, [note, entities]);

  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[13px] leading-relaxed">
      {segments.map((segment, index) =>
        segment.entity ? (
          <mark
            key={index}
            className={cn(
              'entity-mark',
              ENTITY_STYLES[segment.entity.label].mark,
              'text-foreground',
              segment.entity.negated && 'opacity-55 line-through decoration-1',
            )}
            title={`${ENTITY_STYLES[segment.entity.label].label} · ${Math.round(
              segment.entity.confidence * 100,
            )}% confidence${segment.entity.negated ? ' · documented as absent' : ''}`}
          >
            {segment.text}
          </mark>
        ) : (
          <React.Fragment key={index}>{segment.text}</React.Fragment>
        ),
      )}
    </pre>
  );
}
