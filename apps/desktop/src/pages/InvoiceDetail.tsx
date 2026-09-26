import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Card, ErrorBox, PageHeader, Spinner, Table, Td, Th, TierBadge } from "../components/ui";
import { dateTime, display, money } from "../lib/format";

export default function InvoiceDetailPage() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["invoice", id], queryFn: () => api.get(`/invoices/${id}`) });
  const [preview, setPreview] = useState<string | null>(null);
  const isDoc = q.data && ["PDF_TEXT", "OCR", "LLM"].includes(q.data.ingest_method);
  useEffect(() => {
    if (!isDoc) return;
    let url: string | null = null;
    api
      .blob(`/documents/${q.data.document_id}/content`)
      .then((b) => {
        url = URL.createObjectURL(b);
        setPreview(url);
      })
      .catch(() => undefined);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [isDoc, q.data]);
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const inv = q.data;
  return (
    <div className="space-y-4">
      <PageHeader title={`Invoice ${inv.invoice_number_raw ?? inv.id}`} subtitle={`${inv.party} · ${inv.direction} · ${inv.status.toLowerCase()}`} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Header" className="lg:col-span-1">
          <dl className="grid grid-cols-[130px_1fr] gap-y-1 text-sm">
            {[
              ["Invoice date", inv.invoice_date ?? `${inv.invoice_date_raw} (unconfirmed)`],
              ["Due date", inv.due_date],
              ["Total", money(inv.total_cents)],
              ["PO number", inv.po_number],
              ["Claim type", inv.claim_type],
              ["Frequency code", inv.claim_frequency_code],
              ["Original reference", inv.original_invoice_ref],
              ["Source", `${inv.source_path ?? "—"} (${inv.ingest_method ?? "?"})`],
              ["Needs attention", inv.needs_attention],
            ].map(([k, v]) => (
              <div key={k as string} className="contents">
                <dt className="text-ink-2">{k}</dt>
                <dd>{display(v)}</dd>
              </div>
            ))}
          </dl>
          {inv.links.length > 0 && (
            <div className="mt-3 text-sm">
              {inv.links.map((l: any) => (
                <div key={l.id}>
                  <Badge tone="accent">{l.link_type.toLowerCase()}</Badge>{" "}
                  <Link className="text-accent-ink hover:underline" to={`/invoices/${l.from_invoice_id === inv.id ? l.to_invoice_id : l.from_invoice_id}`}>
                    invoice #{l.from_invoice_id === inv.id ? l.to_invoice_id : l.from_invoice_id}
                  </Link>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Flags" className="lg:col-span-2">
          {inv.flags.length === 0 ? (
            <p className="text-sm text-ink-3">No flags on this invoice.</p>
          ) : (
            <ul className="space-y-2">
              {inv.flags.map((f: any) => (
                <li key={f.id} className="flex items-start gap-2 text-sm">
                  <TierBadge tier={f.tier} />
                  <Link to={`/review/${f.id}?status=OPEN,NEEDS_INFO,CONFIRMED,DISMISSED&tier=HARD,PROBABLE,WEAK,INFO`} className="hover:underline">
                    <span className="font-mono text-xs text-ink-2">{f.rule_id}</span> {f.summary}
                  </Link>
                  {f.status !== "OPEN" && <Badge>{f.status.toLowerCase()}</Badge>}
                  {f.suppressed_by && <Badge tone="warn">{f.suppressed_by}</Badge>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Card title="Lines">
        <Table>
          <thead>
            <tr>
              <Th>#</Th>
              <Th>Patient</Th>
              <Th>DOS</Th>
              <Th>Code</Th>
              <Th>Modifiers</Th>
              <Th className="text-right">Units</Th>
              <Th className="text-right">Charge</Th>
              <Th className="text-right">Paid</Th>
              <Th>Rendering NPI</Th>
              <Th>Description</Th>
            </tr>
          </thead>
          <tbody>
            {inv.lines.map((l: any) => (
              <tr key={l.id}>
                <Td>{l.line_no}</Td>
                <Td>
                  {l.patient_name ?? "—"} {l.patient_cluster && <span className="text-xs text-ink-3">P-{l.patient_cluster}</span>}
                </Td>
                <Td className="num">{l.dos_from}</Td>
                <Td className="font-mono">{l.code ?? "—"}</Td>
                <Td>{l.modifiers.join(", ") || "—"}</Td>
                <Td className="num text-right">{l.units}</Td>
                <Td className="num text-right">{money(l.charge_cents)}</Td>
                <Td className="num text-right">{money(l.paid_cents)}</Td>
                <Td className="font-mono">{l.rendering_npi ?? "—"}</Td>
                <Td className="text-xs text-ink-2">{l.description_raw}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
      {preview && (
        <Card title="Source document">
          <iframe title="Source document" src={preview} className="h-[70vh] w-full rounded border border-border" />
        </Card>
      )}
      <Card title="History">
        {inv.history.length === 0 ? (
          <p className="text-sm text-ink-3">No recorded changes.</p>
        ) : (
          <ul className="text-sm">
            {inv.history.map((h: any, i: number) => (
              <li key={i}>
                {dateTime(h.ts)} · {h.action.toLowerCase()}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
