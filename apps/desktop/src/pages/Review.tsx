import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ChevronDown, ChevronRight, Download } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, download } from "../api/client";
import { Badge, Button, Card, Empty, ErrorBox, Input, Kbd, Select, Spinner, Textarea, TierBadge, cn } from "../components/ui";
import { useAuth } from "../lib/auth";
import { display, money, num } from "../lib/format";

const DECISIONS = {
  c: ["CONFIRMED_DUPLICATE", "Confirm duplicate"],
  d: ["NOT_DUPLICATE", "Dismiss"],
  n: ["NEEDS_INFO", "Needs info"],
} as const;

function useFilters() {
  const [sp, setSp] = useSearchParams();
  const f = {
    tier: sp.get("tier") ?? "HARD,PROBABLE",
    status: sp.get("status") ?? "OPEN",
    rule: sp.get("rule") ?? "",
    from: sp.get("from") ?? "",
    to: sp.get("to") ?? "",
    min_cents: sp.get("min_cents") ?? "",
    sort: sp.get("sort") ?? "priority",
    scope: sp.get("scope") ?? "",
  };
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  return [f, set] as const;
}

export default function ReviewPage() {
  const { flagId } = useParams();
  const navigate = useNavigate();
  const [f, setF] = useFilters();
  const list = useQuery({
    queryKey: ["flags", f],
    queryFn: () => api.get("/flags", { ...f, limit: 500 }),
  });
  const items: any[] = useMemo(() => list.data?.items ?? [], [list.data]);
  const idx = items.findIndex((x) => String(x.id) === flagId);
  const current = flagId ? Number(flagId) : items[0]?.id;

  const go = useCallback(
    (delta: number) => {
      if (!items.length) return;
      const base = idx < 0 ? 0 : idx; // no id in the URL means the first item is showing
      const i = Math.min(items.length - 1, Math.max(0, base + delta));
      navigate(`/review/${items[i].id}?${new URLSearchParams(window.location.hash.split("?")[1] ?? "")}`, { replace: true });
    },
    [items, idx, navigate],
  );

  return (
    <div className="flex h-full min-h-0 gap-4">
      <div className="flex w-[380px] shrink-0 flex-col">
        <div className="mb-2 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Review queue</h1>
          <Button size="sm" variant="ghost" onClick={() => download("/flags/export.csv", "invoice-analytics-flags.csv", { tier: f.tier, status: f.status })}>
            <Download className="size-3.5" /> CSV
          </Button>
        </div>
        <div className="mb-2 grid grid-cols-2 gap-2">
          <Select label="Tier" value={f.tier} onChange={(e) => setF("tier", e.target.value)} options={[["HARD,PROBABLE", "Hard + probable"], ["HARD", "Hard"], ["PROBABLE", "Probable"], ["WEAK", "Weak"], ["INFO", "Info"], ["HARD,PROBABLE,WEAK,INFO", "All"]]} />
          <Select label="Status" value={f.status} onChange={(e) => setF("status", e.target.value)} options={[["OPEN", "Open"], ["NEEDS_INFO", "Needs info"], ["CONFIRMED", "Confirmed"], ["DISMISSED", "Dismissed"], ["OPEN,NEEDS_INFO,CONFIRMED,DISMISSED", "Any"]]} />
          <Input label="Rule" placeholder="e.g. CLN-001" value={f.rule} onChange={(e) => setF("rule", e.target.value.toUpperCase())} />
          <Select label="Sort" value={f.sort} onChange={(e) => setF("sort", e.target.value)} options={[["priority", "Priority"], ["amount", "Amount"], ["newest", "Newest"], ["date", "Invoice date"]]} />
          <Input label="Invoice from" type="date" value={f.from} onChange={(e) => setF("from", e.target.value)} />
          <Input label="Invoice to" type="date" value={f.to} onChange={(e) => setF("to", e.target.value)} />
          <div className="col-span-2">
            <Select
              label="Compared with"
              value={f.scope}
              onChange={(e) => setF("scope", e.target.value)}
              options={[["", "Any"], ["ACROSS", "Other invoices (billed before)"], ["WITHIN", "Lines on the same invoice"], ["SINGLE", "Limits on one record"]]}
            />
          </div>
        </div>
        <div className="mb-1 text-xs text-ink-3">
          {num(list.data?.total)} flags · <Kbd>J</Kbd>/<Kbd>K</Kbd> move · <Kbd>C</Kbd> <Kbd>D</Kbd> <Kbd>N</Kbd> decide
        </div>
        <ul className="min-h-0 flex-1 overflow-auto rounded-md border border-border" role="listbox" aria-label="Flags">
          {list.isLoading && <Spinner />}
          {list.error && <ErrorBox error={list.error} />}
          {!list.isLoading && items.length === 0 && <li className="p-6 text-center text-sm text-ink-3">Nothing to review with these filters.</li>}
          {items.map((it) => (
            <li key={it.id} role="option" aria-selected={it.id === current}>
              <Link
                to={`/review/${it.id}?${new URLSearchParams(Object.entries(f).filter(([, v]) => v))}`}
                className={cn("block border-b border-border px-3 py-2 hover:bg-surface-2", it.id === current && "bg-accent/10")}
              >
                <div className="flex items-center justify-between gap-2">
                  <TierBadge tier={it.tier} />
                  <span className="num text-sm font-medium">{money(it.amount_at_risk_cents)}</span>
                </div>
                <div className="mt-1 truncate text-sm">
                  <span className="font-mono text-xs text-ink-2">{it.rule_id}</span> {it.party} · {it.invoice_number}
                </div>
                <div className="truncate text-xs text-ink-3">{it.summary}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {it.scope === "ACROSS" && <Badge tone="accent">vs. another invoice</Badge>}
                  {it.scope === "WITHIN" && <Badge>same invoice</Badge>}
                  {it.status !== "OPEN" && <Badge>{it.status.toLowerCase()}</Badge>}
                  {it.sibling_count > 0 && (
                    <Badge>
                      +{it.sibling_count} other rule{it.sibling_count > 1 ? "s" : ""} on this pair
                    </Badge>
                  )}
                  {it.ai_verdict && (
                    <Badge tone="accent">
                      AI: {it.ai_verdict.toLowerCase().replace("_", " ")}
                    </Badge>
                  )}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <div className="min-w-0 flex-1 overflow-auto">{current ? <FlagDetail id={current} onNext={() => go(1)} onPrev={() => go(-1)} /> : <Empty>Select a flag.</Empty>}</div>
    </div>
  );
}

function FlagDetail({ id, onNext, onPrev }: { id: number; onNext: () => void; onPrev: () => void }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get("/ai/status"), staleTime: 30000 });
  const aiOn = aiStatus.data && aiStatus.data.tier !== "OFF";
  // While AI is on, poll until the background explanation for this flag lands (never blocks the UI).
  const q = useQuery({ queryKey: ["flag", id], queryFn: () => api.get(`/flags/${id}`), refetchInterval: (qq) => (aiOn && qq.state.data && !qq.state.data.ai.explain ? 5000 : false) });
  const reasons = useQuery({ queryKey: ["reasons"], queryFn: () => api.get("/flags/reasons"), staleTime: Infinity });
  const [pending, setPending] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [recovered, setRecovered] = useState("");
  const reasonRef = useRef<HTMLSelectElement>(null);

  const decide = useMutation({
    mutationFn: (decision: string) =>
      api.post(`/flags/${id}/review`, {
        decision,
        reason_code: reason || null,
        note: note || null,
        recovered_cents: recovered ? Math.round(parseFloat(recovered) * 100) : null,
      }),
    onSuccess: () => {
      setPending(null);
      setNote("");
      setRecovered("");
      qc.invalidateQueries({ queryKey: ["flags"] });
      qc.invalidateQueries({ queryKey: ["flag", id] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onNext();
    },
  });

  useEffect(() => {
    setPending(null);
    setReason("");
  }, [id]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || (tag === "SELECT" && e.key !== "Enter")) {
        if (e.key === "Escape") setPending(null);
        return;
      }
      const k = e.key.toLowerCase();
      if (k === "j") onNext();
      else if (k === "k") onPrev();
      else if (k in DECISIONS && can("REVIEWER")) {
        const dec = DECISIONS[k as keyof typeof DECISIONS][0];
        setPending(dec);
        setReason(reasons.data?.[dec]?.[0] ?? "");
        setTimeout(() => reasonRef.current?.focus(), 0);
      } else if (e.key === "Enter" && pending) {
        e.preventDefault();
        decide.mutate(pending);
      } else if (e.key === "Escape") setPending(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onNext, onPrev, pending, decide, reasons.data, can]);

  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const fl = q.data;
  const ev = fl.evidence;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <TierBadge tier={fl.tier} />
            <span className="font-mono text-sm text-ink-2">{fl.rule_id}</span>
            <span className="text-sm font-medium">{ev.title}</span>
            {fl.base_tier !== fl.tier && <Badge>was {fl.base_tier.toLowerCase()}</Badge>}
            {fl.status !== "OPEN" && <Badge tone="accent">{fl.status.toLowerCase()}</Badge>}
            {fl.suppressed_by && <Badge tone="warn">suppressed by {fl.suppressed_by}</Badge>}
          </div>
          <p className="mt-1 max-w-3xl text-sm">{ev.summary}</p>
          <p className="num mt-1 text-xs text-ink-3">
            Amount at risk {money(fl.amount_at_risk_cents)} · score {fl.score.toFixed(2)} · rule v{fl.rule_version} · engine {fl.engine_version}
            {fl.refdata_version ? ` · ${fl.refdata_version}` : ""}
          </p>
        </div>
        {can("REVIEWER") && (
          <div className="flex gap-2">
            {Object.entries(DECISIONS).map(([k, [dec, label]]) => (
              <Button
                key={k}
                variant={pending === dec ? "primary" : "secondary"}
                onClick={() => {
                  setPending(dec);
                  setReason(reasons.data?.[dec]?.[0] ?? "");
                }}
              >
                {label} <Kbd>{k.toUpperCase()}</Kbd>
              </Button>
            ))}
          </div>
        )}
      </div>
      {pending && (
        <Card>
          <div className="grid gap-3 md:grid-cols-4">
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Reason
              <select ref={reasonRef} className="h-9 rounded-md border border-border bg-surface px-2 text-sm text-ink" value={reason} onChange={(e) => setReason(e.target.value)}>
                {(reasons.data?.[pending] ?? []).map((r: string) => (
                  <option key={r} value={r}>
                    {r.replaceAll("_", " ").toLowerCase()}
                  </option>
                ))}
              </select>
            </label>
            {pending === "CONFIRMED_DUPLICATE" && <Input label="Recovered amount ($)" inputMode="decimal" value={recovered} onChange={(e) => setRecovered(e.target.value)} placeholder={(fl.amount_at_risk_cents / 100).toFixed(2)} />}
            <div className={pending === "CONFIRMED_DUPLICATE" ? "md:col-span-2" : "md:col-span-3"}>
              <Textarea label="Note (optional)" rows={1} value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2">
            {fl.other_flags_on_subject.length > 0 && (
              <span className="text-xs text-ink-2">Also applies to other open flags on the same invoice pair.</span>
            )}
            <Button variant="primary" loading={decide.isPending} onClick={() => decide.mutate(pending)}>
              Submit <Kbd>Enter</Kbd>
            </Button>
            <Button variant="ghost" onClick={() => setPending(null)}>
              Cancel <Kbd>Esc</Kbd>
            </Button>
            <ErrorBox error={decide.error} />
          </div>
        </Card>
      )}
      <SideBySide fl={fl} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Evidence ev={ev} />
        <AiPanel fl={fl} />
      </div>
      {fl.reviews.length > 0 && (
        <Card title="Decision history">
          <ul className="space-y-1 text-sm">
            {fl.reviews.map((r: any) => (
              <li key={r.id}>
                <span className="text-ink-3">{new Date(r.decided_at).toLocaleString()}</span> · {r.reviewer}: <b>{r.decision.replaceAll("_", " ").toLowerCase()}</b>
                {r.reason_code && ` (${r.reason_code.replaceAll("_", " ").toLowerCase()})`} {r.note && `— ${r.note}`}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

const HEADER_ROWS: Array<[string, string, (i: any) => unknown]> = [
  ["party", "Party", (i) => i.party],
  ["invoice_number", "Invoice number", (i) => i.invoice_number_raw],
  ["invoice_date", "Invoice date", (i) => i.invoice_date ?? i.invoice_date_raw],
  ["total", "Total", (i) => money(i.total_cents)],
  ["po_number", "PO number", (i) => i.po_number],
  ["claim_frequency_code", "Frequency code", (i) => i.claim_frequency_code],
  ["status", "Status", (i) => i.status],
  ["document_sha256", "Source file", (i) => `${i.source_path ?? "—"} (${i.ingest_method ?? "?"})`],
];

function LinesTable({ inv, lineIds, tone, showAll }: { inv: any; lineIds: Set<number>; tone: (f: string) => string; showAll: boolean }) {
  const hasVisits = inv.lines.some((l: any) => l.visit_label);
  const rows = showAll ? inv.lines : inv.lines.filter((l: any) => lineIds.has(l.id));
  return (
    <div className="mt-2 overflow-auto">
      <table className="w-full text-xs whitespace-nowrap [&_td]:pr-3 [&_th]:pr-3">
        <thead>
          <tr className="text-left text-ink-2">
            <th className="py-1">#</th>
            <th>Patient</th>
            <th>DOS</th>
            {hasVisits && <th>Visit</th>}
            <th>Code</th>
            <th>Mods</th>
            <th className="text-right">Units</th>
            <th className="text-right">Charge</th>
            <th>NPI</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((li: any) => {
            const hit = lineIds.has(li.id);
            return (
              <tr key={li.id} className={cn("border-t border-border", hit && showAll && "outline outline-1 outline-accent")}>
                <td className="py-1">{li.line_no}</td>
                <td className={cn(hit && tone("patient_cluster"))}>{li.patient_name ?? "—"}</td>
                <td className={cn(hit && tone("dos"))}>{li.dos_from}</td>
                {hasVisits && <td className={cn(hit && tone("visit"))}>{li.visit_label ?? "—"}</td>}
                <td className={cn("font-mono", hit && tone("code"))}>{li.code ?? "—"}</td>
                <td className={cn(hit && tone("modifiers"))}>{li.modifiers.join(",") || "—"}</td>
                <td className={cn("num text-right", hit && tone("units"))}>{li.units}</td>
                <td className={cn("num text-right", hit && tone("charge"))}>{money(li.charge_cents)}</td>
                <td className={cn("font-mono", hit && tone("rendering_npi"))}>{li.rendering_npi ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function InvoiceHeader({ inv, tone }: { inv: any; tone: (f: string) => string }) {
  return (
    <dl className="grid grid-cols-[120px_1fr] gap-x-2 text-sm">
      {HEADER_ROWS.map(([field, name, get]) => (
        <div key={field} className="contents">
          <dt className="py-0.5 text-ink-2">{name}</dt>
          <dd className={cn("rounded px-1 py-0.5", tone(field))}>{display(get(inv))}</dd>
        </div>
      ))}
    </dl>
  );
}

function SideBySide({ fl }: { fl: any }) {
  const a = fl.subject_invoice;
  const others: any[] = fl.counterpart_invoices;
  const [k, setK] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const b = others[k];
  const matched = new Set(fl.evidence.matched_fields.map((m: any) => m.field));
  const differing = new Set(fl.evidence.differing_fields.map((m: any) => m.field));
  const subjectLineIds = new Set<number>(fl.subject_type === "LINE" ? [fl.subject_id] : []);
  const cpLineIds = new Set<number>(fl.subject_type === "LINE" ? fl.counterpart_ids : []);
  const tone = (field: string) => (matched.has(field) ? "bg-match text-match-ink" : differing.has(field) ? "bg-diff text-diff-ink" : "");
  const lineFlag = fl.subject_type === "LINE";
  // Some rules compare two lines of the same invoice (a charge billed twice, two visits on one date).
  const sameInvoice = lineFlag && b && b.id === a.id;
  const toggle = lineFlag && (
    <Button size="sm" variant="ghost" onClick={() => setShowAll(!showAll)}>
      {showAll ? "Show flagged lines only" : "Show all lines"}
    </Button>
  );
  const legend = (
    <div className="mb-2 flex gap-3 text-xs text-ink-2">
      <span className="rounded bg-match px-1.5 text-match-ink">matched</span>
      <span className="rounded bg-diff px-1.5 text-diff-ink">differs</span>
    </div>
  );
  if (sameInvoice) {
    const both = new Set<number>([...subjectLineIds, ...cpLineIds]);
    return (
      <Card title="Both lines are on the same invoice" actions={toggle}>
        {legend}
        <div className="mb-1 flex items-center justify-between text-xs font-medium text-ink-2">
          Invoice
          <Link className="text-accent-ink hover:underline" to={`/invoices/${a.id}`}>
            open invoice
          </Link>
        </div>
        <InvoiceHeader inv={a} tone={() => ""} />
        <p className="mt-3 text-xs text-ink-2">
          {showAll ? "All lines; the two flagged lines are outlined." : "The two lines this flag compares:"}
        </p>
        <LinesTable inv={a} lineIds={both} tone={tone} showAll={showAll} />
      </Card>
    );
  }
  return (
    <Card
      title="Side by side"
      actions={
        <div className="flex items-center gap-2">
          {others.length > 1 && (
            <Select aria-label="Counterpart" value={String(k)} onChange={(e) => setK(Number(e.target.value))} options={others.map((o, i) => [String(i), `Counterpart ${i + 1}: ${o.invoice_number_raw}`])} />
          )}
          {toggle}
        </div>
      }
    >
      {legend}
      <div className="grid gap-4 lg:grid-cols-2">
        {[
          ["This record", a, subjectLineIds],
          ["Earlier record", b, cpLineIds],
        ].map(([label, inv, lineIds]: any) => (
          <div key={label} className="min-w-0">
            <div className="mb-1 flex items-center justify-between text-xs font-medium text-ink-2">
              {label}
              {inv && (
                <Link className="text-accent-ink hover:underline" to={`/invoices/${inv.id}`}>
                  open invoice
                </Link>
              )}
            </div>
            {!inv ? (
              <p className="text-sm text-ink-3">No counterpart (limit breached on this record alone).</p>
            ) : (
              <>
                <InvoiceHeader inv={inv} tone={tone} />
                <LinesTable inv={inv} lineIds={lineIds} tone={tone} showAll={showAll || !lineFlag} />
              </>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

const CENTS_FIELDS = new Set(["total", "charge"]);
const fmtEv = (field: string, v: unknown) => (CENTS_FIELDS.has(field) && typeof v === "number" ? money(v) : display(v));

function Evidence({ ev }: { ev: any }) {
  return (
    <Card title="Evidence">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-ink-2">
            <th className="py-1">Field</th>
            <th>This</th>
            <th>Earlier</th>
            <th>Method</th>
          </tr>
        </thead>
        <tbody>
          {ev.matched_fields.map((m: any) => (
            <tr key={`m-${m.field}`} className="border-t border-border">
              <td className="py-1">{m.field.replaceAll("_", " ")}</td>
              <td className="bg-match px-1 text-match-ink">{fmtEv(m.field, m.a)}</td>
              <td className="bg-match px-1 text-match-ink">{fmtEv(m.field, m.b)}</td>
              <td className="text-xs text-ink-2">
                {m.method}
                {m.value !== undefined && ` (${display(m.value)})`}
              </td>
            </tr>
          ))}
          {ev.differing_fields.map((m: any) => (
            <tr key={`d-${m.field}`} className="border-t border-border">
              <td className="py-1">{m.field.replaceAll("_", " ")}</td>
              <td className="bg-diff px-1 text-diff-ink">{fmtEv(m.field, m.a)}</td>
              <td className="bg-diff px-1 text-diff-ink">{fmtEv(m.field, m.b)}</td>
              <td className="text-xs text-ink-3">differs</td>
            </tr>
          ))}
        </tbody>
      </table>
      {ev.refdata.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-medium text-ink-2">Reference data</div>
          {ev.refdata.map((r: any, i: number) => (
            <div key={i} className="font-mono text-xs">
              {r.dataset} {r.version ?? ""}: {JSON.stringify(r.row)}
            </div>
          ))}
        </div>
      )}
      <div className="mt-3">
        <div className="text-xs font-medium text-ink-2">Suppressions considered</div>
        <ul className="list-inside list-disc text-xs text-ink-2">
          {ev.suppressions_considered.map((s: string) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

function Step({ n, done, title, children }: { n: number; done?: boolean; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span
        className={cn(
          "mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-medium",
          done ? "bg-match text-match-ink" : "bg-surface-2 text-ink-2",
        )}
        aria-label={done ? "done" : `step ${n}`}
      >
        {done ? "✓" : n}
      </span>
      <div>
        <div className="font-medium">
          {title}
          {done && <span className="ml-1 text-xs font-normal text-good-ink">done</span>}
        </div>
        <div className="text-ink-2">{children}</div>
      </div>
    </li>
  );
}

function AiSetupGuide({ status }: { status: any }) {
  const { can } = useAuth();
  const setup = status?.setup ?? {};
  const models: string[] = setup.models_installed ?? [];
  const tier = (status?.hardware?.recommended_tier ?? "LITE").toLowerCase();
  const onButBlocked = status && status.tier !== "OFF" && setup.ready === false;
  return (
    <div className="space-y-3 text-sm">
      {onButBlocked && (
        <p className="rounded border border-warning/40 bg-diff p-2 text-diff-ink" role="status">
          AI is set to <b>{String(status.tier).toLowerCase()}</b>, but it can&apos;t run on this computer yet:{" "}
          {(setup.missing ?? []).join("; ")}. No AI work is queued until the steps below are done.
        </p>
      )}
      <p className="text-ink-2">
        {onButBlocked ? "AI is not running." : "AI is switched off."} Detection and review work fully without it. When it is on, this panel shows a plain-English
        explanation of the flag and a suggested verdict with its reasoning. It runs only on this computer; nothing is sent
        anywhere, and you still make every decision.
      </p>
      <div className="text-xs font-medium text-ink-2">To switch it on</div>
      <ol className="space-y-3">
        <Step n={1} done={setup.runner_found} title="Install the AI runner (llama.cpp)">
          Free and open source. On Windows, download <span className="font-mono">llama-server.exe</span> from the llama.cpp
          releases page and add its folder to PATH; on a Mac run <span className="font-mono">brew install llama.cpp</span>. Then
          restart this app.
        </Step>
        <Step n={2} done={models.length > 0} title="Import a model package">
          {models.length > 0 ? (
            <>Installed: {models.join(", ")}.</>
          ) : (
            <>
              Get <span className="font-mono">ia-models-lite.zip</span> (about 2.5 GB) from your administrator, then{" "}
              <b>Settings → Local AI → Import model package…</b> Its checksums and licence are verified before it is installed.
            </>
          )}
        </Step>
        <Step n={3} title="Choose a tier and click Apply">
          In <b>Settings → Local AI</b>, set <b>Tier</b> to <b>{tier}</b> (recommended for this computer) and click <b>Apply</b>.
          The model starts on the first AI task and stops after 10 idle minutes.
        </Step>
      </ol>
      {can("ADMIN") ? (
        <Link className="inline-block text-accent-ink hover:underline" to="/settings">
          Open Settings → Local AI
        </Link>
      ) : (
        <p className="text-xs text-ink-3">Steps 2 and 3 need an administrator.</p>
      )}
    </div>
  );
}

function AiPanel({ fl }: { fl: any }) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get("/ai/status"), staleTime: 30000 });
  const [open, setOpen] = useState(false);
  const investigate = useMutation({
    mutationFn: () => api.post(`/ai/triage/${fl.id}`),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["flag", fl.id] }), 3000),
  });
  const explainNow = useMutation({ mutationFn: () => api.post(`/ai/explain/${fl.id}`) });
  const trace = useQuery({
    queryKey: ["suggestion", fl.ai.triage?.id],
    queryFn: () => api.get(`/ai/suggestions/${fl.ai.triage.id}`),
    enabled: open && !!fl.ai.triage,
  });
  // Treat "still loading" as off (never show AI UI unasked), and a tier that is on but cannot run
  // (runner or model missing) as off too: its jobs could only fail.
  const off = !status.data || status.data.tier === "OFF" || status.data.setup?.ready === false;
  const ex = fl.ai.explain;
  const tr = fl.ai.triage;
  const verdict = tr?.output;
  const v = useMemo(() => (verdict?.verdict ?? "").replace("_", " ").toLowerCase(), [verdict]);
  return (
    <Card
      title={
        <span className="inline-flex items-center gap-1.5">
          <Bot className="size-4" aria-hidden /> AI assistance <span className="font-normal text-ink-3">(advisory — you decide)</span>
        </span>
      }
      actions={
        !off && (
          <Button size="sm" onClick={() => investigate.mutate()} loading={investigate.isPending}>
            Investigate
          </Button>
        )
      }
    >
      {off ? (
        <AiSetupGuide status={status.data} />
      ) : (
        <div className="space-y-3 text-sm">
          <div>
            <div className="text-xs font-medium text-ink-2">Explanation</div>
            {!ex ? (
              <p className="text-ink-3">
                Queued… <button className="text-accent-ink underline" onClick={() => explainNow.mutate()}>prioritise</button>
              </p>
            ) : ex.status === "OK" ? (
              <>
                <p>{ex.output.explanation}</p>
                {ex.output.key_differences?.length > 0 && (
                  <ul className="mt-1 list-inside list-disc text-ink-2">
                    {ex.output.key_differences.map((k: string) => (
                      <li key={k}>{k}</li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="text-ink-3">The explanation failed its fact check and was discarded.</p>
            )}
          </div>
          <div>
            <div className="text-xs font-medium text-ink-2">Triage verdict</div>
            {!tr ? (
              <p className="text-ink-3">{investigate.isSuccess ? "Investigating in the background…" : "Not triaged yet."}</p>
            ) : (
              <>
                <p>
                  <Badge tone={tr.status === "OK" ? "accent" : "warn"}>{v || "uncertain"}</Badge>{" "}
                  {tr.status === "OK" && verdict?.confidence !== undefined && <span className="text-ink-3">confidence {(verdict.confidence * 100).toFixed(0)}%</span>}
                  {tr.status !== "OK" && <span className="text-ink-3"> (not shown as a finding: {tr.status.toLowerCase()})</span>}
                </p>
                {tr.status === "OK" && <p className="mt-1">{verdict?.rationale}</p>}
                <button className="mt-1 inline-flex items-center text-xs text-accent-ink" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
                  {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />} Trace
                </button>
                {open && (trace.isLoading ? <Spinner /> : <Trace s={trace.data} />)}
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function Trace({ s }: { s: any }) {
  if (!s?.trace) return null;
  return (
    <ol className="mt-2 space-y-2 text-xs">
      <li className="text-ink-3">
        model {s.model_id} · prompt {s.prompt_version} · {s.latency_ms} ms
      </li>
      {s.trace
        .filter((t: any) => t.type === "tool")
        .map((t: any) => (
          <li key={t.id} className="rounded border border-border p-2">
            <div className="font-mono">
              {t.id}: {t.name}({typeof t.arguments === "string" ? t.arguments : JSON.stringify(t.arguments)})
            </div>
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-ink-2">{JSON.stringify(t.result, null, 1)}</pre>
          </li>
        ))}
      {s.output?.cited_evidence?.length > 0 && (
        <li>
          <div className="font-medium">Citations</div>
          {s.output.cited_evidence.map((c: any, i: number) => (
            <div key={i} className="font-mono">
              {c.tool_call_id} · {c.field} = {c.value}
            </div>
          ))}
        </li>
      )}
      {s.output?.validation_failures && <li className="text-critical">Validation: {s.output.validation_failures.join("; ")}</li>}
    </ol>
  );
}
