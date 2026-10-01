import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileUp, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Button, Card, Empty, ErrorBox, Input, Modal, PageHeader, Select, Spinner, Table, Td, Th, cn } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime, money, num } from "../lib/format";

const STATUS_TONE: Record<string, "neutral" | "good" | "accent" | "warn" | "bad"> = {
  QUEUED: "neutral",
  RUNNING: "accent",
  DONE: "good",
  NEEDS_MAPPING: "warn",
  NEEDS_REVIEW: "warn",
  NEEDS_CONFIRMATION: "warn",
  DISCARDED: "neutral",
  FAILED: "bad",
};
function refreshAfterSave(qc: ReturnType<typeof useQueryClient>) {
  for (const key of ["invoices", "invoice", "flags", "flag", "dashboard", "docs-review"]) {
    qc.invalidateQueries({ queryKey: [key] });
  }
}

const STATUS_LABEL: Record<string, string> = {
  NEEDS_CONFIRMATION: "possible duplicate: not saved",
  DISCARDED: "discarded",
};

export default function IngestPage() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [opts, setOpts] = useState({ direction: "AP", party_type: "VENDOR", date_order: "US" });
  const [drag, setDrag] = useState(false);
  const [mappingJob, setMappingJob] = useState<any>(null);
  const [draftId, setDraftId] = useState<number | null>(null);
  const [heldJob, setHeldJob] = useState<any>(null);
  const shownHeld = useRef<Set<number>>(new Set());
  const fileRef = useRef<HTMLInputElement>(null);
  const jobs = useQuery({
    queryKey: ["ingest-jobs"],
    queryFn: () => api.get<any[]>("/ingest/jobs"),
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => j.status === "QUEUED" || j.status === "RUNNING") ? 1000 : 5000),
  });
  const docs = useQuery({ queryKey: ["docs-review"], queryFn: () => api.get<any[]>("/documents", { status: "NEEDS_REVIEW" }), refetchInterval: 10000 });
  const upload = useMutation({
    mutationFn: (files: FileList | File[]) => {
      const fd = new FormData();
      Array.from(files).forEach((f) => fd.append("files", f, f.name));
      fd.append("options", JSON.stringify(opts));
      return api.upload("/ingest/files", fd);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ingest-jobs"] }),
  });
  const scan = useMutation({ mutationFn: () => api.post("/ingest/watch/scan"), onSuccess: () => qc.invalidateQueries({ queryKey: ["ingest-jobs"] }) });

  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "confirm" | "discard" }) => api.post(`/ingest/jobs/${id}/${action}`),
    onSuccess: () => {
      setHeldJob(null);
      qc.invalidateQueries({ queryKey: ["ingest-jobs"] });
    },
  });
  // A file held back as a possible duplicate opens the warning straight away, once.
  useEffect(() => {
    const j = jobs.data?.find((x) => x.status === "NEEDS_CONFIRMATION" && !shownHeld.current.has(x.id));
    if (j && !heldJob) {
      shownHeld.current.add(j.id);
      setHeldJob(j);
    }
  }, [jobs.data, heldJob]);

  // When an upload is saved, everything that lists invoices or flags is out of date.
  const savedJobs = useRef<Set<number> | null>(null);
  useEffect(() => {
    const done = (jobs.data ?? []).filter((j) => j.status === "DONE").map((j) => j.id);
    const seen = savedJobs.current;
    savedJobs.current = new Set(done);
    if (seen && done.some((id) => !seen.has(id))) refreshAfterSave(qc);
  }, [jobs.data, qc]);

  return (
    <div className="space-y-4">
      <PageHeader title="Ingest" subtitle="CSV / Excel exports, X12 837P · 837I · 835, and PDF or scanned invoices" />
      {can("REVIEWER") && (
        <Card>
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Direction" value={opts.direction} onChange={(e) => setOpts({ ...opts, direction: e.target.value })} options={[["AP", "AP — bills we receive"], ["AR", "AR — bills we send"]]} />
            <Select label="Party type" value={opts.party_type} onChange={(e) => setOpts({ ...opts, party_type: e.target.value })} options={[["VENDOR", "Vendor"], ["CUSTOMER", "Customer"], ["PAYER", "Payer"]]} />
            <Select label="Date order in files" value={opts.date_order} onChange={(e) => setOpts({ ...opts, date_order: e.target.value })} options={[["US", "MM/DD/YYYY"], ["DMY", "DD/MM/YYYY"]]} />
            <Button variant="ghost" onClick={() => scan.mutate()} loading={scan.isPending}>
              <RefreshCw className="size-4" /> Scan watched folders
            </Button>
          </div>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              if (e.dataTransfer.files.length) upload.mutate(e.dataTransfer.files);
            }}
            className={cn("mt-4 flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-10 text-sm", drag ? "border-accent bg-accent/5" : "border-border")}
          >
            <FileUp className="size-6 text-ink-3" aria-hidden />
            <p>Drop files here, or</p>
            <Button variant="primary" onClick={() => fileRef.current?.click()} loading={upload.isPending}>
              Choose files
            </Button>
            <input ref={fileRef} type="file" multiple hidden onChange={(e) => e.target.files && upload.mutate(e.target.files)} accept=".csv,.tsv,.txt,.xlsx,.xlsm,.x12,.edi,.837,.835,.pdf,.png,.jpg,.jpeg,.tif,.tiff" />
            <ErrorBox error={upload.error} />
          </div>
        </Card>
      )}
      <Card title="Files">
        {jobs.isLoading ? (
          <Spinner />
        ) : !jobs.data?.length ? (
          <Empty>No files ingested in this session.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>File</Th>
                <Th>Status</Th>
                <Th className="text-right">Invoices</Th>
                <Th className="text-right">Lines</Th>
                <Th className="text-right">New flags</Th>
                <Th>Billed before?</Th>
                <Th>Notes</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((j) => {
                const p = j.result?.persist;
                const issues = j.result?.issues ?? [];
                return (
                  <tr key={j.id}>
                    <Td>
                      <div>{j.filename}</div>
                      <div className="text-xs text-ink-3">{j.source}</div>
                    </Td>
                    <Td>
                      <Badge tone={STATUS_TONE[j.status]}>{STATUS_LABEL[j.status] ?? j.status.replaceAll("_", " ").toLowerCase()}</Badge>
                    </Td>
                    <Td className="num text-right">{num(p?.invoice_count)}</Td>
                    <Td className="num text-right">{num(p?.line_count)}</Td>
                    <Td className="num text-right">{num(j.result?.detection?.new_flags)}</Td>
                    <Td className="max-w-sm text-xs">
                      {j.result?.held ? (
                        <HeldSummary held={j.result.held} decided={j.status !== "NEEDS_CONFIRMATION"} />
                      ) : (
                        <BilledBefore h={j.result?.detection?.history} />
                      )}
                    </Td>
                    <Td className="max-w-md text-xs text-ink-2">
                      {j.error}
                      {p?.duplicate_file_of?.length > 0 && <div>Same file was ingested before (flagged INV-002).</div>}
                      {issues.slice(0, 3).map((i: any, k: number) => (
                        <div key={k}>
                          {i.location}: {i.message}
                        </div>
                      ))}
                      {issues.length > 3 && <div>+{issues.length - 3} more</div>}
                    </Td>
                    <Td>
                      {j.status === "NEEDS_MAPPING" && (
                        <Button size="sm" variant="primary" onClick={() => setMappingJob(j)}>
                          Map columns
                        </Button>
                      )}
                      {j.status === "NEEDS_CONFIRMATION" && (
                        <Button size="sm" variant="primary" onClick={() => setHeldJob(j)}>
                          Review duplicate
                        </Button>
                      )}
                      {j.status === "NEEDS_REVIEW" && (
                        <Button size="sm" variant="primary" onClick={() => setDraftId(j.result.meta.needs_review_draft_id)}>
                          Review extraction
                        </Button>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>
      <Card title="Documents waiting for correction">
        {!docs.data?.length ? (
          <p className="text-sm text-ink-3">None.</p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Document</Th>
                <Th>Method</Th>
                <Th className="text-right">Confidence</Th>
                <Th>Ingested</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {docs.data.map((d) => (
                <tr key={d.id}>
                  <Td>{d.source_path}</Td>
                  <Td>{d.ingest_method}</Td>
                  <Td className="num text-right">{d.ingest_confidence?.toFixed(2)}</Td>
                  <Td className="text-xs">{dateTime(d.ingested_at)}</Td>
                  <Td>
                    {d.draft_id && (
                      <Button size="sm" onClick={() => setDraftId(d.draft_id)}>
                        Correct
                      </Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {mappingJob && <MappingWizard job={mappingJob} defaults={opts} onClose={() => setMappingJob(null)} />}
      {draftId && <CorrectionView draftId={draftId} onClose={() => setDraftId(null)} />}
      {heldJob && (
        <DuplicateWarning
          filename={heldJob.filename}
          held={heldJob.result.held}
          busy={decide.isPending}
          error={decide.error}
          confirmLabel="Upload anyway"
          cancelLabel="Discard file"
          onConfirm={() => decide.mutate({ id: heldJob.id, action: "confirm" })}
          onCancel={() => decide.mutate({ id: heldJob.id, action: "discard" })}
          onClose={() => setHeldJob(null)}
        />
      )}
    </div>
  );
}

function MappingWizard({ job, defaults, onClose }: { job: any; defaults: Record<string, string>; onClose: () => void }) {
  const qc = useQueryClient();
  const fields = useQuery({ queryKey: ["csv-fields"], queryFn: () => api.get("/ingest/csv/fields"), staleTime: Infinity });
  const headers: string[] = job.result.headers;
  const [mapping, setMapping] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(job.result.suggestion as Record<string, { column: string }>).map(([k, v]) => [k, v.column])),
  );
  const [opts, setOpts] = useState<Record<string, any>>({ ...defaults, party_name: "", strict_dates: true });
  const [name, setName] = useState(job.filename.replace(/\.\w+$/, "") + " template");
  const submit = useMutation({
    mutationFn: () =>
      api.post(`/ingest/jobs/${job.id}/mapping`, {
        mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)),
        options: { ...opts, party_name: opts.party_name || undefined },
        save_template_name: name || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ingest-jobs"] });
      onClose();
    },
  });
  const all: string[] = useMemo(() => [...(fields.data?.header_fields ?? []), ...(fields.data?.line_fields ?? [])], [fields.data]);
  const missing = (fields.data?.required ?? []).filter((r: string) => !mapping[r]);
  return (
    <Modal open onClose={onClose} title={`Map columns — ${job.filename}`} wide>
      <p className="mb-3 text-sm text-ink-2">
        Match each ABM Invoice Analytics field to a column in the file. Suggestions are pre-filled; saved templates apply automatically to files with the same header row.
      </p>
      <div className="mb-3 overflow-auto rounded border border-border">
        <table className="text-xs">
          <thead>
            <tr>
              {headers.map((h) => (
                <th key={h} className="border-b border-border bg-surface-2 px-2 py-1 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {job.result.preview.map((r: string[], i: number) => (
              <tr key={i}>
                {r.map((c, k) => (
                  <td key={k} className="border-b border-border px-2 py-1 whitespace-nowrap">
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid max-h-80 grid-cols-2 gap-x-4 gap-y-1 overflow-auto md:grid-cols-3">
        {all.map((f) => (
          <label key={f} className="flex items-center justify-between gap-2 text-xs">
            <span className={cn("text-ink-2", (fields.data?.required ?? []).includes(f) && "font-semibold text-ink")}>{f.replaceAll("_", " ")}</span>
            <select className="h-7 w-44 rounded border border-border bg-surface text-xs" value={mapping[f] ?? ""} onChange={(e) => setMapping({ ...mapping, [f]: e.target.value })}>
              <option value="">—</option>
              {headers.map((h) => (
                <option key={h} value={h}>
                  {h}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <Input label="Default party (if no party column)" value={opts.party_name} onChange={(e) => setOpts({ ...opts, party_name: e.target.value })} />
        <Select label="Date order" value={opts.date_order} onChange={(e) => setOpts({ ...opts, date_order: e.target.value })} options={[["US", "MM/DD/YYYY"], ["DMY", "DD/MM/YYYY"]]} />
        <Select
          label="Ambiguous dates"
          value={String(opts.strict_dates)}
          onChange={(e) => setOpts({ ...opts, strict_dates: e.target.value === "true" })}
          options={[["true", "Flag for me to check"], ["false", "Use the declared order"]]}
        />
        <Input label="Save as template" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      {missing.length > 0 && <p className="mt-2 text-sm text-critical">Required: {missing.join(", ")}</p>}
      <ErrorBox error={submit.error} />
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" disabled={missing.length > 0} loading={submit.isPending} onClick={() => submit.mutate()}>
          Import
        </Button>
      </div>
    </Modal>
  );
}

const INV_FIELDS: Array<[string, string]> = [
  ["vendor_name", "Vendor"],
  ["invoice_number", "Invoice number"],
  ["invoice_date", "Invoice date"],
  ["due_date", "Due date"],
  ["total", "Total"],
  ["po_number", "PO number"],
  ["tax_id", "Tax ID"],
  ["npi", "NPI"],
];

function CorrectionView({ draftId, onClose }: { draftId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const d = useQuery({ queryKey: ["draft", draftId], queryFn: () => api.get(`/drafts/${draftId}`) });
  const [inv, setInv] = useState<Record<string, string>>({});
  const [lines, setLines] = useState<any[]>([]);
  // clinical-trial site invoices carry a subject and protocol visit per line instead of a CPT code
  const trial = lines.some((l) => l.subject || l.visit);
  const lineCols: [string, string][] = trial
    ? [["subject", "Subject"], ["visit", "Visit"], ["item", "Item"], ["dos", "DOS"], ["amount", "Amount"]]
    : [["description", "Description"], ["code", "Code"], ["dos", "DOS"], ["units", "Units"], ["amount", "Amount"]];
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [fromAi, setFromAi] = useState(false);

  useEffect(() => {
    if (!d.data) return;
    setInv(d.data.fields.invoice ?? {});
    setLines(d.data.fields.lines ?? []);
    let url: string | null = null;
    api
      .blob(`/documents/${d.data.document_id}/content`)
      .then((b) => {
        url = URL.createObjectURL(b);
        setPdfUrl(url);
      })
      .catch(() => setPdfUrl(null));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [d.data]);

  const [held, setHeld] = useState<any>(null);
  const accept = useMutation({
    mutationFn: (confirm: boolean) =>
      api.post(`/drafts/${draftId}/accept`, { invoice: inv, lines, from_ai: fromAi, options: confirm ? { confirm_duplicates: true } : {} }),
    onSuccess: (res: any) => {
      if (res?.held) {
        setHeld(res.held); // nothing saved yet: ask first
        return;
      }
      qc.invalidateQueries({ queryKey: ["ingest-jobs"] });
      refreshAfterSave(qc);
      onClose();
    },
  });
  const reject = useMutation({ mutationFn: () => api.post(`/drafts/${draftId}/reject`), onSuccess: onClose });

  const conf: Record<string, number> = d.data?.fields.confidence ?? {};
  const ai = d.data?.ai_suggestion?.status === "OK" ? d.data.ai_suggestion.output?.extraction : null;
  const sum = lines.reduce((s, l) => s + (parseFloat(String(l.amount).replace(/[$,]/g, "")) || 0), 0);
  const total = parseFloat(String(inv.total ?? "").replace(/[$,]/g, ""));
  const arithmeticOk = !isNaN(total) && Math.abs(sum - total) < 0.005;

  return (
    <Modal open onClose={onClose} title="Correct extraction" wide>
      {d.isLoading ? (
        <Spinner />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="h-[70vh] rounded border border-border bg-surface-2">
            {pdfUrl ? <iframe title="Source document" src={pdfUrl} className="h-full w-full" /> : <pre className="h-full overflow-auto p-3 text-xs whitespace-pre-wrap">{d.data.fields.text}</pre>}
          </div>
          <div className="space-y-3 overflow-auto">
            <p className="text-xs text-ink-2">
              Method {d.data.fields.method} · overall confidence {d.data.fields.overall?.toFixed(2)} · highlighted fields need checking. Your corrections teach the template for this vendor layout.
            </p>
            {ai && (
              <div className="flex items-center justify-between rounded border border-accent/40 bg-accent/5 p-2 text-xs">
                <span>AI extraction available (suggestion only).</span>
                <Button
                  size="sm"
                  onClick={() => {
                    setInv({ ...inv, ...Object.fromEntries(Object.entries(ai.invoice).filter(([, v]) => v)) } as Record<string, string>);
                    setLines(ai.lines);
                    setFromAi(true);
                  }}
                >
                  Use AI values
                </Button>
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              {INV_FIELDS.map(([k, label]) => (
                <Input
                  key={k}
                  label={`${label}${conf[k] !== undefined ? ` (${Math.round(conf[k] * 100)}%)` : ""}`}
                  value={inv[k] ?? ""}
                  onChange={(e) => setInv({ ...inv, [k]: e.target.value })}
                  className={cn((conf[k] ?? 1) < 0.85 && "border-warning bg-warning/10")}
                />
              ))}
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="font-medium text-ink-2">Lines</span>
                <Badge tone={arithmeticOk ? "good" : "warn"}>
                  lines sum {money(Math.round(sum * 100))} {arithmeticOk ? "= total" : "≠ total"}
                </Badge>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-ink-2">
                    {lineCols.map(([, label]) => (
                      <th key={label}>{label}</th>
                    ))}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={i}>
                      {lineCols.map(([k]) => (
                        <td key={k} className="pr-1">
                          <input
                            aria-label={`line ${i + 1} ${k}`}
                            className="h-7 w-full rounded border border-border bg-surface px-1"
                            value={l[k] ?? ""}
                            onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)))}
                          />
                        </td>
                      ))}
                      <td>
                        <Button size="sm" variant="ghost" onClick={() => setLines(lines.filter((_, j) => j !== i))} aria-label="Remove line">
                          ×
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Button size="sm" variant="ghost" onClick={() => setLines([...lines, { description: "", amount: "", units: "1" }])}>
                + Add line
              </Button>
            </div>
            <ErrorBox error={accept.error} />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => reject.mutate()}>
                Reject document
              </Button>
              <Button variant="primary" loading={accept.isPending} onClick={() => accept.mutate(false)}>
                Accept and ingest
              </Button>
            </div>
          </div>
        </div>
      )}
      {held && (
        <DuplicateWarning
          filename="This document"
          held={held}
          busy={accept.isPending}
          error={accept.error}
          confirmLabel="Ingest anyway"
          cancelLabel="Back to correction"
          onConfirm={() => accept.mutate(true)}
          onCancel={() => setHeld(null)}
          onClose={() => setHeld(null)}
        />
      )}
    </Modal>
  );
}

function BilledBefore({ h }: { h?: any }) {
  if (!h) return <span className="text-ink-3">—</span>;
  const who = `${num(h.patients)} patient${h.patients === 1 ? "" : "s"}/subject${h.patients === 1 ? "" : "s"}`;
  if (h.repeats.length > 0) {
    return (
      <div className="space-y-1">
        <Badge tone="warn">
          Yes: {h.repeats.length} match{h.repeats.length === 1 ? "" : "es"} with earlier invoices
        </Badge>
        {h.repeats.slice(0, 3).map((r: any) => (
          <Link key={r.flag_id} to={`/review/${r.flag_id}`} className="block truncate text-accent-ink hover:underline" title={r.summary}>
            {r.rule_id}: {r.summary}
          </Link>
        ))}
        {h.repeats.length > 3 && (
          <Link to="/review?scope=ACROSS" className="block text-accent-ink hover:underline">
            +{h.repeats.length - 3} more in the review queue
          </Link>
        )}
      </div>
    );
  }
  if (!h.patients) return <span className="text-ink-2">No patient or subject on these lines; invoice-level checks only.</span>;
  return (
    <div className="text-ink-2">
      <Badge tone="good">No</Badge>{" "}
      {h.earlier_lines > 0
        ? `Checked ${num(h.lines)} line${h.lines === 1 ? "" : "s"} for ${who} against ${num(h.earlier_lines)} earlier line${h.earlier_lines === 1 ? "" : "s"} on ${num(h.earlier_invoices)} other invoice${h.earlier_invoices === 1 ? "" : "s"}.`
        : `First invoice for ${who}; nothing earlier to compare with.`}
    </div>
  );
}

function HeldSummary({ held, decided }: { held: any; decided: boolean }) {
  return (
    <div className="space-y-1">
      <Badge tone={decided ? "neutral" : "warn"}>
        Yes: {held.lines_matched > 0 ? `${num(held.lines_matched)} of ${num(held.lines_checked)} lines` : "this invoice"} already billed
      </Badge>
      <div className="text-ink-2">
        On {held.earlier_invoices.map((e: any) => e.number).join(", ") || "an earlier invoice"}.{" "}
        {decided ? "" : "Not saved until you confirm."}
      </div>
    </div>
  );
}

function DuplicateWarning({
  filename,
  held,
  busy,
  error,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
  onClose,
}: {
  filename: string;
  held: any;
  busy: boolean;
  error: unknown;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  onClose: () => void;
}) {
  const inv = held.invoices[0];
  return (
    <Modal open onClose={onClose} title="Possible duplicate: this may already have been billed">
      <div className="space-y-3 text-sm">
        <p>
          <b>{filename}</b>
          {inv && (
            <>
              {" "}
              (invoice {inv.invoice_number ?? inv.number} from {inv.party}, {money(inv.total_cents)})
            </>
          )}{" "}
          matches what was already billed
          {held.lines_matched > 0 && (
            <>
              : <b>{num(held.lines_matched)}</b> of its {num(held.lines_checked)} lines
            </>
          )}
          . <b>It has not been saved.</b>
        </p>
        <div>
          <div className="mb-1 text-xs font-medium text-ink-2">Already billed on</div>
          <ul className="list-disc pl-5">
            {held.earlier_invoices.map((e: any) => (
              <li key={e.id}>
                <Link className="text-accent-ink hover:underline" to={`/invoices/${e.id}`} onClick={onClose}>
                  {e.number}
                </Link>{" "}
                · {e.party} · {e.date ?? "no date"} · {money(e.total_cents)}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-ink-2">What matched ({num(held.repeat_count)})</div>
          <ul className="max-h-48 space-y-1 overflow-auto rounded border border-border p-2 text-xs">
            {held.repeats.slice(0, 12).map((r: any, i: number) => (
              <li key={i}>
                <span className="font-mono text-ink-2">{r.rule_id}</span> {r.summary}
              </li>
            ))}
            {held.repeat_count > 12 && <li className="text-ink-3">+{num(held.repeat_count - 12)} more</li>}
          </ul>
        </div>
        <p className="text-ink-2">
          If it is a genuine new bill (a corrected invoice, a legitimate repeat), save it anyway: the matches are kept
          as flags in the review queue. Otherwise nothing of it is kept.
        </p>
        <ErrorBox error={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button variant="primary" onClick={onConfirm} loading={busy}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
