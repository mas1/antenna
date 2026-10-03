import type { Metadata } from "next";
import Link from "next/link";
import { PipelineDiagram } from "@/components/method/PipelineDiagram";
import { Section } from "@/components/ui/Section";
import { meta } from "@/lib/data";
import { FAMILY_BLURB, FAMILY_LABEL, shortDate } from "@/lib/format";
import { SOURCES } from "@/lib/sources";
import { FAMILIES } from "@/lib/types";

export const metadata: Metadata = {
  title: "Method · Antenna",
  description: "How the pipeline collects, joins and scores public signals, and where it is weak.",
};

const TERMS = [
  {
    name: "Momentum",
    body: "Each signal has a strength from 0 to 100 that halves on a schedule set by its family. A second signal in the same family adds less than the first. A company with signals in only one family is capped and marked down, so a signal from a second family is what moves it up.",
  },
  {
    name: "Thesis fit",
    body: "Everything written about the company (its own site, award abstracts, filings, job posts) is matched against term lists for robotics, autonomy, defense, energy, manufacturing, semiconductors and space. It is a keyword match, and the dossier lists the terms that matched.",
  },
  {
    name: "Earliness",
    body: "How little the company is already known. GitHub stars, Hacker News mentions, traffic rank, open roles, money raised, age and a later reviewed stage each lower it, but only above a floor: $15M raised, 10 open roles, 500 stars, three years old. A company with nothing to measure is marked not measured and is not scored as unknown.",
  },
  {
    name: "Team",
    body: "Affiliations are quoted from the source and matched against a table of forty labs and companies that hard-tech founders tend to come from. Team lifts a score a little and never sinks it, because most early filings name no one but the signer.",
  },
];

const LIMITS = [
  {
    head: "Paperwork is filed by old companies too.",
    body: "A first trademark or a new FCC test authority looks the same from a company formed last month and from Robin Radar, sixteen years old, which filed for a sales demo and ranked in the thirties. Nothing in a filing says how old or how funded the filer is.",
  },
  {
    head: "First means first in one registry.",
    body: "A first federal award is first under one identifier, a first Form D is first under one SEC number, and a renewed waiver reads like a new one. None of them means the company is new.",
  },
  {
    head: "Rank history is reconstructed.",
    body: "These are the first runs. Past weeks are recomputed from event dates, which is accurate for filings and awards and approximate for anything measured only today, such as DNS records.",
  },
  {
    head: "Defense awards arrive late.",
    body: "Department of Defense procurement data is embargoed for ninety days, so an Other Transaction on the board was signed at least three months ago. Form D and FCC filings are the timely ones.",
  },
  {
    head: "GitHub says little about hardware.",
    body: "GitHub is rich for robotics, simulation and chip design and nearly silent for nuclear, grid and space. A popular repository is also often one person, not a company.",
  },
  {
    head: "Hiring pace cannot be measured yet.",
    body: "Job boards show only the roles open now, so filled roles vanish and a bulk repost looks like a hiring spree. Measuring the pace needs a second daily run.",
  },
  {
    head: "Legal names are not brand names.",
    body: "A filing says Aalo Holdings and the press says Aalo Atomics. A match on the legal name can miss, the same company can appear twice, and a Hacker News search under the legal name finds nothing. So an empty search is never scored as a sign the company is unknown.",
  },
  {
    head: "No paid data, no private data.",
    body: "Everything comes from free public sources. There is no LinkedIn, X or PitchBook data.",
  },
  {
    head: "The weights are a guess.",
    body: "Family weights were set by hand. They have not been tested against outcomes.",
  },
];

const NEXT = [
  {
    head: "Funding and founding data",
    body: "Well-funded companies can look new because the scorer does not know what they have raised. A licensed funding source would replace most of the review step.",
  },
  {
    head: "Partner decisions",
    body: "Record each meet or pass and use it to set the weights.",
  },
  {
    head: "Daily snapshots",
    body: "Store every daily run. Hiring pace, DNS changes and rank history can then be measured instead of reconstructed.",
  },
  {
    head: "Engineers leaving together",
    body: "Several engineers leaving the same team at SpaceX or Anduril in one quarter often means a new company. This needs paid employment data.",
  },
  {
    head: "Briefs for the whole board",
    body: `${meta.briefs ? `Briefs exist for ${meta.briefs} companies. Each` : "A brief"} takes an agent a few minutes, so they could be written nightly for every company that moved.`,
  },
];

const AUDIT_FOUND = [
  "An FAA waiver is held by whoever flies. Survey operators and dealers were being read as drone makers.",
  "One person can open an NRC docket with a letter, so a docket alone proves little.",
  "The people on a filing are whoever signed it: a regulatory lead, outside counsel, a sales engineer.",
  "Subsidiaries of public companies file under fresh legal names and look like startups.",
];

function LeadTable() {
  const leads = meta.leads ?? [];
  const ahead = leads.filter((l) => l.days > 0);
  const total = leads.length + (meta.unannounced?.length ?? 0);
  return (
    <>
      <p className="max-w-[62ch] font-serif text-[22px] leading-snug sm:text-[26px]">
        {ahead.length} of {total} briefed companies had a signal on file before they were first mentioned
        anywhere in public.
      </p>
      <p className="mt-5 max-w-[66ch] text-[14.5px] leading-relaxed text-ink-2">
        Any earlier mention counts as public: a launch post, a line in a local paper, a name on a grant
        list. Lead over the announcement of a round would be the more useful measure and cannot be
        tracked until there are daily runs to compare.
      </p>
      <div role="table" className="mt-8">
        <div role="row" className="grid grid-cols-[1fr_7rem] gap-x-4 border-b border-ink pb-2 lg:grid-cols-[13rem_1fr_1fr_7rem]">
          <span role="columnheader" className="label">Company</span>
          <span role="columnheader" className="label hidden lg:block">First signal</span>
          <span role="columnheader" className="label hidden lg:block">First public mention</span>
          <span role="columnheader" className="label text-right">Ahead by</span>
        </div>
        {leads.map((l) => (
          <div key={l.slug} role="row" className="grid grid-cols-[1fr_7rem] items-baseline gap-x-4 border-b border-rule py-3.5 lg:grid-cols-[13rem_1fr_1fr_7rem]">
            <span role="cell">
              <Link href={`/c/${l.slug}/`} className="font-serif text-[17px] underline decoration-transparent underline-offset-4 transition-colors hover:decoration-ink">
                {l.name}
              </Link>
            </span>
            <span role="cell" className="hidden text-[13.5px] leading-snug text-ink-2 lg:block">
              <span className="num mr-2 text-[11px] text-ink-4">{shortDate(l.firstSignalAt, meta.asOf)}</span>
              {l.firstSignal}
            </span>
            <span role="cell" className="hidden text-[13.5px] leading-snug text-ink-3 lg:block">
              <span className="num mr-2 text-[11px] text-ink-4">{shortDate(l.announcedAt, meta.asOf)}</span>
              {l.announcement}
            </span>
            <span role="cell" className={`num text-right text-[13px] ${l.days > 0 ? "text-ink" : "text-ink-4"}`}>
              {l.days > 0 ? `${l.days} days` : "already public"}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

export default function Page() {
  const t = meta.totals;
  const runs = Object.fromEntries(meta.collectors.map((c) => [c.source, c]));

  return (
    <div className="mx-auto max-w-[1320px] px-5 sm:px-8">
      <header className="pb-12 pt-14 sm:pt-20">
        <h1 className="display text-[44px] sm:text-[64px]">Method</h1>
        <p className="mt-6 max-w-[64ch] text-[15px] leading-relaxed text-ink-2 sm:text-base">
          A three-person company building a reactor or an interceptor has no headcount or web
          traffic to track. It still has to file with the SEC to raise, the FCC to test a radio,
          the FAA to fly and the NRC to discuss a reactor. Antenna reads those filings, matches
          them to code, papers and job posts, and ranks higher the companies that appear in more
          than one kind of source.
        </p>
      </header>

      <div className="flex flex-col gap-14">
        <Section label="Pipeline">
          <PipelineDiagram
            byFamily={meta.byFamily}
            signals={t.signals}
            entities={t.entities}
            onThesis={t.onThesis}
            board={t.board}
          />
        </Section>

        <Section label="The score">
          <p className="font-serif text-[26px] leading-tight tracking-[-0.02em] sm:text-[38px]">
            Edge <span className="text-ink-4">=</span> momentum <span className="text-ink-4">×</span> thesis fit{" "}
            <span className="text-ink-4">×</span> earliness <span className="text-ink-4">×</span> team
          </p>
          <div className="mt-10 grid grid-cols-1 gap-x-10 gap-y-8 sm:grid-cols-2">
            {TERMS.map((term) => (
              <div key={term.name} className="border-t border-rule pt-4">
                <h3 className="text-[15px] font-medium">{term.name}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{term.body}</p>
              </div>
            ))}
          </div>
        </Section>

        <Section label="Families">
          <div role="table" className="w-full">
            <div role="row" className="grid grid-cols-[1fr_5.5rem_5.5rem] gap-x-4 border-b border-ink pb-2 sm:grid-cols-[9rem_1fr_5.5rem_5.5rem]">
              <span role="columnheader" className="label">Family</span>
              <span role="columnheader" className="label hidden sm:block">What counts</span>
              <span role="columnheader" className="label text-right" title="How much this family counts toward momentum, relative to the others">Weight</span>
              <span role="columnheader" className="label text-right" title="Days for a signal's strength to fall by half">Half-life</span>
            </div>
            {FAMILIES.map((f) => (
              <div key={f} role="row" className="grid grid-cols-[1fr_5.5rem_5.5rem] items-center gap-x-4 border-b border-rule py-3 sm:grid-cols-[9rem_1fr_5.5rem_5.5rem]">
                <span role="cell" className="text-[14.5px] font-medium">{FAMILY_LABEL[f]}</span>
                <span role="cell" className="hidden text-[13.5px] text-ink-3 sm:block">{FAMILY_BLURB[f]}</span>
                <span role="cell" className="num text-right text-[12px]">{Math.round(meta.weights[f] * 100)}%</span>
                <span role="cell" className="num text-right text-[12px]">{Math.round(meta.halfLives[f])}d</span>
              </div>
            ))}
          </div>
        </Section>

        <Section label="Sources">
          <div role="table">
            <div role="row" className="grid grid-cols-[1fr_4.5rem] gap-x-4 border-b border-ink pb-2 lg:grid-cols-[6rem_13rem_1fr_1fr_4.5rem]">
              <span role="columnheader" className="label hidden lg:block">Family</span>
              <span role="columnheader" className="label">Source</span>
              <span role="columnheader" className="label hidden lg:block">Watches</span>
              <span role="columnheader" className="label hidden lg:block">Why it counts</span>
              <span role="columnheader" className="label text-right">Signals</span>
            </div>
            {SOURCES.map((s) => {
              const run = runs[s.slug];
              const n = meta.bySource[s.slug] ?? 0;
              return (
                <div key={s.slug} role="row" className="grid grid-cols-[1fr_4.5rem] items-baseline gap-x-4 border-b border-rule py-3.5 lg:grid-cols-[6rem_13rem_1fr_1fr_4.5rem]">
                  <span role="cell" className="label hidden lg:block">{FAMILY_LABEL[s.family]}</span>
                  <span role="cell">
                    <span className="font-serif text-[17px] leading-tight">{s.name}</span>
                    <span className="mt-1 block text-[13px] leading-snug text-ink-3 lg:hidden">{s.watches}</span>
                  </span>
                  <span role="cell" className="hidden text-[13.5px] leading-snug text-ink-2 lg:block">{s.watches}</span>
                  <span role="cell" className="hidden text-[13.5px] leading-snug text-ink-3 lg:block">{s.early}</span>
                  <span role="cell" className="num text-right text-[13px]">
                    {run?.error ? <span className="text-heat" title={run.error}>failed</span> : n ? n.toLocaleString("en-US") : <span className="text-ink-4">–</span>}
                  </span>
                </div>
              );
            })}
          </div>
        </Section>

        {meta.portfolioSeen.length > 0 && (
          <Section label="Sanity check">
            <p className="max-w-[62ch] text-[14.5px] leading-relaxed text-ink-2">
              Portfolio companies are kept off the board. The pipeline found these anyway.
            </p>
            <ul className="mt-5 flex flex-wrap gap-x-8 gap-y-2">
              {meta.portfolioSeen.map((p) => (
                <li key={p.name} className="font-serif text-[19px]">
                  {p.name}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {meta.reviewed && meta.reviewed.entries > 0 && (
          <Section label="Review">
            <p className="max-w-[62ch] font-serif text-[22px] leading-snug sm:text-[26px]">
              {meta.reviewed.entries} companies reviewed, {meta.reviewed.excluded} removed. Nothing is
              ranked until it has been reviewed.
            </p>
            <p className="mt-5 max-w-[66ch] text-[14.5px] leading-relaxed text-ink-2">
              Each company is checked by an agent told to assume the row is wrong. It opens the
              evidence, looks the company up and reports what it is: formation, early, growth,
              established or not a company. The stage on each row comes from that.
            </p>
            <ul className="mt-8 grid grid-cols-1 gap-x-10 sm:grid-cols-2">
              {AUDIT_FOUND.map((f) => (
                <li key={f} className="border-t border-rule py-3.5 text-[14px] leading-relaxed text-ink-2">
                  {f}
                </li>
              ))}
            </ul>
            {(meta.reviewed.awaiting ?? 0) > 0 && (
              <p className="mt-6 max-w-[66ch] text-[14.5px] leading-relaxed text-ink-2">
                {meta.reviewed.awaiting} companies that fit the thesis are waiting for review and are not
                on the board yet.
              </p>
            )}
          </Section>
        )}

        <Section label="Where it is weak">
          <div className="grid grid-cols-1 gap-x-10 gap-y-7 sm:grid-cols-2">
            {LIMITS.map((l) => (
              // An odd one out takes the whole last row.
              <div key={l.head} className="border-t border-rule pt-4 sm:last:odd:col-span-2">
                <h3 className="font-serif text-[19px] leading-snug">{l.head}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{l.body}</p>
              </div>
            ))}
          </div>
        </Section>

        {((meta.leads?.length ?? 0) > 0 || (meta.unannounced?.length ?? 0) > 0) && (
          <Section label="Lead time">
            <LeadTable />
          </Section>
        )}

        <Section label="What comes next">
          {/* Five items: three then two from lg, and two, two, one below it, with no empty cell. */}
          <ol className="grid grid-cols-1 gap-x-10 gap-y-7 sm:grid-cols-2 lg:grid-cols-6">
            {NEXT.map((n, i) => (
              <li
                key={n.head}
                className={`border-t border-rule pt-4 ${i < 3 ? "lg:col-span-2" : "lg:col-span-3"} ${
                  i === NEXT.length - 1 ? "sm:max-lg:col-span-2" : ""
                }`}
              >
                <span className="num text-[11px] text-ink-4">{String(i + 1).padStart(2, "0")}</span>
                <h3 className="mt-1 font-serif text-[19px] leading-snug">{n.head}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{n.body}</p>
              </li>
            ))}
          </ol>
        </Section>

        <p className="border-t border-rule pt-6 text-[14px]">
          <a
            href="https://github.com/mas1/antenna"
            target="_blank"
            rel="noreferrer"
            className="underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink"
          >
            Source code ↗
          </a>
        </p>
      </div>
    </div>
  );
}
