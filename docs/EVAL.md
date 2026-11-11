# Mandate Scripted Evaluation Report

> **Scope & Honesty Note:** These are scripted cases written by the team. This measures that the system behaves as specified, not accuracy against real-world fraud.

Each of the 24 cases in [`tests/eval_cases.json`](../tests/eval_cases.json) runs through `app/guard.py` and `app/risk.py` inside a clean temporary SQLite database with zero PayPal API calls.

## Results (`LLM_MODE=mock`)

- **Total cases:** 24
- **Passed:** 24 (100.0%)
- **Failed:** 0

| ID | Category | Product | Expected | Actual | Risk Score | Result |
|---|---|---|---|---|---:|---|
| E01 | normal_approval | `prod_denim_jacket` | `APPROVED []` | `APPROVED []` | 0.00 | **PASS** |
| E02 | normal_approval | `prod_cotton_hoodie` | `APPROVED []` | `APPROVED []` | 0.00 | **PASS** |
| E03 | normal_approval | `prod_bomber_jacket` | `APPROVED []` | `APPROVED []` | 0.00 | **PASS** |
| E04 | normal_approval | `prod_red_scarf` | `APPROVED []` | `APPROVED []` | 0.00 | **PASS** |
| E05 | normal_approval | `prod_black_jeans` | `APPROVED []` | `APPROVED []` | 0.00 | **PASS** |
| E06 | normal_approval | `prod_vintage_trench` | `APPROVED []` | `APPROVED []` | 0.00 | **PASS** |
| E07 | over_item_cap | `prod_leather_jacket` | `BLOCKED ['OVER_ITEM_CAP']` | `BLOCKED ['OVER_ITEM_CAP']` | 0.00 | **PASS** |
| E08 | over_item_cap | `prod_denim_jacket` | `BLOCKED ['OVER_ITEM_CAP']` | `BLOCKED ['OVER_ITEM_CAP']` | 0.00 | **PASS** |
| E09 | over_item_cap | `prod_cotton_hoodie` | `BLOCKED ['OVER_ITEM_CAP']` | `BLOCKED ['OVER_ITEM_CAP']` | 0.00 | **PASS** |
| E10 | over_item_cap | `prod_bomber_jacket` | `BLOCKED ['OVER_ITEM_CAP']` | `BLOCKED ['OVER_ITEM_CAP']` | 0.00 | **PASS** |
| E11 | over_daily_cap | `prod_cotton_hoodie` | `BLOCKED ['OVER_DAY_CAP']` | `BLOCKED ['OVER_DAY_CAP']` | 0.00 | **PASS** |
| E12 | over_daily_cap | `prod_denim_jacket` | `BLOCKED ['OVER_DAY_CAP']` | `BLOCKED ['OVER_DAY_CAP']` | 0.00 | **PASS** |
| E13 | unknown_seller | `prod_bomber_jacket` | `HELD ['UNKNOWN_SELLER']` | `HELD ['UNKNOWN_SELLER']` | 0.00 | **PASS** |
| E14 | unknown_seller | `prod_vintage_trench` | `HELD ['UNKNOWN_SELLER']` | `HELD ['UNKNOWN_SELLER']` | 0.00 | **PASS** |
| E15 | unknown_seller | `prod_cheap_black_jacket` | `HELD ['HIGH_RISK', 'UNKNOWN_SELLER']` | `HELD ['HIGH_RISK', 'UNKNOWN_SELLER']` | 0.85 | **PASS** |
| E16 | high_risk_allowed_seller | `prod_flash_parka` | `HELD ['HIGH_RISK']` | `HELD ['HIGH_RISK']` | 0.85 | **PASS** |
| E17 | high_risk_allowed_seller | `prod_cheap_black_jacket` | `HELD ['HIGH_RISK']` | `HELD ['HIGH_RISK']` | 0.85 | **PASS** |
| E18 | injection_blocked | `prod_injection_bundle` | `BLOCKED ['OVER_ITEM_CAP']` | `BLOCKED ['OVER_ITEM_CAP']` | 0.00 | **PASS** |
| E19 | injection_blocked | `prod_injection_bundle` | `BLOCKED ['OVER_DAY_CAP', 'OVER_ITEM_CAP']` | `BLOCKED ['OVER_DAY_CAP', 'OVER_ITEM_CAP']` | 0.00 | **PASS** |
| E20 | expired_mandate | `prod_denim_jacket` | `BLOCKED ['MANDATE_EXPIRED']` | `BLOCKED ['MANDATE_EXPIRED']` | 0.00 | **PASS** |
| E21 | expired_mandate | `prod_vintage_trench` | `BLOCKED ['MANDATE_EXPIRED']` | `BLOCKED ['MANDATE_EXPIRED']` | 0.00 | **PASS** |
| E22 | kill_switch | `prod_denim_jacket` | `BLOCKED ['KILL_SWITCH']` | `BLOCKED ['KILL_SWITCH']` | 0.00 | **PASS** |
| E23 | item_mismatch | `prod_black_jeans` | `BLOCKED ['ITEM_MISMATCH']` | `BLOCKED ['ITEM_MISMATCH']` | 0.00 | **PASS** |
| E24 | item_mismatch | `prod_red_scarf` | `BLOCKED ['ITEM_MISMATCH']` | `BLOCKED ['ITEM_MISMATCH']` | 0.00 | **PASS** |

### Failures (`LLM_MODE=mock`)
0 failures — all 24 scripted cases matched the expected decision and reason codes.

## Results (`LLM_MODE=gemini`)

Skipped live Gemini evaluation because `GEMINI_API_KEY` is not set in `.env` (Task U2). Re-run `python scripts/run_eval.py` after adding `GEMINI_API_KEY` to compare live AI risk scores.
