import type { Family, Sector } from "./types";

export const FAMILY_LABEL: Record<Family, string> = {
  capital: "Capital",
  regulatory: "Regulatory",
  github: "Code",
  research: "Research",
  hiring: "Hiring",
  launch: "Launch",
  social: "Attention",
  traffic: "Traffic",
};

/** Two-letter codes for the dense glyph and the feed. */
export const FAMILY_CODE: Record<Family, string> = {
  capital: "CP",
  regulatory: "RG",
  github: "GH",
  research: "RS",
  hiring: "HR",
  launch: "LN",
  social: "AT",
  traffic: "TR",
};

export const FAMILY_BLURB: Record<Family, string> = {
  capital: "Form D filings, SBIR and federal awards",
  regulatory: "FCC, FAA and other licences hardware cannot avoid",
  github: "Star velocity and new organizations",
  research: "Papers and the people leaving labs",
  hiring: "Open roles and who is being hired",
  launch: "Show HN, Launch HN and accelerator batches",
  social: "Developer attention over time",
  traffic: "Domain rank and infrastructure footprint",
};

export const SECTOR_LABEL: Record<Sector, string> = {
  robotics: "Robotics",
  autonomy: "Autonomy",
  defense: "Defense",
  energy: "Energy",
  manufacturing: "Manufacturing",
  semiconductors: "Semiconductors",
  space: "Space",
  other: "Other",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Sep 18" for this year, "Sep 18, 2025" otherwise. Dates are ISO strings. */
export function shortDate(iso: string, asOf?: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return iso;
  const base = `${MONTHS[m - 1]} ${d}`;
  return asOf && Number(asOf.slice(0, 4)) === y ? base : `${base}, ${y}`;
}

/** Whole days between two ISO dates (b - a). */
export function daysBetween(a: string, b: string): number {
  const ms = Date.parse(b.slice(0, 10)) - Date.parse(a.slice(0, 10));
  return Math.round(ms / 86_400_000);
}

/** "today", "3d ago", "5w ago". */
export function ago(iso: string, asOf: string): string {
  const n = daysBetween(iso, asOf);
  if (n <= 0) return "today";
  if (n === 1) return "1d ago";
  if (n < 14) return `${n}d ago`;
  if (n < 70) return `${Math.round(n / 7)}w ago`;
  return `${Math.round(n / 30)}mo ago`;
}

/** "Oct 2, 09:04" on the reader's own clock, so it is for the browser only. */
export function localStamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const two = (n: number) => String(n).padStart(2, "0");
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** "74K", "9.2K", "1.3M": short enough for a table cell. Money is "$" + compact(n). */
export function compact(n: number): string {
  const a = Math.abs(n);
  const one = (v: number) => v.toFixed(1).replace(/\.0$/, "");
  // Each step starts where the one below would round up into four digits,
  // so 999,600 reads "1M" and not "1000K".
  if (a >= 999_950_000) return `${one(n / 1e9)}B`;
  if (a >= 999_500) return `${one(n / 1e6)}M`;
  if (a >= 9_950) return `${Math.round(n / 1e3)}K`;
  if (a >= 999.5) return `${one(n / 1e3)}K`;
  return `${Math.round(n)}`;
}
