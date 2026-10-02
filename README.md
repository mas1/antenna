# Antenna

A sourcing edge for the physical world. Antenna reads public filings, licences,
awards, code, papers and job posts, joins them into one record per company,
and ranks the companies where independent signals are lining up before anyone
has written about them.

It was built in one night as a work sample for Anti Fund's Member of
Technical Staff role, against the first line of the job description:

> Build a sourcing edge: pipelines that surface technical founders and
> breakout companies before everyone else, using signals like GitHub
> activity, research papers, hiring, launches, traffic, and social data.

It is tuned to the thesis in Anti Fund's September 2026 manifesto: robotics,
defense, energy and manufacturing.

## The idea

Commercial sourcing tools watch the exhaust of software companies: profile
changes, headcount, web traffic. A three-person company building a reactor
or an interceptor has none of that. It does have to file with the SEC to
raise, with the FCC to test a radio, with the FAA to fly, and with the NRC
to talk about a reactor. Hardware companies cannot hide from paperwork.

So Antenna adds two signal families to the six in the job description:

| Family | What it reads |
| --- | --- |
| Capital | SEC Form D, DoD Other Transactions, SBIR and STTR, NSF, ARPA-E |
| Regulatory | FCC experimental licences, NRC pre-application dockets, FAA drone filings, USPTO trademarks |
| Code | GitHub star history, new organizations |
| Research | Papers where an author's affiliation is a company no registry knows |
| Hiring | Greenhouse, Lever and Ashby boards; the Hacker News hiring thread |
| Launch | Y Combinator, Show HN and Launch HN, a16z speedrun, HAX and SOSV |
| Attention | Hacker News mentions over time |
| Traffic | Tranco rank, DNS records, certificate logs, website verification |

Every source is a public endpoint that needs no key and no account.

## The run of 2 October 2026

| | |
| --- | --- |
| Signals read | 4,034 from 19 collectors |
| Companies resolved | 2,620 |
| On thesis | 643 |
| Reviewed | 414 |
| Removed on review | 106 |
| Ranked | 300, every one reviewed |
| Corroborated by two or more families | 91 |
| With a fact-checked brief | 24 |
| On thesis and waiting for review | 335 |

The top of the board that morning: Swarm Defense Technologies (a Defense
Department prototype award, an Air Force order, a Form D and three trademark
filings), BlueCore Energy (an NRC docket, two Form Ds and open roles),
AMPERA, Deployable Energy and Alva Energy (new or active NRC pre-application
dockets).

BlueCore's first Form D was filed on 6 April 2026. The company announced
itself on 21 July. That is 106 days in which the filing sat on EDGAR.

## What is in the repository

```
pipeline/   Python, standard library only
  antenna/
    collectors/   one file per source, 19 in all
    resolve.py    many hints, one company
    score.py      the edge score
    thesis.py     thesis fit, a keyword model you can read
    review.py     applies review.json
    export.py     writes the JSON the site reads
  review.json     what a reviewer corrected: stage, exclusions, names
  briefs/         fact-checked research briefs for the top companies
  tests/          about 1,400 offline tests against saved real responses
  fixtures/       those responses
web/        Next.js static site: board, dossier, wire, founders, method
docs/
  sources/        what each API really does today, measured
  COLLECTORS.md   the contract a collector follows
  DESIGN.md       the design language
```

## Using the site

The board is built to be opened every morning.

- The tiles are filters: new this week, corroborated, formation stage, with
  a brief, starred, and what changed since the run you last looked at.
- Hover any of the eight family boxes on a row to see the signals behind it.
- The signal under a company's name opens its source filing directly.
- Filters live in the URL, so a view can be bookmarked or shared, and the
  dossier's previous and next step through the view you came from.
- Stars and the last-visit marker are kept in the browser, nowhere else.
- Keys: `/` search, `j` `k` move, enter opens, `o` opens the source, `s`
  stars, left and right on a dossier, Cmd or Ctrl K to jump anywhere.
- Export CSV downloads the rows in the current view.

## Run it

```bash
cd pipeline
python3 -m antenna run
```

That collects, resolves, scores and writes four JSON files into
`web/src/data`. A cold run takes about twenty minutes, most of it polite
pacing against government sites; a warm run takes a few.

```bash
python3 -m antenna probe fcc_els
```

Runs one collector and prints what it emits without writing anything.

```bash
python3 -m antenna report
```

Prints the top of the board in the terminal.

```bash
python3 -m unittest discover -s tests -t .
```

Runs the offline tests. No network.

```bash
cd web && npm install && npm run dev
```

Serves the site. `npm run build` writes a static export to `web/out` that
can sit on any static host.

## The score

```
edge = momentum × thesis fit × earliness × team
```

- **Momentum.** Each signal has a strength that halves on a schedule set by
  its family. One family alone is capped and marked down, so the only way to
  the top is corroboration across independent families.
- **Thesis fit.** A keyword model on purpose. You can read why a company
  matched and edit the list in `pipeline/antenna/config.py`.
- **Earliness.** One minus consensus. Stars, press, traffic rank, headcount,
  round size, age and a reviewed stage all say other people already know.
  Each counts only above a floor. A company whose footprint has not been
  measured is marked as such, not as undiscovered.
- **Team.** Affiliations quoted from the source, matched against a table of
  labs and companies hard-tech founders tend to come from.

Because every term comes from dated events, the score can be recomputed as
of any past week. That is how the rank history exists on the first run.

## How it was built

In one session with Claude Code, as a sequence of agent workflows:

1. **Recon.** Nine agents hit every candidate API and wrote down what works
   today. Several things the documentation says are wrong: GitHub no longer
   lists stargazers with timestamps but has a new star-history endpoint; the
   SBIR.gov API is down but a bulk file is not; the FCC serves curl and
   refuses Python.
2. **Build and verify.** One agent wrote each collector. A second,
   independent agent then audited it against live evidence, opening the
   source pages and checking names, dates and numbers. Every collector had
   real errors caught and fixed at this step.
3. **Reconcile.** The collectors' authors reported bugs in the shared core,
   including one in the thesis classifier that silently ignored every
   multi-word term. The core was fixed and each collector re-aligned.
4. **Audit.** The first ranking looked right and was wrong in ways only
   reading each row would show. Every ranked company went to an auditor told
   to assume the row was wrong: open the evidence, look the company up, say
   what it is. About a quarter were removed (operators, dealers, one-person
   repositories, subsidiaries of public companies, sixteen-year-old firms
   filing a first trademark). The corrections are in `pipeline/review.json`
   and the scoring changed because of them.
5. **Briefs.** For the top formation and early-stage companies, a research
   agent drafted a brief from the evidence, a second agent checked it
   sentence by sentence against its sources, and an editor cut it to length
   without adding anything.

## Where it is weak

- A filing does not say how old or how funded the filer is. Without the
  review step, well-funded companies looked like discoveries. A funding
  database would replace most of that step.
- "First" means first in one registry under one identifier.
- The rank history is a backcast from event dates, not archived runs.
- DoD awards are embargoed for ninety days, so they arrive late.
- GitHub is a thin signal for nuclear, grid and space.
- Job boards show roles open now; true hiring velocity needs daily snapshots.
- Joins on a legal name alone can be wrong, and some companies appear twice.
- Website verification is strict: it finds about a third of filing-only
  companies and leaves the rest unmeasured.
- No paid data: no LinkedIn, no X firehose, no PitchBook.

The method page on the site says the same things in more detail.

## Not affiliated

This is an unsolicited work sample. It is not an Anti Fund product and
nothing here is investment advice. All data is public; each row links to
its source.
