import { Board, type ClientRow } from "@/components/board/Board";
import { Leads } from "@/components/board/Leads";
import { board, meta } from "@/lib/data";
import type { BoardRow } from "@/lib/types";

// The board is a client component, so its rows are inlined into the page.
// Send only the fields it reads; the lead cards render here and keep the rest.
function forBoard(r: BoardRow): ClientRow {
  return {
    slug: r.slug,
    name: r.name,
    oneLiner: r.oneLiner,
    domain: r.domain,
    location: r.location,
    sector: r.sector,
    rank: r.rank,
    rankPrev: r.rankPrev,
    edge: r.edge,
    momentum: r.momentum,
    fit: r.fit,
    earliness: r.earliness,
    families: r.families,
    history: r.history,
    why: r.why,
    latest: r.latest,
    founded: r.founded,
    raisedUsd: r.raisedUsd,
    bySignalFamily: r.bySignalFamily,
    lastSignalAt: r.lastSignalAt,
    flags: r.flags,
    // Optional in the export: left out rather than sent as undefined.
    ...(r.hasBrief && { hasBrief: true }),
    ...(r.stage && { stage: r.stage }),
  };
}

export default function Page() {
  return (
    <>
      <h1 className="sr-only">Antenna: ranked companies</h1>
      <Board rows={board.map(forBoard)} asOf={meta.asOf}>
        <Leads rows={board} asOf={meta.asOf} />
      </Board>
    </>
  );
}
