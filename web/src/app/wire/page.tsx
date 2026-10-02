import type { Metadata } from "next";
import { Wire } from "@/components/wire/Wire";
import { feed, meta } from "@/lib/data";

export const metadata: Metadata = {
  title: "Wire · Antenna",
  description: "Every dated signal on a ranked company, newest first.",
};

export default function Page() {
  return (
    <div className="mx-auto max-w-[1320px] px-5 sm:px-8">
      <h1 className="label pb-4 pt-6">The wire · {feed.length.toLocaleString("en-US")} latest signals</h1>
      <Wire items={feed} asOf={meta.asOf} />
    </div>
  );
}
