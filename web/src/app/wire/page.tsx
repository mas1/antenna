import type { Metadata } from "next";
import { Wire } from "@/components/wire/Wire";
import { feed, meta } from "@/lib/data";

export const metadata: Metadata = {
  title: "Wire · Antenna",
  description: "The latest dated signals on ranked companies.",
};

export default function Page() {
  return (
    <div className="mx-auto max-w-[1320px] px-5 pt-6 sm:px-8">
      <h1 className="sr-only">Wire</h1>
      <Wire items={feed} asOf={meta.asOf} />
    </div>
  );
}
