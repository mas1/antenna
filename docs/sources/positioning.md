# Source card: positioning (desk research)

- slug: `positioning`
- researched: 2026-10-01 (evening, US Central), via live web search + page fetches from this machine
- nature: desk research, not API recon. There are no endpoints to call. The "endpoints" here are competitor platforms.
- fixtures:
  - `/Users/noel/antifund/pipeline/fixtures/positioning/pedigree_orgs.json` (40 orgs, the alumni lookup table)
  - `/Users/noel/antifund/pipeline/fixtures/positioning/signal_evidence.json` (15 signals with citations, evidence grade, suggested prior weight)

Provenance legend used throughout: **[F]** = I fetched the page and read it. **[S]** = number came from the search layer's summary of that URL, page not fetched or fetch blocked. Treat [S] numbers as secondary until someone opens the source.

---

## 0. The one-paragraph answer

Every horizontal sourcing platform (Harmonic, Specter, Dealroom, Tracxn, CB Insights, PitchBook) is built on the same software-shaped exhaust: LinkedIn-style profile changes, headcount, web traffic, funding events. They sell that same feed to every tier-1 firm at roughly $10k to $25k per seat. That is useful coverage but it is a consensus feed by construction, and it is weakest exactly where Anti Fund's next funds point: formation-stage robotics, defense, energy and manufacturing companies, which at birth have no web traffic, no app downloads, often no GitHub, and three employees. The physical world leaves a different paper trail (SBIR awards, contract actions, experimental spectrum licenses, patents, lab departures, co-departure clusters from a short list of employers), and the published evidence says those are among the few signals with causal, not just correlational, support. A thesis-specific engine that owns that paper trail, a time-decayed alumni graph, an explicit novelty term, and a feedback loop from the partners' own pass/meet decisions is defensibly better than buying Harmonic, and it can be built on free public data by one person.

---

## 1. What Anti Fund has said publicly (verified tonight)

| Fact | Source | Prov. |
|---|---|---|
| Tagline is the capital-vs-attention line; firm backs technical founders at formation and category leaders at growth | https://antifund.com/ | [F] |
| Focus list on the homepage: AI, robotics, defense, energy, semiconductors, manufacturing, frontier infrastructure, selective software/consumer | https://antifund.com/ | [F] |
| Manifesto: first funds backed the LLM stack; the next funds target automating the physical world with robotics, defense tech, energy and manufacturing | https://antifund.com/manifesto | [F] |
| Barbell is explicit: Venture = pre-seed and seed, technical founders at formation. Growth = growth and pre-IPO, concentrated positions in category leaders | https://antifund.com/manifesto | [F] |
| Team shown: Geoff Woo and Jake Paul (co-founders, managing partners), Logan Paul (GP), Steve Han (partner), Laura Brady (MD, capital formation) | https://antifund.com/ | [F] |
| Thesis-relevant portfolio shown: Anduril, Kela Systems, Saronic, Aeon, Enigma, General Galactic, Orbital, Helion, General Matter, Physical Intelligence, Etched, Efference | https://antifund.com/ | [F] |
| $100M Growth Fund closed June 2026, firm AUM above $180M | BusinessWire headline, 2026-06-18 (page itself returned 403) | [S] |
| Job post "Associate Member of Technical Staff", San Francisco, posted about 6 hours before fetch. Mission: tooling so the firm runs with a fraction of the usual headcount | https://www.linkedin.com/jobs/view/associate-member-of-technical-staff-at-anti-fund-4472594343/ | [F] |
| JD responsibilities, in order: (1) sourcing pipelines over GitHub, papers, hiring, launches, traffic, social; (2) internal tools and agents for deal pipeline, research, diligence, memos; (3) own company metrics, marks, fund data, comps in one source of truth; (4) deep dives on robotics/defense/energy/manufacturing; (5) automate fund ops, portfolio monitoring, reporting | same | [F] |
| JD asks applicants to send work samples (repo, demo, tool, video) to job@antifund.com | same | [F] |

Implications for what we build:
1. The headcount phrase lives in the **job post**, not the manifesto. Quote it from the JD.
2. The JD is five jobs, and sourcing is only the first. A sourcing tool whose entity store can also hold portfolio metrics and marks answers items 1 through 3 with one schema. Design the entity model so that is visibly true.
3. They asked for work samples. A working tool is the literal application format.

---

## 2. Platform teardown: what exists and where each one stops

Pricing is not published by any of these vendors except Dealroom and Tracxn; figures are third-party estimates and marked as such.

### 2a. Buyable platforms

| Platform | Home | Scale claimed | Signals | Pricing model | Where it stops |
|---|---|---|---|---|---|
| **Harmonic** | https://harmonic.ai | 30M+ companies, 190M+ people [F] (its pricing page reportedly says 35M [F via botmemo review]) | Stealth-founder detection from profile changes, departures from "high-signal employers", hiring, funding, launches, domain registrations, incorporation filings; network sync from inbox/calendar; Scout NL agent; REST + GraphQL + MCP [F] | Annual, sales-gated. Third-party estimates: about $25k/yr minimum, roughly $10k/seat with a 3-seat minimum; other reports of $20k to $24k/seat [S, conflicting] | Discovery only, not a CRM or portfolio system. Cannot score relationship strength. Its own blog concedes employment signals alone produce too many false positives. Customers named on the homepage include Accel, Khosla, Kleiner, GC, Bessemer, NEA, First Round: the feed is shared by the firms you are trying to beat. No thesis-specific physical-world sources are advertised on the pages I read. |
| **Specter** | https://www.tryspecter.com | 55M+ companies, 550M+ people, 300K investors, 1M+ transactions [F] | "Talent Signals" (1,000+/day: new founders, stealth, job changes), "Interest Signals" (7,000+/week: which investors are looking at which company), modeled revenue and valuation, 250+ data points per company; CRM sync; REST API (73 endpoints, 15 req/s [S]); MCP [F] | Demo-gated subscription; API by approval [S] | Same raw material as Harmonic (people-profile exhaust). Interest Signals are by definition a measure of consensus forming, which is a lagging input for a pre-consensus fund. Revenue/valuation are modeled, not observed. |
| **Synaptic** | https://synaptic.com | 100+ alt datasets (not verified tonight) | Web traffic, app downloads/reviews, employee and hiring data, search trends, funding, product reviews [S] | Tiered subscription, custom [S] | Alt-data built for consumer and SaaS traction at growth stage. A pre-seed actuator company has none of these exhaust streams. |
| **PitchBook** (VC Exit Predictor) | https://pitchbook.com | Trained on about 46k companies with known outcomes, tested on 11k+; claims about 75% accuracy [S] | Deal history, investors, company details from PitchBook's own data [S] | Enterprise subscription (commonly cited at $20k+/seat; not verified tonight) | A company must already have **two or more venture rounds** in the last six years to get a score [S]. It is structurally blind to formation stage. |
| **CB Insights** (Mosaic) | https://www.cbinsights.com | 0 to 1,000 score | Four M's: Momentum 50% (news, social, web and mobile traffic), Money 40% (financing history, investor quality), Market 5%, Management 5% [S] | Enterprise subscription | 90% of the weight is momentum plus money, both of which are near zero at formation. Management, the thing the academic evidence says matters most at seed, gets 5%. Predictive claims are self-reported. |
| **Tracxn** | https://tracxn.com | About 5M to 7M companies, 55k+ sector taxonomies, human analysts [S] | Analyst-curated sector maps, analyst rating, "Soonicorn/Minicorn" tiers [S] | About $24k/yr cited; free Lite tier [S] | Analyst curation is a latency source. Good taxonomy, slow on new entities. |
| **Dealroom** | https://dealroom.co | 3M+ profiles [S] | "Dealroom Signal": growth, hiring, fundraising timing, team composition; trade-register detection of new companies; strong government/ecosystem partnerships [S] | Published: about EUR 12k (3 seats), EUR 20k (6), EUR 40k (20) per year [S] | Europe-weighted. US defense and energy formation is not its home turf. |
| **Landscape** | https://www.landscape.vc | small (raised about $756k [S]) | NL market mapping, founder ranking algorithms, CRM integrations [S] | SaaS | Early product. `landscape.ventures` (the URL search returned) did not resolve from this machine; `landscape.vc` did. |

### 2b. Proprietary platforms inside funds (not buyable; these are the real comparables for an in-house build)

| Fund / system | Home | What it does | What they publicly say works | What they say is hard or does not work |
|---|---|---|---|---|
| **SignalFire: Beacon** | https://www.signalfire.com | 650M+ people, 80M+ orgs, "dozens of sources" incl. talent, valuation, open-source signals; Beacon Source (50+ attribute search) and Beacon Talent (recruiting for portfolio) [F] | Talent movement as the core lens; using the same platform for portfolio recruiting, so the data asset pays twice [F] | Outcomes are rare and take years, so labels are scarce; they rely on weak supervision and proxy metrics. Entity resolution is a first-class problem. Real data is inconsistent and contradictory [F] |
| **EQT: Motherbrain** | https://eqtgroup.com/about/motherbrain | Since 2016. Ranks on traction, "foundation quality", stage relevance; web-traffic forecasting; LLMs over internal conversations; relationship metadata (who at EQT knows this company) [F] | Interpretable stack ranks (users see why something ranks) and a good interface are what built trust. Relationship context from internal data. Credited as pivotal in 9 of 60+ investments, incl. Small Giant Games [S] | Deep models need labeled data that private markets do not have [S]. Compute is now the bottleneck for agentic assessment [F]. EQT publishes no hit-rate numbers [F] |
| **InReach Ventures: DIG** | https://www.inreachventures.com | Three layers: data aggregation and enrichment, ML ensemble, workflow product. Thousands of companies per month. Over EUR 3M spent building it [F] | Claimed 10x efficiency; majority of deals sourced through DIG [S] | Founder/problem fit and "do we want to work together" stay human [F] |
| **Moonfire** | https://www.moonfire.com | Embeddings to match companies/founders to thesis; up to 50 signals per founder; reportedly up to 50k companies/week screened [S]. Pipeline segmented into sourcing, screening, founder eval, each with its own model [F] | Text embeddings against the written thesis. A general LLM beat their custom venture-scale classifier by about 20% in an afternoon, versus 5% from a quarter of custom work. Investor decisions fed back as training data [F] | Foundation models are better only about 75% of the time; a well-tuned task-specific model still wins on its own task [F] |
| **Tribe Capital: Magic 8-Ball** | https://tribecap.co | Diligence, not sourcing. Growth accounting, cohort retention, revenue concentration from the company's raw data [S] | Standardized PMF measurement; giving founders the analysis even on a pass builds deal flow [S] | Needs the company's own transaction data, so it only starts after a founder engages. Not applicable pre-revenue, which is most hard tech at formation. |
| **Correlation Ventures** | https://correlationvc.com | Co-invest only. Model over a database of nearly all US VC financings; decision in under two weeks from a handful of documents [S] | Factors about the round and the syndicate predict returns [S] | Requires another VC already leading. It is a follower model by design; it cannot be pre-consensus. |
| **Hone Capital** | (homepage did not resolve; see McKinsey interview) https://www.mckinsey.com/industries/technology-media-and-telecommunications/our-insights/a-machine-learning-approach-to-venture-capital | 30,000+ deals, 400 characteristics reduced to 20 predictive for seed-to-Series-A [F via reprint] | Model picks: 40% follow-on within 15 months vs 16% baseline. Human plus model: about 3.5x baseline, better than either alone [S] | Label is "raised a Series A", i.e. it predicts consensus, not outcomes. Top features include syndicate lead and investor conversion rates, which do not exist before a round. |
| **Connetic Ventures: Wendal** | https://www.conneticventures.com | 20-minute founder application; scores 13 behavioral traits against data on about 20k founders [S] | Removes warm-intro bias; applicants who pass are 25x more likely to get funded [S] | Inbound only. Founders must apply. It does not find anyone. |

### 2c. The pattern across all of them

1. **Same inputs.** Profiles, headcount, traffic, funding. The sophisticated in-house systems add internal relationship data (EQT) and decision feedback (Moonfire, Hone).
2. **Labels are the real constraint**, not models. SignalFire and EQT both say so. Nobody has enough outlier outcomes to train on, so everyone falls back on "raised the next round" as the label, which trains the model to predict what other VCs will like.
3. **What works, by their own account:** interpretable rankings, a UI partners actually open, human plus machine beating either alone, feeding partner decisions back in, using the thesis text itself as the query.
4. **What none of them advertise:** physical-world regulatory and procurement exhaust; team-level (not company-level) alumni resolution; an explicit novelty term; anything about where a specific fund's own advantage (for Anti Fund, attention) changes the outcome.
5. The 2025 Data-Driven VC Landscape (Retterath / Affinity) counts 235 data-driven firms out of 300+ surveyed, up 21% in a year; 94% expect "augmented" (human plus AI) VC to dominate and only 5% expect pure quant; entity matching and build-vs-buy are named as the main technical obstacles [F: https://www.affinity.co/blog/data-driven-vc-landscape]. 65% run most desk work on internal tools and over a third source more than 40% of deals through data tools [S].

---

## 3. Which signals actually predict breakout (published evidence)

Grades: **A** = peer-reviewed with causal or large-sample design. **B** = credible large-sample descriptive or working paper. **C** = practitioner analysis. **D** = vendor assertion only.

| # | Signal | Finding | Grade | Source |
|---|---|---|---|---|
| 1 | Repeat founder with prior success | 30% success vs 18% for first-timers, 20% for previously failed (VC-backed, 1986 to 2003) | A | Gompers, Kovner, Lerner, Scharfstein, JFE 2010. https://www.newyorkfed.org/medialibrary/media/research/economists/kovner/performance_persistence.pdf [S] |
| 2 | Deep same-industry experience | 3+ years in the same 2-digit industry doubles the rate of top-0.1% growth outcomes; closer match is better; mean age of top-growth founders is 45.0 (2.7M founders) | A | Azoulay, Jones, Kim, Miranda, AER: Insights 2020. https://www.nber.org/system/files/working_papers/w24489/w24489.pdf [S] |
| 3 | Co-founders who worked together | 57.8% of multi-founder US unicorns (796 companies) have two or more founders from the same prior employer; 33.3% share a university | B | Strebulaev. https://ilyastrebulaev.substack.com/p/the-making-of-a-unicorn-founder-is [F] |
| 4 | Alumni of a high-spawn employer | Real, but period-specific. PayPal odds ratio 12.0 pre-2016, 1.7 after. Google share of unicorn founders 4.4% to 9.1%. MIT odds ratio 1.9 to 6.3. OpenAI from zero to 1.8% | B | same [F]. Earlier Strebulaev cut: Sun 3.0x, Google 2.5x, Facebook 2.2x; IBM, HP, Intel, Microsoft, Accenture no lift [S] |
| 5 | Parent was itself VC-backed | Most prolific spawners are formerly VC-backed companies in hubs; spawning rises when the parent's growth slows | A | Gompers, Lerner, Scharfstein, J. Finance 2005. https://www.nber.org/papers/w9816 [S] |
| 6 | Team info beats traction at seed | Randomized experiment, about 17,000 emails to 4,500 investors: strong response to team, none to traction or lead investor | A | Bernstein, Korteweg, Laws, J. Finance 2017. https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12470 [S] |
| 7 | **SBIR Phase I award** | About doubles the probability of later VC; large effects on patenting and commercialization; regression discontinuity, 7,436 firms; strongest for young, constrained firms | A | Howell, AER 2017. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2687457 [S] |
| 8 | **First patent grant** | Lenient-examiner "lottery": +55% employment growth, +80% sales growth over 5 years, +47% likelihood of VC, more than 2x IPO odds | A | Farre-Mensa, Hegde, Ljungqvist, J. Finance 2020. https://www.nber.org/papers/w23268 [S] |
| 9 | GitHub community engagement | Startups engaging with open source communities are substantially more likely to raise; larger for novel tech, weaker when GitHub is internal-only | A | Conti, Peukert, Roche, Organization Science 2025. https://ideas.repec.org/a/inm/ororsc/v36y2025i4p1551-1573.html [F] |
| 10 | GitHub star velocity | Redpoint: median 2,850 stars at seed, 4,980 at Series A across 80 dev-tool companies. Runa's ROSS Index ranks by relative star growth from 1,000 stars. But about 4.5M suspected fake stars across about 22.9k repos (3.1M / 15.8k after filtering) | C, with a strong caveat | https://github.com/RunaCapital/ROSS-Index ; He et al. https://arxiv.org/pdf/2412.13459 [S] |
| 11 | Open-web mention velocity | Adding web mentions to structured data gives a substantial lift predicting seed-to-A within a year | B | Sharchilev et al., CIKM 2018. https://dl.acm.org/doi/10.1145/3269206.3272011 [S] |
| 12 | Research pedigree alone | Academic startups raise and patent as much as others but exit less often; "superstar" academics do better | A | Roche, Conti, Rothaermel, Research Policy 2020. https://www.sciencedirect.com/science/article/abs/pii/S0048733320301402 [S] |
| 13 | Headcount growth | Every vendor uses it. I found **no rigorous public study** of its predictive power tonight | D | vendor pages only |
| 14 | ML screening vs humans | XGBoost on 77,279 European companies beat the median VC by 25% and the average by 29% (111 investors, same one-pagers) | B | Retterath 2020. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3706119 [S] |
| 15 | **The trap** | VCs that adopt data tech double investments in the top quartile of similarity-to-past-startups and become less likely to back rare breakthrough winners; plausibly causal | A | Bonelli, Review of Financial Studies 2026. https://academic.oup.com/rfs/advance-article-abstract/doi/10.1093/rfs/hhaf078/8285007 [S] |

Also relevant: Gompers, Gornall, Kaplan, Strebulaev surveyed 885 VCs. Deal flow: 30%+ professional network, 20% other investors, 8% portfolio referrals, almost 30% proactively self-generated, about 10% inbound. VCs rank selection above sourcing and value-add, and team above business [S: https://www.nber.org/papers/w22587].

### What this means for the scorer

1. **Weight the founder, at team resolution.** Signals 1, 2, 3, 6 are the best-supported and all are about people. For hard tech, signal 2 says the question is not "ex-SpaceX" but "three or more years on the exact subsystem they are now building".
2. **Co-departure clusters are the formation event.** Signal 3: two or more people leaving the same team inside the same 90 days, then going quiet, is the highest-prior pattern. Score the cluster, not the individual.
3. **Government paper is the hard-tech equivalent of web traffic, and it has causal evidence behind it.** Signals 7 and 8. SBIR/STTR Phase I, first patent grant. These are public, free, timestamped, and carry a legal entity name. Horizontal platforms do not lead with them.
4. **Decay the pedigree table.** Signal 4: alumni effects fade within a decade. Each org needs a spawn-rate that is re-estimated, not a static tier.
5. **GitHub is a secondary signal for this thesis.** Useful for robot-learning software and autonomy stacks; near-useless for reactors and machine shops. De-bot stars before using them.
6. **Headcount growth and funding events are confirmation, not discovery.** They arrive after the money.
7. **Add a novelty term on purpose.** Signal 15 is the single most important design constraint: a ranker trained to find things that look like past winners will systematically miss what a formation-stage fund exists to find. Score distance-from-known-clusters as a positive, and show the partner both lists.
8. **Research pedigree needs an operator.** Signal 12: lab founders alone under-exit. Boost when a lab founder pairs with someone from the tier-1 operator list.

---

## 4. Why a thesis-specific in-house engine beats buying Harmonic: 5 differentiators

1. **It reads the paper trail the physical world actually leaves.** SBIR/STTR awards, federal contract actions, experimental spectrum licenses, aircraft registrations and waivers, reactor pre-application dockets, ARPA-E and DOE awards, patent assignments, state incorporations. Two of these (SBIR Phase I, first patent) have causal evidence for predicting later venture funding, which is stronger support than anything behind headcount growth. A three-person hypersonics or microreactor company is invisible on traffic, app and headcount signals and plainly visible here. None of the vendor pages reviewed advertise this coverage.

2. **It resolves pedigree to the team and the subsystem, with decay.** Horizontal tools flag "left a high-signal employer". This engine carries a 40-org lookup built for robotics, defense, energy and manufacturing, tags the team within the org (propulsion vs. finance), requires tenure depth (the 3-year same-industry result), detects co-departure clusters (the 57.8% result), and re-estimates each org's spawn rate so the table ages correctly (the PayPal 12.0 to 1.7 result).

3. **It is built to avoid the documented failure mode of data-driven VCs.** Bonelli's result is that data-driven firms drift toward look-alikes and miss breakthroughs. This engine ranks on two axes, evidence strength and novelty versus known clusters, and shows every reason for a rank (EQT's stated lesson: interpretability is what earned partner trust). A black-box vendor score cannot be tuned this way.

4. **It learns from Anti Fund's own judgment and sees Anti Fund's own edge.** Every pass/meet click is a label no vendor will ever have; Moonfire and Hone both report that human-plus-model beats either alone. And only an in-house system can score "attention leverage": where Anti Fund's reach in recruiting, customer trust and financing changes a company's trajectory, which is the firm's stated reason to exist and appears in no vendor schema.

5. **It is not the consensus feed, and it costs one person.** Harmonic's homepage lists Accel, Khosla, Kleiner, General Catalyst, Bessemer, NEA and First Round as customers. A signal that arrives in all of those inboxes the same morning is table stakes, not edge. The in-house engine runs on free public sources, sets its own latency, and shares one entity store with portfolio metrics, marks and comps, which is items 1, 2, 3 and 5 of the job description answered by one system instead of five tools.

Honest counterpoint to state up front if asked: Harmonic's people-graph coverage (190M profiles, refreshed within the week) is not reproducible from free sources. The in-house engine does not replace that; it makes it optional, and if the firm later buys a seat, the engine consumes it as one more input through the vendor's API or MCP.

---

## 5. Five features a partner opens every morning

1. **Formation Radar.** Overnight departures and co-departure clusters from the 40 pedigree orgs, tagged by team and tenure, ranked by cluster strength. Each card: who, from where, how long, what they worked on, what they have filed since (entity, domain, patent, SBIR), and the shortest warm path through the portfolio.
2. **Government Money Tape.** Yesterday's SBIR/STTR Phase I and II awards, small-dollar DoD and DOE contract actions, ARPA-E selections and experimental licenses, filtered to companies under about 50 people with no announced institutional round. This is the feed no other VC dashboard has on its front page.
3. **Breakout Board.** Thesis companies ranked by acceleration (second derivative), not level, across every signal family: hiring posts, Phase I to Phase II conversion, contract ceiling growth, de-botted star velocity, launch and social spikes. One sparkline per signal, one sentence on why it moved.
4. **Entity page with one-click first-pass memo.** Everything known about a company or founder on one page with sources, a drafted memo (what it is, why now, team, evidence, comps, open diligence questions), and two buttons: Meet and Pass. The click trains the ranker.
5. **Lead-Time Scoreboard.** For every thesis-relevant round announced this week: did the engine flag it beforehand, and by how many days? Plus the misses and why. This is the honesty metric for the whole system and the number a partner quotes to LPs.

---

## 6. The alumni-mafia lookup for hard tech

File: `/Users/noel/antifund/pipeline/fixtures/positioning/pedigree_orgs.json` (40 entries; keys `org`, `aliases`, `sector`, `tier`).

Tiering rule: tier 1 = formerly-VC-backed operators or labs with a documented, current spawn record in the thesis sectors (per the spawning literature); tier 2 = strong but narrower, larger-denominator, or less documented. **Evidence** column: E = I saw alumni-founder evidence in a source tonight; K = included on domain knowledge, not re-verified tonight.

| Org | Sector | Tier | Ev. | Evidence seen tonight |
|---|---|---|---|---|
| SpaceX | space | 1 | E | About 140 to 147 alumni-founded companies and $9B to $11B raised depending on tracker [S]; Comma Capital counts about 1,330 alumni founders and $9.2B [F]; 1872's three founders [F]; Stealth Startup Spy #321 lists an ex-SpaceX manufacturing engineer in stealth [F]. Strebulaev names SpaceX as a new-generation unicorn-founder source [F] |
| Tesla | energy | 1 | E | Nine alumni unicorns, about $10B into alumni companies (Redwood, Sila, others) [S]; UMA's CEO from Tesla Autopilot/Optimus [F] |
| Anduril Industries | defense | 1 | E | Saronic co-founder ex-Anduril [S]; Askari Defense team [F]; Nominal [S] |
| Palantir Technologies | defense | 1 | E | 39+ alumni startups, $6B+ raised; Anduril's own founders [S] |
| Boston Dynamics | robotics | 1 | E | Generalist AI team [S] |
| Google DeepMind (incl. Brain robotics, Everyday Robots, X) | robotics | 1 | E | Physical Intelligence, Generalist AI [S]; Reimagine Robotics' four founders [F] |
| OpenAI | robotics | 1 | E | 1.8% of post-2016 unicorn founders from zero [F] |
| Physical Intelligence | robotics | 1 | K | Anti Fund portfolio company; young, so alumni flow is prospective |
| Waymo / Google self-driving | autonomy | 1 | E | Aurora (Urmson), Argo (Salesky) [S] |
| Skydio | drones | 1 | E | Askari Defense team [F] |
| Shield AI | defense | 1 | K | |
| NVIDIA | semiconductors | 1 | K | |
| Carnegie Mellon Robotics Institute / NREC | robotics | 1 | E | Skild AI, Aurora, Argo AI, Carnegie Robotics [S] |
| MIT CSAIL (and MIT broadly) | robotics | 1 | E | MIT unicorn-founder odds ratio 1.9 to 6.3 [F]; Boston Dynamics origin [S] |
| Stanford AI Lab | robotics | 1 | E | Physical Intelligence founding team; Stanford share 2.9% to 5.3% [F] |
| Berkeley AI Research | robotics | 1 | E | Physical Intelligence founding team [S]; PathOn Robotics founder via Berkeley DeepDrive [F] |
| ETH Zurich Robotic Systems Lab | robotics | 1 | E | Eight spinouts incl. ANYbotics; RIVR (ex Swiss-Mile) acquired by Amazon [S] |
| Commonwealth Fusion Systems | energy | 1 | K | $1B raise July 2026 [S]; alumni flow not evidenced tonight |
| DARPA | defense | 1 | K | |
| NASA Jet Propulsion Laboratory | space | 1 | E | Mantis Space chief engineer [F]; Noble Machines team (NASA, Caltech) [F] |
| Apple (SPG, hardware) | manufacturing | 2 | E | Noble Machines team; Mantis Space optical lead [F] |
| Figure AI | robotics | 2 | K | |
| Blue Origin | space | 2 | K | |
| Rocket Lab | space | 2 | K | |
| Relativity Space | space | 2 | K | itself a SpaceX/Blue Origin alumni company [S] |
| Amazon Robotics (Kiva Systems) | robotics | 2 | E | 6 River Systems founders; Berkshire Grey team [S] |
| Zipline | drones | 2 | K | |
| Cruise | autonomy | 2 | K | |
| Aurora Innovation (incl. Uber ATG) | autonomy | 2 | K | |
| Toyota Research Institute | robotics | 2 | K | |
| Hugging Face LeRobot | robotics | 2 | E | UMA's CEO and a co-founder [F] |
| Hadrian | manufacturing | 2 | K | |
| Lockheed Martin Skunk Works | defense | 2 | K | |
| US Navy Nuclear Propulsion Program | energy | 2 | E | Bluecore Energy team (ex Navy nuclear submarine officers) [F] |
| Sandia National Laboratories | energy | 2 | E | Mantis Space chief engineer, 24 years [F] |
| Lawrence Livermore National Laboratory | energy | 2 | K | |
| Idaho National Laboratory (incl. NRIC) | energy | 2 | K | NRIC Launch Pad selects startups [S] |
| Helion Energy | energy | 2 | K | Anti Fund portfolio company |
| Georgia Tech Research Institute / Georgia Tech | defense | 2 | E | Mantis Space COO led R&D at GTRI; Askari founders met at Georgia Tech [F] |
| TSMC | semiconductors | 2 | K | Substrate recruits from TSMC and others [S] |

How to use the table in the scorer:
- Match on `org` and `aliases`, case-insensitive, whole-token. University-level aliases ("MIT", "Stanford University", "Georgia Tech", "UC Berkeley", "ETH Zurich", "Carnegie Mellon University") are deliberately included because profiles and paper affiliations are written at that level, but they are **broad**: when the match is on a university-level alias and not the lab name, down-weight it (suggest 0.4x) unless a lab or advisor string also matches.
- "Hugging Face", "Apple", "NVIDIA", "Tesla" are large employers: require a team/role keyword (robotics, autopilot, Optimus, hardware, SPG, Isaac, LeRobot) before awarding full pedigree credit.
- Pedigree score = tier weight x tenure factor (cap at 3+ years) x recency decay (half-life about 24 months since departure) x cluster multiplier (2 or more co-departers from the same org inside 90 days).
- Legacy primes (Raytheon, Northrop, Boeing, GD) were seen in founder bios tonight (Askari, Bluecore) but are intentionally **excluded**: denominators in the hundreds of thousands and the spawning literature says non-VC-backed parents spawn less. Add them as a tier 3 only with a role filter.
- Refresh quarterly. Recompute each org's trailing 24-month spawn count from the engine's own data and let tiers move.

---

## 7. Formation-stage hard-tech companies seen in sources tonight

Only companies I saw in a fetched or searched source. Use as seed test cases for entity resolution and as a sanity check: a working engine should have flagged these before the cited article date.

| Company | What | Founders / pedigree | Round | Evidence | Prov. |
|---|---|---|---|---|---|
| **1872** | Robotic steel-skid fabrication for data centers and SMRs, Cincinnati | Dan Summers (led Raptor integration at SpaceX), Brian Mongilio, Michael Grant, all ex-SpaceX | $15M seed led by The O.H.I.O. Fund; factory opened 2026-07-22 | https://www.cryptopolitan.com/spacex-engineers-cincinnati-steel-factory/ | [F] |
| **Askari Defense** | Hand-launched autonomous counter-UAS interceptors, Atlanta | Robbie Van Zyl, Ben Airdo (both 25, Georgia Tech), Marc Van Zyl; team from Anduril, Skydio, Raytheon, Hermeus | $9M seed June 2026 led by Builders VC; angels include CEOs of Umbra, Firestorm and Aeon | https://www.tectonicdefense.com/hand-launched-interceptor-startup-askari-raises-9m-seed/ | [F] |
| **Reimagine Robotics** | Robots that learn tasks from on-the-job human demonstration; London and Sydney | Jonathan Scholz (founded DeepMind's applied robotics team), Oleg Sushkov, Akhil Raju, Misha Denil, all ex-DeepMind | Pre-seed, Fly Ventures and firstminute; founded April 2025, out of stealth Aug 2026 | https://www.therobotreport.com/reimagine-robotics-emerges-stealth-with-robotslearn-on-the-job/ | [F] |
| **UMA** | General-purpose humanoid ("Northstar"), Paris | Rémi Cadène (Tesla Autopilot/Optimus, Hugging Face), Simon Alibert (Hugging Face), Rob Knight | Backed by Greycroft, Relentless, Unity Growth; angels incl. Yann LeCun | https://electrek.co/2026/07/07/tesla-optimus-scientist-uma-humanoid-robot/ | [F] |
| **Mantis Space** | Orbital power-beaming constellation, Albuquerque | Eric Truitt (Terran Orbital co-founder, Navy), RADM (ret.) H. Wyman Howard III, Jeremy Scheerer (GTRI), John Sandusky (Sandia 24 yrs, JPL) | $10M seed, Rule 1 Ventures and Montauk Capital, March 2026 | https://www.satellitetoday.com/technology/2026/03/12/orbital-energy-firm-mantis-space-leaves-stealth-with-10m-seed-round/ | [F] |
| **Bluecore Energy** | Floating ~10 MWe light-water nuclear plants, Port of Long Beach | Kofi Asante (CEO); team incl. ex Navy nuclear submarine officers, SpaceX, Northrop | $50M seed (incl. $10M pre-seed) led by Silverton Partners, Sept 2026 | https://www.theregister.com/systems/2026/09/08/floating-nuclear-startup-bluecore-lands-50m-funding-before-setting-sail/5295032 | [F] |
| **Noble Machines** | Industrial general-purpose humanoids; first Fortune Global 500 deployment within 18 months | Wei Ding (CEO); team from Apple, SpaceX, NASA, Caltech; founded 2024 | undisclosed | https://interestingengineering.com/ai-robotics/noble-machines-industrial-robot-record-deployment | [F] |
| **Enigma** | Robot foundation models and control interfaces, Israel | Jonathan Jacobi (ex Microsoft, Check Point), Gal Niv (ex Unit 8200) | $71M seed led by Index and Ribbit, July 2026 | https://www.calcalistech.com/ctechnews/article/h1tdxjhrgx | [F] |
| **Nuclear Turbines** | Deep-tech spinout of BAE Systems, Manchester | Jeremy Owston (ex BAE nuclear submarine engineer), Tim Abram (Univ. of Manchester nuclear professor) | GBP 15M seed, 2026 | https://fundup.ai/recently-funded-startups/company/b2b49c344c82ecb84939867c996526a11c506b06a4019fb820fe5dc5f78e9a14/nuclear-turbines | [S] (page 403 on fetch) |
| **PathOn Robotics** | Autonomous robot navigation | "Rex Z.", ex Amazon applied science manager, Berkeley DeepDrive researcher; 5 months in stealth before launch | n/a | https://stealthstartupspy.substack.com/p/stealth-startup-spy-321 | [F] |
| **Starcloud** | Orbital data centers | Adi Oltean (ex SpaceX Starlink) per Comma Capital | reported $1.1B valuation in 17 months | https://commacapital.substack.com/p/the-spacex-constellation | [F], single source |
| **TerraFirma** | Construction automation | Noah Schochet, Noah McGuinness (ex SpaceX Starlink) per Comma Capital | reported $115M total | https://commacapital.substack.com/p/the-spacex-constellation | [F], single source |

Two things worth a partner's attention:
- **Enigma** appears in Anti Fund's own homepage portfolio list under defense; the Calcalist article about the Israeli robot-model company named Enigma does **not** list Anti Fund among investors. These may be two different companies with the same name. Do not assert they are the same without checking. Good entity-resolution test case.
- **Askari's** seed lists the CEO of "Aeon" as an angel, and Anti Fund's homepage lists a portfolio company called Aeon. If it is the same Aeon, that is a live example of a signal the engine should carry: *a portfolio founder wrote an angel check into a formation-stage company in thesis.* Also unverified; same name-collision caution applies.

Also seen: **Stealth Startup Spy** (weekly Substack from a company called Gravity, issue #321 dated 2026-03-12) publishes about 1% of the stealth-founder moves it tracks, with prior employers and months-in-stealth. It is a free, public, human-readable version of Harmonic's headline feature and a usable benchmark for the Formation Radar.

---

## 8. Entity resolution notes (for whoever wires this to the other source cards)

- **Person**: full name + most recent employer from `pedigree_orgs.json` + departure month. Articles frequently abbreviate ("Matt H.", "Rex Z."); keep a `name_partial` flag and resolve later through co-founder links.
- **Company**: prefer registered domain as the primary key. For formation-stage hard tech the legal name in SBIR/contract/patent records often differs from the brand ("1872" is a brand; the filing entity will be something else). Store `legal_names[]` and `brand_names[]` separately.
- **Name collisions are common** at this stage (Enigma, Aeon, Orbital, Natural, Liquid, Figure, Aurora, Relativity are all ordinary words). Never merge on name alone; require domain, founder overlap, or HQ city + sector.
- **Pedigree org matching**: whole-token, case-insensitive, alias-aware; see section 6 for the university and big-employer down-weights.

---

## 9. Gotchas hit during this research

1. **Blocked or failed fetches**: BusinessWire (403), SSRN abstract pages (403), Fast Company (403), fundup.ai (403), McKinsey (60s timeout), AEA conference paper link (returned a raw PDF the fetcher could not parse). Numbers from those sources are from the search layer and marked [S].
2. **The LinkedIn job page fetched without login.** That may not last; the JD content is captured in section 1.
3. **The manifesto does not contain the headcount line.** It is in the job post. Easy to misattribute.
4. **Vendor pricing is not public** for Harmonic, Specter, Synaptic, PitchBook, CB Insights. Third-party numbers conflict (Harmonic: about $10k/seat with 3-seat minimum vs. $20k to $24k/seat). Never state a vendor price as fact in anything sent to Anti Fund; say "reported".
5. **Vendor scale claims are self-reported and inconsistent** (Harmonic 30M vs 35M companies on its own pages). CB Insights' and PitchBook's accuracy numbers are their own.
6. **SpaceX alumni counts disagree** across trackers because the units differ: about 140 to 147 companies in several sources vs. about 1,330 "alumni founders" in Comma Capital; $9.2B to $11.2B raised. Cite a range.
7. **One fetched summary was internally inconsistent** about Hadrian's founder and SpaceX link (it listed two different names). I did not use it. Hadrian is in the pedigree table as a tier-2 employer on domain knowledge only.
8. **Search-layer summaries are model-written.** Anything marked [S] that will be quoted to Anti Fund should be opened and checked first. The academic effect sizes in section 3 match my prior knowledge of those papers, but only rows 3, 4 and 9 were read from a fetched page tonight.
9. **Hone Capital's homepage did not resolve** (honecap.com, both with and without www). `landscape.ventures` did not resolve; `landscape.vc` did.
10. **No rigorous public evidence for headcount growth** as a predictor was found. This is an absence-of-evidence finding from one evening's search, not proof none exists.
11. **Substrate** (X-ray lithography) surfaced in search described as emerging from stealth "in 2026"; it was founded in 2022 and has raised about $100M, so it is not formation-stage and the date is doubtful. Left out of section 7.
12. **Pedigree tiers are judgment**, anchored where possible to evidence seen tonight (E rows, 21 of 40). 19 of 40 rows are K (domain knowledge, not re-verified). They are plausible and should be validated against the engine's own departure data within the first month.

---

## 10. Sources

Anti Fund
- https://antifund.com/
- https://antifund.com/manifesto
- https://www.linkedin.com/jobs/view/associate-member-of-technical-staff-at-anti-fund-4472594343/
- https://www.businesswire.com/news/home/20260618551048/en/Anti-Fund-Closes-Oversubscribed-$100-Million-Growth-Fund-Firm-AUM-Tops-$180-Million

Platforms
- https://harmonic.ai/ ; https://harmonic.ai/blog/how-to-find-track-stealth-startup-founders ; https://botmemo.com/harmonic-review ; https://pipelineroad.com/compare/pipelineroad-vs-harmonic
- https://www.tryspecter.com/ ; https://www.tryspecter.com/api
- https://synaptic.com/about-us
- https://pitchbook.com/help/understanding-vc-exit-predictor ; https://techcrunch.com/2023/03/20/pitchbooks-new-tool-uses-ai-to-predict-which-startups-will-successfully-exit/
- https://www.cbinsights.com/mosaic-health/
- https://tracxn.com ; https://libguides.stanford.edu/library/tracxn
- https://dealroom.co/contact-us-pricing
- https://www.signalfire.com/blog/engineering-at-the-heart-of-venture-capital
- https://eqtgroup.com/about/motherbrain ; https://tech.eu/2025/11/20/how-eqt-uses-ai-to-see-the-startup-world-differently/
- https://techcrunch.com/2019/02/11/inreach-ventures-the-ai-powered-european-vc-closes-new-e53m-fund/
- https://www.moonfire.com/stories/transformers-llms-and-the-r-evolution-of-the-moonfire-tech-stack/
- https://tribecap.co/essays/a-quantitative-approach-to-product-market-fit
- https://correlationvc.com ; https://www.prnewswire.com/news-releases/correlation-ventures-announces-the-close-of-its-third-fund-and-additions-to-partnership-301864529.html
- https://www.mckinsey.com/industries/technology-media-and-telecommunications/our-insights/a-machine-learning-approach-to-venture-capital
- https://techcrunch.com/2023/07/11/here-is-a-term-sheet-beep-boop/ (Connetic / Wendal)
- https://www.landscape.vc/
- https://www.affinity.co/blog/data-driven-vc-landscape ; https://datadrivenvc.io/data-driven-vc-landscape-2025

Evidence: see URLs in section 3 and in `signal_evidence.json`.

Alumni and companies: see URLs in sections 6 and 7, plus
- https://commacapital.substack.com/p/the-spacex-constellation
- https://jeffburke.substack.com/p/the-spacex-effect-companies-founded
- https://concept.vc/news/palantir-spin-outs-a-deep-dive
- https://www.fastcompany.com/91169196/how-a-network-of-ex-tesla-employees-created-a-10-billion-worth-of-clean-energy-startups
- https://rsl.ethz.ch/partnership/spinoff/anybotics.html
- https://research.contrary.com/company/skild-ai
- https://stealthstartupspy.substack.com/p/stealth-startup-spy-321
