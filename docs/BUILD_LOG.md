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
