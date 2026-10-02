import type { Metadata } from "next";
import { Founders } from "@/components/founders/Founders";
import { people } from "@/lib/data";

export const metadata: Metadata = {
  title: "Founders · Antenna",
  description: "The people behind the ranked companies, as named in filings, papers and code.",
};

export default function Page() {
  const rows = people();
  const companies = new Set(rows.map((r) => r.slug)).size;
  return (
    <div className="mx-auto max-w-[1320px] px-5 sm:px-8">
      <h1 className="label pb-4 pt-6">
        Founders · {rows.length.toLocaleString("en-US")} people across {companies} companies
      </h1>
      <Founders rows={rows} />
    </div>
  );
}
