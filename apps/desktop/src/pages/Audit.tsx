import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { Button, Input, PageHeader, Spinner, Table, Td, Th } from "../components/ui";
import { dateTime, num } from "../lib/format";

export default function AuditPage() {
  const [f, setF] = useState({ action: "", entity_type: "", from: "", to: "" });
  const [page, setPage] = useState(0);
  const q = useQuery({ queryKey: ["audit", f, page], queryFn: () => api.get("/audit", { ...f, limit: 200, offset: page * 200 }) });
  const verify = useMutation({ mutationFn: () => api.post("/audit/verify") });
  return (
    <div className="space-y-3">
      <PageHeader
        title="Audit log"
        subtitle="Append-only and hash-chained. Covers sign-ins, PHI views, decisions, merges, settings, imports and exports."
        actions={
          <Button variant="primary" onClick={() => verify.mutate()} loading={verify.isPending}>
            Check integrity
          </Button>
        }
      />
      {verify.data && (
        <div role="status" className="flex items-center gap-2 text-sm">
          {verify.data.ok ? (
            <>
              <CheckCircle2 className="size-4 text-good" aria-hidden /> Chain intact: {num(verify.data.checked)} entries verified.
            </>
          ) : (
            <>
              <XCircle className="size-4 text-critical" aria-hidden /> Integrity failure at entry {verify.data.first_bad_id}: {verify.data.reason}
            </>
          )}
        </div>
      )}
      <div className="grid grid-cols-4 gap-2">
        <Input label="Action" placeholder="e.g. REVIEW_DECISION" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value.toUpperCase() })} />
        <Input label="Entity type" value={f.entity_type} onChange={(e) => setF({ ...f, entity_type: e.target.value })} />
        <Input label="From" type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} />
        <Input label="To" type="date" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} />
      </div>
      {q.isLoading ? (
        <Spinner />
      ) : (
        <>
          <Table className="max-h-[65vh]">
            <thead>
              <tr>
                <Th>#</Th>
                <Th>Time</Th>
                <Th>User</Th>
                <Th>Action</Th>
                <Th>Entity</Th>
                <Th>Details</Th>
              </tr>
            </thead>
            <tbody>
              {q.data?.items.map((r: any) => (
                <tr key={r.id}>
                  <Td className="num">{r.id}</Td>
                  <Td className="text-xs whitespace-nowrap">{dateTime(r.ts)}</Td>
                  <Td>{r.username ?? "system"}</Td>
                  <Td className="font-mono text-xs">{r.action}</Td>
                  <Td className="text-xs">
                    {r.entity_type} {r.entity_id}
                  </Td>
                  <Td className="max-w-lg truncate font-mono text-xs text-ink-2" title={JSON.stringify(r.after)}>
                    {r.after ? JSON.stringify(r.after) : ""}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <div className="flex items-center gap-2 text-sm">
            <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Newer
            </Button>
            <Button size="sm" disabled={(page + 1) * 200 >= (q.data?.total ?? 0)} onClick={() => setPage(page + 1)}>
              Older
            </Button>
            <span className="text-ink-3">{num(q.data?.total)} entries</span>
          </div>
        </>
      )}
    </div>
  );
}
