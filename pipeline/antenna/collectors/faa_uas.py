"""FAA drone paperwork: Remote ID declarations of compliance and Part 107 waivers.

A drone cannot be sold for normal use in the US until the FAA accepts its
maker's Remote ID declaration of compliance, and it cannot be flown beyond
visual line of sight, over people or several-per-pilot without a Part 107
waiver, so both lists name a company at the moment a product or an operation
becomes legal, usually before any launch post. The collector reads the FAA's
public declaration list (JSON) and its table of waivers issued (HTML) and
emits one signal per manufacturer and one per waiver.

What the two feeds can and cannot say:

* Declarations. The public list holds the declarations the FAA currently
  shows as accepted, so "first" means first on that list under this maker's
  name, and titles say so: the list cannot show a declaration that was
  withdrawn, or one filed under another name. The list's date is "last
  updated"; for a declaration that has never been revised (revision 0) that
  is the day it was accepted. Revised declarations are reported as
  amendments unless they were filed inside the window. Timestamps are UTC
  in the API and are turned into the US Eastern day, which is the day the
  FAA's own pages show to a reader in the US. The page for one declaration
  shows its status and filing date; the "updated" date is on the list page.
* Waivers. The table gives a company name, a responsible person and
  regulation numbers, nothing else. Police, fire, government, universities,
  hospitals, utilities, film crews, drone shows and individuals are dropped
  by a stoplist; what is left still includes some companies that merely use
  drones, and those are held to a low strength unless the name itself says
  aviation or technology, or the company is also a manufacturer on the
  declaration list (same name, or the same name with one descriptive word
  such as "Robotics" added or left off). A waiver on the list may replace an earlier one
  that the FAA then removes, so this collector never calls a waiver a
  company's first: it reports how many other waivers the list shows.
  The company name is the table's, and the table has typos the waiver PDF
  does not (it lists "Physics A" for a waiver issued to "Physics AI"), so a
  name that ends in a lone letter is dropped rather than passed on.
  The table's paging drops rows at page boundaries (rows sharing a date are
  served in an unstable order), so the window is read a second time through
  the per-regulation search and the two readings are merged.
"""

from __future__ import annotations

import html
import math
import re
import time
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Iterator
from urllib.parse import quote, urljoin

from .. import http
from ..models import EntityHint, Person, Signal
from .base import Context, iso, loose_name, normalize_name, parse_date, squash

SLUG = "faa_uas"
FAMILY = "regulatory"
STAGE = "discover"
DESCRIPTION = "FAA Remote ID declarations of compliance by drone makers and Part 107 waivers for advanced operations"

KIND_DOC = "faa_remote_id_doc"
KIND_WAIVER = "faa_part107_waiver"
TITLE_MAX = 109  # titles stay under 110 characters

# --------------------------------------------------------------------------
# Feed 1: UAS Declaration of Compliance (Remote ID), public JSON API
# --------------------------------------------------------------------------

DOC_API = "https://uasdoc.faa.gov/api/v1/publicDOCRev"
DOC_PAGE = "https://uasdoc.faa.gov/listDocs/{}"  # the public page for one declaration
DOC_PAGE_SIZE = 200  # about 500 rows exist; the API accepts large pages
DOC_LIST_TTL = 6 * 3600
# Only the filing date and the means of compliance are read from the detail
# record, and neither changes once a declaration exists.
DOC_DETAIL_TTL = 7 * 24 * 3600

try:
    from zoneinfo import ZoneInfo

    _FAA_TZ = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 - no time zone database on this machine: stay on UTC
    _FAA_TZ = None

# Makers this large are not companies to source, whatever they file.
_BIG_MAKERS = ("dji", "autel", "sony", "nokia", "parrot", "yuneec", "xag")

# A first word this generic does not tie two manufacturers together.
_GENERIC_FIRST = {
    "drone", "drones", "uas", "uav", "aero", "aerial", "air", "sky", "global", "the",
    "broadcast", "black", "blue", "red", "light", "new", "american", "us", "general",
}


def faa_day(stamp: Any) -> date | None:
    """The US Eastern calendar day of an API timestamp.

    The API stores UTC and the FAA's pages show each date in the reader's
    own time zone, so an evening update in Washington carries the next day's
    date in UTC. A value with no time of day is taken as written.
    """
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    text = stamp.strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return parse_date(text[:10])
    if moment.tzinfo is None or _FAA_TZ is None:
        return moment.date()
    return moment.astimezone(_FAA_TZ).date()


def _words(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, (str, int, float)) else ""


def _doc_items(payload: Any) -> list[dict]:
    """The item dicts of a list or detail response, whatever else is in it."""
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("items") if isinstance(data, dict) else None
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def parse_doc_list(payload: dict) -> tuple[list[dict], int]:
    """Rows and the reported total from one page of the declaration list."""
    rows = []
    for it in _doc_items(payload):
        tracking = _words(it.get("trackingNumber"))
        make = _words(it.get("makeName"))
        updated = faa_day(it.get("updatedAt"))
        if not tracking or not make or updated is None:
            continue
        try:
            rev = int(_words(it.get("revNumber")) or 0)
        except ValueError:
            rev = 0
        rows.append({
            "tracking": tracking,
            "make": make,
            "model": _words(it.get("modelName")),
            "series": _words(it.get("series")),
            "status": _words(it.get("status")).lower(),
            "doc_type": _words(it.get("docType")).lower(),
            "rev": rev,
            "updated": updated,
            "declared_for": _words(it.get("categoryDeclarationFor")),
        })
    data = payload.get("data") if isinstance(payload, dict) else None
    try:
        total = int((data or {}).get("totalItems") or 0) if isinstance(data, dict) else 0
    except (TypeError, ValueError):
        total = 0
    return rows, total


def parse_doc_detail(payload: dict) -> dict:
    """The few fields of a single declaration that the list does not carry."""
    items = _doc_items(payload)
    if not items:
        return {}
    it = items[0]
    rev = it.get("publicRevision")
    listed = rev.get("complianceMeans") if isinstance(rev, dict) else None
    means = [_words(m.get("name")) for m in listed if isinstance(m, dict)] if isinstance(listed, list) else []
    return {
        "created": faa_day(it.get("createdAt")),
        "means": [m for m in means if m],
    }


def make_key(make: str) -> str:
    return normalize_name(make) or make.strip().lower()


def family_key(make: str) -> str:
    """A looser key used only to refuse a "first declaration" claim.

    "Draganfly" and "Draganfly Innovations" are one company filing under two
    spellings. Tying names by their first word can wrongly join two
    companies, which only ever removes a "first" claim, never adds one.
    """
    key = make_key(make)
    first = key.split()[0] if key.split() else key
    return key if first in _GENERIC_FIRST else first


def is_big_maker(make: str) -> bool:
    """DJI, Autel and the like: their filings are not news about an early company."""
    key = make_key(make)
    return any(re.match(rf"{re.escape(big)}(?![a-z0-9])", key) for big in _BIG_MAKERS)


def bare_maker_keys(makers: Iterable[str]) -> set[str]:
    """Maker keys with one trailing descriptive word dropped, for is_listed_maker."""
    return {loose_name(m) for m in makers} - _GENERIC_FIRST


def is_listed_maker(company: str, makers: Iterable[str], bare: Iterable[str] = ()) -> bool:
    """Is this waiver holder a manufacturer on the Remote ID declaration list?

    `makers` holds make_key() of every listed manufacturer and `bare` is
    bare_maker_keys(makers). The two FAA lists write one company two ways:
    the declaration list has "Percepto" and "Rainmaker" where the waiver
    table has "Percepto Robotics Inc." and "Rainmaker Technology
    Corporation". So a name also matches when it is the listed name plus one
    descriptive word, or the listed name less one. Two names that differ in
    that word ("Genesis AI", "Genesis Robotics") do not match.
    """
    key = make_key(company)
    if key in makers:
        return True
    short = loose_name(company)
    if short != key and short not in _GENERIC_FIRST and short in makers:
        return True
    return key in bare


def split_make(make: str) -> tuple[str, list[str]]:
    """'Amor Fati Industries (dba Seneca)' -> ('Amor Fati Industries', ['Seneca'])."""
    m = re.match(r"^(.*?)\s*\(\s*d/?b/?a\.?\s+(.+?)\s*\)\s*$", make, re.I)
    if m and m.group(1).strip():
        return m.group(1).strip(), [m.group(2).strip()]
    return make.strip(), []


def summarize_makers(rows: list[dict], since: date, today: date,
                     created: dict[str, date | None]) -> list[dict]:
    """One summary per manufacturer with a Remote ID declaration in the window.

    `created` maps tracking number to the day the declaration was first
    filed (from the detail endpoint), or None when that is unknown.

    A declaration counts as new when it sits at revision 0 (the list date is
    then its acceptance) or when it was filed inside the window. Anything
    else updated in the window is an amendment to an older declaration.
    """
    by_make: dict[str, list[dict]] = {}
    by_family: dict[str, list[dict]] = {}
    for r in rows:
        by_make.setdefault(make_key(r["make"]), []).append(r)
        by_family.setdefault(family_key(r["make"]), []).append(r)

    def in_window(r: dict) -> bool:
        return (r["doc_type"] == "rid" and r["status"] == "accepted"
                and since <= r["updated"] <= today)

    def is_new(r: dict) -> bool:
        if not in_window(r):
            return False
        filed = created.get(r["tracking"])
        return r["rev"] == 0 or (filed is not None and filed >= since)

    out = []
    for key, mine in by_make.items():
        if is_big_maker(mine[0]["make"]):
            continue
        window = sorted((r for r in mine if in_window(r)), key=lambda r: r["updated"], reverse=True)
        if not window:
            continue
        new = [r for r in window if is_new(r)]
        amended = [r for r in window if not is_new(r)]
        family = by_family[family_key(mine[0]["make"])]
        first = bool(new) and all(is_new(r) for r in family)
        spellings: list[str] = []
        for r in sorted(mine, key=lambda r: r["updated"], reverse=True):
            if r["make"] not in spellings:
                spellings.append(r["make"])
        out.append({
            "key": key,
            "spellings": spellings,
            "new": new,
            "amended": amended,
            "first": first,
            "rid_on_list": sum(1 for r in mine if r["doc_type"] == "rid" and r["status"] == "accepted"),
            "oop_on_list": sum(1 for r in mine if r["doc_type"] == "oop" and r["status"] == "accepted"),
            "latest": (new or amended)[0]["updated"],
        })
    out.sort(key=lambda s: s["latest"], reverse=True)
    return out


def _thing(row: dict) -> str:
    return "broadcast module" if "broadcast" in row["declared_for"].lower() else "aircraft model"


def _model_names(rows: list[dict]) -> list[str]:
    names: list[str] = []
    for r in rows:
        name = r["model"] or r["tracking"]
        if name not in names:
            names.append(name)
    return names


def _fit_list(stem: str, names: list[str], tail: str = "", limit: int = TITLE_MAX) -> str:
    """Stem plus as many names as fit under the title limit, then 'and N more'."""
    for keep in range(len(names), 0, -1):
        shown = names[:keep]
        rest = len(names) - keep
        if rest:
            body = ", ".join(shown) + f" and {rest} more"
        elif len(shown) > 1:
            body = ", ".join(shown[:-1]) + " and " + shown[-1]
        else:
            body = shown[0]
        title = f"{stem}{body}{tail}"
        if len(title) <= limit:
            return title
    return f"{stem}{names[0][: max(10, limit - len(stem) - len(tail))]}{tail}"


def _since(rows: list[dict]) -> str:
    """' since Jun 2026' when the rows were accepted on different days.

    The signal is dated by the latest of them, so a count that covers several
    days has to say where it starts.
    """
    days = {r["updated"] for r in rows}
    return f" since {min(days).strftime('%b %Y')}" if len(days) > 1 else ""


def doc_title(s: dict) -> str:
    new, amended = s["new"], s["amended"]
    if new:
        names = _model_names(new)
        n = len(new)
        if s["first"]:
            # "First" is a statement about the FAA's public list, which is
            # all that was read: it holds no withdrawn declarations and none
            # the same company may have filed under another name.
            if n == 1:
                return _fit_list(f"FAA accepted a Remote ID declaration for {_thing(new[0])} ", names,
                                 ", its first on the FAA list")
            return _fit_list(f"FAA accepted {n} Remote ID declarations{_since(new)}, its first on the FAA list: ",
                             names)
        # "New" describes the declaration. The list cannot say whether the
        # aircraft itself is new: makers re-declare existing models.
        tail = f" ({s['rid_on_list']} on the FAA list)" if s.get("complete", True) else ""
        if n == 1:
            return _fit_list(f"FAA accepted a new Remote ID declaration, for {_thing(new[0])} ", names, tail)
        return _fit_list(f"FAA accepted {n} new Remote ID declarations{_since(new)}: ", names, tail)
    names = _model_names(amended)
    n = len(amended)
    if n == 1:
        r = amended[0]
        return _fit_list(f"FAA accepted revision {r['rev']} of the Remote ID declaration for {_thing(r)} ", names)
    return _fit_list(f"FAA accepted amendments to {n} existing Remote ID declarations{_since(amended)}: ", names)


def doc_strength(s: dict) -> float:
    """First accepted declaration is the rare event; an amendment is routine."""
    new, amended = s["new"], s["amended"]
    if new:
        aircraft = any(_thing(r) == "aircraft model" for r in new)
        extra = squash(len(new) - 1, 2.0)
        if s["first"]:
            base = 0.86 if aircraft else 0.68  # a bolt-on module is a smaller step than an airframe
            return round(min(0.96, base + 0.1 * extra), 2)
        base = 0.45 if aircraft else 0.36
        return round(min(0.6, base + 0.15 * extra), 2)
    return round(0.15 + 0.1 * squash(len(amended), 3.0), 2)


def _filing_words(rows: list[dict]) -> str:
    """What the filing is, in plain words, for the evidence text.

    The list gives a make, a model and a category, no description, so this
    sentence is all a reader (or the thesis classifier) has to tell that the
    filing is about a drone. It describes the filing, not the company.
    """
    n = len(rows)
    modules = sum(1 for r in rows if _thing(r) == "broadcast module")
    if n == 1:
        stem = "FAA Remote ID declaration of compliance for "
        if modules:
            return stem + "a Remote ID broadcast module, a device that adds Remote ID to an unmanned aircraft (drone)"
        return stem + "an unmanned aircraft (drone) model"
    stem = f"{n} FAA Remote ID declarations of compliance, "
    if modules == n:
        return stem + "each for a Remote ID broadcast module, a device that adds Remote ID to an unmanned aircraft (drone)"
    if modules:
        return stem + "for unmanned aircraft (drone) models and Remote ID broadcast modules"
    return stem + "each for an unmanned aircraft (drone) model"


def doc_signal(s: dict, created: dict[str, date | None], means: dict[str, list[str]]) -> Signal:
    rows = s["new"] or s["amended"]
    head = rows[0]
    name, aliases = split_make(s["spellings"][0])
    for other in s["spellings"]:
        base, extra = split_make(other)
        for a in [base, *extra]:
            if a != name and a not in aliases:
                aliases.append(a)

    lines = []
    for r in s["new"] + s["amended"]:
        filed = created.get(r["tracking"])
        bits = [
            f"{r['model'] or 'unnamed model'}" + (f" series {r['series']}" if r["series"] else ""),
            f"declaration {r['tracking']} revision {r['rev']}",
            f"declared for {r['declared_for'] or 'unmanned aircraft'}",
            f"status accepted, last updated {iso(r['updated'])}",
        ]
        if filed:
            bits.append(f"filed {iso(filed)}")
        if means.get(r["tracking"]):
            bits.append("means of compliance " + ", ".join(means[r["tracking"]]))
        lines.append("; ".join(bits))
    text = (
        f"{_filing_words(s['new'] + s['amended'])}. "
        "Listed in the FAA UAS Declaration of Compliance system (14 CFR Part 89). "
        f"Manufacturer as declared: {s['spellings'][0]}. " + " | ".join(lines)
    )

    metrics: dict[str, Any] = {
        "new_declarations": len(s["new"]),
        "amended_declarations": len(s["amended"]),
        "first_declaration": 1 if s["first"] else 0,
    }
    if s.get("complete", True):  # counts over a partly read list would be too low
        metrics["remote_id_declarations_on_list"] = s["rid_on_list"]
        if s["oop_on_list"]:
            metrics["over_people_declarations_on_list"] = s["oop_on_list"]
    if s["new"]:
        filed = created.get(head["tracking"])
        if head["rev"] == 0 and filed:
            metrics["days_filing_to_acceptance"] = (head["updated"] - filed).days
    else:
        metrics["latest_revision"] = head["rev"]

    products = _model_names(rows)[:3]
    if all(_thing(r) != "aircraft model" for r in rows):
        what = "Remote ID broadcast module" + ("s" if len(products) > 1 else "")
    else:
        what = "unmanned aircraft"
    one_liner = f"Maker of the {', '.join(products)} {what}"

    return Signal(
        source=SLUG, family=FAMILY, kind=KIND_DOC,
        entity=EntityHint(name=name, aliases=aliases, one_liner=one_liner),
        title=doc_title(s),
        occurred_at=iso(head["updated"]),
        url=DOC_PAGE.format(head["tracking"]),
        value=float(len(rows)), unit="declarations",
        strength=doc_strength(s),
        metrics=metrics,
        text=text,
    )


def _collect_docs(ctx: Context, quota: int | None, makers: set[str] | None = None) -> Iterator[Signal]:
    """Declaration signals. `makers`, when given, is filled with the name key
    of every manufacturer holding an accepted Remote ID declaration."""
    by_tracking: dict[str, dict] = {}
    served: set[str] = set()  # tracking numbers the API returned, parsed or not
    total = 0
    for page in range(20):
        try:
            payload = http.get_json(
                DOC_API,
                params={"itemsPerPage": DOC_PAGE_SIZE, "pageIndex": page,
                        "orderBy[0][0]": "updatedAt", "orderBy[0][1]": "DESC"},
                ttl=DOC_LIST_TTL,
            )
        except Exception as e:  # noqa: BLE001 - the other feed should still run
            ctx.warn(f"faa_uas: declaration list page {page} failed: {e}")
            break
        err = payload.get("error") if isinstance(payload, dict) else "not a JSON object"
        if isinstance(err, dict):
            err = err.get("message")
        if err:
            ctx.warn(f"faa_uas: declaration list page {page} returned error: {err}")
            break
        page_rows, page_total = parse_doc_list(payload)
        total = max(total, page_total)
        items = _doc_items(payload)
        for r in page_rows:
            by_tracking.setdefault(r["tracking"], r)
        served.update(t for t in (_words(it.get("trackingNumber")) for it in items) if t)
        if not items or len(served) >= total:
            break
    rows = list(by_tracking.values())
    if not rows:
        return
    # Distinct tracking numbers are counted, not rows: if the list shifts
    # between two page requests one row repeats and another is never served.
    whole_list = total > 0 and len(served) >= total
    if not whole_list:
        ctx.warn(f"faa_uas: read {len(served)} of {total} declarations; no 'first declaration' claims this run")
    if makers is not None:
        makers.update(make_key(r["make"]) for r in rows if r["doc_type"] == "rid" and r["status"] == "accepted")

    # Which declarations fall in the window is known from the list alone; the
    # detail hop (one request each) is only made for those.
    prelim = summarize_makers(rows, ctx.since, ctx.today, {})
    if quota is not None:
        prelim = prelim[:quota]
    created: dict[str, date | None] = {}
    means: dict[str, list[str]] = {}
    for s in prelim:
        for r in s["new"] + s["amended"]:
            try:
                d = parse_doc_detail(http.get_json(f"{DOC_API}/{r['tracking']}", ttl=DOC_DETAIL_TTL))
            except Exception as e:  # noqa: BLE001
                ctx.warn(f"faa_uas: declaration {r['tracking']} detail failed: {e}")
                continue
            created[r["tracking"]] = d.get("created")
            means[r["tracking"]] = d.get("means") or []

    keep = {s["key"] for s in prelim}
    emitted = 0
    for s in summarize_makers(rows, ctx.since, ctx.today, created):
        if s["key"] not in keep:
            continue
        s["complete"] = whole_list
        if not whole_list:
            s["first"] = False
        try:
            yield doc_signal(s, created, means)
            emitted += 1
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"faa_uas: declaration signal for {s['spellings'][0]!r} failed: {e}")
    ctx.log(f"faa_uas: {len(rows)} declarations on the FAA list, {emitted} manufacturers updated since {ctx.since}")


# --------------------------------------------------------------------------
# Feed 2: Part 107 waivers issued, HTML table
# --------------------------------------------------------------------------

WAIVER_URL = "https://www.faa.gov/uas/commercial_operators/part_107_waivers/waivers_issued"
# www.faa.gov answers 403 to this project's contact User-Agent and 200 to a
# plain curl one (measured; see docs/sources/regulatory.md). A tool-style
# agent is sent as-is. No browser string is used.
WAIVER_UA = "curl/8.7.1"
# The table's keyword box. It matches company, person, waiver number and the
# regulation column, so "107.31" lists every BVLOS waiver.
WAIVER_SEARCH_PARAM = "saa_field_media_file"
WAIVER_INTERVAL = 1.0  # seconds between real requests to www.faa.gov
WAIVER_TTL = 6 * 3600
WAIVER_MAX_PAGES = 200
# Pages cached at different times do not line up (new rows push old ones
# down), so if the pages of one walk were fetched further apart than this
# the stale ones are fetched again.
SNAPSHOT_SPREAD = 3600
# The whole table (about 90 pages) is only walked on a full run or a large
# probe. It is what tells how many other waivers a company holds.
HISTORY_MIN_LIMIT = 100

# Official section headings of 14 CFR Part 107, for the evidence text.
SECTION_TITLES = {
    "107.25": "operation from a moving vehicle or aircraft",
    "107.29": "operation at night",
    "107.31": "visual line of sight aircraft operation",
    "107.33": "visual observer",
    "107.35": "operation of multiple small unmanned aircraft",
    "107.37": "operation near aircraft; right-of-way rules",
    "107.39": "operation over human beings",
    "107.41": "operation in certain airspace",
    "107.51": "operating limitations for small unmanned aircraft",
    "107.145": "operations over moving vehicles",
}
QUALIFYING = ("107.31", "107.35", "107.39")

_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r'<td\b[^>]*class="[^"]*views-field-field-([a-z-]+)[^"]*"[^>]*>(.*?)</td>', re.S | re.I)
_TIME = re.compile(r'<time\b[^>]*datetime="(\d{4}-\d{2}-\d{2})', re.I)
_LINK = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_WAIVER_NO = re.compile(r"(107W-\d{4}-\d{3,6})", re.I)


def _plain(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def parse_regulations(text: str) -> list[str]:
    """'107.31, 107..33, 107.51(b0' -> ['107.31', '107.33', '107.51'] (typos tolerated)."""
    out: list[str] = []
    for num in re.findall(r"107\s*\.+\s*(\d{2,3})", text or ""):
        sec = f"107.{num}"
        if sec not in out:
            out.append(sec)
    return out


def parse_waiver_page(page: str) -> list[dict]:
    """Rows of the 'waivers issued' table, as written, with dates parsed."""
    rows = []
    for tr in _ROW.findall(page or ""):
        cells = {k.lower(): v for k, v in _CELL.findall(tr)}
        if "company-name" not in cells or "issue-date" not in cells:
            continue
        issued = _TIME.search(cells["issue-date"])
        issued_d = parse_date(issued.group(1)) if issued else parse_date(_plain(cells["issue-date"]))
        if issued_d is None:
            continue
        exp = _TIME.search(cells.get("expiration-date", ""))
        expires_d = parse_date(exp.group(1)) if exp else parse_date(_plain(cells.get("expiration-date", "")))
        person_cell = cells.get("responsible-person", "")
        link = _LINK.search(person_cell)
        if link:
            person = _plain(link.group(2))
            href = html.unescape(link.group(1)).strip()
            pdf = urljoin("https://www.faa.gov/", quote(href, safe="/:%?=&#+,;@()~!$'*-._"))
        else:
            person = re.sub(r"\s*\(pdf\)\s*$", "", _plain(person_cell), flags=re.I)
            pdf = None
        company = _plain(cells["company-name"])
        # A known glitch in the table: the person's name glued straight onto
        # the company ("Amazon.com Services LLCWilson Ragle"). A name that
        # merely ends with the person's ("Law Office of Ann Lee") is left alone.
        if person and len(company) > len(person) and company.lower().endswith(person.lower()):
            head = company[: -len(person)]
            if head[-1].isalnum() or head[-1] == ".":
                company = head
        company = company.strip(" ,;")  # a stray comma after 'Hoverfly Technologies Inc,' would keep 'Inc' in the name key
        raw_regs = _plain(cells.get("waivered-regulation", ""))
        number = _WAIVER_NO.search(pdf or "")
        rows.append({
            "issued": issued_d,
            "expires": expires_d,
            "company": company,
            "person": person,
            "pdf": pdf,
            "number": number.group(1).upper() if number else None,
            "regs_raw": raw_regs,
            "regs": parse_regulations(raw_regs),
        })
    return rows


def dedupe_waivers(rows: list[dict]) -> list[dict]:
    """The table repeats some waivers (same number, re-uploaded PDF)."""
    seen: set[tuple] = set()
    out = []
    for r in rows:
        ident = (r["number"] or r["pdf"] or r["person"].lower(), make_key(r["company"]), r["issued"])
        if ident in seen:
            continue
        seen.add(ident)
        out.append(r)
    return out


# Organisations that hold waivers but are not companies to source.
_STOP = [
    ("public safety or government", re.compile(
        r"\b(police|sheriff'?s?|fire|rescue|ems|emergency|public safety|law enforcement|enforcement|"
        r"marshal|constable|corrections|homeland security|department|dept|county|city of|town of|"
        r"township|village of|borough|parish|state of|commonwealth|municipal|district|authority|"
        r"commission|division of|bureau|agency|office of|tribe|tribal|nation of|parks|transit|"
        r"transportation|national guard|air force|army|navy|coast guard|federal|government|"
        r"task force|highway patrol|nation|squad|judicial|crimes|canal|public service|response team|"
        r"noaa|nasa|usda|usgs|faa|caltrans|mta)\b", re.I)),
    ("public safety or government", re.compile(r"[A-Za-z]DOT\b|\b[A-Z]*PD\b|\bPSD\b")),  # NCDOT, Clinton PD, RFPD
    ("education or research institution", re.compile(
        r"\b(university|college|schools?|academy|institute|regents|suny|polytechnic|campus|"
        r"laborator(?:y|ies)|applied physics lab|mitre|foundation|conservancy|conservation|"
        r"research|cent(?:er|re)|alliance|partnership|program|team|training)\b", re.I)),
    ("hospital or health system", re.compile(r"\b(hospital|health|healthcare|clinic|medical|medicine)\b", re.I)),
    ("utility, resource or property operator", re.compile(
        r"\b(electric|power|energy|utility|utilities|gas|water|cooperative|co-op|pipeline|petroleum|"
        r"oil|mining|mines|aluminum|steel|railroad|railway|construction|builders|contractors|casino|"
        r"resort|ranch|farms?|bank|mortgage|financial|parking|zoo|wildlife|insurance|risk management|"
        r"management company|entertainment|realty|real estate|properties|homes|edison|renewables|"
        r"roofing|contracting|automotive|hauling|detailing|restoration|surveying)\b", re.I)),
    # Firms that fly drones as part of another trade.
    ("engineering, inspection, consulting or security firm", re.compile(
        r"\b(inspections?|consultants?|consulting|engineers|engineering|professional services|"
        r"land services|fieldworks|environmental|agronomics|resource group|building group|"
        r"investigations|patrol)\b", re.I)),
    ("film, photo or drone show", re.compile(
        r"\b(media|photo|photos|photography|pix|pictures|studios?|visuals|films?|productions|video|"
        r"cinema|cinematography|cinematics|documentaries|news|gazette|newspaper|tribune|broadcasting|"
        r"television|images?|imaging|filming|creative|shows?|pyrotechnics|fireworks|events|magic)\b", re.I)),
]

# Large corporations that fly drones as a tool. Matched on the start of the
# normalized name. A hand list: it will always be incomplete.
_BIG_USERS = (
    "walmart", "amazon", "chevron", "koch", "td bank", "marvell", "novelis", "rio tinto",
    "freeport", "live nation", "lockheed", "boeing", "aurora flight sciences", "leidos", "aecom",
    "pcl", "quanta", "garney", "southern company", "santee cooper", "northrop", "raytheon", "rtx",
    "general atomics", "l3harris", "bae", "textron", "honeywell", "google", "wing aviation", "ups",
    "united parcel", "fedex", "at&t", "att", "verizon", "bnsf", "union pacific", "shell", "exxon",
    "bp", "dominion", "duke", "state farm", "allstate", "johns hopkins", "disney", "comcast",
    "archer daniels", "hf sinclair", "valmont", "caterpillar", "cat financial", "cdm smith",
    "mortenson", "rwe", "axon", "acuren", "energix", "tesla", "blue origin", "weyerhaeuser", "parsons",
    "gatx", "oneok", "enel", "nisource", "duquesne", "nutrien", "michels", "woolpert", "csx",
    "general dynamics", "dynetics", "dji", "adt", "lhoist", "chs", "davey", "clayco", "brasfield",
    "be&k", "aerovironment", "entergy", "tva", "bge", "mlgw", "mitsubishi",
)
# Smaller firms known to fly drones for another trade, whose names give no
# hint of it: Merjent is an environmental consultancy, AUI Partners a builder.
_KNOWN_USERS = ("merjent", "aui partners")
# The same, for names usually written as one word with something attached.
_BIG_PREFIXES = ("exxon", "lyondell", "firstenergy", "comed", "ameren", "basf", "bechtel")

# Given names common enough that "<given name> <surname>" in the company
# column is far more likely a person than a company.
_GIVEN_NAMES = frozenset("""
    aaron adam alan albert alex alexander andrew andy anna anthony antonio austin barry ben benjamin
    bill bob brad bradley brandon brent brian bruce bryan carl carlos chad charles chris christian
    christopher cody craig dale dan daniel danny darren dave david dean dennis derek devon don donald
    doug douglas dustin dylan edward eric erik ethan frank gary george gerald glenn greg gregory
    harold henry jack jacob jake james jared jason jay jeff jeffrey jeremy jerry jesse jim jimmy joe
    joel john johnny jon jonathan jordan jose joseph josh joshua juan justin karen keith kelly ken
    kenneth kevin kyle larry lawrence lee linda lisa logan luis luke marc mark martin mary matt
    matthew michael mike nathan nicholas nick nicky pat patrick paul peter phil philip phillip ralph
    randy ray raymond richard rick rob robert rodney roger ron ronald roy russell ryan sam samuel
    sarah scott sean shane shawn stephen steve steven susan ted terry thomas tim timothy todd tom
    tommy tony travis trevor troy tyler victor vincent walter wayne william zach zachary
""".split())

_ORG_WORD = re.compile(
    r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|co|company|ltd|limited|lp|llp|pllc|pbc|"
    r"group|partners|services|solutions|enterprises|associates|holdings)\b", re.I)
_NAME_EXTRAS = {"jr", "sr", "ii", "iii", "iv"}

# Words in a company name that say what its own business is. An aviation
# word is strong evidence of a drone company; a generic technology word is
# weaker; no such word at all means the holder may only be a drone user.
_CUE_AVIATION = re.compile(
    r"aero|aerial|avia|drone|\buas\b|uav|unmanned|uncrewed|robot|autonom|flight|fly|flite|sky|"
    r"\bair|copter|rotor|hover|\bwing|defen[cs]e", re.I)
_CUE_TECH = re.compile(
    r"dynamics|technolog|\btech\b|\bsystems?\b|\blabs?\b|industries|intelligence|sensing|"
    r"security|delivery|automation|radar", re.I)
_CUE_AI = re.compile(r"(?:\b|[a-z])AI\b")
# A name built on these words is usually a pilot-for-hire business. It is a
# real drone operator, but a waiver says less about it than about a company
# with a product, so it stays in the routine band.
_SERVICE = re.compile(r"\b(services?|solutions|aerials|on demand)\b", re.I)
# "Physics A" in the table is "Physics AI" on the waiver itself.
_CUT_OFF = re.compile(r"\s[A-Za-z]$")


def _name_tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z']+", s.lower()) if t not in _NAME_EXTRAS and len(t) > 1}


def looks_individual(company: str, person: str) -> bool:
    """True when the 'company' cell is a person's name, or empty."""
    c = _name_tokens(company)
    if not c:
        return True
    if _ORG_WORD.search(company):
        return False
    p = _name_tokens(person)
    if p and (c <= p or p <= c):
        return True
    if name_cue(company):
        return False
    words = [re.sub(r"[^a-z]", "", w.lower()) for w in company.split()]
    words = [w for w in words if w and w not in _NAME_EXTRAS]
    surname = re.sub(r"[^a-z]", "", person.lower().split()[-1]) if person.split() else ""
    if surname in _NAME_EXTRAS and len(person.split()) > 1:
        surname = re.sub(r"[^a-z]", "", person.lower().split()[-2])
    given = re.sub(r"[^a-z]", "", person.lower().split()[0]) if person.split() else ""
    if not words:
        return True
    # A relative's or a mistyped name: same surname as the responsible person.
    if 2 <= len(words) <= 3 and surname and words[-1] == surname:
        return True
    # A login typed into the company box: 'pburke' for Peter Burke.
    if len(words) == 1 and len(surname) >= 3 and words[0].endswith(surname) and words[0] != surname:
        prefix = words[0][: -len(surname)]
        if given and prefix[0] == given[0]:
            return True
    # 'James Michael' filed under a different responsible person.
    if len(words) == 2 and words[0] in _GIVEN_NAMES and company.replace(" ", "").isalpha():
        return True
    return False


def name_cue(company: str) -> int:
    """2 = the name says aviation or robotics, 1 = generic technology, 0 = neither."""
    if _CUE_AVIATION.search(company):
        return 2
    if _CUE_TECH.search(company) or _CUE_AI.search(company):
        return 1
    return 0


def is_service_name(company: str) -> bool:
    return bool(_SERVICE.search(company))


def drop_reason(company: str, person: str) -> str | None:
    """Why this waiver holder is not a company to source, or None to keep it."""
    for reason, pat in _STOP:
        # The two columns are sometimes swapped, so the person cell is checked too.
        if pat.search(company) or (reason == "public safety or government" and pat.search(person)):
            return reason
    if looks_individual(company, person):
        return "individual"
    if _CUT_OFF.search(company.strip()):
        return "name cut off in the FAA table"
    norm = normalize_name(company)
    low = company.lower()
    for big in _BIG_USERS:
        if re.match(rf"{re.escape(big)}(?![a-z0-9])", norm) or re.match(rf"{re.escape(big)}(?![a-z0-9])", low):
            return "large corporate drone user"
    if low.replace(" ", "").startswith(_BIG_PREFIXES):
        return "large corporate drone user"
    if any(re.match(rf"{re.escape(known)}(?![a-z0-9])", norm) for known in _KNOWN_USERS):
        return "drone user in another trade"
    return None


def qualifies(regs: list[str]) -> bool:
    """BVLOS, multiple aircraft per pilot or flight over people. A night plus
    multiple-aircraft waiver without BVLOS is the drone light show template."""
    if not any(q in regs for q in QUALIFYING):
        return False
    if "107.35" in regs and "107.29" in regs and "107.31" not in regs:
        return False
    return True


def _month(d: date) -> str:
    return d.strftime("%b %Y")


def waiver_title(regs: list[str], expires: date | None) -> str:
    tail = f", valid to {_month(expires)}" if expires else ""
    multi = "107.35" in regs
    where = []
    if "107.31" in regs:
        where.append("beyond visual line of sight")
    if "107.39" in regs:
        where.append("over people")
    if "107.145" in regs:
        where.append("over moving vehicles")
    stem = "FAA waiver to fly multiple drones per pilot" if multi else "FAA waiver to fly drones"
    while True:
        if not where:
            body = ""
        elif len(where) == 1:
            body = " " + where[0]
        else:
            body = " " + ", ".join(where[:-1]) + " and " + where[-1]
        title = f"{stem}{body}{tail}"
        if len(title) <= TITLE_MAX or not where:
            return title
        where.pop()  # the least important privilege is last


def waiver_strength(regs: list[str], *, cue: int, term_days: int | None,
                    others_on_list: int | None, service: bool = False, maker: bool = False) -> float:
    """BVLOS and multi-aircraft are the scarce privileges; flight over people
    alone is common. A name with no aviation or technology word is as likely
    a drone user as a drone company, so it stays in the routine band, as does
    a pilot-for-hire name, and a name with only a generic technology word
    cannot reach the notable band."""
    bvlos, multi, people = "107.31" in regs, "107.35" in regs, "107.39" in regs
    if bvlos and multi:
        s = 0.56
    elif bvlos:
        s = 0.45
    elif multi:
        s = 0.4
    else:
        s = 0.27
    if people and (bvlos or multi):
        s += 0.05
    if term_days is not None and term_days < 90:
        s -= 0.1  # a short campaign, not a standing operation
    if others_on_list:
        # The nth waiver of a company that already holds several is routine
        # for it. The reverse is not rewarded: a lone waiver is often a
        # reissue of one the FAA has since taken off the list, so it does
        # not prove a newcomer.
        s -= 0.06 * squash(others_on_list, 3.0)
    if maker:
        s += 0.05  # a manufacturer with its own aircraft on the FAA list, not only an operator
    if cue == 0 or service:
        s = min(s, 0.3)
    elif cue == 1:
        s = min(s, 0.5)
    return round(max(0.15, min(0.8, s)), 2)


def _as_name(raw: str) -> str:
    """'MARCELL HAYWOOD' -> 'Marcell Haywood'. Mixed case is left as written."""
    if not raw.isupper():
        return raw
    words = [w if w.lower() in ("ii", "iii", "iv") else w.title() for w in raw.split()]
    return re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), " ".join(words))


_DBA = re.compile(r"^(.*?)[\s,(/]+d/?b/?a/?\.?\s+(.+?)\)?$", re.I)


def waiver_signal(row: dict, *, on_list: int | None, in_window: int, maker: bool = False) -> Signal:
    """`maker` is True when the holder is also a manufacturer on the FAA's
    Remote ID declaration list, which settles that it is a drone company."""
    regs = row["regs"]
    term = (row["expires"] - row["issued"]).days if row["expires"] else None
    if term is not None and term < 0:  # an expiry before the issue date is a typo in the table
        term = None
    cue = 2 if maker else name_cue(row["company"])
    service = is_service_name(row["company"]) and not maker
    others = None if on_list is None else max(on_list - 1, 0)
    name, aliases = row["company"], []
    dba = _DBA.match(name)
    if dba and dba.group(1).strip(" ,;.") and dba.group(2).strip():
        name, aliases = dba.group(1).strip(" ,;"), [dba.group(2).strip()]

    metrics: dict[str, Any] = {
        "bvlos": 1 if "107.31" in regs else 0,
        "multiple_aircraft_per_pilot": 1 if "107.35" in regs else 0,
        "over_people": 1 if "107.39" in regs else 0,
        "waived_sections": len(regs),
        "waivers_in_window": in_window,
        # 2 aviation or robotics word in the name (or a maker on the declaration
        # list), 1 generic technology word, 0 neither
        "name_cue": cue,
        "remote_id_maker": 1 if maker else 0,
    }
    if term is not None:
        metrics["validity_days"] = term
    if others is not None:
        metrics["other_waivers_on_list"] = others

    sections = "; ".join(f"{sec} {SECTION_TITLES.get(sec, '')}".strip() for sec in regs)
    # The table carries no description of the holder. The first sentence says
    # what the paper is: permission for drone operations, nothing about what
    # the holder builds or sells.
    text = (
        "FAA Part 107 certificate of waiver for small unmanned aircraft (drone) operations"
        + (f", number {row['number']}," if row["number"] else "")
        + f" issued {iso(row['issued'])} to {row['company']}"
        + (f", responsible person {row['person']}" if row["person"] else "")
        + (f", expires {iso(row['expires'])}" if term is not None else "")
        + f". Waived regulations as listed: {row['regs_raw']}. Sections: {sections}."
    )
    people = []
    if row["person"]:
        people.append(Person(name=_as_name(row["person"]), role="Responsible person on FAA Part 107 waiver"))
    return Signal(
        source=SLUG, family=FAMILY, kind=KIND_WAIVER,
        entity=EntityHint(name=name, aliases=aliases),
        title=waiver_title(regs, row["expires"] if term is not None else None),
        occurred_at=iso(row["issued"]),
        url=row["pdf"],
        value=float(term) if term is not None else None,
        unit="days valid" if term is not None else None,
        strength=waiver_strength(regs, cue=cue, term_days=term, others_on_list=others, service=service,
                                 maker=maker),
        metrics=metrics,
        people=people,
        text=" ".join(text.split()),
    )


def select_waivers(rows: list[dict], since: date, today: date,
                   makers: frozenset[str] | set[str] = frozenset()) -> tuple[list[dict], dict[str, int]]:
    """Waivers worth a signal, newest first, plus counts of what was dropped.

    `makers` holds the name keys of manufacturers on the declaration list.
    """
    dropped: dict[str, int] = {}
    keep = []
    bare = bare_maker_keys(makers)
    for r in rows:
        if r["issued"] > today:
            # The column behaves like an effective date. A waiver dated in the
            # future is picked up by a later run, once that day has come.
            dropped["dated in the future"] = dropped.get("dated in the future", 0) + 1
            continue
        if r["issued"] < since:
            continue
        if not qualifies(r["regs"]):
            dropped["other waiver type"] = dropped.get("other waiver type", 0) + 1
            continue
        reason = drop_reason(r["company"], r["person"])
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        if not r["pdf"]:
            dropped["no document link"] = dropped.get("no document link", 0) + 1
            continue
        # Flight over people alone is the commonest waiver. Without BVLOS or
        # several aircraft per pilot it is only kept for a holder whose name
        # (or a declaration on the FAA list) says it is a drone company.
        if "107.31" not in r["regs"] and "107.35" not in r["regs"] \
                and not is_listed_maker(r["company"], makers, bare) \
                and (name_cue(r["company"]) == 0 or is_service_name(r["company"])):
            why = "over people only, by a drone user or pilot for hire"
            dropped[why] = dropped.get(why, 0) + 1
            continue
        keep.append(r)
    keep.sort(key=lambda r: r["issued"], reverse=True)
    return keep, dropped


def _stamp(headers: dict) -> float:
    try:
        return parsedate_to_datetime(headers.get("date", "")).timestamp()
    except (TypeError, ValueError):
        return time.time()


def merge_listings(main: list[dict], extra: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Union of the main table walk and the per-regulation searches.

    The FAA table sorts by date only, and its paging is not stable among rows
    that share a date: at a page boundary one row can be served twice and its
    neighbour not at all. Reading the same rows through a second, differently
    paged listing recovers the ones that fell in the gap.
    """
    merged = dedupe_waivers(main)
    repeated = len(main) - len(merged)
    before = len(merged)
    merged = dedupe_waivers(merged + extra)
    merged.sort(key=lambda r: r["issued"], reverse=True)
    return merged, {"repeated": repeated, "recovered": len(merged) - before}


def _walk_listing(ctx: Context, keyword: str | None, ttl: float, stop) -> tuple[list[dict], list[float], bool]:
    """Read one listing from page 0 until `stop(page_rows, rows)` or the end.

    Returns (rows, response timestamps, reached_the_end).
    """
    rows: list[dict] = []
    stamps: list[float] = []
    complete = False
    for page in range(WAIVER_MAX_PAGES):
        params: dict[str, Any] = {"page": page}
        if keyword:
            params = {WAIVER_SEARCH_PARAM: keyword, "page": page}
        t0 = time.monotonic()
        try:
            body, headers = http.get(WAIVER_URL, params=params, headers={"User-Agent": WAIVER_UA},
                                     ttl=ttl, return_headers=True)
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"faa_uas: waiver table {keyword or 'main'} page {page} failed: {e}")
            break
        took = time.monotonic() - t0
        if took > 0.08:  # a real request, not a cache hit: keep to one a second
            time.sleep(max(0.0, WAIVER_INTERVAL - took))
        page_rows = parse_waiver_page(body)
        if not page_rows:
            complete = page > 0
            break
        rows.extend(page_rows)
        stamps.append(_stamp(headers))
        if stop(page_rows, rows):
            break
    return rows, stamps, complete


def _read_waivers(ctx: Context, *, full: bool, quota: int | None,
                  makers: set[str]) -> tuple[list[dict], bool, dict[str, int]]:
    """All waiver rows needed for this run: (rows, whole_table_read, paging stats)."""

    def stop_main(page_rows: list[dict], rows: list[dict]) -> bool:
        if full:
            return False
        if max(r["issued"] for r in page_rows) < ctx.since:
            return True
        if quota is not None:
            kept, _ = select_waivers(dedupe_waivers(rows), ctx.since, ctx.today, makers)
            return len({make_key(r["company"]) for r in kept}) >= quota
        return False

    ttl = WAIVER_TTL
    main: list[dict] = []
    extra: list[dict] = []
    complete = False
    for attempt in range(2):
        main, stamps, complete = _walk_listing(ctx, None, ttl, stop_main)
        if not main:
            break
        # How far back the main walk reached; the searches cover the same span.
        floor = ctx.since if full else max(ctx.since, min(r["issued"] for r in main))
        extra = []
        for keyword in QUALIFYING:
            found, more, _ = _walk_listing(
                ctx, keyword, ttl, lambda page_rows, rows: max(r["issued"] for r in page_rows) < floor)
            extra.extend(r for r in found if r["issued"] >= floor)
            stamps.extend(more)
        if attempt == 0 and stamps and max(stamps) - min(stamps) > SNAPSHOT_SPREAD:
            ctx.log("faa_uas: cached waiver pages are from different times, refreshing the stale ones")
            ttl = SNAPSHOT_SPREAD
            continue
        break
    merged, stats = merge_listings(main, extra)
    return merged, complete, stats


def _collect_waivers(ctx: Context, quota: int | None, makers: set[str] | None = None) -> Iterator[Signal]:
    if quota is not None and quota <= 0:
        return
    makers = makers or set()
    full = ctx.limit is None or ctx.limit >= HISTORY_MIN_LIMIT
    rows, complete, paging = _read_waivers(ctx, full=full, quota=quota, makers=makers)
    if not rows:
        return
    keep, dropped = select_waivers(rows, ctx.since, ctx.today, makers)

    all_by_company: dict[str, int] = {}
    window_by_company: dict[str, int] = {}
    for r in rows:
        key = make_key(r["company"])
        all_by_company[key] = all_by_company.get(key, 0) + 1
        if ctx.since <= r["issued"] <= ctx.today:
            window_by_company[key] = window_by_company.get(key, 0) + 1

    bare = bare_maker_keys(makers)
    entities: set[str] = set()
    emitted = 0
    for r in keep:
        key = make_key(r["company"])
        if quota is not None and key not in entities and len(entities) >= quota:
            continue
        try:
            sig = waiver_signal(r, on_list=all_by_company[key] if complete else None,
                                in_window=window_by_company.get(key, 1),
                                maker=is_listed_maker(r["company"], makers, bare))
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"faa_uas: waiver row for {r['company']!r} failed: {e}")
            continue
        entities.add(key)
        emitted += 1
        yield sig
    drops = ", ".join(f"{n} {k}" for k, n in sorted(dropped.items(), key=lambda kv: -kv[1]))
    ctx.log(f"faa_uas: {len(rows)} waiver rows read ({'whole table' if complete else 'newest pages only'}), "
            f"{emitted} signals for {len(entities)} companies; dropped in window: {drops or 'none'}")
    ctx.log(f"faa_uas: FAA paging repeated {paging['repeated']} rows; "
            f"{paging['recovered']} rows it skipped were recovered through the regulation search")


def collect(ctx: Context) -> Iterable[Signal]:
    # With a limit, the two feeds share it: declarations get half, rounded up.
    doc_quota = None if ctx.limit is None else max(1, math.ceil(ctx.limit / 2))
    doc_entities = 0
    makers: set[str] = set()  # manufacturers on the declaration list, for the waiver feed
    try:
        for sig in _collect_docs(ctx, doc_quota, makers):
            doc_entities += 1
            yield sig
    except Exception as e:  # noqa: BLE001 - one feed failing must not lose the other
        ctx.warn(f"faa_uas: declaration feed failed: {e}")
    waiver_quota = None if ctx.limit is None else max(1, ctx.limit - doc_entities)
    try:
        yield from _collect_waivers(ctx, waiver_quota, makers)
    except Exception as e:  # noqa: BLE001
        ctx.warn(f"faa_uas: waiver feed failed: {e}")
