import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ColumnChart } from "../components/ColumnChart";
import { Card, ErrorBox, PageHeader, Spinner, Table, Td, Th, TierBadge } from "../components/ui";
import { compactMoney, money, num, pct } from "../lib/format";

function StatTile({ label, value, sub, to }: { label: React.ReactNode; value: string; sub?: string; to?: string }) {
  const body = (
    <div className="h-full rounded-lg border border-border bg-surface p-4 hover:bg-surface-2">
      <div className="text-xs text-ink-2">{label}</div>
      <div className="num mt-1 text-2xl font-semibold text-ink">{value}</div>
      {sub && <div className="num mt-0.5 text-xs text-ink-3">{sub}</div>}
    </div>
  );
  return to ? (
    <Link className="block h-full" to={to}>
      {body}
    </Link>
  ) : (
    body
  );
}

export default function DashboardPage() {
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get("/dashboard"), refetchInterval: 15000 });
  const byMonth = useQuery({ queryKey: ["dup-by-month"], queryFn: () => api.get<any[]>("/reports/duplicates", { period: "month" }) });

  const series = useMemo(() => {
    const m = new Map<string, { flags: number; confirmed: number }>();
    for (const r of byMonth.data ?? []) {
      if (!r.period) continue;
      const v = m.get(r.period) ?? { flags: 0, confirmed: 0 };
      v.flags += r.flagged_invoices;
      v.confirmed += r.confirmed_invoices;
      m.set(r.period, v);
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(-24).map(([label, v]) => ({ label, value: v.flags, detail: `${v.confirmed} confirmed` }));
  }, [byMonth.data]);

  if (dash.isLoading) return <Spinner />;
  if (dash.error) return <ErrorBox error={dash.error} />;
  const d = dash.data;
  const t = d.open_by_tier;
  return (
    <div className="space-y-5">
      <PageHeader title="Dashboard" subtitle={`${num(d.totals.invoices)} invoices · ${num(d.totals.lines)} lines`} />
      {d.stale_reference_data?.length > 0 && (
        <div role="alert" className="flex items-center gap-2 rounded-md border border-warning/60 bg-warning/10 p-3 text-sm">
          <AlertTriangle className="size-4 text-[#7a4d00]" aria-hidden />
          Reference data past its effective period: {d.stale_reference_data.join(", ")}. Import a newer bundle in Reference data.
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label={<TierBadge tier="HARD" />} value={num(t.HARD.count)} sub={`invoices · ${money(t.HARD.amount_cents)} at risk`} to="/review?tier=HARD" />
        <StatTile label={<TierBadge tier="PROBABLE" />} value={num(t.PROBABLE.count)} sub={`invoices · ${money(t.PROBABLE.amount_cents)} at risk`} to="/review?tier=PROBABLE" />
        <StatTile label="Amount at risk (hard + probable)" value={compactMoney(d.amount_at_risk_cents)} sub={`${num(t.WEAK.count)} more invoices with weak flags only`} />
        <StatTile label="Amount recovered" value={compactMoney(d.amount_recovered_cents)} sub="from confirmed reviews" to="/reports" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          {byMonth.isLoading ? <Spinner /> : series.length ? <ColumnChart title="Invoices with hard or probable flags, by invoice month" data={series} format={(v) => num(v)} /> : <p className="text-sm text-ink-3">No flags yet.</p>}
        </Card>
        <Card title="Needs attention">
          <ul className="space-y-2 text-sm">
            <li className="flex justify-between">
              <Link className="text-accent-ink hover:underline" to="/ingest">
                Documents to review
              </Link>
              <span className="num">{num(d.totals.needs_review_documents)}</span>
            </li>
            <li className="flex justify-between">
              <Link className="text-accent-ink hover:underline" to="/identity">
                Identity suggestions
              </Link>
              <span className="num">{num(d.totals.identity_reviews)}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-ink-2">AI</span>
              <span>{d.ai.tier === "OFF" ? "off" : `${d.ai.queue.queued} queued`}</span>
            </li>
            <li className="flex justify-between">
              <span className="text-ink-2">Last detection run</span>
              <span className="text-ink-3">{d.last_run ? `${d.last_run.mode.toLowerCase()} · ${d.last_run.status.toLowerCase()}` : "—"}</span>
            </li>
          </ul>
        </Card>
      </div>
      <Card title="Top vendors by duplicate rate">
        {d.top_vendors_by_duplicate_rate.length === 0 ? (
          <p className="text-sm text-ink-3">No vendors with 5 or more invoices yet.</p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Vendor</Th>
                <Th className="text-right">Invoices</Th>
                <Th className="text-right">Flagged (hard/probable)</Th>
                <Th className="text-right">Rate</Th>
              </tr>
            </thead>
            <tbody>
              {d.top_vendors_by_duplicate_rate.map((v: any) => (
                <tr key={v.party_id}>
                  <Td>
                    <Link className="hover:underline" to={`/invoices?party_id=${v.party_id}`}>
                      {v.display_name}
                    </Link>
                  </Td>
                  <Td className="num text-right">{num(v.invoices)}</Td>
                  <Td className="num text-right">{num(v.flagged)}</Td>
                  <Td className="num text-right">{pct(v.duplicate_rate)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
