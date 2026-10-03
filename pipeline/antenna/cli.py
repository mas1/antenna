"""Command line.

    python -m antenna run                 collect, resolve, score, export
    python -m antenna run --only sec_form_d,github_velocity
    python -m antenna run --no-collect    re-score and re-export what is stored
    python -m antenna probe sec_form_d    run one collector, print, write nothing
    python -m antenna list                show the registered collectors
    python -m antenna report              print the current top of the board
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from . import http
from .collectors import load_all
from .collectors.base import Context
from .config import DATA_DIR, DB_PATH, EXPORT_DIR, LOOKBACK_DAYS
from .db import connect, insert_signals, now_iso, prune_superseded
from .export import export
from .resolve import resolve
from .score import score_all, thesis_of
from .thesis import classify


def _today(arg: str | None) -> date:
    """The run date. UTC, because every source stamps its records in UTC."""
    return date.fromisoformat(arg) if arg else datetime.now(timezone.utc).date()


def _run_collector(mod, ctx: Context) -> tuple[list, float, str | None]:
    t0 = time.monotonic()
    try:
        signals = list(mod.collect(ctx))
        return signals, time.monotonic() - t0, None
    except Exception as e:  # one collector failing must not sink the run
        traceback.print_exc()
        return [], time.monotonic() - t0, f"{type(e).__name__}: {e}"


def _known_entities(conn, limit: int, min_fit: float = 0.3) -> list[dict]:
    """On-thesis entities found so far, strongest first, for enrichers.

    Each dict carries the identifiers enrichers key on (domain, github,
    name) plus `ats`, any job-board slugs a discovery collector spotted.
    """
    out = []
    for e in conn.execute("SELECT * FROM entities"):
        sigs = conn.execute(
            "SELECT title, text, strength, source, family, metrics FROM signals WHERE entity_id=?", (e["id"],)
        ).fetchall()
        c = thesis_of(dict(e), [dict(s) for s in sigs])
        if c["fit"] < min_fit:
            continue
        ats = []
        for s in sigs:
            m = json.loads(s["metrics"] or "{}")
            if m.get("ats_provider") and m.get("ats_slug"):
                pair = {"provider": str(m["ats_provider"]), "slug": str(m["ats_slug"]), "source": s["source"]}
                if not any(a["provider"] == pair["provider"] and a["slug"] == pair["slug"] for a in ats):
                    ats.append(pair)
        families = {s["source"] for s in sigs}
        out.append({
            "slug": e["slug"], "name": e["name"], "kind": e["kind"], "domain": e["domain"],
            "github": e["github"], "sector": c["sector"], "fit": c["fit"],
            "one_liner": e["one_liner"], "description": e["description"], "location": e["location"],
            "links": json.loads(e["links"] or "{}"),
            "sources": sorted(families),
            "ats": ats,
            # Fit, total strength, and a lift for being seen by several sources.
            "prelim": round(c["fit"] * (1 + sum(s["strength"] for s in sigs)) * (1 + 0.5 * (len(families) - 1)), 3),
        })
    out.sort(key=lambda d: -d["prelim"])
    return out[:limit]


def cmd_run(args) -> int:
    today = _today(args.today)
    conn = connect()
    run_id = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (now_iso(),)).lastrowid
    conn.commit()

    mods = load_all()
    if args.only:
        want = set(args.only.split(","))
        mods = [m for m in mods if m.SLUG in want]
    if args.skip:
        skip = set(args.skip.split(","))
        mods = [m for m in mods if m.SLUG not in skip]

    def stage(name: str, stage_mods: list, known: list[dict]) -> None:
        if not stage_mods:
            return
        print(f"\n[{name}] {', '.join(m.SLUG for m in stage_mods)}", file=sys.stderr)
        ctxs = {m.SLUG: Context(today=today, lookback_days=args.lookback, limit=args.limit, known=known)
                for m in stage_mods}
        with ThreadPoolExecutor(max_workers=min(8, len(stage_mods))) as pool:
            futures = {m.SLUG: pool.submit(_run_collector, m, ctxs[m.SLUG]) for m in stage_mods}
        for m in stage_mods:
            signals, seconds, error = futures[m.SLUG].result()
            good = []
            for s in signals:
                try:
                    s.validate()
                    good.append(s)
                except ValueError as ve:
                    ctxs[m.SLUG].warn(f"dropped invalid signal: {ve}")
            emitted, inserted = insert_signals(conn, run_id, good)
            conn.execute(
                "INSERT OR REPLACE INTO collector_runs VALUES (?,?,?,?,?,?,?)",
                (run_id, m.SLUG, now_iso(), round(seconds, 1), emitted, inserted, error),
            )
            conn.commit()
            status = f"ERROR {error}" if error else "ok"
            print(f"  {m.SLUG:<22} {emitted:>5} signals  {inserted:>5} new  {seconds:>6.1f}s  {status}",
                  file=sys.stderr)

    if not args.no_collect:
        by_stage = lambda name: [m for m in mods if getattr(m, "STAGE", "discover") == name]  # noqa: E731
        stage("discover", by_stage("discover"), [])
        n = resolve(conn)
        print(f"\n[resolve] {n} entities after discovery", file=sys.stderr)
        # Identify: find a website for companies that only exist on paper so
        # far. Any thesis evidence at all qualifies, since a site is often
        # the first real description a filing-only company gets.
        if by_stage("identify"):
            known = _known_entities(conn, args.identify_top, min_fit=0.01)
            print(f"[identify] {sum(1 for k in known if not k['domain'])} of {len(known)} candidates have no domain",
                  file=sys.stderr)
            stage("identify", by_stage("identify"), known)
            n = resolve(conn)
            print(f"\n[resolve] {n} entities after identification", file=sys.stderr)
        if by_stage("enrich"):
            known = _known_entities(conn, args.enrich_top)
            print(f"[enrich] {len(known)} on-thesis entities go to enrichment", file=sys.stderr)
            stage("enrich", by_stage("enrich"), known)
    else:
        # Carry the last collector report forward so the export still has one.
        prev = conn.execute("SELECT MAX(run_id) r FROM collector_runs").fetchone()["r"]
        if prev:
            conn.execute(
                "INSERT OR REPLACE INTO collector_runs SELECT ?, source, started_at, seconds,"
                " emitted, inserted, error FROM collector_runs WHERE run_id=?", (run_id, prev))

    n = resolve(conn)
    prune_superseded(conn)
    scored = score_all(conn, run_id, today)
    meta = export(conn, run_id, today, collected=not args.no_collect)
    stats = {"entities": n, "scored": scored, "http": http.stats(), "totals": meta["totals"]}
    conn.execute("UPDATE runs SET finished_at=?, stats=? WHERE id=?", (now_iso(), json.dumps(stats), run_id))
    conn.commit()
    print(f"\n[done] {n} entities, {scored} scored, {meta['totals']['board']} on the board", file=sys.stderr)
    print(f"       db {DB_PATH}\n       export {EXPORT_DIR}", file=sys.stderr)
    print(f"       http {http.stats()}", file=sys.stderr)
    return 0


def cmd_probe(args) -> int:
    """Run one collector and print what it emits. Touches no database."""
    today = _today(args.today)
    mods = {m.SLUG: m for m in load_all()}
    if args.slug not in mods:
        print(f"no collector {args.slug!r}; have: {', '.join(sorted(mods))}", file=sys.stderr)
        return 2
    known = []
    if args.known:
        known = json.loads(open(args.known).read())
    ctx = Context(today=today, lookback_days=args.lookback, limit=args.limit or None, known=known)
    t0 = time.monotonic()
    signals = list(mods[args.slug].collect(ctx))
    bad = 0
    for s in signals:
        try:
            s.validate()
        except ValueError as e:
            bad += 1
            print(f"INVALID: {e}", file=sys.stderr)
    for s in signals[: args.show]:
        row = s.to_row()
        row["thesis"] = classify(" ".join(filter(None, [s.entity.name, s.entity.one_liner, s.entity.description, s.title, s.text])))
        if row.get("text"):
            row["text"] = row["text"][:240]
        print(json.dumps(row, default=str, ensure_ascii=False))
    on = sum(1 for s in signals if classify(" ".join(filter(None, [s.entity.name, s.entity.one_liner, s.entity.description, s.title, s.text])))["fit"] >= 0.3)
    print(f"\n{args.slug}: {len(signals)} signals ({on} on-thesis, {bad} invalid), "
          f"{len({(s.entity.domain or s.entity.github or s.entity.name) for s in signals})} entities, "
          f"{time.monotonic() - t0:.1f}s, http {http.stats()}, {len(ctx.warnings)} warnings", file=sys.stderr)
    return 1 if bad else 0


def cmd_list(_args) -> int:
    for m in load_all():
        print(f"{m.SLUG:<22} {m.FAMILY:<11} {getattr(m, 'STAGE', 'discover'):<9} {getattr(m, 'DESCRIPTION', '')}")
    return 0


def cmd_report(args) -> int:
    board = json.loads((EXPORT_DIR / "board.json").read_text())
    for b in board[: args.n]:
        fams = "".join(f[0].upper() if v >= 0.12 else "." for f, v in b["families"].items())
        print(f"{b['rank']:>3} {b['edge']:>5.1f}  {fams}  {b['sector']:<14} {b['name'][:34]:<34} "
              f"{(b['why'][0]['title'] if b['why'] else '')[:80]}")
    # Companies that scored on thesis but have not been reviewed are not
    # ranked. Show the strongest so a reviewer knows where to start.
    queue_path = DATA_DIR / "awaiting.json"
    if queue_path.exists():
        queue = json.loads(queue_path.read_text())
        if queue:
            print(f"\n{len(queue)} awaiting review. Strongest:")
            for q in queue[:10]:
                print(f"      {q['edge']:>5.1f}  {q['name'][:50]:<50} {q['slug']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="antenna", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, resolve, score, export")
    r.add_argument("--only", help="comma-separated collector slugs")
    r.add_argument("--skip", help="comma-separated collector slugs")
    r.add_argument("--limit", type=int, help="cap items per collector, for quick runs")
    r.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    r.add_argument("--enrich-top", type=int, default=450, help="entities passed to enrichers")
    r.add_argument("--identify-top", type=int, default=700, help="entities passed to website identification")
    r.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    r.add_argument("--no-collect", action="store_true", help="re-score stored signals only")
    r.set_defaults(func=cmd_run)
    pr = sub.add_parser("probe", help="run one collector, print signals, write nothing")
    pr.add_argument("slug")
    pr.add_argument("--limit", type=int, default=25, help="entities to stop after; 0 for no limit")
    pr.add_argument("--show", type=int, default=8, help="signals to print")
    pr.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    pr.add_argument("--today")
    pr.add_argument("--known", help="JSON file of known entities, for enrich collectors")
    pr.set_defaults(func=cmd_probe)
    ls = sub.add_parser("list", help="show collectors")
    ls.set_defaults(func=cmd_list)
    rep = sub.add_parser("report", help="print the top of the board")
    rep.add_argument("-n", type=int, default=40)
    rep.set_defaults(func=cmd_report)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
