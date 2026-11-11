"""Run the 24 scripted evaluation cases through Guard & Risk and generate docs/EVAL.md."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.db import db_session, init_db, new_id, set_kill_switch_value  # noqa: E402
from app.guard import evaluate_purchase  # noqa: E402
from app.ledger import append_event  # noqa: E402
from app.mandate import parse_iso_datetime  # noqa: E402

CASES_PATH = PROJECT_ROOT / "tests" / "eval_cases.json"
EVAL_MD_PATH = PROJECT_ROOT / "docs" / "EVAL.md"


def load_eval_cases() -> list[dict[str, Any]]:
    with CASES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def run_suite(llm_mode: str) -> list[dict[str, Any]]:
    """Execute all scripted cases in an isolated temp DB under the requested LLM_MODE."""
    cases = load_eval_cases()
    results: list[dict[str, Any]] = []

    prev_llm = os.environ.get("LLM_MODE")
    prev_paypal = os.environ.get("PAYPAL_MODE")
    os.environ["LLM_MODE"] = llm_mode
    os.environ["PAYPAL_MODE"] = "mock"

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            for idx, case in enumerate(cases):
                db_file = str(Path(tmp_dir) / f"eval_{llm_mode}_{idx}.db")
                init_db(db_file)
                now_dt = parse_iso_datetime(case["now"])
                m = case["mandate"]
                mandate_id = new_id("mnd")

                with db_session(db_file) as conn:
                    conn.execute(
                        """
                        INSERT INTO mandates (
                            id, raw_text, item_keywords, max_item_usd, max_day_usd,
                            allowed_sellers, expires_at, confirmed_at, status, assumptions
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', '[]')
                        """,
                        (
                            mandate_id,
                            m["raw_text"],
                            json.dumps(m["item_keywords"]),
                            float(m["max_item_usd"]),
                            float(m["max_day_usd"]),
                            json.dumps(m["allowed_sellers"]),
                            m["expires_at"],
                            m["confirmed_at"],
                        ),
                    )
                    append_event(
                        conn,
                        event_type="MANDATE_CONFIRMED",
                        ref_id=mandate_id,
                        body={"id": mandate_id, **m},
                        ts=m["confirmed_at"],
                    )

                    # Seed any prior approved purchases earlier on the same day
                    for p_idx, prior_pid in enumerate(case.get("prior_approved_products", [])):
                        prior_ts = now_dt - timedelta(minutes=10 - p_idx)
                        evaluate_purchase(conn, mandate_id, prior_pid, now=prior_ts)

                    if case.get("kill_switch"):
                        set_kill_switch_value(conn, True)

                    outcome = evaluate_purchase(
                        conn,
                        mandate_id=mandate_id,
                        product_id=case["product_id"],
                        now=now_dt,
                        request_text=m["raw_text"],
                    )

                expected_dec = case["expected_decision"]
                expected_codes = sorted(case["expected_reason_codes"])
                actual_dec = outcome["decision"]
                actual_codes = sorted(outcome["reason_codes"])

                passed = actual_dec == expected_dec and actual_codes == expected_codes
                results.append(
                    {
                        "id": case["id"],
                        "category": case["category"],
                        "description": case["description"],
                        "product_id": case["product_id"],
                        "expected_decision": expected_dec,
                        "expected_reason_codes": expected_codes,
                        "actual_decision": actual_dec,
                        "actual_reason_codes": actual_codes,
                        "risk_score": outcome["risk_score"],
                        "passed": passed,
                    }
                )
    finally:
        if prev_llm is None:
            os.environ.pop("LLM_MODE", None)
        else:
            os.environ["LLM_MODE"] = prev_llm
        if prev_paypal is None:
            os.environ.pop("PAYPAL_MODE", None)
        else:
            os.environ["PAYPAL_MODE"] = prev_paypal

    return results


def _format_mode_section(mode_label: str, rows: list[dict[str, Any]]) -> list[str]:
    total = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    failed = [r for r in rows if not r["passed"]]
    pct = (100.0 * passed / total) if total else 0.0

    lines: list[str] = [
        f"## Results (`LLM_MODE={mode_label}`)",
        "",
        f"- **Total cases:** {total}",
        f"- **Passed:** {passed} ({pct:.1f}%)",
        f"- **Failed:** {len(failed)}",
        "",
        "| ID | Category | Product | Expected | Actual | Risk Score | Result |",
        "|---|---|---|---|---|---:|---|",
    ]

    for r in rows:
        exp_str = f"{r['expected_decision']} {r['expected_reason_codes']}"
        act_str = f"{r['actual_decision']} {r['actual_reason_codes']}"
        status = "PASS" if r["passed"] else "FAIL"
        lines.append(
            f"| {r['id']} | {r['category']} | `{r['product_id']}` | `{exp_str}` | `{act_str}` | {r['risk_score']:.2f} | **{status}** |"
        )

    lines.append("")
    lines.append(f"### Failures (`LLM_MODE={mode_label}`)")
    if not failed:
        lines.append("0 failures — all 24 scripted cases matched the expected decision and reason codes.")
    else:
        for f in failed:
            lines.append(
                f"- **{f['id']} ({f['category']})**: expected `{f['expected_decision']} {f['expected_reason_codes']}`, "
                f"got `{f['actual_decision']} {f['actual_reason_codes']}` — {f['description']}"
            )
    lines.append("")
    return lines


def generate_eval_report() -> tuple[str, bool]:
    """Run eval suite (mock always; gemini if GEMINI_API_KEY is configured) and write docs/EVAL.md."""
    mock_rows = run_suite("mock")
    settings = get_settings()

    lines: list[str] = [
        "# Mandate Scripted Evaluation Report",
        "",
        "> **Scope & Honesty Note:** These are scripted cases written by the team. This measures that the system behaves as specified, not accuracy against real-world fraud.",
        "",
        "Each of the 24 cases in [`tests/eval_cases.json`](../tests/eval_cases.json) runs through `app/guard.py` and `app/risk.py` inside a clean temporary SQLite database with zero PayPal API calls.",
        "",
    ]
    lines.extend(_format_mode_section("mock", mock_rows))

    gemini_ok = True
    if settings.gemini_api_key:
        gemini_rows = run_suite("gemini")
        lines.extend(_format_mode_section("gemini", gemini_rows))
        gemini_ok = all(r["passed"] for r in gemini_rows)
    else:
        lines.extend(
            [
                "## Results (`LLM_MODE=gemini`)",
                "",
                "Skipped live Gemini evaluation because `GEMINI_API_KEY` is not set in `.env` (Task U2). "
                "Re-run `python scripts/run_eval.py` after adding `GEMINI_API_KEY` to compare live AI risk scores.",
                "",
            ]
        )

    report = "\n".join(lines)
    EVAL_MD_PATH.write_text(report, encoding="utf-8")
    all_mock_ok = all(r["passed"] for r in mock_rows)
    return report, (all_mock_ok and gemini_ok)


if __name__ == "__main__":
    _report, ok = generate_eval_report()
    print(f"Wrote {EVAL_MD_PATH}. All cases passed: {ok}")
    sys.exit(0 if ok else 1)
