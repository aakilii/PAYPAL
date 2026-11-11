# Mandate — Project Handoff

## 1. What Is Done (Verified)
All milestones **M0 through M11** plus live deployment readiness are complete and pushed to `https://github.com/aakilii/PAYPAL.git` (branch `main`, tagged `v1.0` / updated):
- **Backend & Safety:** Config with PayPal Sandbox-only startup check ([`app/config.py`](../app/config.py)), SQLite schema with WAL mode & busy timeout ([`app/db.py`](../app/db.py)), append-only SHA-256 hash-chained ledger with `verify()` and `DEV_MODE` tamper demo ([`app/ledger.py`](../app/ledger.py)).
- **AI & Guard Engine:** Single Gemini SDK wrapper with schema validation, retry, and mock fallbacks ([`app/llm.py`](../app/llm.py)), natural-language mandate parser with explicit assumption pills & confirmation ([`app/mandate.py`](../app/mandate.py)), authoritative catalog ([`app/catalog.py`](../app/catalog.py), [`data/catalog.json`](../data/catalog.json)), 8-rule deterministic Guard ([`app/guard.py`](../app/guard.py)), heuristic + AI seller risk scoring ([`app/risk.py`](../app/risk.py)), shopper agent + prompt-injection demo ([`app/agent.py`](../app/agent.py)).
- **PayPal & Disputes:** PayPal OAuth2 token cache, Orders v2 create/capture (`PayPal-Request-Id` idempotency, `experience_context`, `PAYER_ACTION_REQUIRED` support, dynamic reverse-proxy callback URL detection), Disputes v1 read + mock mode ([`app/paypal.py`](../app/paypal.py)), and ledger-only Dispute Defender with factual post-checks ([`app/defender.py`](../app/defender.py)).
- **Live PayPal Sandbox Verification:** Verified OAuth2 token exchange, real Sandbox Order creation (`status=PAYER_ACTION_REQUIRED`), and Sandbox Disputes v1 API read against `https://api-m.sandbox.paypal.com`.
- **Cloud Deployment Configs:** Added [`render.yaml`](../render.yaml) and [`Procfile`](../Procfile) for quick Render or Railway deployment.
- **Tests:** `39 passed` in `pytest -v` and `24/24 PASS` in `scripts/run_eval.py`.

## 2. Task Status
| ID | Task | Status | Notes |
|---|---|---|---|
| **U1** | PayPal Sandbox App & Buyer Account | **Done** | Local `.env` configured with `PAYPAL_MODE=sandbox` and verified live against `https://api-m.sandbox.paypal.com`. |
| **U2** | Gemini API Key (Optional) | Optional | Add `GEMINI_API_KEY` to `.env` (and host env vars) + set `LLM_MODE=gemini` if you want live Gemini calls. |
| **U3** | GitHub Repo & Push | **Done** | Remote `origin` set to `https://github.com/aakilii/PAYPAL.git` and pushed (`main` + tags). |

## 3. Deploy Live for a Judge (Render Blueprint — ~3 Minutes)
1. Sign in to [render.com](https://render.com) with GitHub -> **New +** -> **Blueprint** (or **Web Service**).
2. Select repository **`aakilii/PAYPAL`** (Render detects [`render.yaml`](../render.yaml) automatically).
3. When prompted for `sync: false` environment variables:
   - `PAYPAL_CLIENT_ID`: paste your PayPal Sandbox Client ID from `.env`.
   - `PAYPAL_CLIENT_SECRET`: paste your PayPal Sandbox Client Secret from `.env`.
   - `APP_BASE_URL`: leave empty on first deploy or set to your Render URL (`https://<your-app>.onrender.com`); [`app/main.py`](../app/main.py) also auto-detects `X-Forwarded-Proto` + `Host` if left empty.
   - `GEMINI_API_KEY`: optional (leave blank if `LLM_MODE=mock`, or set key and change `LLM_MODE=gemini`).
4. Click **Apply** and wait ~2 minutes for `/health` to go green.
5. **Test once before giving the URL to a judge**, and if you click **Tamper demo** or **Kill switch** during testing, re-enable/reset before submitting.
6. In Devpost's **Testing Instructions**, provide your Sandbox Personal buyer test login (`sb-v3n4653749622@personal.example.com` + password) so judges can approve real Sandbox checkout themselves.

> **Security Tip:** After the hackathon ends (or if you ever accidentally pasted credentials anywhere public), click **Regenerate** on your Client Secret inside the PayPal Developer Dashboard.

## 4. Exact Commands to Run Locally (Windows PowerShell)
```powershell
.venv\Scripts\Activate.ps1
pytest -v
python scripts/run_eval.py
uvicorn app.main:app --reload
```
Open `http://127.0.0.1:8000/`. *(On macOS/Linux, activate with `source .venv/bin/activate`.)*

## 5. Remaining Checklist (Before 13 Nov 2026, 1:30 AM IST)
1. Complete **one live Sandbox buyer checkout** locally or on your deployed URL (`Approve in PayPal` -> log in with your Sandbox Personal buyer account -> verify redirect back to **Merchant** tab with **Verified mandate**).
2. Record the **2-minute demo** following [`docs/DEMO_SCRIPT.md`](DEMO_SCRIPT.md) with `PAYPAL_MODE=sandbox`.
3. Fill out Devpost using [`docs/SUBMISSION.md`](SUBMISSION.md) + live URL + GitHub link + video link, and submit early.
4. Read [`docs/LEARNING.md`](LEARNING.md) and [`docs/BUILD_LOG.md`](BUILD_LOG.md) to study every module.

## 6. Known Limitations
- **Tamper-evident, not tamper-proof:** Anyone with full database write access could recompute the whole hash chain from `GENESIS`; production requires external hash anchoring or HSM signatures.
- **Confirmed by user, not cryptographically signed:** Mandates store `confirmed_at` rather than a private-key signature.
- **Simulated catalog and risk signals:** Store prices and seller age/verification metadata come from [`data/catalog.json`](../data/catalog.json).
- **Disputes may be simulated:** PayPal Sandbox `GET /v1/customer/disputes` is checked when `PAYPAL_MODE=sandbox`, falling back to `source: simulated` disputes when none exist.
- **No real money:** Startup checks block any `PAYPAL_BASE_URL` other than `https://api-m.sandbox.paypal.com`.
- **Scripted eval scope:** [`docs/EVAL.md`](EVAL.md) verifies specification behavior on 24 team-written cases, not real-world fraud detection accuracy.
