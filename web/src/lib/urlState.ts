"use client";

import { useMemo, useSyncExternalStore } from "react";

// The board's filters live in the URL, so a view can be bookmarked, shared,
// and is still there when you come back from a dossier.

const EVENT = "antenna:url";

function subscribe(onChange: () => void) {
  window.addEventListener("popstate", onChange);
  window.addEventListener(EVENT, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(EVENT, onChange);
  };
}

/** The current query string as parsed params. Empty on the server. */
export function useQuery(): URLSearchParams {
  const search = useSyncExternalStore(
    subscribe,
    () => window.location.search,
    () => "",
  );
  return useMemo(() => new URLSearchParams(search), [search]);
}

/** Set, change or (with null or "") remove query params without a navigation. */
export function setQuery(patch: Record<string, string | null>) {
  const params = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(patch)) {
    if (value == null || value === "") params.delete(key);
    else params.set(key, value);
  }
  const qs = params.toString();
  window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  window.dispatchEvent(new Event(EVENT));
}

/** A comma-separated param as a set. */
export function listParam(params: URLSearchParams, key: string): Set<string> {
  return new Set((params.get(key) ?? "").split(",").filter(Boolean));
}

/** Toggle one value inside a comma-separated param. */
export function toggleInList(params: URLSearchParams, key: string, value: string) {
  const set = listParam(params, key);
  if (set.has(value)) set.delete(value);
  else set.add(value);
  setQuery({ [key]: [...set].join(",") });
}
