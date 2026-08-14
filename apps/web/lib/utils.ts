import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

import type { ApprovalBand } from '@clinchec/shared-types';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Presentation tokens for an approval band.
 *
 * Colour alone never carries the meaning: every band ships a label and an icon
 * name alongside its palette, so the badge stays readable in greyscale and for
 * a clinician with a colour vision deficiency.
 */
export interface BandPresentation {
  label: string;
  short: string;
  icon: 'check-circle' | 'alert-triangle' | 'x-circle';
  text: string;
  surface: string;
  border: string;
  ring: string;
  bar: string;
  description: string;
}

export const BAND_PRESENTATION: Record<ApprovalBand, BandPresentation> = {
  green: {
    label: 'Likely approved',
    short: 'Likely',
    icon: 'check-circle',
    text: 'text-approve-foreground',
    surface: 'bg-approve-surface',
    border: 'border-approve-border',
    ring: 'ring-approve/30',
    bar: 'bg-approve',
    description: 'Documentation meets the criteria this payer applies. Submit as-is.',
  },
  amber: {
    label: 'Needs attention',
    short: 'Review',
    icon: 'alert-triangle',
    text: 'text-caution-foreground',
    surface: 'bg-caution-surface',
    border: 'border-caution-border',
    ring: 'ring-caution/30',
    bar: 'bg-caution',
    description: 'Defensible but incomplete. Strengthen the gaps before submitting.',
  },
  red: {
    label: 'Likely denied',
    short: 'At risk',
    icon: 'x-circle',
    text: 'text-deny-foreground',
    surface: 'bg-deny-surface',
    border: 'border-deny-border',
    ring: 'ring-deny/30',
    bar: 'bg-deny',
    description: 'Core medical-necessity documentation is missing. Fix before submitting.',
  },
};

export function formatPercent(score: number): string {
  return `${Math.round(score * 100)}%`;
}

export function formatDuration(value: number, unit: string): string {
  const rounded = Number.isInteger(value) ? value : Number(value.toFixed(1));
  const singular = unit.replace(/s$/, '');
  return `${rounded} ${rounded === 1 ? singular : `${singular}s`}`;
}

/** "3 hours ago" / "12 days ago" — used for payer-rule freshness. */
export function formatRelativeHours(hours: number): string {
  if (hours < 1) return 'just now';
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.round(days / 30)}mo ago`;
}

export function titleCase(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}
