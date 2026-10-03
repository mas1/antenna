import type { Metadata } from "next";
import Link from "next/link";
import { meta } from "@/lib/data";

export const metadata: Metadata = {
  title: "Not found · Antenna",
};

/**
 * Mostly reached from an old link to a company. The board is rebuilt on
 * every run, so a dossier that was there last week can be gone today.
 */
export default function NotFound() {
  return (
    <div className="mx-auto max-w-[1320px] px-5 sm:px-8">
      <header className="pb-10 pt-14 sm:pt-20">
        <h1 className="display text-[44px] sm:text-[64px]">Page not found</h1>
        <p className="mt-6 max-w-[62ch] text-[15px] leading-relaxed text-ink-2 sm:text-base">
          If it was a company, it may have dropped out of the top{" "}
          <span className="num">{meta.totals.board}</span> or been removed on review.
        </p>
        <p className="mt-6 flex">
          <Link
            href="/"
            className="label py-2 !text-ink underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink"
          >
            Back to the board
          </Link>
        </p>
      </header>
    </div>
  );
}
