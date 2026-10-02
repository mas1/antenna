# Source card: GitHub activity (`github`)

Measured live on 2026-10-01 (23:40Z) to 2026-10-02 (00:35Z) from this machine with the `gh` CLI
(OAuth token, scopes `gist, read:org, repo, workflow`) plus unauthenticated `curl` where noted.
Everything below is what the API actually returned, not what the docs or memory say.

Fixtures (real responses, trimmed): `/Users/noel/antifund/pipeline/fixtures/github/`

| File | What it is |
|---|---|
| `rest_search_repositories.json` | `GET /search/repositories` for `topic:robotics created:>2026-07-01 stars:>25` (8 of 30 items) |
| `rest_repo_bundle.json` | Per-repo enrichment bundle for one org-owned repo (`QymIs-Tech/QymCAD`) and one user-owned repo (`kevinzakka/mjbatch`): repo, owner profile, org members, contributors, **star history**, star count, the 404 from list-stargazers, releases, events; plus a `users/{u}/starred` sample with `starred_at` |
| `graphql_search_with_owner.json` | One GraphQL request = 2 aliased searches (`first:20` each, 31 repos returned) with owner profile inlined (cost 1 point) |
| `trending_weekly.parsed.json` | Parsed `github.com/trending?since=weekly` rows + one raw `<article>` block |
| `gharchive_2026-10-01-20.sample.jsonl` | One event per type from a GH Archive hourly file |

---

## TL;DR (the three things that changed vs. what everyone remembers)

1. **The stargazer list is dead for third-party repos.** `GET /repos/{o}/{r}/stargazers` (with or without
   `Accept: application/vnd.github.star+json`) returns **404** when authenticated and **401 "Requires authentication"**
   when not, for every repo we do not own (tested: `dexmal/opendm`, `kevinzakka/mjbatch`, `PX4/PX4-Autopilot`,
   `octocat/Hello-World`). It returns 200 only on a repo owned by the token's user. GraphQL
   `repository.stargazers` returns `totalCount: 0, edges: []` for the same repos. So the classic
   "read the last pages via the `Link` header" trick no longer works.
2. **There is a new, better endpoint: `GET /repos/{o}/{r}/stargazers/history`.** One call returns up to 30 weeks of
   *daily* star counts, newest first. Star velocity and acceleration now cost **1 core call per repo** (0 with an ETag hit).
   It also works unauthenticated.
3. **GH Archive is a sparse sample now and useless for star velocity.** An hourly file has ~92k events and only
   ~350-420 `WatchEvent`s for all of GitHub. A repo that got 11 stars in an hour (per the events API) had 0 in the archive.

---

## 1. Endpoints that work

All REST calls: base `https://api.github.com`, header `Accept: application/vnd.github+json`,
auth `Authorization: Bearer <token>` (here: `gh api <path>`). `X-GitHub-Api-Version` default resolved to
`2022-11-28`; `2026-03-10` also accepted and behaves the same for everything below.

### 1.1 Repo search (discovery)

```
GET /search/repositories?q={query}&sort=stars|updated|forks&order=desc&per_page={1..100}&page={n}
```

- Auth: optional. **Measured limit: 30 req/min authenticated, 10 req/min unauthenticated**
  (`x-ratelimit-resource: search`). 422 validation failures still consume quota.
- Pagination: `page`/`per_page`, hard cap of **1000 results** per query
  (`per_page=100&page=11` -> 422 `Only the first 1000 search results are available`). Slice by `created:` windows to get under it.
- Qualifiers that work: `topic:`, `created:>YYYY-MM-DD`, `created:A..B`, `pushed:>`, `stars:>N`, `stars:A..B`,
  `in:name,description,topics`, `in:readme`.
- `OR` works **between bare keywords only**. `topic:px4 OR topic:ardupilot ...` -> 422
  `Logical operators only apply to text, not to qualifiers`. Multiple `topic:` qualifiers are ANDed
  (`topic:drone topic:uav topic:px4 topic:ardupilot` returned 2).
- Keep: `items[].full_name, html_url, description, stargazers_count, forks_count, open_issues_count, created_at,
  pushed_at, homepage, topics[], language, license.spdx_id, fork, archived, owner.login, owner.type, owner.id`.

```
GET /search/users?q={keywords} type:org created:>{date} followers:>{n}&sort=followers|joined&per_page=30
```

- Same `search` bucket. Finds **newly created org accounts** by keyword in login/name. Returns only
  `login, id, type, html_url`; resolve with 1.4 or the GraphQL org batch in 1.3.

### 1.2 Star velocity (the core signal)

```
GET /repos/{owner}/{repo}/stargazers/history?per_page=30&page=1
```

- Auth: optional (200 unauthenticated; 60 req/h per IP in that case). Authenticated: `core` bucket, 5000/h.
- Response: array, newest week first.
  `[{ "week": 1790467200, "total": 14, "days": [4,1,5,4,0,0,0] }, ...]`
  - `week` = unix seconds of **Sunday 00:00 UTC** (measured: 1790467200 = Sun 2026-09-27).
  - `days[0..6]` = Sunday..Saturday. The current partial week and partial day are included.
  - `total` = sum of `days`.
- Pagination: `per_page` **max 30** (asking for 100 silently returns 30), `page` max 100
  (page 101 -> 422 `Pagination is limited to 100 pages`). `Link` header gives `rel="last"`
  (PX4/PX4-Autopilot: 25 pages = its whole life).
- Sum of all weeks == `GET /repos/{o}/{r}/stargazers/count` -> `{"count": 581}` (checked on `kevinzakka/mjbatch`:
  14+18+184+365 = 581). Counts are net of un-stars.
- Cost: **1 core call per repo for 30 weeks**, 0.48 s average wall. Sends `ETag` + `Cache-Control: max-age=60`;
  a conditional request with `If-None-Match` returned **304 and did not increment `X-RateLimit-Used`** (54 -> 54).
  A daily refresh of a 5,000-repo watchlist fits in one hour of quota, and unchanged repos are free.
- Daily counts track the events feed approximately, not exactly (ESPARGOS/esp-sdr on Wed 2026-09-30:
  113 from history vs 123 `WatchEvent`s in the events feed). Docs say day boundaries are not guaranteed to be UTC.

```
GET /repos/{owner}/{repo}/events?per_page=100&page={1..3}
```

- Core bucket. Up to 300 most recent events. `type == "WatchEvent"` rows give **who starred and when**
  (`actor.login`, `created_at`), which is the only remaining way to get stargazer identities for a repo we do not own.
  Also carries `ForkEvent`, `ReleaseEvent`, `PushEvent`, `PullRequestEvent`. `X-Poll-Interval: 60`, ETag supported.
- For a breakout repo, 300 events covered ~48 h (esp-sdr); for a quiet repo, weeks.
- Not strictly sorted by `created_at`; sort client-side.

```
GET /users/{username}/starred?sort=created&direction=desc&per_page=30
Accept: application/vnd.github.star+json
```

- Still works for any user and still returns `starred_at` + full `repo` object. This is the "tastemaker feed":
  follow N known-good robotics/hardware people and see what they star this week.

### 1.3 GraphQL (batching; cheapest in rate-limit terms)

```
POST https://api.github.com/graphql      (gh api graphql -f query=... )
```

- Auth: **required**. Separate `graphql` bucket, 5000 points/h. Every request below cost **1 point**
  (read `rateLimit { cost remaining }` in the same query).
- GraphQL `search` does **not** use the 30/min REST search bucket and accepts the same query syntax
  (put `sort:stars-desc` inside the query string).
- The practical limit is a ~10 s server timeout that surfaces as **HTTP 502 with an HTML body**, not rate limits.

Measured:

| Request | Wall | Result |
|---|---|---|
| 15 aliased searches x `first:3`, light fields | 4.0-4.5 s | ok |
| 2 aliased searches x `first:20` with owner profile (31 nodes returned) | 9.3 / 9.7 / 10.8 s | ok three times (fixture), but borderline |
| 2 aliased searches x `first:10` with owner profile (20 nodes) | 5.1 s | ok |
| same + `mentionableUsers` + commit `history{totalCount}` on 50 nodes | 10.7 s | **502** |
| 30 `repository(...)` aliases, full fragment without commit history | 7.9-8.9 s | ok, but too close to the edge |
| 30 `repository(...)` aliases with commit `history{totalCount}` | 10 s | **502** |
| 8 `repository(...)` aliases with commit history | 3.5 s | ok |
| 20 `organization(...)` aliases with top-3 repos each | 1.5 s | ok |
| 80 `user(login:)` aliases (createdAt, followers, repos) | 3.0-3.8 s | ok |

Wall time scales at roughly 0.3 s per fully-enriched repo node, whatever the mix of aliases.
Recommended batch sizes: **<= 20 enriched repo nodes per request** (10 if commit history is selected),
15 light searches per request, 80 users per request.

Search-with-owner query used for the fixture:

```graphql
query($q1:String!,$q2:String!){
  rateLimit{cost remaining limit resetAt}
  a: search(query:$q1, type:REPOSITORY, first:20){ repositoryCount nodes{ ...R } }
  b: search(query:$q2, type:REPOSITORY, first:20){ repositoryCount nodes{ ...R } }
}
fragment R on Repository{
  nameWithOwner url description stargazerCount forkCount createdAt pushedAt homepageUrl isFork isArchived
  licenseInfo{spdxId} primaryLanguage{name}
  repositoryTopics(first:12){nodes{topic{name}}}
  releases{totalCount} watchers{totalCount} issues{totalCount} pullRequests{totalCount}
  owner{ __typename login url
    ... on Organization{ name websiteUrl createdAt description email location twitterUsername isVerified
                         membersWithRole{totalCount} repositories(privacy:PUBLIC){totalCount} }
    ... on User{ name company websiteUrl location bio createdAt twitterUsername
                 followers{totalCount} repositories(privacy:PUBLIC){totalCount} }
  }
}
```

GraphQL has **no star-history field** (`Repository` exposes only `stargazerCount` and the now-empty `stargazers`);
velocity must come from the REST history endpoint.

### 1.4 Owner / entity resolution (REST, core bucket, 1 call each)

| Endpoint | Keep |
|---|---|
| `GET /repos/{o}/{r}` | `owner.type`, `organization.login`, `homepage`, `license.spdx_id`, `topics`, `subscribers_count`, `network_count`, `has_discussions`, `created_at`, `pushed_at` |
| `GET /orgs/{org}` | `name, blog, email, location, twitter_username, description, created_at, public_repos, followers, is_verified` |
| `GET /users/{login}` | `name, company, blog, location, bio, twitter_username, followers, public_repos, created_at, hireable` |
| `GET /orgs/{org}/members?per_page=100` | public members only; **0 for most orgs** (membership is private by default) |
| `GET /repos/{o}/{r}/contributors?per_page=10` | `login, type, contributions` |
| `GET /repos/{o}/{r}/contributors?per_page=1&anon=1` | read `Link rel="last"` page number = total contributor count (dexmal/opendm: 8) |
| `GET /repos/{o}/{r}/commits?per_page=1` | `Link rel="last"` page number = total commit count (dexmal/opendm: 36) |
| `GET /repos/{o}/{r}/releases?per_page=5` | `tag_name, published_at` |
| `GET /repos/{o}/{r}/readme` with `Accept: application/vnd.github.raw+json` | raw README for domain/hiring regex |
| `GET /repos/{o}/{r}/community/profile` | `health_percentage`, which of CoC/contributing/license/PR template exist |
| `GET /repos/{o}/{r}/forks?sort=newest&per_page=100` | fork timestamps (fork velocity) |
| `GET /repos/{o}/{r}/stats/contributors` | **202 with empty body on first call**; retry after a few seconds |

Measured cost of the full REST enrichment (repo + owner + members + contributors + history + releases + readme):
**7 core calls per repo; 22 repos = 148 calls in 114 s** with a 0.25 s pause between calls.
The GraphQL batch in 1.3 replaces 5 of those 7 (everything but star history and README) at 1 point per 10 repos.

### 1.5 Trending page (HTML, no API)

```
GET https://github.com/trending?since=daily|weekly|monthly
GET https://github.com/trending/{language}?since=...        (python, c++, rust, verilog, systemverilog, vhdl ...)
GET https://github.com/trending?since=weekly&spoken_language_code=en
GET https://github.com/trending/developers/{language}?since=weekly
```

- No auth, plain `curl` with a descriptive UA returned HTTP 200 on all six URLs tried (2 s apart).
  520-750 KB of HTML each. `robots.txt` has no rule mentioning `/trending`.
- Each repo is one `<article class="Box-row">`. Regexes that parsed every row on 2026-10-01:
  - repo: `<h2[^>]*>\s*<a[^>]*href="/([^"]+)"`
  - description: `<p class="col-9[^"]*">\s*(.*?)\s*</p>`
  - language: `itemprop="programmingLanguage">([^<]+)<`
  - total stars: `href="/[^"]+/stargazers"[^>]*>.*?</svg>\s*([\d,]+)`
  - period stars: `([\d,]+)\s+stars\s+(today|this week|this month)`
  - built by: `<a class="d-inline-block"[^>]*href="/([^"]+)"`
- Row counts are not fixed at 25: weekly 18, weekly+`spoken_language_code=en` 23, python weekly 14, c++ daily 21, verilog monthly 5.
- **Thesis yield is poor.** The overall weekly list on 2026-10-01 was all agent/LLM tooling (paperclip, hindsight,
  VoiceStudio, claude-skills ...); zero robotics/hardware. `verilog?since=monthly` showed only incumbents
  (picorv32, OpenROAD). Use it as a cheap sanity feed and for `stars_in_period` cross-checks, not for discovery.
- `spoken_language_code=en` returned a quite different list dominated by old mega-repos; do not treat it as a filtered subset.

### 1.6 GH Archive (does not work for this purpose)

```
GET https://data.gharchive.org/{YYYY-MM-DD}-{H}.json.gz      (H = 0..23, no zero padding)
```

- No auth. `2026-10-01-20` = 23.3 MB gz / 107 MB JSONL / 92,585 events; `2026-09-30-15` = 28.0 MB.
  Download + gunzip 0.6 s. Files land about 5 minutes after the hour ends (`last-modified: 21:05:08` for hour 20;
  hour 23 was still 404 at 00:03Z).
- Event mix in hour `2026-10-01-20`: PushEvent 70,193, CreateEvent 8,511, PullRequestEvent 3,962, DeleteEvent 3,955,
  IssueCommentEvent 1,990, IssuesEvent 1,468, **WatchEvent 422**, ReleaseEvent 252, **ForkEvent 87**, MemberEvent 45, PublicEvent 7.
- Coverage test: `ESPARGOS/esp-sdr` had 11 `WatchEvent`s in hour `2026-10-01-12` per the repo events API;
  the archive file for that hour contained **0** of them (356 WatchEvents total, max 5 for any single repo).
- Verdict: a thin sample of the public timeline. Not usable for per-repo velocity or for a reliable new-repo firehose.
  Use the star-history endpoint instead.

---

## 2. Thesis queries run and result counts (2026-10-01)

REST `GET /search/repositories`, `sort=stars&order=desc&per_page=30` (25 authenticated search calls used in total, 3 of them 422s):

| Query | total_count | Top hits seen |
|---|---|---|
| `topic:robotics created:>2026-07-01 stars:>25` | 83 | dexmal/opendm 2202, OpenWAM-Official/OpenWAM 938, RoboDojo-Benchmark/RoboDojo 665, Hebbian-Robotics/hflow 284 |
| `humanoid in:name,description,topics created:>2026-07-01 stars:>15` | 33 | Roboparty/UFO 286, menloresearch/cyclotron 103, BeijingDynamics/open_sprite 21 |
| `topic:drone topic:uav topic:px4 topic:ardupilot created:>2026-07-01 stars:>5` (AND) | 2 | TianHengZhuang/mavplan, NIKX-Tech/karshipta |
| `px4 OR ardupilot OR uav OR drone in:name,description,topics created:>2026-07-01 stars:>20` | 35 | agamrossen/VolAnti 399, formiat/px4-ros2-drone-nav 143, Fratres-X-AI/JamBoy 135 |
| `topic:ros2 created:>2026-07-01 stars:>15` | 21 | Flaminis/Dalaran 736, Tim-HW/glass-lio 175 |
| `"vision-language-action" OR vla OR "robot learning" in:name,description,topics created:>2026-07-01 stars:>30` | 50 | dexmal/opendm, Robbyant/lingbot-vla-v2 991, H-EmbodVis/TurboVLA 607 |
| `manipulation OR teleoperation OR dexterous in:name,description,topics created:>2026-07-01 stars:>30` | 33 | facebookresearch/project_superdex 707, shengshu-ai/Motus2 393, neoteai/N0-TWAM 190 |
| `mujoco OR "isaac lab" OR isaaclab OR "isaac sim" in:name,description,topics created:>2026-07-01 stars:>25` | 30 | kevinzakka/mjbatch 581, menloresearch/cyclotron 103 |
| `fusion-energy OR tokamak OR stellarator OR "nuclear reactor" OR "power grid" OR "battery management" in:name,description,topics created:>2026-06-01 stars:>10` | 5 | FusionAlpha/veqpy 18, kickstage/bamboogrid 11 |
| `cnc OR cad OR gcode OR "computer-aided" in:name,description,topics created:>2026-07-01 stars:>40` | 39 | Pan-Chera/Multi-Agent-CAD 1013, BOMWiki/partmode 511, QymIs-Tech/QymCAD 182, cadmpeg/cadmpeg 128 |
| `sdr OR "software-defined radio" OR gnuradio OR lora in:name,description,topics created:>2026-07-01 stars:>25` | 71 | mostly ML-LoRA noise; real: ESPARGOS/esp-sdr 290, Newspicel/sdrminusminus 258 |
| `satellite OR cubesat OR orbital OR spacecraft in:name,description,topics created:>2026-07-01 stars:>25` | 25 | mostly Home Assistant "voice satellite" noise; real: zhaoyun20180911/AeroGNC-Lab 54 |
| `fpga OR verilog OR systemverilog OR risc-v OR asic OR tapeout in:name,description,topics created:>2026-07-01 stars:>30` | 21 | SigmanticAI/apex-inference-chip 577, penberg/titania 119, exeex/edge-cores 110 |
| `topic:robotics pushed:>2026-09-24 created:2025-10-01..2026-06-30 stars:100..3000` | 33 | mosaico-labs/mosaico 1057, robocurve/inspect-robots 631, softmata/horus 441, selfpatch/ros2_medkit 271, VinRobotics/vla.cpp 213 |
| `topic:drone pushed:>2026-09-15 created:>2025-06-01 stars:40..3000` | 14 | batear-io/batear 415, altnautica/ADOSMissionControl 245, PteroLabsAI/PteroSim-UAV-Simulator 107 |
| `"we are hiring" OR "we're hiring" in:readme topic:robotics pushed:>2026-08-01 stars:>20` | 1 | HorizonRobotics/HoloMotion |

REST `GET /search/users` (new org accounts):

| Query | total_count |
|---|---|
| `robotics type:org created:>2026-06-01` (sort=joined) | 750 (mostly school clubs / FRC teams) |
| `robotics type:org created:>2026-04-01 followers:>10` | 18 (VinRobotics, robocurve, Hebbian-Robotics, DAXIAORobotics, mirrormerobotics, lumi-robotics, Nori-Robotics, murobotics-ai ...) |
| `drone OR uav OR aerospace OR defense type:org created:>2026-01-01 followers:>5` | 14 (OpenDrone-hw, Bare-Metal-Foundry ...) |
| `energy OR fusion OR nuclear OR grid OR battery type:org created:>2026-01-01 followers:>5` | 27 (Parisi-Labs, FusionAlpha; rest is noise) |
| `dynamics OR humanoid OR embodied type:org created:>2026-01-01 followers:>10` | 15 (LuwuDynamics, Riemann-Dynamics, MachEmbodied ...) |

GraphQL topic sweep, `topic:{t} created:>2026-07-01 stars:>=10 sort:stars-desc` (45 topics in 3 requests, 3 points):

| topic | n | topic | n | topic | n |
|---|---|---|---|---|---|
| robotics | 154 | slam | 18 | gnuradio | 2 |
| humanoid | 1 | lidar | 13 | radar | 11 |
| ros2 | 41 | fusion | 3 (not energy) | satellite | 3 |
| px4 | 3 | tokamak | 0 | cubesat | 0 |
| ardupilot | 3 | nuclear | 0 | aerospace | 4 |
| drone | 22 | battery | 18 (laptop widgets) | gnss | 2 |
| uav | 10 | bms | 1 | fpga | 23 |
| vla | 26 | power-systems | 4 | verilog | 18 |
| robot-learning | 23 | energy-storage | 8 | risc-v | 8 |
| embodied-ai | 46 | cnc | 4 | asic | 7 |
| mujoco | 25 | cad | 38 | eda | 17 |
| isaac-sim | 5 | 3d-printing | 51 | pcb | 15 |
| lerobot | 11 | manufacturing | 6 | kicad | 23 |
| teleoperation | 5 | plc | 9 | defense | 0 |
| manipulation | 9 | sdr | 19 | motor-control | 1 |

Reading: GitHub is a **dense** signal for robotics / embodied AI / sim / CAD / EDA / FPGA / SDR and a
**near-empty** one for nuclear, fusion, grid, batteries, space and defense. Do not weight this source equally across sectors.

---

## 3. Derived features for the scorer

From `stargazers/history` (flatten `week + i*86400` -> daily series, then window):

- `stars_7d`, `stars_prev_7d`, `stars_30d`, `stars_prev_30d`
- `accel_7d = (stars_7d + 1) / (stars_prev_7d + 1)`; `accel_30d` likewise. Measured examples:
  QymIs-Tech/QymCAD 144 vs 8 -> 16.1x; ESPARGOS/esp-sdr 291 vs 0 (4 days old); menloresearch/cyclotron 58 vs 45 -> 1.28x;
  dexmal/opendm 43 vs 468 -> 0.09x (post-launch decay; 1,743 in 30 d).
- `velocity_share_30d = stars_30d / stargazers_count` (how much of the repo's life is "now").
- `peak_day`, `days_since_peak`, `burstiness = peak_day / stars_30d` (one-day HN spike vs. sustained pull).
- `sustained_weeks` = count of the last 8 weeks with `total >= k`.
- `first_star_week` (earliest non-zero week) as true public launch date; `created_at` can precede it by months.

From repo events (`WatchEvent` actors) + one GraphQL user batch (**stargazer quality**, 1-3 core calls + 1 point per repo):

- `sg_median_account_age_days`, `sg_pct_age_lt_90d`, `sg_pct_zero_followers`, `sg_pct_zero_repos`, `sg_max_followers`.
- Measured, last <=80 distinct recent stargazers:

  | Repo | median acct age (d) | <90 d old | 0 followers |
  |---|---|---|---|
  | kevinzakka/mjbatch | 2182 | 1% | 22% |
  | ESPARGOS/esp-sdr | 3969 | 0% | 14% |
  | QymIs-Tech/QymCAD | 3352 | 4% | 31% |
  | v-modal/vmodal_sdk_robotics | 43 | 78% | 94% |
  | shengshu-ai/Motus2 | 46 | 98% | 99% |

  The last two also have `forks/stars` of 1/462 and 2/394. Use `sg_pct_age_lt_90d > 0.5` as a hard discount on
  velocity. This is the single most useful de-noiser found.
- `tastemaker_hits` = number of watchlisted accounts appearing among `WatchEvent` actors, or having the repo in
  their `users/{u}/starred` feed within N days.

From repo + owner:

- `fork_star_ratio` (flag < 0.01 and > 0.4; Hebbian-Robotics/hflow is 159/284, worth a manual look rather than an auto-penalty).
- `owner_is_org`, `owner_age_days`, `org_age_lt_365d`.
- `has_custom_domain` (owner `blog` or repo `homepage`, excluding github.io, t.me, linkedin, x.com, huggingface, arxiv, readthedocs).
- `email_on_domain` (org `email` domain == site domain).
- `human_contributors_ge5` (contributors with >= 5 commits after dropping bots; see gotchas).
- `commit_count`, `contributor_count` (Link-header trick), `release_count`, `days_since_last_release`, `days_since_push`.
- `license_is_real` (`spdx_id` not null / `NOASSERTION`), `has_twitter`, `is_verified`.
- `org_public_repos`, `org_repo_breadth` (several sibling repos such as sdk + hardware + site = product, not a paper drop).
- `hardware_repo` (topics/README mention KiCad, CERN-OHL, STEP, BOM): in this thesis, published hardware files are a strong tell.
- `novelty` = repo not seen in any prior run AND `first_star_week` within 4 weeks.
- `paper_drop` = description starts with `[NeurIPS|CoRL|ICRA|IROS|ECCV ...]` or "Official implementation/repository of":
  route to the researcher/founder track, not the company track.

Company-in-formation heuristic tested on 34 enriched repos
(`2*org + 2*org_age<365d + 2*custom_domain + multi_contributor(>=2, >=4) + releases + real_license + twitter + email_on_domain`):
top of the ranking was copperheadhq/copperhead (12), selfpatch/ros2_medkit, robocurve/inspect-robots,
Hebbian-Robotics/hflow (11), VinRobotics/vla.cpp (10); bottom was single-author user repos
(agamrossen/VolAnti 0, exeex/edge-cores 2). It separates companies from hobby repos well on this sample.

README hiring text: regex `we're hiring|join our team|careers|open positions|jobs@` matched **0 of 34** enriched
READMEs, and the `in:readme` search returned 1 repo. Keep as a rare bonus, not a core feature; hiring is better
read from the company domain (other source cards).

---

## 4. Entity resolution

- **Company key = registrable domain** from, in order: org `blog`/`websiteUrl`, repo `homepage`, org `email` domain,
  first non-generic link in README. Examples seen: `dexmal.com`, `hebbianrobotics.com`, `robocurve.org`,
  `copperhead.sh`, `selfpatch.ai`, `qymis.tech`, `mosaico.dev`, `menlo.ai`, `murobotics.ai`, `batear.io`, `pterolabs.ai`, `parisi-labs.com`.
- **GitHub keys**: `owner.id` (stable across renames) + `owner.login`; repo `id` (the `Link` headers already use
  `/repositories/{id}/...`).
- **Social key**: `twitter_username` on org or user (`hbr_pbc`, `robocurve`, `copperheadhq`, `menloresearch`, `kevin_zakka`).
- **Founder candidates**: top 1-3 human contributors by commits on the flagship repo -> `GET /users/{login}` for
  `name, company, blog, location, twitter_username`. Org public members are usually empty, so contributors are the path.
- User-owned repos: the user *is* the entity; `company` and `blog` on the profile link to an employer or a personal domain.
- Merge rule: same domain OR same org id => same company; a person links to a company when they are a top
  contributor to an org repo or their profile `company`/`blog` matches the domain.

---

## 5. Rate limits and budgets (measured)

| Bucket | Limit | Notes |
|---|---|---|
| REST `search` | 30/min auth, 10/min unauth | 422s count |
| REST `core` | 5000/h auth, 60/h unauth | 304s are free |
| GraphQL | 5000 points/h | every query here cost 1; real limit is the ~10 s timeout (502) |

No secondary rate limit (403/429) was hit at 0.25-3 s spacing. This token is shared with other jobs in the same
run, so `remaining` values moved between calls independently of this work.

Suggested daily budget for a 2,000-repo watchlist: 3 GraphQL sweep requests (45 topic slices) + ~20 REST searches
for keyword slices + 2,000 history calls (most 304) + 200 GraphQL repo batches + events/user-batch only for the top ~100 by acceleration.

---

## 6. Gotchas

1. List-stargazers and list-watchers (`/subscribers`) return 404 for repos you do not own, 401 unauthenticated;
   GraphQL `stargazers` is silently empty. The public docs page still says the endpoint works without auth. Trust the response.
2. `GET /rate_limit` reported `used: 0, remaining: 5000` on every call during this session while response headers and
   GraphQL `rateLimit` showed real usage. Read `X-RateLimit-*` headers on actual responses instead.
3. Search `OR` cannot combine qualifiers; 1000-result cap; 422s burn quota.
4. Keyword noise is severe: `lora` (ML adapters), `satellite` (Home Assistant voice satellites), `cad` matches
   `cadence`/`Caduceus`/`cadets`, `orbital` (games), `fusion` (Fusion 360, DaVinci), `battery` (laptop widgets),
   `grid` (CSS/UI). Prefer `topic:` slices or post-filter on topics + description.
5. `topic:humanoid` has 1 repo but the keyword `humanoid` has 33: most authors do not tag topics. Need both passes.
6. Heavy GraphQL selections (`mentionableUsers`, commit `history.totalCount`, 30 repos at once) fail as HTTP 502 HTML, not as a GraphQL error. Keep batches small and retry by halving.
7. Contributors include automation: `dependabot[bot]`, `claude`, `Copilot`, `*-release-bot[bot]` all appeared. Filter by `type == "Bot"` and a name list.
8. Repo `homepage` can be junk (`--disable-wiki`, a path back to github.com, empty string); validate as a URL.
9. `orgs/{org}/members` shows public members only (0-3 for every org sampled). `membersWithRole.totalCount` in GraphQL is the same public view.
10. `stats/contributors` returns 202 + empty on first hit.
11. Star history `per_page` is capped at 30 without error; a 397-star repo created in 2025 showed only 28 stars across the first page, so older repos need page 2+ for lifetime totals.
12. Inorganic stars exist in this exact niche (see stargazer-quality table). Raw star velocity must never be used without the quality discount.
13. Many high-velocity robotics repos are paper code drops from labs and large companies (facebookresearch, Tencent, alibaba-damo-academy, NVlabs, OpenBMB). Exclude a known-incumbent org list before ranking.
14. A large share of new robotics repos come from China-based labs and companies; location is often null. Geography must come from the domain/WHOIS or other sources.
15. Profile `email` fields are sometimes personal addresses. Fixtures have personal/free-mail addresses nulled; the pipeline should store company-domain emails only.
16. GH Archive filenames use an un-padded hour (`-0` .. `-23`); the newest hour 404s until ~5 min past the next hour.
17. Trending HTML is class-name scraping and will break without notice; row count varies (5-23 seen).

---

## 7. Thesis-aligned entities seen in live responses (2026-10-01)

Numbers are from the responses above; `s7`/`s30` from the star-history endpoint.

| Entity | Sector | Evidence | Why it surfaced |
|---|---|---|---|
| QymIs-Tech / QymCAD | manufacturing (CAD kernel) | https://github.com/QymIs-Tech/QymCAD | Org created 2026-08-09, domain qymis.tech (Kazakhstan); 182 stars, s7 144 vs 8 prior week (16x); recent stargazers are old, real accounts; 4 releases |
| copperheadhq / copperhead | semiconductors / EDA-PCB | https://github.com/copperheadhq/copperhead | Org created 2026-08-07, copperhead.sh; 315 stars, 260 in 30 d; 9 human contributors with >= 5 commits; top company-formation score |
| ESPARGOS / esp-sdr | RF / SDR | https://github.com/ESPARGOS/esp-sdr | Repo created 2026-09-28, 291 stars in 4 days, organic stargazers (median account age 3,969 d); espargos.net, Germany |
| Hebbian-Robotics / hflow | robotics data infra | https://github.com/Hebbian-Robotics/hflow | Org created 2026-04-11, hebbianrobotics.com, 20 releases, 10 human contributors; 284 stars. Fork ratio 159/284 needs a manual look |
| robocurve / inspect-robots | robotics evals | https://github.com/robocurve/inspect-robots | Org created 2026-06-25, robocurve.org; 631 stars, 433 in 30 d; 7 contributors |
| murobotics-ai / handumi-hw | robotics data-collection hardware | https://github.com/murobotics-ai/handumi-hw | Org created 2026-07-13, murobotics.ai (US); open hand-worn UMI hardware + software + Quest app; 97 stars |
| batear-io / batear | defense / counter-UAS | https://github.com/batear-io/batear | Org created 2026-03-29, batear.io; low-cost acoustic drone detector; 415 stars; dataset + site repos |
| OpenDrone-hw / OpenRX | drones (open FPV hardware) | https://github.com/OpenDrone-hw/OpenRX | Org created 2026-04-05, opendrone.be (Belgium); 30 repos of ESC / flight controller / receiver hardware; 425 stars |
| PteroLabsAI / PteroSim-UAV-Simulator | drones / sim | https://github.com/PteroLabsAI/PteroSim-UAV-Simulator | Org created 2026-03-31, pterolabs.ai; 107 stars, 61 in last 30 d vs 24 prior 30 d |
| altnautica / ADOSMissionControl | drones / autonomy software | https://github.com/altnautica/ADOSMissionControl | Org created 2026-02-17; ground control + drone agent + Android GCS repos; 245 stars |
| selfpatch / ros2_medkit | robotics ops | https://github.com/selfpatch/ros2_medkit | selfpatch.ai (Poland), org created 2025-10-24; 271 stars; two core committers with 1,426 and 399 commits; releases |
| Parisi-Labs / twingraph | energy (grid world models) | https://github.com/Parisi-Labs/twingraph | Org created 2026-06-11, parisi-labs.com, bio says world models for physical industry starting with energy; <= 19 stars, very early |
| SigmanticAI / apex-inference-chip | semiconductors | https://github.com/SigmanticAI/apex-inference-chip | sigmanticai.com; LLM inference chip design on FPGA; 577 stars; single committer |
| menloresearch / cyclotron | humanoids | https://github.com/menloresearch/cyclotron | menlo.ai (Singapore); RL training framework for its Asimov humanoid; created 2026-09-11, 58 stars in last 7 d vs 45 prior |
| MachEmbodied / ME-U0 | robotics / embodied AI | https://github.com/MachEmbodied/ME-U0 | Org created 2026-09-14, machembodied.com; 5 repos in 3 weeks; 30 stars |

Seen but flagged rather than recommended: `v-modal/vmodal_sdk_robotics` and `shengshu-ai/Motus2`
(recent stargazer samples 78% and 98% accounts under 90 days old), `dexmal/opendm` and `DAXIAORobotics/kairos`
(real and large, 2,202 and 2,996 stars, but already past the pre-consensus stage).
