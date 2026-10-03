import type { Metadata } from "next";
import { Founders } from "@/components/founders/Founders";
import { people } from "@/lib/data";

export const metadata: Metadata = {
  title: "Founders · Antenna",
  description: "The people behind the ranked companies, as named in filings, papers and code.",
};

export default function Page() {
  return (
    <div className="mx-auto max-w-[1320px] px-5 pt-6 sm:px-8">
      <h1 className="sr-only">Founders</h1>
      <Founders rows={people()} />
    </div>
  );
}
