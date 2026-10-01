import { AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";

/** A line's open hard/probable duplicate flags, as returned by the engine in `line.duplicates`. */
export type LineDuplicate = {
  flag_id: number;
  rule_id: string;
  tier: string;
  summary: string;
  same_invoice: boolean;
  other_invoice_id: number;
  other_invoice_number: string | null;
  other_line_no: number;
};

export const isPossibleDuplicate = (line: { duplicates?: LineDuplicate[] }) => (line.duplicates?.length ?? 0) > 0;

/** Row shading for a line that may be a duplicate (paired with the text label, never colour alone). */
export const duplicateRowClass = "bg-diff";

export function countPossibleDuplicates(lines: Array<{ duplicates?: LineDuplicate[] }>): number {
  return lines.filter(isPossibleDuplicate).length;
}

/** "Billed before on 407444 (line 8)" / "Repeated on this invoice (line 2)", linking to the flag. */
export function DuplicateLabel({ dups, onNavigate }: { dups?: LineDuplicate[]; onNavigate?: () => void }) {
  if (!dups?.length) return <span className="text-ink-3">—</span>;
  const d = dups[0];
  const text = d.same_invoice
    ? `Repeated on this invoice (line ${d.other_line_no})`
    : `Billed before on ${d.other_invoice_number ?? "another invoice"} (line ${d.other_line_no})`;
  return (
    <Link
      to={`/review/${d.flag_id}`}
      onClick={onNavigate}
      title={dups.map((x) => `${x.rule_id}: ${x.summary}`).join("\n")}
      className="inline-flex items-center gap-1 font-medium text-diff-ink hover:underline"
    >
      <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
      <span>{text}</span>
      {dups.length > 1 && <span className="font-normal">+{dups.length - 1}</span>}
    </Link>
  );
}
