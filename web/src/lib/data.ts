import boardJson from "@/data/board.json";
import entitiesJson from "@/data/entities.json";
import feedJson from "@/data/feed.json";
import metaJson from "@/data/meta.json";
import type { BoardRow, Dossier, FeedItem, Meta, PersonOut } from "./types";

// The pipeline's export is the only data source. These casts are the one
// place the app trusts the JSON to match the types.
export const board = boardJson as unknown as BoardRow[];
export const feed = feedJson as unknown as FeedItem[];
export const meta = metaJson as unknown as Meta;

const dossiers = entitiesJson as unknown as Record<string, Dossier>;

export function getDossier(slug: string): Dossier | undefined {
  return dossiers[slug];
}

export function allSlugs(): string[] {
  return Object.keys(dossiers);
}

export type RoleGroup = "founder" | "researcher" | "contact" | "other";

export type PersonRow = {
  name: string;
  /** The title as the source gives it, without the Form D relationship boilerplate. */
  role: string | null;
  /** Which kind of person this is, for the views on the founders page. */
  group: RoleGroup;
  /** Where the name came from, in plain words: "Named on SEC Form D". */
  origin: string | null;
  company: string;
  slug: string;
  sector: Dossier["sector"];
  rank: number;
  edge: number;
  pedigree: string[];
  affiliations: string[];
  hIndex: number | null;
  citations: number | null;
  bio: string | null;
  link: string | null;
  /** Sort key: the company's edge, lifted by the person's own record. */
  weight: number;
};

const BRIEF_ROLE = "Named in the brief";

/** Lower case, accents folded, punctuation to spaces. Letters of any script are kept, so "António" stays one word. */
const squashName = (s: string) =>
  s
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();

/** Same words in any order: "Kofi Asante" is "Asante Kofi", but "Jeff Dailey" is not "Jeffrey F. Dailey". */
const nameKey = (s: string) => squashName(s).split(" ").sort().join(" ");

/** A regulatory contact is not a founder, and the author of a post is not a researcher. */
function roleGroup(role: string | null): RoleGroup {
  const r = (role ?? "").toLowerCase();
  // Whole words for the initials: "director" contains "cto".
  if (/founder|\b(ceo|cto|coo|cpo)\b|chief|president|executive officer|director|promoter|named in the brief/.test(r))
    return "founder";
  if (/submission|waiver|contact|responsible|filer|signer|applicant/.test(r)) return "contact";
  if (/investigator|author/.test(r) && !/\b(hn|post)\b/.test(r)) return "researcher";
  return "other";
}

/** Founders and officers first, filing contacts last. */
const ROLE_WEIGHT: Record<RoleGroup, number> = { founder: 1.35, researcher: 1.1, other: 1, contact: 0.6 };

const FORM_D_BOXES = "(?:Executive Officer|Director|Promoter)";
const FORM_D_TITLE = new RegExp(`^${FORM_D_BOXES}(?:, ${FORM_D_BOXES})*\\s*\\((.+)\\)$`);

/**
 * "Executive Officer, Director (Chief Executive Officer)" is Form D's checkboxes
 * around a title: keep the title. "Named in the brief" is where the name came
 * from, not a role, and the origin line already says it.
 */
function shortRole(role: string | null): string | null {
  if (!role || role === BRIEF_ROLE) return null;
  return role.match(FORM_D_TITLE)?.[1] ?? role;
}

/** Where each collector finds a name, said the way a person would say it. */
const ORIGIN: Record<string, string> = {
  brief: "Named in the research brief",
  sec_form_d: "Named on SEC Form D",
  yc_directory: "Listed as a founder by Y Combinator",
  accelerators: "Listed by the accelerator",
  hn_launch: "Posted the launch on Hacker News",
  hn_hiring: "Posted the hiring notice on Hacker News",
  github_velocity: "Top contributor to the repository",
  research_affil: "Author on a paper under the company's name",
  energy_grants: "Principal investigator on an NSF or ARPA-E award",
  sbir_awards: "Principal investigator on an SBIR award",
  nrc_adams: "Signed a submission to the NRC",
  fcc_els: "Contact on an FCC filing",
  faa_uas: "Named on an FAA filing",
};

function origin(sources: string[]): string | null {
  const parts = sources.flatMap((slug) => (ORIGIN[slug] ? [ORIGIN[slug]] : []));
  if (!parts.length) return null;
  // One sentence: the first in full, the rest in lower case after it.
  return [parts[0], ...parts.slice(1).map((x) => x[0].toLowerCase() + x.slice(1))].join("; ");
}

/** One record per person in a company: two collectors, or one form filed twice, can name the same person. */
function mergePeople(list: PersonOut[]): PersonOut[] {
  // A title from a filing is firmer than a mention in the brief.
  const firmness = (p: PersonOut) => ROLE_WEIGHT[roleGroup(p.role ?? null)] - (p.role === BRIEF_ROLE ? 0.01 : 0);
  const byName = new Map<string, PersonOut>();
  for (const p of list) {
    // A name with no letters in it has no words to compare: it only matches itself.
    const key = nameKey(p.name) || p.name;
    const seen = byName.get(key);
    if (!seen) {
      byName.set(key, p);
      continue;
    }
    // The firmer record keeps its name and role; the other fills the gaps.
    const [keep, fill] = firmness(p) > firmness(seen) ? [p, seen] : [seen, p];
    byName.set(key, {
      ...fill,
      ...keep,
      affiliations: [...new Set([...(keep.affiliations ?? []), ...(fill.affiliations ?? [])])],
      pedigree: [...new Set([...(keep.pedigree ?? []), ...(fill.pedigree ?? [])])],
      sources: [...new Set([...(keep.sources ?? []), ...(fill.sources ?? [])])],
      links: { ...fill.links, ...keep.links },
      facts: { ...fill.facts, ...keep.facts },
    });
  }
  return [...byName.values()];
}

/** Everyone named on a ranked company, strongest record first. */
export function people(): PersonRow[] {
  const out: PersonRow[] = [];
  for (const d of Object.values(dossiers)) {
    const self = squashName(d.name);
    for (const p of mergePeople(d.people)) {
      const h = typeof p.facts?.h_index === "number" ? p.facts.h_index : null;
      const cites = typeof p.facts?.cited_by_count === "number" ? p.facts.cited_by_count : null;
      const pedigree = p.pedigree ?? [];
      const group = roleGroup(p.role ?? null);
      const link =
        (p.links && Object.values(p.links).find((v) => typeof v === "string" && v.startsWith("http"))) || null;
      // The company's own name is not a prior affiliation.
      const affiliations = (p.affiliations ?? []).filter((a) => !self || !squashName(a).startsWith(self));
      out.push({
        name: p.name,
        role: shortRole(p.role ?? null),
        group,
        origin: origin(p.sources ?? []),
        company: d.name,
        slug: d.slug,
        sector: d.sector,
        rank: d.rank,
        edge: d.edge,
        pedigree,
        affiliations,
        hIndex: h && h > 0 ? h : null,
        citations: cites,
        bio: typeof p.facts?.bio === "string" && p.facts.bio ? p.facts.bio : null,
        link,
        weight:
          d.edge *
          ROLE_WEIGHT[group] *
          (1 + 0.6 * Math.min(pedigree.length, 3) + Math.min(h ?? 0, 60) / 60 + (p.facts?.bio ? 0.15 : 0)),
      });
    }
  }
  return out.sort((a, b) => b.weight - a.weight);
}
