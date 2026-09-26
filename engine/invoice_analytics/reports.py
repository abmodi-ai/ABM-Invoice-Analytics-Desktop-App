"""Reports: dashboard, duplicates found/recovered, review throughput, rule precision and the
threshold tuning report. Export to CSV or PDF."""

from __future__ import annotations

import csv
import io
from typing import Any

from invoice_analytics.context import Engine
from invoice_analytics.normalize import format_cents
from invoice_analytics.rules import ALL_RULES, RULES_BY_ID

_TIER_RANK_SQL = "CASE f.tier WHEN 'HARD' THEN 4 WHEN 'PROBABLE' THEN 3 WHEN 'WEAK' THEN 2 ELSE 1 END"


def subject_exposure_sql(where: str) -> str:
    """One row per flagged subject invoice (HARD/PROBABLE, active, unsuppressed):
    invoice_id, tier (highest), amount (at risk, counted once), confirmed, recovered.

    amount = the larger of the invoice-level exposure and the sum of distinct flagged line charges,
    capped at the invoice total."""
    return f"""
        SELECT f.subject_invoice_id AS invoice_id,
               CASE MAX({_TIER_RANK_SQL}) WHEN 4 THEN 'HARD' WHEN 3 THEN 'PROBABLE' WHEN 2 THEN 'WEAK' ELSE 'INFO' END AS tier,
               MIN(MAX(COALESCE(MAX(CASE WHEN f.subject_type='INVOICE' THEN f.amount_at_risk_cents END), 0),
                       COALESCE((SELECT SUM(m) FROM (SELECT MAX(f2.amount_at_risk_cents) AS m FROM flags f2
                                  WHERE f2.subject_invoice_id=f.subject_invoice_id AND f2.subject_type='LINE'
                                    AND f2.active=1 AND f2.suppressed_by IS NULL AND f2.tier IN ('HARD','PROBABLE')
                                  GROUP BY f2.subject_id)), 0)),
                   MAX(ABS(i.total_cents))) AS amount,
               MAX(CASE WHEN f.status='CONFIRMED' THEN 1 ELSE 0 END) AS confirmed,
               (SELECT SUM(r.recovered_cents) FROM reviews r JOIN flags f3 ON f3.id=r.flag_id
                 WHERE f3.subject_invoice_id=f.subject_invoice_id AND r.decision='CONFIRMED_DUPLICATE') AS recovered
        FROM flags f JOIN invoices i ON i.id=f.subject_invoice_id
        WHERE f.active=1 AND f.suppressed_by IS NULL AND f.tier IN ('HARD','PROBABLE') AND {where}
        GROUP BY f.subject_invoice_id"""


def dashboard(eng: Engine) -> dict[str, Any]:
    q = eng.db.query
    # Money and counts are per subject invoice, not per flag: one duplicate invoice typically fires
    # several rules (INV-001 + INV-006 + CLN-001 ...), and summing flags would count it repeatedly.
    by_tier = {r["tier"]: r for r in q(f"""
        SELECT tier, COUNT(*) AS n, SUM(amount) AS amount FROM ({subject_exposure_sql("f.status='OPEN'")})
        GROUP BY tier""")}
    by_tier["WEAK"] = {
        "n": eng.db.scalar(
            "SELECT COUNT(DISTINCT subject_invoice_id) FROM flags WHERE status='OPEN' AND active=1 AND suppressed_by IS NULL"
            " AND tier='WEAK' AND subject_invoice_id NOT IN (SELECT subject_invoice_id FROM flags WHERE status='OPEN'"
            " AND active=1 AND suppressed_by IS NULL AND tier IN ('HARD','PROBABLE'))"
        ),
        "amount": None,
    }
    recovered = eng.db.scalar(
        "SELECT COALESCE(SUM(recovered_cents),0) FROM reviews WHERE decision='CONFIRMED_DUPLICATE'"
    )
    top = q("""SELECT p.id AS party_id, p.display_name, COUNT(DISTINCT i.id) AS invoices,
                      COUNT(DISTINCT CASE WHEN f.id IS NOT NULL THEN i.id END) AS flagged
               FROM invoices i JOIN parties p ON p.id=i.party_id
               LEFT JOIN flags f ON f.subject_invoice_id=i.id AND f.active=1 AND f.suppressed_by IS NULL
                    AND f.tier IN ('HARD','PROBABLE')
               WHERE i.deleted_at IS NULL GROUP BY p.cluster_id HAVING invoices >= 5
               ORDER BY flagged * 1.0 / invoices DESC LIMIT 10""")
    for t in top:
        t["duplicate_rate"] = round(t["flagged"] / t["invoices"], 4) if t["invoices"] else 0
    from invoice_analytics.clinical.refdata import installed_versions

    stale = [r["dataset"] for r in installed_versions(eng) if r["stale"]]
    return {
        "flag_rows_open": eng.db.scalar(
            "SELECT COUNT(*) FROM flags WHERE status='OPEN' AND active=1 AND suppressed_by IS NULL"
            " AND tier IN ('HARD','PROBABLE')"
        ),
        "open_by_tier": {
            t: {"count": by_tier.get(t, {}).get("n", 0), "amount_cents": by_tier.get(t, {}).get("amount") or 0}
            for t in ("HARD", "PROBABLE", "WEAK", "INFO")
        },
        "amount_at_risk_cents": sum((by_tier.get(t, {}).get("amount") or 0) for t in ("HARD", "PROBABLE")),
        "amount_recovered_cents": recovered,
        "top_vendors_by_duplicate_rate": top,
        "totals": {
            "invoices": eng.db.scalar("SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL"),
            "lines": eng.db.scalar("SELECT COUNT(*) FROM invoice_lines WHERE deleted_at IS NULL"),
            "documents": eng.db.scalar("SELECT COUNT(*) FROM documents"),
            "needs_review_documents": eng.db.scalar(
                "SELECT COUNT(*) FROM documents WHERE ingest_status='NEEDS_REVIEW'"
            ),
            "identity_reviews": eng.db.scalar("SELECT COUNT(*) FROM identity_suggestions WHERE status='OPEN'"),
        },
        "stale_reference_data": stale,
        "last_run": eng.db.one(
            "SELECT id, mode, status, started_at, finished_at FROM detection_runs ORDER BY id DESC LIMIT 1"
        ),
    }


def duplicates_by_period(
    eng: Engine, period: str = "month", date_from: str | None = None, date_to: str | None = None
) -> list[dict[str, Any]]:
    expr = {
        "month": "strftime('%Y-%m', i.invoice_date)",
        "quarter": "strftime('%Y', i.invoice_date) || '-Q' || ((CAST(strftime('%m', i.invoice_date) AS INTEGER) + 2) / 3)",
        "year": "strftime('%Y', i.invoice_date)",
        "week": "strftime('%Y-W%W', i.invoice_date)",
    }.get(period, "strftime('%Y-%m', i.invoice_date)")
    return eng.db.query(
        f"""SELECT {expr} AS period, p.display_name AS vendor,
                   COUNT(*) AS flagged_invoices,
                   SUM(CASE WHEN x.confirmed THEN 1 ELSE 0 END) AS confirmed_invoices,
                   SUM(CASE WHEN x.confirmed THEN x.amount ELSE 0 END) AS confirmed_cents,
                   COALESCE(SUM(x.recovered), 0) AS recovered_cents
            FROM ({subject_exposure_sql("1=1")}) x JOIN invoices i ON i.id=x.invoice_id
            JOIN parties p ON p.id=i.party_id
            WHERE (? IS NULL OR i.invoice_date>=?) AND (? IS NULL OR i.invoice_date<=?)
            GROUP BY 1, 2 ORDER BY 1, 2""",
        (date_from, date_from, date_to, date_to),
    )


def review_throughput(eng: Engine) -> list[dict[str, Any]]:
    return eng.db.query("""SELECT substr(r.decided_at,1,10) AS day, u.display_name AS reviewer, COUNT(*) AS decisions,
                  SUM(r.decision='CONFIRMED_DUPLICATE') AS confirmed, SUM(r.decision='NOT_DUPLICATE') AS dismissed,
                  SUM(r.decision='NEEDS_INFO') AS needs_info
           FROM reviews r LEFT JOIN users u ON u.id=r.reviewer_user_id GROUP BY 1, 2 ORDER BY 1 DESC, 2""")


def rule_precision(eng: Engine) -> list[dict[str, Any]]:
    rows = {r["rule_id"]: r for r in eng.db.query("""SELECT f.rule_id, COUNT(DISTINCT f.id) AS flags,
                  COUNT(DISTINCT CASE WHEN r.decision='CONFIRMED_DUPLICATE' THEN f.id END) AS confirmed,
                  COUNT(DISTINCT CASE WHEN r.decision='NOT_DUPLICATE' THEN f.id END) AS dismissed
           FROM flags f LEFT JOIN reviews r ON r.flag_id=f.id WHERE f.active=1 AND f.suppressed_by IS NULL
           GROUP BY f.rule_id""")}
    out = []
    for rule in ALL_RULES:
        r = rows.get(rule.rule_id, {"flags": 0, "confirmed": 0, "dismissed": 0})
        reviewed = r["confirmed"] + r["dismissed"]
        out.append(
            {
                "rule_id": rule.rule_id,
                "title": rule.title,
                "flags": r["flags"],
                "reviewed": reviewed,
                "confirmed": r["confirmed"],
                "dismissed": r["dismissed"],
                "precision": round(r["confirmed"] / reviewed, 4) if reviewed else None,
            }
        )
    return out


def threshold_tuning(eng: Engine, min_reviews: int = 20) -> list[dict[str, Any]]:
    """Per-rule precision from real reviews, with suggested changes an admin may apply."""
    out = []
    for r in rule_precision(eng):
        s: dict[str, Any] = {**r, "suggestion": None}
        rule = RULES_BY_ID[r["rule_id"]]
        cfg = eng.settings.get(f"rules.{r['rule_id']}", {})
        if r["reviewed"] >= min_reviews and r["precision"] is not None:
            if r["precision"] < 0.5 and cfg.get("tier") in ("PROBABLE", "HARD"):
                s["suggestion"] = {"change": {"tier": "WEAK"}, "reason": f"precision {r['precision']:.2f} below 0.50"}
            elif r["precision"] > 0.95 and cfg.get("tier") == "WEAK":
                s["suggestion"] = {
                    "change": {"tier": "PROBABLE"},
                    "reason": f"precision {r['precision']:.2f} above 0.95",
                }
            elif r["rule_id"] == "INV-005" and r["precision"] < 0.3:
                s["suggestion"] = {
                    "change": {"window_days": max(10, int(cfg.get("window_days", 45)) - 15)},
                    "reason": "same-total window is producing mostly non-duplicates",
                }
            elif r["rule_id"] == "CLN-008" and r["precision"] < 0.5:
                s["suggestion"] = {
                    "change": {"min_cosine": min(0.98, float(cfg.get("min_cosine", 0.9)) + 0.03)},
                    "reason": "semantic threshold too loose",
                }
        s["default_tier"] = rule.default_tier
        s["current_config"] = cfg
        out.append(s)
    return out


REPORTS = {
    "duplicates": duplicates_by_period,
    "throughput": review_throughput,
    "rule_precision": rule_precision,
    "threshold_tuning": threshold_tuning,
}


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(
                {k: (format_cents(v) if k.endswith("_cents") and isinstance(v, int) else v) for k, v in r.items()}
            )
    return buf.getvalue().encode()


def to_pdf(title: str, rows: list[dict[str, Any]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), title=title)
    styles = getSampleStyleSheet()
    story: list[Any] = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    if rows:
        cols = [k for k in rows[0] if not isinstance(rows[0][k], (dict, list))]
        data = [cols] + [
            [
                (
                    format_cents(r[c])
                    if c.endswith("_cents") and isinstance(r[c], int)
                    else str(r[c] if r[c] is not None else "")
                )[:40]
                for c in cols
            ]
            for r in rows[:2000]
        ]
        t = Table(data, repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ]
            )
        )
        story.append(t)
    else:
        story.append(Paragraph("No data.", styles["Normal"]))
    doc.build(story)
    return buf.getvalue()
