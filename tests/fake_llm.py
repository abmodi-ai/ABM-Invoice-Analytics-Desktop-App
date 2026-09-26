"""A scriptable OpenAI-compatible fake of llama-server for tests (loopback only).

Behaviour is chosen from the request:
  - response_format with an "explanation" property -> T2 explanation built from the evidence
  - tools present                                  -> T4 tool-calling script, then a verdict
  - response_format with a "sql" property          -> T3: SQL from FakeLLM.sql_for(question)
  - response_format with an "invoice" property     -> T1 extraction from the document text
Set FakeLLM.mode to "hallucinate" / "bad_citation" / "adversarial" to exercise the validators.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeLLM:
    def __init__(self) -> None:
        self.mode = "good"
        self.requests: list[dict[str, Any]] = []
        self.sql_override: str | None = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeLLM:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()

    # ------------------------------------------------------------ scripted behaviours
    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(body)
        msgs = body["messages"]
        rf = (body.get("response_format") or {}).get("json_schema", {}).get("schema", {})
        props = rf.get("properties", {})
        if "explanation" in props and "sql" not in props:
            ev = json.loads(msgs[-1]["content"].split("EVIDENCE:\n", 1)[1])
            text = ev.get("summary", "")
            if self.mode == "hallucinate":
                text += " The same patient was also billed code 99999 for $4,321.00 on 2019-01-01."
            diffs = [f"{d['field']} differs" for d in ev.get("differing_fields", [])]
            return self._content(json.dumps({"explanation": text, "key_differences": diffs}))
        if "sql" in props:
            q = msgs[-1]["content"]
            sql = self.sql_override or self.sql_for(q)
            return self._content(json.dumps({"sql": sql, "explanation": "fake"}))
        if "invoice" in props:
            text = msgs[-1]["content"]
            num = re.search(r"Invoice (?:No|Number)[.:]?\s*([A-Z0-9-]+)", text, re.I)
            total = re.search(r"Total(?: Due)?[:\s]*\$?([\d,]+\.\d{2})", text, re.I)
            date = re.search(r"Date[:\s]*(\d{1,2}/\d{1,2}/\d{4})", text, re.I)
            lines = [
                {
                    "description": m.group(1).strip(),
                    "amount": m.group(2),
                    "code": None,
                    "units": "1",
                    "dos": None,
                    "modifiers": [],
                }
                for m in re.finditer(r"^(.+?)\s+\$?([\d,]+\.\d{2})$", text, re.M)
                if not re.search(r"total", m.group(1), re.I)
            ]
            return self._content(
                json.dumps(
                    {
                        "invoice": {
                            "vendor_name": text.split("\n")[1].strip() if "\n" in text else None,
                            "invoice_number": num.group(1) if num else None,
                            "invoice_date": date.group(1) if date else None,
                            "total": total.group(1) if total else None,
                            "due_date": None,
                            "po_number": None,
                            "tax_id": None,
                            "npi": None,
                        },
                        "lines": lines,
                        "confidence": {"invoice_number": 0.9},
                    }
                )
            )
        if body.get("tools"):
            tool_msgs = [m for m in msgs if m["role"] == "tool"]
            flag_id = int(re.search(r"FLAG ID: (\d+)", msgs[1]["content"]).group(1))
            if len(tool_msgs) == 0:
                return self._tool_calls([("get_flag", {"flag_id": flag_id})])
            if len(tool_msgs) == 1:
                res = json.loads(tool_msgs[0]["content"])["result"]
                cps = res.get("counterpart_invoice_ids") or []
                if cps:
                    return self._tool_calls(
                        [("diff_invoices", {"invoice_a": res["subject_invoice_id"], "invoice_b": cps[0]})]
                    )
            return self._content("done")
        # final verdict (json schema with "verdict")
        if "verdict" in props:
            tool_msgs = [m for m in msgs if m["role"] == "tool"]
            first = json.loads(tool_msgs[0]["content"]) if tool_msgs else {"result": {}}
            rule = first["result"].get("rule_id", "")
            cite_val = "WRONG-VALUE" if self.mode == "bad_citation" else rule
            return self._content(
                json.dumps(
                    {
                        "verdict": "DUPLICATE",
                        "confidence": 0.8,
                        "rationale": f"Rule {rule} matched.",
                        "cited_evidence": [{"tool_call_id": "tc_1", "field": "rule_id", "value": cite_val}],
                        "recommended_action": "REVIEW",
                    }
                )
            )
        return self._content("{}")

    def sql_for(self, q: str) -> str:
        if "vendor" in q.lower():
            return "SELECT party_name, COUNT(*) AS n FROM v_invoices GROUP BY party_name ORDER BY n DESC"
        return "SELECT flag_id, rule_id, tier FROM v_flags ORDER BY flag_id LIMIT 10"

    @staticmethod
    def _content(s: str) -> dict[str, Any]:
        return {"choices": [{"message": {"role": "assistant", "content": s}}]}

    @staticmethod
    def _tool_calls(calls: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                            for i, (n, a) in enumerate(calls)
                        ],
                    }
                }
            ]
        }

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def do_POST(self) -> None:
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n))
                out = json.dumps(outer.respond(body)).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(out)

        return H
