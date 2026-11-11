# Project Handoff

*This document tracks project status, exact commands to run, and tasks that require the human engineer.*

## Current Status
- Building Milestone **M0 (Setup)** in mock mode (`LLM_MODE=mock`, `PAYPAL_MODE=mock`).
- Git repository initialized locally on branch `main`.

## Blockers Requiring Human Action
| ID | Task | Status | Needed For |
|---|---|---|---|
| U1 | Create PayPal Developer account, open **Apps & Credentials** -> **Sandbox**, create app, copy `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` into `.env`, plus note one Sandbox Personal (buyer) email & password. | Waiting on user | M6 (live PayPal sandbox) & final demo video |
| U2 | Create a Gemini API key in Google AI Studio and add `GEMINI_API_KEY` to `.env` (plus `LLM_MODE=gemini`). | Optional (mock works without it) | Live AI mode |
| U3 | Create an empty GitHub repository, verify `git push` works on this machine, and add the remote (`git remote add origin <repo-url>`). | Waiting on user | Remote GitHub push (`git push origin main`) |

## How to Run Locally (Windows PowerShell)
```powershell
.venv\Scripts\activate
uvicorn app.main:app --reload
```
Open `http://127.0.0.1:8000/health` to verify `{"status": "ok"}`.
