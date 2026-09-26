import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { api, download } from "../api/client";
import { Badge, Button, Card, ErrorBox, Input, PageHeader, Select, Spinner, Table, Tabs, Td, Th } from "../components/ui";
import { display, money, pct } from "../lib/format";

type Name = "duplicates" | "throughput" | "rule_precision" | "threshold_tuning";

export default function ReportsPage() {
  const [name, setName] = useState<Name>("duplicates");
  const [period, setPeriod] = useState("month");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const params = { period, from, to };
  const q = useQuery({ queryKey: ["report", name, params], queryFn: () => api.get<any[]>(`/reports/${name}`, params) });
  const rows = q.data ?? [];
  const cols = rows.length ? Object.keys(rows[0]).filter((c) => !["current_config", "suggestion"].includes(c)) : [];
  return (
    <div className="space-y-4">
      <PageHeader
        title="Reports"
        actions={
          <>
            <Button size="sm" onClick={() => download(`/reports/${name}`, `verismo-${name}.csv`, { ...params, format: "csv" })}>
              <Download className="size-3.5" /> CSV
            </Button>
            <Button size="sm" onClick={() => download(`/reports/${name}`, `verismo-${name}.pdf`, { ...params, format: "pdf" })}>
              <Download className="size-3.5" /> PDF
            </Button>
          </>
        }
      />
      <Tabs
        tabs={[
          ["duplicates", "Duplicates found & recovered"],
          ["throughput", "Review throughput"],
          ["rule_precision", "Rule precision"],
          ["threshold_tuning", "Threshold tuning"],
        ]}
        value={name}
        onChange={setName}
      />
      {name === "duplicates" && (
        <div className="flex gap-2">
          <Select label="Period" value={period} onChange={(e) => setPeriod(e.target.value)} options={[["week", "Week"], ["month", "Month"], ["quarter", "Quarter"], ["year", "Year"]]} />
          <Input label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          <Input label="To" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
      )}
      {name === "threshold_tuning" && (
        <p className="text-sm text-ink-2">Precision from real review decisions (needs at least 20 reviewed flags per rule). Suggestions are for an admin to apply on the Rules page.</p>
      )}
      <Card>
        {q.isLoading ? (
          <Spinner />
        ) : q.error ? (
          <ErrorBox error={q.error} />
        ) : !rows.length ? (
          <p className="text-sm text-ink-3">No data yet.</p>
        ) : (
          <Table className="max-h-[65vh]">
            <thead>
              <tr>
                {cols.map((c) => (
                  <Th key={c} className={typeof rows[0][c] === "number" ? "text-right" : ""}>
                    {c.replaceAll("_", " ")}
                  </Th>
                ))}
                {name === "threshold_tuning" && <Th>Suggestion</Th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => (
                    <Td key={c} className={typeof r[c] === "number" ? "num text-right" : ""}>
                      {c.endsWith("_cents") ? money(r[c]) : c === "precision" ? pct(r[c]) : display(r[c])}
                    </Td>
                  ))}
                  {name === "threshold_tuning" && (
                    <Td className="text-xs">{r.suggestion ? <><Badge tone="accent">{JSON.stringify(r.suggestion.change)}</Badge> {r.suggestion.reason}</> : "—"}</Td>
                  )}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
