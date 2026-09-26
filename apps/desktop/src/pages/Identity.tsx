import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Card, Empty, ErrorBox, Input, PageHeader, Select, Spinner, Table, Tabs, Td, Th } from "../components/ui";
import { useAuth } from "../lib/auth";
import { pct } from "../lib/format";

export default function IdentityPage() {
  const [tab, setTab] = useState<"parties" | "patients" | "clusters">("parties");
  const qc = useQueryClient();
  const { can } = useAuth();
  const resolve = useMutation({ mutationFn: () => api.post("/identity/resolve"), onSuccess: () => setTimeout(() => qc.invalidateQueries(), 4000) });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Parties & patients"
        subtitle="Suggested merges are never applied automatically for parties. Patients auto-link only above the precision threshold."
        actions={
          can("REVIEWER") && (
            <Button onClick={() => resolve.mutate()} loading={resolve.isPending}>
              Run identity resolution
            </Button>
          )
        }
      />
      {resolve.isSuccess && <p className="text-sm text-ink-2">Identity resolution is running in the background, followed by a full sweep.</p>}
      <Tabs
        tabs={[
          ["parties", "Party merge suggestions"],
          ["patients", "Patient link review"],
          ["clusters", "Party clusters"],
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "parties" && <PartySuggestions />}
      {tab === "patients" && <PatientSuggestions />}
      {tab === "clusters" && <PartyClusters />}
    </div>
  );
}

function PartySuggestions() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [status, setStatus] = useState("OPEN");
  const q = useQuery({ queryKey: ["party-sugg", status], queryFn: () => api.get<any[]>("/parties/merge-suggestions", { status }) });
  const act = useMutation({
    mutationFn: ({ merge, l, r }: { merge: boolean; l: number; r: number }) => api.post(merge ? "/parties/merge" : "/parties/unmerge", { left_id: l, right_id: r }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["party-sugg"] }),
  });
  return (
    <Card actions={<Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={[["OPEN", "Open"], ["ACCEPTED", "Merged"], ["AUTO", "Deterministic (tax ID / NPI)"], ["REJECTED", "Rejected"]]} />}>
      {q.isLoading ? (
        <Spinner />
      ) : !q.data?.length ? (
        <Empty>No suggestions.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Party A</Th>
              <Th>Party B</Th>
              <Th className="text-right">Probability</Th>
              <Th>Evidence</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {q.data.map((s) => (
              <tr key={s.id}>
                <Td>
                  {s.left?.display_name}
                  <div className="text-xs text-ink-3">{s.left?.remit_address_norm}</div>
                </Td>
                <Td>
                  {s.right?.display_name}
                  <div className="text-xs text-ink-3">{s.right?.remit_address_norm}</div>
                </Td>
                <Td className="num text-right">{pct(s.match_probability)}</Td>
                <Td className="text-xs text-ink-2">
                  {s.evidence.method}
                  {s.evidence.name && ` · name ${s.evidence.name.level.replace("name_", "")}`}
                  {s.evidence.remit_address && ` · address ${s.evidence.remit_address.replace("addr_", "")}`}
                  {s.evidence.phone && ` · phone ${s.evidence.phone.replace("phone_", "")}`}
                </Td>
                <Td className="whitespace-nowrap">
                  {can("ADMIN") && status === "OPEN" && (
                    <>
                      <Button size="sm" variant="primary" onClick={() => act.mutate({ merge: true, l: s.left_id, r: s.right_id })}>
                        Merge
                      </Button>{" "}
                      <Button size="sm" onClick={() => act.mutate({ merge: false, l: s.left_id, r: s.right_id })}>
                        Keep separate
                      </Button>
                    </>
                  )}
                  {can("ADMIN") && (status === "ACCEPTED" || status === "AUTO") && (
                    <Button size="sm" onClick={() => act.mutate({ merge: false, l: s.left_id, r: s.right_id })}>
                      Unmerge
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <ErrorBox error={act.error} />
    </Card>
  );
}

function PatientSuggestions() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("OPEN");
  const [names, setNames] = useState(false);
  const q = useQuery({ queryKey: ["pat-sugg", status, names], queryFn: () => api.get<any[]>("/patients/link-suggestions", { status, show_names: names }) });
  const act = useMutation({
    mutationFn: ({ link, l, r }: { link: boolean; l: number; r: number }) => api.post(link ? "/patients/link" : "/patients/unlink", { left_id: l, right_id: r }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pat-sugg"] }),
  });
  return (
    <Card
      actions={
        <>
          <label className="flex items-center gap-1 text-xs text-ink-2">
            <input type="checkbox" checked={names} onChange={(e) => setNames(e.target.checked)} /> Show names (logged)
          </label>
          <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={[["OPEN", "Needs review"], ["AUTO", "Auto-linked"], ["ACCEPTED", "Linked by reviewer"], ["REJECTED", "Kept separate"]]} />
        </>
      }
    >
      {q.isLoading ? (
        <Spinner />
      ) : !q.data?.length ? (
        <Empty>No patient pairs in this state.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Record A</Th>
              <Th>Record B</Th>
              <Th className="text-right">Probability</Th>
              <Th>Field agreement</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {q.data.map((s) => (
              <tr key={s.id}>
                <Td>{names ? `${s.left?.name} · ${s.left?.dob}` : `P-${s.left_id}`}</Td>
                <Td>{names ? `${s.right?.name} · ${s.right?.dob}` : `P-${s.right_id}`}</Td>
                <Td className="num text-right">{pct(s.match_probability)}</Td>
                <Td className="text-xs text-ink-2">
                  {s.evidence.first_name && <>first JW {s.evidence.first_name.jaro_winkler ?? "—"}{s.evidence.first_name.nickname && " (nickname)"} · </>}
                  {s.evidence.last_name && <>last JW {s.evidence.last_name.jaro_winkler ?? "—"}{s.evidence.last_name.phonetic && " (phonetic)"} · </>}
                  {s.evidence.dob && <>DOB {s.evidence.dob} · </>}
                  {s.evidence.source_id && <Badge tone="accent">same member/MRN</Badge>}
                  {!s.evidence.first_name && s.evidence.method}
                </Td>
                <Td className="whitespace-nowrap">
                  {status !== "ACCEPTED" && (
                    <Button size="sm" variant="primary" onClick={() => act.mutate({ link: true, l: s.left_id, r: s.right_id })}>
                      Same person
                    </Button>
                  )}{" "}
                  {status !== "REJECTED" && (
                    <Button size="sm" onClick={() => act.mutate({ link: false, l: s.left_id, r: s.right_id })}>
                      Different people
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <ErrorBox error={act.error} />
    </Card>
  );
}

function PartyClusters() {
  const [q, setQ] = useState("");
  const parties = useQuery({ queryKey: ["parties", q], queryFn: () => api.get<any[]>("/parties", { q, limit: 500 }) });
  return (
    <Card actions={<Input aria-label="Filter parties" placeholder="Filter by name" value={q} onChange={(e) => setQ(e.target.value)} />}>
      {parties.isLoading ? (
        <Spinner />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Cluster</Th>
              <Th>Name</Th>
              <Th>Type</Th>
              <Th>NPI</Th>
              <Th className="text-right">Invoices</Th>
              <Th className="text-right">Aliases</Th>
            </tr>
          </thead>
          <tbody>
            {parties.data?.map((p) => (
              <tr key={p.id}>
                <Td className="num">{p.cluster_id}</Td>
                <Td>
                  {p.display_name}
                  {p.cluster_id !== p.id && <span className="ml-1 text-xs text-ink-3">(merged)</span>}
                </Td>
                <Td>{p.party_type.toLowerCase()}</Td>
                <Td className="font-mono">{p.npi ?? "—"}</Td>
                <Td className="num text-right">{p.invoices}</Td>
                <Td className="num text-right">{p.aliases}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
