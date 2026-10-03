import { Reveal } from "./Reveal";

type Props = {
  label: string;
  /** Hover text on the label. */
  title?: string;
  /** Small print under the label, in the left column. */
  note?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
};

/** A ruled section: mono label in a narrow left column, content on the right. */
export function Section({ label, title, note, children, className }: Props) {
  return (
    <Reveal as="section" className={`border-t border-rule pt-6 ${className ?? ""}`}>
      <div className="grid grid-cols-12 gap-x-6 gap-y-4">
        <div className="col-span-12 lg:col-span-2">
          <h2 className="label" title={title}>
            {label}
          </h2>
          {note && <p className="mt-2 max-w-[26ch] text-[12px] leading-snug text-ink-4">{note}</p>}
        </div>
        <div className="col-span-12 lg:col-span-10">{children}</div>
      </div>
    </Reveal>
  );
}
