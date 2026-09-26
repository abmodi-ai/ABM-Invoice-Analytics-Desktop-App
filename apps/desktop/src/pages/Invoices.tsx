import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Button, ErrorBox, Input, Modal, PageHeader, Select, Spinner, Table, Td, Th, TierBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
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
  const { can } = useAuth();
  const admin = can("ADMIN");
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.post("/invoices/delete", { invoice_ids: [...selected] }),
    onSuccess: () => {
      setSelected(new Set());
      setConfirming(false);
      qc.invalidateQueries();
    },
  });
  const set = (k: string, v: string) => {
    setPage(0);
    setSelected(new Set());
    setF({ ...f, [k]: v });
  };
  const toggle = (id: number) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  const pageIds: number[] = q.data?.items.map((i: any) => i.id) ?? [];
  const allOnPage = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const chosen = (q.data?.items ?? []).filter((i: any) => selected.has(i.id));
  return (
    <div className="space-y-3">
      <PageHeader
        title="Invoices"
        subtitle={q.data ? `${num(q.data.total)} matching` : undefined}
        actions={
          admin && (
            <Button variant="danger" disabled={selected.size === 0} onClick={() => setConfirming(true)}>
              <Trash2 className="size-4" /> Delete selected{selected.size ? ` (${selected.size})` : ""}
            </Button>
          )
        }
      />
      <Modal open={confirming} onClose={() => setConfirming(false)} title={`Delete ${selected.size} invoice${selected.size === 1 ? "" : "s"}?`}>
        <p className="text-sm">
          These will be permanently deleted, with their lines, source documents and every flag involving them. The
          deletion is recorded in the audit log. This can&apos;t be undone.
        </p>
        <ul className="mt-2 max-h-48 list-disc overflow-auto pl-5 text-sm">
          {chosen.map((i: any) => (
            <li key={i.id}>
              {i.invoice_number} · {i.party} · {money(i.total_cents)}
            </li>
          ))}
        </ul>
        <ErrorBox error={remove.error} />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
          <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
            Delete {selected.size === 1 ? "invoice" : `${selected.size} invoices`}
          </Button>
        </div>
      </Modal>
      {remove.data && (
        <p className="text-sm text-good-ink" role="status">
          Deleted {num(remove.data.invoices)} invoice{remove.data.invoices === 1 ? "" : "s"} and {num(remove.data.flags)} flag
          {remove.data.flags === 1 ? "" : "s"}.
        </p>
      )}
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
                {admin && (
                  <Th className="w-8">
                    <input
                      type="checkbox"
                      aria-label="Select all invoices on this page"
                      checked={allOnPage}
                      onChange={() => setSelected(allOnPage ? new Set() : new Set(pageIds))}
                    />
                  </Th>
                )}
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
                <tr key={i.id} className={selected.has(i.id) ? "bg-surface-2" : "hover:bg-surface-2"}>
                  {admin && (
                    <Td>
                      <input type="checkbox" aria-label={`Select invoice ${i.invoice_number}`} checked={selected.has(i.id)} onChange={() => toggle(i.id)} />
                    </Td>
                  )}
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
            <Button size="sm" disabled={page === 0} onClick={() => { setSelected(new Set()); setPage(page - 1); }}>
              Previous
            </Button>
            <span className="text-ink-2">
              Page {page + 1} of {Math.max(1, Math.ceil(q.data.total / limit))}
            </span>
            <Button size="sm" disabled={(page + 1) * limit >= q.data.total} onClick={() => { setSelected(new Set()); setPage(page + 1); }}>
              Next
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
