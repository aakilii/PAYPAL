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

## M5: Agent, injection demo, kill switch
What I built: Created [`app/agent.py`](file:///c:/Users/Aakil/paypal/app/agent.py) connecting the shopper agent (which picks only a `product_id` and never reports prices) to the deterministic Guard, plus an intentional gullible prompt-injection mode alongside a safe-prompt comparison. Refined `mock_pick_product` in [`app/llm.py`](file:///c:/Users/Aakil/paypal/app/llm.py) so normal requests prefer verified clean listings while injection attacks pick the $199 bundle trap. Added `POST /api/agent/run`, `GET/POST /api/kill-switch`, and `GET /api/activity?since=` in [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py), plus tests in [`tests/test_agent.py`](file:///c:/Users/Aakil/paypal/tests/test_agent.py).
Why it matters for the project: Proves that even if a malicious seller page tricks an AI shopping agent into picking a $199 bundle, hard code limits (`OVER_ITEM_CAP`) and the emergency kill switch still protect the user's money.
Terms learned:
- *prompt injection* = hiding fake commands inside external text (like a product description) to trick an AI into disobeying its user.
- *kill switch* = an emergency global toggle that immediately freezes all agent purchases regardless of any mandate.
- *activity feed (polling)* = the browser asking `/api/activity?since=...` every 2 seconds for new ledger events so updates appear live.
How to try it: Run `.venv\Scripts\python.exe -m pytest tests/test_agent.py -v` or `POST /api/agent/run` with `{"mandate_id": "...", "request_text": "get me a black jacket", "attack": "injection"}`.

## M6: PayPal
What I built: Created [`app/paypal.py`](file:///c:/Users/Aakil/paypal/app/paypal.py) implementing PayPal OAuth2 token caching (`/v1/oauth2/token`), Orders v2 creation (`/v2/checkout/orders`) with `PayPal-Request-Id` idempotency headers and `payment_source.paypal.experience_context`, order capture (`/v2/checkout/orders/{id}/capture`), sandbox dispute reading (`/v1/customer/disputes`), and full mock-mode simulation (`MOCK-...`). Wired `/paypal/return`, `/paypal/cancel`, and `/api/orders` (with the `verified_mandate` badge check) into [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py), logging `PAYPAL_ERROR` cleanly on any failure without leaving half-written rows. Added 4 tests in [`tests/test_paypal.py`](file:///c:/Users/Aakil/paypal/tests/test_paypal.py).
Why it matters for the project: Connects Guard-approved purchases (and human-approved holds) directly to real PayPal Sandbox checkout & capture, while staying testable offline in mock mode.
Terms learned:
- *OAuth token* = a temporary access pass obtained by exchanging your app's Client ID and Secret so you don't send secrets on every API request.
- *idempotency key (`PayPal-Request-Id`)* = a unique label attached to a payment request so network retries never charge or create the same order twice.
- *order capture* = the final PayPal step after the buyer approves checkout that actually moves the funds and produces a capture ID.
How to try it: Run `.venv\Scripts\python.exe -m pytest tests/test_paypal.py -v` (works in mock mode; switch `PAYPAL_MODE=sandbox` after filling Task U1 in `.env`).

## M7: Frontend
What I built: Created [`static/index.html`](file:///c:/Users/Aakil/paypal/static/index.html), [`static/style.css`](file:///c:/Users/Aakil/paypal/static/style.css), and [`static/app.js`](file:///c:/Users/Aakil/paypal/static/app.js) with zero build tools or frameworks. Built the responsive four-tab UI (`Shop`, `Approvals`, `Merchant`, `Ledger`) supporting automatic light/dark mode, visible keyboard focus, live mode badges, the global Kill Switch banner, editable parsed mandates with `Assumed` pills, all four agent demo scenarios, a 2-second polling live activity feed, human approve/deny controls, and the Ledger verify/tamper demo. Mounted `/static` and `/` in [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py) and added [`tests/test_ui.py`](file:///c:/Users/Aakil/paypal/tests/test_ui.py).
Why it matters for the project: Gives users, judges, and merchants an interactive dashboard to drive every scenario, watch the Guard block injection attacks, approve PayPal orders, and verify the hash chain visually.
Terms learned:
- *CSS variables & `prefers-color-scheme`* = reusable color tokens in CSS that automatically switch between light and dark themes based on the user's operating system setting.
- *XSS escaping (`escapeHtml`)* = turning `<` and `>` into safe text before rendering user or seller strings into HTML so untrusted text can never run scripts in the browser.
How to try it: Start `uvicorn app.main:app --reload` and open `http://127.0.0.1:8000/` in your browser.

## M8: Dispute defender and merchant view
What I built: Created [`app/defender.py`](file:///c:/Users/Aakil/paypal/app/defender.py) which constructs dispute evidence packets strictly from immutable ledger events, assigns evidence strength (`STRONG`, `WEAK`, or `NO_RECORD`), and drafts factual merchant replies with a strict post-check ensuring order ID, amount, and "should not contest" guidance are never omitted. Wired `/api/disputes/simulate`, `/api/disputes`, and `/api/disputes/{id}/defend` into [`app/main.py`](file:///c:/Users/Aakil/paypal/app/main.py), updated [`static/app.js`](file:///c:/Users/Aakil/paypal/static/app.js) to auto-sync sandbox disputes when `PAYPAL_MODE=sandbox`, and added 4 comprehensive tests in [`tests/test_defender.py`](file:///c:/Users/Aakil/paypal/tests/test_defender.py).
Why it matters for the project: Solves the second core problem—giving merchants verifiable proof from the ledger to fight friendly fraud when evidence is strong, and telling them honestly not to contest when proof is missing or tampered.
Terms learned:
- *friendly fraud* = when a customer makes or approves a purchase and later files a dispute claiming they never authorized it.
- *evidence packet* = the structured bundle of mandate confirmation, request text, Guard checks, PayPal IDs, and ledger verification used to answer a dispute.
- *post-check* = validating an AI-written reply in deterministic code afterward to make sure required facts (like order ID and dollar amount) were actually included.
How to try it: Run `.venv\Scripts\python.exe -m pytest tests/test_defender.py -v` or open the **Merchant** tab and click **Simulate dispute** -> **Build defense**.

## M9: Eval and tests
What I built: Created [`tests/eval_cases.json`](file:///c:/Users/Aakil/paypal/tests/eval_cases.json) with 24 scripted scenarios covering normal approvals (6), over item cap (4), over daily cap (2), unknown sellers (3), high-risk on allowed sellers (2), injection attacks (2), expired mandates (2), kill switch (1), and item mismatches (2). Built [`scripts/run_eval.py`](file:///c:/Users/Aakil/paypal/scripts/run_eval.py) to execute all 24 cases in clean temporary databases (plus live Gemini if `GEMINI_API_KEY` is configured) and generate [`docs/EVAL.md`](file:///c:/Users/Aakil/paypal/docs/EVAL.md) with an explicit honesty disclaimer. Added [`tests/test_eval.py`](file:///c:/Users/Aakil/paypal/tests/test_eval.py) so `pytest` verifies all 24 cases continuously.
Why it matters for the project: Proves every deterministic Guard rule, daily cap accumulation, and seller risk boundary behaves exactly as specified across 24 reproducible cases.
Terms learned:
- *eval (evaluation suite)* = a fixed set of known test scenarios run automatically to verify system behavior does not regress when code changes.
- *regression* = an old bug returning or a working rule silently breaking after a later edit.
How to try it: Run `.venv\Scripts\python.exe scripts/run_eval.py` and read [`docs/EVAL.md`](file:///c:/Users/Aakil/paypal/docs/EVAL.md).
