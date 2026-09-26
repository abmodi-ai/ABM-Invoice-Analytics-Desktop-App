import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Card, ErrorBox, PageHeader, Spinner, Table, Td, Th, Textarea } from "../components/ui";

interface Result {
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  ms?: number;
}

function ResultTable({ r }: { r: Result }) {
  return (
    <div className="space-y-2">
      <p className="text-xs text-ink-3">
        {r.row_count} rows{r.ms !== undefined ? ` · ${r.ms} ms` : ""} (read-only, max 500)
      </p>
      <Table className="max-h-[60vh]">
        <thead>
          <tr>
            {r.columns.map((c) => (
              <Th key={c}>{c}</Th>
            ))}
          </tr>
        </thead>
        <tbody>
          {r.rows.map((row, i) => (
            <tr key={i}>
              {row.map((v, k) => (
                <Td key={k} className="num">
                  {v === null ? "—" : String(v)}
                </Td>
              ))}
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

export default function SearchPage() {
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get("/ai/status") });
  const schema = useQuery({ queryKey: ["search-schema"], queryFn: () => api.get("/search/schema"), staleTime: Infinity });
  const [question, setQuestion] = useState("");
  const [sql, setSql] = useState("SELECT rule_id, tier, COUNT(*) AS flags, SUM(amount_at_risk_cents) AS cents\nFROM v_flags WHERE status = 'OPEN'\nGROUP BY rule_id, tier ORDER BY flags DESC");
  const [jobId, setJobId] = useState<number | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [aiNote, setAiNote] = useState<string | null>(null);

  const ask = useMutation({
    mutationFn: () => api.post("/ai/ask", { question }),
    onSuccess: (r) => {
      setJobId(r.job_id);
      setAiNote(null);
    },
  });
  const run = useMutation({ mutationFn: () => api.post<Result>("/search/sql", { sql }), onSuccess: setResult });
  const job = useQuery({
    queryKey: ["ask-job", jobId],
    queryFn: () => api.get(`/ai/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && ["DONE", "FAILED", "CANCELLED"].includes(q.state.data.status) ? false : 1000),
  });

  useEffect(() => {
    const j = job.data;
    if (!j || j.status !== "DONE") return;
    const res = j.result;
    if (res?.generated?.sql) setSql(res.result?.sql ?? res.generated.sql);
    if (res?.result) setResult(res.result);
    setAiNote(res?.status === "OK" ? res.generated?.explanation : `The generated query was rejected: ${res?.error}`);
    setJobId(null);
  }, [job.data]);

  const aiOn = status.data && status.data.tier !== "OFF";
  return (
    <div className="space-y-4">
      <PageHeader title="Search / Ask" subtitle="Query the curated read-only views. Patients appear only as cluster ids." />
      {aiOn && (
        <Card title={<span className="inline-flex items-center gap-1.5"><Bot className="size-4" aria-hidden /> Ask in plain language</span>}>
          <div className="flex gap-2">
            <input
              className="h-9 flex-1 rounded-md border border-border bg-surface px-2.5 text-sm"
              placeholder="e.g. Which vendors had the most confirmed duplicates this year?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && question.length > 2 && ask.mutate()}
              aria-label="Question"
            />
            <Button variant="primary" onClick={() => ask.mutate()} loading={ask.isPending || !!jobId} disabled={question.length < 3}>
              Ask
            </Button>
          </div>
          {jobId && <Spinner label="Generating SQL on the local model (runs in the background)" />}
          {aiNote && <p className="mt-2 text-sm text-ink-2">{aiNote}</p>}
          <ErrorBox error={ask.error} />
        </Card>
      )}
      <Card title="SQL (SELECT only)" actions={<Badge>validated before it runs</Badge>}>
        <Textarea aria-label="SQL" value={sql} onChange={(e) => setSql(e.target.value)} rows={6} className="font-mono text-xs" />
        <div className="mt-2 flex items-center gap-2">
          <Button variant="primary" onClick={() => run.mutate()} loading={run.isPending}>
            <Play className="size-4" /> Run
          </Button>
          <ErrorBox error={run.error} />
        </div>
        <details className="mt-3 text-xs text-ink-2">
          <summary className="cursor-pointer">Available views</summary>
          <ul className="mt-1 space-y-1 font-mono">
            {Object.entries((schema.data ?? {}) as Record<string, string[]>).map(([v, cols]) => (
              <li key={v}>
                <b>{v}</b>({cols.join(", ")})
              </li>
            ))}
          </ul>
        </details>
      </Card>
      {result && (
        <Card title="Results">
          <ResultTable r={result} />
        </Card>
      )}
    </div>
  );
}
