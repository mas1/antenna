"use client";

import { useCallback, useSyncExternalStore } from "react";

// Two things a daily user wants the browser to remember: which companies
// they starred, and which run they last looked at. Both live in
// localStorage; nothing leaves the machine.

const STAR_KEY = "antenna:starred";
const RUN_KEY = "antenna:run";
const EVENT = "antenna:local";
const EMPTY: readonly string[] = [];

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(EVENT, onChange);
  };
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null; // private mode, storage disabled
  }
}

function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // nothing to do: the feature simply does not persist
  }
}

// useSyncExternalStore needs the same reference for the same content.
let starCache: { raw: string | null; list: readonly string[] } = { raw: null, list: EMPTY };
function starSnapshot(): readonly string[] {
  const raw = read(STAR_KEY);
  if (raw !== starCache.raw) {
    let list: readonly string[] = EMPTY;
    try {
      const parsed = raw ? JSON.parse(raw) : [];
      if (Array.isArray(parsed)) list = parsed.filter((x) => typeof x === "string");
    } catch {
      list = EMPTY;
    }
    starCache = { raw, list };
  }
  return starCache.list;
}

/** Starred company slugs, and a toggle. */
export function useStarred(): [readonly string[], (slug: string) => void] {
  const list = useSyncExternalStore(subscribe, starSnapshot, () => EMPTY);
  const toggle = useCallback((slug: string) => {
    const cur = starSnapshot();
    const next = cur.includes(slug) ? cur.filter((s) => s !== slug) : [...cur, slug];
    write(STAR_KEY, JSON.stringify(next));
    window.dispatchEvent(new Event(EVENT));
  }, []);
  return [list, toggle];
}

// "Since your last visit": the run you saw before this one. Worked out once
// per page load, and kept for as long as the current run is the latest, so
// the markers do not vanish on a reload.
const baselines = new Map<string, string | null>();
function baselineFor(asOf: string): string | null {
  if (baselines.has(asOf)) return baselines.get(asOf) ?? null;
  let stored: { run?: string; prev?: string | null } = {};
  try {
    stored = JSON.parse(read(RUN_KEY) ?? "{}") ?? {};
  } catch {
    stored = {};
  }
  let prev: string | null;
  if (stored.run === asOf) {
    prev = stored.prev ?? null;
  } else {
    prev = stored.run && stored.run < asOf ? stored.run : null;
    write(RUN_KEY, JSON.stringify({ run: asOf, prev }));
  }
  baselines.set(asOf, prev);
  return prev;
}

/** The date of the run this browser last saw before the current one, or null. */
export function useLastRun(asOf: string): string | null {
  return useSyncExternalStore(
    subscribe,
    () => baselineFor(asOf),
    () => null,
  );
}
