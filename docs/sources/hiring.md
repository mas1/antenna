# Source card: Hiring signals (`hiring`)

Measured live on 2026-10-01 23:55Z to 2026-10-02 01:00Z from this machine with `curl` / `python3 urllib`,
no API keys, no logins, User-Agent `antifund-sourcing-research/0.1 (mason@alterity.systems)`.
Everything below is what the endpoints actually returned tonight. Counts are point-in-time.

Fixtures (real responses, long text fields truncated): `/Users/noel/antifund/pipeline/fixtures/hiring/`

| File | What it is |
|---|---|
| `greenhouse_valaratomics_jobs_content.json` | `GET boards-api.greenhouse.io/v1/boards/valaratomics/jobs?content=true`, all 94 jobs, `content` cut to 400 chars |
| `ashby_charge-robotics.json` | `GET api.ashbyhq.com/posting-api/job-board/charge-robotics?includeCompensation=true`, all 14 jobs, descriptions cut to 400 chars |
| `lever_cx2.json` | `GET api.lever.co/v0/postings/cx2?mode=json`, all 5 postings, description fields cut |
| `hn_algolia_whoishiring_2026-10_toplevel.json` | Algolia response for the 105 top-level comments of "Ask HN: Who is hiring? (October 2026)" (`_highlightResult` removed) |
| `ats_registry_seed.json` | Derived, not a raw response: 142 identity-verified `(company, ats, slug)` rows with tonight's feature values, plus 18 slug collisions (wrong company behind a 200). Use it to seed the pipeline without repeating the sweep |

---

## TL;DR

1. **Greenhouse, Lever and Ashby job-board APIs all work with no key and all expose a per-role posted date**
   (`first_published`, `createdAt`, `publishedAt`). 488 slug probes per provider at 0.35 s spacing produced zero 429s.
2. **Ashby is where the small companies are.** In the last three HN hiring threads, links to `jobs.ashbyhq.com`
   outnumber Greenhouse 145 to 20. Ashby also has an unauthenticated GraphQL call that returns the org's
   **name and public website** for a slug, which is the cleanest slug-to-domain verification of any ATS.
3. **Slug guessing alone resolved 127 of 241 thesis companies** to a live, identity-verified board (132 boards).
   Six more boards came from careers pages, for a seed registry of 138 live boards and 11,299 open roles.
   But **18 guessed slugs returned 200 for the wrong company**
   (`radiant` is a UK cloud company, `sila` on Lever is an HVAC firm). Never trust a 200 without checking identity.
4. **Careers-page discovery from a bare domain hit 22 of 36** (61%). Misses are client-rendered pages that proxy
   the ATS through the company's own backend, bot walls (403/429), and tiny teams with no ATS at all.
5. **These APIs are snapshots of currently open roles.** Posted dates only cover roles still open, and they reset
   when a company migrates ATS (seen tonight: Rainmaker, 25 roles all `publishedAt` 2026-10-01 on Ashby while its
   Lever board shows the real dates from April onward). Real velocity needs our own daily snapshots.
6. **HN "Who is hiring?" is a good feed of 2 to 20 person companies.** October 2026 thread: 105 top-level posts
   nine hours after opening; 15 match strict thesis keywords, 8 of those are truly thesis-aligned on manual read.
7. **Workable rate-limits hard** (429 from request 57 onward at 0.35 s spacing). Wayback CDX was offline. YC Work at a
   Startup search needs a login; the open `yc-oss` mirror plus `ycombinator.com/companies/{slug}/jobs` covers it.

---

## 1. Endpoints that work (no auth)

Common headers used everywhere: `User-Agent: antifund-sourcing-research/0.1 (mason@alterity.systems)`,
`Accept: application/json`, `Accept-Encoding: gzip` (cuts Ashby and Lever payloads about 9x).

### 1.1 Greenhouse

```
GET https://boards-api.greenhouse.io/v1/boards/{slug}                 board name + description HTML (tiny, good existence probe)
GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs            all open jobs, light (valaratomics: 94 jobs, 71 KB)
GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true   adds content, departments[], offices[] (1.78 MB for 94 jobs)
GET https://boards-api.greenhouse.io/v1/boards/{slug}/departments     department tree with jobs nested (cheap way to get dept per job)
GET https://boards-api.greenhouse.io/v1/boards/{slug}/offices         offices with departments and jobs nested
```

- Pagination: none. One response holds every job (`meta.total`). Anduril (`andurilindustries`) returned 2,420 jobs in 2.5 MB without `content`.
- Unknown slug: 404 with a 38-byte JSON body. Slugs are case-insensitive (`ValarAtomics` -> 200).
- Headers: `etag` present, `cache-control: max-age=0, private, must-revalidate`, no rate-limit headers.
- Fields to keep (per `jobs[]`): `id`, `title`, `company_name`, `location.name`, `first_published`, `updated_at`,
  `absolute_url`, `requisition_id`, `internal_job_id`, `metadata[]` (e.g. `{"name":"Salary","value":{"min_value","max_value","unit"}}`),
  and with `content=true`: `departments[].name`, `departments[].parent_id`, `offices[].name`, `offices[].location`, `content`.
- **`departments` and `offices` are absent without `content=true`.** For big boards call `/departments` and join on job `id` instead of pulling content.
- The hosted page is now `job-boards.greenhouse.io/{slug}` (older `boards.greenhouse.io/{slug}` and the embed
  `boards.greenhouse.io/embed/job_board?for={slug}` still appear in careers pages).

### 1.2 Lever

```
GET https://api.lever.co/v0/postings/{slug}?mode=json[&limit=N&skip=M][&group=team]
```

- Returns a bare JSON array. `limit`/`skip` work (verified). `group=team` returns `[{title, postings[]}]`.
  `limit=1` is the cheap existence probe (14 KB instead of 9.3 MB raw for `shieldai`, about 580 postings).
- Unknown slug: 404 `{"ok":false,"error":"Document not found"}`. Known account with nothing open: **200 `[]`**
  (seen: `forterra`, `nominal`, `extropic`, `proximafusion`, `reliable`, `beta`, `perennial`, `genesis-ai`).
- **Slugs are case-sensitive** (`ShieldAI` -> 404). `api.eu.lever.co` answers on the same path and returned 404 for a US slug (EU-hosted tenants are expected there; not verified tonight).
- Fields: `id`, `text` (title), `createdAt` (epoch ms), `categories.{team,department,location,commitment,allLocations[]}`,
  `country`, `workplaceType`, `salaryRange.{min,max,currency,interval}`, `hostedUrl`, `applyUrl`, `descriptionPlain`, `lists[]`, `additionalPlain`.
- **No company name anywhere in the JSON.** Get it from `<title>` of `https://jobs.lever.co/{slug}` (verified for 18 slugs).
- No `updatedAt`. No description-free mode, so full pulls are heavy; always send `Accept-Encoding: gzip`.

### 1.3 Ashby

```
GET  https://api.ashbyhq.com/posting-api/job-board/{slug}[?includeCompensation=true]
HEAD https://api.ashbyhq.com/posting-api/job-board/{slug}            existence probe (200 / 404), about 0.1 s
POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiOrganizationFromHostedJobsPageName
POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams
```

- REST returns `{apiVersion, jobs[]}`, no pagination, always with full descriptions (saronic: 204 jobs, 3.2 MB raw, 340 KB gzipped).
- `cache-control: public, max-age=60`, served through Cloudflare, `etag` present, no rate-limit headers.
- Unknown slug: 404 `Not Found`. Known org with nothing open: 200 with `jobs: []`. Slugs are case-insensitive.
- Fields (per `jobs[]`): `id`, `title`, `department`, `team`, `employmentType`, `location`, `secondaryLocations[]`,
  `address.postalAddress.{addressLocality,addressRegion,addressCountry}`, `publishedAt`, `isListed`, `isRemote`,
  `workplaceType`, `jobUrl`, `applyUrl`, `descriptionPlain`, `compensation.{compensationTierSummary,summaryComponents[]}`.
- **Org identity (use this on every Ashby slug):**
  ```
  {"operationName":"ApiOrganizationFromHostedJobsPageName",
   "variables":{"organizationHostedJobsPageName":"{slug}"},
   "query":"query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!) { organization: organizationFromHostedJobsPageName(organizationHostedJobsPageName: $organizationHostedJobsPageName) { name publicWebsite customJobsPageUrl hostedJobsPageSlug } }"}
  ```
  Returns e.g. `{"name":"Radiant","publicWebsite":"https://radiant.co/"}`. 74 of 74 calls returned an organization tonight
  (one, `helsing`, an empty board, had a UUID as its `name`).
- **Light listing for cheap daily diffs:** `ApiJobBoardWithTeams` with selection
  `teams { id name parentTeamId } jobPostings { id title teamId locationId locationName workplaceType employmentType secondaryLocations { locationId locationName } compensationTierSummary }`
  returned 10 postings in 3.5 KB for `valstad`. It has no `publishedAt`, so use it for presence diffs and the REST call for dates.

### 1.4 Other ATS feeds that returned JSON tonight

| ATS | URL template | Date field | Notes |
|---|---|---|---|
| Workable | `GET https://apply.workable.com/api/v1/widget/accounts/{slug}` | `jobs[].published_on`, `created_at` (YYYY-MM-DD) | Also `name`, `description`, `jobs[].{title,department,city,country,telecommuting,experience,function,industry,url}`. `GET /api/v1/accounts/{slug}` gives `name`, `url` (website). `POST /api/v3/accounts/{slug}/jobs` with `{"query":""}` gives `{total, results[]}`. **Rate-limited, see section 9** |
| Rippling | `GET https://ats.rippling.com/api/v2/board/{slug}/jobs?page=0&pageSize=50` | none in list; `createdOn` in `GET .../jobs/{id}` | `{items[], page, pageSize, totalItems, totalPages}`; item has `name`, `department.name`, `locations[]`, `url`. Detail has `companyName`, `payRangeDetails`, `description` |
| Gem | `GET https://api.gem.com/job_board/v0/{slug}/job_posts/` | `first_published_at`, `created_at`, `updated_at` | Greenhouse-like array: `title`, `departments[]`, `offices[]`, `location`, `content_plain`, `absolute_url` |
| Breezy | `GET https://{slug}.breezy.hr/json` | `published_date` | `name`, `department`, `location`, `type`, `salary`, `company.name` |
| Dover | `GET https://app.dover.com/api/v1/careers-page-slug/{slug}` then `GET https://app.dover.com/api/v1/careers-page/{id}/jobs` | none | First call gives `id`, `name`, **`primary_domain`**, `careers_page_info`; second gives `{count, results[{title, locations[], workplace_type}]}` |
| BambooHR | `GET https://{slug}.bamboohr.com/careers/list` | none | `{meta.totalCount, result[{jobOpeningName, departmentLabel, location, isRemote}]}` |
| Recruitee | `GET https://{slug}.recruitee.com/api/offers/` | `created_at`, `published_at`, `updated_at` | `offers[]` with `title`, `department`, `city`, `country`, `company_name` |
| Teamtailor | `GET https://{slug}.teamtailor.com/jobs.rss` (also `/jobs.json`, JSON Feed) | `<pubDate>` | Regional hosts exist (`{slug}.na.teamtailor.com`) |
| SmartRecruiters | `GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0` | `releasedDate` | `{totalFound, content[]}`; mostly large corporates, low value for early stage |

Did not work or not worth it: JazzHR (`{slug}.applytojob.com/apply` is HTML only, links are scrapeable; the `/apply/jobs/feed` path 302s),
Personio XML (307), Rippling without the exact hyphenated slug (`boomsupersonic` 404, `boom-supersonic` 200),
`jobs.gem.com/api/public/...` (302 to login; use `api.gem.com`), Wayback CDX for backfill (empty result, then "Temporarily Offline").

### 1.5 Which APIs expose a posted date

| Source | Posted date | Updated date | Survives ATS migration? |
|---|---|---|---|
| Greenhouse | `first_published` | `updated_at` | No evidence either way; a brand-new board (General Galactic, 17 roles) had every date equal to 2026-10-01 |
| Lever | `createdAt` (ms) | none | n/a |
| Ashby | `publishedAt` | none | **No.** Rainmaker's 25 roles all read 2026-10-01 on Ashby; same 25 roles on Lever `make-rain` read 2026-04-13 to 2026-10-01 |
| Workable / Gem / Breezy / Recruitee / Teamtailor | yes (see 1.4) | Gem, Recruitee only | untested |
| Rippling | detail call only (`createdOn`) | none | untested |
| Dover / BambooHR | none | none | must snapshot |
| YC company jobs page | relative string (`"5 months"`) | `lastActive` relative | n/a |

---

## 2. Slug sweep on thesis companies (what hit)

Method: 241 thesis companies (robotics, drones/autonomy/defense, energy, semis, space), 489 candidate slugs
(name with spaces removed, hyphenated name, plus hand-written variants), probed on Greenhouse, Lever, Ashby and Workable
with 0.35 s between calls. Every 200 was then identity-checked.

| ATS | 200 | 404 | other | Verified live boards (>0 roles) |
|---|---:|---:|---|---:|
| Greenhouse | 63 | 424 | 1 timeout | 58 |
| Ashby | 74 | 414 | 0 | 59, plus 5 found through careers pages = 64 |
| Lever | 25 | 457 | 6 timeouts | 15 (8 of the 200s were empty arrays, 2 were other companies), plus `make-rain` from a careers page = 16 |
| Workable | 17 | 59 | **411 x 429**, 1 timeout | 1 (`agility`); 16 of the 17 were zero-job squatted or parked names such as `1x` -> name "Workable" |

Slug conventions among the 132 verified live boards found by guessing:

| ATS | name, no spaces (`valaratomics`) | hyphenated (`charge-robotics`) | other |
|---|---:|---:|---|
| Greenhouse | 49 | 0 | 9: `figureai`, `andurilindustries`, `nerostechnologies`, `flyzipline`, `vardaspace`, `diracinc`, `silananotechnologies`, `antora`, `etchedai` |
| Ashby | 36 | 17 | 6: `hadrian-automation`, `cobot`, `runetech`, `siftstack`, `sunday`, `fuse` (careers pages added `radiant-industries`, `apex-technology-inc`, `9-mothers`) |
| Lever | 12 | 2 (`field-ai`, `last-energy`) | 1: `merlinlabs` (careers page added `make-rain` for Rainmaker) |

Rules of thumb that follow: try `{name}` with spaces removed first on all three; on Ashby also try the hyphenated form;
then `{name}ai`, `{name}inc`, `{name}technologies`, `{name}space`, `{name}industries`, `fly{name}`, the legal name.
The original examples in the brief resolved as: `figureai` (GH), `skydio` (Ashby), `shieldai` (Lever), `saronic` (Ashby),
`andurilindustries` (GH), `hadrian-automation` (Ashby), `physicalintelligence` (Ashby), `1x` (Ashby), `apptronik` (GH),
`radiant-industries` (Ashby), `valaratomics` (GH), `flyzipline` (GH).

### 2.1 Verified live boards (identity checked), values as of 2026-10-01

`new 14d / 30d` = open roles whose posted date falls in the window. `prev 30d` = posted 31 to 60 days ago.
`hw share` = share of roles classified hardware, controls/embedded or manufacturing by the title regexes in section 6.

| Company (board name) | ATS | slug | open | new 14d | new 30d | prev 30d | hw share | newest | note |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| 1X | ashby | `1x` | 92 | 13 | 28 | 17 | 0.65 | 2026-09-29 |  |
| 9 Mothers | ashby | `9-mothers` | 7 | 2 | 3 | 0 | 0.43 | 2026-10-01 |  |
| Allen Control Systems | ashby | `allen-control-systems` | 43 | 5 | 23 | 11 | 0.49 | 2026-09-30 |  |
| Antares | ashby | `antares` | 86 | 11 | 43 | 24 | 0.79 | 2026-10-01 |  |
| Apex | ashby | `apex-technology-inc` | 133 | 57 | 82 | 19 | 0.64 | 2026-10-01 |  |
| Apolink Communcations | ashby | `apolink` | 4 | 2 | 2 | 0 | 0.0 | 2026-09-19 |  |
| Astro Mechanica | ashby | `astro-mechanica` | 38 | 0 | 21 | 2 | 0.82 | 2026-09-15 |  |
| Aurelius Systems | ashby | `aureliussystems` | 11 | 1 | 1 | 3 | 0.45 | 2026-09-29 |  |
| Base Power Company | ashby | `base-power` | 194 | 17 | 58 | 29 | 0.43 | 2026-10-01 |  |
| Beacon AI | ashby | `beaconai` | 14 | 6 | 10 | 2 | 0.07 | 2026-09-30 |  |
| Bedrock Robotics Inc | ashby | `bedrock-robotics` | 42 | 4 | 16 | 7 | 0.31 | 2026-10-01 |  |
| Blue Energy | ashby | `blue-energy` | 19 | 2 | 4 | 3 | 0.05 | 2026-09-23 |  |
| BRINC | ashby | `brinc` | 31 | 3 | 10 | 4 | 0.42 | 2026-09-29 |  |
| Carbon Robotics | ashby | `carbon-robotics` | 18 | 1 | 4 | 2 | 0.39 | 2026-09-22 |  |
| Cerebras Systems | ashby | `cerebras` | 115 | 18 | 36 | 14 | 0.22 | 2026-09-30 |  |
| Charge Robotics | ashby | `charge-robotics` | 14 | 11 | 11 | 0 | 0.71 | 2026-09-28 |  |
| Cobot | ashby | `cobot` | 14 | 6 | 8 | 2 | 0.64 | 2026-10-01 |  |
| Crusoe | ashby | `crusoe` | 349 | 72 | 137 | 84 | 0.36 | 2026-10-01 |  |
| Dyna Robotics | ashby | `dyna-robotics` | 24 | 1 | 1 | 6 | 0.38 | 2026-09-23 |  |
| Etched | ashby | `etched` | 108 | 9 | 14 | 12 | 0.56 | 2026-10-01 |  |
| Extropic | ashby | `extropic` | 10 | 0 | 2 | 1 | 0.5 | 2026-09-08 |  |
| First Resonance | ashby | `first-resonance` | 14 | 4 | 5 | 2 | 0.07 | 2026-10-01 |  |
| Form Energy, Inc | ashby | `formenergy` | 203 | 22 | 62 | 41 | 0.79 | 2026-10-01 |  |
| Fractile | ashby | `fractile` | 37 | 0 | 1 | 2 | 0.73 | 2026-09-07 |  |
| Fuse | ashby | `fuse` | 37 | 0 | 0 | 24 | 0.35 | 2026-08-19 |  |
| Gecko Robotics | ashby | `gecko-robotics` | 23 | 2 | 7 | 7 | 0.3 | 2026-09-30 |  |
| Generalist | ashby | `generalist` | 23 | 0 | 7 | 2 | 0.17 | 2026-09-09 |  |
| Hadrian Automation | ashby | `hadrian-automation` | 142 | 23 | 47 | 32 | 0.56 | 2026-10-01 |  |
| HavocAI | ashby | `havocai` | 26 | 4 | 14 | 1 | 0.08 | 2026-09-24 |  |
| Helion | ashby | `helion` | 69 | 24 | 28 | 7 | 0.81 | 2026-10-01 |  |
| Heron Power | ashby | `heron-power` | 39 | 13 | 16 | 6 | 0.62 | 2026-09-30 |  |
| Inertia | ashby | `inertia` | 7 | 0 | 1 | 2 | 0.57 | 2026-09-03 |  |
| Lydian | ashby | `lydian` | 6 | 0 | 3 | 1 | 0.83 | 2026-09-17 |  |
| Mariana Minerals | ashby | `marianaminerals` | 73 | 14 | 37 | 12 | 0.34 | 2026-10-01 |  |
| MatX | ashby | `matx` | 44 | 11 | 20 | 5 | 0.64 | 2026-10-01 |  |
| Monumental | ashby | `monumental` | 21 | 3 | 5 | 2 | 0.38 | 2026-09-24 |  |
| Normal Computing Corporation | ashby | `normalcomputing` | 18 | 2 | 4 | 6 | 0.44 | 2026-09-30 |  |
| Northwood Space | ashby | `northwoodspace` | 73 | 25 | 33 | 27 | 0.51 | 2026-10-01 |  |
| Periodic Labs | ashby | `periodic-labs` | 32 | 3 | 8 | 8 | 0.28 | 2026-10-01 |  |
| Physical Intelligence | ashby | `physicalintelligence` | 35 | 1 | 6 | 11 | 0.51 | 2026-09-24 |  |
| Picogrid | ashby | `picogrid` | 19 | 1 | 1 | 1 | 0.21 | 2026-09-25 |  |
| Proxima Fusion | ashby | `proxima-fusion` | 47 | 11 | 16 | 8 | 0.64 | 2026-10-01 |  |
| Quilter | ashby | `quilter` | 5 | 0 | 0 | 0 | 0.0 | 2026-05-07 |  |
| Radiant | ashby | `radiant-industries` | 94 | 5 | 30 | 25 | 0.78 | 2026-10-01 |  |
| Rainmaker Technology Corporation | ashby | `rainmaker` | 25 | 25 | 25 | 0 | 0.32 | 2026-10-01 |  |
| Reflect Orbital | ashby | `reflect-orbital` | 22 | 5 | 9 | 9 | 0.41 | 2026-10-01 |  |
| Reflex Robotics | ashby | `reflexrobotics` | 7 | 0 | 4 | 0 | 0.71 | 2026-09-14 |  |
| REGENT | ashby | `regent` | 31 | 11 | 24 | 7 | 0.58 | 2026-09-30 |  |
| Reliable Robotics Corporation | ashby | `reliable-robotics` | 56 | 7 | 12 | 5 | 0.77 | 2026-10-01 |  |
| RobCo | ashby | `robco` | 38 | 3 | 8 | 3 | 0.21 | 2026-09-25 |  |
| Rune Technologies | ashby | `runetech` | 17 | 0 | 5 | 4 | 0.0 | 2026-09-16 |  |
| Saronic Technologies | ashby | `saronic` | 204 | 16 | 47 | 45 | 0.52 | 2026-09-30 |  |
| Scout AI | ashby | `scout-ai` | 29 | 0 | 1 | 5 | 0.52 | 2026-09-02 |  |
| Serve Robotics | ashby | `serverobotics` | 28 | 6 | 12 | 4 | 0.14 | 2026-09-30 |  |
| Sift Stack, Inc. | ashby | `siftstack` | 39 | 8 | 14 | 5 | 0.0 | 2026-09-29 |  |
| Skydio | ashby | `skydio` | 138 | 12 | 38 | 38 | 0.36 | 2026-10-01 |  |
| Standard Bots | ashby | `standardbots` | 38 | 10 | 15 | 11 | 0.37 | 2026-09-29 |  |
| Substrate | ashby | `substrate` | 7 | 0 | 0 | 0 | 0.43 | 2026-02-13 |  |
| Sunday Robotics | ashby | `sunday` | 24 | 2 | 2 | 0 | 0.46 | 2026-09-23 |  |
| Swarm Aero | ashby | `swarmaero` | 37 | 13 | 30 | 5 | 0.38 | 2026-10-01 |  |
| The Bot Company | ashby | `thebotcompany` | 7 | 0 | 0 | 0 | 0.14 | 2026-02-25 |  |
| Turion Space | ashby | `turion-space` | 1 | 1 | 1 | 0 | 0.0 | 2026-10-01 |  |
| Valinor Enterprises, Inc. | ashby | `valinor` | 7 | 1 | 2 | 1 | 0.29 | 2026-09-29 |  |
| Valstad Shipworks | ashby | `valstad` | 10 | 4 | 4 | 6 | 0.5 | 2026-09-22 |  |
| Agility Robotics | greenhouse | `agilityrobotics` | 77 | 17 | 28 | 21 | 0.35 | 2026-10-01 |  |
| Albedo | greenhouse | `albedo` | 6 | 0 | 1 | 4 | 0.33 | 2026-09-16 |  |
| Allen Control Systems | greenhouse | `allencontrolsystems` | 68 | 0 | 0 | 0 | 0.71 | 2026-03-24 | stale: newest 2026-03-24; live board is Ashby allen-control-systems |
| Anduril Industries | greenhouse | `andurilindustries` | 2421 | 478 | 870 | 431 | 0.57 | 2026-10-01 |  |
| Anno.ai | greenhouse | `annoai` | 4 | 0 | 0 | 0 | 0.5 | 2026-06-16 | low activity: newest 2026-06-16 |
| Antora Energy | greenhouse | `antora` | 41 | 8 | 12 | 11 | 0.44 | 2026-09-30 |  |
| Apptronik | greenhouse | `apptronik` | 76 | 6 | 11 | 23 | 0.59 | 2026-09-29 |  |
| Arbor Energy | greenhouse | `arborenergy` | 18 | 3 | 4 | 1 | 0.39 | 2026-09-28 |  |
| Astranis | greenhouse | `astranis` | 174 | 91 | 101 | 16 | 0.77 | 2026-09-30 |  |
| Carbon Robotics | greenhouse | `carbonrobotics` | 18 | 1 | 4 | 2 | 0.39 | 2026-09-22 |  |
| CHAOS Industries | greenhouse | `chaosindustries` | 122 | 19 | 30 | 31 | 0.44 | 2026-10-01 |  |
| Diligent Robotics | greenhouse | `diligentrobotics` | 9 | 3 | 4 | 2 | 0.44 | 2026-10-01 |  |
| Dirac, Inc. | greenhouse | `diracinc` | 7 | 2 | 2 | 0 | 0.29 | 2026-09-24 |  |
| Divergent | greenhouse | `divergent` | 119 | 30 | 57 | 20 | 0.72 | 2026-10-01 |  |
| Efficient Computer | greenhouse | `efficientcomputer` | 19 | 3 | 5 | 7 | 0.53 | 2026-10-01 |  |
| Epirus | greenhouse | `epirus` | 41 | 3 | 8 | 5 | 0.59 | 2026-09-25 |  |
| Etched | greenhouse | `etchedai` | 25 | 0 | 0 | 0 | 0.6 | 2025-02-06 | stale: newest first_published 2025-02-06; live board is Ashby etched |
| Fervo Energy | greenhouse | `fervoenergy` | 4 | 4 | 4 | 0 | 0.25 | 2026-09-29 |  |
| Figure | greenhouse | `figureai` | 98 | 5 | 9 | 22 | 0.41 | 2026-10-01 |  |
| Zipline | greenhouse | `flyzipline` | 350 | 28 | 65 | 145 | 0.43 | 2026-09-30 |  |
| Formic | greenhouse | `formic` | 41 | 0 | 12 | 10 | 0.37 | 2026-09-17 |  |
| Fractile | greenhouse | `fractile` | 37 | 0 | 1 | 2 | 0.73 | 2026-09-07 |  |
| General Galactic | greenhouse | `generalgalactic` | 17 | 17 | 17 | 0 | 0.76 | 2026-10-01 |  |
| General Matter | greenhouse | `generalmatter` | 124 | 2 | 19 | 42 | 0.59 | 2026-09-30 |  |
| Helsing | greenhouse | `helsing` | 166 | 22 | 45 | 37 | 0.42 | 2026-10-01 |  |
| Hubble Network | greenhouse | `hubblenetwork` | 18 | 7 | 11 | 2 | 0.56 | 2026-10-01 |  |
| Inversion | greenhouse | `inversionspace` | 81 | 6 | 12 | 14 | 0.78 | 2026-09-30 |  |
| Kairos Power | greenhouse | `kairospower` | 34 | 12 | 16 | 3 | 0.65 | 2026-09-30 |  |
| KoBold Metals | greenhouse | `koboldmetals` | 32 | 0 | 2 | 5 | 0.12 | 2026-09-17 |  |
| Lightmatter | greenhouse | `lightmatter` | 68 | 5 | 17 | 13 | 0.87 | 2026-09-30 |  |
| Mach Industries | greenhouse | `machindustries` | 133 | 22 | 79 | 54 | 0.76 | 2026-10-01 |  |
| Muon Space | greenhouse | `muonspace` | 132 | 23 | 44 | 52 | 0.46 | 2026-09-30 |  |
| Neros Technologies | greenhouse | `nerostechnologies` | 105 | 19 | 35 | 26 | 0.5 | 2026-10-01 |  |
| Oklo | greenhouse | `oklo` | 76 | 17 | 36 | 14 | 0.41 | 2026-10-01 |  |
| Orbital Operations | greenhouse | `orbitaloperations` | 5 | 0 | 0 | 5 | 0.6 | 2026-08-31 |  |
| Pacific Fusion | greenhouse | `pacificfusion` | 28 | 0 | 6 | 7 | 0.89 | 2026-09-09 |  |
| Peak Energy | greenhouse | `peakenergy` | 38 | 4 | 10 | 15 | 0.53 | 2026-09-29 |  |
| Portal Space Systems | greenhouse | `portalspacesystems` | 17 | 4 | 5 | 6 | 0.53 | 2026-10-01 |  |
| Pronto | greenhouse | `pronto` | 24 | 5 | 5 | 4 | 0.42 | 2026-10-01 |  |
| Quaise Energy, Inc | greenhouse | `quaise` | 10 | 1 | 2 | 1 | 0.5 | 2026-09-18 |  |
| Re:Build Manufacturing | greenhouse | `rebuildmanufacturing` | 129 | 18 | 38 | 30 | 0.67 | 2026-10-01 |  |
| Redwood Materials | greenhouse | `redwoodmaterials` | 136 | 7 | 20 | 28 | 0.58 | 2026-09-29 |  |
| Relativity Space | greenhouse | `relativity` | 344 | 53 | 104 | 59 | 0.75 | 2026-10-01 |  |
| Rocket Lab Corporation | greenhouse | `rocketlab` | 558 | 91 | 264 | 136 | 0.64 | 2026-10-01 |  |
| Salient Motion | greenhouse | `salientmotion` | 18 | 2 | 3 | 5 | 0.83 | 2026-09-29 |  |
| Scout AI | greenhouse | `scoutai` | 29 | 0 | 1 | 5 | 0.52 | 2026-09-02 |  |
| Senra Systems | greenhouse | `senrasystems` | 28 | 3 | 6 | 1 | 0.64 | 2026-09-28 |  |
| Sila | greenhouse | `silananotechnologies` | 34 | 5 | 14 | 7 | 0.62 | 2026-09-30 |  |
| Skyryse | greenhouse | `skyryse` | 19 | 2 | 6 | 3 | 0.63 | 2026-10-01 |  |
| Tenstorrent | greenhouse | `tenstorrent` | 127 | 20 | 39 | 18 | 0.43 | 2026-10-01 |  |
| The Nuclear Company | greenhouse | `thenuclearcompany` | 75 | 8 | 19 | 25 | 0.35 | 2026-10-01 |  |
| Unusual Machines | greenhouse | `unusualmachines` | 24 | 6 | 16 | 3 | 0.58 | 2026-10-01 |  |
| Ursa Major | greenhouse | `ursamajor` | 108 | 17 | 30 | 36 | 0.6 | 2026-09-30 |  |
| Valar Atomics | greenhouse | `valaratomics` | 94 | 9 | 32 | 33 | 0.66 | 2026-10-01 |  |
| Vannevar | greenhouse | `vannevarlabs` | 29 | 4 | 9 | 4 | 0.03 | 2026-09-30 |  |
| Varda Space Industries | greenhouse | `vardaspace` | 102 | 40 | 55 | 18 | 0.72 | 2026-10-01 |  |
| Vast | greenhouse | `vast` | 202 | 26 | 66 | 39 | 0.63 | 2026-10-01 |  |
| Zone 5 Technologies | greenhouse | `zone5technologies` | 74 | 10 | 12 | 11 | 0.47 | 2026-10-01 |  |
| ANYbotics | lever | `anybotics` | 20 | 2 | 6 | 2 | 0.4 | 2026-09-29 |  |
| Bright Machines | lever | `brightmachines` | 18 | 4 | 4 | 3 | 0.61 | 2026-10-01 |  |
| CX2 | lever | `cx2` | 5 | 3 | 3 | 1 | 0.4 | 2026-09-30 |  |
| Dexterity | lever | `dexterity` | 21 | 0 | 0 | 5 | 0.05 | 2026-08-20 |  |
| Epsilon3 | lever | `epsilon3` | 4 | 0 | 0 | 1 | 0.0 | 2026-08-03 |  |
| Exowatt | lever | `exowatt` | 18 | 5 | 7 | 0 | 0.89 | 2026-09-28 |  |
| FieldAI | lever | `field-ai` | 97 | 9 | 23 | 9 | 0.18 | 2026-10-01 |  |
| Hermeus | lever | `hermeus` | 90 | 10 | 25 | 17 | 0.7 | 2026-10-01 |  |
| Last Energy | lever | `last-energy` | 34 | 2 | 2 | 4 | 0.47 | 2026-10-01 |  |
| Rainmaker Technology Corporation | lever | `make-rain` | 25 | 4 | 10 | 3 | 0.32 | 2026-10-01 |  |
| Merlin Labs | lever | `merlinlabs` | 23 | 2 | 5 | 3 | 0.22 | 2026-09-18 |  |
| Pickle Robot Company | lever | `picklerobot` | 14 | 2 | 3 | 3 | 0.43 | 2026-09-24 |  |
| Pyka | lever | `pyka` | 21 | 2 | 5 | 12 | 0.57 | 2026-09-28 |  |
| Shield AI | lever | `shieldai` | 582 | 109 | 233 | 92 | 0.46 | 2026-09-30 |  |
| Tutor Intelligence | lever | `tutorintelligence` | 41 | 7 | 14 | 5 | 0.29 | 2026-09-29 |  |
| Xcimer Energy | lever | `xcimer` | 55 | 23 | 30 | 9 | 0.56 | 2026-10-01 |  |

Notes on the table:
- **Rainmaker (Ashby `rainmaker`)** shows 25/25 new because it moved from Lever (`make-rain`) on 2026-10-01. Use the Lever dates.
- **General Galactic (Greenhouse `generalgalactic`)**: 17 roles, all `first_published` 2026-10-01. No older board found, so this reads as a board birth.
- **Astro Mechanica**: 14 of its 21 "new in 30d" roles share one date (2026-09-10), which looks like a bulk publish. Swarm Aero's 30 are spread over more than a dozen days (organic).
- **Duplicates across ATS**: Carbon Robotics (GH 18 and Ashby 18), Fractile (37 and 37), Scout AI (29 and 29) are live on both. Dedupe by company, keep the board with the newest posting.
- Stale boards left behind after a migration: Greenhouse `etchedai` (newest 2025-02-06, live board is Ashby `etched` with 108 roles) and Greenhouse `allencontrolsystems` (newest 2026-03-24, live is Ashby `allen-control-systems`).

### 2.2 Slug collisions: HTTP 200 for the wrong company

| ATS | slug | HTTP | open roles | What the response shows |
|---|---|---|---:|---|
| greenhouse | `figure` | 200 | 18 | board name 'Figure Lending' (self-described financial technology company), not Figure AI (= `figureai`) |
| greenhouse | `archer` | 200 | 1 | board name 'Archer Veterinary Clinic', newest posting 2019 |
| greenhouse | `reflex` | 200 | 3 | Austin TX 'Reflex' (3 roles); Reflex Robotics is Ashby reflexrobotics |
| greenhouse | `regent` | 200 | 10 | Beverly Hills M&A / tax / litigation roles; REGENT (regentcraft.com) is Ashby regent |
| greenhouse | `thea` | 200 | 6 | board name 'Théa', titles like 'Assistant General Manager'; not verified as Thea Energy |
| lever | `mimic` | 200 | 1 | enterprise server-state software per job description; not the robotics company |
| lever | `sila` | 200 | 208 | hosted-page title 'Sila Services' (HVAC/plumbing roles); Sila (batteries) is Greenhouse silananotechnologies |
| ashby | `arbor` | 200 | 1 | org website joinarbor.com; Arbor Energy is Greenhouse arborenergy |
| ashby | `atomic` | 200 | 8 | org website atomic.vc; Atomic Industries is Ashby atomicindustries (0 roles) |
| ashby | `boom` | 200 | 11 | org website boompay.app; Boom Supersonic is Rippling boom-supersonic |
| ashby | `foundation` | 200 | 4 | org website buildwithfoundation.com; not verified as the humanoid company |
| ashby | `impulse` | 200 | 2 | org name 'Impulse Labs' (impulselabs.com); not Impulse Space |
| ashby | `radiant` | 200 | 23 | org website radiant.co, Gloucestershire/London infrastructure roles; Radiant (nuclear) is Ashby radiant-industries |
| ashby | `salient` | 200 | 17 | org website trysalient.com; Salient Motion is Greenhouse salientmotion |
| ashby | `sanctuary` | 200 | 6 | org website sanctuary.co, construction roles in Texas/Delhi; not Sanctuary AI |
| ashby | `scout` | 200 | 0 | org website onscout.com, empty board; Scout AI is scout-ai / Greenhouse scoutai |
| ashby | `sift` | 200 | 7 | org website sift.com; Sift Stack is Ashby siftstack |
| ashby | `zeno` | 200 | 2 | 2 legal/partnerships roles in Germany; not Zeno Power |

---

## 3. From domain to ATS + slug

Tested on 36 domains (12 well-known, 24 small). Hit rate **22 of 36** (7 of 12 well-known, 15 of 24 small).

Algorithm (reference implementation logic, in order, stop at first hit):

1. `GET https://{domain}/careers`, `/jobs`, `/` (follow redirects). Regex the final URL and body after unescaping `\/`, `/`, `%2F`.
2. Follow up to 4 same-page links whose href matches `career|job|open-roles|openings|positions|join|work-with-us|hiring`. External hrefs are scanned directly.
3. Fetch up to 10 same-origin `<script src>` bundles from the careers page (skip polyfill/framework/webpack/analytics) and regex them.
4. Fallback: slug guessing against the APIs (section 2), then identity verification (section 7).

Patterns (capture group 1 is the slug):

```
greenhouse   (?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)
greenhouse   boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)
lever        jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)        |  api(?:\.eu)?\.lever\.co/v0/postings/([A-Za-z0-9_.-]+)
ashby        jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)             |  api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)
workable     apply\.workable\.com/([A-Za-z0-9_-]+)             |  https?://([A-Za-z0-9-]+)\.workable\.com
rippling     ats\.rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)/jobs
gem          jobs\.gem\.com/([A-Za-z0-9_-]+)
dover        app\.dover\.com/(?:jobs/|apply/)?([A-Za-z0-9_-]+)
breezy       https?://([A-Za-z0-9-]+)\.breezy\.hr
bamboohr     https?://([A-Za-z0-9-]+)\.bamboohr\.com
jazzhr       https?://([A-Za-z0-9-]+)\.applytojob\.com
recruitee / teamtailor / pinpoint / personio   https?://([A-Za-z0-9-]+)\.(recruitee\.com|teamtailor\.com|pinpointhq\.com|jobs\.personio\.(de|com))
yc           ycombinator\.com/companies/([A-Za-z0-9_-]+)/jobs
```
Drop captures in `{embed, js, api, www, apply, jobs, job_board, v1, assets, static, cdn, app, careers}`.

What each step found:

| Step | Hits | Examples |
|---|---:|---|
| 1. HTML of `/careers`, `/jobs`, `/` | 16 | `shield.ai` -> lever `shieldai`; `saronic.com` -> ashby `saronic`; `hadrian.co` -> ashby `hadrian-automation`; `1x.tech` -> ashby `1x`; `chargerobotics.com` -> ashby `charge-robotics`; `apexspace.com` -> ashby `apex-technology-inc`; `9mothers.com` -> ashby `9-mothers`; `beaconai.co` -> ashby `beaconai`; `springcraft.ai` and `ko-br.com` -> dover; `silkline.ai` -> gem `silkline` |
| 2. one hop to a job-detail or listings link | 4 | `apptronik.com/careers/job-listings` -> greenhouse `apptronik`; `radiantnuclear.com/careers/{uuid}` -> ashby `radiant-industries`; `monumental.co`, `valstad.com` -> ashby |
| 3. JS bundle scan | 2 | `valaratomics.com` -> greenhouse `valaratomics`; `rainmaker.com` -> lever `make-rain` (stale: they moved to Ashby the same day) |
| miss | 14 | `figure.ai`, `anduril.com`, `zipline.com`, `skydio.com` (server-side proxy, no ATS host in HTML or bundles); `physicalintelligence.company` (429 to every path); `generalgalactic.com` (connection failed); `cascadespace.com`, `strobepower.com`, `whistlerobotics.com`, `lumenresearch.co`, `freeform.co`, `edgerun.com`, `vernius.systems`, `lambdarobotics.ai` (no ATS reference found; several of these take applications by email or a form) |

Useful tells when there is no ATS hostname:
- `?gh_jid=` with a numeric id means Greenhouse. **`gh_jid=` with a UUID means the company moved to Ashby and kept the old parameter** (skydio.com job links look like `/jobs/{uuid}/?gh_jid={uuid}`; Ashby `skydio` is the live board).
- `?ashby_jid={uuid}` in a URL (seen in HN job posts for `jiga.io`, `artie.com`) means Ashby with an embedded board.
- `grnhse_iframe` / `grnhse_app` element ids mean a Greenhouse embed; the slug is in the `for=` parameter of the embed script.
- A miss on a sub-10-person company is itself informative: **no ATS yet**. First appearance of any ATS board is a feature (section 6).

---

## 4. Hacker News "Ask HN: Who is hiring?" via Algolia

Base `https://hn.algolia.com/api/v1`, no key. (Core Algolia mechanics are also covered in `launches.md`; this section is the hiring-thread recipe.)

**Find the threads** (the bot account `whoishiring` posts three stories on the first weekday of each month):

```
GET /search_by_date?tags=story,author_whoishiring&hitsPerPage=6
```
Returned `nbHits: 512`. Filter `title` starting with `Ask HN: Who is hiring?`; ignore "Who wants to be hired?" and "Freelancer?".
Tonight: October 2026 = `49922569` (created 2026-10-01T15:02:07Z, `num_comments` 142), September = `49522897` (395), August = `49156683` (378).

**Pull top-level comments only** (one call, no tree walk):

```
GET /search_by_date?tags=comment,story_{id}&numericFilters=parent_id={id}&hitsPerPage=1000&page=0
```
- October returned `nbHits: 105` in one page (332 KB), September 254, August 232. `hitsPerPage=1000` was honoured; `nbPages: 1`.
- Alternative: `GET /items/{id}` returns the whole tree (`children[]` with `text`, `author`, `created_at`, nested replies), 179 KB for October.
- Fields: `objectID` (comment id, permalink `https://news.ycombinator.com/item?id={objectID}`), `author`, `created_at`, `created_at_i`,
  `comment_text` (HTML, entity-escaped, `<p>` paragraph breaks), `parent_id`, `story_id`, `story_title`.

**Parse.** First paragraph is the header, pipe-delimited by convention: `Company | Role(s) | Location | ONSITE/REMOTE | salary | url`.
Split on `|`; field 0 is the company (strip a trailing parenthetical such as `(YC S23)` or `(https://...)`).
Parse rate tonight: 97 of 105 (Oct), 239 of 254 (Sep), 212 of 232 (Aug). Field order after the company is not reliable
(one post put the city first), so take the company from field 0 only when it is not a location, and take the domain from the first `href` that is not an ATS or video host.

**Thesis keyword counts (top-level posts mentioning at least one keyword group)**

| Thread | top-level | loose regex | strict regex, >=1 group | strict, >=2 groups | per-group (strict) |
|---|---:|---:|---:|---:|---|
| October 2026 (9 h old) | 105 | 22 | **15** | 7 | robotics 9, hardware_eng 5, drones 2, defense 2, semis 2, energy 1, space 1, manufacturing 1 |
| September 2026 | 254 | 67 | **37** | 14 | robotics 14, hardware_eng 12, defense 8, space 7, energy 6, manufacturing 5, drones 4, semis 2 |
| August 2026 | 232 | 54 | **33** | 12 | hardware_eng 13, robotics 10, energy 7, space 6, drones 3, defense 3, manufacturing 3, semis 3 |

- Loose regex = bare words like `autonomy`, `space`, `grid`, `silicon`, `factory`, `clearance`. It matched "Silicon Valley", "Factory" (the company), "space" in any sense. Do not use it.
- Strict regex (strip `<a>` tags first so company domains do not match), per group:
  - robotics: `robots?|robotics?|humanoids?|teleoperat\w*|manipulators?`
  - drones_autonomy: `drones?|uavs?|uas|suas|evtol|unmanned|autonomous (vehicles?|vessels?|aircraft|flight|ships?|boats?|trucks?)|counter-?drone|avionics|flight (software|controls?)`
  - defense: `dod|department of (defense|war)|defen[sc]e (tech|missions?|programs?|contractor|industr\w+|customers?)|national security|warfighters?|itar|missiles?|interceptors?|munitions?`
  - energy: `nuclear|fusion|fission|microreactors?|smr|batter(y|ies)|power grid|the grid|grid[- ]scale|energy storage|solar farms?|geothermal|power plants?|electricity (demand|markets?)|utilit(y|ies)[- ]scale|transformers?`
  - manufacturing: `cnc|machining|machine shops?|factory floor|factories|microfactor\w+|shipbuilding|shipyards?|additive manufacturing|3d print\w*|manufacturing (engineer\w*|systems?|work|process\w*|lines?)|industrial (robots?|automation)|welding`
  - semis: `semiconductors?|asics?|fpgas?|rtl|chip design|tape-?outs?|lithography|photonics?|wafer\w*|foundry`
  - space: `spacecraft|satellites?|rockets?|launch vehicles?|in[- ]orbit|orbital|aerospace|deep space|lunar|the moon|ground stations?|smallsat`
  - hardware_eng: `firmware|embedded (systems?|software|linux|engineer\w*)|mechanical engineer\w*|electrical engineer\w*|controls engineer\w*|gnc|pcbs?|mechatronic\w*|hardware engineer\w*|rf (engineer\w*|hardware)`
- Manual read of the 15 October strict matches: 8 are truly thesis-aligned (Brain Corp, Tangram Vision, Monumental, Charge Robotics,
  etc., ko-br, Skydio, Beacon AI). Precision 8/15 at >=1 group, 6/7 at >=2 groups. False positives were one-word incidental mentions
  ("robotics" in a customer list, "foundry", "semiconductor" as an insured industry). Use >=2 groups, or >=1 group plus an LLM read, as the gate.

**ATS links inside hiring posts** (link counts, three threads): ashbyhq.com 145, greenhouse.io 20, workable.com 16, lever.co 12,
gem.com 6, applytojob.com 5, rippling.com 3, dover.com 3, pinpointhq.com 3, wellfound.com 3, bamboohr.com 2,
recruitee.com 2, teamtailor.com 2, breezy.hr 1, myworkdayjobs.com 1. Feed these straight into section 1 endpoints.

**Novelty: is this the company's first hiring post?**
```
GET /search_by_date?tags=comment,author_{author}&hitsPerPage=100     then keep hits where story_title matches /Who is hiring/
GET /search_by_date?query="{Company}"&tags=comment&hitsPerPage=20    exact-phrase search across all comments
```
Measured: `geiman` (etc.) has 1 comment ever, the October 2026 post. `cal5k` (Valstad Shipworks) has one hiring post (September 2026) and `"Valstad"` returns 1 hit.
`justicz` (Charge Robotics) has 54 hiring-thread comments going back to June 2021, so that post is a repeat, not a new signal.

**YC job stories on HN:** `GET /search_by_date?tags=job&hitsPerPage=40` (17,439 total) and
`GET https://hacker-news.firebaseio.com/v0/jobstories.json` (31 ids). Titles follow `Name (YC S24) Is Hiring ...`.
Thesis hits in the last 40: Zettascale (YC S24) hiring ASIC/FPGA engineers (2026-09-17), Voltair (YC W26) hiring a flight test engineer (2026-08-15), 9 Mothers (YC P26) hiring in Austin (2026-09-03).

---

## 5. YC feeds for hiring

| Feed | URL | Result tonight |
|---|---|---|
| Open mirror of YC directory, hiring subset | `GET https://yc-oss.github.io/api/companies/hiring.json` | 200, 2.57 MB, **1,485 companies with `isHiring: true`**. `meta.json` `last_updated` 2026-10-01T03:12Z (daily) |
| YC company jobs page | `GET https://www.ycombinator.com/companies/{slug}/jobs` | 200. Parse `data-page="..."` (HTML-escaped JSON, Inertia). `props.company` has `name, website, team_size, batch_name, location, year_founded, linkedin_url, twitter_url, founders[{full_name,title,linkedin_url}]`; `props.jobPostings[]` has `title, location, type, role, roleSpecificType, salaryRange, equityRange, minExperience, createdAt, lastActive, url` |
| YC jobs by role | `GET https://www.ycombinator.com/jobs/role/{role}` | 200, 39 `jobPostings` in `data-page`. **Unknown role slugs silently fall back to software-engineer** (`/jobs/role/hardware-engineer` returned the Software Engineer page) |
| Work at a Startup public list | `GET https://www.workatastartup.com/jobs` with `Accept: text/html` | 200, `data-page` has `jobs[30]` and `totalJobsCount: 2882`. Fields `title, roleType, location, salary, companyName, companySlug, companyBatch, companyLastActiveAt`. Without `Accept: text/html` it returns **406**. `/companies` 302s to login; full search is behind an account |

Thesis filter on `hiring.json` (regex over `tags + industries + subindustry`:
`robot|hard ?tech|aerospace|drone|defen|energy|manufactur|semiconductor|space|satellite|nuclear|fusion|batter|hardware|industrial|autonomous|aviation|climate|mining|construction`):
**253 of 1,485** hiring companies; **90** of those are in 2025 or 2026 batches. Fields to keep: `name, slug, website, batch, team_size, one_liner, tags, industries, launched_at, url`.
Recent-batch companies often have empty `tags`, so also regex `one_liner` / `long_description`.

---

## 6. Features a scorer should compute

Normalise every source to one row per open role:
`{company_id, ats, slug, job_id, title, dept, location, posted_at, updated_at, url, first_seen_at, last_seen_at}`.
`first_seen_at` / `last_seen_at` come from our own daily snapshots and are the only trustworthy clock (section 8).

Classification used for the numbers in this card (regex on lowercased title, falling back to department; first match wins):

| Function | Pattern (abridged) |
|---|---|
| leadership | `chief|ceo|cto|coo|cfo|vp|vice president|head of|general manager|president|general counsel` |
| gtm | `sales|account executive|account manager|business development|capture|growth|marketing|partnerships?|customer success|solutions? (engineer|architect)|revenue|commercial|government (relations|affairs)|proposal|communications` |
| manufacturing | `manufactur\w*|production|assembl\w*|technician|machinist|cnc|weld\w*|fabricat\w*|quality|supply chain|procurement|buyer|planner|tooling|npi|harness|operator|inspector|mechanic` |
| ml_ai | `machine learning|ml|ai|research (scientist|engineer)|perception|computer vision|reinforcement|autonomy|slam|planning` |
| controls_embedded | `controls?|gnc|guidance|navigation|embedded|firmware|fpga|rtos|avionics|flight software|instrumentation|plc|automation` |
| hardware | `mechanical|electrical|hardware|rf|power electronics|battery|propulsion|thermal|structur\w*|aero\w*|design engineer|asic|rtl|analog|silicon|pcb|systems? engineer|test engineer|nuclear|reactor|plasma|magnet|materials?|process engineer` |
| software, ops_field, g_and_a, product_design | the usual |

Seniority from title: `exec` (chief/VP/head of), `director`, `manager`, `staff_plus`, `senior`, `intern_newgrad`, else `ic`.

| Feature | Definition | Example from tonight |
|---|---|---|
| `open_roles` | count of listed roles | Valstad 10, Charge Robotics 14, 9 Mothers 7 |
| `new_14d`, `new_30d` | roles with `posted_at` in window | Swarm Aero 13 / 30 |
| `hiring_accel_30d` | `(new_30d - prev_30d) / max(prev_30d, 1)` | Swarm Aero (30 vs 5) = 5.0; Northwood Space (33 vs 27) = 0.22 |
| `pct_new_30d` | `new_30d / open_roles` | Charge Robotics 0.79 |
| `net_adds_7d`, `closes_7d` | from snapshot diffs: ids appearing / disappearing | needs history; not available from any API |
| `role_half_life` | median days between `first_seen_at` and disappearance | needs history |
| `function_mix`, `hardware_share` | share by function; hardware + controls + manufacturing | Pacific Fusion 0.89, Lightmatter 0.87, Radiant 0.78, HavocAI 0.08 |
| `manufacturing_share` rising | prototype to production transition | Valstad: 5 of 10 roles manufacturing |
| `first_gtm_hire` | first-ever role classified gtm, or first with `head of|vp` + sales/BD/growth | Heron Power `Head of Sales`; Hubble Network `VP of Enterprise Sales`; Rune `Growth Director - U.S. Navy` |
| `first_exec_hire` / `head_of_count` | first `head of|vp|chief` posting, and count | Charge Robotics `Head of Manufacturing` + `Head of Engineering` (both 2026-09-28); Valstad `Head of Manufacturing & Industrialization`; CX2 `Head of Manufacturing` |
| `gov_affairs_hire` | title matches `government (relations|affairs)|policy|licensing|regulatory|capture` | Blue Energy `Head of Government Affairs`; Proxima Fusion `Head of Licensing & Regulation`; REGENT `Director, Government Relations - Defense` |
| `founding_role_count` | titles containing `founding` | ko-br: 3 of 4 Dover roles are "Founding ..." |
| `location_expansion` | locations present in roles posted in last 30 d and absent before | computed as `new_locations_30d`; watch for second site, first non-HQ state, first non-US |
| `n_locations` | distinct `location` strings | Valar Atomics 3, Shield AI 34 |
| `board_birth` | first day any ATS board exists for the company | General Galactic on Greenhouse, 2026-10-01, 17 roles |
| `ats_migration` | same company seen on a second ATS with all dates on one day | Rainmaker Lever -> Ashby 2026-10-01. Set `posted_at` reliability to low |
| `bulk_publish_flag` | >= 50% of `new_30d` roles share one calendar day | Astro Mechanica (14 of 21 on 2026-09-10) |
| `comp_band_max` | Greenhouse `metadata[Salary].max_value`, Lever `salaryRange.max`, Ashby `compensation`, HN header | Valar Atomics Automation and Controls Engineer 90,000 to 137,000 USD |
| `hn_first_hiring_post` | first month the company (author or name) appears in a hiring thread | etc. (Oct 2026), Valstad (Sep 2026) |
| `hn_hiring_streak` | number of the last 6 threads the company posted in | Charge Robotics: 4 of the last 6 |
| `team_size_vs_open_roles` | `open_roles / team_size` from YC or LinkedIn | 9 Mothers: 7 or 8 open roles vs team of 19 |
| `no_ats_yet` | domain resolves to no ATS and company hires by email | Lumen Labs (team of 2 hiring number 3), Whistle Robotics |

Composite for pre-consensus ranking: reward small absolute size with high acceleration, hardware-heavy mix, a first exec or first GTM hire, and board birth;
discount anything above about 150 open roles (already consensus) and anything flagged `ats_migration` or `bulk_publish_flag`.

---

## 7. Entity resolution

Primary key is the **registrable domain**. Each source yields:

| Source | Identity it gives | How to verify the board is the right company |
|---|---|---|
| Greenhouse | `jobs[].company_name`, `GET /v1/boards/{slug}` -> `name`, `content` | Compare name; if ambiguous, compare job locations and titles to the company (caught `figure` = Figure Lending, `archer` = a veterinary clinic) |
| Ashby | GraphQL `organization.name`, **`publicWebsite`** | Domain equality with the candidate company. Caught 11 wrong-company slugs tonight |
| Lever | nothing in JSON; `<title>` of `jobs.lever.co/{slug}` | Title match plus department/location sanity (caught `sila` = Sila Services) |
| Workable | `name`, and `GET /api/v1/accounts/{slug}` -> `url` | Domain equality; reject boards whose `name` is "Workable" or that have 0 jobs |
| Dover | `primary_domain` | Domain equality |
| Rippling | job detail `companyName` | Name match |
| Gem / Breezy / Recruitee | `company.name` / `company_name` | Name match |
| HN hiring post | company string (header field 0), first non-ATS `href` domain, `author` handle, any ATS link | ATS link in the post is the strongest join; else domain |
| YC | `slug`, `website`, `founders[].full_name` + `linkedin_url`, `batch` | Website domain |

Join order: ATS link in an HN/YC post -> `(ats, slug)`; `(ats, slug)` -> domain via Ashby `publicWebsite` / Dover `primary_domain` / Workable `url`;
domain -> company row; company row -> founders via YC company page or other cards (`github`, `launches`).
People: ATS APIs expose no person names. Founder names come from the HN post text ("I'm Daniel, co-founder"), the HN `author` handle, and YC `founders[]`.

---

## 8. Snapshot and polling plan

- Daily: for each registry row, one light call (Greenhouse `/jobs` without content, Ashby `ApiJobBoardWithTeams`, Lever full with gzip).
  Diff job ids against yesterday; stamp `first_seen_at` / `last_seen_at`. Store the raw response.
- Weekly: Greenhouse `/departments`, Ashby REST (for `publishedAt`), Ashby org GraphQL (website can change), re-run domain discovery for companies with `no_ats_yet`.
- Monthly on the 1st and again on the 3rd and 10th: HN hiring thread (it keeps growing: September ended at 254 top-level posts, October had 105 after nine hours).
- Daily: `yc-oss` `hiring.json` diff on `isHiring` and `team_size`.
- Conditional requests: Greenhouse and Ashby both return `etag`; send `If-None-Match` (not load-tested tonight).

## 9. Rate limits (measured)

| Host | What was sent | Result |
|---|---|---|
| `boards-api.greenhouse.io` | 488 probes at 0.35 s spacing, then 124 board pulls, then 20 back-to-back (7.9 req/s) | all 200/404, no 429, no rate headers |
| `api.ashbyhq.com` | 488 HEAD at 0.35 s spacing, 79 full GETs, 20 back-to-back HEAD (15 req/s) | all 200/404, no 429 |
| `jobs.ashbyhq.com` GraphQL | 75 POSTs at 0.4 s spacing | all 200 |
| `api.lever.co` | 488 probes, 18 full pulls, 10 back-to-back (2.8 req/s) | no 429, but **6 timeouts at 25 s and several 7 to 9 s stalls**; one un-timed curl hung past 120 s. Always set a timeout and retry once |
| `apply.workable.com` | 488 probes at 0.35 s spacing | **429 from request 57 to the end** (`Retry-After: 0`, which is not honest). Normal again 25 minutes later. Budget about 30 requests per 10 minutes |
| `hn.algolia.com` | about 25 calls, 10 back-to-back (4.5 req/s) | all 200, no rate headers (Algolia documents 10,000/hour per IP; not tested) |
| `ats.rippling.com` | 8 back-to-back | all 200 |
| company websites | roughly 350 page and bundle fetches | `physicalintelligence.company` 429 on every path; `www.radiantnuclear.com/careers` 403 on one fetch, 200 on others; `swarm.aero/careers` 401 |

Safe defaults: 2 req/s per ATS host, 1 req/3 s for Workable, 25 s timeout, one retry with backoff, gzip on.

---

## 10. Gotchas (all hit tonight)

1. **200 does not mean the right company.** 18 collisions (17 in the 489-slug sweep plus `figure` in the first probe, section 2.2). Short or dictionary-word slugs are the worst (`radiant`, `boom`, `atomic`, `sift`, `scout`, `arbor`, `impulse`).
2. **Empty-but-200 boards**: Ashby (`figure`, `vast`, `helsing`, `machina-labs`, `atomicindustries`, `scout`), Lever (8 slugs), Greenhouse (`apex`), Workable (16). An empty board is a parked or abandoned account, not "zero hiring".
3. **Stale boards after migration** keep serving old jobs (Greenhouse `etchedai`, `allencontrolsystems`). Always check newest posted date; ignore boards with nothing newer than 90 days when another board exists.
4. **Posted dates reset on ATS migration and on bulk republish** (Rainmaker, Astro Mechanica). `new_30d` from dates alone would have ranked Rainmaker first.
5. **Survivorship**: posted-date windows count only roles still open. A company that filled 20 roles last month shows nothing. Only snapshot diffs see fills and closes.
6. **Same company on two ATS at once** (Carbon Robotics, Fractile, Scout AI): double counting unless deduped by company.
7. **Evergreen and junk postings** inflate counts: `General Job Application` (Epsilon3), `Your Dream Job` (Boom on Rippling), `Rockstar` (Orbital Operations), talent-network roles. Exclude titles matching `general (application|interest)|dream job|talent (network|community)|future opportunities|rockstar`.
8. **Requisition-per-seat boards**: Shield AI lists near-identical titles with different `(R5156)`/`(R5157)` req ids; Zone 5 lists `Chief Engineer` three times. Dedupe on normalised title + location for "distinct roles", keep raw count for "seats".
9. **Greenhouse `departments`/`offices` only with `content=true`**, which is 25x larger. Use `/departments`.
10. **Lever is case-sensitive, Greenhouse and Ashby are not.** Careers pages sometimes link `jobs.ashbyhq.com/NorthwoodSpace`; lowercase before dedupe on Ashby/Greenhouse but never on Lever.
11. **Lever has no company name** in its JSON and **Ashby REST has none either**; you need the extra identity call.
12. **Ashby HEAD is only an existence check.** With `Accept-Encoding: gzip` the `content-length` header was missing for every non-empty board. GET to count roles.
13. **Careers pages hide the ATS**: Figure, Anduril, Zipline and Skydio proxy jobs through their own site. Skydio's `gh_jid` values are Ashby UUIDs.
14. **`zipline.com` vs `flyzipline.com`**: the old domain redirects; the Greenhouse slug kept the old name (`flyzipline`). Slugs outlive rebrands.
15. **HN header parsing**: company is field 0 in about 93% of posts; some posts lead with a city or have no pipes. `comment_text` is HTML with entities; strip `<a>` before keyword matching or domains like `factory.ai` and `reactor.inc` produce false positives.
16. **HN thread is not complete on day 1** (105 posts at 9 hours vs 254 for the full previous month). Re-pull.
17. **Keyword false positives**: "Silicon Valley", "space" (office space), "grid" (CSS), "foundry", "autonomy" (workplace autonomy). Strict patterns in section 4 remove most; expect about 50% precision at one keyword group.
18. **YC `/jobs/role/{x}` silently falls back** to software engineer for unknown roles, and Work at a Startup returns 406 unless `Accept: text/html` is sent.
19. **zsh note for builders**: `curl $FLAGS` with flags in a variable is passed as one argument in zsh; use a function or an array.
20. **Wayback CDX was unusable** tonight for backfilling board history (empty array, then an offline page). Do not plan on it.

---

## 11. Thesis-aligned entities seen in live responses (2026-10-01)

Small or early companies first. Every row is from a response fetched tonight.

| Entity | Sector | What the data showed | Evidence |
|---|---|---|---|
| Valstad Shipworks | robotics / shipbuilding | First-ever HN hiring post (Sep 2026): "high-mix robotics platform for autonomous shipbuilding", Austin. Ashby board: 10 roles, oldest 2026-08-31, half manufacturing, incl. `Head of Manufacturing & Industrialization`, welders, controls technician | https://news.ycombinator.com/item?id=49528413 , https://api.ashbyhq.com/posting-api/job-board/valstad |
| etc. (Exploration Technology Corp.) | space / defense | First-ever HN post by the author (Oct 2026): space-based IR sensing and interceptor work for DoD and NASA, SF. Rippling board: 7 roles (avionics EE, lead mechanical, vision systems, wire harness technician, BD associate) | https://news.ycombinator.com/item?id=49925468 , https://ats.rippling.com/api/v2/board/etc/jobs |
| General Galactic | space propulsion | Greenhouse board appeared with 17 roles all first published 2026-10-01, El Segundo: electric propulsion, electrolyzer design, GNC, flight software, Director of Avionics | https://boards-api.greenhouse.io/v1/boards/generalgalactic/jobs |
| Charge Robotics | robotics / energy (solar construction) | HN Oct 2026: YC-backed Series A, robots that build solar farms. Ashby: 14 roles, 11 published 2026-09-28 incl. `Head of Manufacturing`, `Head of Engineering`, Robotic Factory Operator | https://news.ycombinator.com/item?id=49926244 , https://api.ashbyhq.com/posting-api/job-board/charge-robotics |
| ko-br | robotics / manufacturing | HN Oct 2026: robots for mundane manufacturing work, SF / London / Cairo. Dover board: 4 roles (Founding Software Engineer, Founding Deployment & Operations Engineer, Founding Sales Engineer, Robot Teleoperator) | https://news.ycombinator.com/item?id=49925283 , https://app.dover.com/api/v1/careers-page-slug/ko-br |
| Lumen Labs | robotics / physical AI | HN Sep 2026: team of two hiring teammate 3 (robotics/hardware engineer), pre-seed, SF; targets construction, mining, pipelines, battlefield | https://news.ycombinator.com/item?id=49667711 |
| Cascade Space | space ground infrastructure | HN Sep 2026: dish arrays with digital beamforming for lunar and deep-space comms; seed stage; first mechanical engineering hire | https://news.ycombinator.com/item?id=49538639 |
| Whistle Robotics | robotics / manufacturing | HN Sep 2026: task-specific robots for small and medium manufacturers, London; founding geometry engineer; post written by the CTO | https://news.ycombinator.com/item?id=49611044 |
| Springcraft | robotics platform / hardware | HN Sep 2026: "platform for physical AI", Palo Alto; first manufacturing hire. Careers page resolves to Dover `springcraft` | https://news.ycombinator.com/item?id=49555479 |
| Valkyrie Aero | defense / counter-drone | HN Sep 2026: DoD prime building an airborne counter-drone system for a manned aircraft. Breezy board: 3 contract software roles (autonomy, perception, HMI), Mesa AZ, published 2026-09-18 | https://news.ycombinator.com/item?id=49578811 , https://valkyrie-aero.breezy.hr/json |
| 9 Mothers | defense / counter-drone | YC P26, Austin, team 19, founded 2024; YC page lists 3 founders with LinkedIn. Ashby: 7 roles (perception, UAS pilot, field applications, integration), newest 2026-10-01 | https://www.ycombinator.com/companies/9-mothers-corporation/jobs , https://api.ashbyhq.com/posting-api/job-board/9-mothers |
| Swarm Aero | drones / defense aviation | Ashby: 37 open roles, 30 published in the last 30 days vs 5 in the prior 30, spread over more than a dozen dates; `Vice President, Software`, `Head of Marketing` open | https://api.ashbyhq.com/posting-api/job-board/swarmaero |
| Heron Power | energy / power electronics | Ashby: 39 roles, 16 new in 30 days vs 6 prior, hardware share 0.62, single location, `Head of Sales` and `Head of People Operations` open | https://api.ashbyhq.com/posting-api/job-board/heron-power |
| Strobe Power | energy / grid | HN Sep 2026: control stack and trading platform turning commercial batteries and generators into distributed power plants; SF, small team | https://news.ycombinator.com/item?id=49534764 |
| Vernius Systems / Edgerun / Streamline Systems | defense hardware (YC S26, S26, F26) | In `yc-oss` hiring feed with `isHiring: true`: mass-deployable air-defense radars (team 3); military exoskeletons (team 5, EE and firmware roles on Work at a Startup); GPS-denied UAV navigation (team 2) | https://yc-oss.github.io/api/companies/hiring.json |

Larger names that also showed strong movement tonight (context, not pre-consensus): Apex (Ashby `apex-technology-inc`, 133 open, 82 new in 30 d vs 19),
Antares (86 open, 43 vs 24), Astranis (174 open, 101 new in 30 d vs 16), Varda (102 open, 55 vs 18), Xcimer Energy (Lever, 55 open, 30 vs 9), Helion (69 open, 28 vs 7).
