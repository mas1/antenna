# Source card: `regulatory` (hardware regulatory exhaust)

Measured live from this machine on 2026-10-01 (evening US/Central, 2026-10-02 UTC) with `curl`. No paid keys, no accounts, no logins.
Everything below is what actually returned data. Anything marked BLOCKED / DEAD / KEYED was tested and failed as described.

Fixtures: `/Users/noel/antifund/pipeline/fixtures/regulatory/`

## TL;DR ranking (what to build tonight)

| # | Source | Status | Freshness | Difficulty | Signal for pre-consensus sourcing |
|---|--------|--------|-----------|------------|-----------------------------------|
| 1 | **FCC ELS** (experimental licences / STAs): RSS of grants + sequential file-number walk for *pending* applications + Form 442 / STA detail | WORKS, no key | same day | easy-medium (HTML parse) | Very high. Every drone / radar / satellite / C-UAS startup must file before it can transmit. Pending apps are visible ~5-8 weeks before grant, with contact name, title, corporate e-mail domain, test site lat/long, and radio bill of materials |
| 2 | **NRC ADAMS public search API** (`adams-search.nrc.gov/api/search`) | WORKS, no key | same day | easy (JSON) | Very high for nuclear. A new `99902xxx` pre-application docket number = a new reactor startup engaging the NRC |
| 3 | **USPTO trademark search** (`tmsearch.uspto.gov` backing API) | WORKS, no key | 1 day | easy (JSON, Elasticsearch-style DSL) | High. New hardware brands by owner, with goods/services text, basis (1a in-use vs 1b intent), owner address, attorney |
| 4 | FAA UAS Declaration of Compliance API (Remote ID / Ops-over-people) | WORKS, no key | days | easy (JSON) | Medium-high. First accepted Remote ID DoC = first shippable drone airframe. Only 506 rows total, so low volume / high precision |
| 5 | FAA Part 107 waivers issued (HTML table) | WORKS with default curl UA | days | easy (HTML) | Medium. BVLOS (107.31) and swarm (107.35) waivers. Noisy: mostly police/fire departments |
| 6 | USPTO Patent Public Search (PPUBS) anonymous-session API | WORKS, no key | weekly (Thursday publication) | medium (undocumented, session token) | Medium. Structurally lagging (publication is normally 18 months after filing), but great for "first-ever publication by applicant" |
| 7 | regulations.gov v4 (FAA 44807 exemption petitions) | WORKS with `DEMO_KEY`, limit 10 requests | same day | medium (rate limit) | Low. Dominated by agricultural spray-drone LLCs |
| - | FCC Equipment Authorization (FCC ID / grantee codes) | BLOCKED (Akamai 403) | - | hard | would be high; not reachable by script |
| - | FAA aircraft registry bulk zip (N-numbers) | BLOCKED (Akamai 403) | daily | hard | would be high; not reachable by script |
| - | FCC ICFS (satellite filings) | ServiceNow SPA, guest JSON exists | - | hard | medium (ELS already catches early space startups) |
| - | USPTO ODP / PatentsView / TSDR | KEYED (401) / NXDOMAIN | - | - | use PPUBS + tmsearch instead |
| - | SAM.gov entity API (new CAGE/UEI) | 404 without a personal key; site search returns exclusions only | - | - | use USAspending for enrichment |
| - | NRC legacy `adams.nrc.gov/wba/...` | DEAD (NXDOMAIN) | - | - | replaced by #2 |

Build order: **1 FCC ELS, 2 NRC ADAMS, 3 USPTO trademarks.** Then FAA DoC and FAA waivers as cheap add-ons (each under an hour).

User-Agent notes (important, measured):
- `antifund-sourcing-research/0.1 (mason@alterity.systems)` works on apps.fcc.gov ELS, adams-search.nrc.gov, tmsearch.uspto.gov, ppubs.uspto.gov, api.regulations.gov, opendata.fcc.gov.
- `www.faa.gov` returns **403 to that UA** but **200 to the default `curl/x.y` UA**. Use plain curl (no `-A`) for faa.gov and uasdoc.faa.gov.
- `apps.fcc.gov` ELS returns **403 to Python `urllib`** even with the same UA string (Akamai fingerprinting). Shell out to `curl` (or use an HTTP/2 client); do not use `urllib`/`requests` blindly.
- We did not and should not spoof browser UAs to get past Akamai denials (EAS, registry.faa.gov).

---

## 1. FCC Experimental Licensing System (ELS)

Base: `https://apps.fcc.gov/oetcf/els/`. Auth: none. Format: RSS XML + ColdFusion HTML (ISO-8859-1).

### 1a. Recent grants RSS (works)

```
GET https://apps.fcc.gov/oetcf/els/rss/NewGrants.cfm
```
- 200, `text/xml`, 137 KB, **358 items covering 2026-09-02 to 2026-10-01** (rolling ~30 days). `lastBuildDate` was the minute of the request.
- No params, no pagination. Poll daily; dedupe on file number.
- Item shape:
  - `title`: `File Number: 0726-EX-CN-2026, Callsign: WQ2XRO`
  - `description`: `A grant was issued on 09/04/2026 to Firestorm Labs, Inc, experiment type: Unmanned Aerial Vehicle`
  - `link`: `../reports/GetApplicationInfo.cfm?id_file_num=0726-EX-CN-2026` (relative)
  - `pubDate`
- Regex: `A grant was issued on (\S+) to (.*), experiment type: (.*)$`
- Experiment-type mix in the 358 items: Unmanned Aerial Vehicle 64, Vehicle Radar 30, Fixed Radar 18, Rocket Launch 11, satellite general 11, Military 6, Cubesats 6, Space (other than cubesats) 5, High-Altitude Platform 5, C-UAS 2, Autonomous Underwater Vehicle 1, plus ~200 non-thesis (broadcast, GPS re-radiators, 5G, WiFi).
- Thesis filter (experiment type in): `Unmanned Aerial Vehicle, C-UAS, Robotics, Rocket Launch, Cubesats, Space (other than cubesats), satellite, general, big LEO, Fixed Radar, Vehicle Radar, RF Sensor, Autonomous Ground Vehicle, Autonomous Underwater Vehicle, Electronic Countermeasures, Military, High-Altitude Platform System, Telemetry, Tracking and Command`.

### 1b. Application status by file number, including PENDING (works): the real edge

```
GET https://apps.fcc.gov/oetcf/els/reports/GetApplicationInfo.cfm?id_file_num={NNNN}-EX-{TYPE}-{YYYY}
```
- File numbers are **sequential per type per year**. `TYPE`: `CN` new conventional licence, `ST` special temporary authority (STA), `CR` renewal, `CM` modification, `TC` transfer of control, `AL`/`AU` assignment.
- Walking the sequence exposes applications that are `Pending` or `Incomplete Electronic Filing`, i.e. **before grant and before the RSS feed shows them**.
- Measured frontier on 2026-10-01: `1082-EX-CN-2026` (last existing; 1083-1091 all absent) and STs up to at least `1980-EX-ST-2026` (2000 absent).
- Scan result: `1041..1082-EX-CN-2026` = 42 consecutive hits, receipt dates 09/23 to 10/01, so roughly **5 new-licence applications per business day**; STAs run faster (1940 was received 09/28, 1980 on 10/01).
- Row fields (the `<td>` cells following the file number): `file_num, callsign (N/A until grant), applicant, receipt_date, status, status_date`.
- Status values seen: `Pending`, `Incomplete Electronic Filing`, `Granted`.
- Links in the page give the ids needed for the detail hop:
  - CN/CM/CR: `442_Print.cfm?mode=current&application_seq={A}&license_seq={L}` (also `mode=initial`)
  - ST: `STA_Print.cfm?mode=current&application_seq={A}`
  - `ViewExhibitReport.cfm?id_file_num=...&application_seq={A}` (list of exhibit PDFs with category, description, date)
- Missing numbers return the same 200 page without a data row (page ~11.7 KB vs ~15 KB). Detect by absence of the file-number cell, not by status code.
- Lead time example: Firestorm Labs `0726-EX-CN-2026` received 07/28/2026, granted 09/04/2026 (38 days visible before grant).

Collector loop: keep `last_seen[type, year]`; each day request `last_seen+1 ..` until 10 consecutive misses; also re-poll still-pending numbers weekly for status changes.

### 1c. Form 442 (new licence) and STA detail (works)

```
GET https://apps.fcc.gov/oetcf/els/reports/442_Print.cfm?mode=current&application_seq={A}&license_seq={L}
GET https://apps.fcc.gov/oetcf/els/reports/STA_Print.cfm?mode=current&application_seq={A}
```
Label/value pairs as consecutive text nodes (strip tags, split on newlines, take the node after the label):
- `Applicant's Name (company):` / `Name of Applicant:`
- `Attention:`, `Street Address:`, `City:`, `State:`, `Zip Code:`
- `E-Mail Address:` (first occurrence is the company mailing contact: **use the domain for entity resolution**)
- Contact block: `First Name:`, `Last Name:`, `Title:` (e.g. `CTO`, `VP of Systems`, `Regulatory Affairs Lead`)
- `Application is for:` (`NEW LICENSE` / modification), `Applicant is:` (Corporation, ...)
- Government contract yes/no (item 4), estimated duration in months
- Equipment table: `Manufacturer, Model Number, No. Of Units, Experimental`
- Station Location table: city, state, lat/long DMS, street, county, radius of operation; frequency table: band, station class, power, emission designator
- STA only: `Please explain why an STA is necessary`, **`Purpose of Operation`** free text, operation start/end dates

Examples seen:
- Firestorm Labs 442: 100x Silvus SC42A0 + 100x Doodle Labs RM-2025 + 100x TrellisWare TW-310 radios (fleet scale signal).
- Neros Technologies STA `1649-EX-ST-2026`: purpose "test the EcoShield radar" (sic), Echodyne EchoShield, Helendale CA, 09/21/2026 to 03/21/2027.
- Starcloud `1066-EX-CN-2026`: station "NONGEOSTATIONARY", 1618.7-1626.5 MHz.

### 1d. Not working
- `GenericSearchResult.cfm` (date-range / experiment-type search): **503** every time (5 attempts: POST and GET, with and without cookies/Referer, two UAs). Response is a static Akamai NetStorage error page, so the origin is failing. The form page itself (`GenericSearch.cfm`) loads and documents the fields (`receipt_date_from`, `grant_date_from`, `experiment_type` codes such as `UAV`, `CUS`, `ROB`, `ROK`, `CUB`, `SPA`, `AUV`, `AGV`, `HAP`). Retry occasionally; if it comes back it replaces the sequence walk.
- `ApplicationSearchResult.cfm` POST returns 200 but adds nothing over `GetApplicationInfo.cfm`.

### Rate limits (measured)
51 sequential requests at ~1 req/s: 0 errors, 0.22-0.51 s latency. No rate-limit headers. Stay at <= 1 req/s.

### Features
- `els_first_filing`: first-ever file number for this applicant name (novelty; strongest signal).
- `els_pending_new_license`: CN application in `Pending` state (pre-grant lead).
- `els_filings_90d`, `els_filing_velocity` (30d vs prior 90d), `els_sta_to_cn_conversion` (STA followed by a multi-year CN = programme getting real).
- `els_unit_count` = sum of `No. Of Units` (fleet scale), `els_experimental_equipment` (own-design radios), `els_gov_contract` flag.
- `els_test_sites` = distinct station locations; presence at known ranges; `els_experiment_type`.
- `els_contact_title` (CTO / founder filing personally = very early company).
- `els_time_to_grant` = grant date minus receipt date.

### Entity resolution
- Primary: e-mail domain on the form (`launchfirestorm.com`, `hawthornaero.com`, `starcloud.com`, `tiaminetworks.com`, `beamlink.io`, `neros.tech`).
- Secondary: normalized applicant name (strip `Inc|LLC|Corp|,|.`; names vary: "Dedrone Holdings, LLC" vs "Dedrone Holdings, LCC"; "SpaceX" vs "Space Exploration Technologies Corp."). FRN is a field on the search form; we did not see it on the print views we parsed.
- Person: contact first/last name + title.

### Gotchas
- ISO-8859-1 HTML, uppercase tags, values are separate text nodes from labels.
- FCC test rows exist (`FCCTest1`, `Testz 9/30/26 1pm`, `Testing STA Form (CBTS)`): drop applicants matching `(?i)^test|fcctest`.
- Incumbent primes dominate volume (Insitu had 40+ transfer-of-control rows in one day). Filter on `TC`/`CR` types and a prime blocklist.
- Forms contain personal phone numbers and sometimes personal e-mail. Store domain + name + title only. Phone digits are redacted in the fixture.
- Python `urllib` gets 403; use curl.

---

## 2. NRC ADAMS public search API

Legacy `https://adams.nrc.gov/wba/services/search/advanced/nrc` is **dead (NXDOMAIN)**. `https://adams-api-developer.nrc.gov/` is a developer portal that needs sign-up (not used). The public search SPA at `https://adams-search.nrc.gov/` calls an unauthenticated JSON API, which works:

```
POST https://adams-search.nrc.gov/api/search
Content-Type: application/json

{"q":"","filters":[{"field":"DateAdded","value":"(DateAdded ge '2026-08-01')"},
                    {"field":"DocketNumber","value":"99902","operator":"contains"}],
 "anyFilters":[],"legacyLibFilter":false,"mainLibFilter":true,"content":false,
 "sort":"DateAddedTimestamp","sortDirection":-1,"skip":0}
```
- Auth: none. 200 JSON in 0.45-2.4 s.
- Response: `{count, results[100], facets{...}, pageNumber}`. **Page size is fixed at 100**; paginate with `skip` (0, 100, 200...). Verified `skip=100` returns the next page.
- Text filters: `{"field":F,"value":V,"operator":"contains|notcontains|equals|notequals"}`. Date filters: `{"field":"DateAdded","value":"(DateAdded ge 'YYYY-MM-DD')"}` (also `le`, `eq`, and `(F ge 'a' and F le 'b')`).
- `sortDirection`: `-1` desc (verified), `1` asc. `sort: "DateAddedTimestamp"` verified; the SPA bundle also references `DocumentDate`, `AccessionNumber`, `DocumentTitle` as sort fields (not tested).
- `content:true` returns full OCR text per document (one page was 292 KB); keep `false` for polling.
- `GET https://adams-search.nrc.gov/api/metadata` lists the 24 searchable properties (`cogSearchKey`); `GET /api/search/docdetails/{id}` exists.

Queries run:
| Query | count |
|---|---|
| `q="pre-application"`, no filters (all time, content on) | 208,346 |
| `q="pre-application"`, DateAdded >= 2026-09-01 | 355 |
| `q=""`, DateAdded >= 2026-08-01, DocketNumber contains `99902` | **291** |
| `q=""`, DateAdded >= 2026-01-01, DocketNumber contains `999021` | 704 |
| `q="regulatory engagement plan"`, DateAdded >= 2026-07-01 | 248 (keyword match is loose; filter client-side) |

Fields to keep (`results[].document.*`): `AccessionNumber`, `DocumentTitle`, `DocumentDate`, `DateAdded`, `DateAddedTimestamp`, `DocumentType[]`, `DocketNumber[]`, `AuthorAffiliation[]`, `AuthorName[]`, `AddresseeAffiliation[]`, `CaseReferenceNumber[]`, `Keyword[]`, `Url` (direct PDF, `https://www.nrc.gov/docs/ML2627/ML26274A394.pdf`), `EstimatedPageCount`, `IsPackage`. Facets: `facets.DocketNumber[] {value,count}`, `facets.AuthorAffiliation[]`, `facets.DocumentType[]`.

How to read it: pre-application / vendor project dockets are `999021xx` and are issued sequentially. Highest seen with their non-NRC author affiliation: `99902185` Valar Atomics, `99902183` RankShield Energy, `99902177` BlueCore Energy, `99902176` Advanced Float Co., `99902171` AMPERA, `99902160` Apollo Atomics, `99902155` Deployable Energy, `99902154` Alva Energy, `99902144` Hadron Energy, `99902140` Last Energy, `99902138` Terra Innovatum, `99902137` Blue Energy Global, `99902128` Aalo Holdings, `99902126` Deep Fission (NRC letter), `99902106` Radiant Industries.

### Features
- `nrc_new_docket`: first appearance of a `99902xxx` docket (new entrant).
- `nrc_first_rep`: first "Regulatory Engagement Plan" document (formal start of pre-application).
- `nrc_doc_velocity_30d/90d` per docket; acceleration.
- `nrc_stage` ladder from `DocumentType` / title: REP -> QAPD topical report -> white papers / topical reports -> readiness assessment -> construction permit / LWA / ESP application -> acceptance review.
- `nrc_meeting_scheduled` (Meeting Notice with future date), `nrc_fee_waiver_request`, `nrc_staff_response_ratio` (NRC-authored vs company-authored docs).

### Entity resolution
`AuthorAffiliation` when it does not start with `NRC/`; one docket = one company (alias variants: "Blue Energy" / "Blue Energy Global, Inc"; "Radiant", "Radiant Industries, Inc", "R-50, LLC (Radiant)"). Company name also appears in `DocumentTitle`. `AuthorName` gives the licensing lead ("Surname Initials").

### Gotchas
- `DateAdded` lags `DocumentDate` by days to months (Valar docs dated 2026-03-28 were added 2026-10-01). Always poll on `DateAdded`.
- Docket `99902xxx` also covers incumbents (Westinghouse 99902038, Framatome 99902041, Holtec 99902049). Use first-seen date, not membership.
- Some documents have empty `DocketNumber`/`AuthorAffiliation` (package parents); join through `PackagesFiledIn`.
- `www.nrc.gov` HTML pages return 403 to our UA; only the `Url` PDFs and the search API are needed.

---

## 3. USPTO trademarks (tmsearch backing API)

TSDR API is **keyed** (401: "you'll need to register for an API key"). ODP `api.uspto.gov` is **keyed** (401). The public Trademark Search site's backing API works without a key:

```
POST https://tmsearch.uspto.gov/prod-stage-v1-0-0/tmsearch
Content-Type: application/json

{"query":{"bool":{
   "must":[{"query_string":{"query":"\"unmanned aerial vehicles\"","default_operator":"AND","fields":["goodsAndServices"]}},
           {"query_string":{"query":"\"UNITED STATES\"","fields":["ownerFullText"]}}],
   "filter":[{"range":{"filedDate":{"gte":"2026-09-01"}}}]}},
 "size":30,"from":0,"track_total_hits":true,
 "sort":[{"filedDate":{"order":"desc"}}],
 "_source":["id","wordmark","markName","ownerName","ownerCity","ownerStateCountryAddress","ownerEntity","filedDate","goodsAndServices","internationalClass","currentBasis","attorney","firstUseAnyDate","markDescription","recordLoadDate","alive","markType"]}
```
- Auth: none. 200 JSON in 0.2-0.8 s. Elasticsearch-style body; response keys are camelCased (`hits.totalValue`, `hits.hits[].source`).
- Pagination: `from` / `size`. Without `_source` each hit is ~70 KB/10 hits; always pass `_source`.
- Freshness: newest `filedDate` 2026-09-30, `recordLoadDate` 2026-10-01T09:18 (about one day).

Queries run (US owners, filed >= 2026-09-01):
| goodsAndServices query | total |
|---|---|
| `"humanoid robots"` | 43 (124 without the US-owner filter; 794 all-time for `humanoid robot`) |
| `"unmanned aerial vehicles"` | 39 |
| `"nuclear reactors"` | 12 |
| `fusion AND (reactor OR reactors)` | 2 |
| `satellites AND (spacecraft OR launch)` | 6 |
| `(drone OR drones) AND (interceptor OR counter OR jamming)` | 10 |
| `(autonomous OR unmanned) AND (vessels OR boats OR submersibles)` | 13 |
| `"semiconductor" AND (lithography OR "wafers" OR "chips")` | 32 |
| `"battery cells" OR "solid state batteries"` | 1 |
| `"rocket engines" OR "launch vehicles"` | 0 |

Fields to keep (`hits.hits[].source.*`): `id` (serial number), `wordmark`, `ownerName[]` (`"Proception Inc. (CORPORATION; Delaware, USA)"`), `ownerFullText[]` (full street address), `ownerCity`, `ownerEntity`, `filedDate`, `priorityDate`, `currentBasis[]`, `firstUseAnyDate`, `goodsAndServices[]` (prefixed `IC 009:`), `internationalClass[]`, `attorney`, `alive`, `registered`, `recordLoadDate`.

### Features
- `tm_first_filing_by_owner` (novelty), `tm_filings_30d`, `tm_family_burst` (>= 3 marks in a week: K2 Space filed K2 EDGE / K2 FIBER / K2 RF / K2 COMPUTE; ONE Nuclear Energy filed 7).
- `tm_basis`: `1a` (already in commerce, with `firstUseAnyDate`) vs `1b` (intent to use = product not yet shipped = earlier).
- `tm_hardware_classes` = IC 007 / 009 / 012 / 013 share; `tm_goods_keywords` thesis match.
- `tm_owner_entity`: Delaware corporation vs individual vs foreign; `tm_attorney` (which firm; a top-tier firm on a new entity implies funding).

### Entity resolution
`ownerName` before the parenthesis is the legal entity plus state of incorporation; `ownerFullText` gives the address; `wordmark` is usually the product or company brand and often the domain stem. Cross-match on normalized legal name to ELS / NRC / patents (Blue Energy Global appears in all of NRC, trademark and patent results).

### Gotchas
- Undocumented internal API with a version in the path (`prod-stage-v1-0-0`); expect it to move. Keep the request small and polite.
- `wordmark` is null for design-only marks; fall back to `markDescription`.
- Heavy noise from Chinese sellers and individuals; require the US-owner clause and `ownerEntity` in (CORPORATION, LIMITED LIABILITY COMPANY).
- `goodsAndServices` identification text is boilerplate-heavy ("humanoid robots" appears in Amazon and smart-glasses filings); score on IC class + owner novelty, not keyword alone.
- An evidence link per mark was not verified; cite serial number `id` plus the query.

---

## 4. FAA UAS Declaration of Compliance (Remote ID / operations over people)

```
GET https://uasdoc.faa.gov/api/v1/publicDOCRev?itemsPerPage=50&pageIndex=0&orderBy[0][0]=updatedAt&orderBy[0][1]=DESC
```
(use `curl -g`; default curl UA.) Auth: none. 200 JSON, 17.6 KB for 50 rows. `data.totalItems` = **506**; paginate with `pageIndex`. `orderBy=updatedAt,DESC` fails ("Could not read request"); it must be the nested-array form. Optional `search=` (>= 3 chars), `docType`, `status`.

Fields (`data.items[]`): `trackingNumber` (`RID000002674`, `OOP...`), `makeName`, `modelName`, `series`, `status` (`accepted`), `updatedAt`, `docType` (`rid` | `oop`), `revNumber`, `categoryDeclarationFor`.

Newest rows seen: Autel Dragonfish (09-28), DeltaQuad Evo, Manna Drone Delivery 3.8.3 (09-24), BRINC RESPONDER (09-22), Wingcopter 198 (09-21), Amor Fati Industries (dba Seneca) Argo-3 (09-15), Hylio PEGASUS (09-08), Aurelia X8 PRO, Trace UAS, Angel Aerial Systems Trio Scout, Matternet M2, Flyby Robotics F-11 (07-22), Lucid Bots Sherpa, Zipline P2 Zip (06-15), Avol 10 (05-27), SkySpecs Foresight V2-E, Ceres Air C6.

Features: `rid_first_doc_by_make` (first shippable airframe), `rid_model_count`, `rid_new_model_velocity`, `oop_category` (ops over people capability). Entity key: `makeName` (free text, includes dba). Gotcha: `updatedAt` moves on revisions, so an old tracking number with a new `updatedAt` is not a new product; use first-seen `trackingNumber`.

---

## 5. FAA Part 107 waivers issued (and Part 91.113)

```
GET https://www.faa.gov/uas/commercial_operators/part_107_waivers/waivers_issued?page={0..87}
GET https://www.faa.gov/uas/commercial_operators/part_107_waivers/waivers_issued?saa_field_media_file={keyword}
GET https://www.faa.gov/uas/advanced_operations/part_91_waivers/waivers_issued?page={0..47}
```
- **Default curl UA only** (our contact UA and a `Mozilla/5.0 (compatible; ...)` bot UA both got 403).
- HTML table, 25 rows/page, 88 pages for Part 107 (~2,200 rows), 48 pages for 91.113. 6 pages fetched at 1.2 s spacing, no errors.
- Columns: `Date of Issuance`, `Expiration Date`, `Company Name`, `Responsible Person` (link to waiver PDF `/sites/faa.gov/files/107W-2026-NNNNN-Name-CoW.pdf`), `Waivered Regulation`. 91.113 adds `City, State` and `Waiver Number`.
- Thesis filter on `Waivered Regulation`: `107.31` = BVLOS, `107.33` = visual observer, `107.35` = multiple aircraft per pilot (swarms / drone-in-a-box fleets), `107.39` + `107.145` = over people / moving vehicles, `107.51` = altitude/speed.
- In the first 150 rows (2026-08-11 to 2026-11-02), 91 carried 107.31 / 107.33 / 107.35. Company-type hits: Percepto Robotics, blueflite, Avol, Neros Technologies, Kettering Industries, Overwatch Aero, LandSkyAI, Flytrex, Manna Drone Delivery, Censys Technologies, DroneDeploy, Flying Lion, Keelson Strategic, Airloom.
- 91.113 page is almost entirely police/fire "drone as first responder" programmes (useful only as demand signal for DFR vendors).

Features: `faa_first_waiver`, `faa_bvlos_waiver` (107.31), `faa_swarm_waiver` (107.35), `faa_waiver_count_12m`, `faa_waiver_term_days` (multi-year = operational). Entity key: `Company Name` free text (many are individuals in caps); person: `Responsible Person`.
Gotchas: "Date of Issuance" includes future dates (2026-11-02 seen on 2026-10-01; it behaves like an effective date), so sort is not strictly "newest issued". Typos in the regulation column (`107..33`, `107.51(b0`). Exclude `Police|Sheriff|Fire|County|City of|University|Department`.

Other FAA items tested:
- **Aircraft registry bulk** `https://registry.faa.gov/database/ReleasableAircraft.zip` (URL confirmed on the faa.gov download page): **403 from Akamai** for HEAD, ranged GET, three honest UAs. `registry.faa.gov/aircraftinquiry/` also 403. Not scriptable from here; this also blocks experimental-airworthiness and new-N-number-by-new-LLC signals. Manual browser download would be the only route.
- **Section 44807 exemptions**: FAA page points to regulations.gov (see section 7).
- **Part 135 drone certificates**: `https://www.faa.gov/uas/advanced_operations/package_delivery_drone` is narrative only, no machine-readable holder list found. Skip.

---

## 6. USPTO patents: Patent Public Search (PPUBS) anonymous API

- `api.patentsview.org` redirects to a transition notice on data.uspto.gov; `search.patentsview.org` is **NXDOMAIN**; PatentsView S3 bulk files return **403**; `api.uspto.gov` (ODP) returns **401 Unauthorized** without a key; `bulkdata.uspto.gov` and `assignment-api.uspto.gov` do not connect. So: PatentsView and ODP need a key now.
- PPUBS works with an anonymous session:

```
POST https://ppubs.uspto.gov/api/users/me/session        body: -1      (Content-Type: application/json)
  -> JSON {userCase:{caseId}}, response header  X-Access-Token: <jwt>

POST https://ppubs.uspto.gov/api/searches/counts          headers: X-Access-Token
  body = the "query" object below -> {numResults}

POST https://ppubs.uspto.gov/api/searches/searchWithBeFamily
{"start":0,"pageCount":50,"sort":"date_publ desc","docFamilyFiltering":"familyIdFiltering","searchType":1,
 "familyIdEnglishOnly":true,"familyIdFirstPreferred":"US-PGPUB","familyIdSecondPreferred":"USPAT","familyIdThirdPreferred":"FPRS",
 "showDocPerFamilyPref":"showEnglish","queryId":0,"tagDocSearch":false,
 "query":{"caseId":<caseId>,"hl_snippets":"2","op":"OR","q":"B64U$.cpc. AND @pd>=\"20260901\" AND US.aaco.",
          "queryName":"<same>","highlights":"0","qt":"brs","spellCheck":false,"viewName":"tile","plurals":true,
          "britishEquivalents":true,"databaseFilters":[{"databaseName":"US-PGPUB","countryCodes":[]}],
          "searchType":1,"ignorePersist":true,"userEnteredQuery":"<same>"}}
```
- Response: `{numFound, totalResults, cursorMarker, patents[]}`; paginate with `start`. ~35 calls at ~1/s, 0.3-0.6 s each, no errors.
- Query syntax: `B25J$.cpc.` (CPC with truncation), `@pd>="YYYYMMDD"` (publication date), `US.aaco.` (applicant country), `"name".aanm.` (applicant name). Databases: `US-PGPUB` (applications), `USPAT` (grants).

Counts, published >= 2026-09-01, US-PGPUB:
| Query | n |
|---|---|
| `B25J$.cpc.` (robots/manipulators), all applicants | 450 |
| same `AND US.aaco.` | 163 |
| `B62D57/032.cpc.` (legged robots) | 11 |
| `B64U$.cpc. AND US.aaco.` (UAVs) | 65 |
| `G21C$.cpc. AND US.aaco.` (fission) | 20 |
| `G21B$.cpc. AND US.aaco.` (fusion) | 6 |
| `B64G$.cpc. AND US.aaco.` (spacecraft) | 37 |
| `H01M10/$.cpc. AND US.aaco.` (batteries) | 193 |

Fields to keep (`patents[].*`): `guid` (`US-20260290628-A1`), `datePublished`, `applicationFilingDate[]`, `applicantName[]`, `assigneeName[]` (often null on applications; prefer `applicantName`), `inventorsShort`, `inventionTitle`, `cpcInventiveFlattened`, `cpcAdditionalFlattened`, `familyIdentifierCur`, `priorityClaimsDate`, `governmentInterest`.

Novelty test that works: two `counts` calls per applicant, `"<name>".aanm.` and `"<name>".aanm. AND @pd<"20260101"`, over `US-PGPUB`+`USPAT`. Measured: Kepler Fusion Technologies 21 lifetime / 0 before 2026; FlyShepherd 1/0; Variable UAV 1/0; Eagle Eye Robotics 1/0; Mytra 19/10; Natura Resources 22/11; Blue Energy Global 5/3; Skydio 619/480.

Features: `pat_first_publication` (lifetime count before window = 0), `pat_pub_velocity_12m`, `pat_filing_to_pub_days` (short = Track One / early publication = aggressive IP), `pat_cpc_thesis_share`, `pat_inventor_count`, `pat_gov_interest` flag.
Entity keys: `applicantName` (company or "Surname; First" for solo inventors), `inventorsShort` (founder names).
Gotchas: undocumented internal API; token lifetime unknown (refresh per run). Publication lags filing by up to 18 months, so this is confirmation rather than discovery, although many filings from 2026-03 to 2026-06 were already published in 2026-09/10. Solo inventors and big companies dominate; the `aanm` novelty count is what makes it useful. Returned applicant strings are not normalized ("AeroVironment, Inc." and "AEROVIRONMENT, INC." both appear), so normalize before grouping.

---

## 7. regulations.gov v4 (FAA Section 44807 petitions)

```
GET https://api.regulations.gov/v4/documents?filter[searchTerm]=44807%20exemption&filter[agencyId]=FAA&sort=-postedDate&page[size]=10&api_key=DEMO_KEY
```
(`curl -g`.) Works. Headers: `x-ratelimit-limit: 10`, `x-ratelimit-remaining: 9` then `8`. **DEMO_KEY = 10 requests per window**; a real collector needs a free api.data.gov key (requires sign-up, which we did not do).
- `meta.totalElements` 12,400; facets show 85 posted in the last 30 days, 304 in 90 days. `page[size]=25` worked; further pages via `page[number]` (not exercised, to conserve the 10-request budget).
- Second query `44807 "beyond visual line of sight"`, postedDate >= 2026-07-01: 44 results.
- Fields: `data[].id` (`FAA-2026-12641-0001`), `attributes.title` (`"Strata UAV LLC - Petition"`), `attributes.docketId`, `attributes.postedDate`, `attributes.subtype` (`Petition(s)`, `Amendment`, `Decision`), `attributes.documentType`.
- Reality: results are dominated by agricultural spray-drone LLCs (EDGE Ag Solutions, Tallgrass Drone Spraying, Vortek Ag...). Low thesis value; build last, if at all.
- Federal Register API (`https://www.federalregister.gov/api/v1/documents.json?...`, no key) works but returned only rules/notices for the same term (50 results, mostly information-collection notices). Not useful for company discovery.

---

## 8. Tested and not usable

| Source | Exact URL | Result |
|---|---|---|
| FCC equipment authorization search | `https://apps.fcc.gov/oetcf/eas/reports/GenericSearch.cfm`, `.../GranteeSearch.cfm`, `/oetcf/eas/` | **403 Access Denied** (Akamai), while ELS on the same host works |
| FCC TCB grant form | `https://apps.fcc.gov/oetcf/tcb/reports/Tcb731GrantForm.cfm` | 503 |
| FCC grantee registrations (Socrata) | `https://opendata.fcc.gov/resource/3b3k-34jp.json` | Works, no key, 50,153 rows, but **stale: max `date_received` = 2021-03-22**. Usable only as a historical grantee-code lookup |
| fccid.io, fcc.report (third-party mirrors) | `https://fccid.io/`, `https://fcc.report/` | 403 to our UA |
| www.fcc.gov (any page) | `https://www.fcc.gov/icfs` etc. | 403 to our UA |
| FCC ICFS (satellite) | `https://fccprod.servicenowservices.com/icfs` | 200, ServiceNow portal, guest session; `/api/now/sp/page?id=...` returns JSON. No simple search endpoint found in the time box. Hard |
| FCC ECFS | `https://publicapi.fcc.gov/ecfs/filings?api_key=DEMO_KEY&limit=1` | 200 (works with DEMO_KEY) but it is docket comments, not licensing |
| FAA registry | `https://registry.faa.gov/database/ReleasableAircraft.zip` | 403 |
| NRC legacy ADAMS WBA | `https://adams.nrc.gov/wba/services/search/advanced/nrc` | NXDOMAIN |
| PatentsView | `https://search.patentsview.org/api/v1/patent/` | NXDOMAIN |
| USPTO ODP | `https://api.uspto.gov/api/v1/patent/applications/search` | 401 `{"message":"Unauthorized"}` |
| USPTO TSDR | `https://tsdrapi.uspto.gov/ts/cd/casestatus/sn.../info.json` | 401, key required |
| SAM.gov entity API | `https://api.sam.gov/entity-information/v3/entities?...` (no key and `DEMO_KEY`, v2/v3/v4) | 404 empty body every time. Needs a personal SAM key (account). `https://sam.gov/api/prod/sgs/v1/search/?index=ei&q=...` returns only exclusion records to anonymous callers |
| USAspending (SAM alternative, enrichment only) | `POST https://api.usaspending.gov/api/v2/autocomplete/recipient/` and `/api/v2/search/spending_by_award/` | Works, no key. Recipient autocomplete 0.8 s; award search by `recipient_search_text` 11.6 s; a NAICS + date-range query returned 502 after 60 s. Use per-entity lookups, not sweeps |

---

## 9. Cross-source scoring notes

- Compound novelty is the point: the same young legal entity showing up in two or more of {ELS pending licence, NRC docket, trademark 1b filing, first patent publication, first FAA waiver / DoC} inside 90 days. Seen live: Blue Energy Global (NRC construction-permit part 1 + 3 trademarks + patent publication, all September 2026); Neros Technologies (ELS STA + BVLOS waiver); Avol (BVLOS waiver + Remote ID DoC).
- Suggested per-entity features: `reg_sources_hit` (0-6), `reg_first_seen_date`, `reg_events_30d / 90d`, `reg_velocity` = events_30d / (events_90d/3), `reg_stage` (intent -> testing -> shipping: TM 1b / ELS STA -> ELS CN / FAA waiver -> RID DoC / TM 1a), `reg_prime_flag` (blocklist of incumbents).
- Join order: e-mail domain (ELS) > normalized legal name > brand/wordmark > person name.

## 10. Fixtures

| File | What |
|---|---|
| `fcc_els_new_grants_rss.xml` | first 80 of 358 RSS items, real |
| `fcc_els_application_info_0950-EX-CN-2026.html` | status page for a pending application (hop 1) |
| `fcc_els_form442_pending_1059-EX-CN-2026.html` | Form 442 print view for a pending new licence (hop 2); 10-digit phone numbers redacted |
| `nrc_adams_search_preapp_dockets.json` | first 30 of 291 results + full facets for the `99902` docket query |
| `uspto_tmsearch_uav_us_owners.json` | 30 hits, `"unmanned aerial vehicles"`, US owners, filed >= 2026-09-01 |
| `faa_uasdoc_publicDOCRev.json` | 50 most recently updated declarations of compliance |
| `uspto_ppubs_search_g21_nuclear.json` | 12 of 25 hits for `(G21C$ OR G21B$).cpc.`, US applicants, published >= 2026-09-01 |
| `faa_part107_waivers_issued_p0.html` | table + pager from page 0 |
