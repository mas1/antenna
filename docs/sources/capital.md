# Source card: `capital` (money and government exhaust)

Recon date: 2026-10-01 (UTC evening). Everything below was measured from this machine with `curl` / `python3 urllib`, no API keys, no logins.
User-Agent used everywhere: `antifund-sourcing-research/0.1 (mason@alterity.systems)`.

Fixtures: `/Users/noel/antifund/pipeline/fixtures/capital/`

## TL;DR

| # | Source | Status | Freshness | What it gives | Signal strength (pre-consensus) |
|---|---|---|---|---|---|
| 1 | SEC EDGAR Form D (daily index + `primary_doc.xml` + full-text search) | WORKS, no key, UA required | same day (FTS) / ~22:00 ET (daily index) | Priced round / SAFE happened: issuer, city, year of inc, amount sold, # investors, date of first sale, **executive officers and directors by name** | Very high. Median filing lag is 15 days after first sale. No business description, so thesis fit needs a join. |
| 2 | FPDS ATOM feed (Other Transaction awards) | WORKS, no key | DoD rows embargoed 90 days | New DoD OT prototype awards (DIU, DARPA, CDAO, SOCOM, Army ACC), vendor UEI, CAGE, **SAM registration date** | Very high. OTs are NOT in USAspending. 318 new DoD OTs in the newest visible quarter. |
| 3 | USAspending `spending_by_award` | WORKS, no key, slow/flaky | DoD embargoed 90 days; DOE/NASA near real time | Contracts + grants; AFWERX Phase I = `FA8649..P....` purchase orders | High for AFWERX/NASA Phase I and ARPA-E (`DEAR...`) awards |
| 4 | SBIR.gov API (`api.www.sbir.gov/public/api/*`) | **DOWN: HTTP 403** on awards, firm, solicitations ("undergoing maintenance") | n/a | n/a | Use the bulk CSV instead |
| 4b | SBIR.gov bulk CSV (`data.www.sbir.gov/...award_data_no_abstract.csv`) | WORKS, 91.5 MB, refreshed 2026-10-01 | NSF to 2026-08-28, DoD to 2026-01, NASA/DOE/HHS to 2025-09 | 219,649 awards since 1983 with **company website, employee count, PI name + email**, UEI | High, but 2026 is thin (303 rows) |
| 5 | NSF Awards API | WORKS, no key, fast | to 2026-09-09 | SBIR/STTR Phase I + Fast-Track with full abstract, PI name + email, UEI | High for deep-tech formation; small volume (95 Phase I/Fast-Track in 2026) |
| 6 | ARPA-E projects JSON:API (undocumented) | WORKS, no key | newest selection 2026-06-18 | 1,721 projects, 681 private-company; org, award $, program, status | Medium-high for energy |
| 7 | DoD SBIR topics API (`dodsbirsttr.mil`) | WORKS, no key | live | Open/pre-release topics (demand side), 48 open | Context feature, not an entity source |
| 8 | NASA TechPort API | WORKS, no key | live | Project metadata by `updatedSince` | Low (mostly NASA-internal projects) |
| - | DIU site, AFWERX site, war.gov/defense.gov contract announcements, SAM.gov API | no API / 403 / 404 / key required | | | Covered indirectly by 2 and 3 |

Key structural facts a builder must know:

1. **DoD procurement data is embargoed 90 days** in both FPDS and USAspending. Measured: DoD small-business new awards 2026-07-03..10-01 = 2 contracts; 2026-04-01..07-02 = 451,456 contracts. So the "last 90 days" DoD window is empty by design. The freshest usable DoD window is days 91-180. DOE and NASA are not embargoed (NASA rows dated 2026-09-30 are present).
2. **SBIR/STTR volume collapsed after 2025-09** (program authorization lapse): awards per month in the CSV go 1,216 (2025-09) -> 154 (2025-10) -> 66 (2025-11) -> 10 (2025-12). 2026 has only 303 rows (155 DoD, 147 NSF, 1 DOE). The scorer must not read "no SBIR in 2026" as a negative.
3. **Form D has no description of the business.** Only an industry-group dropdown. Thesis classification needs a join to SBIR / FPDS / USAspending / web. The join works: 34 of the 1,054 operating-company Form D issuers in the last 30 days matched an SBIR awardee (2023+) by normalized name, which also yields the company domain.

---

## 1. SEC EDGAR Form D

### 1.1 Endpoints

All require a `User-Agent` with contact info. Measured: curl default UA and empty UA both return **HTTP 403** on `www.sec.gov/Archives`.

| Purpose | URL template | Notes |
|---|---|---|
| Daily index listing | `GET https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{n}/index.json` | JSON directory. Files: `form.YYYYMMDD.idx`, `company.*.idx`, `master.*.idx`, `crawler.*.idx`, `sitemap.*.xml`. `QTR4/index.json` was still empty at 2026-10-01 23:56 UTC. |
| Daily form index | `GET https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{n}/form.{YYYYMMDD}.idx` | Fixed-width text, 0.56-1.1 MB/day, sorted by form type. Posted around 22:00 ET (`last-modified` e.g. `09/30/2026 10:0x PM`). No file on weekends/holidays (no 20260907). |
| Form D structured XML | `GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/primary_doc.xml` | 5-10 KB. The path in the idx is `edgar/data/{cik}/{accession-with-dashes}.txt`; strip dashes to form the folder. |
| Full-text search | `GET https://efts.sec.gov/LATEST/search-index?q={q}&forms=D&dateRange=custom&startdt=YYYY-MM-DD&enddt=YYYY-MM-DD&from={0,100,...}` | Elasticsearch JSON. `q` optional. |
| Entity metadata | `GET https://data.sec.gov/submissions/CIK{10-digit}.json` | Name, **EIN**, phone, addresses, formerNames, filing list. `website` was empty for the new issuers checked. |

### 1.2 Full-text search behaviour (measured)

- `forms=D` returns D **and** D/A (root form). `forms=D,-D/A` returns new D only (3,508 for 09-01..09-30, matching unique D accessions in the daily index).
- Page size is fixed at 100 (`size=10` ignored). Paginate with `from`. Hard cap: `from + 100 <= 10000` (`from=10000` returns an error object, HTTP 200, no `hits` key). Keep date windows under 10k hits; one month of D + D/A is ~5.7-6k.
- FTS indexes the text of `primary_doc.xml`, so it matches issuer name, **person names** (`"Caleb Horan"` -> 1 hit), industry-group strings and security descriptions. It cannot match a business description because none exists.
- FTS is fresher than the daily index: at recon time it already had `file_date=2026-10-01` filings (5,957 hits for 09-01..10-01) while the QTR4 daily index was empty.
- Two transient failures in about 45 calls (HTTP 500 / error body without `hits`). Retry with backoff; always check for the `hits` key.
- Response fields worth keeping: `hits.total.value`, `hits.hits[]._id` (`{accession}:primary_doc.xml`), `_source.ciks[]`, `_source.display_names[]` (`"Name  (CIK 0002134746)"`), `_source.file_date`, `_source.form`, `_source.adsh`, `_source.biz_locations[]`, `_source.biz_states[]`, `_source.inc_states[]`, `_source.items[]` (exemptions: `06B`, `06C`, `3C`, `3C.1`, `3C.7`), `_source.file_num[]`. Aggregations: `entity_filter`, `sic_filter`, `biz_states_filter`, `form_filter`.

Thesis queries run (window 2026-09-01..2026-10-01, `forms=D`):

| q | hits | examples seen |
|---|---|---|
| (none) | 5,957 | |
| `robotics` | 4 | Salem Robotics, Cargo Robotics II (SPV), Parallel Robotics |
| `robot OR robotics OR humanoid` | 8 | Humanoid NP Atoms, Humanoid Genki Series, Hyperlink Humanoid I (all SPVs) |
| `drone OR drones OR UAS OR unmanned` | 1 | Drone Volt S.A. |
| `autonomous OR autonomy` | 0 | |
| `defense` | 8 | Grex Defense, Callan OS Defense Fund, Decimus Defense Fund |
| `aerospace OR space` | 23 | FreeFall Aerospace, Fenix Space |
| `nuclear OR fusion OR fission OR atomic OR reactor` | 12 | Dream SPV Atomic, LFG New Atomic Age |
| `battery OR batteries OR grid` | 5 | |
| `energy` | 86 | Mojave Energy Systems, Kanin Energy |
| `semiconductor OR silicon OR photonics` | 5 | Apogee Semiconductor |
| `manufacturing` | 37 | (matches the industry-group string too) |
| `hypersonic OR propulsion OR rocket` | 2 | both false positives (Rocket Carwash) |
| `"Other Technology"` | 409 | industry-group prefilter |
| `"Simple Agreement for Future Equity"` | 152 | SAFE rounds |
| `"Other Technology" OR "Manufacturing" OR "Other Energy" OR "Energy Conservation" OR "Electric Utilities"` with `forms=D,-D/A`, 09-01..09-30 | 402 | **the cheap prefilter** |

Conclusion: keyword FTS on Form D is a name match only and misses most real companies. Use FTS as an industry-group prefilter, then parse the XML.

### 1.3 Pulling every Form D and D/A for the last 30 days (done)

Method: 21 daily `form.YYYYMMDD.idx` files (2026-09-01..09-30), keep rows whose form type is exactly `D` or `D/A`, then GET each `primary_doc.xml`.

Row regex: `^(\S+(?: \S+)*?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/\S+)` after the dashed separator line.

Measured counts:

| Step | Count |
|---|---|
| idx rows, D + D/A | 5,727 (D 3,541, D/A 2,186) |
| unique accessions (multi-issuer filings repeat once per co-issuer CIK) | 5,667 (D 3,508, D/A 2,159) |
| `primary_doc.xml` fetched and parsed | 5,727 of 5,727, 0 errors |
| per business day | 195-372 |

Industry group over all 5,727: Pooled Investment Fund 3,883; Other Technology 385; Other 363; Other Real Estate 196; Commercial 178; Residential 93; Other Health Care 89; Investing 74; Biotechnology 68; Other Banking 66; Oil and Gas 42; REITS 38; Manufacturing 35; Insurance 33; Other Energy 31; Business Services 30; Computers 24.

Funnel to "likely thesis-aligned early-stage operating company" (unique accessions):

| Stage | Rule | Count |
|---|---|---|
| S0 | all D + D/A | 5,667 |
| S0b | new D only | 3,508 |
| S1 | industry not fund / real estate / finance / hospitality, and no `3C*` exemption | 882 |
| S2a | `entityType == Corporation` | 633 |
| S2b | `yearOfInc.value >= 2021` | 414 |
| S2c | `totalOfferingAmount` in $250k..$100M or `Indefinite` | 355 |
| S3 | industry in {Other Technology, Manufacturing, Other Energy, Energy Conservation, Electric Utilities, Computers, Telecommunications, Other, Oil and Gas, Business Services} | **300** |
| S4 | issuer name matches a thesis regex (robot, defense, drone, nuclear, fusion, atomic, energy, power, forge, dynamics, munition, space, ...) | **23** |

S3 profile: median `totalAmountSold` $650k, median 3 investors, 101 of 300 are SAFE/other-type, 172 equity; year of inc 2026: 104, 2025: 81, 2024: 39; 208 of 300 have CIK >= 2,140,000 (first-ever EDGAR filer); top states CA 59, FL 25, TX 21, NY 18. Median lag from `dateOfFirstSale` to filing: **15 days** (p25 13, p75 53).

S4 is too narrow (name match). S3 is the right candidate set to hand to a classifier / entity resolver: 300 companies per month is small enough to enrich each one.

Cheaper production path: FTS prefilter (402 hits/month) then about 400 XML GETs instead of 5,700.

### 1.4 `primary_doc.xml` fields (XPath from `edgarSubmission`)

| Field | Path | Notes |
|---|---|---|
| form | `submissionType` | `D` or `D/A` |
| issuer name | `primaryIssuer/entityName` | |
| CIK | `primaryIssuer/cik` | entity key |
| address | `primaryIssuer/issuerAddress/{street1,street2,city,stateOrCountry,zipCode}` | Often a registered agent or law firm address (many DE/WY "cities" are agents) |
| phone | `primaryIssuer/issuerPhoneNumber` | Often the founder's mobile |
| jurisdiction | `primaryIssuer/jurisdictionOfInc` | |
| entity type | `primaryIssuer/entityType` | Corporation / Limited Liability Company / Limited Partnership / Other |
| year of inc | `primaryIssuer/yearOfInc/value` with `withinFiveYears=true`; otherwise only `overFiveYears=true` (no year); or `yetToBeFormed` | |
| previous names | `primaryIssuer/issuerPreviousNameList/value` | rename detection |
| co-issuers | `issuerList/issuer[]` | 100 filings had more than one issuer |
| **people** | `relatedPersonsList/relatedPersonInfo[]/relatedPersonName/{firstName,middleName,lastName}`, `.../relatedPersonRelationshipList/relationship[]` in {Executive Officer, Director, Promoter}, `.../relationshipClarification`, `.../relatedPersonAddress/*` | **Founders.** Directors who are not executives are frequently the lead investor's partner (investor fingerprint). |
| industry | `offeringData/industryGroup/industryGroupType` (+ `investmentFundInfo/investmentFundType` for funds) | |
| size | `offeringData/issuerSize/revenueRange` | usually "Decline to Disclose" |
| exemptions | `offeringData/federalExemptionsExclusions/item[]` | `06b`, `06c`, `3C`, `3C.1`, `3C.7` |
| amendment | `offeringData/typeOfFiling/newOrAmendment/isAmendment` | |
| first sale | `offeringData/typeOfFiling/dateOfFirstSale/value` or `yetToOccur` | the real round date |
| securities | `offeringData/typesOfSecuritiesOffered/{isEquityType,isDebtType,isOptionToAcquireType,isSecurityToBeAcquiredType,isPooledInvestmentFundType,isOtherType}` + `descriptionOfOtherType` | SAFE shows up as `isOtherType` + free text |
| amounts | `offeringData/offeringSalesAmounts/{totalOfferingAmount,totalAmountSold,totalRemaining}` | `totalOfferingAmount` may be the string `Indefinite` |
| investors | `offeringData/investors/{totalNumberAlreadyInvested,hasNonAccreditedInvestors}` | |
| min check | `offeringData/minimumInvestmentAccepted` | |
| brokers | `offeringData/salesCompensationList/recipient[]` | placement agent present = later stage |
| signer | `offeringData/signatureBlock/signature/{nameOfSigner,signatureTitle,signatureDate}` | title often "CEO" |

### 1.5 SPV names as a demand signal (non-obvious)

Pooled-fund Form Ds named "X, a Series of Y Master LLC" name the target company. In the 30-day pull: 1,243 SPV-style pooled funds, 28 with a thesis keyword in the name. Seen: `Aalo Atomics BBVC X`, `HII Aalo Atomics-02` ($9.0M, 177 investors), `HII Helion Energy-01` ($4.55M, 53 investors), `Anduril Investment, a series of BlueArc Private Direct Investments` ($42.9M, 166 investors), `Cargo Robotics II, a Series of Omni Ventures Master LLC` ($767k, 10 investors), `Hyperlink Humanoid I`, `Humanoid Genki Series`, `Subatomic, a Series of 8090 Industries Master Series` ($10.15M), `Nuvion Series I Defense Tech`. Feature: count of distinct SPVs and syndicate platforms per target name per month. This is mostly a consensus/heat signal (names that already have syndicate demand), useful as a negative for "pre-consensus".

### 1.6 Rate limits (measured)

- 5,105 sequential `primary_doc.xml` GETs over one keep-alive connection with a 0.15 s sleep: 1,085 s, about **4.7 req/s, zero 429/403**. SEC's published ceiling is 10 req/s.
- Opening a new TLS connection per request (plain `urllib.urlopen`) dropped throughput to about 1-3 req/s. Reuse the connection.
- Daily idx downloads: 0.2-1.1 s each.
- FTS: 0.1-2.1 s per call.

---

## 2. FPDS ATOM feed: Other Transaction awards (best new finding)

USAspending's award-type list has no OT type (`/api/v2/references/award_types/` returns only contracts, IDVs, grants, loans, direct payments, other assistance), and a DoD keyword search there for "other transaction prototype" returned 0. FPDS still serves OTs.

### 2.1 Endpoint

`GET https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q={query}&start={0,10,20,...}`

- No auth. Atom XML, **10 entries per page fixed**. Total is not returned directly: read `link[rel="last"]` and take its `start` value (+ up to 10).
- With curl use `-g` (brackets in the date range break URL globbing) and URL-encode `q`.
- Query grammar (space = AND): `AWARD_TYPE:"OTHER TRANSACTION AGREEMENT"`, `AWARD_TYPE:"OTHER TRANSACTION IDV"`, `DEPARTMENT_ID:"9700"` (DoD; NASA is `8000`), `SIGNED_DATE:[2026/04/01,2026/07/02]`, `MODIFICATION_NUMBER:"0"` (new awards only), `PIID:"HQ0845269E042"`, `PRODUCT_OR_SERVICE_CODE:"1550"`, `PRINCIPAL_NAICS_CODE:"336411"`, `DESCRIPTION_OF_REQUIREMENT:"SBIR"`, bare words for free text. `CONTRACT_TYPE:"OTHER TRANSACTION AGREEMENT"` returns 0 (wrong field name).
- Latency 0.7-1.2 s. About 45 calls at 0.6 s spacing, no errors.

### 2.2 Queries run

| Query | Result |
|---|---|
| `AWARD_TYPE:"OTHER TRANSACTION AGREEMENT" DEPARTMENT_ID:"9700" SIGNED_DATE:[2026/07/03,2026/10/01]` | 0 (90-day embargo) |
| same, `SIGNED_DATE:[2026/04/01,2026/07/02]` | about 1,460 actions (incl. mods) |
| same + `MODIFICATION_NUMBER:"0"` | **318 new DoD OT awards** (all pulled, 32 pages) |
| `AWARD_TYPE:"OTHER TRANSACTION IDV" DEPARTMENT_ID:"9700" SIGNED_DATE:[2026/04/01,2026/07/02]` | about 680 actions |
| `AWARD_TYPE:"OTHER TRANSACTION AGREEMENT" SIGNED_DATE:[2026/05/01,2026/07/01]` (all agencies) | about 1,070 actions (includes ARPA-H under Interior `140D04...`) |
| `DEPARTMENT_ID:"9700" SIGNED_DATE:[2026/09/01,2026/09/30]` | about 650 rows only (telecom orders), confirming the embargo |
| `DEPARTMENT_ID:"8000" SIGNED_DATE:[2026/09/20,2026/09/30] DESCRIPTION_OF_REQUIREMENT:"SBIR"` | about 150 (NASA is not embargoed) |

Of the 318 new DoD OTs: 75 match a thesis keyword in the description (54 unique vendors); 44 went to vendors whose SAM registration date is 2023 or later (42 unique). By contracting office: Army ACC-RI `W519TC` 81, DARPA `HR0011` 48, ACC-DTA `W912CH` 19, WHS `HQ0034` 18, AEDC `FA9101` 17, CDAO `HQ0883` 13, **DIU `HQ0845` 8**, USMC `M67854` 8, SDA `FA2401` 6.

DIU awards: PIID prefix `HQ0845` (office name prints as `DIRECTOR`; the `createdBy` field on those rows is an `@DIU.MIL` address). This is the machine-readable DIU award list. Seen for the CADDS (Containerized Autonomous Drone Delivery System) project: Valinor Dispatch, Swarm Defense Technologies, StrixDrones USA, Quantum-Systems Inc, Tesseract Ventures.

### 2.3 Fields (namespace `ns1 = https://www.fpds.gov/FPDS`)

Root element per entry is one of `ns1:award`, `ns1:IDV`, `ns1:OtherTransactionAward`, `ns1:OtherTransactionIDV`.

| Field | Path under the root |
|---|---|
| PIID / mod | `*ID/*ContractID/PIID`, `.../modNumber` (`0` = new) |
| dates | `contractDetail/relevantContractDates/{signedDate,effectiveDate,currentCompletionDate,ultimateCompletionDate}` (for `award` the `contractDetail` wrapper is absent) |
| dollars | `dollarValues/{obligatedAmount,baseAndAllOptionsValue,nonGovernmentalDollars}`, `totalDollarValues/{totalObligatedAmount,totalBaseAndAllOptionsValue}` |
| office | `purchaserInformation/contractingOfficeAgencyID[@name]`, `contractingOfficeID[@name]`, `fundingRequestingOfficeID[@name]` |
| type | `contractData/contractActionType[@description]`, `contractData/typeOfAgreement` (`PROTOTYPE` / `PRODUCTION`) |
| description | `contractData/descriptionOfContractRequirement` (often just "PROTOTYPE OTHER TRANSACTION AGREEMENT") |
| non-traditional | `contractData/nonTraditionalGovernmentContractorParticipation[@description]` |
| PSC / NAICS | `PSCCode` (OT) or `productOrServiceInformation/{productOrServiceCode,principalNAICSCode}` (contracts) |
| vendor | `vendor/vendorHeader/{vendorName,vendorAlternateName}` |
| vendor ids | `vendor/vendorSiteDetails/entityIdentifiers/vendorUEIInformation/{UEI,ultimateParentUEI,ultimateParentUEIName}`, `.../cageCode` |
| vendor location | `vendor/vendorSiteDetails/vendorLocation/{streetAddress,city,state,ZIPCode,phoneNo}` |
| **SAM registration** | `vendor/vendorSiteDetails/ccrRegistrationDetails/{registrationDate,renewalDate}` |
| consortium | `consortiaInformation/consortiaFlag` |
| competition | `competition/solicitationProcedures[@description]`, `extentCompeted` |
| audit | `transactionInformation/{createdBy,createdDate,lastModifiedDate}` |

---

## 3. USAspending.gov

### 3.1 Endpoints (no key)

| Endpoint | Use |
|---|---|
| `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` | list awards. Body: `{"filters":{...},"fields":[...],"page":1,"limit":100,"sort":"Start Date","order":"desc"}`. `limit` max 100. Pagination: `page` + `page_metadata.hasNext`. |
| `POST .../api/v2/search/spending_by_award_count/` | counts by type group for the same `filters` |
| `GET .../api/v2/awards/{generated_internal_id}/` | detail: recipient address, `recipient_hash`, `executive_details`, awarding office name, set-aside, `number_of_offers_received`, `parent_award` |
| `GET .../api/v2/recipient/{recipient_id}/?year=all` | recipient profile: `total_transaction_amount`, `total_transactions`, `business_types`, address, alternate names. Took 56 s. |
| `GET .../api/v2/references/award_types/` | code list |

Filters that worked: `time_period:[{"start_date","end_date","date_type":"new_awards_only"}]`, `award_type_codes` (contracts `["A","B","C","D"]`, grants `["02","03","04","05"]`; do not mix groups in one call), `agencies:[{"type":"awarding","tier":"toptier","name":"Department of Defense"}]`, `recipient_type_names:["small_business"]`, `keywords:["..."]`, `psc_codes:["1550"]` or the tree form `{"require":[["Research and Development"]]}`, `recipient_search_text:["<UEI>"]`.

Contract fields: `Award ID`, `Recipient Name`, `Recipient UEI`, `recipient_id`, `Start Date`, `End Date`, `Award Amount`, `Awarding Agency`, `Awarding Sub Agency`, `Contract Award Type`, `Description`, `NAICS{code,description}`, `PSC{code,description}`, `Place of Performance State Code`, `Recipient Location{city_name,state_code,address_line1,zip5}`, `Base Obligation Date`, `Last Modified Date`, `generated_internal_id`. For grants swap `Contract Award Type` for `Award Type` and add `Assistance Listings`.

### 3.2 Queries and counts

Counts from `spending_by_award_count`, `recipient_type_names=["small_business"]`, `date_type=new_awards_only`:

| Agency | 2026-07-03..10-01 (last 90 d) | 2026-04-01..07-02 |
|---|---|---|
| DoD | contracts 2, grants 1 | contracts 451,456, IDVs 4,756 |
| DOE | contracts 446, grants 38 | contracts 330, grants 59 |
| NASA | contracts 1,390, IDVs 759 | contracts 793 |

DoD keyword counts (small business, new, 2026-04-01..07-02, contracts): `unmanned aircraft` 86; `counter-UAS` 5; `autonomous` 28; `hypersonic` 10; `robotic` 32; `humanoid` 0; `directed energy` 4; `SBIR Phase I` 11; `microreactor` 1; `battery` 2,144 (commodity noise); `additive manufacturing` 8; `satellite` 225; `semiconductor` 595 (parts noise); PSC `1550` (unmanned aircraft) 72.

R&D PSC tree (`psc_codes={"require":[["Research and Development"]]}`), small business, new awards:

| Pull | Rows | Unique UEIs | Notes |
|---|---|---|---|
| DoD 2026-04-01..07-02 | **662** | 532 | Air Force 344, Navy 147, Army 105. Median $250k. 260 match a thesis regex in `Description`. |
| NASA 2026-07-03..10-01 | **316** | 254 | 259 mention SBIR/STTR; **196 are Phase I** (award IDs `80NSSC26C....`, about $225k, dated 2026-09-30) |
| DOE 2026-07-03..10-01 | 2 | | DOE R&D goes out as grants, see next row |
| DOE grants, small business, 2026-07-03..10-01 | **38** | 36 | `DEAR...` = ARPA-E, `DESC...` = Office of Science (SBIR), `DEFE...` = fossil/carbon, `DEEE...` = EERE |
| DOE grants, all recipients, same window | 491 | | mostly universities and labs |

**AFWERX**: there is no AFWERX award list or API. AFWERX SBIR/STTR awards are the USAspending rows whose `Award ID` starts with `FA8649` (awarding office name in award detail: `FA8649 USAF SBIR STTR CNTRCTNG`). In the DoD R&D pull: 199 such awards, all purchase orders (`FA864926P....`), 152 at about $75k and 45 at about $100-110k, 183 of them dated 2026-06. That is the AFWERX Phase I cohort.

**First-time recipient test** (no native filter exists): for each candidate UEI call `spending_by_award_count` with `time_period=[{"start_date":"2007-10-01","end_date":"<window_start - 1 day>"}]` and `recipient_search_text=["<UEI>"]`, sum the groups. Run on 40 thesis-matching AFWERX Phase I recipients: 9 had zero prior federal awards, 5 had exactly one, 19 had five or more (SBIR mills such as Opto-Knowledge Systems with 160, PC Krause with 166).

Zero-prior AFWERX Phase I recipients seen: DroneNX LLC (El Segundo), Skytrax LLC, OrbitalWorx LLC (Austin), Poseidon Tech Corporation (Foster City), Military Toys LLC (Austin), General Robotics Technology, Inc. (Redmond), FibrX Inc (Henderson NV), Aktoh Cyber LLC, Atlas Technologies Global Inc.

### 3.3 Reliability (measured)

- No 429s. Latency is bimodal: 0.3-5 s when warm, 25-58 s when cold. In the 40-call first-time check: median 9.6 s, 17 calls over 20 s, max 54.8 s.
- Three gateway failures (one 502, two 504, each after about 60 s) in about 20 paged list calls. An immediate retry of the same page succeeded every time. Set timeout >= 120 s and retry.
- Every response carries a `messages` array with a boilerplate notice about the 2007-10-01 floor; ignore it.

---

## 4. SBIR / STTR

### 4.1 Public API: down

`GET https://api.www.sbir.gov/public/api/awards?agency=DOD&year=2026&rows=5` -> **HTTP 403** `{"message":"Forbidden"}` (`x-amzn-errortype: ForbiddenException`). Same for `/public/api/firm?keyword=robotics` and `/public/api/solicitations?keyword=robotics`, with a contact UA, a browser UA and the curl default UA. The docs page `https://www.sbir.gov/api` states the APIs are "currently undergoing maintenance". The legacy `https://www.sbir.gov/api/awards.json` path is a 404 page. The docs page also shows the DoD agency code has changed to `DOW`.

Re-test before relying on it; documented params when it returns: `agency`, `year`, `firm`, `ri`, `keyword`, `rows` (default 100), `start`, `format=xml`.

### 4.2 Free alternative: bulk CSV (works)

Linked from `https://www.sbir.gov/data-resources`:

| File | Size | Last-Modified |
|---|---|---|
| `https://data.www.sbir.gov/mod_awarddatapublic_no_abstract/award_data_no_abstract.csv` | 91,504,083 bytes | 2026-10-01 05:47 GMT (regenerated daily) |
| `https://data.www.sbir.gov/awarddatapublic/award_data.csv` (with abstracts) | 367,551,355 bytes | **2026-01-01** (stale; do not use for recent awards) |

- No auth. `accept-ranges: bytes` works. Full 91.5 MB download took 3.0 s.
- Sorted by `Proposal Award Date` descending, so `Range: bytes=0-12000000` returns the newest 23,752 rows (back to 2021-12). An incremental job only needs the first 1-2 MB plus the `etag`.
- 219,649 rows total. Rows per `Award Year`: 2022 6,639; 2023 6,325; 2024 6,412; 2025 5,219; 2026 303.

Columns (41): `Company`, `Award Title`, `Agency`, `Branch`, `Phase`, `Program`, `Agency Tracking Number`, `Contract`, `Proposal Award Date`, `Contract End Date`, `Solicitation Number`, `Solicitation Year`, `Solicitation Close Date`, `Proposal Receipt Date`, `Date of Notification`, `Topic Code`, `Award Year`, `Award Amount`, `UEI`, `Duns`, `HUBZone Owned`, `Socially and Economically Disadvantaged`, `Woman Owned`, `Number Employees`, **`Company Website`**, `Address1`, `Address2`, `City`, `State`, `Zip`, `Contact Name`, `Contact Title`, `Contact Phone`, `Contact Email`, **`PI Name`**, `PI Title`, `PI Phone`, **`PI Email`**, `RI Name`, `RI POC Name`, `RI POC Phone`.

### 4.3 First Phase I to an unknown firm (computed)

Firm key = `UEI`, else `Duns`, else normalized company name. "First-time" = earliest award date for that key is on or after 2025-07-01 and the firm has at most 2 awards ever.

- Phase I awards since 2025-07-01: **1,630**
- of which to first-time firms: **527**
- thesis keyword match on `Award Title`:

| Bucket (regex on title) | recent Phase I | first-time firm |
|---|---|---|
| robotics (`robot|humanoid|manipulat|exoskelet|legged|gripper`) | 34 | 11 |
| autonomy / UAS (`uas|suas|unmanned|drone|autonom|uav|c-uas|swarm`) | 53 | 21 |
| hypersonics / propulsion | 34 | 5 |
| energy (`nuclear|fusion|reactor|batter|grid|energy storage|...`) | 50 | 13 |
| semis / photonics | 54 | 18 |
| space | 104 | 18 |
| manufacturing | 78 | 24 |
| **union, deduped** | | **98** |

Title-only matching has false positives (for example "nuclear delivery of DNA", "Bio-Chip"); the no-abstract CSV has no abstract to disambiguate. For NSF rows pull the abstract from the NSF API (section 5).

Awards since 2025-10-01 by agency/phase: NSF Phase I 138, DoD Phase I 111, NSF Phase II 84, DoD Phase II 44, DOC Phase II 8, DOE Phase II 5. NASA and HHS have nothing after 2025-09 in this file, yet USAspending shows 196 NASA SBIR Phase I contracts dated 2026-09-30. For NASA and AFWERX, USAspending is the fresher source.

### 4.4 Other machine-readable award lists checked

| Source | Result |
|---|---|
| DOE Office of Science SBIR (`https://science.osti.gov/sbir/Awards`) | HTML page linking XLSX files: `/-/media/sbir/excel/2025/FY-2020-2025-SBIR-STTR-Awards.xlsx`, `FY-2025-Phase-I-Release-1-Awards.xlsx`, `FY-2025-Phase-I-Release-2-Awards.xlsx` (112,967 bytes, modified 2026-06-23), `FY-2025-Phase-II-Release-1-Awards.xlsx`. No FY2026 file yet. Not parsed. |
| DoD SBIR topics (`https://www.dodsbirsttr.mil/topics/api/public/topics/search?searchParam={url-encoded JSON}&size=N&page=0`) | Works. `searchParam={"searchText":null,"topicReleaseStatus":[591,592]}` -> `total: 48` open or pre-release topics; `searchText:"robot"` -> 3. Fields: `topicCode`, `topicTitle`, `component`, `program`, `topicStatus`, `solicitationTitle`, `topicStartDate`, `topicEndDate` (epoch ms), `topicManagers[]`. Demand-side context only. |
| NASA TechPort (`https://techport.nasa.gov/api/projects?updatedSince=YYYY-MM-DD`, `/api/projects/{id}`) | Works, no key. 184 projects updated since 2026-09-01. Detail has `leadOrganization`, `otherOrganizations`, `program`, `trlBegin/trlEnd`, `startDate/endDate`. The one sampled was a NASA HQ science project; low yield for startups. |
| NASA SBIR award page (`https://www.nasa.gov/sbir_sttr/awards/`) | 404. Use USAspending `80NSSC..C....` + "SBIR PHASE I" in description. |
| AFWERX (`afwerx.com`) | no list or API (path tried returned 404). Use USAspending `FA8649`. |
| DIU (`https://www.diu.mil/work-with-us/...`) | 3.2 MB server-rendered page, no JSON endpoints found. Use FPDS `HQ0845`. |
| DoD daily contract announcements (`https://www.war.gov/News/Contracts/`, `https://www.defense.gov/News/Contracts/`) | **403 Access Denied** (Akamai) for non-browser clients. |
| SAM.gov (`https://api.sam.gov/opportunities/v2/search`) | 404 without `api_key`; a key needs an account. Not used. |

---

## 5. NSF Awards API

`GET https://api.nsf.gov/services/v1/awards.json?{params}` (no key; 0.2-1.0 s; no throttling in about 25 calls)

Measured behaviour:

- `rpp` up to 100 honoured. `offset` is **0-based** (`offset=1` shifts by one record). `response.metadata = {offset, rpp, totalCount}`.
- `printFields` is ignored: every call returns the full record, including `abstractText` (about 4 KB per award, 128 KB per 30 awards).
- `fundProgramName=` is a loose token match: `fundProgramName=SBIR Phase I` returned 275 rows including SBIR Phase II, STTR Phase I and unrelated programs. Do not use it as a filter.
- `keyword='"SBIR Phase I"'` (quoted phrase, matches the title prefix) is precise: 30 of 30 were `fundProgramName == "SBIR Phase I"`.
- Boolean `OR` between quoted phrases returns 0. Run one call per phrase and merge.
- `progEleCode` is not a valid parameter (`AwardAPI-002 Invalid parameter(s)` inside `response.serviceNotification`, still HTTP 200).
- `dateStart=MM/DD/YYYY` filters on award date. Default sort is relevance, not date; sort client-side on `date`.
- `id={awardId}` fetches one award.

Exact pull used: for each of `"SBIR Phase I"`, `"STTR Phase I"`, `"SBIR Fast-Track"`, `"STTR Fast-Track"`: `keyword=<phrase>&dateStart=01/01/2026&rpp=100&offset=0` -> 63 + 25 + 9 + 1 = 96 unique awards, 95 after dropping one Phase II. By month: 06/2026 44, 07/2026 16, 08/2026 34, 09/2026 1. Thesis title hits: 22 (robotics 5, autonomy 1, energy 7, semis 5, manufacturing 4).

Fields to keep: `id`, `title`, `abstractText`, `date`, `startDate`, `expDate`, `fundProgramName`, `awardeeName`, `awardeeCity`, `awardeeStateCode`, `awardeeAddress`, `awardeeZipCode`, `awardeePhone`, `ueiNumber`, `parentUeiNumber`, `piFirstName`, `piLastName`, **`piEmail`**, `pdPIName`, `fundsObligatedAmt`, `estimatedTotalAmt`, `progEleCode` (537100 = SBIR Phase I, 150500 = STTR Phase I), `dirAbbr`/`divAbbr` (`TIP`/`TI`), `poName`.

The PI email domain is the fastest route to the company domain (for example `elvy@trellisrobotics.com`, `mike@channelrobotics.com`, `tianshu.zhai@hexaspec.com`); about a third use gmail and need the SBIR CSV `Company Website` instead.

---

## 6. ARPA-E projects (undocumented JSON:API behind the React site)

`GET https://arpa-e.energy.gov/jsonapi/custom/index/project` returns **301 to `/JSONAPI/custom/index/project`** (upper case); follow redirects (`curl -L`) or call the upper-case path directly. Header `Accept: application/vnd.api+json`. `?_format=json` returns 406.

- Default order is newest first (`attributes.primary_date`, for example `20260618.56471`).
- Fixed 50 per page. `page[limit]` is ignored; `page[offset]=50` works. Plain `page=0` returns 400. `links.next.href` is provided.
- `meta.count.count` = 1,721 projects.
- Filters that work: `filter[custom_reference_organization_type]=29` (Private Company -> 681), `filter[field_project_status]=selected` (-> 75), `filter[fulltext]=fusion` (-> 119, took 9 s). `meta.filters` lists every option id (programs, research sub-areas such as Nuclear Fission 74, Nuclear Fusion 81, Batteries 27, Manufacturing 129, funding round, status, state).
- Unprefixed params (`field_project_status=selected`) are silently ignored; unknown params (`keyword=`, `search=`) return 400.
- Each page is about 1.1 MB for 50 records because the full program object is nested in every record. Strip `fields.related_programs[].fields` before storing.

Fields: `data[].attributes.title`, `.date`, `.primary_date`, `.redirect_url` (site path `/technologies/projects/<slug>`), `.fields.award` (string, inconsistent format: `"2,000,000"`, `"$3,949,109"`, or null), `.fields.location`, `.fields.state`, `.fields.status` (Selected / Active / Alumni / Cancelled), `.fields.organization[0].fields.title`, `.fields.organization[0].fields.type[0].name`, `.fields.organization[0].fields.website` (empty on the recent ones), `.fields.related_programs[].fields.title`, `.fields.related_technologies[].name`, `.fields.program_director`.

Private-company projects in the newest 150 by year: 2026: 16, 2024: 10, 2023: 23. 2026 selections seen: ROCKS (2026-06-18): Fieldstone Bio, Deep Blue Geophysics, Wetstone Exploration, KoBold Metals, Halliburton, Baker Hughes; CATALCHEM-E (04-07): P2 Science, Oxylus Energy; DC-GRIDS (03-17): OMNIPOWER, GE Vernova; QC3 (03-05): Infleqtion, Phasecraft, Quantinuum, Xanadu, Alice & Bob, Boeing.

The same awards appear later in USAspending as DOE grants with `Award ID` prefix `DEAR` (for example `DEAR0002175` Fieldstone Bio 2026-08-05, `DEAR0002161` OMNIPOWER 2026-07-28, `DEAR0002079` Transmutex USA, `DEAR0002087` Omega-P R&D (NEWTON, nuclear waste transmutation), `DEAR0002177` Lithios $20.0M). The ARPA-E site leads USAspending by 1-4 months.

---

## 7. Features a scorer should compute

Form D
- `formd_first_filing`: first Form D for this CIK, and CIK in the newest block (>= about 2,140,000 as of 2026-09).
- `formd_amount_sold`, `formd_offering_total`, `formd_fill_ratio = sold / offering` (1.0 = closed round; low = still raising, which is the outreach window).
- `formd_days_since_first_sale`, `formd_filing_lag_days`.
- `formd_investor_count`, `formd_avg_check = sold / investors` (1-3 investors with $5M+ = institutional lead; 30+ small checks = party round or syndicate).
- `formd_is_safe` (other-type text matches SAFE / convertible note) vs priced equity.
- `company_age_years = filing_year - yearOfInc`.
- `formd_round_velocity`: months between consecutive D filings for the same CIK; `formd_step_up`: ratio of amounts.
- `formd_new_director`: director who is not an executive officer and was absent from the previous filing (new lead investor). Build a person -> fund lookup over time.
- `founder_repeat`: related person appears on Form Ds of other CIKs (serial founder or professional director).
- `spv_demand`: number of SPV pooled-fund Form Ds naming the company in a trailing 90 days (heat; penalise for pre-consensus).

Government awards
- `first_federal_award`: zero awards for the UEI before the window (USAspending count check) or first row for the UEI in the SBIR CSV.
- `sam_registration_age_days` at award (FPDS `registrationDate`): registered under 18 months before a prototype OT is the strongest formation signal here.
- `award_ladder`: Phase I -> Phase II -> Phase III / OT prototype -> production OT or delivery orders; time between rungs = velocity.
- `agency_breadth`: distinct awarding offices (DIU + AFWERX + SOCOM beats three awards from one office).
- `ot_ceiling_to_obligated = totalBaseAndAllOptionsValue / obligatedAmount` (large ceiling on a small first obligation = the customer plans to scale).
- `nontraditional_flag`, `consortiaFlag`.
- `sbir_mill_penalty`: lifetime award count over 20 with no Form D (services shop, not a venture company).
- `employees_at_award` (SBIR CSV `Number Employees`): 1-10 at first award.
- `topic_pull`: award description matches a currently open DoD topic or a multi-vendor project (5 vendors on DIU CADDS = a category forming).

Cross-source
- `capital_plus_customer`: Form D within plus or minus 6 months of a first federal award. Seen this run: StrixDrones USA (DIU OT 2026-06-11, Form D 2026-09-11), Parallel Robotics (NSF Phase I 2026-07-06, Form D 2026-09-21), Twenty Technologies (CDAO OT 2026-06-18, Form D 2026-09-25).
- `novelty`: entity absent from all sources 12 months ago.
- `acceleration`: count of new capital/award events in the trailing 90 days vs the prior 90.

---

## 8. Entity resolution

Keys by source:

| Source | Native keys |
|---|---|
| Form D | CIK, accession, issuer name, previous names, phone, street/zip, related person names; EIN via `data.sec.gov/submissions` |
| FPDS | UEI, ultimate parent UEI, CAGE, vendor name + alternate (DBA) name, street/zip, phone |
| USAspending | UEI, `recipient_id` (hash + `-C`/`-P`/`-R` level), name, address |
| SBIR CSV | UEI, DUNS, company name, **website**, contact + PI email |
| NSF | UEI, awardee name, **PI email** |
| ARPA-E | organization title, city/state, slug |

Recipe:

1. Government sources join to each other exactly on **UEI**.
2. Form D has no UEI. Join Form D to the UEI world on normalized name (lower-case, strip punctuation and `inc|corp|co|llc|ltd|pbc|holdings|usa|the`), confirmed by state or zip, then by phone, then by person-name overlap (Form D executive vs SBIR PI/contact, NSF PI).
3. Domain, in priority order: SBIR CSV `Company Website`; domain of NSF `piEmail` / SBIR `Contact Email` if not a free-mail domain; otherwise a web search on name + city (out of scope for this card).
4. Founder: Form D `relatedPersonInfo` with role `Executive Officer`; signer with `signatureTitle` CEO/President; SBIR `PI Name` (technical founder); NSF `piFirstName piLastName`.
5. GitHub org: not available from these sources; resolve from the domain.

Measured join yield (30-day Form D operating companies, n = 1,054 normalized names): 34 matched SBIR awardees (2023+), 2 matched new DoD OT vendors, 0 matched the DoD R&D small-business contract pull. Low overlap is expected and useful: the sets are mostly disjoint, so the union is the candidate universe and the intersection is the high-conviction tier.

---

## 9. Gotchas (all hit during this run)

1. SEC returns 403 without a contact User-Agent.
2. `form.idx` repeats multi-issuer filings once per co-issuer CIK (5,727 rows vs 5,667 unique accessions). Dedupe on accession.
3. Form type in the idx must be matched exactly (`D`, `D/A`); a prefix match also catches `DEF 14A`, `DFAN14A`, etc.
4. 68% of Form D filings are pooled investment funds. Filter on `industryGroupType` and `3C*` exemptions before anything else.
5. `yearOfInc/value` is absent for issuers older than five years (only `overFiveYears=true`).
6. `totalOfferingAmount` can be the literal string `Indefinite`.
7. Issuer address is often a registered agent (Dover DE, Sheridan WY, Wilmington DE). Do not geo-score on it; prefer related-person addresses.
8. SAFE rounds are `isOtherType=true` with free text; spelling varies ("SAFE", "Simple Agreement for Future Equity", "Convertible Notes").
9. Many seed rounds never file a Form D, and filings can be late (p75 lag 53 days). Absence is not evidence.
10. Foreign issuers use state codes like `A0`, `A6`, `X0`, `Y7` (Canadian provinces, UK, Guernsey).
11. EDGAR FTS caps at 10,000 results per query and returns HTTP 200 with an error body (no `hits`) past the cap and on transient failures.
12. QTR folder for a new quarter is empty until the first evening post; use FTS for same-day data.
13. DoD rows are embargoed 90 days in FPDS and USAspending. Anything "DoD, last 90 days" returns near zero. This is not an outage.
14. USAspending has no OT award type. OTs exist only in FPDS (`AWARD_TYPE:"OTHER TRANSACTION AGREEMENT"`).
15. Consortium OTs hide the real performer: 57 of the 318 new DoD OTs (57 of the 81 Army ACC-RI `W519TC` awards) name "ONE NATION INNOVATION" as vendor, and 10 more name Advanced Technology International; SOSSEC also appears. The actual performer is not in the record. Detect by vendor repetition or `consortiaFlag`, and exclude from first-time logic.
16. FPDS OT descriptions are often empty of content ("PROTOTYPE OTHER TRANSACTION AGREEMENT", "BASE AWARD", "SB-AMTI"). Classify by vendor, not description.
17. Small OT obligations can be placeholders ($10,000 SB-AMTI awards to Anduril, Blue Origin, Boeing, Umbra on 2026-06-02..08). Use `totalBaseAndAllOptionsValue` too.
18. FPDS ATOM returns 10 rows per page and no total; derive the total from the `rel="last"` link. curl needs `-g`.
19. USAspending: 25-58 s cold latency and intermittent 502/504. Retry the same page; do not parallelise.
20. USAspending `keywords` matches description text and is noisy for commodity words (`battery` 2,144, `semiconductor` 595). Combine with the R&D PSC tree or NAICS 541715.
21. USAspending `Start Date` is period-of-performance start and can be in the future (rows dated 2026-10-30, 2027-01-01 appeared in a window ending 2026-10-01). Use `Base Obligation Date` for recency.
22. `recipient_type_names=["small_business"]` is self-certified and applies cleanly to contracts only; for DOE grants it drops most startups' peers (38 of 491).
23. SBIR.gov API is 403 across all three resources. The CSV with abstracts is nine months stale; only the no-abstract CSV is current.
24. SBIR CSV coverage lags by agency (NASA/HHS/DOE nothing after 2025-09). Some rows have blank `Proposal Award Date`. `State` is a full name ("California"), names are padded with trailing spaces in some rows, and some rows have typos ("Z-Polyemrs.LLC").
25. SBIR program lapse: 2026 volume is about 5% of normal. Normalise per-period features by agency volume.
26. NSF API: `fundProgramName` is fuzzy, `printFields` ignored, OR of phrases returns 0, invalid params return HTTP 200 with `serviceNotification`.
27. ARPA-E: lower-case path 301s to upper case; `page[limit]` ignored; `award` is a free-text string; organization `website` empty on new records.
28. `war.gov` / `defense.gov` contract announcements block non-browser clients (403). SAM.gov needs a key.
29. Personal data: Form D, SBIR CSV and NSF expose founder names, phones and emails. They are public records, but store only what the product displays (name, role, company) and keep phones/emails out of the UI.

---

## 10. Fixtures

In `/Users/noel/antifund/pipeline/fixtures/capital/`:

| File | What |
|---|---|
| `edgar_fts_formD_robot.json` | raw FTS response, `q=robot OR robotics OR humanoid`, `forms=D`, 2026-09-01..10-01 (8 hits) |
| `edgar_daily_form_idx_20260930_excerpt.idx` | header + first 60 D / D/A rows of `form.20260930.idx` |
| `edgar_formD_primary_doc.xml` | raw `primary_doc.xml` (Salem Robotics, SAFE) |
| `edgar_formD_parsed_thesis_sample.json` | DERIVED: 23 parsed Form D records (stage S4), suggested normalized shape |
| `fpds_atom_dod_ot_new_page0.xml` | raw FPDS ATOM page 0 (10 entries) for new DoD OT awards 2026-04-01..07-02 |
| `fpds_dod_ot_new_parsed_sample.json` | DERIVED: 60 of the 318 parsed new DoD OTs, newest SAM registrants first |
| `usaspending_spending_by_award_dod_uas.json` | request body + raw response (DoD, small business, new awards, keyword "unmanned aircraft") |
| `sbir_award_data_no_abstract_head150.csv` | header + newest 150 rows of the SBIR bulk CSV |
| `nsf_awards_sbir_phase1_thesis.json` | 12 raw NSF award records (thesis-matching Phase I / Fast-Track, with abstracts) |
| `arpae_projects_private_company.json` | first 3 records + `meta` + `links` of the ARPA-E private-company query |

---

## 11. Entities seen in live data (evidence)

Only what the responses contained; sector labels come from the award text or issuer name, not from outside knowledge.

| Entity | What the data showed | Evidence |
|---|---|---|
| Salem Robotics, Inc. (Austin TX) | Form D 2026-09-08; inc 2024; SAFE; $1.82M sold of $5.1M; 5 investors; execs Caleb Horan, Janak Panthi | https://www.sec.gov/Archives/edgar/data/2134746/000213474626000002/primary_doc.xml |
| Parallel Robotics (Ann Arbor MI) | Form D 2026-09-21: $7.31M sold of $11.31M, 32 investors, exec Shorya Awtar; plus NSF SBIR Phase I 2507711 (2026-07-06, surgical robot architecture) | https://www.sec.gov/Archives/edgar/data/2155434/000215543426000001/primary_doc.xml |
| Grex Defense, Inc. (Colorado Springs CO) | Form D 2026-09-30; inc 2026; SAFE; $525k sold of $3.0M; 8 investors | https://www.sec.gov/Archives/edgar/data/2155302/000089183926000413/primary_doc.xml |
| STRIXDRONES USA Inc | DIU OT `HQ0845269E045` (CADDS, $625k, 2026-06-11) then Form D 2026-09-11: $5.0M sold of $7.2M, 3 investors | https://www.sec.gov/Archives/edgar/data/2154122/000121390026099351/primary_doc.xml |
| Valinor Dispatch, Inc. (Washington DC) | DIU OT `HQ0845269E042`, CADDS, $498.7k, signed 2026-06-01; SAM-registered 2025-07-16 | https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:%22HQ0845269E042%22 |
| Swarm Defense Technologies, Inc (Auburn Hills MI) | DIU OT `HQ0845269E046` $956k (2026-06-16); SAM-registered 2025-03-10; AFWERX Phase I `FA864926P0137`; 1 prior federal award | https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:%22HQ0845269E046%22 |
| DroneNX LLC (El Segundo CA) | AFWERX Phase I `FA864926P0183`, $72k, 2026-06-30, battery manufacturing for sUAS loitering munitions; zero prior federal awards | https://api.usaspending.gov/api/v2/awards/CONT_AWD_FA864926P0183_9700_-NONE-_-NONE-/ |
| Poseidon Tech Corporation (Foster City CA) | AFWERX `FA864926P0264`, $110k, 2026-06-25, autonomous aircraft logistics; zero prior federal awards | https://api.usaspending.gov/api/v2/awards/CONT_AWD_FA864926P0264_9700_-NONE-_-NONE-/ |
| General Robotics Technology, Inc. (Redmond WA) | AFWERX `FA864926P0074`, $74k, 2026-06-25, counter-UAS simulator; zero prior federal awards | https://api.usaspending.gov/api/v2/awards/CONT_AWD_FA864926P0074_9700_-NONE-_-NONE-/ |
| Trellis Robotics Inc (Mountain View CA) | NSF STTR Phase I 2538085, 2026-08-11, $305k, vine-robot inspection of confined industrial assets; PI Elvymond Yao | https://api.nsf.gov/services/v1/awards.json?id=2538085 |
| CX2, Inc. (El Segundo CA) | SOCOM OT `H92402269E003`, $1.05M, 2026-04-06, RF-seeking payload kits; SAM-registered 2024-05-29 | https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:%22H92402269E003%22 |
| Mach Industries Inc (Huntington Beach CA) | NAVAIR OT `N004212690012` (RIMES prototype, $2.0M, 2026-05-29) and AFWERX `FA864926P0127` (Viper strike munition); SAM-registered 2023-01-09 | https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:%22N004212690012%22 |
| Allen Control Systems, Inc. (Austin TX) | USMC OT `M678542690070`, $6.25M, 2026-05-21, L-MADIS direct-fire capability prototype; SAM-registered 2023-01-21 | https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=PIID:%22M678542690070%22 |
| Blue Laser Fusion, Inc. (Goleta CA) | Form D 2026-09-25; inc 2022; $25.0M sold of $50M; 118 investors; execs include Shuji Nakamura | https://www.sec.gov/Archives/edgar/data/1938570/000119312526401614/primary_doc.xml |
| Cosmic Robotics, Inc. (San Francisco CA) | NASA SBIR Phase I `80NSSC25C0179`, 2025-09-23, $149.5k, truss-traversing outfitting robot; 8 employees; first SBIR for the firm | https://data.www.sbir.gov/mod_awarddatapublic_no_abstract/award_data_no_abstract.csv |
| Deep Blue Geophysics, LLC (Los Angeles CA) | ARPA-E ROCKS selection 2026-06-18, $2.2M, UAV-based controlled-source electromagnetics for ore characterization | https://arpa-e.energy.gov/JSONAPI/custom/index/project?filter%5Bcustom_reference_organization_type%5D=29 |
