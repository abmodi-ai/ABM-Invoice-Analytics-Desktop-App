"""In-memory DuckDB mirror of the invoice data used by the rule passes.

Loaded from SQLite (via Arrow) once per engine process and kept current incrementally on ingest,
so incremental detection does not pay the full load cost. DuckDB runs in-memory only with
spilling disabled and external file access off: no plaintext PHI ever reaches disk.
Patient names are never loaded here (identity linkage decrypts them into its own short-lived
connection).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import duckdb
import numpy as np
import pyarrow as pa

if TYPE_CHECKING:
    from invoice_analytics.context import Engine

log = logging.getLogger("invoice_analytics.store")
EPOCH = dt.date(1970, 1, 1)
DIM = 384


def new_duckdb(memory_limit: str = "1500MB") -> duckdb.DuckDBPyConnection:
    import psutil

    threads = max(1, (psutil.cpu_count(logical=False) or 2) - 1)
    con = duckdb.connect(
        ":memory:",
        config={
            "temp_directory": "",
            "max_temp_directory_size": "0B",
            "memory_limit": memory_limit,
            "threads": threads,
            "preserve_insertion_order": False,
        },
    )
    return con


def lock_down(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET enable_external_access = false")


_INV_SQL = """
SELECT i.id, i.party_id, COALESCE(p.cluster_id, p.id) AS cluster, p.party_type, i.direction,
       i.invoice_number_norm AS num_norm, i.invoice_number_ocr AS num_ocr, i.invoice_date AS inv_date,
       i.total_cents AS total, i.is_credit, i.status, i.claim_frequency_code AS freq,
       i.original_invoice_ref AS orig_ref, i.claim_type, i.document_id AS doc_id, d.sha256 AS doc_sha,
       p.tax_id_hmac AS tax_h, p.npi AS party_npi, p.remit_address_norm AS addr, i.po_number,
       (SELECT COUNT(*) FROM invoice_lines l WHERE l.invoice_id=i.id AND l.deleted_at IS NULL) AS line_count
FROM invoices i JOIN parties p ON p.id=i.party_id LEFT JOIN documents d ON d.id=i.document_id
WHERE i.deleted_at IS NULL
"""

_LINE_SQL = """
SELECT l.id, l.invoice_id, l.line_no, l.patient_id, COALESCE(pt.cluster_id, pt.id) AS patient_cluster,
       l.dos_from AS dos, l.dos_to, l.code, l.code_system, l.modifiers, l.units, l.charge_cents AS charge,
       l.paid_cents AS paid, l.rendering_npi AS npi, l.place_of_service AS pos, l.description_norm AS desc_norm,
       (l.description_embedding IS NOT NULL) AS has_emb,
       COALESCE(pa.cluster_id, pa.id) AS party_cluster, i.direction, i.invoice_date, i.claim_frequency_code AS freq,
       i.status AS inv_status, i.is_credit, i.claim_type, l.visit_label AS visit
FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id JOIN parties pa ON pa.id=i.party_id
LEFT JOIN patients pt ON pt.id=l.patient_id
WHERE l.deleted_at IS NULL AND i.deleted_at IS NULL
"""

_INV_SCHEMA = pa.schema(
    [
        ("id", pa.int64()),
        ("party_id", pa.int64()),
        ("cluster", pa.int64()),
        ("party_type", pa.string()),
        ("direction", pa.string()),
        ("num_norm", pa.string()),
        ("num_ocr", pa.string()),
        ("inv_date", pa.date32()),
        ("total", pa.int64()),
        ("is_credit", pa.bool_()),
        ("status", pa.string()),
        ("freq", pa.string()),
        ("orig_ref", pa.string()),
        ("claim_type", pa.string()),
        ("doc_id", pa.int64()),
        ("doc_sha", pa.string()),
        ("tax_h", pa.string()),
        ("party_npi", pa.string()),
        ("addr", pa.string()),
        ("po_number", pa.string()),
        ("line_count", pa.int64()),
        ("sort_key", pa.int64()),
    ]
)

_LINE_SCHEMA = pa.schema(
    [
        ("id", pa.int64()),
        ("invoice_id", pa.int64()),
        ("line_no", pa.int64()),
        ("patient_id", pa.int64()),
        ("patient_cluster", pa.int64()),
        ("dos", pa.date32()),
        ("dos_to", pa.date32()),
        ("code", pa.string()),
        ("code_system", pa.string()),
        ("mods", pa.string()),
        ("units", pa.float64()),
        ("charge", pa.int64()),
        ("paid", pa.int64()),
        ("npi", pa.string()),
        ("pos", pa.string()),
        ("desc_norm", pa.string()),
        ("has_emb", pa.bool_()),
        ("party_cluster", pa.int64()),
        ("direction", pa.string()),
        ("inv_sort", pa.int64()),
        ("freq", pa.string()),
        ("inv_status", pa.string()),
        ("is_credit", pa.bool_()),
        ("claim_type", pa.string()),
        ("visit", pa.string()),
    ]
)


def _d(v: str | None) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v[:10])
    except ValueError:
        return None


def _mods(js: str | None) -> str:
    if not js or js == "[]":
        return ""
    # stored as a JSON sorted array; keep a compact comma-joined form for SQL equality
    return js.strip("[]").replace('"', "").replace(" ", "")


class DetectionStore:
    def __init__(self, eng: Engine) -> None:
        self.eng = eng
        self.con = new_duckdb()
        lock_down(self.con)
        self.lock = threading.RLock()
        self.loaded = False
        self.dim = DIM

    def close(self) -> None:
        with self.lock:
            self.con.close()

    @contextmanager
    def locked(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with self.lock:
            if not self.loaded:
                self.load_all()
            yield self.con

    # ------------------------------------------------------------ loading
    def _raw_rows(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        conn = self.eng.db.conn
        prev = conn.row_factory
        conn.row_factory = None
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.row_factory = prev

    def _inv_table(self, rows: list[tuple[Any, ...]]) -> pa.Table:
        cols = list(zip(*rows, strict=True)) if rows else [()] * 21
        dates = [_d(v) for v in cols[7]]
        ids = list(cols[0])
        sort_key = [((d - EPOCH).days if d else 0) * 4294967296 + i for d, i in zip(dates, ids, strict=True)]
        arrays = [
            pa.array(ids, pa.int64()),
            pa.array(cols[1], pa.int64()),
            pa.array(cols[2], pa.int64()),
            pa.array(cols[3], pa.string()),
            pa.array(cols[4], pa.string()),
            pa.array(cols[5], pa.string()),
            pa.array(cols[6], pa.string()),
            pa.array(dates, pa.date32()),
            pa.array(cols[8], pa.int64()),
            pa.array([bool(x) for x in cols[9]], pa.bool_()),
            pa.array(cols[10], pa.string()),
            pa.array(cols[11], pa.string()),
            pa.array(cols[12], pa.string()),
            pa.array(cols[13], pa.string()),
            pa.array(cols[14], pa.int64()),
            pa.array(cols[15], pa.string()),
            pa.array(cols[16], pa.string()),
            pa.array(cols[17], pa.string()),
            pa.array(cols[18], pa.string()),
            pa.array(cols[19], pa.string()),
            pa.array(cols[20], pa.int64()),
            pa.array(sort_key, pa.int64()),
        ]
        return pa.Table.from_arrays(arrays, schema=_INV_SCHEMA)

    def _line_table(self, rows: list[tuple[Any, ...]]) -> pa.Table:
        cols = list(zip(*rows, strict=True)) if rows else [()] * 25
        inv_dates = [_d(v) for v in cols[19]]
        inv_sort = [
            ((d - EPOCH).days if d else 0) * 4294967296 + iid for d, iid in zip(inv_dates, cols[1], strict=True)
        ]
        arrays = [
            pa.array(cols[0], pa.int64()),
            pa.array(cols[1], pa.int64()),
            pa.array(cols[2], pa.int64()),
            pa.array(cols[3], pa.int64()),
            pa.array(cols[4], pa.int64()),
            pa.array([_d(v) for v in cols[5]], pa.date32()),
            pa.array([_d(v) for v in cols[6]], pa.date32()),
            pa.array(cols[7], pa.string()),
            pa.array(cols[8], pa.string()),
            pa.array([_mods(v) for v in cols[9]], pa.string()),
            pa.array(cols[10], pa.float64()),
            pa.array(cols[11], pa.int64()),
            pa.array(cols[12], pa.int64()),
            pa.array(cols[13], pa.string()),
            pa.array(cols[14], pa.string()),
            pa.array(cols[15], pa.string()),
            pa.array([bool(b) for b in cols[16]], pa.bool_()),
            pa.array(cols[17], pa.int64()),
            pa.array(cols[18], pa.string()),
            pa.array(inv_sort, pa.int64()),
            pa.array(cols[20], pa.string()),
            pa.array(cols[21], pa.string()),
            pa.array([bool(b) for b in cols[22]], pa.bool_()),
            pa.array(cols[23], pa.string()),
            pa.array(cols[24], pa.string()),
        ]
        return pa.Table.from_arrays(arrays, schema=_LINE_SCHEMA)

    def _stream_into(self, table: str, sql: str, build: Any, size: int = 50_000) -> int:
        """Stream rows from SQLite into a DuckDB table chunk by chunk (bounded Python memory)."""
        conn = self.eng.db.conn
        prev = conn.row_factory
        conn.row_factory = None
        c = self.con
        n = 0
        try:
            cur = conn.execute(sql)
            first = True
            while True:
                rows = cur.fetchmany(size)
                if not rows and not first:
                    break
                tbl = build(rows)
                c.register("_chunk", tbl)
                if first:
                    c.execute(f"CREATE TABLE {table} AS SELECT * FROM _chunk")
                    first = False
                else:
                    c.execute(f"INSERT INTO {table} SELECT * FROM _chunk")
                c.unregister("_chunk")
                n += len(rows)
                del tbl, rows
                if n % size:
                    break
        finally:
            conn.row_factory = prev
        return n

    def load_all(self) -> dict[str, float]:
        with self.lock:
            t0 = time.perf_counter()
            c = self.con
            for t in ("inv", "lines"):
                c.execute(f"DROP TABLE IF EXISTS {t}")
            n_inv = self._stream_into("inv", _INV_SQL, self._inv_table)
            n_lines = self._stream_into("lines", _LINE_SQL, self._line_table)
            t1 = time.perf_counter()
            self._load_refdata()
            self.loaded = True
            t2 = time.perf_counter()
            stats = {"read_s": t1 - t0, "build_s": t2 - t1, "invoices": n_inv, "lines": n_lines}
            log.info("detection store loaded: %s", stats)
            return stats

    def _load_refdata(self) -> None:
        c = self.con
        specs = {
            "ref_ncci_ptp": "SELECT column1_code, column2_code, effective_from, effective_to, modifier_indicator, setting FROM ref_ncci_ptp",
            "ref_mue": "SELECT code, mue_value, mai, setting, effective_from, effective_to, version FROM ref_mue",
            "ref_global_days": "SELECT code, global_days, effective_year FROM ref_global_days",
            "ref_frequency_limits": "SELECT id, code, max_count, period, scope FROM ref_frequency_limits",
            "ref_modifiers": "SELECT modifier, category, ncci_bypass FROM ref_modifiers",
            "ref_recurring_series": "SELECT code, typical_frequency FROM ref_recurring_series",
        }
        for name, sql in specs.items():
            conn = self.eng.db.conn
            cur = conn.execute(sql + " LIMIT 0")
            names = [d[0] for d in cur.description]
            rows = self._raw_rows(sql)
            cols = list(zip(*rows, strict=True)) if rows else [()] * len(names)
            tbl = pa.table(
                {
                    n: pa.array(list(col)) if rows else pa.array([], pa.string())
                    for n, col in zip(names, cols, strict=True)
                }
            )
            c.execute(f"DROP TABLE IF EXISTS {name}")
            c.register("_ref", tbl)
            c.execute(f"CREATE TABLE {name} AS SELECT * FROM _ref")
            c.unregister("_ref")
        # typed helper views with DATE columns for effective dating
        c.execute(
            "CREATE OR REPLACE VIEW ptp AS SELECT column1_code AS c1, column2_code AS c2,"
            " TRY_CAST(effective_from AS DATE) AS eff_from, TRY_CAST(effective_to AS DATE) AS eff_to,"
            " CAST(modifier_indicator AS VARCHAR) AS mi, setting FROM ref_ncci_ptp"
        )
        c.execute(
            "CREATE OR REPLACE VIEW mue AS SELECT code, CAST(mue_value AS DOUBLE) AS mue_value,"
            " CAST(mai AS INTEGER) AS mai, setting, TRY_CAST(effective_from AS DATE) AS eff_from,"
            " TRY_CAST(effective_to AS DATE) AS eff_to, version FROM ref_mue"
        )

    def reload_refdata(self) -> None:
        with self.lock:
            if self.loaded:
                self._load_refdata()

    def upsert_invoices(self, invoice_ids: Iterable[int]) -> None:
        ids = sorted(set(invoice_ids))
        if not ids:
            return
        with self.lock:
            if not self.loaded:
                self.load_all()
                return
            c = self.con
            for chunk_start in range(0, len(ids), 900):
                chunk = ids[chunk_start : chunk_start + 900]
                ph = ",".join("?" * len(chunk))
                inv = self._inv_table(self._raw_rows(_INV_SQL + f" AND i.id IN ({ph})", chunk))
                lines = self._line_table(self._raw_rows(_LINE_SQL + f" AND l.invoice_id IN ({ph})", chunk))
                c.register("_ids", pa.table({"id": pa.array(chunk, pa.int64())}))
                c.execute("DELETE FROM lines WHERE invoice_id IN (SELECT id FROM _ids)")
                c.execute("DELETE FROM inv WHERE id IN (SELECT id FROM _ids)")
                c.unregister("_ids")
                c.register("_inv_arrow", inv)
                c.execute("INSERT INTO inv SELECT * FROM _inv_arrow")
                c.unregister("_inv_arrow")
                c.register("_line_arrow", lines)
                c.execute("INSERT INTO lines SELECT * FROM _line_arrow")
                c.unregister("_line_arrow")

    def refresh_clusters(self) -> None:
        """Re-read party and patient cluster ids after merges/links."""
        with self.lock:
            if not self.loaded:
                return
            c = self.con
            prow = self._raw_rows("SELECT id, COALESCE(cluster_id,id) FROM parties")
            trow = self._raw_rows("SELECT id, COALESCE(cluster_id,id) FROM patients")
            c.register(
                "_pc",
                pa.table(
                    {"id": pa.array([r[0] for r in prow], pa.int64()), "cl": pa.array([r[1] for r in prow], pa.int64())}
                ),
            )
            c.register(
                "_tc",
                pa.table(
                    {"id": pa.array([r[0] for r in trow], pa.int64()), "cl": pa.array([r[1] for r in trow], pa.int64())}
                ),
            )
            c.execute("UPDATE inv SET cluster = _pc.cl FROM _pc WHERE inv.party_id = _pc.id")
            c.execute("UPDATE lines SET party_cluster = inv.cluster FROM inv WHERE lines.invoice_id = inv.id")
            c.execute("UPDATE lines SET patient_cluster = _tc.cl FROM _tc WHERE lines.patient_id = _tc.id")
            c.unregister("_pc")
            c.unregister("_tc")

    # ------------------------------------------------------------ embeddings
    def embeddings_for(self, line_ids: Sequence[int]) -> tuple[list[int], np.ndarray]:
        """Embeddings are read on demand from the encrypted DB for candidate pairs only; holding every
        line's 384-float vector in memory would cost ~1.5 KB per line."""
        ids = sorted(set(line_ids))
        got: list[int] = []
        blobs: list[bytes] = []
        for i in range(0, len(ids), 900):
            chunk = ids[i : i + 900]
            for lid, blob in self._raw_rows(
                f"SELECT id, description_embedding FROM invoice_lines WHERE id IN ({','.join('?' * len(chunk))})"
                " AND description_embedding IS NOT NULL",
                chunk,
            ):
                got.append(int(lid))
                blobs.append(blob)
        if not blobs:
            return [], np.zeros((0, DIM), dtype=np.float32)
        return got, np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(-1, DIM)

    # ------------------------------------------------------------ queries used by rules/evidence
    def scope_table(self, invoice_ids: Iterable[int] | None) -> None:
        c = self.con
        for t in ("scope", "inv_c", "lines_c"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        if invoice_ids is None:
            c.execute("CREATE TABLE scope AS SELECT id FROM inv")
            return
        c.register("_s", pa.table({"id": pa.array(sorted(set(invoice_ids)), pa.int64())}))
        c.execute("CREATE TABLE scope AS SELECT id FROM _s")
        c.unregister("_s")
        # Candidate invoices: same party cluster, or sharing a tax ID / NPI / remit address / file hash
        # with a scoped invoice. Candidate lines: same patient cluster, or on a scoped invoice.
        c.execute("""
            CREATE TABLE inv_c AS
            WITH s AS (SELECT i.* FROM inv i JOIN scope USING (id))
            SELECT * FROM inv WHERE cluster IN (SELECT cluster FROM s)
               OR tax_h IN (SELECT tax_h FROM s WHERE tax_h IS NOT NULL)
               OR party_npi IN (SELECT party_npi FROM s WHERE party_npi IS NOT NULL)
               OR addr IN (SELECT addr FROM s WHERE addr IS NOT NULL)
               OR doc_sha IN (SELECT doc_sha FROM s WHERE doc_sha IS NOT NULL)""")
        c.execute("""
            CREATE TABLE lines_c AS
            SELECT * FROM lines WHERE invoice_id IN (SELECT id FROM scope)
               OR patient_cluster IN (SELECT DISTINCT l.patient_cluster FROM lines l JOIN scope s ON s.id = l.invoice_id
                                      WHERE l.patient_cluster IS NOT NULL)""")

    def fetch(self, table: str, ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        c = self.con
        c.register("_f", pa.table({"id": pa.array(sorted(set(ids)), pa.int64())}))
        try:
            cur = c.execute(f"SELECT t.* FROM {table} t JOIN _f USING (id)")
            names = [d[0] for d in cur.description]
            return {r[0]: dict(zip(names, r, strict=True)) for r in cur.fetchall()}
        finally:
            c.unregister("_f")
