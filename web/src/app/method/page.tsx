import type { Metadata } from "next";
import Link from "next/link";
import { PipelineDiagram } from "@/components/method/PipelineDiagram";
import { Meter } from "@/components/ui/Meter";
import { Section } from "@/components/ui/Section";
import { meta } from "@/lib/data";
import { FAMILY_BLURB, FAMILY_CODE, FAMILY_LABEL, shortDate } from "@/lib/format";
import { SOURCES } from "@/lib/sources";
import { FAMILIES } from "@/lib/types";

export const metadata: Metadata = {
  title: "Method · Antenna",
  description: "How the pipeline collects, joins and scores public signals, and where it is weak.",
};

const TERMS = [
  {
    name: "Momentum",
    body: "Each signal has a strength from 0 to 1 that halves on a schedule set by its family. Signals in a family combine so that the second matters less than the first. One family alone is capped and marked down, so the only way to the top is corroboration: a filing plus a licence plus a hiring burst beats any one of them at full strength.",
  },
  {
    name: "Thesis fit",
    body: "Everything written about the company (its own site, award abstracts, filings, job posts) is matched against term lists for robotics, autonomy, defense, energy, manufacturing, semiconductors and space. It is a keyword model on purpose: you can read why a company matched and edit the list.",
  },
  {
    name: "Earliness",
    body: "One minus consensus. Stars, press, a traffic rank, a long list of open roles, a large round, age and a reviewed stage all say that other people already know. Each counts only above a floor, because a seed round and nine open roles are what early looks like. A company nobody has measured is marked unmeasured, not undiscovered.",
  },
  {
    name: "Team",
    body: "Affiliations are quoted from the source and matched against a table of forty labs and companies that hard-tech founders tend to come from. Team lifts a score a little and never sinks it, because most early filings name no one but the signer.",
  },
];

const LIMITS = [
  {
    head: "Paperwork is filed by old companies too.",
    body: "A first trademark or a new FCC test authority looks the same from a company formed last month and from a sixteen-year-old radar maker's sales office. Nothing in a filing says how old or how funded the filer is. The review step exists because of this, and it is the first thing a funding database would fix.",
  },
  {
    head: "First means first in one registry.",
    body: "A first federal award is first under one identifier, a first Form D is first under one SEC number, and a renewed waiver reads like a new one. The titles say what the record says. They do not say the company is new.",
  },
  {
    head: "The rank history is a backcast.",
    body: "These are the first runs. Past weeks are recomputed from the dates on each event, which is honest for filings and awards and approximate for anything measured only today, such as DNS records.",
  },
  {
    head: "Defense awards arrive late.",
    body: "Department of Defense procurement data is embargoed for ninety days, so an Other Transaction on the board was signed at least three months ago. Form D and FCC filings are the timely ones.",
  },
  {
    head: "Code is a thin signal for atoms.",
    body: "GitHub is rich for robotics, simulation and chip design and nearly silent for nuclear, grid and space. A popular repository is also often one person, not a company.",
  },
  {
    head: "Hiring velocity needs snapshots.",
    body: "Job boards show roles open now, so filled roles vanish and a bulk repost looks like a hiring spree. True velocity starts with the second daily run.",
  },
  {
    head: "Legal names are not brand names.",
    body: "A filing says Aalo Holdings where the world says Aalo Atomics. Joins on a legal name can miss, the same company can appear twice, and a search for press under the legal name finds nothing. A search that finds nothing is therefore never scored as obscurity.",
  },
  {
    head: "No paid data, no private data.",
    body: "Everything comes from public endpoints without a key. There is no LinkedIn, no X firehose and no PitchBook. Each would help, and each is a line item rather than a rewrite.",
  },
];

const NEXT = [
  {
    head: "Funding and founding data",
    body: "The audit's largest finding: well-funded companies looked like discoveries because nothing told the scorer what they had raised. One licensed funding source would replace most of the review step.",
  },
  {
    head: "Meet and pass as labels",
    body: "A partner's decision on each company is training data no vendor has. Feed it back through the review file and the weights stop being my guess and start being the firm's judgment.",
  },
  {
    head: "Daily snapshots",
    body: "Run every morning and store each run. Hiring velocity, follower growth and DNS changes become real time series, the backcast is replaced by history, and lead time is measured as it happens.",
  },
  {
    head: "Co-departure clusters",
    body: "Three engineers leaving the same team at SpaceX or Anduril in one quarter is a company forming. It needs employment data, the second thing worth paying for.",
  },
  {
    head: "Briefs for the whole board",
    body: "The research and fact-check pair that wrote the briefs here costs minutes per company. Run nightly on whatever moved, it becomes the first draft of every memo.",
  },
  {
    head: "One entity store",
    body: "The same company record should hold portfolio metrics, marks and comparables, so sourcing, diligence and reporting stop being separate spreadsheets.",
  },
];

const AUDIT_FOUND = [
  "A filing does not say how old the filer is. Robin Radar, sixteen years old, filed for a sales demo and ranked in the thirties.",
  "An FAA waiver is held by whoever flies. Survey operators and dealers were being read as drone makers.",
  "One person can open an NRC docket with a letter. A docket alone is a lead, not a finding.",
  "A repository with a thousand new stars is often one engineer. Several were listed as companies.",
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
        That is a hard bar on purpose. A launch post, a line in a local paper or a name on a grant list
        counts as public, so most companies were mentioned somewhere before their first filing in this
        window. The useful measure is signal against the announcement of a round, tracked run by run.
        That needs daily history, which starts now. Every briefed company is listed, ahead or not.
      </p>
      <div role="table" className="mt-8">
        <div role="row" className="grid grid-cols-[1fr_7rem] gap-x-4 border-b border-ink pb-2 lg:grid-cols-[13rem_1fr_1fr_7rem]">
          <span role="columnheader" className="label">Company</span>
          <span role="columnheader" className="label hidden lg:block">First signal</span>
          <span role="columnheader" className="label hidden lg:block">First public mention</span>
          <span role="columnheader" className="label text-right">Signal ahead by</span>
        </div>
        {leads.map((l) => (
          <div key={l.slug} role="row" className="grid grid-cols-[1fr_7rem] items-baseline gap-x-4 border-b border-rule py-3.5 lg:grid-cols-[13rem_1fr_1fr_7rem]">
            <span role="cell">
              <Link href={`/c/${l.slug}/`} className="font-serif text-[17px] underline decoration-transparent underline-offset-4 transition-colors hover:decoration-ink">
                {l.name}
              </Link>
            </span>
            <span role="cell" className="hidden text-[13.5px] leading-snug text-ink-2 lg:block">
              <span className="num mr-2 text-[11px] text-ink-4">{shortDate(l.firstSignalAt)}</span>
              {l.firstSignal}
            </span>
            <span role="cell" className="hidden text-[13.5px] leading-snug text-ink-3 lg:block">
              <span className="num mr-2 text-[11px] text-ink-4">{shortDate(l.announcedAt)}</span>
              {l.announcement}
            </span>
            <span role="cell" className={`num text-right text-[13px] ${l.days > 0 ? "text-signal" : "text-ink-4"}`}>
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
  const maxWeight = Math.max(...FAMILIES.map((f) => meta.weights[f]));
  const maxHalf = Math.max(...FAMILIES.map((f) => meta.halfLives[f]));

  return (
    <div className="mx-auto max-w-[1320px] px-5 sm:px-8">
      <header className="pb-12 pt-14 sm:pt-20">
        <p className="label">Method · Run of {shortDate(meta.asOf)}</p>
        <h1 className="display mt-5 max-w-[22ch] text-[44px] sm:text-[64px]">
          Hardware companies cannot hide from paperwork.
        </h1>
        <p className="mt-6 max-w-[64ch] text-[15px] leading-relaxed text-ink-2 sm:text-base">
          Commercial sourcing tools watch the exhaust of software companies: profile changes,
          headcount, web traffic. A company of three people building a reactor or an interceptor
          has none of that. It does have to file with the SEC to raise, with the FCC to test a
          radio, with the FAA to fly and with the NRC to talk about a reactor. Antenna reads
          those filings, joins them to code, papers and hiring, and ranks what converges.
        </p>
      </header>

      <div className="flex flex-col gap-16">
        <Section label="Pipeline" note="Counts are from this run.">
          <PipelineDiagram
            byFamily={meta.byFamily}
            signals={t.signals}
            entities={t.entities}
            onThesis={t.onThesis}
            board={t.board}
          />
        </Section>

        <Section label="The score" note="Every term is between 0 and 1, and every term can be read off the dossier.">
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

        <Section
          label="Families"
          note="Weight is how much a family can lift momentum alone. Half-life is how long a signal stays fresh."
        >
          <div role="table" className="w-full">
            <div role="row" className="grid grid-cols-[2rem_1fr_5.5rem_5.5rem] gap-x-4 border-b border-ink pb-2 sm:grid-cols-[2rem_9rem_1fr_9rem_9rem]">
              <span role="columnheader" className="label" />
              <span role="columnheader" className="label">Family</span>
              <span role="columnheader" className="label hidden sm:block">What counts</span>
              <span role="columnheader" className="label">Weight</span>
              <span role="columnheader" className="label">Half-life</span>
            </div>
            {FAMILIES.map((f, i) => (
              <div key={f} role="row" className="grid grid-cols-[2rem_1fr_5.5rem_5.5rem] items-center gap-x-4 border-b border-rule py-3 sm:grid-cols-[2rem_9rem_1fr_9rem_9rem]">
                <span role="cell" className="num text-[11px] text-ink-4">{FAMILY_CODE[f]}</span>
                <span role="cell" className="text-[14.5px] font-medium">{FAMILY_LABEL[f]}</span>
                <span role="cell" className="hidden text-[13.5px] text-ink-3 sm:block">{FAMILY_BLURB[f]}</span>
                <span role="cell" className="flex items-center gap-3">
                  <span className="num w-8 text-[12px]">{Math.round(meta.weights[f] * 100)}%</span>
                  <span className="hidden flex-1 sm:block"><Meter value={meta.weights[f] / maxWeight} delay={0.04 * i} label={`${FAMILY_LABEL[f]} weight`} /></span>
                </span>
                <span role="cell" className="flex items-center gap-3">
                  <span className="num w-8 text-[12px]">{Math.round(meta.halfLives[f])}d</span>
                  <span className="hidden flex-1 sm:block"><Meter value={meta.halfLives[f] / maxHalf} delay={0.04 * i} label={`${FAMILY_LABEL[f]} half-life`} /></span>
                </span>
              </div>
            ))}
          </div>
        </Section>

        <Section label="Sources" note={`${SOURCES.length} collectors, all keyless public endpoints. Signals and timings are from this run.`}>
          <div role="table">
            <div role="row" className="grid grid-cols-[2rem_1fr_4.5rem] gap-x-4 border-b border-ink pb-2 lg:grid-cols-[2rem_13rem_1fr_1fr_4.5rem_3.5rem]">
              <span role="columnheader" className="label" />
              <span role="columnheader" className="label">Source</span>
              <span role="columnheader" className="label hidden lg:block">Watches</span>
              <span role="columnheader" className="label hidden lg:block">Why it is early</span>
              <span role="columnheader" className="label text-right">Signals</span>
              <span role="columnheader" className="label hidden text-right lg:block">Time</span>
            </div>
            {SOURCES.map((s) => {
              const run = runs[s.slug];
              const n = meta.bySource[s.slug] ?? 0;
              return (
                <div key={s.slug} role="row" className="grid grid-cols-[2rem_1fr_4.5rem] items-baseline gap-x-4 border-b border-rule py-3.5 lg:grid-cols-[2rem_13rem_1fr_1fr_4.5rem_3.5rem]">
                  <span role="cell" className="num text-[11px] text-ink-4" title={FAMILY_LABEL[s.family]}>{FAMILY_CODE[s.family]}</span>
                  <span role="cell">
                    <span className="font-serif text-[17px] leading-tight">{s.name}</span>
                    <span className="mt-1 block text-[13px] leading-snug text-ink-3 lg:hidden">{s.watches}</span>
                  </span>
                  <span role="cell" className="hidden text-[13.5px] leading-snug text-ink-2 lg:block">{s.watches}</span>
                  <span role="cell" className="hidden text-[13.5px] leading-snug text-ink-3 lg:block">{s.early}</span>
                  <span role="cell" className="num text-right text-[13px]">
                    {run?.error ? <span className="text-heat" title={run.error}>failed</span> : n ? n.toLocaleString("en-US") : <span className="text-ink-4">–</span>}
                  </span>
                  <span role="cell" className="num hidden text-right text-[12px] text-ink-4 lg:block">
                    {run?.seconds != null ? `${Math.round(run.seconds)}s` : "–"}
                  </span>
                </div>
              );
            })}
          </div>
        </Section>

        {meta.portfolioSeen.length > 0 && (
          <Section
            label="Sanity check"
            note="Companies Anti Fund already holds that the collectors picked up without being told to look."
          >
            <p className="max-w-[62ch] text-[14.5px] leading-relaxed text-ink-2">
              Portfolio companies are kept off the board, since the point is the next one. Seeing
              them arrive through the same filings and feeds is a check that the net is in the
              right water.
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
          <Section
            label="Review"
            note="Every ranked company was checked by an agent told to assume the row was wrong. Corrections live in one file, pipeline/review.json."
          >
            <p className="max-w-[62ch] font-serif text-[22px] leading-snug sm:text-[26px]">
              {meta.reviewed.entries} companies reviewed, {meta.reviewed.excluded} removed, and the scoring
              changed because of what the review found. Nothing is ranked until it has been reviewed.
            </p>
            <p className="mt-5 max-w-[66ch] text-[14.5px] leading-relaxed text-ink-2">
              The first ranking looked right and was wrong in ways only reading each row would show. So
              each company went to an auditor that opened its evidence, looked the company up, and
              reported what it was: formation, early, growth, an incumbent, or not a company at all.
              The stage on each row comes from that. In a fund, this file is where a partner&apos;s
              meet or pass would land.
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
                {meta.reviewed.awaiting} on-thesis companies from this run are waiting for review and are
                not on the board yet.
              </p>
            )}
            <p className="mt-6 max-w-[66ch] text-[14.5px] leading-relaxed text-ink-2">
              What changed in the scoring: a company firing on one family alone is marked down, a
              reviewed stage counts as consensus, and a press search that finds nothing is no longer
              read as obscurity.
            </p>
          </Section>
        )}

        {((meta.leads?.length ?? 0) > 0 || (meta.unannounced?.length ?? 0) > 0) && (
          <Section
            label="Lead time"
            note="For companies with a research brief: when the first signal could be seen, against the first public mention of the company."
          >
            <LeadTable />
          </Section>
        )}

        <Section label="Where it is weak" note="Stated plainly, because a ranking you cannot question is not worth much.">
          <div className="grid grid-cols-1 gap-x-10 gap-y-7 sm:grid-cols-2">
            {LIMITS.map((l) => (
              <div key={l.head} className="border-t border-rule pt-4">
                <h3 className="font-serif text-[19px] leading-snug">{l.head}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{l.body}</p>
              </div>
            ))}
          </div>
        </Section>

        <Section label="What comes next" note="In the order I would build it, given what the review showed.">
          <ol className="grid grid-cols-1 gap-x-10 gap-y-7 sm:grid-cols-2 lg:grid-cols-3">
            {NEXT.map((n, i) => (
              <li key={n.head} className="border-t border-rule pt-4">
                <span className="num text-[11px] text-ink-4">{String(i + 1).padStart(2, "0")}</span>
                <h3 className="mt-1 font-serif text-[19px] leading-snug">{n.head}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{n.body}</p>
              </li>
            ))}
          </ol>
        </Section>

        <Section label="Run it" note="Python standard library only. No keys, no accounts.">
          <pre className="num overflow-x-auto border border-rule bg-paper-2 p-5 text-[13px] leading-relaxed text-ink-2">
{`cd pipeline
python3 -m antenna run            # collect, resolve, score, export
python3 -m antenna probe fcc_els  # run one collector, print, write nothing
python3 -m antenna report         # the top of the board in a terminal`}
          </pre>
          <p className="mt-5 text-[14px] text-ink-2">
            The export is four JSON files that this site reads at build time.{" "}
            <Link href="/" className="underline decoration-rule-2 underline-offset-4 transition-colors hover:decoration-ink">
              Back to the board
            </Link>
            .
          </p>
        </Section>
      </div>
    </div>
  );
}
