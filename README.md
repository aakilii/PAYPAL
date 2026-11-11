# Mandate

**Agents can spend safely. Merchants can prove it.**

Mandate is a safety and proof layer between an AI shopping agent and PayPal. A user writes a spending request in plain English, confirms a structured mandate with clear limits, and deterministic rules check every purchase before any money moves through PayPal Sandbox. Every event is written to a tamper-evident, hash-chained ledger used to defend against disputes.

> *Milestone M0 stub — full documentation, screenshots, and quick start guide will be completed in M10.*

## Quick Run

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
