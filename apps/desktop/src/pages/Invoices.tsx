import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Button, ErrorBox, Input, PageHeader, Select, Spinner, Table, Td, Th, TierBadge } from "../components/ui";
import { money, num } from "../lib/format";

const TIER_BY_RANK: Record<number, string> = { 4: "HARD", 3: "PROBABLE", 2: "WEAK", 1: "INFO" };

export default function InvoicesPage() {
  const [sp] = useSearchParams();
  const [f, setF] = useState({ q: "", status: "", from: "", to: "", direction: "", flagged: "", party_id: sp.get("party_id") ?? "" });
  const [page, setPage] = useState(0);
  const limit = 100;
  const q = useQuery({
    queryKey: ["invoices", f, page],
    queryFn: () => api.get("/invoices", { ...f, limit, offset: page * limit }),
  });
  const set = (k: string, v: string) => {
    setPage(0);
    setF({ ...f, [k]: v });
  };
  return (
    <div className="space-y-3">
      <PageHeader title="Invoices" subtitle={q.data ? `${num(q.data.total)} matching` : undefined} />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        <Input label="Search number, vendor or PO" value={f.q} onChange={(e) => set("q", e.target.value)} />
        <Select label="Status" value={f.status} onChange={(e) => set("status", e.target.value)} options={[["", "Any"], ["OPEN", "Open"], ["PAID", "Paid"], ["VOID", "Void"], ["CREDIT", "Credit"]]} />
        <Select label="Direction" value={f.direction} onChange={(e) => set("direction", e.target.value)} options={[["", "Any"], ["AP", "AP"], ["AR", "AR"]]} />
        <Select label="Flagged" value={f.flagged} onChange={(e) => set("flagged", e.target.value)} options={[["", "Any"], ["true", "Hard/probable flag"], ["false", "No strong flag"]]} />
        <Input label="From" type="date" value={f.from} onChange={(e) => set("from", e.target.value)} />
        <Input label="To" type="date" value={f.to} onChange={(e) => set("to", e.target.value)} />
      </div>
      {q.isLoading && <Spinner />}
      <ErrorBox error={q.error} />
      {q.data && (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Date</Th>
                <Th>Party</Th>
                <Th>Invoice #</Th>
                <Th className="text-right">Total</Th>
                <Th className="text-right">Lines</Th>
                <Th>Status</Th>
                <Th>Flag</Th>
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((i: any) => (
                <tr key={i.id} className="hover:bg-surface-2">
                  <Td className="num whitespace-nowrap">{i.invoice_date ?? <span className="text-ink-3">{i.invoice_date_raw} ?</span>}</Td>
                  <Td>{i.party}</Td>
                  <Td>
                    <Link className="text-accent-ink hover:underline" to={`/invoices/${i.id}`}>
                      {i.invoice_number}
                    </Link>
                    {i.needs_attention && (
                      <span className="ml-1">
                        <Badge tone="warn">check date</Badge>
                      </span>
                    )}
                  </Td>
                  <Td className="num text-right">{money(i.total_cents)}</Td>
                  <Td className="num text-right">{i.line_count}</Td>
                  <Td>
                    {i.direction} · {i.status.toLowerCase()}
                  </Td>
                  <Td>{i.max_tier_rank ? <TierBadge tier={TIER_BY_RANK[i.max_tier_rank]} /> : null}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <div className="flex items-center gap-2 text-sm">
            <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <span className="text-ink-2">
              Page {page + 1} of {Math.max(1, Math.ceil(q.data.total / limit))}
            </span>
            <Button size="sm" disabled={(page + 1) * limit >= q.data.total} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
