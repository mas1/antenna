import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DossierTools } from "@/components/dossier/DossierTools";
import { HistoryChart } from "@/components/dossier/HistoryChart";
import { CountUp } from "@/components/ui/CountUp";
import { FamilyGlyph } from "@/components/ui/FamilyGlyph";
import { Meter } from "@/components/ui/Meter";
import { RankDelta } from "@/components/ui/RankDelta";
import { Section } from "@/components/ui/Section";
import { allSlugs, board, getDossier, meta, shortRole } from "@/lib/data";
import { FAMILY_LABEL, SECTOR_LABEL, compact, shortDate } from "@/lib/format";
import { FAMILIES, type Dossier, type PersonOut, type SignalOut } from "@/lib/types";

export function generateStaticParams() {
  const slugs = allSlugs();
  // A static export needs at least one param even before the first run.
  return (slugs.length ? slugs : ["_"]).map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: PageProps<"/c/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const d = getDossier(slug);
  return d ? { title: `${d.name} · Antenna`, description: d.oneLiner ?? undefined } : { title: "Antenna" };
}

/** What already makes a company visible, named to sit in a sentence. */
const CONSENSUS_LABEL: Record<string, string> = {
  stars: "GitHub stars",
  press: "Hacker News mentions",
  traffic: "web traffic",
  headcount: "headcount",
  capital: "capital raised",
  age: "company age",
  awards: "federal awards",
  stage: "funding stage",
};

const STAGE_LABEL: Record<string, string> = {
  formation: "Formation stage",
  early: "Early stage",
  growth: "Growth stage",
  incumbent: "Established",
};

/** Hover text for the stage names that need one. */
const STAGE_HINT: Record<string, string> = {
  formation: "Pre-seed or seed",
  early: "Series A or B",
};

/** Hover text on a "First public mention" label. */
const FIRST_MENTION = "The earliest public mention a research agent found and a second confirmed";

const LINK_LABEL: Record<string, string> = {
  website: "Website",
  github: "GitHub",
  twitter: "X",
  x: "X",
  linkedin: "LinkedIn",
  yc: "Y Combinator",
  hn: "Hacker News",
  careers: "Careers",
  jobs: "Jobs",
  sec_filing: "SEC filing",
  sec_filings: "All SEC filings",
  fpds_history: "Federal awards",
  usaspending: "Federal awards",
  sbir_profile: "SBIR profile",
  hn_item: "Hacker News post",
  hn_user: "Hacker News profile",
  repo: "Repository",
  project_page: "Project page",
  ror: "ROR record",
  nrc_docket: "NRC docket",
  trademark: "Trademark",
  paper: "Paper",
  nsf_award: "NSF award",
  arpa_e_project: "ARPA-E project",
  huggingface: "Hugging Face",
  crunchbase: "Crunchbase",
};

/** A family counts as firing from this strength up, here and in the score. */
const FIRING = 0.12;

// Date, family, signal, strength, arrow. A phone stacks the date on the
// family and drops the strength.
const SIGNAL_COLS =
  "grid grid-cols-[5.5rem_minmax(0,1fr)_auto] items-baseline gap-x-3 sm:grid-cols-[5.5rem_6rem_minmax(0,1fr)_9rem_1.25rem] sm:gap-x-5";

const LINK_ORDER = ["website", "github", "careers", "jobs", "yc", "hn", "x", "twitter", "linkedin"];
function linkRank(key: string): number {
  const i = LINK_ORDER.indexOf(key);
  return i === -1 ? LINK_ORDER.length : i;
}

/** One address under two spellings: with or without "www." or a closing slash. */
function sameUrl(a: string, b: string): boolean {
  const bare = (u: string) => u.toLowerCase().replace(/^https?:\/\/(www\.)?/, "").replace(/\/+$/, "");
  return bare(a) === bare(b);
}

/** A person's affiliation that is just this company, under its name or one of its aliases. */
function isSelf(affiliation: string, names: string[]): boolean {
  const sq = (x: string) => x.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const a = sq(affiliation);
  return names.some((n) => sq(n) !== "" && a.startsWith(sq(n)));
}

/** Same words in any order: a filing writes "Asante Kofi" where the brief writes "Kofi Asante". */
function nameKey(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").sort().join(" ") || name;
}

/** One entry per person. The first record keeps its name and role; a later one fills the gaps. */
function onePerPerson(list: PersonOut[]): PersonOut[] {
  const seen = new Map<string, PersonOut>();
  for (const p of list) {
    const key = nameKey(p.name);
    const first = seen.get(key);
    seen.set(
      key,
      first
        ? {
            ...p,
            ...first,
            affiliations: [...new Set([...(first.affiliations ?? []), ...(p.affiliations ?? [])])],
            pedigree: [...new Set([...(first.pedigree ?? []), ...(p.pedigree ?? [])])],
            links: { ...p.links, ...first.links },
            facts: { ...p.facts, ...first.facts },
          }
        : p,
    );
  }
  return [...seen.values()];
}

/** A web search for a person at this company, for names that came without a link. */
function searchUrl(person: string, company: string): string {
  // The name is quoted, so a nickname in brackets or a trailing ", PhD" would
  // have to appear word for word: both are left out of the query.
  const name =
    person
      .replace(/\s*\([^)]*\)/g, "")
      .replace(/,.*$/, "")
      .replace(/"/g, "")
      .trim() || person;
  return `https://www.google.com/search?q=${encodeURIComponent(`"${name}" ${company}`)}`;
}

/**
 * What each term does to edge, which starts from momentum. Fit, earliness
 * and team scale it within a floor, and a single family firing on its own is
 * discounted. Keep in step with pipeline/antenna/score.py.
 */
function scale(d: Dossier) {
  return {
    fit: 0.25 + 0.75 * d.fit,
    earliness: 0.4 + 0.6 * d.earliness,
    team: 0.85 + 0.15 * d.team,
    lone: FAMILIES.filter((f) => d.families[f] >= FIRING).length < 2 ? 0.85 : 1,
  };
}

const times = (x: number) => `× ${x.toFixed(2)}`;
/** A 0..1 term as the page prints it, 0 to 100. */
const points = (x: number) => Math.round(x * 100);

function explain(d: Dossier, people: PersonOut[], pedigree: string[]) {
  const firing = FAMILIES.filter((f) => d.families[f] >= FIRING);
  // "unmeasured" is a default, not a measurement, so it is never listed.
  const known = Object.entries(d.consensus)
    .filter(([k]) => k !== "unmeasured")
    .sort((a, b) => b[1] - a[1]);
  return {
    momentum: firing.length
      ? firing.map((f, i) => (i ? FAMILY_LABEL[f].toLowerCase() : FAMILY_LABEL[f])).join(", ")
      : "No strong signals right now",
    fit: d.terms.length ? `Matched on ${d.terms.slice(0, 5).join(", ")}` : "Weak match to the thesis",
    earliness: d.consensus.unmeasured
      ? "Footprint not measured yet"
      : known.length
        ? `Already visible: ${known.slice(0, 2).map(([k]) => CONSENSUS_LABEL[k] ?? k).join(", ")}`
        : "Almost no public footprint",
    // Every measured component with its value, for the hover on the earliness cell.
    footprint:
      known.length && !d.consensus.unmeasured
        ? `Footprint, 0 to 100: ${known.map(([k, v]) => `${CONSENSUS_LABEL[k] ?? k} ${points(v)}`).join(", ")}`
        : undefined,
    team: pedigree.length
      ? `Team from ${pedigree.slice(0, 3).join(", ")}`
      : people.length
        ? `${people.length} named ${people.length === 1 ? "person" : "people"}, none from a top lab or company`
        : "No people identified yet",
  };
}

/** One line of the signal list. An undated signal leaves the date cell empty: its sub-head says so. */
function SignalRow({ s }: { s: SignalOut }) {
  const then = Math.round(s.strength * 100);
  const now = Math.round(s.now * 100);
  const strength = s.state ? `Strength ${now} today` : `Strength ${then} when it happened, ${now} today`;
  return (
    <li>
      <a
        href={s.url}
        target="_blank"
        rel="noreferrer"
        className={`group ${SIGNAL_COLS} border-b border-rule py-3 transition-colors duration-150 hover:bg-paper-2`}
      >
        {/* One stacked cell on a phone, two cells of the row from sm up. */}
        <span className="flex flex-col gap-0.5 sm:contents">
          <span className={`num text-[12px] text-ink-3 ${s.state ? "max-sm:hidden" : ""}`}>
            {s.state ? null : shortDate(s.occurredAt, meta.asOf)}
          </span>
          <span className="label !text-ink-4">{FAMILY_LABEL[s.family]}</span>
        </span>
        <span className="min-w-0 text-[14.5px] leading-snug">{s.title}</span>
        <span className="hidden sm:block" role="img" aria-label={strength} title={strength}>
          <span className="relative block h-[3px] w-full bg-paper-3">
            <span className="absolute inset-y-0 left-0 bg-rule-2" style={{ width: `${s.strength * 100}%` }} />
            <span className="absolute inset-y-0 left-0 bg-ink" style={{ width: `${s.now * 100}%` }} />
          </span>
        </span>
        <span className="text-[12px] text-ink-4 transition-colors group-hover:text-ink">↗</span>
      </a>
    </li>
  );
}

export default async function Page({ params }: PageProps<"/c/[slug]">) {
  const { slug } = await params;
  const d = getDossier(slug);
  if (!d) notFound();

  const prev = board[d.rank - 2];
  const next = board[d.rank];
  const people = onePerPerson(d.people);
  const pedigree = [...new Set(people.flatMap((p) => p.pedigree ?? []))];
  const ex = explain(d, people, pedigree);
  const x = scale(d);
  // Momentum is where edge starts, so it has no factor: a space holds its line.
  const terms = [
    { key: "Momentum", value: d.momentum, applies: "\u00a0", range: null, text: ex.momentum },
    { key: "Thesis fit", value: d.fit, applies: times(x.fit), range: "0.25 to 1", text: ex.fit },
    { key: "Earliness", value: d.earliness, applies: times(x.earliness), range: "0.40 to 1", text: ex.earliness, title: ex.footprint },
    // A team that adds nothing gets a dash, not a zero and an empty bar. That
    // includes a trace of one, which would print as 0.
    { key: "Team", value: d.team, applies: times(x.team), range: "0.85 to 1", text: ex.team, noLift: points(d.team) === 0 },
  ];
  const facts = [
    { text: SECTOR_LABEL[d.sector] },
    { text: d.location },
    { text: d.founded ? `Founded ${d.founded}` : null },
    { text: d.raisedUsd != null ? `Raised $${compact(d.raisedUsd)}` : null },
    { text: d.stage ? STAGE_LABEL[d.stage] : null, hint: d.stage ? STAGE_HINT[d.stage] : undefined },
    { text: d.kind !== "company" ? (d.kind === "project" ? "Open-source project" : "Person") : null },
  ].filter((f) => f.text);
  // Two flags go unsaid: "pre-consensus" follows the stage in the facts line,
  // and "convergent" is the family count beside the glyph.
  const isNew = d.flags.includes("new");
  const team = d.flags.includes("pedigree") && pedigree.length > 0 ? `Team from ${pedigree.slice(0, 3).join(", ")}` : null;
  // The brief's "Stage and round" covers the same ground, so the note gives way to it.
  const review = !d.brief && d.review?.note ? d.review : null;
  const links = Object.entries(d.links)
    .filter(([, v]) => typeof v === "string" && v.startsWith("http"))
    // The note's own source is linked beside it already.
    .filter(([, v]) => !(review?.source && sameUrl(v, review.source)))
    .sort((a, b) => linkRank(a[0]) - linkRank(b[0]));
  // Events have a date; undated signals (a DNS record, a lifetime count) are true as of the run and are not news.
  const dated = d.signals.filter((s) => !s.state);
  const undated = d.signals.filter((s) => s.state);
  // Where "Why now" already lists every signal, the full list would only repeat it.
  const inWhy = new Set(d.why.map((w) => w.url + w.title));
  const moreSignals = d.signals.some((s) => !inWhy.has(s.url + s.title));
  // What sits behind each box of the glyph: counts and the two strongest signals.
  const glyphDetails = Object.fromEntries(
    FAMILIES.map((f) => {
      const inFamily = d.signals.filter((s) => s.family === f).sort((a, b) => b.now - a.now);
      const items = inFamily.slice(0, 2).map((s) => ({ title: s.title, occurredAt: s.occurredAt, state: s.state }));
      return [f, { count: inFamily.length, items }];
    }).filter(([, v]) => (v as { count: number }).count > 0),
  );

  return (
    <article className="mx-auto max-w-[1320px] px-5 sm:px-8">
      <DossierTools
        slug={d.slug}
        name={d.name}
        rank={d.rank}
        total={meta.totals.board}
        prev={prev && { slug: prev.slug, name: prev.name }}
        next={next && { slug: next.slug, name: next.name }}
      />

      {/* Header: enough to qualify the company without scrolling. */}
      <header className="grid grid-cols-12 gap-x-6 gap-y-6 py-8">
        <div className="col-span-12 lg:col-span-8">
          <p className="label">
            {facts.map((f, i) => (
              <span key={f.text} title={f.hint}>
                {i > 0 && " · "}
                {f.text}
              </span>
            ))}
          </p>
          <h1 className="display mt-3 text-[36px] sm:text-[44px]">{d.name}</h1>
          {d.oneLiner && (
            <p className="mt-3 max-w-[60ch] font-serif text-[18px] leading-snug text-ink-2 sm:text-[20px]">
              {d.oneLiner}
            </p>
          )}
          {review && (
            <p className="mt-4 max-w-[72ch] text-[14px] leading-relaxed text-ink-2">
              <span className="label mr-3">Review note</span>
              {review.note}
              {review.source && (
                <>
                  {" "}
                  <a href={review.source} target="_blank" rel="noreferrer" className="underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink">
                    Source ↗
                  </a>
                </>
              )}
            </p>
          )}
          {links.length > 0 && (
            <ul className="mt-5 flex flex-wrap gap-x-6 gap-y-2">
              {links.slice(0, 7).map(([k, v]) => (
                <li key={k}>
                  <a
                    href={v}
                    target="_blank"
                    rel="noreferrer"
                    className="label underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:!text-ink hover:decoration-ink"
                  >
                    {LINK_LABEL[k] ?? k.replace(/_/g, " ")} ↗
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="col-span-12 lg:col-span-4 lg:border-l lg:border-rule lg:pl-8">
          <p className="label" title="Edge, 0 to 100">
            Edge
          </p>
          <p className="mt-2 flex items-baseline gap-4">
            {/* A short count: flipping through dossiers should not replay a long one each time. */}
            <CountUp value={d.edge} duration={0.3} className="text-[60px] leading-[0.9] tracking-[-0.04em]" />
            {/* The rank, then what it did in a week. */}
            <span className="label">
              #{d.rank} ·{" "}
              {isNew ? (
                <span className="!text-signal" title="First signal in the last seven days">
                  New
                </span>
              ) : d.rankPrev == null ? (
                "Not ranked 7d ago"
              ) : d.rankPrev === d.rank ? (
                "Unchanged in 7d"
              ) : (
                <>
                  <RankDelta rank={d.rank} prev={d.rankPrev} /> in 7d
                </>
              )}
            </span>
          </p>
          <div className="mt-4 flex items-center gap-3">
            <FamilyGlyph families={d.families} details={glyphDetails} asOf={meta.asOf} size={14} gap={3} align="left" />
            <span className="label">{d.convergence} of 8 families</span>
          </div>
          {team && <p className="label mt-3 !text-ink">{team}</p>}
        </div>
      </header>

      <div className="flex flex-col gap-14">
        {/* Why now */}
        {d.why.length > 0 && (
          <Section label="Why now">
            <ol className="flex flex-col">
              {d.why.map((w, i) => (
                <li key={w.url + i} className={`py-4 last:pb-0 ${i ? "border-t border-rule" : "pt-0"}`}>
                  <a
                    href={w.url}
                    target="_blank"
                    rel="noreferrer"
                    className="group/source font-serif text-[21px] leading-snug tracking-[-0.01em] sm:text-[24px]"
                  >
                    <span className="underline decoration-transparent decoration-1 underline-offset-4 transition-colors group-hover/source:decoration-ink">
                      {w.title}
                    </span>{" "}
                    <span aria-hidden className="font-sans text-[13px] text-ink-4 transition-colors group-hover/source:text-ink">
                      ↗
                    </span>
                  </a>
                  <p className="label mt-1.5">
                    {FAMILY_LABEL[w.family]} · {w.state ? "Undated" : shortDate(w.occurredAt, meta.asOf)}
                  </p>
                </li>
              ))}
            </ol>
          </Section>
        )}

        {/* Brief */}
        {d.brief && (
          <Section
            label="Brief"
            note={`Written by a research agent on ${shortDate(d.brief.generatedAt, meta.asOf)} from the sources below. A second agent checked each claim.`}
          >
            <p className="max-w-[64ch] font-serif text-[20px] leading-[1.4] text-ink sm:text-[22px]">{d.brief.summary}</p>
            <div className="mt-8 grid grid-cols-1 gap-x-10 gap-y-7 md:grid-cols-2">
              <div className="border-t border-rule pt-4">
                <h3 className="label">Why it fits</h3>
                <p className="mt-2 text-[14.5px] leading-relaxed text-ink-2">{d.brief.thesis}</p>
              </div>
              <div className="border-t border-rule pt-4">
                <h3 className="label">Stage and round</h3>
                <p className="mt-2 text-[14.5px] leading-relaxed text-ink-2">{d.brief.stage}</p>
              </div>
              <div className="border-t border-rule pt-4">
                <h3 className="label">Ask on the first call</h3>
                <ol className="mt-2 flex flex-col gap-2">
                  {d.brief.questions.map((q, i) => (
                    <li key={q} className="flex gap-3 text-[14.5px] leading-relaxed text-ink-2">
                      <span className="num pt-0.5 text-[11px] text-ink-4">{String(i + 1).padStart(2, "0")}</span>
                      <span>{q}</span>
                    </li>
                  ))}
                </ol>
              </div>
              <div className="border-t border-rule pt-4">
                <h3 className="label">Risks</h3>
                <ul className="mt-2 flex flex-col gap-2">
                  {d.brief.risks.map((r) => (
                    <li key={r} className="flex gap-3 text-[14.5px] leading-relaxed text-ink-2">
                      <span className="pt-0.5 text-ink-4">–</span>
                      <span>{r}</span>
                    </li>
                  ))}
                </ul>
                {d.brief.unverified.length > 0 && (
                  <>
                    <h3 className="label mt-6">Could not confirm</h3>
                    <ul className="mt-2 flex flex-col gap-2">
                      {d.brief.unverified.map((r) => (
                        <li key={r} className="flex gap-3 text-[13.5px] leading-relaxed text-ink-3">
                          <span className="pt-0.5 text-ink-4">?</span>
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </div>
            {d.lead && d.lead.days <= 0 && (
              <p className="mt-7 border-t border-rule pt-4 text-[14px] leading-relaxed text-ink-2">
                <span className="label mr-3" title={FIRST_MENTION}>
                  First public mention · {shortDate(d.lead.announcedAt, meta.asOf)}
                </span>
                <a href={d.lead.announcementUrl} target="_blank" rel="noreferrer" className="underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink">
                  {d.lead.announcement}
                </a>
              </p>
            )}
            <p className="mt-7 flex flex-wrap gap-x-5 gap-y-1.5 border-t border-rule pt-4">
              <span className="label !text-ink-4">Sources</span>
              {d.brief.sources.map((src) => (
                <a
                  key={src.url}
                  href={src.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[12.5px] text-ink-3 underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:text-ink hover:decoration-ink"
                >
                  {src.title} ↗
                </a>
              ))}
            </p>
          </Section>
        )}

        {/* People. The wrapper carries the anchor other pages link to; the page's scroll padding clears the sticky header. */}
        {people.length > 0 && (
          <div id="people">
            <Section label="People">
              <ul className="grid grid-cols-1 gap-x-10 sm:grid-cols-2">
                {people.map((p) => {
                  const role = shortRole(p.role ?? null);
                  const link = p.links && Object.values(p.links).find((v) => typeof v === "string" && v.startsWith("http"));
                  const elsewhere = (p.affiliations ?? []).filter((a) => !isSelf(a, [d.name, ...d.aliases]));
                  return (
                    // An odd one out takes the whole last row, so its rule runs the full width.
                    <li key={p.name} className="min-w-0 border-b border-rule py-3.5 sm:last:odd:col-span-2">
                      <div className="flex flex-wrap items-baseline gap-x-3">
                        {link ? (
                          <a href={link} target="_blank" rel="noreferrer" className="group/name font-serif text-[18px]">
                            <span className="underline decoration-transparent decoration-1 underline-offset-4 transition-colors group-hover/name:decoration-ink">
                              {p.name}
                            </span>{" "}
                            <span aria-hidden className="font-sans text-[12px] text-ink-4 transition-colors group-hover/name:text-ink">
                              ↗
                            </span>
                          </a>
                        ) : (
                          <>
                            <span className="font-serif text-[18px]">{p.name}</span>
                            <a
                              href={searchUrl(p.name, d.name)}
                              target="_blank"
                              rel="noreferrer"
                              aria-label={`Search the web for ${p.name}`}
                              className="label shrink-0 underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:!text-ink hover:decoration-ink"
                            >
                              Search ↗
                            </a>
                          </>
                        )}
                      </div>
                      {/* On its own line, so a long filing role wraps instead of pushing the row wide. */}
                      {role && <p className="label mt-1 [overflow-wrap:anywhere]">{role}</p>}
                      {p.pedigree && p.pedigree.length > 0 && (
                        <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
                          {p.pedigree.map((o) => (
                            <span key={o} className="label !text-ink">
                              {o}
                            </span>
                          ))}
                        </p>
                      )}
                      {elsewhere.length > 0 && (
                        <p className="mt-1 text-[13px] leading-snug text-ink-3">{elsewhere.slice(0, 4).join(" · ")}</p>
                      )}
                      {p.facts && typeof p.facts.bio === "string" && p.facts.bio.length > 0 && (
                        <p className="mt-1.5 line-clamp-3 text-[13.5px] leading-snug text-ink-2">{p.facts.bio}</p>
                      )}
                      {p.facts && typeof p.facts.h_index === "number" && p.facts.h_index > 0 && (
                        <p className="num mt-1 text-[12px] text-ink-3">
                          h-index {p.facts.h_index}
                          {typeof p.facts.cited_by_count === "number" ? ` · ${compact(p.facts.cited_by_count)} citations` : ""}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </Section>
          </div>
        )}

        {/* Lead time */}
        {d.lead && d.lead.days > 0 && (
          <Section label="Lead time">
            <p className="max-w-[60ch] font-serif text-[22px] leading-snug sm:text-[26px]">
              The first signal was on file {d.lead.days} day{d.lead.days === 1 ? "" : "s"} before the company
              was first mentioned in public.
            </p>
            <div className="mt-6 grid grid-cols-1 gap-x-10 sm:grid-cols-2">
              <a href={d.lead.firstSignalUrl} target="_blank" rel="noreferrer" className="group border-t border-rule py-4">
                <span className="label">First signal · {shortDate(d.lead.firstSignalAt, meta.asOf)}</span>
                <span className="mt-1.5 block text-[14.5px] leading-snug text-ink-2 underline decoration-transparent underline-offset-4 transition-colors group-hover:decoration-ink">
                  {d.lead.firstSignal}
                </span>
              </a>
              <a href={d.lead.announcementUrl} target="_blank" rel="noreferrer" className="group border-t border-rule py-4">
                <span className="label" title={FIRST_MENTION}>
                  First public mention · {shortDate(d.lead.announcedAt, meta.asOf)}
                </span>
                <span className="mt-1.5 block text-[14.5px] leading-snug text-ink-2 underline decoration-transparent underline-offset-4 transition-colors group-hover:decoration-ink">
                  {d.lead.announcement}
                </span>
              </a>
            </div>
          </Section>
        )}

        {!d.lead && d.unannounced && d.brief && (
          <Section label="Lead time">
            <p
              className="max-w-[60ch] font-serif text-[22px] leading-snug sm:text-[26px]"
              title="A search for news of the company or its round, made when the brief was written"
            >
              No public announcement found. The first signal has been on file since{" "}
              {shortDate(d.firstSignalAt, meta.asOf)}.
            </p>
          </Section>
        )}

        {/* Score */}
        <Section label="Score">
          <div className="grid grid-cols-1 gap-x-10 gap-y-8 sm:grid-cols-2 xl:grid-cols-4">
            {terms.map((t, i) => (
              <div key={t.key} title={t.title}>
                {/* One height for the four heads, so they keep one baseline. */}
                <div className="flex h-[25px] items-baseline justify-between">
                  <span className="text-[14px] font-medium">{t.key}</span>
                  <span className="num text-[22px] leading-none">
                    {t.noLift ? (
                      <span className="text-ink-4">–</span>
                    ) : (
                      points(t.value)
                    )}
                  </span>
                </div>
                {/* The track's height is held when there is no bar, so the four columns stay level. */}
                <div className="mt-3 h-[3px]">
                  {!t.noLift && <Meter value={t.value} delay={0.1 + i * 0.08} label={t.key} />}
                </div>
                <p className="num mt-3 text-[12px]" title={t.range ? `Scales edge by ${t.range}` : undefined}>
                  {t.applies}
                </p>
                <p className="mt-1.5 text-[13px] leading-snug text-ink-3">{t.text}</p>
              </div>
            ))}
          </div>
          <p
            className="num mt-8 border-t border-rule pt-4 text-[13px]"
            title="Each factor is shown rounded. Edge is worked out from the exact values."
          >
            {points(d.momentum)} {times(x.fit)} {times(x.earliness)} {times(x.team)}
            {x.lone < 1 && (
              <>
                {" "}
                {times(x.lone)} <span className="text-ink-3">one family only</span>
              </>
            )}{" "}
            = {d.edge.toFixed(0)}
          </p>
        </Section>

        {/* Edge over twelve weeks */}
        <Section label="Edge, 12 weeks" title="Recomputed for each past week from signal dates">
          <HistoryChart
            dates={d.historyDates}
            values={d.history}
            markers={dated.map((s) => ({ t: s.occurredAt, family: s.family, title: s.title }))}
          />
        </Section>

        {/* Signals */}
        {moreSignals && (
          <Section label="Signals">
            {/* The cells name themselves to a screen reader, so the head row is for the eye. */}
            <div aria-hidden className={`${SIGNAL_COLS} border-b border-ink pb-2 max-sm:hidden`}>
              <span className="label">Date</span>
              <span className="label">Family</span>
              <span className="label">Signal</span>
              <span className="label" title="Gray: when it happened. Black: today, after fading with age.">
                Strength
              </span>
              <span />
            </div>
            {dated.length > 0 && (
              <ol>
                {dated.map((s, i) => (
                  <SignalRow key={s.id + i} s={s} />
                ))}
              </ol>
            )}
            {undated.length > 0 && (
              <>
                <h3 className="label mt-7 border-b border-rule pb-2">Undated</h3>
                <ol>
                  {undated.map((s, i) => (
                    <SignalRow key={s.id + i} s={s} />
                  ))}
                </ol>
              </>
            )}
          </Section>
        )}
      </div>
    </article>
  );
}
