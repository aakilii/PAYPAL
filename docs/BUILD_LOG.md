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
