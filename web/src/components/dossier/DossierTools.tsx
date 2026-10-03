"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { StarButton } from "@/components/ui/StarButton";
import { useStarred } from "@/lib/local";
import { useBoardView } from "./boardView";

type Neighbour = { slug: string; name: string };

type Props = {
  slug: string;
  name: string;
  rank: number;
  /** How many companies are ranked. */
  total: number;
  /** Neighbours by global rank: what the server renders, and the fallback. */
  prev?: Neighbour;
  next?: Neighbour;
};

/** One arrow: where it leads and its hover title. */
type Step = { slug: string; title: string };

const CRUMB = "label -m-2 whitespace-nowrap p-2 transition-colors hover:!text-ink";
// A phone has no room for "Previous" and "Next" beside "112 of 300": the arrow stands alone there.
const WORD = "max-sm:sr-only";

const byRank = (n?: Neighbour): Step | undefined => n && { slug: n.slug, title: n.name };
const inView = (slug: string | undefined, title: string): Step | undefined => (slug ? { slug, title } : undefined);

/**
 * The crumbs above a dossier: back to the board, previous and next, where
 * this company sits, and its star. A reader who came from a filtered board
 * steps through that list and returns to it; anyone else steps by rank.
 * Left and right arrows flip, s stars.
 */
export function DossierTools({ slug, name, rank, total, prev, next }: Props) {
  const [starred, toggle] = useStarred();
  const router = useRouter();
  const view = useBoardView();

  // The reader's list only counts when this company is on it.
  const list = view?.slugs.includes(slug) ? view.slugs : null;
  const at = list ? list.indexOf(slug) : -1;
  const before = list ? inView(list[at - 1], "Previous in your view") : byRank(prev);
  const after = list ? inView(list[at + 1], "Next in your view") : byRank(next);
  const left = before?.slug;
  const right = after?.slug;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Shift with an arrow extends a text selection; it is not a flip.
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const el = e.target as HTMLElement | null;
      if (el?.closest?.("input, textarea, select, [contenteditable], [role=dialog]")) return;
      // Replace, so a run of flips is one history entry and Back returns to the board.
      if (e.key === "ArrowLeft" && left) router.replace(`/c/${left}/`);
      else if (e.key === "ArrowRight" && right) router.replace(`/c/${right}/`);
      else if (e.key === "s") toggle(slug);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [left, right, slug, router, toggle]);

  return (
    <nav aria-label="Dossier" className="flex items-center justify-between gap-4 border-b border-rule py-4">
      <Link href={`/${view?.search ?? ""}`} className={CRUMB}>
        ← Board
      </Link>
      <div className="flex items-center gap-3 sm:gap-5">
        {before && (
          <Link href={`/c/${before.slug}/`} replace className={CRUMB} title={`${before.title} (left arrow)`}>
            ← <span className={WORD}>Previous</span>
          </Link>
        )}
        <span className="label whitespace-nowrap !text-ink">
          {list ? `${at + 1} of ${list.length}` : `${rank} of ${total}`}
        </span>
        {after && (
          <Link href={`/c/${after.slug}/`} replace className={CRUMB} title={`${after.title} (right arrow)`}>
            <span className={WORD}>Next</span> →
          </Link>
        )}
        <StarButton on={starred.includes(slug)} onToggle={() => toggle(slug)} name={name} size={16} />
      </div>
    </nav>
  );
}
