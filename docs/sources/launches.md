# Source card: Launches

Slug: `launches`
Measured: 2026-10-01 23:56 UTC to 2026-10-02 00:25 UTC, from this machine, no API keys, no logins.
User-Agent used on every call: `antifund-sourcing-research/0.1 (mason@alterity.systems)`
Fixtures: `/Users/noel/antifund/pipeline/fixtures/launches/` (10 small files, 411 KB total, largest 137 KB, every one a real response trimmed). That is more files than the 1-3 asked for because each source has a different shape; total size is still under the 3 x 200 KB budget.

Everything below is what was measured, not what the docs say. Where a number is a documented claim rather than a measurement it is labelled.

---

## 0. Ranking (what to build first)

| # | Source | Works keyless | Machine-readable | Pre-consensus value | Build |
|---|--------|---------------|------------------|---------------------|-------|
| 1 | YC directory mirror (`yc-oss.github.io/api`) + daily `changes/latest.json` | yes | JSON | High. 140 thesis companies in the last 3 batches, most with team_size 2-4, new ones appear daily | tonight |
| 2 | YC Launches JSON (`ycombinator.com/launches`, `Accept: application/json`) | yes | JSON (undocumented) | High. Dated launch posts with vote counts, searchable | tonight |
| 3 | HN Algolia (`show_hn`, `launch_hn`) + Firebase | yes | JSON | Medium-high. Only source here that catches non-accelerator builders; noisy | tonight |
| 4 | a16z speedrun API (`speedrun-api.a16z.com`) | yes | JSON (undocumented) | High. 321 companies, founders + LinkedIn + GitHub org in detail | tonight |
| 5 | HAX / SOSV WordPress REST (`/wp-json/wp/v2/company`, `/founder`) | yes | JSON | High for deep hard tech (fusion, lithography, propulsion, 2D semis) | tonight |
| 6 | YC company page (`data-page` JSON) | yes | JSON-in-HTML | Enrichment only: founders, LinkedIn, X, GitHub | tonight |
| 7 | Product Hunt Atom feed with `?category=` | yes | Atom | Low for this thesis; consumer gadget heavy, no vote counts | optional |
| 8 | TechCrunch RSS + WP REST, Hackaday, Robot Report, SpaceNews, IEEE Spectrum, Breaking Defense | yes | RSS / JSON | Low (already-consensus press). Use as a "known" penalty, not a discovery feed | optional |
| 9 | Activate fellows, Breakthrough Energy Fellows, Thiel Fellows, AFWERX, Starburst, a16z American Dynamism | no clean list | see section 7 | would be high, but not machine-readable | skip tonight |

---

## 1. Hacker News via Algolia

### 1.1 Endpoints (all GET, no auth, CORS `*`)

| Endpoint | URL template | Notes |
|---|---|---|
| Search, newest first | `https://hn.algolia.com/api/v1/search_by_date?query={q}&tags={tags}&numericFilters={nf}&hitsPerPage={n}&page={p}` | the workhorse |
| Search, relevance | `https://hn.algolia.com/api/v1/search?...` | same params; ranks by relevance then points |
| Item with full comment tree | `https://hn.algolia.com/api/v1/items/{id}` | keys: `author, children, created_at, created_at_i, id, options, parent_id, points, story_id, text, title, type, url` |
| User | `https://hn.algolia.com/api/v1/users/{username}` | `{about, karma, username}` |

Parameters that were verified to work:

- `tags=show_hn`, `tags=launch_hn` (yes, a real tag; 33 hits in 90 days, 104 in 365 days), `tags=(show_hn,launch_hn)` for OR, `tags=story,author_{name}` for AND, `tags=story,story_{id}` to fetch exactly one story, `tags=front_page`.
- `numericFilters=created_at_i>{unix}` and comma-joined AND: `created_at_i>{unix},points>=50`.
- `restrictSearchableAttributes=title` (or `url`), `typoTolerance=false`, `optionalWords=a b c` (turns a multi-word query into OR).
- `hitsPerPage` max is 1000 (asking for 2000 silently returns 1000).
- `page` is zero-based. Hard cap of 1000 hits per query: `page=10` at `hitsPerPage=100` returns HTTP 200 with zero hits and a `message` field. Page through time windows with `created_at_i` instead.

Headers: none required. Response has no rate-limit headers at all. Measured: about 80 requests over 25 minutes at 0.4-1.0 s spacing, zero throttling, latency 0.19-0.40 s (1.2 s for a 1000-hit page). The 10,000 requests/hour/IP figure is from memory of Algolia's docs and was NOT verifiable today (`hn.algolia.com/api` now serves an 88-character JS shell). Budget 1 req/s and you will never find out.

### 1.2 Fields worth keeping (per hit, path `hits[]`)

`objectID` (= HN item id, string), `story_id`, `title`, `url` (null on text posts), `story_text` (HTML, present on text posts and most Launch HN), `author`, `points`, `num_comments`, `created_at` (ISO), `created_at_i` (unix), `updated_at` (last index refresh), `_tags[]` (contains `show_hn` / `launch_hn` / `author_x` / `story_id`). Top level: `nbHits`, `nbPages`, `hitsPerPage`, `page`, `exhaustiveNbHits`.

Drop `_highlightResult` (it doubles payload size).

### 1.3 Queries actually run and counts (2026-10-01)

Baseline volume, no keyword:

| Query | nbHits |
|---|---|
| `tags=show_hn`, last 90 d | 11,424 |
| `tags=show_hn`, last 24 h | 160 |
| `tags=show_hn`, last 90 d, `points>=50` | 324 (2.8 %) |
| `tags=launch_hn`, last 90 d | 33 |
| `tags=launch_hn`, last 365 d | 104 |

Effect of matching options on `query=robot&tags=show_hn`, 90 d:

| Options | nbHits | Comment |
|---|---|---|
| default | 134 | top hit was a "banyan tree" post: typo tolerance matched `root` in story_text |
| `typoTolerance=false` | 72 | |
| `restrictSearchableAttributes=title` | 53 | |
| title + `typoTolerance=false` | 40 | use this |

Thesis keyword matrix: `tags=(show_hn,launch_hn)`, 90 d, title-only, typo off, `hitsPerPage=50`. Format `keyword: nbHits (of which >=10 points)`:

robot 43 (8), robotics 11 (5), humanoid 2 (1), drone 11 (2), UAV 0, autonomous 38 (1), defense 5 (0), nuclear 3 (0), fusion 4 (0), battery 5 (0), grid 28 (4), manufacturing 6 (1), CNC 3 (0), PCB 5 (2), semiconductor 0, chip 7 (2), FPGA 3 (1), satellite 4 (3), rocket 1 (1), space 37 (10), hardware 30 (2), sensor 6 (0), lidar 3 (0), motor 2 (0), actuator 0, "3D printed" 5 (0), "open-source hardware" 2 (0), firmware 2 (0), embedded 12 (0), RISC-V 3 (1), solar 12 (2), reactor 1 (1), machining 0, factory 13 (2), teleoperation 0, ROS 11 (2), exoskeleton 0, radar 7 (0), welding 0, mining 0.

298 unique stories across all 40 keywords. Honest read: precision is poor for the ambiguous words. `space` is mostly "latent space", "color space", space games. `grid` is CSS/data grids. `autonomous` is coding agents. `chip` is CHIP-8 and chiptune. `factory` is "software factory". `fusion` and `reactor` are mostly software names. The clean ones are `robot`, `robotics`, `humanoid`, `drone`, `PCB`, `FPGA`, `lidar`, `satellite`, `RISC-V`, `ROS`, `manufacturing`. A second-stage classifier on title + story_text + landing page is mandatory.

`optionalWords` OR query (`robot drone humanoid satellite`): 48 hits in one call, good for cheap polling.

Launch HN thesis hits seen in the 365-day pull (title regex): Nori Robotics, Hebbian Robotics, Salem Robotics, Mireye, Discovered Materials, ProvenMetal, Voltair, OctaPulse, Constellation Space.

Entity lookups that worked: `query=norirobotics.com&restrictSearchableAttributes=url` returned exactly the launch post; `query=github.com/Hebbian-Robotics` on `url` returned 3 posts including two earlier 1-2 point submissions by the same org (a pre-launch trail).

### 1.4 Firebase HN API (live score sampling)

Base `https://hacker-news.firebaseio.com/v0`, GET, no auth, `Cache-Control: no-cache`, no rate-limit headers. Latency 0.08-0.40 s.

| Endpoint | Returned today |
|---|---|
| `/item/{id}.json` | `by, descendants, id, kids[], score, text, time, title, type, url` |
| `/user/{id}.json` | `about, created, id, karma, submitted[]` |
| `/showstories.json` | 135 ids (ranked Show HN page) |
| `/newstories.json` | 500 ids |
| `/topstories.json` | 500 ids |
| `/beststories.json` | 200 ids |
| `/askstories.json` | 25, `/jobstories.json` 31 |
| `/maxitem.json` | 49928399 |
| `/updates.json` | `{items: 58, profiles: 11}` (changed in the last few minutes) |

Algolia vs Firebase freshness, measured on four stories aged 3.6 h to 30 days: `points` and `num_comments` were identical in all four. Algolia's `updated_at` was within about 15 minutes of now for the fresh ones. So Algolia alone is good enough for hourly velocity; use Firebase only when you want sub-15-minute sampling of a story's first hours, or rank position (`showstories.json` index).

Neither API gives a points history. Velocity requires storing your own samples.

### 1.5 Features to compute

- `hn_points`, `hn_comments`, `hn_comment_ratio = comments / max(points,1)` (high ratio = controversy or real technical discussion).
- `hn_velocity_1h/6h/24h`: delta points between your own samples divided by elapsed hours. Sample new thesis-matching stories at t+1h, 3h, 6h, 24h.
- `hn_acceleration`: velocity(6h) minus velocity(1h).
- `hn_peak_rank`: best index seen in `showstories.json` / `topstories.json`.
- `hn_percentile`: points vs the 90-day Show HN distribution (>=50 points is roughly top 3 %).
- `hn_is_launch_hn`, `yc_batch_from_title` (regex `\(YC ([A-Z]\d{2})\)`; saw W, S, F, X, P prefixes).
- `hn_author_karma`, `hn_author_age_days`, `hn_author_first_submission` (karma 101 / 1 submission for the Nori founder: a new account that still hit 201 points is a strong signal).
- `hn_prior_submissions_for_domain`: count of earlier stories whose `url` matches the same domain or GitHub org.
- `hn_repeat_show`: same author posting 2+ thesis Show HNs in 180 days.
- `thesis_match_score`: classifier output on title + story_text, because keyword precision is low.

### 1.6 Entity resolution

- Company domain: registrable domain of `url`. If `url` is null (most Launch HN posts and many Show HN), take the first `<a href>` in `story_text` (HTML-escaped: `&#x2F;` is `/`).
- GitHub org/repo: when `url` host is `github.com`, org = path segment 1. Also `*.github.io` maps to a user/org.
- Person: `author` then `/user/{author}.json` `about` (free text; founders often put full name, company, bio there). Launch HN `story_text` usually opens "I'm {first name} from {company}".
- YC join key: batch code in title + company name, then match to the YC directory by name within that batch.

---

## 2. YC company directory: `yc-oss.github.io/api` (open mirror)

### 2.1 Liveness and freshness

Alive. `meta.json` `last_updated` = `2026-10-01T03:12:16.858Z`; GitHub repo `yc-oss/api` `pushed_at` 2026-10-01T03:12:29Z with a daily "Update APIs: YYYY-MM-DD" commit for each of the last 5 days checked. Newest `launched_at` in the data: 2026-09-30 16:27 UTC. So lag is under 24 h. It is built by a GitHub Action from YC's own Algolia index and only includes companies with a public YC page.

### 2.2 Endpoints (GitHub Pages, GET, no auth)

| URL | Size today | Content |
|---|---|---|
| `/api/meta.json` | 67 KB | `last_updated`, plus maps `companies{9}`, `batches{51}`, `industries{59}`, `tags{337}`, each `{name, count, api}` |
| `/api/companies/all.json` | 10.5 MB | array of 6,268 companies |
| `/api/companies/{top,hiring,nonprofit,...}.json` | hiring = 2.6 MB | filtered arrays |
| `/api/batches/{season}-{year}.json` | 170-400 KB | e.g. `fall-2026`, `summer-2026`, `spring-2026`, `winter-2026` |
| `/api/industries/{slug}.json` | `industrials` = 826 KB | |
| `/api/tags/{slug}.json` | `robotics` 265 KB, `hard-tech` 247 KB | |
| `/api/batches/{batch-slug}/{company-slug}.json` | 2 KB | single company (use the record's own `api` field, do not guess the slug) |
| `/api/changes/latest.json` | 58 KB | daily diff: `summary{previous_total,current_total,added,removed,updated}`, `added[]` (full records), `removed[]`, `updated[]` with `changed_fields[]` and `changes{field:{before,after}}` |

Headers: `Cache-Control: max-age=600`, `ETag`, `Last-Modified`. Conditional GET with `If-None-Match` returned 304. No rate-limit headers (GitHub Pages; soft limits only). One fetch of `all.json` per day is all that is needed.

### 2.3 Fields (every record has all 29)

`id, name, slug, former_names[], small_logo_thumb_url, website, all_locations, long_description, one_liner, team_size, industry, subindustry, launched_at (unix seconds), tags[], tags_highlighted[], top_company, isHiring, nonprofit, batch ("Summer 2026"), status, industries[], regions[], stage, app_video_public, demo_day_video_public, app_answers, question_answers, url (YC page), api`.

No founders, no LinkedIn, no GitHub. Those come from the YC company page (section 4).

### 2.4 Counts: thesis companies in the most recent 3 batches

The directory also holds two placeholder batches with one company each ("Winter 2027": Rote; "Summer 2027": Memorable). The three most recent real batches:

| Batch | Companies | Hard Tech | Robotics | Aerospace | Defense | Energy | Manufacturing | Hardware | Any of those 7 tags | Any thesis tag OR industry = Industrials |
|---|---|---|---|---|---|---|---|---|---|---|
| Fall 2026 (still filling) | 108 | 8 | 13 | 3 | 1 | 4 | 8 | 8 | 30 | 34 |
| Summer 2026 | 231 | 27 | 28 | 5 | 8 | 9 | 16 | 22 | 70 | 79 |
| Spring 2026 | 194 | 4 | 6 | 2 | 2 | 0 | 2 | 4 | 11 | 27 |
| Total | 533 | 39 | 47 | 10 | 11 | 13 | 26 | 34 | 111 | 140 |

Other thesis tags in those 533: Drones 9, Semiconductors 9, Satellites 3, Advanced Materials 4, Electronics 4. Subindustry counts: "Industrials -> Manufacturing and Robotics" 53, "Industrials -> Defense" 14, "Industrials -> Aviation and Space" 8, "Industrials -> Energy" 8.

So about 26 % of the last three batches (140 / 533) is physical-world, and Summer 2026 alone is 34 %.

### 2.5 `changes/latest.json` (the novelty feed)

Today: 4 added, 1 removed, 54 updated. `changed_fields` frequency today: long_description 18, all_locations 11, one_liner 11, tags 11, stage 9, isHiring 7, team_size 6, website 4, name 3. Real examples seen: `Micora (Fall 2026) team_size 3 -> 4`, `Simantic website simantic.dev -> simantic.com`, `Rex isHiring false -> true`. It only ever holds the latest day, so persist it daily.

### 2.6 Features

- `yc_days_since_listed = now - launched_at` (first public appearance; this is directory publish time, not batch start).
- `yc_team_size`, `yc_team_size_delta_30d` (from stored daily diffs), `yc_is_hiring_flip` (false -> true).
- `yc_thesis_tag_count`, `yc_subindustry`.
- `yc_one_liner_rewrite_count`, `yc_website_changed` (pivot / rebrand markers).
- `yc_batch_recency` and `yc_stage` ("Early").
- `yc_listed_before_launch_post`: `launched_at` precedes any YC Launch or Launch HN post (the widest pre-consensus window; for Nori: directory 2026-07-03, YC Launch 2026-08-20, Launch HN 2026-09-01).

### 2.7 Entity resolution

Primary key `id` (stable integer; `slug` and `api` do change, both appeared in today's `changed_fields`). Domain from `website`. Name collisions are real: two companies called "Nori" (`nori`, Fall 2025, nori.ai, health; `noril1`, Summer 2026, norirobotics.com, humanoid). Always key on `id` or (`name`, `batch`).

---

## 3. YC Launches JSON (undocumented, works)

`GET https://www.ycombinator.com/launches` returns JSON when the request sends `Accept: application/json` (or `*/*`, curl's default). With `Accept: text/html` it returns the HTML page. Python `urllib` sends no Accept header and got HTML.

| Param | Verified behaviour |
|---|---|
| `page` | zero-based; 20 per page by default |
| `hitsPerPage` | 100 works (nbPages becomes 10) |
| `query` | full-text; `robot` = 224 hits |
| `batch` | `batch=Fall 2026` = 14 hits |
| `industry`, `sort` | ignored (same result as no param) |

Envelope: `{hits[], nbHits, page, nbPages, hitsPerPage}` (Algolia-shaped). Unfiltered `nbHits` = 3,297 but only 1,000 are reachable (page 49 at 20/page ends at 2025-11-06; page 50 returns `nbHits: 0`). Use `query` or `batch` to reach older ones.

Hit fields: `id, title, tagline, slug, created_at (ISO), total_vote_count, search_path (public URL), company{id, name, url (company website), slug, logo, tags[], batch, industry}`.

Queries run with `hitsPerPage=100` (nbHits / of which created since 2026-07-01 within the first 100): robot 224/46, robotics 167/38, humanoid 27/8, drone 60/18, defense 76/21, nuclear 18/10, fusion 14/2, battery 34/5, energy 140/27, manufacturing 155/30, semiconductor 25/5, chip 63/13, satellite 56/12, space 343/43, hardware 225/46. Union: 517 unique launches, 88 of them since 2026-06-01 with a thesis tag or industry = Industrials.

Vote counts seen among thesis launches (useful scale): Stoa 777, DeepReach 470, Libra Robotics 374, HERA 138, Baud 98, Kara 85, Hebbian Robotics 81, Neuromorphic 73, Lamb Labs 64, Shiraz AI 62, Synapse Semiconductor 56. Across those 88 thesis launches: median 11 votes, p75 28, p90 64, and 14 of 88 at 50 or more. So anything above about 50 is a breakout inside the YC audience.

Rate limits: none advertised. About 30 requests at 1.2-1.5 s spacing, all 200, latency 0.3-0.7 s. Behind Cloudflare (`cf-cache-status: DYNAMIC`). One Python `urllib` request died with a TLS handshake timeout; curl never did. Use curl or `requests`/`httpx` with an explicit Accept header and a retry.

Features: `yc_launch_votes`, `yc_launch_votes_percentile_in_batch`, `yc_launch_count` (companies that launch twice: Hop Aero, Ultrasonium, Edviro each had 2), `days_directory_to_launch`, `launch_title_keywords`.

Join: `company.id` equals the yc-oss `id`.

---

## 4. YC company page (founder enrichment)

`GET https://www.ycombinator.com/companies/{slug}` returns HTML containing `<div data-page="...">`. HTML-unescape the attribute and `json.loads` it. `robots.txt` allows `/companies/{slug}` and disallows only `/companies?*`.

Paths that matter under `props`:

- `company.{id, slug, name, batch_name, website, year_founded, team_size, location, city, country, linkedin_url, twitter_url, github_url, cb_url, tags[], ycdc_status, primary_group_partner{full_name}}`
- `company.founders[].{user_id, full_name, title, founder_bio, twitter_url, linkedin_url, is_active, latest_yc_company}`
- `launches[].{id, title, tagline, body (markdown), created_at, total_vote_count, url}`
- `newsItems[].{title, url, date}`, `jobPostings[]`

Measured 2 requests, 105-110 KB each, 0.28 s. Avatar URLs carry short-lived signed query strings: strip everything after `?` before storing (done in the fixture).

This is the only keyless place found for YC founder names + LinkedIn + X. Fetch it only for companies that pass the thesis filter (about 140 for the last three batches), at 1 req / 1.5 s.

---

## 5. Product Hunt

| Attempt | Result |
|---|---|
| `GET https://www.producthunt.com/feed` | 200, Atom, 50 entries, `Cache-Control: public, max-age=30` |
| `GET /feed?category={topic-slug}` | 200, a different 50 entries. Verified distinct feeds for `hardware`, `robots`, `drones`, `space`, `climate-tech`, `wearables`, `tech` (0-3 overlap with the default feed) |
| `GET /feed?category=robotics`, `3d-printing`, or any unknown slug | 200 but silently identical to the default feed (50/50 overlap). Detect by comparing entry ids with the default feed |
| `GET /feed?page=2` | ignored, same 50 |
| `/feed.atom`, `/topics/hardware/feed` | 404 |
| `POST https://api.producthunt.com/v2/api/graphql` without token | 401 `invalid_oauth_token` |
| `GET https://www.producthunt.com/frontend/graphql` | 200 `{"errors":[{"message":"This request could not be processed"}]}` |

Entry fields: `id` (`tag:www.producthunt.com,2005:Post/{post_id}`), `title`, `published`, `updated`, `link@href` (product page), `author/name` (the hunter, not necessarily the maker), `content` (HTML: tagline in the first `<p>`, then a `/r/p/{post_id}` redirect link to the maker's site).

Limits of the feed: no vote count, no comment count, no topics, no maker, no pagination, and category feeds are not chronological (the `drones` feed reaches back to 2018, `space` to 2021). So it gives existence + date + tagline only. Votes need the OAuth GraphQL API (free developer token, but that requires an account, which is out of bounds here).

Thesis fit is weak: the `hardware` feed today was phones, keyboards, a Dyson product, a Steam device. Two relevant items seen: PewCB (desktop PCB factory) and Autonomyware. Salem Robotics (YC S26) showed up in `climate-tech`. Treat PH as a low-weight corroboration signal (`ph_listed`, `ph_first_seen`), not a discovery source.

---

## 6. Accelerator and fund lists with real machine-readable data

### 6.1 a16z speedrun (undocumented public API)

Found by reading `__NEXT_DATA__` on `https://speedrun.a16z.com/companies`.

- List: `GET https://speedrun-api.a16z.com/api/companies/companies/?limit={n}&offset={k}&ordering=name`. Envelope `{count, next, previous, results[]}` (Django REST). `limit=400` returned all 321 in one call (363 KB, 1.3 s).
- Filters verified: `cohort=SR007` (79), `industry=Robotics` (16). `industries=` (plural) is silently ignored.
- Detail: `GET .../companies/{uuid}/` works; `.../companies/{slug}/` is 404. Use `id` from the list.
- Response headers: `allow: GET, HEAD, OPTIONS`, no rate-limit headers.

List fields: `id (uuid), slug, name, cohort ("SR001".."SR007"), preamble, industries[], team_size, city, state, country, region, founder_set[].{id, profile_pic}`.
Detail adds: `description, founded_year, website_url, github_url, x_url, linkedin_url, demo_day_video_url, founder_set[].{first_name, last_name, title, linkedin_url, slug}`.

Cohort sizes: SR001 25, SR002 25, SR003 32, SR004 42, SR005 59, SR006 59, SR007 79. Thesis-ish industries (Robotics, Hardware, Gov Tech / Defense, Space Tech, Climate / Energy, Manufacturing / Industrials, Deep Tech, Transportation, Construction, Supply Chain): 57 of 321, with 23 in SR007 and 13 in SR006. speedrun has shifted from games toward physical-world companies; it is a real feed for this thesis.

No dates on records. `cohort` is the only time proxy, so diff the list daily to get `first_seen`.

Public page for evidence: `https://speedrun.a16z.com/companies/{slug}`.

### 6.2 HAX and SOSV (WordPress REST with custom post types)

- `GET https://hax.co/wp-json/wp/v2/company?per_page=100&page={p}&orderby=date&order=desc` : `X-WP-Total: 203`, 21 pages at 10.
- `GET https://hax.co/wp-json/wp/v2/founder?...` : `X-WP-Total: 532`.
- `GET https://sosv.com/wp-json/wp/v2/company?...` : `X-WP-Total: 786` (HAX + IndieBio + others). Filter `tx_program=2633` (`sosv-hax`) = 214.
- Taxonomies: `/wp-json/wp/v2/tx_cohort` (e.g. `hax-seed-2024` 24, `hax-seed-2025` 17, `hax-seed-2026` 3), `tx_category` (`hard-tech` 172, `climate-tech` 77, `industrial-iot` 62, `manufacturing-energy` 58, `energy` 33), `tx_program`, `tx_location`, `tx_stage`.
- Standard WP params verified: `per_page` max 100 (101 returns 400), `after=ISO` (27 companies since 2026-01-01), `modified_after=ISO` (37 since 2026-09-01), `_fields=` to shrink payloads.

Company fields: `id, date, modified, slug, link, title.rendered, content.rendered, excerpt.rendered, class_list[]` (carries readable taxonomy slugs: `tx_cohort-hax-seed-2025`, `tx_location-houston-texas`, `tx_program-sosv-hax`), `acf.{tagline, founded_year, website, linked_in, twitter, crunchbase, now_raising (bool), total_capital_raised, employee_count_range, portal_id}`.
Founder fields: `title.rendered` (name), `class_list[]` containing `tx_company-{slug}` (the join to company), `acf.{position, linked_in, twitter}`.

sosv.com is fresher than hax.co: it listed TopologiQ (2026-08-31), RB-Ware and Diamond Quanta (2026-08-28) as `hax-seed-2026`, none of which were on hax.co yet. Poll sosv.com with `tx_program=2633` as primary and hax.co as a cross-check.

Features: `hax_cohort`, `hax_now_raising` (explicit "raising now" flag, rare and valuable), `hax_first_seen` (your own first-seen date; see gotcha on `date`), `founder_count`, `has_linkedin`.

### 6.3 News feeds that work (corroboration, not discovery)

| Feed | URL | Result |
|---|---|---|
| TechCrunch main | `https://techcrunch.com/feed/` | 200, 20 items |
| TechCrunch by tag | `https://techcrunch.com/tag/{slug}/feed/` | 200, 20 items (`robotics` verified) |
| TechCrunch by category | `https://techcrunch.com/category/{slug}/feed/` | 200 (`hardware`, `space` verified) |
| TechCrunch WP REST | `https://techcrunch.com/wp-json/wp/v2/posts?search={q}&after={ISO}&per_page={n}&_fields=id,date,link,title` | 200, `X-WP-Total` header. Tag ids: robotics 13426, drones 137903, defense-tech 1926267, semiconductors 284207, batteries 24973, fusion 53189, nuclear 29481, humanoid-robots 577337380. Category ids: robotics 577123751, space 174, hardware 449223024, climate 576957003, startups 20429, fundraising 577234943 |
| Hackaday blog | `https://hackaday.com/blog/feed/` | 200, only 7 items; `https://hackaday.com/wp-json/wp/v2/posts?search=drone` works (`X-WP-Total: 1141`) |
| Hackaday.io projects | `https://hackaday.io/projects?sort=date` | 200 HTML, 17 `/project/{id}-{slug}` links, scrape only. `api.hackaday.io/v1` returns 400 "API key missing" |
| The Robot Report | `https://www.therobotreport.com/feed/` | 200, 15 items |
| SpaceNews | `https://spacenews.com/feed/` | 200, 16 items |
| IEEE Spectrum robotics | `https://spectrum.ieee.org/feeds/topic/robotics.rss` | 200, 30 items |
| Breaking Defense | `https://breakingdefense.com/feed/` | 200, 15 items |
| Lobsters hardware tag | `https://lobste.rs/t/hardware.json` | 200, 25 stories with `score`, `comment_count`, `url`, `tags` (hobbyist, low startup density) |
| Activate news | `https://activate.org/news/rss.xml` | 200, 10 items; includes monthly "Activate Fellow News" and "Hard Tech to Market" profiles (Reforge Robotics, Lightfinder, Semion seen today) |

Use these as `press_mention_count` and as a consensus penalty: a company already in TechCrunch with a round size is no longer pre-consensus.

---

## 7. Lists that are NOT machine-readable today

| Program | What was tried | Verdict |
|---|---|---|
| Activate fellows | `activate.org/activate-fellows` embeds an iframe to `https://activate-fellows-index.softr.app/`. The page config names an Airtable base (`app5iw89U8jzOf1tF`, table `Fellows`) but data loads through Softr's private endpoints. Two guessed endpoint shapes returned 400. | No keyless list. Needs a headless browser render, or parse the news RSS above for names. |
| Breakthrough Energy Fellows | `/fellows/` and `/programs/fellows` both soft-redirect to a generic SPA shell. Every page embeds `window.__INITIAL_STATE__` (Kontent.ai) with 103 `company` items (fields `title, description, tags, technologies, url, cohort_number`), but these are the BEV portfolio (Fervo, QuantumScape, Zap Energy, Redwood...) and `cohort_number` is null on all 103. | Fellows list not present. The portfolio JSON is parseable but late-stage: use as a "known" list only. |
| Thiel Fellows | `thielfellowship.org` is a Nuxt site with only `/`, `/faq`, `/jobs`. `/fellows` is 404. `api.thielfellowship.org/openapi.json` is public but only exposes applicant-submission endpoints (not touched). `/jobs` lists fellow companies that are hiring, static, mostly old (Figma, Luminar, Plaid...). | No list of fellows or classes. Classes are announced by press release only. |
| AFWERX | `afwerx.com/wp-json/wp/v2/types` returns 401 `rest_not_logged_in`. `afwerx.com/feed/` returns one item, "Hello world!" from 2023. | Nothing. The SBIR.gov API (`api.www.sbir.gov/public/api/awards`, `/firm`) returned 403 `{"message":"Forbidden"}` on every call, with both the contact UA and curl's default UA. USAspending (`POST https://api.usaspending.gov/api/v2/search/spending_by_award/`) is keyless and answered, but took 31 s and a keyword search for "AFWERX" returned 1 award. It belongs to a contracts/grants card with a proper sub-agency + SBIR filter, not here. |
| Starburst | `starburst.aero/portfolio/` is server-rendered HTML with name, founders, category, location and product blurb per startup, but only the first few are in the initial HTML, there are no dates, and `/wp-json/wp/v2/types` exposes only `post` and `page` (latest post is from 2022). | Scrape-only, undated, partial. Low value. |
| a16z American Dynamism | `a16z.com/wp-json/wp/v2/types` is 404 (REST disabled). `a16z.com/american-dynamism-50/` redirects to `/american-dynamism-50-2025/`, a static page whose outbound links are about 50 company domains (anduril.com, hadrian.co, saronic.com, castelion.com, radiantnuclear.com...). | Parseable once by extracting external hrefs, but it is an annual list of already-famous companies. Use as a consensus/known list. |
| Crowd Supply | `/browse.atom`, `/feed` | 404 |

---

## 8. Gotchas (all hit today)

1. Algolia `numericFilters=created_at_i>N` must be URL-encoded. A raw `>` in the URL returns HTTP 400 with an HTML body. Use `curl -G --data-urlencode` or `urllib.parse.urlencode`.
2. Algolia typo tolerance is on by default and searches `story_text`: `robot` matched `root`. Always send `typoTolerance=false&restrictSearchableAttributes=title` for keyword screens.
3. `tags=story_{id}` matches the story and every comment under it; with `search_by_date` the first hit is a comment with `points: null`. Use `tags=story,story_{id}`.
4. Algolia caps every query at 1000 hits and returns HTTP 200 with an empty `hits` plus a `message` when you page past it. There are 11,424 Show HN posts per 90 days, so an unfiltered backfill must walk `created_at_i` windows of about 5 days.
5. `url` is null on most Launch HN posts and many Show HN posts. The company link is inside `story_text` and is HTML-entity-escaped.
6. Thesis keywords are heavily polluted on HN (`space`, `grid`, `chip`, `factory`, `fusion`, `reactor`, `autonomous`, `hardware`). Do not score on keyword hit alone.
7. YC directory: Spring 2026 companies are under-tagged (only 11 of 194 carry a core thesis tag, but 25 are industry = Industrials, many with `tags: []`). Filter on `industry`/`subindustry` OR tags, never tags alone.
8. YC directory: `team_size` can be `null` or `0`; `website` can be empty (Mantle, Fall 2026). `launched_at` is page publish time and can be years before the batch (Baud: Summer 2026 batch, `launched_at` 2023).
9. YC directory: duplicate names across batches (two "Nori"). `slug` gets suffixes (`noril1`, `reframe-2`, `ohm-2`) and can change. Key on `id`.
10. YC directory: placeholder batches "Winter 2027" and "Summer 2027" exist with one company each; do not treat them as the latest batch.
11. yc-oss `changes/latest.json` is overwritten daily. Miss a day and that diff is gone (recoverable only from the repo's git history).
12. YC Launches JSON depends on the Accept header. No Accept header (Python urllib default) yields HTML. `industry=` and `sort=` are silently ignored. 1000-hit ceiling.
13. One Python urllib call to ycombinator.com failed with a TLS handshake timeout while curl was fine. Add retries; prefer curl/httpx.
14. YC company page avatar URLs include temporary signed AWS query strings. Strip them; never persist them.
15. Product Hunt `?category=` with an unknown slug returns the default feed with HTTP 200. `robotics` is not a valid slug, `robots` is.
16. Product Hunt category feeds are not in date order and include posts years old; dedupe on post id and use `published`.
17. speedrun API: `industries=` (plural) is ignored and returns everything; the working filter is `industry=`. Detail lookups need the UUID, not the slug. No timestamps on records.
18. HAX/SOSV `date` is the CMS record creation date, not the investment date: 15+ HAX companies share 2026-03-16/17 (a bulk import), and sosv.com shows "Robotic Actuators Company" and "Halltech B.V." twice (2026-03-24 and 2026-04-29). Dedupe on `acf.portal_id` or slug and rely on `tx_cohort` for vintage.
19. HAX `acf.website` is sometimes empty and `founded_year` is sometimes blank.
20. Breakthrough Energy returns HTTP 200 with the same 940 KB shell for unknown routes (soft 404). Do not trust status codes there.
21. SBIR.gov public API is 403 for every request today, regardless of User-Agent.
22. zsh treats `===` at the start of an `echo` argument as an expansion; quote it in shell scripts (cost one failed run).

---

## 9. Cross-source entity resolution (how the pieces join)

| Key | HN | yc-oss | YC launches | YC page | speedrun | HAX/SOSV | Product Hunt |
|---|---|---|---|---|---|---|---|
| Registrable domain | `url` or first link in `story_text` | `website` | `company.url` | `company.website` | `website_url` (detail) | `acf.website` | via `/r/p/{id}` redirect (not followed) |
| Company name | title after "Show HN:" / "Launch HN:" | `name`, `former_names[]` | `company.name` | `company.name` | `name` | `title.rendered` | `title` |
| Stable id | `objectID` | `id` | `company.id` (same as yc-oss) | `company.id` | `id` (uuid) | `id`, `acf.portal_id` | post id in `id` |
| GitHub org | `url` host github.com | none | none | `company.github_url` | `github_url` (detail) | none | none |
| Founder name | `author` then `/user` `about` | none | none | `founders[].full_name` | `founder_set[].first_name/last_name` | `/founder` `title.rendered` + `tx_company-{slug}` | `author/name` (hunter) |
| Founder LinkedIn / X | none | none | none | `founders[].linkedin_url`, `twitter_url` | `founder_set[].linkedin_url` | `acf.linked_in`, `acf.twitter` | none |
| Company LinkedIn / X | none | none | none | `company.linkedin_url`, `twitter_url` | `linkedin_url`, `x_url` | `acf.linked_in`, `acf.twitter` | none |
| Batch / cohort | regex `(YC X26)` in title | `batch` | `company.batch` | `company.batch_name` | `cohort` | `tx_cohort-*` in `class_list` | none |

Recommended canonical key: lower-cased registrable domain with `www.` stripped, after following one redirect for `http://` sites. Fall back to (normalised name, program, cohort). Watch for website changes (yc-oss diffs showed 4 website changes in one day), so keep a domain alias table.

---

## 10. Composite features for the scorer

- `launch_surface_count`: number of distinct surfaces a company has appeared on (YC directory, YC launch, Launch HN, Show HN, PH, speedrun, HAX). 1 = earliest; 3+ = it is spreading.
- `first_seen_ts`: min over all sources; `days_since_first_seen`.
- `lead_time_to_press_days`: first TechCrunch/RobotReport mention minus `first_seen_ts` (negative or null = still pre-consensus).
- `attention_velocity`: HN points/hour in the first 6 h, YC launch votes relative to batch median.
- `attention_acceleration`: change in velocity between consecutive samples.
- `novelty`: 1 if the domain has no prior HN story, no TechCrunch hit, and is not on the a16z AD50 / BEV portfolio known lists.
- `team_smallness`: `team_size <= 4` (most thesis YC companies seen today are 2-4 people).
- `team_growth`: team_size delta from yc-oss diffs; `isHiring` flip.
- `technical_founder_evidence`: HN `about` or YC `founder_bio` mentions lab, PhD, prior hardware company; GitHub org present.
- `raising_flag`: HAX `acf.now_raising`.
- `thesis_bucket`: robotics / drones-autonomy / defense / energy / manufacturing / semis / space, from tags + subindustry + classifier.
- `consensus_penalty`: already on AD50 or BEV portfolio list, or TechCrunch article with a round size.

---

## 11. Polling plan (polite defaults)

| Source | Cadence | Calls per run |
|---|---|---|
| yc-oss `meta.json` (ETag) then `all.json` + `changes/latest.json` | daily after 03:30 UTC | 3 |
| YC launches `?hitsPerPage=100&page=0` | every 6 h | 1 |
| YC company page | once per newly seen thesis company | about 5 per day |
| HN Algolia `(show_hn,launch_hn)` last 2 h, no keyword, `hitsPerPage=200` | hourly | 1 |
| HN re-sample thesis-matched stories under 48 h old | hourly | 5-20 |
| speedrun `?limit=400` | daily | 1 (+ detail per new id) |
| sosv.com `company?tx_program=2633&modified_after=` and hax.co `company?modified_after=` | daily | 2 |
| Product Hunt `hardware`, `robots`, `drones`, `space`, `climate-tech` | daily | 5 |
| RSS press feeds | every 6 h | 8 |

Under 300 requests per day in total.

---

## 12. Fixtures

All in `/Users/noel/antifund/pipeline/fixtures/launches/`. Each JSON file carries a `_fixture_note` with the exact request and what was trimmed, except the Firebase item, which is the raw object from `GET /v0/item/49525153.json` with `kids` cut to 10.

| File | Bytes | Shape |
|---|---|---|
| `hn_algolia_launch_hn_365d.json` | 136,922 | Algolia envelope, first 25 of 104 Launch HN hits |
| `hn_algolia_show_hn_thesis_90d.json` | 79,216 | top 40 thesis-keyword Show/Launch HN hits by points; first hit keeps `_highlightResult` |
| `hn_firebase_item_49525153.json` | 3,277 | Firebase item (Nori Launch HN), `kids` cut to 10 |
| `yc_oss_recent_batches_thesis.json` | 75,816 | 45 unmodified yc-oss company records |
| `yc_oss_changes_latest.json` | 14,146 | daily diff, `updated` cut to 12 |
| `yc_launches_query_robot.json` | 28,903 | YC launches envelope, 30 hits |
| `yc_company_page_noril1.json` | 6,941 | parsed `data-page` company + founders + launches |
| `a16z_speedrun_companies_robotics.json` | 21,490 | full `industry=Robotics` response (16) |
| `hax_wp_company_founder.json` | 29,978 | 10 companies + 5 founders, Yoast/_links removed |
| `producthunt_feed_category_hardware.atom.xml` | 14,107 | Atom, 15 of 50 entries |
