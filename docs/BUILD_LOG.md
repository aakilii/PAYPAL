# Mandate Build Log

A plain-English diary of every milestone built, tested, and verified.

## M0: Setup
What I built: Created the project directory structure, Python 3.11 virtual environment, `requirements.txt`, `.gitignore`, `.env.example`, and an untracked local `.env` running in safe mock mode. Built [`app/config.py`](file:///c:/Users/Aakil/paypal/app/config.py) with a strict startup security check that refuses to boot unless PayPal points only to the Sandbox URL, plus a minimal FastAPI server in [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py) exposing `/health` and `/api/config`. Also created initial docs (`README.md`, `DECISIONS.md`, `HANDOFF.md`) and M0 pytest checks.
Why it matters for the project: Gives us a clean, reproducible foundation where secrets stay out of git and the app can never accidentally touch real money on live PayPal.
Terms learned:
- *virtual environment (venv)* = an isolated Python folder so this project's packages don't clash with other projects on your computer.
- *environment variable (.env)* = a secret or setting stored outside source code so keys never get uploaded to GitHub.
- *sandbox* = PayPal's fake-money test server where developers can test payments safely without real bank transfers.
- *liveness probe (`/health`)* = a simple URL that answers `{"status": "ok"}` to prove the server is running.
How to try it: `.venv\Scripts\python.exe -m uvicorn app.main:app --reload` then visit `http://127.0.0.1:8000/health` (or run `.venv\Scripts\python.exe -m pytest -v`).

## M1: Database and ledger
What I built: Created [`app/db.py`](file:///c:/Users/Aakil/paypal/app/db.py) with all six SQLite tables (`mandates`, `attempts`, `orders`, `disputes`, `settings`, `ledger`) and safe connection helpers. Built [`app/ledger.py`](file:///c:/Users/Aakil/paypal/app/ledger.py) implementing an append-only SHA-256 hash chain starting at `GENESIS`, a `verify()` walker that detects any broken link or edited record, and a `DEV_MODE`-guarded `dev_tamper_entry()` demo function. Wired `/api/ledger`, `/api/ledger/verify`, and `/api/dev/tamper/{seq}` into [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py) and added 5 unit/API tests in [`tests/test_ledger.py`](file:///c:/Users/Aakil/paypal/tests/test_ledger.py).
Why it matters for the project: Gives Mandate an audit trail where every decision, confirmation, and payment links cryptographically to the previous event, proving tampering immediately if history is altered.
Terms learned:
- *ledger* = an append-only chronological log where new entries are added to the end and old ones are never edited.
- *hash (SHA-256)* = a one-way digital fingerprint of text; changing even one character produces a completely different fingerprint.
- *hash chain* = linking every ledger entry to the previous entry's hash so editing an old entry breaks every entry after it.
- *tamper-evident* = a system that makes tampering immediately visible when verified (distinct from *tamper-proof*, which prevents editing completely).
How to try it: Run `.venv\Scripts\python.exe -m pytest -v` (8 tests passing) or visit `http://127.0.0.1:8000/api/ledger/verify`.

## M2: Mandate
What I built: Created [`app/llm.py`](file:///c:/Users/Aakil/paypal/app/llm.py) as the single wrapper for Gemini SDK calls (using `response_schema` JSON output, one automatic retry, and deterministic mock fallbacks) containing all four prompt templates. Built [`app/mandate.py`](file:///c:/Users/Aakil/paypal/app/mandate.py) to parse plain-English shopping requests into editable drafts, automatically filling omitted limits (2x daily cap, `["thrift-shop"]`, 24-hour expiry) and listing every default in `assumptions`. Added `/api/mandates/parse`, `/api/mandates`, and `/api/mandates/active` to [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py), recording `MANDATE_CONFIRMED` in the ledger upon confirmation.
Why it matters for the project: Translates messy human instructions into explicit, verifiable spending limits that the user reviews and confirms before the agent is allowed to act.
Terms learned:
- *mandate* = a structured spending permission specifying what item keywords, dollar caps, allowed sellers, and expiry time a user confirmed.
- *mock mode* = a deterministic offline mode that simulates AI or external APIs using predictable rules so the project works without live API keys.
- *schema validation (Pydantic)* = checking that AI JSON output has the exact field names and number/string types our code expects before trusting it.
- *assumptions* = default limits the parser filled in automatically when the user left them out, shown clearly so the user can edit them before confirming.
How to try it: Run `.venv\Scripts\python.exe -m pytest tests/test_mandate.py -v` or `POST /api/mandates/parse` with `{"text": "get me a black jacket under $35"}`.

## M3: Catalog and guard
What I built: Created [`data/catalog.json`](file:///c:/Users/Aakil/paypal/data/catalog.json) with 12 products across four sellers (trusted stores, a brand-new unverified domain, and a listing carrying a hidden prompt-injection attack) and [`app/catalog.py`](file:///c:/Users/Aakil/paypal/app/catalog.py) to load authoritative prices and seller metadata. Built [`app/guard.py`](file:///c:/Users/Aakil/paypal/app/guard.py) implementing all eight deterministic rule checks (`KILL_SWITCH`, `MANDATE_INACTIVE`, `MANDATE_EXPIRED`, `OVER_ITEM_CAP`, `OVER_DAY_CAP`, `ITEM_MISMATCH`, `UNKNOWN_SELLER`, `HIGH_RISK`), strict precedence (`BLOCKED` > `HELD` > `APPROVED`), and the cost-saving rule that skips AI risk checks when already blocked. Added `GET /api/catalog` to [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py) and 10 tests in [`tests/test_guard.py`](file:///c:/Users/Aakil/paypal/tests/test_guard.py).
Why it matters for the project: Guarantees the AI agent can never lie about prices or sellers and can never talk its way past hard spending limits, because deterministic code enforces the rules from catalog ground truth.
Terms learned:
- *guard* = deterministic Python rule checks that verify every purchase before any money moves.
- *authoritative source (ground truth)* = loading the real price and seller directly from `catalog.json` rather than trusting whatever an AI agent claims.
- *decision precedence* = strict priority order where any hard `BLOCKED` rule overrides `HELD`, and `APPROVED` only happens when every check passes.
- *allowlist* = an explicit list of approved sellers (`allowed_sellers`) used to hold unknown shops for human review.
How to try it: Run `.venv\Scripts\python.exe -m pytest tests/test_guard.py -v` or `GET http://127.0.0.1:8000/api/catalog`.

## M4: Risk and holds
What I built: Created [`app/risk.py`](file:///c:/Users/Aakil/paypal/app/risk.py) combining a deterministic seller-risk heuristic (new domain under 30 days +0.30, price below 40% of market value +0.25, urgency/injection text +0.15, unverified seller +0.15) with live Gemini risk scoring (taking the higher score). Wired `evaluate_risk` into [`app/guard.py`](file:///c:/Users/Aakil/paypal/app/guard.py) and added human-in-the-loop resolution helpers (`list_holds`, `resolve_hold`) that enforce daily caps and append `HUMAN_RESOLVED` to the ledger. Added `GET /api/holds`, `POST /api/holds/{attempt_id}/approve`, and `POST /api/holds/{attempt_id}/deny` in [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py) plus tests in [`tests/test_risk_holds.py`](file:///c:/Users/Aakil/paypal/tests/test_risk_holds.py).
Why it matters for the project: Keeps suspicious sellers or abnormally cheap listings from spending money automatically, pausing them for a human to review and approve or deny with full reasons.
Terms learned:
- *human-in-the-loop* = pausing an automated action so a real person makes the final approve/deny call before money moves.
- *heuristic* = a simple, predictable math rule (like domain age < 30 days) that scores risk consistently without needing AI.
- *untrusted input* = data coming from an outside website or seller listing that must never be treated as system instructions.
How to try it: Run `.venv\Scripts\python.exe -m pytest tests/test_risk_holds.py -v` or query `GET http://127.0.0.1:8000/api/holds`.
