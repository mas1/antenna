"use client";

import { useSyncExternalStore } from "react";
import { localStamp, shortDate } from "@/lib/format";

/** A daily run that is this late has been missed: a day, and half a day's grace. */
const STALE_MS = 36 * 3_600_000;

// Age is read off the clock, so it is looked at again every minute and when
// the tab comes back to the front: a board left open overnight must say so.
function subscribe(onChange: () => void) {
  const timer = window.setInterval(onChange, 60_000);
  document.addEventListener("visibilitychange", onChange);
  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onChange);
  };
}

type Props = {
  /** When the pipeline finished, as an ISO instant. */
  generatedAt: string;
  /** The run date, which is all the server can print. */
  asOf: string;
  className?: string;
};

/**
 * When the data was last run, on the reader's own clock, and a warning once
 * that is more than 36 hours ago. The server knows neither the reader's time
 * zone nor the time of reading, so its HTML carries the date alone and the
 * browser fills in the rest after hydration.
 */
export function RunStamp({ generatedAt, asOf, className }: Props) {
  const age = useSyncExternalStore(
    subscribe,
    () => (Date.now() - Date.parse(generatedAt) > STALE_MS ? "stale" : "fresh"),
    () => null,
  );
  return (
    <p className={`label ${className ?? ""}`}>
      Run {age ? localStamp(generatedAt) : shortDate(asOf, asOf)}
      {age === "stale" && (
        <>
          {" · "}
          <span className="text-heat" title="This run is more than 36 hours old">
            Stale
          </span>
        </>
      )}
    </p>
  );
}
