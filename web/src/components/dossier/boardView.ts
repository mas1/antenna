"use client";

import { useSyncExternalStore } from "react";

// The board records the filtered, sorted list the reader was working before
// it opens a dossier. It lives in sessionStorage, so it belongs to one tab
// and is gone when the tab closes. The board writes it; the dossier reads.

const VIEW_KEY = "antenna:view";

export type BoardView = {
  /** The board's query string ("?sector=energy"), or "" for the plain board. */
  search: string;
  /** Company slugs in the order the board showed them. */
  slugs: readonly string[];
};

function subscribe(onChange: () => void) {
  // Fires only when another frame of this tab writes, but it keeps the hook honest.
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

function parse(raw: string | null): BoardView | null {
  if (!raw) return null;
  try {
    const stored = JSON.parse(raw);
    if (!stored || !Array.isArray(stored.slugs)) return null;
    return {
      // Only a query string may follow "/", so a stored value cannot point off the board.
      search: typeof stored.search === "string" && stored.search.startsWith("?") ? stored.search : "",
      slugs: stored.slugs.filter((s: unknown) => typeof s === "string"),
    };
  } catch {
    return null;
  }
}

// useSyncExternalStore needs the same reference for the same content.
let cache: { raw: string | null; view: BoardView | null } = { raw: null, view: null };
function snapshot(): BoardView | null {
  let raw: string | null;
  try {
    raw = window.sessionStorage.getItem(VIEW_KEY);
  } catch {
    raw = null; // private mode, storage disabled
  }
  if (raw !== cache.raw) cache = { raw, view: parse(raw) };
  return cache.view;
}

/** The list the reader was working on the board, or null when there is none. */
export function useBoardView(): BoardView | null {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}
