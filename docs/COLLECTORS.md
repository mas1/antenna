# Writing a collector

A collector watches one public source and turns what it sees into `Signal`
objects. It is one file in `pipeline/antenna/collectors/`. Everything after
that (storage, entity resolution, scoring, export) is shared and already
written. Read these first, they are short:

- `pipeline/antenna/models.py` — `Signal`, `EntityHint`, `Person`
- `pipeline/antenna/collectors/base.py` — `Context` and helpers
- `pipeline/antenna/http.py` — the only way to make requests
- `pipeline/antenna/thesis.py` — `classify(text)` for thesis fit
- `pipeline/antenna/score.py` — how your numbers get used

## The module

```python
"""One line: what this watches and why it is early."""

from __future__ import annotations

from typing import Iterable

from .. import http
from ..models import EntityHint, Person, Signal
from ..thesis import classify
from .base import Context, clean_domain, iso, parse_date, squash

SLUG = "hn_launch"            # unique, snake_case, equals the file name
FAMILY = "launch"             # one of models.FAMILIES
STAGE = "discover"            # "discover" finds entities; "enrich" needs ctx.known
DESCRIPTION = "Show HN and Launch HN posts on thesis, with points and comments"


def collect(ctx: Context) -> Iterable[Signal]:
    ...
    yield Signal(
        source=SLUG, family=FAMILY, kind="launch_hn",
        entity=EntityHint(name="Nori Robotics", domain="norirobotics.com",
                          one_liner="Low-cost bimanual mobile robot for developers"),
        title="Launch HN reached 201 points and 66 comments",
        occurred_at="2026-09-01",
        url="https://news.ycombinator.com/item?id=...",
        value=201, unit="points", strength=0.7,
        metrics={"hn_points": 201, "hn_comments": 66},
        text="<the post text, used for thesis classification>",
    )
```

Run it without touching the database:

```bash
cd pipeline && python3 -m antenna probe hn_launch --limit 25 --show 10
```

Enrichment collectors get a list of known entities. To probe one:

```bash
python3 -m antenna probe ats_jobs --known fixtures/known_sample.json --limit 25
```

## Rules

1. **Never invent anything.** Every field comes from a response you received.
   If a source does not give a domain, leave `domain=None`. No placeholder
   names, no guessed founders, no estimated numbers.
2. **Every signal links to evidence a human can open.** Prefer the public
   page (the filing, the repo, the post) over the API URL that returned it.
3. **`occurred_at` is when it happened in the world**, not when we fetched it.
   Skip anything older than `ctx.since`, unless the source is slow by nature
   (embargoed awards, annual programmes): then use a longer window, name it
   in a module constant and say why in the docstring.
4. **All requests go through `antenna.http`** (`get`, `get_json`, `post_json`,
   `request`, `download`). It caches on disk, paces per host and retries.
   Pick a `ttl` that suits the source: 6 hours default, 24 hours or more for
   documents that never change (a filed form, a granted licence).
5. **Survive partial failure.** Wrap per-item work in try/except, call
   `ctx.warn(...)`, continue. One bad row must not lose the run.
6. **Respect `ctx.limit`.** When set, stop after roughly that many entities so
   a probe takes seconds. A full run should finish in under ten minutes with
   a warm cache; say so in your report if it cannot.
7. **Pre-filter to the thesis when the source gives you text.** Use
   `classify(text)["fit"] >= 0.3`. When a source has no description at all
   (Form D), emit anyway: the join with other sources supplies the fit.
8. **Do not edit shared files** (`models.py`, `http.py`, `db.py`, `config.py`,
   `base.py`, `resolve.py`, `score.py`, `export.py`, `cli.py`, `thesis.py`).
   If you need something from them, put a helper in your own module and
   report the request.
9. Standard library only. No pip installs.

## Titles

One plain sentence a partner can read cold. Specific, with the number in it.
Sentence case, no trailing period, no source name prefix, under 110
characters.

- "Filed Form D: $1.8M sold of $5.1M from 5 investors"
- "Stars up 144 in 7 days against 8 the week before"
- "New FCC experimental licence application for UAV radio testing in Mojave"
- "Opened 11 roles in 14 days including Head of Manufacturing"

## Entity hints

Fill what the source gives. `domain` is the strongest key, then `github`
(login only, never a URL), then name. Use `clean_domain()` on anything
URL-shaped: it drops github.com, linkedin.com and other non-company hosts.
`kind` is "company" unless it is clearly a `project` (a repo with no company
behind it yet) or a `person` (a researcher with no company yet).

Put founders, executives, authors and filers in `people=[Person(...)]` with
whatever the source states: `role`, `affiliations` (employer and university
names, as written), `links`, and `facts` (`h_index`, `cited_by_count`, `followers`, and `bio` for
a free-text biography). The scorer matches affiliations against a pedigree
table; inside a sentence-length bio it needs an employment cue ("previously
at SpaceX") before it counts a name.

## Strength

`strength` is your 0..1 read of how much this one event should move a
partner, before decay. Calibrate against this scale and use the full range:

| strength | meaning |
| --- | --- |
| 0.15 to 0.3 | Routine or noisy. It exists, little more. |
| 0.35 to 0.55 | Solid. A real, dated step by a real company. |
| 0.6 to 0.8 | Notable. Top decile for this source. |
| 0.85 to 1.0 | Rare. A first-ever event, or an extreme outlier. |

Use `squash(x, half)` to turn a count into 0..1 with diminishing returns.
First-ever events (first federal award, first licence, first paper under a
company name) deserve a premium over the nth.

## Reserved metric keys

The scorer reads these from `metrics` to judge how well known a company
already is. Use these exact names when you have the number:

- `stars_total` — GitHub stars on the main repo
- `hn_mentions_total` — lifetime Hacker News stories and comments naming it
- `tranco_rank` — current Tranco rank (lower is bigger)
- `open_roles` — open job postings now
- `team_size` — headcount when a source states it
- `amount_usd` — size of a round or award in dollars

Anything else numeric is welcome under your own names.

A few more keys change how a signal is stored, joined or scored:

- `amount_sold` — money actually raised, preferred over `amount_usd` when both exist
- `uei`, `cik` — government identifiers; signals that share one resolve to one company
- `award_key` — a stable id for an award ("nsf:2612345"); two collectors that
  report the same award are counted once
- `public_at` — ISO date a record became visible, for embargoed sources.
  Strength decays from this date instead of `occurred_at`
- `sbir_prior_awards` — lifetime award count, read as a grant-shop penalty
- `observed_only: true` — a reading with no date of its own (a DNS record).
  Stored as one row per entity that each run refreshes
- `rolling: true` — a rolling count whose date moves forward every run (rank
  movement, papers in the window). Also one refreshed row per entity
- `repo` or `subject` — tells apart several continuous signals of one kind
  on one entity (two repositories, two subdomains)

A strength of 0.0 is valid. It means the signal carries metrics only: it
adds nothing to momentum, but its reserved metrics still tell the scorer how
well known the company is.

## Series

For a measurement that changes over time (stars per week, mentions per week,
roles posted per week), attach `series=[{"t": "2026-09-21", "v": 44, "s": 0.6}, ...]`,
oldest first. `v` is the raw value for charts. `s`, if you supply it, is the
strength the signal would have had at that date, which lets the scorer
rebuild the company's rank history. Omit `s` for point events.

## Tests

Add `pipeline/tests/test_<slug>.py` using `unittest` and the saved fixtures in
`pipeline/fixtures/`. Test your parsing functions offline: no network in
tests. Run with `python3 -m unittest discover -s tests -t .` from `pipeline/`.
