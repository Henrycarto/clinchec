'use client';

import * as React from 'react';
import { AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';

import type { ApprovalAssessment, ApprovalBand } from '@clinchec/shared-types';

import { BAND_PRESENTATION, cn, formatPercent } from '@/lib/utils';

/**
 * The approval-confidence indicator.
 *
 * Green ≥ 80%, amber 50–79%, red below 50% — the thresholds the rule engine
 * bands on, imported rather than re-derived so the badge can never disagree
 * with the service that produced the score.
 *
 * Colour is never the only channel. Each band carries a distinct icon shape
 * and an explicit label, because a red-green colour vision deficiency affects
 * roughly one in twelve male clinicians and this badge is the single most
 * consequential pixel on the screen.
 */

const ICONS = {
  'check-circle': CheckCircle2,
  'alert-triangle': AlertTriangle,
  'x-circle': XCircle,
} as const;

export interface ApprovalScoreBadgeProps {
  score: number;
  band: ApprovalBand;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
  className?: string;
}

export function ApprovalScoreBadge({
  score,
  band,
  size = 'md',
  showLabel = true,
  className,
}: ApprovalScoreBadgeProps) {
  const presentation = BAND_PRESENTATION[band];
  const Icon = ICONS[presentation.icon];
  const percent = formatPercent(score);

  const sizes = {
    sm: { wrap: 'gap-1.5 px-2 py-1 text-xs', icon: 'size-3.5', score: 'text-xs' },
    md: { wrap: 'gap-2 px-3 py-1.5 text-sm', icon: 'size-4', score: 'text-sm' },
    lg: { wrap: 'gap-2.5 px-4 py-2 text-base', icon: 'size-5', score: 'text-base' },
  }[size];

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border font-medium',
        presentation.surface,
        presentation.border,
        presentation.text,
        sizes.wrap,
        className,
      )}
      // Screen readers get the meaning, not just the number.
      role="status"
      aria-label={`Approval likelihood ${percent}. ${presentation.label}.`}
    >
      <Icon className={sizes.icon} aria-hidden="true" />
      <span className={cn('font-semibold tabular-nums', sizes.score)}>{percent}</span>
      {showLabel ? (
        <>
          <span aria-hidden="true" className="opacity-40">
            ·
          </span>
          <span>{presentation.label}</span>
        </>
      ) : null}
    </span>
  );
}

/**
 * The full-width score panel shown at the top of a scan result.
 *
 * The bar is a secondary encoding of the same number and is marked
 * decorative — the badge above it already announces the value.
 */
export function ApprovalScorePanel({
  approval,
  className,
}: {
  approval: ApprovalAssessment;
  className?: string;
}) {
  const presentation = BAND_PRESENTATION[approval.band];
  const Icon = ICONS[presentation.icon];
  const percent = Math.round(approval.score * 100);

  return (
    <section
      className={cn(
        'rounded-lg border p-5',
        presentation.surface,
        presentation.border,
        className,
      )}
      aria-labelledby="approval-heading"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <Icon
            className={cn('mt-0.5 size-6 shrink-0', presentation.text)}
            aria-hidden="true"
          />
          <div>
            <h2 id="approval-heading" className={cn('text-sm font-semibold', presentation.text)}>
              {presentation.label}
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{approval.rationale}</p>
          </div>
        </div>

        <div className="text-right">
          <div className={cn('text-4xl font-bold tabular-nums leading-none', presentation.text)}>
            {percent}
            <span className="text-2xl">%</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {approval.basis === 'payer_rule' && approval.payer_slug
              ? `${approval.payer_slug.toUpperCase()} criteria`
              : 'National baseline'}
          </p>
        </div>
      </div>

      <div
        className="mt-4 h-2 w-full overflow-hidden rounded-full bg-background/60"
        role="presentation"
      >
        <div
          className={cn('h-full rounded-full transition-[width] duration-500', presentation.bar)}
          style={{ width: `${Math.max(percent, 2)}%` }}
        />
      </div>

      {/* Threshold ticks make the band boundaries legible rather than implied. */}
      <div className="mt-1.5 flex justify-between text-[10px] uppercase tracking-wide text-muted-foreground">
        <span>0</span>
        <span className="translate-x-2">50 · review</span>
        <span>80 · likely · 100</span>
      </div>
    </section>
  );
}
