# Source card: `research` — papers and the people behind them

Recon run: 2026-10-01 23:56 UTC to 2026-10-02 00:46 UTC, from this machine, no API keys, no accounts.
User-Agent on every call: `antifund-sourcing-research/0.1 (mason@alterity.systems)`.
Everything below is measured unless it is explicitly marked "not verified".

Fixtures (real responses, trimmed): `pipeline/fixtures/research/`

| file | what it is |
|---|---|
| `arxiv_api_cs_RO_recent.atom.xml` | arXiv API Atom response, 5 newest `cat:cs.RO` entries (verbatim) |
| `openalex_affiliation_signals.json` | 4 OpenAlex responses: works with raw affiliations (incl. unmatched company strings), batched author timeline, author singletons, strong-authors-with-null-institution list |
| `paper_code_linkage_hf_s2.json` | HF `daily_papers`, HF `papers/{id}` (200 and 404), Semantic Scholar `paper/batch` and `author/batch` |

---

## 0. TL;DR

| Source | Works free, no key? | Role in the pipeline |
|---|---|---|
| arXiv API `export.arxiv.org/api/query` | Yes. 0.2 to 0.5 s per call when healthy, but unusable for us from about 00:05 to 00:31 UTC (429 / 503 / timeouts) | Keyword + category + date-range discovery, backfill |
| arXiv RSS `rss.arxiv.org` | Yes, fast, cached | Daily delta (best) |
| arXiv OAI-PMH `oaipmh.arxiv.org/oai` | Yes, slow (29 s to 90 s+ timeouts) | Complete daily delta incl. `comments` and `submitter`; backfill |
| arXiv HTML `arxiv.org/html/{id}` | Yes (robots.txt: Crawl-delay 15) | Day-0 affiliations and author email domains |
| OpenAlex `api.openalex.org` | Yes, 1000 credits/day anonymous | Author strength, affiliation history, **unmatched-affiliation detection** (the founder signal) |
| Semantic Scholar Graph API | Barely: shared anonymous pool, mostly 429 | Citation counts for arXiv preprints (OpenAlex undercounts these badly) |
| Hugging Face papers API | Yes, 500 req / 5 min | paper -> GitHub repo / project page / HF org linkage, community upvotes |
| Papers with Code API | **Dead** (302 to `huggingface.co/papers/trending`) | none |
| ROR `api.ror.org/v2` | Yes | "Is this org already known?" novelty check |
| ORCID `pub.orcid.org/v3.0` | Yes anonymous, but employment data was empty in 2 of 2 samples | weak, optional |

The single strongest thing found: **OpenAlex `authorships[].affiliations[]` entries whose `institution_ids` is `[]`**. A raw affiliation string that OpenAlex could not map to a ROR institution is, in practice, a company too new to be in ROR. In one 200-work page of recent arXiv robotics preprints this surfaced Genesis AI, X-Humanoid, Sapient Intelligence, DexGEM Lab / DexRobot, Tsing-AI, Sudo AI GmbH, XGRIDS; in fusion/nuclear topics it surfaced Gauss Fusion, Proxima Fusion, Blue Laser Fusion, Beijing VeloAlpha, Sophelio, Helicity Space, Ergodic, Light Stream Labs.

---

## 1. arXiv

### 1.1 Query API

```
GET https://export.arxiv.org/api/query
    ?search_query={q}            # e.g. cat:cs.RO AND (abs:humanoid OR abs:"whole-body")
    &sortBy=submittedDate        # or lastUpdatedDate | relevance
    &sortOrder=descending
    &start={offset}
    &max_results={n}
```

- Auth: none. Headers: none required (we sent the contact UA).
- Response: Atom XML. `opensearch:totalResults`, `opensearch:startIndex`, `opensearch:itemsPerPage`.
- Date window works inside `search_query`: `submittedDate:[202609240000 TO 202610010000]` (UTC, `YYYYMMDDHHMM`).
- `max_results=200` returned 200 entries, 516 KB, 0.50 s (when healthy).
- Pagination via `start` verified on the 681-result cs.RO window: `start=0` 200 entries, `start=200` 200 entries (0 overlap with page 0, 503 KB, 0.43 s), `start=600` 81 entries.
- Courtesy delay: 8 sequential calls at 3.2 s spacing all succeeded (0.19 to 0.73 s each) between 23:56 and 23:58 UTC; 18 sequential calls at 6 s spacing all succeeded between 00:42 and 00:45 UTC (0.21 to 0.43 s, one 4.8 s). In between, nothing worked (gotcha 1).

Queries run while healthy (all-time `totalResults`, 6 newest fetched each):

| query | totalResults |
|---|---|
| `cat:cs.RO` | 59,589 |
| `cat:cs.RO AND submittedDate:[202609240000 TO 202610010000]` | 681 (7 days) |
| `cat:cs.RO AND (abs:humanoid OR abs:"whole-body")` | 2,549 |
| `cat:physics.plasm-ph AND (abs:tokamak OR abs:stellarator OR abs:fusion)` | 3,641 |
| `cat:physics.app-ph AND (abs:battery OR abs:"solid-state")` | 867 |
| `cat:eess.SY AND (abs:grid OR abs:"power system")` | 6,196 |
| `cat:cs.AR AND (abs:accelerator OR abs:chip OR abs:RISC-V)` | 4,441 |
| `cat:eess.SP AND (abs:radar OR abs:UAV OR abs:drone)` | 4,035 |

Thesis queries with a 7-day window, each ANDed with `submittedDate:[202609240000 TO 202610010000]`. First attempt at 00:05 UTC: 15 of 15 failed (429 / 503 / 60 s timeout). Second attempt at 00:42 UTC, 6 s spacing: 15 of 15 succeeded.

| bucket | query | 7-day total |
|---|---|---|
| cs.RO all | `cat:cs.RO` | 681 |
| humanoid | `cat:cs.RO AND (abs:humanoid OR abs:"whole-body" OR abs:"loco-manipulation")` | 61 |
| embodied outside cs.RO | `(cat:cs.AI OR cat:cs.LG OR cat:cs.CV) AND (abs:"vision-language-action" OR abs:embodied OR abs:"robot learning") ANDNOT cat:cs.RO` | 66 |
| drones | `(cat:cs.RO OR cat:eess.SY) AND (abs:UAV OR abs:drone OR abs:quadrotor OR abs:"unmanned aerial")` | 36 |
| plasma all | `cat:physics.plasm-ph` | 51 |
| fusion | `cat:physics.plasm-ph AND (abs:tokamak OR abs:stellarator OR abs:"inertial confinement" OR abs:fusion)` | 22 |
| grid | `cat:eess.SY AND (abs:grid OR abs:"power system" OR abs:microgrid OR abs:inverter)` | 41 |
| app-ph all | `cat:physics.app-ph` | 65 |
| batteries | `(cat:physics.app-ph OR cat:cond-mat.mtrl-sci OR cat:physics.chem-ph) AND (abs:battery OR abs:"solid electrolyte" OR abs:"lithium metal" OR abs:"sodium-ion")` | 8 |
| radar / UAS signal | `cat:eess.SP AND (abs:radar OR abs:UAV OR abs:drone OR abs:jamming)` | 19 |
| cs.AR all | `cat:cs.AR` | 43 |
| chips | `cat:cs.AR AND (abs:accelerator OR abs:chip OR abs:"RISC-V" OR abs:photonic OR abs:"in-memory")` | 30 |
| fission | `abs:"small modular reactor" OR abs:microreactor OR abs:"molten salt reactor" OR abs:"nuclear reactor"` | 4 |
| space | `(cat:cs.RO OR cat:eess.SY OR cat:physics.space-ph OR cat:astro-ph.IM) AND (abs:spacecraft OR abs:satellite OR abs:"on-orbit")` | 33 |
| manufacturing | `(cat:cs.RO OR cat:eess.SY) AND (abs:manufacturing OR abs:assembly OR abs:welding OR abs:machining)` | 37 |

Volume reality: robotics is roughly 100 papers a day; every other thesis area is single digits a day on arXiv. Fusion, fission, batteries, space and manufacturing publish mostly in journals, which is why the OpenAlex path (section 2) matters more for them. At 00:44 UTC the newest entry was still a 2026-09-30 submission: the API had not yet picked up the 10-01 batch.

Fields worth keeping (Atom, namespaces `a`=Atom, `arxiv`=`http://arxiv.org/schemas/atom`):

| path | note |
|---|---|
| `entry/a:id` | `http://arxiv.org/abs/2609.40353v1` -> arxiv_id + version |
| `entry/a:title`, `entry/a:summary` | collapse whitespace |
| `entry/a:published`, `entry/a:updated` | v1 time, latest version time |
| `entry/a:author/a:name` | ordered. `arxiv:affiliation` child present on only 2 of 200 entries |
| `entry/arxiv:primary_category/@term`, `entry/a:category/@term` | `cat:cs.RO` matches cross-lists: 173 of 200 had cs.RO as primary |
| `entry/arxiv:comment` | venue acceptance, project page, code URL |
| `entry/a:link[@title='pdf']/@href` | |

Link coverage in abstract + comment, 200 newest cs.RO entries (2026-09-29 to 09-30):
16 (8%) contain a `github.com/{owner}/{repo}` link, 45 (22.5%) a `*.github.io` project page, 67 (33.5%) any URL, 2 a huggingface.co link.

### 1.2 RSS / Atom daily feed (best daily delta)

```
GET https://rss.arxiv.org/rss/{cat}[+{cat}...]      # RSS 2.0
GET https://rss.arxiv.org/atom/{cat}[+{cat}...]     # Atom
```

- `rss/cs.RO`: 200, 0.14 s, 445 KB, 203 items (127 `new`, 47 `replace`, 17 `cross`, 12 `replace-cross`).
- `rss/cs.RO+eess.SY+physics.plasm-ph+physics.app-ph+eess.SP+cs.AR`: 200, 1.6 s, 691 KB, 322 items (179 new, 68 replace, 38 cross, 37 replace-cross).
- Headers: `etag`, `cache-control: max-age=...` -> use `If-None-Match`. `lastBuildDate: Thu, 01 Oct 2026 04:00:06 +0000`, i.e. rebuilt once a day at 04:00 UTC; `skipDays` Saturday, Sunday.
- Item fields: `title`, `link` (`https://arxiv.org/abs/{id}`), `guid` (`oai:arXiv.org:{id}v{n}`), `category` (repeated), `pubDate`, `arxiv:announce_type` (`new|cross|replace|replace-cross`), `dc:creator` (comma-joined names), `description` (`arXiv:{id}v{n} Announce Type: new \nAbstract: ...`).
- Missing vs API: no `comment`, no affiliations, one day only (no history).

### 1.3 OAI-PMH

```
GET https://oaipmh.arxiv.org/oai?verb=ListRecords&metadataPrefix=arXiv&set=cs:cs:RO&from=2026-09-30
GET https://oaipmh.arxiv.org/oai?verb=GetRecord&metadataPrefix=arXivRaw&identifier=oai:arXiv.org:2609.39403
```

- `https://export.arxiv.org/oai2` redirects to `https://oaipmh.arxiv.org/oai`.
- Day granularity (`YYYY-MM-DD`). Set names are `{group}:{archive}:{CAT}`: `cs:cs:RO`, `cs:cs:AR`, `physics:physics:plasm-ph`, `eess:eess:SY`.
- `set=cs:cs:RO&from=2026-09-30`: 403 records, 1.40 MB, **29.2 s**, no `resumptionToken`.
- `set=cs:cs:AR&from=2026-09-30` at 00:20 UTC: 36 records, 67 s. `physics:physics:plasm-ph` and `eess:eess:SY` at the same time: timed out at 90 s with 0 bytes.
- `metadataPrefix=arXiv`: `id`, `created`, `updated`, `authors/author/{keyname,forenames}`, `title`, `categories`, `comments` (215 of 403), `license`, `abstract`. 0 affiliation elements.
- `metadataPrefix=arXivRaw`: adds `submitter` (name of the uploading account) and full `version` history (date, size). `submitter` is a cheap "who drove this paper" hint.

**Which for daily deltas?** RSS: one cached GET for all categories, tells new from replacement. OAI-PMH as the nightly reconciliation (it has `comments`, where code links live, and catches replacements). Query API for keyword-targeted pulls and date-range backfill only.

### 1.4 arXiv HTML (day-0 affiliations and email domains)

```
GET https://arxiv.org/html/{arxiv_id}v{n}
```

- `robots.txt`: `/html` allowed, `Crawl-delay: 15`. `/api` is disallowed on arxiv.org (use export.arxiv.org).
- 27 fetches over the run: 23 x 200, 3 x 404 (no LaTeXML rendering), 1 timeout.
- Parse the block between `<div class="ltx_authors">` and `class="ltx_abstract"` (the abstract div carries an `id`, so do not match `<div class="ltx_abstract">` literally):
  - names: `span.ltx_personname` (strip `<sup>`)
  - affiliations: `span.ltx_contact.ltx_role_affiliation`
  - emails: regex over the first ~300 KB of text, plus the `{a,b,c}@domain` brace form
- 14-paper sample of recent cs.RO: 12 loaded; 9 of 12 yielded at least one email domain; 7 of 12 an affiliation span.
- New-company domains seen: `dex-gem.ai` (2609.24093), `x-humanoid.com` (2609.23483). Affiliation text seen: `1 Carnegie Mellon University 2 Genesis AI` (2609.28766).

---

## 2. OpenAlex

### 2.1 Access and measured limits

- Base `https://api.openalex.org`. No key. `mailto=` accepted; limits were identical with and without it.
- Response headers on every call: `x-ratelimit-limit: 1000`, `x-ratelimit-limit-usd: 0.1`, `x-ratelimit-remaining`, `x-ratelimit-credits-used`, `x-ratelimit-reset` (seconds). At 23:57:46 UTC reset was 134; at 00:00:05 UTC it read 86394 and remaining was back to 990: **daily budget of 1000 credits, resets at 00:00 UTC**.
- Measured credit cost:

| call | credits |
|---|---|
| singleton by OpenAlex id (`/authors/A...`, `/works/W...`, `/institutions/I...`) | 0 |
| singleton `/works/doi:10.48550/arxiv.2609.28766` | 0 |
| singleton `/works/https://doi.org/10.48550/arXiv.2410.24164` | 1 |
| list with `filter=` (any page size up to 200), incl. `group_by` | 1 |
| anything with `search=` or a `*.search` filter (`raw_affiliation_strings.search`, `display_name.search`) | 10 |
| invalid-filter 400 (useful: lists every valid filter name) | 0 |

- `per-page=200` honoured. `cursor=*` returns `meta.next_cursor`.
- `GET /rate-limit` -> 401 "You must provide a valid API key".
- `from_created_date` filter -> 429 "Plan upgrade required ... Premium, Institutional, or Partner plan".
- One `group_by` call hung 19 s and died (curl 35); retry answered in 0.49 s.
- Total spend for this whole recon: about 100 credits (13 before the midnight reset, 88 after).

### 2.2 Endpoints

```
# author profile (0 credits)
GET /authors/{A_id}?select=id,display_name,orcid,works_count,cited_by_count,summary_stats,last_known_institutions,affiliations,counts_by_year,created_date

# works of up to N authors in one call, newest first (1 credit / 200 works)
GET /works?filter=author.id:A1|A2|A3,from_publication_date:2023-01-01&sort=publication_date:desc&per-page=200
          &select=id,doi,title,publication_date,type,cited_by_count,authorships

# recent works in thesis topics that carry raw affiliation strings (1 credit / page)
GET /works?filter=primary_topic.id:T10653|T10462|T10879|T10571|T10191,from_publication_date:2026-08-15,
           has_raw_affiliation_strings:true,primary_location.source.id:S4306400194   # S4306400194 = arXiv
          &per-page=200&cursor=*&sort=publication_date:desc
          &select=id,doi,title,publication_date,cited_by_count,primary_topic,authorships

# company-suffix search inside raw affiliations (10 credits / page)
GET /works?filter=primary_topic.id:T10346|T10384|T10597|T11242,from_publication_date:2026-05-01,
           raw_affiliation_strings.search:Inc|LLC|Ltd|GmbH|Corp&per-page=200&sort=publication_date:desc

# strong authors whose latest affiliation OpenAlex could not resolve (1 credit / page)
GET /authors?filter=topics.id:T10653,summary_stats.h_index:>14,last_known_institutions.id:null&sort=cited_by_count:desc

# batch DOI lookup (1 credit)
GET /works?filter=doi:10.48550/arxiv.2609.39403|10.48550/arxiv.2609.36593|...
```

Thesis topic ids (from `/topics?filter=subfield.id:...` and `display_name.search`):

| area | topic ids |
|---|---|
| Robotics | T10653 Robot Manipulation and Learning, T10462 RL in Robotics, T10879 Robotic Locomotion and Control, T10571 Robotic Mechanisms and Dynamics, T10191 Robotics and Sensor-Based Localization, T10586 Path Planning, T12784 Modular/Swarm, T10868 Soft Robotics |
| Drones / autonomy | T11133 UAV Applications and Optimization, T12158 Guidance and Control Systems, T11099 Autonomous Vehicle Technology |
| Fusion | T10346 Magnetic confinement fusion, T10384 Laser-Plasma Interactions |
| Fission | T10597 Nuclear reactor physics and engineering, T11242 Nuclear Materials and Properties |
| Batteries | T10663, T10018, T10281 |
| Grid | T10223 Microgrid Control, T10603 Smart Grid, T10305 Power System Optimization and Stability, T10228 Inverters |
| Semis | T10558 Semiconductor Devices and Circuit Design, T10361 SiC, T10472 |
| Space | T11701 Space Satellite Systems and Control, T12449 Spacecraft Design, T12513 Rocket and propulsion, T13200 Spacecraft and Cryogenic |
| Manufacturing | T10783, T10705 (additive), T11741 |

### 2.3 Queries run and result counts

| query | count |
|---|---|
| robotics topics, arXiv source, `has_raw_affiliation_strings:true`, from 2026-08-15 | 660 works. First 200: 1,106 authorships, 1,044 with raw strings, 944 matched to an institution, 100 fully unmatched, 29 partially unmatched, 37 distinct unmatched strings |
| robotics topics, `institutions_distinct_count:0`, any source, from 2026-06-01 | 365 works (dominated by Zenodo single-author uploads: noisy) |
| robotics topics, `authorships.institutions.type:company`, from 2026-04-01, `group_by=authorships.institutions.id` | 702 works, capped at 200 groups |
| `raw_affiliation_strings.search:stealth`, from 2026-01-01 | 25 works (mostly stealth *materials* labs; 4 genuine "Stealth Mode"/"Stealth Software" hits) |
| fusion + fission topics, raw affiliation contains Inc/LLC/Ltd/GmbH/Corp, from 2026-05-01 | 293 works |
| UAV / space / propulsion / guidance topics, Inc/LLC/Corp/GmbH, from 2026-06-01 | 62 works |
| battery / SiC / semis / additive topics, Inc/LLC/GmbH, from 2026-08-01 | 110 works |
| authors: topic T10653, h-index > 14, `last_known_institutions.id:null` | 313 authors |
| top-cited robotics-topic works since 2025-01-01 | 26,919 works in filter |

### 2.4 Fields worth keeping

Work:
- `id`, `doi`, `title`, `publication_date`, `type` (`preprint|article|...`), `primary_topic.{id,display_name,subfield,field}`
- `cited_by_count`, `counts_by_year[].{year,cited_by_count}`, `fwci`, `citation_normalized_percentile.{value,is_in_top_1_percent}`
- `authorships[].author.{id,display_name,orcid}`
- `authorships[].author_position` (`first|middle|last`), `authorships[].is_corresponding`
- `authorships[].institutions[].{id,display_name,ror,type,country_code}` (`type`: `education|company|facility|government|nonprofit|healthcare|funder`)
- `authorships[].raw_affiliation_strings[]`
- **`authorships[].affiliations[].{raw_affiliation_string,institution_ids[]}`** <- per-string match result

Author:
- `works_count`, `cited_by_count`, `summary_stats.{h_index,i10_index,2yr_mean_citedness}`
- `counts_by_year[].{year,works_count,cited_by_count}`
- `affiliations[].{institution.{id,display_name,type,ror},years[]}`
- `last_known_institutions[]` (null/empty when the newest work has no matched institution)
- `created_date` (a profile minted in the last few weeks is a disambiguation fragment)

Institution (`/institutions/{id}`, 0 credits): `ror`, `type`, `homepage_url`, `works_count`, `counts_by_year`.

---

## 3. Semantic Scholar Graph API (unauthenticated)

```
GET  https://api.semanticscholar.org/graph/v1/paper/arXiv:{id}?fields=title,publicationDate,citationCount,influentialCitationCount,externalIds,authors.name,authors.authorId,authors.hIndex,authors.citationCount,authors.paperCount,authors.affiliations
POST https://api.semanticscholar.org/graph/v1/paper/batch?fields=...      body {"ids":["arXiv:2609.28766", ...]}
POST https://api.semanticscholar.org/graph/v1/author/batch?fields=name,affiliations,homepage,paperCount,citationCount,hIndex,externalIds,url   body {"ids":[...]}
GET  https://api.semanticscholar.org/graph/v1/paper/search?query=...&year=2026&limit=5&fields=...
GET  https://api.semanticscholar.org/graph/v1/paper/search/bulk?query=...&publicationDateOrYear=2026-09-01:&sort=publicationDate:desc&fields=...
```

Measured, no key, 22 calls over ~35 min: **8 x 200, 13 x 429, 1 x 400** (the 400 was our own unencoded `|` in a bulk query).
- 429 body: "Too Many Requests ... apply for a key". No `Retry-After`, no rate-limit headers.
- After one 200, the next 5 calls at 3 s spacing were all 429; 5 more at 10 s spacing all 429. At 60 s spacing, 3 of 5 valid calls succeeded. Treat anonymous access as "about one call a minute, when you are lucky".
- Also confirmed working (one 200 each): `GET /author/{authorId}/papers?fields=title,publicationDate,citationCount,externalIds&limit=5` and `GET /paper/arXiv:{id}/citations?fields=title,publicationDate&limit=5` (citing papers with dates: the raw material for a real citation-velocity curve).
- So: always use the batch POST endpoints. One `paper/batch` call returned all 8 requested arXiv ids, including two submitted 2026-09-30 (S2 indexes arXiv within about a day, faster than OpenAlex).
- `paper/search` relevance: `humanoid whole-body control`, year 2026 -> total 2,010. `search/bulk` `humanoid` from 2026-09-01 -> total 241, 68 KB, no continuation token needed.
- `authors.affiliations` was `[]` for every author in every response. S2 is not an affiliation source.
- Citation counts are the reason to use it: arXiv:2410.24164 (pi-0) -> S2 `citationCount` 2,925, `influentialCitationCount` 515; OpenAlex `cited_by_count` for the same DOI: 11.
- S2 author profiles are fragmented too (same paper: Sergey Levine appears as author id 2249615151 with hIndex 37; Hao Su on 2609.27095 as a profile with hIndex 3).

---

## 4. Paper -> code linkage

### 4.1 Papers with Code: dead

`GET https://paperswithcode.com/api/v1/papers/?arxiv_id=2410.24164` -> **302** `Location: https://huggingface.co/papers/trending`. Same for `/api/v1/`. The old dump host `production-media.paperswithcode.com` fails the TLS handshake.

### 4.2 Hugging Face papers API (works)

```
GET https://huggingface.co/api/daily_papers?date=2026-09-30&limit=100      # also ?week=2026-W39, ?p=1 (Link: rel="next")
GET https://huggingface.co/api/papers/{arxiv_id}
GET https://huggingface.co/api/papers/search?q=humanoid&limit=8
GET https://huggingface.co/api/arxiv/{arxiv_id}/repos
```

- No auth. Rate-limit headers: `ratelimit-policy: "fixed window";"api";q=500;w=300` (500 per 5 min); search is its own bucket `q=50;w=300`; HTML pages `q=100;w=300`.
- `daily_papers?date=2026-09-30&limit=100` -> 92 items, 497 KB. `?week=2026-W39&limit=100` -> 100 items; 60 have `githubRepo`; of 19 robotics/embodied hits 11 have `githubRepo`, 9 `projectPage`.
- Keep: `paper.id` (arXiv id), `paper.title`, `paper.publishedAt`, `paper.submittedOnDailyAt`, `paper.upvotes`, `paper.githubRepo`, `paper.githubStars`, `paper.projectPage`, `paper.authors[].{name,user}`, `organization.{name,fullname}` (HF org that claimed the paper), `numComments`, `isAuthorParticipating`.
- `papers/{id}` -> `linkedModels[]`, `linkedDatasets[]`, `linkedSpaces[]`, `numTotalModels`, `upvotes`, `ai_keywords[]`. Returns **404** for any paper not cited in a HF repo README (2609.39403 -> 404). Do not treat 404 as an error.
- `arxiv/{id}/repos` -> `{models:[...],datasets:[...],spaces:[...]}` (52 KB for pi-0).
- Coverage is ML-centric: useful for robotics / embodied AI, useless for fusion, batteries, grid, space.

### 4.3 Regex fallback (always run)

Over arXiv `summary` + `comment` (and the HTML body when fetched):
```
https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)
https?://([A-Za-z0-9-]+)\.github\.io(/[^\s,;)]*)?
```
Strip trailing `.`, `,`, `)` from the repo segment (seen: `github.com/WeiYuFei0217/StreamRig.`). A github.io subdomain is the GitHub org/user (`xpeng-robotics.github.io` -> org `xpeng-robotics`). Org-owned repos seen this way: `MagiclabRobotics/Inference`, `form-robots/FORM`, `fluentrobotics/Legible_MPPI`.

---

## 5. The founder signal: detecting affiliation transitions

### 5.1 What the data looks like (real example, in the fixture)

Batched timeline call for authors A5010354503, A5044383265, A5022138848, A5087311970:

```
Yi-Ling Qiao (A5010354503, h-index 10)
  2026-09-23  institutions=[]                         raw=['Genesis AI']            arXiv 2609.28766
  2025-10-19  institutions=[Univ. of Maryland (education)]  raw=['University of Maryland']
  2023..2025  University of Maryland on 8 further works

Zhehuan Chen (A5044383265)
  2026-09-23  institutions=[]                         raw=['Genesis AI']
  2026-06-02  institutions=[UMass Amherst (education)]
  2024-03-30  institutions=[Peking University]

Alexis Duburcq (A5022138848)   profile affiliations: Wandercraft (company) 2018-2022
  2026-09-23  institutions=[]                         raw=['Genesis AI']

Minchen Li (A5087311970)   dual affiliation on 16 works, 2026-03-01 to 2026-09-23
  institutions=[Carnegie Mellon University]           raw=['Carnegie Mellon University','Genesis AI, San Carlos, USA']
```

Second batch: Jost Tobias Springenberg (A5017985443, h-index 36; profile history Google DeepMind 2024-2025): 2026-03-04 `institutions=[]`, `raw=['Physical Intelligence ,']`.

### 5.2 Detection algorithm (exact fields)

1. **Candidate works** (daily, 1 credit per 200): section 2.2 "recent works in thesis topics" query, one per thesis area.
2. **Per authorship**, iterate `authorships[i].affiliations[]`:
   - `institution_ids == []` and `raw_affiliation_string` non-empty -> `unmatched_org`.
   - If `authorships[i].institutions` is non-empty as well, it is a **dual affiliation** (professor or student with a company on the side); if empty, a **full move**.
3. **Clean the string**: drop leading footnote junk (`") Proxima Fusion GmbH , Munich"`), cut at the first comma-address, casefold. Discard if it matches `universit|institut|college|school|hospital|academy|laborator|department|faculty|independent|^\W*$|^https?:` or is a bare place name. Keep a positive hint flag for `Inc|LLC|Ltd|GmbH|Corp|Co\.|Technologies|Labs|Robotics|Energy|Fusion|Aerospace|AI`.
4. **Novelty check**: `GET https://api.ror.org/v2/organizations?query="{name}"`. `number_of_results == 0` -> not in ROR (measured: Genesis AI, Gauss Fusion, Physical Intelligence, Proxima Fusion all 0; Tokamak Energy 1, ROR record created 2018-11-14 with website). Also keep our own first-seen date per cleaned string.
5. **Author history** (1 credit per batch): `works?filter=author.id:A1|A2|...,from_publication_date:{today-3y}&sort=publication_date:desc&select=...authorships`. For each author build `[(publication_date, {institution ids}, {unmatched strings})]`.
   - `prior_home` = most frequent `institutions[].id` over works older than the first unmatched appearance, with its `type`.
   - **Transition event** when: the unmatched org first appears within the last N days, AND at least 2 earlier works carry `prior_home`, AND the newest work does not carry `prior_home` (full move) or still does (dual).
   - `transition_type` = `{education|facility|government -> unknown_company}` (spin-out: highest value), `{company -> unknown_company}` (operator leaving big lab), `dual`.
6. **Author strength** (0 credits each): `/authors/{id}` -> `summary_stats.h_index`, `cited_by_count`, `counts_by_year`. If `created_date` is within ~60 days or `works_count <= 2`, the id is a fragment: re-resolve with `authors?filter=display_name.search:{name},topics.id:{T}&sort=cited_by_count:desc` (10 credits) and pick the profile whose `last_known_institutions` or co-authors overlap. Measured: "Hao Su" on 2609.27095 is A5152653211 (created 2026-09-25, 2 works, h 0); the real profile is A5091622325 (92 works, 12,090 citations, h 27, UC San Diego).
7. **Shortcut list** (weekly, 1 credit per 200): `authors?filter=topics.id:{T},summary_stats.h_index:>14,last_known_institutions.id:null`. Keep rows with `counts_by_year[year=2026].works_count > 0`, then run step 5 on them. `last_known_institutions` is null both for "new unmatched company" and for "newest work has no affiliation data at all" (Shuran Song's newest rows have `raw=[]`), so this list is a candidate generator, not a verdict.
8. **Day-0 path** (before OpenAlex has the paper): arXiv HTML -> affiliation spans and email domains (section 1.4). An email domain that is not academic, not a free-mail provider, and not a known big-company domain is a new-company domain. Do not classify "academic" by `.edu` alone: `tecnico.ulisboa.pt`, `torontomu.ca`, `aist.go.jp` showed up as false "non-academic" with a naive rule. Use ROR `links`/`domains` or a university-domain list.

### 5.3 Timing

- arXiv submission 2026-09-29 -> OpenAlex work `created_date` 2026-10-01 (2 days). Submissions of 2026-09-30 were not in OpenAlex yet on 10-02 00:10 UTC.
- Raw affiliation strings lag further: 0 of 5 and 0 of 7 authorships on works created 09-30/10-01, versus 94% of authorships on works dated 09-17 to 09-27.
- So: HTML/email path gives the signal on day 0 to 1; the OpenAlex path confirms and adds history around day 5 to 10.

---

## 6. Features a scorer should compute

Paper level
- `topic_bucket` (keyword + category match), `is_new_vs_replace` (RSS `announce_type`)
- `has_code` / `has_project_page` (regex or HF), `github_owner_is_org`
- `hf_upvotes`, `hf_upvote_velocity` (delta between polls), `hf_github_stars`
- `s2_citation_count`, `s2_influential_citations`, `citations_per_month_since_publication`
- `n_authors_unmatched_org`, `share_authors_unmatched_org` (11 of 13 on TapeSim)
- `non_academic_email_domains[]`, `email_domain_first_seen_date`

Org string level (cluster of cleaned unmatched strings)
- `first_seen_date`, `papers_30d`, `papers_90d`, `paper_velocity` (30d vs prior 30d), `acceleration`
- `distinct_authors`, `distinct_senior_authors` (h-index >= 15 after re-resolution)
- `in_ror` (bool), `ror_created_date`
- `prior_homes[]` of its authors (labs it is drawing from), `max_author_h_index`, `sum_author_citations`
- `has_company_suffix`, `has_address` (street address in the raw string usually means a registered company)
- `dual_affiliation_share` (professor-led spin-out pattern)

Author level
- `h_index`, `cited_by_count`, `works_count`, `two_year_mean_citedness`
- `citation_velocity` = `counts_by_year[y].cited_by_count / counts_by_year[y-1].cited_by_count` (use full years only; see gotchas)
- `works_velocity` = works this year vs last
- `transition_event` {date, from_institution, from_type, to_string, type: full|dual}
- `days_since_transition`, `first_last_author_share` on recent works
- `last_known_institutions_null` and `profile_is_fragment` flags

---

## 7. Entity resolution

| from | to | how |
|---|---|---|
| arXiv id | DOI | `10.48550/arXiv.{id}` (OpenAlex lowercases it) |
| arXiv id | OpenAlex work | `/works/doi:10.48550/arxiv.{id}` (0 credits) or batch `filter=doi:a|b|c` |
| arXiv id | S2 paper | `arXiv:{id}` in `paper/batch` |
| arXiv id | HF paper | `/api/papers/{id}` |
| paper | GitHub repo / org | HF `githubRepo`; regex on abstract/comment/HTML; `{org}.github.io` |
| paper | company domain | email domain from arXiv HTML; project page host |
| unmatched affiliation string | company | cleaned string as provisional key -> join on email domain, GitHub org, HF `organization.name`; ROR for known orgs (`ror`, `links[type=website]`) |
| matched institution | domain | `/institutions/{id}` -> `homepage_url`, `ror` |
| author name on paper | person | OpenAlex `author.id` + `orcid` when present; fall back to (normalized name, co-author set, topic) because of fragments; S2 `authorId` and `externalIds.DBLP` as secondary keys |

---

## 8. Gotchas (all hit during this run)

1. **arXiv API outage window.** 23:56 to 23:58 UTC: 8 of 8 calls fine. From about 00:05 UTC (just after the 20:00 ET daily announcement) to 00:31 UTC: 15 of 15 thesis queries failed, then 4 of 4 retries and 6 of 6 single probes failed: `429` with body `Rate exceeded.` (sometimes after 14 to 31 s, sometimes in 0.15 s), `503` after 46 s, or 60 s read timeouts. No `Retry-After`. After 11 minutes of sending nothing, a probe at 00:42 UTC returned 200 in 0.3 s and the next 17 calls were all fine. During the failure window an HTML probe at 15 s spacing was also running against `arxiv.org`, and our retry loop kept knocking, so whether this was per-IP throttling, backend overload at announcement time, or both is not determined. Rules that follow: single-thread everything arXiv (treat API, OAI and HTML as one budget), on the first 429 stop for 10+ minutes instead of retrying, avoid 00:00 to 01:00 UTC, and keep RSS as the primary daily path.
2. arXiv OAI-PMH is slow even when it works (29 s for 403 records) and timed out twice at 90 s in the same window.
3. arXiv metadata has no affiliations (2 of 200 API entries, 0 of 403 OAI records).
4. `cat:cs.RO` returns cross-lists; check `arxiv:primary_category`.
5. Keyword regex: `fusion` matches "diffusion" and "sensor fusion" (our RSS bucket count for fusion, 23, is inflated by this). Gate keyword buckets by category and use word boundaries.
6. arXiv HTML is missing for some papers (404) and the author markup varies by LaTeX class; some put every author in one `ltx_personname`, some put the author list inside an "Affiliation" span.
7. **OpenAlex anonymous budget is 1000 credits/day**; `search` costs 10. Design around `filter` + ids. `from_created_date` is paywalled, so "new since yesterday" must use `from_publication_date` plus our own seen-set.
8. `x-ratelimit-remaining` is not perfectly consistent: one response mid-session read 999 with a different reset anchor, the next read 912. Track spend client-side.
9. **OpenAlex citation counts for arXiv preprints are far too low** (pi-0: 11 vs 2,925 on S2). Author `counts_by_year` reflects this: Chelsea Finn shows 2,995 citations in 2023, 830 in 2024, 639 in 2025, 11 in 2026. Do not compute recent citation velocity from OpenAlex for preprint-heavy fields; use S2 for work-level counts and use OpenAlex h-index only as a coarse seniority tier.
10. **Author fragmentation.** 528 of 1,105 authorships (48%) on recent arXiv robotics works point at author ids minted in the last weeks (>= A5152000000), with 1 to 2 works and h-index 0, even for famous people (Hao Su, Henrik Christensen).
11. **Institution false matches.** "Autel US" -> `Autel (Czechia)`; "DexRobot Co. Ltd" -> `Medrobotics (United States)`; UMD "Center for Machine Learning" -> `Machine Science`; UC Berkeley -> `Berkeley College`; `Robotics Research (United States)` (I4210116723) collects 178 works in 2026 and tops the robotics company group-by. A matched `type:company` is not proof of the right company; keep the raw string.
12. Author `affiliations[]` is noisy for common profiles (Chelsea Finn's lists UNC Health Care, Harvard, Tsinghua for 2023, and MIT 1988).
13. `last_known_institutions: null` also fires on missing affiliation data, not only on unmatched companies.
14. `institutions_distinct_count:0` without a source filter is mostly Zenodo single-author uploads ("Independent Researcher", "Saluca LLC", duplicates per version DOI). Restrict to arXiv / journal sources and require >= 2 authors or a senior co-author.
15. Garbage raw strings exist: `Michael`, `University`, `Guangzhou , China`, `Microcontroller Adafruit Feather M4 CAN`, `https:// protracer-failure`.
16. `raw_affiliation_strings.search:stealth` is dominated by stealth-materials labs; use `"stealth mode"`-style phrases and topic filters.
17. OpenAlex topic assignment is rough (DeepSeek-R1 is the top-cited "robotics RL" work; "Magnetic confinement fusion research" claims 5.26 M works) and `publication_date` can be a re-deposit date for decades-old papers.
18. `group_by` is capped at 200 groups and returns co-occurring universities alongside companies.
19. Semantic Scholar without a key: 13 of 22 calls were 429. No affiliations. URL-encode `|` in bulk queries.
20. Papers with Code is gone; HF `papers/{id}` 404s for anything not referenced in a HF repo.
21. ORCID anonymous read works (`Accept: application/json`), but `/employments` was empty for both researchers tried.
22. GitHub links in abstracts often carry a trailing period.

---

## 9. Suggested schedule (fits the free limits)

| when (UTC) | job | cost |
|---|---|---|
| 04:15 daily | RSS for all thesis categories (1 GET, ETag) | free |
| 04:20 daily | regex code/project links; HF `daily_papers` for the day | 1 to 2 HF calls |
| 04:30 daily | arXiv HTML for new papers passing the keyword gate, 15 s apart | ~100 papers = 25 min |
| 06:00 daily | OAI-PMH reconciliation per set (comments, replacements) | 4 to 6 slow calls |
| 07:00 daily | OpenAlex: one works page per thesis area (publication window today-14d .. today-3d), then batched author timelines for authors on unmatched strings | ~30 to 60 credits |
| weekly | OpenAlex strong-author null-institution list per topic; ROR checks on new strings | ~20 credits |
| opportunistic | S2 `paper/batch` for citation counts, retry with multi-minute backoff | 1 to 3 calls |

---

## 10. Entities seen in the live responses

Only things that appeared in a response during this run. "Unmatched" means `authorships[].affiliations[].institution_ids == []` in OpenAlex.

| entity | sector | what was seen | evidence |
|---|---|---|---|
| Genesis AI | robotics / simulation | 11 of 13 authors on TapeSim list "Genesis AI" (unmatched, not in ROR); authors arrived from UMD, UMass Amherst, Wandercraft; Minchen Li (CMU) lists CMU + Genesis AI on 16 works dated 2026-03-01 to 2026-09-23 | https://arxiv.org/abs/2609.28766 |
| Beijing VeloAlpha Technology Co. | fusion software | 8 works between 2026-07-01 and 2026-09-20 (gyrokinetic solvers, tokamak equilibrium DB, 0-D fusion design platform), recurring author Huasheng Xie; unmatched | https://arxiv.org/abs/2609.23296 |
| Gauss Fusion GmbH | fusion (stellarator) | 4 works 2026-06 to 2026-09 incl. "one Gigawatt electric stellarator power plant" equilibrium basis and GIGA magnet system; unmatched | https://arxiv.org/abs/2607.09346 |
| Proxima Fusion GmbH | fusion (stellarator) | 3 works 2026-07 to 2026-09 (PQLS transport solver, SPECTRE 3D equilibria); unmatched | https://arxiv.org/abs/2609.29336 |
| Blue Laser Fusion Inc. | fusion (laser) | 7 authors on a Communications Physics paper on cross-beam energy transfer; unmatched | https://doi.org/10.1038/s42005-026-02829-8 |
| Sophelio LLC (Austin) | fusion software | 5 authors on MGKDB gyrokinetic database; unmatched | https://arxiv.org/abs/2609.03132 |
| Ergodic LLC | fusion / ICF design | 3 authors, differentiable-simulation inverse design of ICF implosions; unmatched | https://doi.org/10.1103/bpms-63ml |
| Helicity Space Corp. (Pasadena) | space propulsion / plasma | 2 authors, Physics of Plasmas paper; unmatched | https://doi.org/10.1063/5.0321458 |
| X-Humanoid, Humanoid Innovation Department | humanoids | 8 authors on STRIDER loco-manipulation; unmatched; corresponding email domain `x-humanoid.com` in arXiv HTML | https://arxiv.org/abs/2609.23483 |
| DexGEM Lab / DexRobot Co. Ltd | dexterous manipulation | 9 authors; email domain `dex-gem.ai`; OpenAlex mis-maps DexRobot to "Medrobotics" | https://arxiv.org/abs/2609.24093 |
| Sapient Intelligence | robotics / AI | 2 authors with a Tsinghua group on G6D pose solver; unmatched | https://arxiv.org/abs/2609.23566 |
| Tsing-AI (Shanghai) Technology Co. | robotics world models | 2 authors with MIT + Tsinghua on PileBelief; unmatched | https://arxiv.org/abs/2609.22858 |
| Sudo AI GmbH (Hao Su) | embodied AI | last author Hao Su lists only "Sudo AI GmbH" on "Intelligence Across Embodiments" (co-authors UCSD, Stanford); the established OpenAlex profile of that name in the same topic is UC San Diego, h-index 27 (name match, not confirmed identity) | https://arxiv.org/abs/2609.27095 |
| XGRIDS | drones / spatial | 1 author on Skytopia monocular drone navigation, with NTU and Autel | https://arxiv.org/abs/2609.26007 |
| Juno Propulsion Inc (Tukwila) | space propulsion / manufacturing | 2 authors, additive-manufacturing qualification for Inconel; unmatched | https://doi.org/10.1007/s11665-026-14784-0 |
| Kall Morris Inc (Marquette, MI) | space (debris removal) | 1 author on Acta Astronautica debris-mitigation standards paper; unmatched | https://doi.org/10.1016/j.actaastro.2026.08.003 |
| Ionworks Technologies Inc (Pittsburgh) | battery modelling | 1 author (Robert Timms), multiscale mechanics in Li-ion models; unmatched | https://arxiv.org/abs/2608.20163 |
| Coulomb AI Inc. (San Francisco) | battery diagnostics | 1 author, ultrasound response of degraded Li-ion cells; unmatched | https://doi.org/10.1016/j.est.2026.124902 |
| RLWRLD | robot foundation models | HF organization attached to HuRo (robotizing human videos for VLA pretraining) in `daily_papers?week=2026-W39`; repo `3587jjh/HuRo`, 48 stars, 18 upvotes | https://arxiv.org/abs/2609.10706 |
| MagiclabRobotics | robotics / VLA | GitHub org `MagiclabRobotics/Inference` linked from "Toward Real-Time VLAs" | https://arxiv.org/abs/2609.39822 |
| Yi-Ling Qiao (researcher) | robotics simulation | University of Maryland 2020-2025 -> "Genesis AI" on 2026-09-23; h-index 10 | https://openalex.org/A5010354503 |
| Jost Tobias Springenberg (researcher) | robot learning | Google DeepMind 2024-2025 -> raw "Physical Intelligence" on 2026-03-04; h-index 36, `last_known_institutions` null | https://openalex.org/A5017985443 |
