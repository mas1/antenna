# Source card: `traffic` — web traffic and domain momentum (free only)

Measured live from this machine on 2026-10-01 23:56Z to 2026-10-02 00:30Z. Every number below is from a real response in that window, not from memory.
User-Agent on every call: `antifund-sourcing-research/0.1 (mason@alterity.systems)`. No keys, no accounts, no logins.

Fixtures: `/Users/noel/antifund/pipeline/fixtures/traffic/`
- `tranco_ranks_domain_skild.ai.json` — raw Tranco per-domain rank history
- `certspotter_issuances_neros.tech.json` — raw Certificate Transparency issuances
- `domain_probe_bundle.json` — raw RDAP, Wayback sparkline, Wayback CDX (2 shapes), DoH MX/TXT/DMARC, HackerTarget, subdomain.center, Majestic row, Tranco list meta, plus a derived 69-domain panel matrix (`panel_69_domains`) joining every signal below

## Verdict in one paragraph

Classic "traffic rank" lists are an enrichment signal, not a discovery signal. The public top-1M lists miss most early hard-tech startups (4 of 15 in my panel), but the **Tranco full list (4.57M domains, free daily CSV)** catches 12 of 15 and gives a daily rank you can diff across any two dates back to 2019. Rank deltas in the 500k to 3M band are real and discriminating (skild.ai went 1,166,698 to 442,601 in 39 days; the cohort median barely moves). Umbrella and CrUX are useless for tiny companies (0/15 and 1/15). Cloudflare Radar and the CrUX API need keys. For companies too small for any list, the cheap proxies work extremely well and are the sharper edge: **Certificate Transparency** (new subdomains reveal factories, sites, PLM, customer portals), **DNS TXT/MX fingerprints** (22 of 69 hard-tech domains are on Microsoft 365 GCC High, a direct defense-contracting tell), **RDAP** (70/70 success) and **Wayback** (new-URL feed reads like a press-release and referrer log). crt.sh was down (502) for the entire session; Cert Spotter is the working substitute but only returns unexpired certs at about 10 full-domain queries per hour, so CT must be polled and stored.

## 1. Tranco (works, no auth) — primary list source

| What | URL template | Notes |
|---|---|---|
| Latest list metadata | `GET https://tranco-list.eu/api/lists/date/latest` | JSON: `list_id`, `created_on`, `download`, `configuration.providers` |
| List metadata by date | `GET https://tranco-list.eu/api/lists/date/{YYYY-MM-DD}` | Works back to at least 2019-06-01 (returned `3N5L`) |
| Top 1M, latest (zip) | `GET https://tranco-list.eu/top-1m.csv.zip` | 302 to `/download/daily/top-1m.csv.zip`; 9.7 MB, 1.3 s |
| Top 1M by list id (csv) | `GET https://tranco-list.eu/download/{list_id}/1000000` | Uncompressed, 22.6 MB |
| **Full list by list id (csv)** | `GET https://tranco-list.eu/download/{list_id}/full` | Uncompressed, ~100-110 MB, 2-4 s. **This is the one to use.** |
| Per-domain rank history | `GET https://tranco-list.eu/api/ranks/domain/{domain}` | JSON `{"domain":..., "ranks":[{"date","rank"}...]}`, newest first |

Measured:
- Latest list `Q2K34`, `created_on` 2026-09-30T22:00:02, providers `crux, farsight, majestic, radar, umbrella`, 30-day window (2026-09-01 to 2026-09-30), Dowdall combination, `filterPLD: on` (registrable domains only, no subdomains).
- Full list sizes: 2026-09-30 `Q2K34` = 4,566,870 rows; 2026-09-01 `K9QPW` = 4,298,032; 2026-07-01 `JZ2VY` = 4,451,991; 2025-10-01 `9WZV2` = 4,063,027. CSV format `rank,domain`, no header.
- Per-domain API returns **39 daily points** (2026-08-24 to 2026-10-01), and **includes ranks beyond 1M** (valaratomics.com 1,933,439 to 1,560,102). `ranks: []` with HTTP 200 means not in the full list (thea.energy, cx2.com, neros.tech).
- Rate limit (per-domain API): back-to-back calls returned 429 on 3 of 5; at 1.2 s spacing 1 of 15 returned 429; at 2.2 s spacing 0 of 8. The 429 is an nginx HTML page with **no Retry-After header**. Use 2 s spacing plus one retry. Response has `cache-control: public, max-age=223` and `access-control-allow-origin: *`.
- List downloads were not rate limited (5 full lists in a few minutes, each under 5 s).
- Gotcha: at 23:56Z on 2026-10-01 `lists/date/latest` still returned `Q2K34` (created 2026-09-30T22:00Z) while the per-domain API already served a `2026-10-01` point (anduril.com: 15,793 in file `Q2K34` and for API date `2026-09-30`; 15,922 for `2026-10-01`). The "latest" pointer lags the newest list by at least a couple of hours. Key everything by `list_id` and the API `date`, not by "today".

Fields to keep: `list_id`, `created_on`, per domain `rank` per date.

### Hit-rate test (honest numbers)

Panel of 15 small hard-tech startup domains, fixed before looking at results (the first 15 resolvable domains in my candidate file), plus a wider 69-domain set that also includes later-stage names (Anduril, Skydio, Shield AI, Figure, 1X...).

| List | Panel of 15 | All 69 |
|---|---|---|
| Tranco top 1M | **4 / 15** | 19 / 69 |
| Tranco full (4.57M) | **12 / 15** | 50 / 69 |
| Majestic Million | 12 / 15 | 46 / 69 |
| Cisco Umbrella top 1M | **0 / 15** | 4 / 69 |
| CrUX top 1M (zakird mirror) | 1 / 15 | 8 / 69 |

Panel detail (ranks; `-` = absent from that day's full list):

| domain | Tranco full 2026-09-30 | 2026-09-01 | 2026-07-01 | 2025-10-01 | Majestic | Umbrella | CrUX | RDAP registered | Wayback first / last12m / prev12m | MX | TXT n |
|---|---|---|---|---|---|---|---|---|---|---|---|
| valaratomics.com | 1,562,564 | 1,826,253 | 2,396,009 | - | 499,590 | - | - | 2023-07-04 | 20240407 / 58 / 40 | microsoft_365 | 2 |
| aalo.com | 1,358,119 | 1,900,353 | 2,230,420 | - | 326,509 | - | - | 2002-03-28 | 20020330 / 59 / 60 | other | 15 |
| radiantnuclear.com | 1,303,675 | 1,378,905 | 1,408,003 | 2,394,658 | 374,963 | - | - | 2019-04-10 | 20190917 / 210 / 93 | microsoft_365_gcc_high_or_dod | 23 |
| pacificfusion.com | 1,530,069 | 1,950,621 | 2,397,181 | - | 390,790 | - | - | 2008-09-25 | 19981212 / 43 / 62 | google_workspace | 11 |
| thea.energy | - | - | - | - | - | - | - | 2022-10-12 | 20230726 / 57 / 87 | other | 6 |
| castelion.com | 2,133,149 | 3,382,818 | 2,891,738 | - | 523,681 | - | - | 2023-07-06 | 20180807 / 76 / 92 | microsoft_365_gcc_high_or_dod | 21 |
| cx2.com | - | - | - | - | - | - | - | 1999-07-27 | 19961223 / 14 / 43 | google_workspace | 11 |
| neros.tech | - | - | - | - | - | - | - | 2023-08-26 | 20231101 / 32 / 48 | microsoft_365_gcc_high_or_dod | 16 |
| machindustries.com | 1,994,005 | 2,220,189 | 3,186,865 | - | 683,123 | - | - | 2016-05-27 | 20010404 / 55 / 59 | google_workspace | 28 |
| hadrian.co | 1,128,561 | 1,425,758 | 1,868,731 | - | 348,165 | - | - | 2020-09-03 | 20130515 / 45 / 81 | microsoft_365_gcc_high_or_dod,proofpoint | 21 |
| skild.ai | 449,531 | 1,133,223 | 1,155,116 | 2,538,996 | 114,006 | - | - | 2023-02-07 | 20240102 / 75 / 87 | google_workspace | 22 |
| physicalintelligence.company | 700,864 | 752,278 | 978,590 | 1,617,372 | 190,206 | - | - | 2024-02-25 | 20240312 / 69 / 123 | google_workspace | 11 |
| foundation.bot | 2,185,763 | 2,381,236 | - | - | 775,364 | - | - | 2024-02-06 | 20240417 / 297 / 175 | google_workspace | 4 |
| k2space.com | 865,579 | 980,202 | 1,152,966 | 2,927,106 | 992,338 | - | - | 2016-08-10 | 20040325 / 36 / 62 | microsoft_365_gcc_high_or_dod | 16 |
| reflectorbital.com | 495,718 | 460,725 | 722,787 | 1,604,258 | 192,681 | - | 1,000,000 | 2022-10-08 | 20230316 / 68 / 69 | google_workspace | 11 |

Read: the three domains missing from the full list (thea.energy, cx2.com, neros.tech) are real, funded companies. Absence from Tranco is not evidence of absence of the company. Those are exactly the ones the cheap proxies in section 5 still cover.

### Baseline drift (needed to score rank deltas)

Raw rank deltas are meaningless without a cohort baseline. Measured from the four full lists (ratio = old rank / new rank, above 1 = improved):

| Window | Old-rank cohort | Survival into today's list | p50 ratio | p90 ratio | p99 ratio |
|---|---|---|---|---|---|
| 30 d | 300k-1M | 98.4% | 0.99 | 1.25 | 2.63 |
| 30 d | 1M-2M | 96.4% | 0.99 | 1.29 | 3.09 |
| 30 d | 2M-3.5M | 71.9% | 0.97 | 1.54 | 4.55 |
| 90 d | 1M-2M | 83.7% | 0.94 | 1.40 | 3.88 |
| 90 d | 2M-3.5M | 51.6% | 0.96 | 1.83 | 6.59 |
| 365 d | 1M-2M | 55.8% | 0.99 | 1.83 | 5.22 |
| 365 d | 2M-3.5M | 34.7% | 1.12 | 2.41 | 9.45 |

So skild.ai (30 d ratio 2.52), generalistai.com (2.22), picogrid.com (1.84), castelion.com (1.59) are at or beyond the p90 of their cohorts; radiantnuclear.com (1.06) is noise. Below rank ~2M, churn is heavy (28% of domains drop out within 30 days), so treat entry/exit there as weak.

### Discovery from Tranco diffs (tested, low precision)

Query run: all domains in the 2026-09-30 full list with rank at or under 1.5M whose second-level label matches
`robotics|humanoid|drones?$|defen[sc]e(tech)?$|nuclear|fission|atomics|aerospace|photonics|semiconductor|hypersonic|propulsion|autonomy|spacecraft|orbital|fusion(energy|power)|reactors?$|munitions?$` on a startup-ish TLD, minus a junk blocklist.
- Result: **373 matches; 60 absent from the 2025-10-01 list; 119 new or with log10 rank gain of 0.3+ over a year.**
- A looser keyword set (adds `robot|drone|battery|quantum|radar|rocket|fusion|...`) returned **3,634 matches under rank 3M; 1,353 new vs a year ago** and was dominated by junk.
- A TLD-led cut (`.space`, `.energy`, `.bot`, `.aero`) returned **3,998 matches, 2,710 new**: `.space` and `.energy` are overwhelmingly piracy, proxy and gambling sites (`filmyzilla.energy`, `vidoyu.space`). Do not use thesis TLDs as a filter.
- Manual check of 11 of the most plausible-looking movers by fetching the homepage title: 3 were thesis-relevant young companies (axisrobotics.ai, thehumanoid.ai, mondorobotics.com), 1 was an established Chinese mobility-robot maker (bangbangrobotics.com), 2 were **defense/drone-sounding domains now serving betting sites** (adeptdefense.io, exodrones.com), and the rest were a DJI dealer, an FRC parts store, a conference, a news-automation firm and a blank page. That is about 1 in 4 on a hand-picked sample, so the raw filter's precision is lower still. It needs a homepage-title classifier pass before anything reaches a human.

Conclusion: use Tranco as a join target for entities found elsewhere (GitHub, arXiv, hiring, launches), and run the keyword diff only as a weekly long-shot feed gated by a homepage-title classifier.

## 2. Cloudflare Radar (not usable without a token)

- `GET https://api.cloudflare.com/client/v4/radar/ranking/top?limit=5` returns HTTP 400 `{"errors":[{"code":9106,"message":"Missing X-Auth-Key, X-Auth-Email or Authorization headers"}]}`.
- Same 9106 for `/radar/ranking/domain/{domain}` and `/radar/datasets?datasetType=RANKING_BUCKET`.
- `https://radar.cloudflare.com/domains/domain/{domain}` returns 403 with a "Just a moment..." bot challenge. Not attempted further (no challenge bypass).
- A Cloudflare API token is free but requires an account, which is out of scope here. Radar's ranking is already one of the five inputs to Tranco, so the free route is: consume it through Tranco.

## 3. Cisco Umbrella and Majestic Million (both work, no auth)

**Umbrella top 1M**
- `GET https://s3-us-west-1.amazonaws.com/umbrella-static/top-1m.csv.zip` — 12.9 MB zip, 1.2 s, `Last-Modified: Thu, 01 Oct 2026 01:35:44 GMT` (daily, under 24 h old).
- Dated archive: `.../umbrella-static/top-1m-{YYYY-MM-DD}.csv.zip` — verified 200 for 2026-09-01 and 2025-10-01.
- Format `rank,fqdn`, no header. It ranks **FQDNs by DNS query volume**, so `www.google.com` and `data.microsoft.com` are separate rows and infrastructure hostnames dominate.
- Hit rate 0/15 (4/69: anduril.com 302,398, skydio.com 358,562, basepowercompany.com 814,582, shield.ai 980,007). Only useful as a "has real end-user or device traffic" flag for later-stage or consumer/fleet companies. Base Power appearing here (consumer installs) while far better-funded defense names do not is the kind of thing it shows.

**Majestic Million**
- `GET https://downloads.majestic.com/majestic_million.csv` — 81.1 MB uncompressed CSV, 12.4 s, `Last-Modified: Thu, 01 Oct 2026 05:00:19 GMT` (daily). 1,000,000 rows plus header.
- Columns: `GlobalRank,TldRank,Domain,TLD,RefSubNets,RefIPs,IDN_Domain,IDN_TLD,PrevGlobalRank,PrevTldRank,PrevRefSubNets,PrevRefIPs`.
- This is a **backlink** ranking (referring /24 subnets), not traffic. Hit rate 12/15, 46/69, the best of any top-1M list for small B2B hard-tech sites because press and investor pages link to them long before anyone visits.
- `RefSubNets` is an absolute, comparable count (skild.ai 830, previous 847). Store it daily; the day-over-day `Prev*` columns alone are too short a window.
- Because Tranco folds Majestic in, a domain whose Tranco rank is beyond 1M is being ranked mostly on its Majestic position. Deep Tranco movement is largely link growth, so do not double count Tranco and Majestic as independent signals.

## 4. Chrome UX Report (API needs a key; free mirror works but is shallow)

- `POST https://chromeuxreport.googleapis.com/v1/records:queryRecord` with `{"origin":"https://www.figure.ai"}` and no key: HTTP 403 `PERMISSION_DENIED`, "Method doesn't allow unregistered callers". Same for `records:queryHistoryRecord`.
- Keyless PageSpeed Insights (`https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=...`), which embeds CrUX data: HTTP 429, `Quota exceeded ... 'Queries per day'`, `quota_limit_value: 0`. The anonymous quota is zero.
- BigQuery (`chrome-ux-report`) needs a GCP project. Not tested.
- Free mirror that works: `GET https://raw.githubusercontent.com/zakird/crux-top-lists/main/data/global/current.csv.gz` — 8.7 MB, 1,000,000 origins, columns `origin,rank` where rank is a bucket (1000, 5000, 10000, 50000, 100000, 500000, 1000000). Monthly files `data/global/{YYYYMM}.csv.gz` (latest present: 202608; last commit 2026-09-08).
- Hit rate 1/15, 8/69. Origins, not domains (`https://www.x.com` and `https://app.x.com` are separate). The only value is a binary "entered the CrUX top 1M this month" event, which means real Chrome users. Seen: reflectorbital.com and generalistai.com in the 1M bucket, basepowercompany.com and anduril.com in the 500k bucket.

## 5. Cheap proxies that work for tiny companies

### 5a. Certificate Transparency

**crt.sh — down for the whole session.** `GET https://crt.sh/?q=%25.{domain}&output=json` returned **HTTP 502 on 8 of 8 attempts** between 23:59Z and 00:26Z, including the site root and the `Identity=...&exclude=expired` form. TCP port 5432 (the public `guest@certwatch` Postgres interface) accepted a connection but no Postgres client is installed here, so it is untested beyond the port check. Treat crt.sh as a best-effort backfill source with retries and a circuit breaker, never as a dependency.

**Cert Spotter — works without a key.**
- `GET https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names&expand=issuer`
- Pagination and incremental polling: add `&after={last id}`; response `Link: <...>; rel="next"`. Verified `after=` returns `[]` with `retry-after: 3600` when nothing is new.
- Rate limit headers: `x-ratelimit-limit: 10`, `x-ratelimit-remaining` decremented by one per call and recovered about one unit per 6-7 minutes (6 at 00:00Z, 7 after further calls at 00:14Z, 00:21Z and 00:26Z). Budget **about 10 full-domain queries per hour per IP**.
- One request timed out (53 s, then succeeded on retry).
- **Returns only unexpired certificates.** With 90-day certs, the visible history is 3 to 12 months. First-seen dates must come from your own stored polls (or a crt.sh backfill when it is up).
- Fields: `[].id`, `[].dns_names[]`, `[].not_before`, `[].not_after`, `[].issuer.friendly_name`, `[].revoked`.

What it returned for the 5 test domains (plus one extra):

| domain | unexpired issuances | distinct names | What the names reveal |
|---|---|---|---|
| skild.ai | 47 | 11 | `control.`, `webrtc.`, `tunneling.`, `infra.`, `buf.`, `auth.` — teleoperation and fleet-control stack going to production |
| neros.tech | 14 | 10 | `pay.` (2026-07-28), `web-console.`, `staging-configurator.`, `git.`, `product-tables.` (2026-10-01) — a commerce and configurator surface for a drone maker |
| thea.energy | 15 | 14 | `te-*-01.cloud.` hosts (June 2026), `desc-mcp.` (2026-09-30) — internal compute build-out |
| hadrian.co | 97 | 73 | `*.f2.`, `*.f3.`, `*.fx.` plus per-cell hosts (`cnc-cell1..4.f3`, `cmm-cell1..5.f3`), `hermle.`, `zoller.`, `atlas.`, `docs.` — **factory count and cell count readable from certificates** |
| valaratomics.com | 10 | 8 | `git.`, `badgerequest.`, `permanentrecord.`, `ste.` |
| pacificfusion.com | 33 | 18 | `newmexico.`, `livermore.`, `alameda.` (all 2026-07), `windchill.` (PTC Windchill PLM), `ad.corp.` — site expansion and hardware-engineering tooling |

**Fallback subdomain sources (tested):**
- `GET https://api.hackertarget.com/hostsearch/?q={domain}` — 200, `text/plain`, `host,ip` per line. Headers `x-api-quota: 21`, `x-api-count: 1`. Tiny daily quota, use only for spot checks. Gave 8 hosts for skild.ai and 7 for neros.tech including `gitlab-az.` and `vpn.` not present in the unexpired-cert set.
- `GET https://api.subdomain.center/?domain={domain}` — 200, JSON array of 17 names for skild.ai, but includes junk permutations (`wwwa.vpn.skild.ai`, `www.auth.skild.ai`). Needs DNS validation.
- Do not work without auth: AlienVault OTX passive DNS (429 "Anonymous access to this endpoint is limited"), Merklemap (401), `jldc.me/anubis` (301, dead). urlscan.io search answers 200 anonymously but with a 30-day window and returned 0 results for skild.ai.

### 5b. RDAP (works, no auth) — 70 of 70 lookups succeeded

- `GET https://rdap.org/domain/{domain}` with `Accept: application/rdap+json` — 302 to the registry. Fine for .com, .ai, .tech, .energy, .company, .bot, .computer, .industries, .dev.
- **rdap.org returns 404 `"No RDAP service is available for this resource"` for .io, .co and .us** because those ccTLDs are not in the IANA bootstrap (`https://data.iana.org/rdap/dns.json`, 592 services, published 2026-09-30). Direct registry endpoints that worked:
  - `.io` → `https://rdap.identitydigital.services/rdap/domain/{domain}`
  - `.co` → `https://rdap.registry.co/co/domain/{domain}`
  - `.us` → `https://rdap.nic.us/domain/{domain}`
  - Also not in bootstrap: `.so`, `.sh`, `.vc`, `.de` (use `whois` CLI, which worked for .co/.io/.us here).
- Going direct to registries (bootstrap map plus the three overrides) at 1 request per second: 70/70 HTTP 200, median latency 0.18 s. No rate-limit response seen.
- Fields: `events[] | select(.eventAction=="registration").eventDate`, `"expiration"`, `"last changed"`; `nameservers[].ldhName`; `status[]`; registrar at `entities[] | select(.roles[]=="registrar") | .vcardArray[1][] | select(.[0]=="fn") | .[3]`.
- Registrant identity is redacted everywhere. RDAP gives dates, registrar and nameservers only.

Results on the 5 test domains: skild.ai registered 2023-02-07 (expires 2033); valaratomics.com 2023-07-04; neros.tech 2023-08-26 (expires 2030); thea.energy 2022-10-12; hadrian.co 2020-09-03.

### 5c. Wayback Machine (works, no auth, flaky)

| Purpose | URL template | Measured |
|---|---|---|
| Monthly capture counts, first and last capture (one cheap call) | `GET https://web.archive.org/__wb/sparkline?output=json&url={domain}&collection=web` | 64 of 70 OK (6 connection errors), ~0.35 s. Works with or without a Referer header. Undocumented endpoint. |
| First 200 capture | `GET https://web.archive.org/cdx/search/cdx?url={domain}&output=json&fl=timestamp,original,statuscode&filter=statuscode:200&limit=1` | 7 of 10 OK; 7-39 s; one 504 (60 s), two 503 |
| Homepage monthly cadence and content changes | `...cdx?url={domain}&output=json&fl=timestamp,statuscode,digest,length&collapse=timestamp:6&from=2023` | 3 of 5 OK (two 503 "Temporarily Offline"); 5-37 s |
| **New URL feed** (every distinct HTML page with its first capture time) | `...cdx?url={domain}&matchType=domain&output=json&fl=timestamp,original&collapse=urlkey&filter=statuscode:200&filter=mimetype:text/html&limit=1500` | 5 of 5 OK, 0.3-0.9 s |
| Closest snapshot | `GET https://archive.org/wayback/available?url={domain}&timestamp=YYYYMMDD` | OK, 1.7 s |

- CDX JSON is an array of arrays with a header row first. Sparkline JSON: `years.{YYYY}[12]` monthly counts, `first_ts`, `last_ts`, `status.{YYYY}` (12 chars, one HTTP class digit per month).
- Overall CDX reliability in this session: 5 failures in 26 calls (503 "Internet Archive services are temporarily offline" or 504). Retry with backoff; never block a pipeline run on it.
- The new-URL feed is the surprise winner. Rows returned: skild.ai 83, thea.energy 84, neros.tech 42, hadrian.co 42, valaratomics.com 31. It surfaces the company's own announcements with dates (seen: `valaratomics.com/docs/Announcing-our-1B-Series-B-Led-By-Sequoia` first captured 2026-08-03, `/castle-country` 2026-09-18; `neros.tech/articles/neros-raises-250m-series-c-...` 2026-09-05; `skild.ai/blogs/skild-crosses-100m-arr` 2026-09-11), new section types (`/careers`, `/jobs`, `/technology`, `/protected-downloads`), and **inbound referrers preserved in query strings** (`?utm_source=substack`, `?ref=infinitefrontiers.io`, `?utm_source=www.thelagrind.com`, `?trk=organization_guest_main-feed-card-text` from LinkedIn), which is free evidence of who is linking to them.
- Do not use `matchType=domain` together with `collapse=timestamp:6`: collapse only merges adjacent rows and the index is sorted by URL, so it hit the 2,000-row cap without collapsing anything useful.

### 5d. DNS MX/TXT fingerprints (works, no auth, fast)

- `GET https://dns.google/resolve?name={name}&type={MX|TXT|NS|A|CNAME}` — JSON `Status`, `Answer[].{name,type,TTL,data}`. About 2,200 queries at roughly 3 per second with no throttling or errors.
- `GET https://cloudflare-dns.com/dns-query?name={name}&type=MX` with `accept: application/dns-json` — same shape, 0.08 s.
- Local `dig +short` works too and returned 22 TXT strings for skild.ai (the DoH answer is the same set).
- Probe set per domain: apex MX, apex TXT, `_dmarc` TXT, NS, then A/CNAME for 26 common labels (`app api docs careers jobs status portal support help shop store blog developer dashboard console login auth investors ir trust security mail vpn git staging www`).

Results across 69 hard-tech domains (every one had at least one TXT record; median 11):
- **MX provider:** Google Workspace 33, **Microsoft 365 GCC High / DoD (`*.mail.protection.office365.us`) 22**, Microsoft 365 commercial 7, Proofpoint 3, Mimecast 2, other 4 (for example Barracuda at thea.energy).
- **GCC High set:** radiantnuclear.com, castelion.com, neros.tech, hadrian.co, k2space.com, atomicsemi.com, saronic.com, zenopower.com, generalmatter.com, salientmotion.com, launchfirestorm.com, hermeus.com, antaresindustries.com, apexspace.com, varda.com, stokespace.com, impulsespace.com, albedo.com, epirusinc.com, ursamajor.com, shield.ai, anduril.com. A startup moving mail to `office365.us` is preparing for CUI/ITAR work, which usually precedes or accompanies a DoD contract.
- **Gov-cloud nameservers:** aalo.com and castelion.com on `azuregov-dns.us`, allencontrolsystems.com on `awsdns-us-gov`.
- **TXT verification tokens seen (count of 69):** google-site-verification 52, apple 44, MS tenant 42, anthropic 41, atlassian 29, openai 25, slack 24, rippling 20, cursor 19, **autodesk 16**, docusign 14, zoom 14, adobe 13, **smartsheet 11**, tailscale 11, notion 10, 1password 10, box 9, linear 8, airtable 8, jamf 7.
- **SPF includes:** hubspot 10, amazon SES 7, salesforce 6, mailgun 6, **greenhouse 4**, mailchimp 3, zendesk 3, sendgrid 3, netsuite 1.
- **Live common subdomains (counts include one wildcard-DNS domain, see gotcha 8):** vpn 16, api 12, docs 11, shop 10, portal 9, support 9, app 9, status 8, trust 8 (Vanta 4, SafeBase 1), careers 4, jobs 4, investors 3.
- **DMARC policy:** reject 26, quarantine 21, none 17, missing 5.

On the 5 test domains: skild.ai 22 TXT (Linear, Notion, Slack, Tailscale, HCP, OpenAI, Anthropic, Cursor, Atlassian, a Salesforce-style org id); valaratomics.com only 2 TXT and commercial M365 (lean IT despite large funding); neros.tech GCC High plus Salesforce and Zendesk in SPF (sales and support functions exist); thea.energy Barracuda MX, Rippling, Atlassian; hadrian.co GCC High plus Proofpoint, Autodesk, JetBrains, Asana, docs on AWS GovCloud.

## Derived features a scorer should compute

Rank lists
- `tranco_rank_log10` and `tranco_present` (bool) from the daily full list.
- `tranco_ratio_30d`, `_90d`, `_365d` = old rank / new rank, then **convert to a cohort percentile** using same-band domains from the old list (table above). Score the percentile, not the ratio.
- `tranco_velocity` = slope of log10(rank) over the 39 API points; `tranco_acceleration` = slope of last 13 days minus slope of prior 26.
- `tranco_first_entry_date` (first list containing the domain) and `tranco_entered_top1m_date`. Example event: northwoodspace.io first appears 2026-09-30 at 505,708.
- `majestic_refsubnets` and its 30/90-day delta (store daily); `majestic_present`.
- `umbrella_present`, `crux_bucket`, `crux_bucket_entered_month` (binary consumer or fleet-traffic flags).

Certificate Transparency
- `ct_new_names_30d`, `ct_new_names_90d` (names never seen in your store before), `ct_distinct_names`, and acceleration between the two windows.
- `ct_maturity_flags` from label classes: product (`app api console dashboard portal`), developer (`docs developer api`), reliability (`status`), commerce (`pay shop store`), people (`careers jobs`), enterprise IT (`sso auth okta vpn mdm`), environments (`staging dev prod`), engineering tooling (`git gitlab jira windchill teamcenter`).
- `ct_site_labels`: geographic or facility labels (`newmexico`, `livermore`, `f2`, `f3`, `cnc-cell*`) counted as physical-footprint growth. This is the single most thesis-specific feature here.
- `ct_issuer_mix`: move from only Let's Encrypt to Amazon/Google/DigiCert as infrastructure matures.

Domain and DNS
- `domain_age_days` (RDAP registration), `expiry_horizon_years` (castelion.com to 2035, skild.ai to 2033 = long prepay), `registrar`.
- `mail_provider`, `gcc_high` (bool) and `gcc_high_first_seen` (needs daily snapshots), `govcloud_ns`.
- `txt_tool_count` and `txt_tool_set`; daily diff gives `tool_added` events. Grouped flags: go-to-market (HubSpot, Salesforce, Zendesk, Mailchimp, Stripe), hiring (Greenhouse, Ashby, Lever in SPF or `jobs.` CNAME), hardware engineering (Autodesk, Smartsheet, Onshape), compliance (`trust.` on Vanta/SafeBase, DMARC reject), AI-native engineering (Anthropic, OpenAI, Cursor).
- `dmarc_policy` as an IT-maturity ordinal (none, quarantine, reject).

Wayback
- `wayback_company_epoch` = first capture after the RDAP registration date (not `first_ts`).
- `wayback_captures_12m` vs prior 12 months; `homepage_digest_changes_12m` (site being actively edited).
- `new_urls_per_quarter` and `new_url_types` (careers, product, press, docs); `press_urls_90d` (paths under `/news`, `/articles`, `/blogs`, `/docs` first captured in the window).
- `referrer_diversity` = distinct `utm_source` / `ref` hosts in captured URLs; `linkedin_referrals` (`trk=` params).

Novelty rule of thumb for "pre-consensus": high CT and DNS activity with **no or deep Tranco rank** (neros.tech, cx2.com, thea.energy pattern) is the interesting quadrant. High Tranco plus high Majestic means the market already knows.

## Entity resolution

- The key for this whole family is the **registrable domain** (eTLD+1). Normalise with the Public Suffix List; strip `www.`; lower-case. Tranco and Majestic are already eTLD+1; Umbrella is FQDN and CrUX is origin, so reduce both before joining.
- Domain to company: homepage `<title>` and meta description (cheap GET), the RDAP registrar is not identifying. Domain to GitHub org: links on the homepage, and `git.`/`gitlab.` hosts in CT (self-hosted, so no public org). GitHub's org-verification TXT lives at `_github-challenge-{org}-org.{domain}`, which can confirm a candidate org name but cannot discover one (not tested here). Domain to people: not available from this family (RDAP is redacted); join through other sources by domain.
- Company to domain (the reverse, needed to enrich entities from other sources): take the website field from GitHub org profiles, arXiv author emails, job-board listings or launch posts, then verify with RDAP registration date after the earliest plausible founding date.
- Many startups use non-.com domains that are the canonical brand: `physicalintelligence.company`, `foundation.bot`, `thea.energy`, `neros.tech`, `diode.computer`, `atomic.industries`, `co.bot`, `1x.tech`.

## Gotchas hit

1. crt.sh was 502 for the whole 27-minute window. Cert Spotter only shows unexpired certs and allows about 10 full-domain queries an hour. CT history must be accumulated by polling.
2. Tranco per-domain API returns 429 with no Retry-After under roughly 1 request per second, and occasionally even at 1.2 s. Prefer the daily full-list download for batch work.
3. Tranco top-1M is not enough: 8 of the 12 panel domains that Tranco knows about sit between 1M and 2.2M. Always pull `/download/{id}/full`.
4. Deep Tranco ranks churn heavily (28% of 2M-3.5M domains vanish in 30 days) and are driven mostly by the Majestic backlink component. Normalise against a cohort and do not count Majestic twice.
5. Tranco "latest" pointer lagged the per-domain API by one day label.
6. Keyword discovery on rank lists is polluted: `.space`/`.energy` TLDs are piracy and gambling, and expired defense-sounding domains get re-registered as betting sites (adeptdefense.io, exodrones.com). Always fetch and classify the homepage title.
7. Wayback `first_ts` often predates the company because the domain had a previous owner or was bought: cx2.com 1996, saronic.com 1997, pacificfusion.com 1998, aalo.com 2002, hadrian.co 2013. castelion.com has captures from 2018 but was registered 2023-07-06 (dropped and re-registered). RDAP registration is also old for purchased premium domains (aalo.com 2002, periodic.com 1997). Use the first capture after a long gap or after a title change as the company epoch.
8. Wildcard DNS breaks subdomain probing: machindustries.com resolved all 26 probed labels. Query a random label first and discard the probe set if it resolves (the fixture marks this as `wildcard_dns_suspected`).
9. `.ai` RDAP shows `last changed` of 2026-08-12 for most .ai domains (registry-wide touch) and a 2017-12-15 registration date for older .ai names (figure.ai, shield.ai). Ignore `last changed` on .ai and distrust that specific registration date.
10. rdap.org cannot route .io, .co, .us. Hard-code the three registry endpoints above.
11. Umbrella rows are FQDNs and CrUX rows are origins; naive joins on domain miss them.
12. CT names can contain people's names (per-engineer dev clusters showed up under one domain). Strip or hash person-like labels before storing or displaying; keep only label classes.
13. Wayback CDX latency ranged from 0.3 s to 39 s with intermittent 503/504. The sparkline endpoint is far faster but undocumented and could change.
14. TXT verification tokens show a tool was once verified, not that it is still in use. Treat additions as events and ignore removals.
15. DomCop "top 10 million" (`https://www.domcop.com/files/top/top10milliondomains.csv.zip`) is dead: 301 to an Open PageRank page that returns 404. Common Crawl's CDX index (`https://index.commoncrawl.org/CC-MAIN-2026-39-index?url={domain}&matchType=domain&output=json`) does work (10 s) if another crawl-presence source is wanted.

## Suggested polling plan

| Job | Cadence | Cost |
|---|---|---|
| Tranco full list download, store `(list_id, domain, rank)` for tracked domains plus keyword matches | daily | 1 request, ~110 MB |
| Majestic Million, store `RefSubNets` for tracked domains | daily | 1 request, 81 MB |
| Umbrella top 1M and CrUX mirror presence flags | daily / monthly | 1 request each |
| DoH MX, TXT, DMARC, NS and 26 label probes | weekly per domain | ~32 queries per domain |
| RDAP | once, then quarterly | 1 request per domain |
| Cert Spotter with `after=` cursor | rotate through watchlist at 10 domains per hour per IP | 240 domains per day per IP |
| crt.sh backfill | opportunistic, with retries | best effort |
| Wayback sparkline and new-URL feed | weekly per domain | 2 requests per domain |
