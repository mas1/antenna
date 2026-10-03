// The shape of the JSON the pipeline writes to src/data. Keep in step with
// pipeline/antenna/export.py: that file is the source of truth.

export const FAMILIES = [
  "capital",
  "regulatory",
  "github",
  "research",
  "hiring",
  "launch",
  "social",
  "traffic",
] as const;
export type Family = (typeof FAMILIES)[number];

export const SECTORS = [
  "robotics",
  "autonomy",
  "defense",
  "energy",
  "manufacturing",
  "semiconductors",
  "space",
] as const;
export type Sector = (typeof SECTORS)[number] | "other";

export type Flag = "new" | "convergent" | "pre-consensus" | "pedigree";

export type Why = {
  family: Family;
  title: string;
  url: string;
  occurredAt: string;
  /** An undated reading, not an event. */
  state?: boolean;
};

/** One row of the board. Small on purpose: this ships to the client. */
export type BoardRow = {
  slug: string;
  name: string;
  kind: "company" | "project" | "person";
  oneLiner: string | null;
  domain: string | null;
  location: string | null;
  sector: Sector;
  rank: number;
  /** Rank seven days ago, from the backcast. Null when it was not ranked. */
  rankPrev: number | null;
  /** 0..100 */
  edge: number;
  /** All 0..1 */
  momentum: number;
  fit: number;
  earliness: number;
  team: number;
  families: Record<Family, number>;
  /** How many independent families are firing. */
  convergence: number;
  /** Weekly edge, oldest first, 13 points ending today. */
  history: number[];
  why: Why[];
  /** The newest dated event (never an undated reading), or null. */
  latest: Why | null;
  /** Founding year, from a filing or from review. */
  founded: number | null;
  /** Money raised in dollars, where a reviewer confirmed it from a source. */
  raisedUsd: number | null;
  /** Per family: how many signals, and the two strongest. Drives the glyph hover. */
  bySignalFamily: Partial<
    Record<Family, { count: number; items: { title: string; occurredAt: string; state?: boolean }[] }>
  >;
  signalCount: number;
  /** Dates of the first and latest dated events. Undated readings do not count. */
  firstSignalAt: string;
  lastSignalAt: string;
  flags: Flag[];
  /** A research brief exists for this company. */
  hasBrief?: boolean;
  /** Set when a person has reviewed the company. */
  stage?: "formation" | "early" | "growth" | "incumbent";
};

/** Prose about the evidence, written by a research agent and fact-checked. */
export type Brief = {
  generatedAt: string;
  /** What the company builds, in two or three sentences. */
  summary: string;
  /** Why it matters for this thesis. */
  thesis: string;
  /** What the filings and signals imply about stage and round. */
  stage: string;
  team: { name: string; background: string }[];
  /** What to ask on a first call. */
  questions: string[];
  /** What could make this a pass. */
  risks: string[];
  /** Claims the checker could not confirm, stated as open. */
  unverified: string[];
  sources: { title: string; url: string }[];
};

export type SignalOut = {
  id: string;
  family: Family;
  kind: string;
  source: string;
  title: string;
  occurredAt: string;
  url: string;
  /** Strength when it happened, 0..1 */
  strength: number;
  /** Strength today, after decay, 0..1 */
  now: number;
  value?: number;
  unit?: string | null;
  /** True for an undated reading (a DNS record, a lifetime count). */
  state?: boolean;
  series?: { t: string; v: number }[];
};

export type PersonOut = {
  name: string;
  role?: string;
  github?: string;
  openalex_id?: string;
  affiliations?: string[];
  links?: Record<string, string>;
  facts?: Record<string, number | string | boolean>;
  pedigree?: string[];
  sources?: string[];
};

/** First signal against first public announcement. Positive days: signal first. */
export type Lead = {
  firstSignalAt: string;
  firstSignal: string;
  firstSignalUrl: string;
  announcedAt: string;
  announcement: string;
  announcementUrl: string;
  days: number;
};

/** Everything known about one entity. Server-side only. */
export type Dossier = BoardRow & {
  description: string | null;
  github: string | null;
  links: Record<string, string>;
  aliases: string[];
  sectors: Partial<Record<Sector, number>>;
  terms: string[];
  /** What already makes this company known, by component, 0..1 */
  consensus: Record<string, number>;
  metrics: Record<string, number>;
  historyDates: string[];
  momentumHistory: number[];
  people: PersonOut[];
  signals: SignalOut[];
  brief?: Brief;
  lead?: Lead;
  /** A research agent looked for news of this company and found none. */
  unannounced?: boolean;
  /** Corrections from the review file, with their source. */
  review?: { note?: string; source?: string; founded?: number; raised_usd?: number };
};

export type FeedItem = Pick<
  SignalOut,
  "id" | "family" | "kind" | "source" | "title" | "occurredAt" | "url" | "strength"
> & {
  slug: string;
  name: string;
  sector: Sector;
  rank: number;
  stage: "formation" | "early" | "growth" | "incumbent" | null;
};

export type CollectorRun = {
  source: string;
  seconds: number | null;
  emitted: number | null;
  inserted: number | null;
  error: string | null;
};

export type Meta = {
  generatedAt: string;
  asOf: string;
  lookbackDays: number;
  totals: {
    signals: number;
    entities: number;
    sources: number;
    scored: number;
    onThesis: number;
    board: number;
    newThisWeek: number;
    convergent: number;
  };
  bySector: Partial<Record<Sector, number>>;
  byFamily: Partial<Record<Family, number>>;
  bySource: Record<string, number>;
  collectors: CollectorRun[];
  weights: Record<Family, number>;
  halfLives: Record<Family, number>;
  thesis: Record<string, string[]>;
  portfolioSeen: { name: string; edge: number; momentum: number }[];
  reviewed?: { entries: number; excluded: number; awaiting?: number };
  leads?: (Lead & { slug: string; name: string; rank: number })[];
  /** Briefed companies for which no public announcement could be found. */
  unannounced?: { slug: string; name: string; rank: number; firstSignalAt: string }[];
  briefs?: number;
};
