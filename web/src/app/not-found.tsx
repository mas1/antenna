import type { Metadata } from "next";
import Link from "next/link";
import { meta } from "@/lib/data";

export const metadata: Metadata = {
  title: "Not on the board · Antenna",
};

const LINK =
  "label py-2 !text-ink underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink";

/**
 * Mostly reached from an old link to a company. The board is rebuilt on
 * every run, so a dossier that was there last week can be gone today.
 */
export default function NotFound() {
  return (
    <div className="mx-auto max-w-[1320px] px-5 sm:px-8">
      <header className="pb-10 pt-14 sm:pt-20">
        <p className="label">Not found</p>
        <h1 className="display mt-5 max-w-[20ch] text-[44px] sm:text-[64px]">This page is not on the board.</h1>
        <p className="mt-6 max-w-[62ch] text-[15px] leading-relaxed text-ink-2 sm:text-base">
          If it was a company, it may have dropped below the top{" "}
          <span className="num">{meta.totals.board}</span> on the latest run, or been removed on review.
        </p>
        <p className="mt-6 flex flex-wrap gap-x-8 gap-y-1">
          <Link href="/" className={LINK}>
            Back to the board
          </Link>
          <Link href="/wire/" className={LINK}>
            Read the wire
          </Link>
        </p>
      </header>
    </div>
  );
}
