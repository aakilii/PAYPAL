# Mandate — Project Handoff

## 1. What Is Done (Verified)
All milestones **M0 through M11** are complete, committed locally on branch `main`, and verified in mock mode and HTTP-transport sandbox tests:
- **Backend & Safety:** Config with PayPal Sandbox-only startup check ([`app/config.py`](../app/config.py)), SQLite schema ([`app/db.py`](../app/db.py)), append-only SHA-256 hash-chained ledger with `verify()` and `DEV_MODE` tamper demo ([`app/ledger.py`](../app/ledger.py)).
- **AI & Guard Engine:** Single Gemini SDK wrapper with schema validation, retry, and mock fallbacks ([`app/llm.py`](../app/llm.py)), natural-language mandate parser with explicit assumption pills & confirmation ([`app/mandate.py`](../app/mandate.py)), authoritative catalog ([`app/catalog.py`](../app/catalog.py), [`data/catalog.json`](../data/catalog.json)), 8-rule deterministic Guard ([`app/guard.py`](../app/guard.py)), heuristic + AI seller risk scoring ([`app/risk.py`](../app/risk.py)), shopper agent + prompt-injection demo ([`app/agent.py`](../app/agent.py)).
- **PayPal & Disputes:** PayPal OAuth2 token cache, Orders v2 create/capture (`PayPal-Request-Id` idempotency, `experience_context`), Disputes v1 read + mock mode ([`app/paypal.py`](../app/paypal.py)), and ledger-only Dispute Defender with factual post-checks ([`app/defender.py`](../app/defender.py)).
- **Frontend & Eval:** Responsive 4-tab light/dark web UI ([`static/index.html`](../static/index.html), [`static/style.css`](../static/style.css), [`static/app.js`](../static/app.js)), 24-case scripted eval suite ([`scripts/run_eval.py`](../scripts/run_eval.py), [`docs/EVAL.md`](EVAL.md)), and full documentation ([`README.md`](../README.md), [`docs/ARCHITECTURE.md`](ARCHITECTURE.md), [`docs/LEARNING.md`](LEARNING.md), [`docs/DEMO_SCRIPT.md`](DEMO_SCRIPT.md), [`docs/SUBMISSION.md`](SUBMISSION.md), [`docs/BUILD_LOG.md`](BUILD_LOG.md), [`docs/DECISIONS.md`](DECISIONS.md)).
- **Tests:** `38 passed` in `pytest -v` and `24/24 PASS` in `scripts/run_eval.py` (under `LLM_MODE=mock`, `PAYPAL_MODE=mock`).

## 2. What Needs Your Action Before Demo & Submission
Because `U1`, `U2`, and `U3` require your accounts, live PayPal Sandbox checkout, live Gemini calls, and pushing to GitHub could not be executed yet:

| ID | Task | How to Complete |
|---|---|---|
| **U1** | **PayPal Sandbox App & Buyer Account** | 1. Log in to [developer.paypal.com](https://developer.paypal.com) -> **Apps & Credentials** -> switch to **Sandbox** -> create an app.<br>2. Copy **Client ID** and **Client Secret** into `.env` as `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`, and set `PAYPAL_MODE=sandbox`.<br>3. Go to **Sandbox -> Accounts** and copy one **Personal** (buyer) account email and password for approving checkout during your demo. |
| **U2** | **Gemini API Key (Optional)** | Get an API key from Google AI Studio, put it in `.env` as `GEMINI_API_KEY=...`, and set `LLM_MODE=gemini`. (Mock mode works without it.) |
| **U3** | **Push to GitHub** | Create an empty repository on GitHub, then run:<br>`git remote add origin <YOUR_REPO_URL>`<br>`git push -u origin main --tags` |

## 3. Exact Commands to Run (Windows PowerShell)

```powershell
# 1. Activate virtual environment
.venv\Scripts\Activate.ps1

# 2. Run all unit & integration tests
pytest -v

# 3. Run the 24-case scripted eval (also re-runs Gemini eval if GEMINI_API_KEY is set)
python scripts/run_eval.py

# 4. Start the app
uvicorn app.main:app --reload
```
Then open `http://127.0.0.1:8000/`.

*(On macOS/Linux, activate with `source .venv/bin/activate`.)*

## 4. Remaining Checklist for You (Before 13 Nov 2026, 1:30 AM IST)
1. **Run the app yourself** and click through every scenario once (`Normal buy`, `Over the cap`, `Fake seller` -> **Approvals** Deny/Approve, `Injection attack`, `Kill switch`, `Ledger` Verify + Tamper demo, `Merchant` Simulate dispute -> Build defense).
2. **Record the 2-minute demo** following [`docs/DEMO_SCRIPT.md`](DEMO_SCRIPT.md) with `PAYPAL_MODE=sandbox` enabled and a live Sandbox buyer approval; upload it where Devpost expects (check the Rules tab).
3. **Fill out the Devpost submission form** using [`docs/SUBMISSION.md`](SUBMISSION.md); attach your GitHub repository link and video link; verify every Devpost rule requirement.
4. **Submit early**—avoid waiting until the final hour before **13 Nov 2026, 1:30 AM IST** (12:00 PM PST on 12 Nov).
5. **Study the codebase** using [`docs/LEARNING.md`](LEARNING.md) and [`docs/BUILD_LOG.md`](BUILD_LOG.md).

## 5. Known Limitations
- **Tamper-evident, not tamper-proof:** Anyone with full database write access could recompute the whole hash chain from `GENESIS`; production requires external hash anchoring or HSM signatures.
- **Confirmed by user, not cryptographically signed:** Mandates store `confirmed_at` rather than a private-key signature.
- **Simulated catalog and risk signals:** Store prices and seller age/verification metadata come from [`data/catalog.json`](../data/catalog.json).
- **Disputes may be simulated:** PayPal Sandbox `GET /v1/customer/disputes` is checked when `PAYPAL_MODE=sandbox`, falling back to `source: simulated` disputes when none exist.
- **No real money:** Startup checks block any `PAYPAL_BASE_URL` other than `https://api-m.sandbox.paypal.com`.
- **Scripted eval scope:** [`docs/EVAL.md`](EVAL.md) verifies specification behavior on 24 team-written cases, not real-world fraud detection accuracy.
