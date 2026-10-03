import Link from "next/link";
import { meta } from "@/lib/data";
import { CommandPalette } from "./CommandPalette";
import { NavLinks } from "./NavLinks";
import { RunStamp } from "./RunStamp";

/** Page frame: wordmark, navigation, a run stamp, and the footer. */
export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      {/* First in the tab order, and off screen until it has focus. */}
      <a
        href="#main"
        className="label fixed left-4 top-3 z-50 -translate-y-16 border border-ink bg-paper px-3 py-2 !text-ink transition-transform duration-150 focus:translate-y-0 print:hidden"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-rule bg-paper/90 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-[1320px] items-center justify-between px-5 sm:px-8">
          <div className="flex items-baseline gap-4">
            <Link href="/" aria-label="Antenna, home" className="transition-colors duration-150 hover:text-ink-3">
              <span className="display text-[22px] tracking-[-0.02em]">Antenna</span>
            </Link>
            {/* Below this width the header has no room; the footer carries the run date. */}
            <RunStamp generatedAt={meta.generatedAt} asOf={meta.asOf} className="hidden md:block" />
          </div>
          <div className="flex items-center gap-4 sm:gap-7">
            <NavLinks />
            <CommandPalette />
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="flex-1">
        {children}
      </main>
      <footer className="mt-24 border-t border-rule">
        <div className="mx-auto flex max-w-[1320px] flex-col gap-3 px-5 py-8 sm:flex-row sm:items-center sm:px-8">
          {/* What the header drops on a narrow screen: the stamp below md, Method below sm. */}
          <div className="flex items-baseline justify-between gap-4 md:hidden">
            <RunStamp generatedAt={meta.generatedAt} asOf={meta.asOf} />
            <Link
              href="/method/"
              className="label -my-2 py-2 !text-ink underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink sm:hidden"
            >
              Method
            </Link>
          </div>
          <p className="label sm:ml-auto">
            Mason Tilghman ·{" "}
            <a
              href="mailto:mason@alterity.systems"
              className="normal-case tracking-normal !text-ink underline decoration-rule-2 underline-offset-4 transition-colors hover:decoration-ink"
            >
              mason@alterity.systems
            </a>
          </p>
        </div>
      </footer>
    </>
  );
}
