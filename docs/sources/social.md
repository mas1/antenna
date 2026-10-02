# Source card: `social` (free social / attention signals)

Measured live from this machine on 2026-10-01 (UTC evening) with `curl` / `python3 urllib`.
User-Agent on every call: `antifund-sourcing-research/0.1 (mason@alterity.systems)`.
Everything below is what the endpoints actually returned today, not what docs or memory say.
Fixtures: `/Users/noel/antifund/pipeline/fixtures/social/`.

## TL;DR scoreboard

| # | Source | Works free today? | Use it for | Signal strength (pre-consensus) |
|---|--------|-------------------|------------|---------------------------------|
| 1 | Hacker News (Algolia) | YES, no key, no visible rate limit | Weekly mention series per company/domain; Launch HN / Show HN discovery | **High** (best free source here) |
| 2 | Bluesky AppView | PARTIAL: `searchPosts` only on `api.bsky.app` and only page 1 (cursor = 403); profile/graph on `public.api.bsky.app` | Weekly mention counts via `hitsTotal`, domain mentions, follower counts | Medium-low (news/bot heavy) |
| 3 | Reddit | `.json` is DEAD (403). `.rss` works at 1 request/minute | New-post firehose for r/robotics, r/fusion, r/drones etc.; builders posting pre-launch | Medium (good raw builders, no scores) |
| 4 | YouTube RSS | YES, no key | Demo-video cadence, views, likes per channel | Medium-high for hardware companies |
| 5 | Mastodon | PARTIAL: hashtag timelines + 7-day tag history; public timeline and status search need auth | Hashtag volume only | Low |
| 6 | Lobsters | YES for tag/domain/newest JSON; search has no JSON | Domain mentions, hardware tag | Low (little thesis coverage) |
| 7 | GitHub social graph | PARTIAL: repo -> stargazers is NOT listable (401 unauth, 404 with token, GraphQL returns 0). user -> starred works; GH Archive works | "Which watched people starred what, and when" (inverted star graph) | **High** when driven from a seed list of people |
| 8 | Substack | RSS + per-publication `/api/v1/archive` work; global search returns empty | Newsletter coverage of a company, reaction counts | Low-medium (lagging) |
| 9 | X follower counts | `followbutton/info.json` DEAD (200, empty body). `syndication.twitter.com/srv/timeline-profile` works (30 req / 15 min). `api.fxtwitter.com` and `api.vxtwitter.com` work | Follower count + last 20 posts' engagement | Medium-high (fragile, third-party) |

---

## 1. Hacker News via Algolia

### Endpoints (all GET, no auth, no key)

| Purpose | URL template |
|---|---|
| Time-ordered search | `https://hn.algolia.com/api/v1/search_by_date?query={q}&tags={tags}&numericFilters={nf}&hitsPerPage={n}&page={p}` |
| Relevance search | `https://hn.algolia.com/api/v1/search?...` (same params) |
| Item + full comment tree | `https://hn.algolia.com/api/v1/items/{id}` |
| User | `https://hn.algolia.com/api/v1/users/{username}` -> `{about, karma, username}` |
| Official item (score, kids) | `https://hacker-news.firebaseio.com/v0/item/{id}.json` |
| Official user (submitted ids) | `https://hacker-news.firebaseio.com/v0/user/{id}.json` |

Params that matter (all verified):
- `tags=story | comment | show_hn | ask_hn | (story,comment)` (parentheses = OR), `author_{name}`, `story_{id}`.
- `numericFilters=created_at_i>=A,created_at_i<B` (unix seconds; comma = AND). Also `points>N`, `num_comments>N`.
- `typoTolerance=false` **mandatory**. With it on, `humanoid` returned 6,782 stories (top hit: "The Humanism Driving the Anti-A.I. Movement"); off: 1,296.
- `advancedSyntax=true` + quoted query for exact phrases (`"humanoid robot"` -> 492 stories).
- `restrictSearchableAttributes=url` for domain mentions (`figure.ai` -> 23 stories); `=title` to stop matches on author names (query `robot` matched user `robot1996`).
- `hitsPerPage=0` returns only `nbHits` (cheapest way to count). Max `hitsPerPage=1000`. Hard cap of 1,000 retrievable hits per query; paging past it returns `{"message":"you can only fetch the 1000 hits for this query..."}` with `nbHits:0`.
- `attributesToRetrieve=created_at_i,objectID&attributesToHighlight=` shrinks payloads (the `_highlightResult` block still comes back; strip client-side).

Pagination: `page` (0-based), `nbPages`, `nbHits`, `exhaustiveNbHits`.
Rate limit: no rate-limit headers are returned. ~170 requests in ~10 minutes at 0.35-0.7 s spacing produced zero 429s; latency 0.1-0.25 s. (The commonly quoted 10,000/hour per IP was not verified.)

### Weekly bucketing recipe
Two options, both tested:
1. One call per week per term with `hitsPerPage=0` and a `created_at_i` window, read `nbHits` (12 calls for 12 weeks).
2. One call with `hitsPerPage=1000&attributesToRetrieve=created_at_i` and bucket client-side (works when total < 1,000; `Unitree` all-time story+comment = 423 hits in a single call).

Measured 12 weekly buckets ending 2026-10-01 (oldest -> newest):

| Term | tags | counts |
|---|---|---|
| humanoid | story | 4, 6, 11, 6, 3, 16, 17, 7, 6, 10, 2, 6 |
| humanoid | comment | 9, 25, 65, 21, 20, 17, 44, 58, 39, 36, 10, 26 |
| Anduril | (story,comment) | 7, 6, 7, 19, 8, 4, 2, 9, 11, 2, 3, 4 |
| "fusion reactor" | (story,comment) | 1, 4, 3, 1, 2, 4, 4, 0, 0, 3, 1, 1 |
| Helion | (story,comment) | 0, 0, 1, 0, 2, 1, 1, 2, 0, 2, 2, 2 |
| Skydio | (story,comment) | 2, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 2 |
| Unitree | (story,comment) | 0, 0, 16, 1, 2, 8, 6, 5, 0, 1, 2, 5 |
| physicalintelligence.company (url) | story | all zeros |
| "Figure AI" | (story,comment) | 0 x9, 1, 0, 0 |

### Discovery queries run
- Launch HN, last 180 days: `query="Launch HN"&advancedSyntax=true&tags=story&restrictSearchableAttributes=title` -> **55 hits**. Thesis-aligned: Nori Robotics, Hebbian Robotics, Salem Robotics, ProvenMetal, Discovered Materials, Rise Reforming, Mireye, General Instinct, Transload.
- Show HN, last 90 days, title-restricted, `nbHits` per term: robot 40, robotics 8, humanoid 1, drone 11, UAV 0, autonomous 38 (mostly coding agents), fusion 4, nuclear 3, battery 5, grid 28 (mostly UI grids), semiconductor 0, chip 7 (CHIP-8, chiptune), FPGA 3, satellite 4, rocket 1 (Rocket League), manufacturing 6, CNC 3, PCB 5, lidar 3, defense 5, motor 2, actuator 0.

### Fields to keep
`hits[].objectID` (HN id; evidence URL = `https://news.ycombinator.com/item?id={objectID}`), `title`, `url`, `author`, `points`, `num_comments`, `created_at`, `created_at_i`, `story_text` (Launch HN body, contains the company URL and founder intro), `_tags`. For comments: `comment_text`, `story_id`, `story_title`, `story_url`.

---

## 2. Bluesky

### What works unauthenticated today

| Call | Host | Result |
|---|---|---|
| `app.bsky.feed.searchPosts` | `public.api.bsky.app` | **403** HTML (BunnyCDN), with custom and browser UA |
| `app.bsky.feed.searchPosts` | `api.bsky.app` | **200** |
| `searchPosts` with `cursor=` (any value) | `api.bsky.app` | **403** "Request forbidden by administrative rules." |
| `app.bsky.actor.searchActors`, `searchActorsTypeahead` | `public.api.bsky.app` | 200 (cursor returned) |
| `app.bsky.actor.getProfile`, `getProfiles` | `public.api.bsky.app` | 200 |
| `app.bsky.feed.getAuthorFeed` (cursor works) | `public.api.bsky.app` | 200 |
| `app.bsky.graph.getFollowers`, `getFollows` | `public.api.bsky.app` | 200 |

URL template: `https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={q}&sort=latest|top&since={ISO}&until={ISO}&lang=en&domain={domain}&url={url}&tag={tag}&limit={1..100}`
- `limit` max 100 (`limit=101` -> 400 InvalidRequest; `limit=100` returned 99 posts).
- Response has `hitsTotal` (capped at 10,000) so **weekly counts cost one call each with `limit=1`**.
- Since cursors are blocked, page backwards with `until={createdAt of last post}`; the boundary post repeats, so dedupe on `uri`.
- `q=*&domain=example.com` works as "all posts linking to this domain".
- No rate-limit headers; ~45 calls at 0.3-0.9 s spacing, no 429s. `public.api` responses carry `cache-control: public, max-age=30`.

### Queries run
| Query | Result |
|---|---|
| `q=humanoid robot&sort=latest` | hitsTotal 10000 (cap) |
| `q=Anduril`, week 09-24..10-01 / prior week | 206 / 192 |
| `q=figure&domain=figure.ai` | 220 |
| `q="fusion startup"&sort=top&since=2026-09-01` | 19 |
| `q=robotics startup&sort=top&since=2026-09-01&lang=en` | 131 |
| `q=nori&domain=norirobotics.com` | 13 (mostly HN mirror bots) |
| 8 weekly buckets, `Unitree` | 264, 816, 441, 113, 101, 127, 103, 73 |
| 8 weekly buckets, `"Physical Intelligence"` | 31, 12, 12, 18, 10, 10, 5, 9 |
| 8 weekly buckets, `Anduril` | 249, 170, 204, 198, 301, 213, 192, 206 |
| `searchActors q=fusion energy` | 8 actors incl. Kronos Fusion Energy, Jeff Lawson (CEO of Inertia) |

### Fields to keep
`posts[].uri`, `.author.did`, `.author.handle`, `.record.createdAt`, `.record.text`, `.record.embed.external.uri` (the linked domain), `.record.facets[].features[].uri`, `.likeCount`, `.repostCount`, `.replyCount`, `.quoteCount`, `.bookmarkCount`. Profile: `did`, `handle`, `displayName`, `description`, `followersCount`, `followsCount`, `postsCount`, `createdAt`.

---

## 3. Reddit

- `https://www.reddit.com/r/robotics/new.json?limit=5` -> **403** (190 KB HTML block page) with the contact UA and with a Chrome UA.
- `https://api.reddit.com/r/robotics/new` -> **403**. `https://old.reddit.com/r/robotics/new.json` -> **302** to `/login`.
- `/r/robotics/about.json` -> 403. So no scores, no subscriber counts, no comment counts without OAuth.
- **RSS (Atom) still works**:

| Template | Verified |
|---|---|
| `https://www.reddit.com/r/{sub}/new/.rss?limit={<=100}` | robotics (100 entries), drones (25), fusion (25), hardware (25) |
| `https://www.reddit.com/r/{a}+{b}+{c}/new/.rss?limit=100` | 6 subs in one call -> 100 entries (robotics 24, fusion 20, nuclear 19, hardware 17, drones 14, AerospaceEngineering 6) |
| `https://www.reddit.com/r/{sub}/top/.rss?t=week&limit=25` | 25 |
| `https://www.reddit.com/r/{sub}/search.rss?q={q}&restrict_sr=1&sort=new&limit=25` | `startup` in r/robotics -> 25 entries back to 2026-04-03 |
| `https://www.reddit.com/search.rss?q={q}&sort=new` | works but leads with subreddit entries; noisy |
| `https://www.reddit.com/r/{sub}/comments/{id}/.rss` | post + comments |
| `https://www.reddit.com/user/{name}/.rss` | user overview |

- **Measured rate limit: 1 request per ~60 s per IP.** Every 200 returns `x-ratelimit-used: 1`, `x-ratelimit-remaining: 0.0`, `x-ratelimit-reset: ~57`. A second request inside the window returned **429** (empty body); one over-limit request hung 48 s and timed out. Honour `x-ratelimit-reset`.
- Volume: r/robotics 100 newest posts span 2026-09-23 -> 10-01 (~12 posts/day), so one multi-subreddit call every few minutes keeps up.
- Entry fields: `id` (`t3_xxxxx`), `title`, `author/name` (`/u/...`), `author/uri`, `category@term` (subreddit), `published`, `updated`, `link@href` (permalink), `content` (HTML; the external link is the `[link]` anchor). **No score, no comment count.**

---

## 4. YouTube channel RSS

- `https://www.youtube.com/feeds/videos.xml?channel_id={UC...}` -> 200 Atom, **15 most recent uploads, no pagination**, `cache-control: public, max-age=900`.
- `https://www.youtube.com/feeds/videos.xml?playlist_id=UULF{channel id minus "UC"}` -> long-form uploads only (identical to channel feed for the channels tested; 404 when the channel has no uploads).
- Per entry: `yt:videoId`, `title`, `published`, `updated`, `media:group/media:description`, `media:community/media:statistics@views`, `media:community/media:starRating@count` (= likes).
- Handle -> channel id: `GET https://www.youtube.com/@{handle}` (0.8-1.4 MB HTML) and regex `"externalId":"(UC[\w-]{22})"` or `<link rel="canonical" href=".../channel/UC...">`. Worked 4/4 with the contact UA, no consent wall from this US IP.
- No subscriber count in the feed (the channel HTML contains several "N subscribers" strings for different channels; not a reliable scrape).

Measured:

| Channel | channel_id | uploads last 30d / 90d | median views (15) |
|---|---|---|---|
| Figure | UCYlq-KmwPjc1DtsGmthFqSQ | 4 / 5 | 20,410 |
| 1X | UCoHslVexR2q57wUoCRfdUsg | 0 / 1 | 94,342 |
| Unitree | UCsMbp4V8oxzHCMdOUP-3oWw | 4 / 8 | 5,645,925 |
| Boston Dynamics | UC7vVhkEfw4nOGp8TyDk7RcQ | 3 / 11 | 95,424 (15 entries since 2026-05-29) |
| Physical Intelligence | UC7S_imQ6iv9j5oVf2oJvvFg | feed 200 with **0 entries**; UULF 404 | n/a |

---

## 5. Mastodon

| Call | Result |
|---|---|
| `GET https://{instance}/api/v1/timelines/tag/{tag}?limit={<=40}` | 200 (mastodon.social, fosstodon.org); paginate via `Link` header `max_id` |
| `GET /api/v1/tags/{tag}` | 200, `history[]` = 7 daily `{day, uses, accounts}` |
| `GET /api/v1/trends/tags` | 200 |
| `GET /api/v1/accounts/lookup?acct={name}` | 200 (`followers_count`, `statuses_count`) |
| `GET /tags/{tag}.rss` | 200 |
| `GET /api/v1/timelines/public` | **422** "This method requires an authenticated user" |
| `GET /api/v2/search?q=..&type=statuses` | 200 but **empty** unauthenticated; `type=hashtags` works |

Rate limit (headers): `x-ratelimit-limit: 300` per 5-minute window.
7-day hashtag uses on mastodon.social: robotics 161 (105 accounts), humanoid 8, drones 141, uav 145 (17 accounts: bot-driven), fusion 26, nuclear 93, semiconductors 84, batteries 30, space 799, defensetech 16, hardware 442. Content is mostly news reposts and Bluesky bridges; treat as a weak corroborating signal only.

---

## 6. Lobsters

Working: `https://lobste.rs/newest.json`, `/hottest.json`, `/t/{tag}.json` (comma-separate tags, `?page=N`, 25 per page), `/domains/{domain}.json`, `/~{user}.json`, `/t/{tag}.rss`.
Not working: `/search.json` -> **400** "Unpermitted query or form parameter" (search is HTML only).
Fields: `short_id`, `created_at`, `title`, `url`, `score`, `comment_count`, `submitter_user`, `tags[]`, `comments_url`.
There is no robotics/energy/space tag (closest: `hardware`, `science`, `ai`, `ml`). The hardware tag today is hobbyist/retro content. Useful only as `domains/{company-domain}.json` mention lookup.

---

## 7. GitHub as a social graph

Unauthenticated budget measured: `core 60/hour`, `search 10/min`, `graphql 0` (shared per IP).

| Call | Unauthenticated | With a free token |
|---|---|---|
| `GET /repos/{o}/{r}/stargazers` (also with `Accept: application/vnd.github.star+json`) | **401** "Requires authentication" | **404** (non-admin; tested Hebbian-Robotics/hflow and huggingface/lerobot) |
| GraphQL `repository.stargazers` | n/a | `totalCount: 0, edges: []` while `stargazerCount` = 284 / 27,897 |
| `GET /repos/{o}/{r}/subscribers` | 401 | 404 |
| `GET /repos/{o}/{r}` (`stargazers_count`, `forks_count`, `topics`, `homepage`) | 200 | 200 |
| `GET /repos/{o}/{r}/contributors`, `/forks?sort=newest`, `/events` | 200 | 200 |
| `GET /users/{u}` (`company`, `bio`, `blog`, `twitter_username`, `followers`) | 200 | 200 |
| `GET /users/{u}/followers` | 200 (Link pagination) | 200 |
| `GET /users/{u}/starred` + `Accept: application/vnd.github.star+json` (`starred_at`) | 200 | 200 |
| `GET /users/{u}/events/public` (includes `WatchEvent` = star) | 200, `x-poll-interval: 60` | 200 |
| GraphQL `user.starredRepositories(orderBy: STARRED_AT)` | n/a | 200 (cost 1, 5,000/hour) |
| `https://data.gharchive.org/{YYYY-MM-DD-H}.json.gz` | 200, 28 MB for one hour; `WatchEvent` rows carry `actor.login` + `repo.name` | same |

**Consequence:** "who starred this repo" cannot be listed from the repo side today. Build the overlap graph inverted:
1. Keep a seed list of people (top-lab staff, known hardware founders, YC physical-world founders; pull `company`/`bio` from `/users/{u}`).
2. Poll each seed's `/users/{u}/starred` (star+json) or `events/public`; aggregate stars by repo with timestamps.
3. For full coverage of a specific repo, scan GH Archive hours for `WatchEvent` on `repo.name`.

Live example: seed `kstonekuan` (bio: "building @Hebbian-Robotics (yc s26), prev @janestreet @verkada", 130 followers) starred `Pantheon-Industries-Inc/argus` at 2026-10-01T19:48:29Z, a robot-learning data annotation repo created 2026-09-27 with 17 stars.

---

## 8. Substack / newsletter RSS

- `https://{sub}.substack.com/feed` and `https://{custom-domain}/feed` -> 200 RSS, 20 items, full `content:encoded` (~800 KB per feed). Fields: `title`, `link`, `pubDate`, `dc:creator`, `description`, `enclosure`.
- `https://{sub}.substack.com/api/v1/archive?sort=new&limit={n}&offset={k}` -> 200 JSON. Keep: `id`, `title`, `slug`, `canonical_url`, `post_date`, `audience` (`everyone`/`only_paid`), `reaction_count`, `reactions`, `comment_count`, `restacks`, `wordcount`, `publishedBylines[].name`.
- `https://substack.com/api/v1/publication/search?query=...` -> 200 but `{"results":[]}` for robotics / chips (with and without extra params). `/api/v1/post/search` -> empty. `/api/v1/publication` -> 403. **Discovery by search is dead unauthenticated; maintain a curated publication list.**
- Tested feeds: `thechipletter.substack.com`, `www.construction-physics.com`.

---

## 9. X / Twitter without the paid API

| Endpoint | Result today |
|---|---|
| `https://cdn.syndication.twimg.com/widgets/followbutton/info.json?screen_names={h}` | **Dead**: HTTP 200 with a 0-byte body (custom and browser UA) |
| `https://syndication.twitter.com/srv/timeline-profile/screen-name/{h}` | **Works**: HTML; parse `<script id="__NEXT_DATA__">` -> `props.pageProps.timeline.entries[].content.tweet` (up to 20 tweets: `created_at`, `full_text`, `favorite_count`, `retweet_count`, `reply_count`, `quote_count`) and `.tweet.user.followers_count`, `statuses_count`, `created_at`. Headers `x-rate-limit-limit: 30`, window 15 min, per IP |
| `https://cdn.syndication.twimg.com/tweet-result?id={id}&token=a` | Works: single tweet JSON (`favorite_count`, `conversation_count`, `user`) |
| `https://publish.x.com/oembed?url=https://x.com/{h}` | 200, embed HTML only, no counts (`publish.twitter.com` 301s here) |
| `https://api.fxtwitter.com/{handle}` | **Works** (third-party FixTweet): `user.followers`, `following`, `tweets`, `likes`, `media_count`, `joined`, `description`, `website`, `verification` |
| `https://api.fxtwitter.com/{handle}/status/{id}` | Works: `likes`, `retweets`, `replies`, `views`, `bookmarks` |
| `https://api.vxtwitter.com/{handle}` | Works: `followers_count`, `tweet_count`, `created_at`, `fetched_on` (cached; Figure value was ~28 h stale) |
| nitter.net, nitter.privacydev.net | connection refused |
| nitter.poast.org | DNS does not resolve |
| nitter.tiekoetter.com | 429 |
| lightbrd.com, nitter.space | 403 Cloudflare challenge |
| xcancel.com | 451 "XCancel service is suspended." |

Measured follower counts: Figure_robot 224,378 (fxtwitter) / 224,380 (syndication) / 222,324 (vxtwitter, stale); physical_int 50,715; Helion_Energy 56,154; TokamakEnergy 15,280; NoriRobotics 3,900 (account created 2026-06-22).

---

## Features a scorer should compute

Per entity (company / repo / person), per week:
- `hn_mentions_w` (stories + comments, exact name and domain), `hn_mention_velocity` (4-week mean vs prior 4), `hn_mention_accel` (delta of velocity), `hn_first_seen_at`, `hn_novelty` (weeks since first mention; reward first-ever appearance).
- `hn_launch_flag` (Launch HN / Show HN present), `hn_launch_points`, `hn_launch_comments`, `hn_comment_to_point_ratio`, `hn_distinct_authors` (comment breadth), `hn_founder_karma` and `hn_founder_account_age` (Firebase user).
- `bsky_mentions_w` (hitsTotal), `bsky_domain_links_w`, `bsky_mention_velocity`, `bsky_nonbot_share` (exclude HN mirror accounts), `bsky_followers`, `bsky_follower_delta_w` (snapshot diffs).
- `reddit_posts_w` by subreddit, `reddit_self_promo_flag` (author posts own domain / "looking for feedback before launch"), `reddit_first_seen_at`, `reddit_crosspost_count`.
- `yt_uploads_30d`, `yt_uploads_90d`, `yt_days_since_last_upload`, `yt_median_views_15`, `yt_max_views_30d`, `yt_view_velocity` (re-poll views daily; delta per day), `yt_like_view_ratio`, `yt_cadence_change` (30d vs prior 60d).
- `gh_seed_star_count` (seed people who starred repo), `gh_seed_star_first_at`, `gh_seed_star_velocity_7d`, `gh_top_lab_star_share` (stars from seeds whose `company`/`bio` matches a top-lab list), `gh_star_to_fork_ratio`, `gh_repo_age_days`, `gh_founder_followers`.
- `x_followers`, `x_follower_delta_7d` (snapshot diffs; no history endpoint), `x_account_age_days`, `x_followers_per_day_since_creation`, `x_median_likes_last20`, `x_max_likes_last20`, `x_post_cadence_30d`.
- `substack_mentions_90d` across the curated list, `substack_reaction_sum`.
- Cross-source: `sources_with_signal_count`, `first_seen_source`, `first_seen_at`, `attention_per_age` (total mentions / company age), `breakout_flag` (z-score of this week vs trailing 12 weeks > 2 on any source).

## Entity resolution

- **Domain is the primary key.** HN `url`, Launch HN `story_text` links, Bluesky `record.embed.external.uri`, Reddit `[link]` anchors, GitHub `homepage`, fxtwitter `user.website.display_url` / `raw_description.facets[].replacement` all yield a registrable domain. Normalise to eTLD+1, lower-case, strip `www.`.
- **GitHub org/repo**: from HN/Reddit URLs matching `github.com/{org}/{repo}`; then `/repos/...` -> `homepage` (domain) and `/users/{contributor}` -> `name`, `company`, `twitter_username`.
- **Person**: HN `author` -> Firebase user `about`; GitHub `login` -> `name`, `company`, `bio`, `twitter_username`; Bluesky `did` (stable) + `handle` (custom-domain handles are verified domains); X `id` (stable) + `screen_name`.
- **YC batch**: regex `\(YC ([SWFP]\d{2})\)` on Launch HN titles.
- **Reddit**: `t3_` id + author; no profile metadata, so resolve only through the linked domain.
- **YouTube**: `channel_id` is the stable key; link to a company via the channel page's handle and the domain in descriptions.

## Gotchas (all hit today)

1. HN Algolia typo tolerance silently inflates counts 5x; always send `typoTolerance=false`.
2. HN default search also matches author usernames and comment bodies; restrict attributes.
3. HN 1,000-hit ceiling per query; slice by time for bigger terms. Generic words (grid, chip, rocket, autonomous) are mostly false positives in title search.
4. Bluesky: the documented public host (`public.api.bsky.app`) 403s `searchPosts`; `api.bsky.app` works but 403s any `cursor`. `hitsTotal` caps at 10,000. `until` is inclusive (dedupe). A large share of startup-domain mentions come from HN mirror bots.
5. Reddit JSON is fully blocked from servers. RSS is limited to 1 request/minute, has no scores, and an over-limit call can hang instead of failing fast (set a timeout).
6. YouTube RSS gives only 15 entries and no subscriber count; a channel with no public uploads returns 200 with zero entries.
7. Mastodon status search returns an empty 200 (not an error) without auth; easy to misread as "no results".
8. GitHub stargazer and watcher lists returned 401/404/empty on every path tried, including with a logged-in token. Do not design around repo-side stargazer listing. Unauthenticated core budget is 60/hour per IP and is shared with every other job on the machine.
9. X syndication: the first call returned 429 with `remaining: 0` (budget is per IP and was already spent by something else on this network), and `remaining` dropped 24 -> 10 between two of my calls. Small/new accounts (NoriRobotics) return 200 with **zero entries**, so no follower count from this endpoint for exactly the accounts we care most about; fall back to fxtwitter.
10. fxtwitter handle lookup quirk: `NoriRobotics` -> 404 "User not found" but `norirobotics` -> 200 (reproduced twice). Lower-case handles before calling. fxtwitter/vxtwitter are volunteer-run third-party proxies with no SLA; cache results and expect breakage. vxtwitter values can be a day stale (`fetched_on`).
11. Every Nitter instance tried is down, rate-limited, or behind a Cloudflare challenge; xcancel is suspended.
12. Substack feeds are ~800 KB each because of full post HTML; use `/api/v1/archive` for metadata.
13. No source here provides history for follower counts; start snapshotting now to get deltas later.

## Fixtures

`/Users/noel/antifund/pipeline/fixtures/social/`
- `hn_algolia_search_by_date.json` (raw, 3 hits, with `_highlightResult`)
- `hn_algolia_launch_hn_180d.json` (55 Launch HN hits, highlight stripped, `story_text` truncated)
- `bluesky_searchPosts.json` (12 posts), `bluesky_getProfile.json`
- `reddit_r_robotics_new.rss.xml` (12 entries)
- `youtube_channel_feed_FigureAI.xml` (15 entries)
- `x_syndication_timeline_profile.json` (`__NEXT_DATA__`, 5 entries), `x_fxtwitter_user.json`
- `github_user_starred.json` (10 stars with `starred_at`, repo fields slimmed)
- `substack_archive.json` (3 posts, bodies truncated)
- `mastodon_tag_timeline.json` (tag history + 5 statuses)
- `lobsters_tag_hardware.json` (10 stories)
