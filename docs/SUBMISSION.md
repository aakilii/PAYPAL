# Mandate — Devpost Submission Draft

**Tagline:** Agents can spend safely. Merchants can prove it.

## Inspiration
AI shopping agents are moving quickly toward autonomous checkout—63% of merchants are exploring agentic AI payments, yet fraud-liability questions remain unresolved (Merchant Risk Council 2026 report, as cited by Chargeflow). At the same time, PayPal's executives have noted security as the biggest risk to agent-shopping adoption (reported by Tearsheet, from Money20/20), and PayPal's CTO said at Ai4 2026 that trust is the biggest obstacle to AI adoption (PayPal newsroom). On the consumer side, ECOMMPAY consumer research found about 21.3% of consumers would not let an agent spend for them at all, and about 44.3% would cap autonomous spending around £100/€100. Meanwhile, merchants already struggle with friendly fraud—estimated at around 75% of eCommerce disputes—winning roughly 8.1% of manually contested disputes (Chargeflow research, as summarized by beancount.io), while about 23.5% of merchants use five or more separate tools to gather dispute evidence (eCommerceNews). We built **Mandate** so shoppers get hard deterministic limits and merchants get tamper-evident proof in one place.

## What It Does
1. **Plain-English Mandates with Explicit Confirmation:** A user writes a spending instruction (e.g., *"get me a black jacket under $35"*). AI parses it into a structured draft, clearly flags any assumed defaults (daily cap, allowed sellers, expiry), and waits for the user to confirm.
2. **Deterministic Guard + Risk Scoring:** The shopper agent proposes only a `product_id`. Plain Python code loads the authoritative price and seller from the catalog and runs 8 rule checks (`KILL_SWITCH`, `MANDATE_INACTIVE`, `MANDATE_EXPIRED`, `OVER_ITEM_CAP`, `OVER_DAY_CAP`, `ITEM_MISMATCH`, `UNKNOWN_SELLER`, `HIGH_RISK`). Safe purchases are `APPROVED`; suspicious sellers or high-risk listings are `HELD` for human-in-the-loop approval; rule violations are `BLOCKED` before any AI risk call or payment call is spent.
3. **Prompt-Injection Defense Demo:** A simulated malicious product page tries to trick a gullible AI shopper into buying a $199 bundle. Because limits live in code rather than prompts, the Guard blocks it with `OVER_ITEM_CAP`.
4. **PayPal Sandbox Checkout & Tamper-Evident Ledger:** Approved orders go through PayPal Sandbox checkout & capture, and every event (mandate confirmation, Guard decision, human resolution, PayPal order & capture, disputes) appends to a SHA-256 hash-chained ledger.
5. **Dispute Defender:** When a dispute arrives, an AI defender constructs an evidence packet strictly from ledger records, assigns a `STRONG`, `WEAK`, or `NO_RECORD` strength rating, drafts a factual reply (with deterministic post-checks), and advises merchants not to contest if evidence is weak or tampered.

## How We Built It (PayPal & AI Usage)
**How Mandate uses PayPal and AI:** Mandate integrates with PayPal's Sandbox REST API (`https://api-m.sandbox.paypal.com`—enforced via a startup security check) using cached OAuth2 tokens (`POST /v1/oauth2/token`), Orders v2 creation (`POST /v2/checkout/orders` with `payment_source.paypal.experience_context` and `PayPal-Request-Id` idempotency keys), buyer redirect checkout & order capture (`POST /v2/checkout/orders/{id}/capture`), and Customer Disputes v1 reads (`GET /v1/customer/disputes`). Alongside PayPal, Mandate routes all four AI tasks through a single `google-genai` Gemini wrapper with Pydantic JSON schemas, retry, and deterministic mock fallbacks: (1) parsing plain-English requests into structured mandates, (2) scoring untrusted seller descriptions for risk signals alongside deterministic heuristics, (3) running the shopper agent in both untrusted-data safe mode and a gullible prompt-injection simulation, and (4) drafting factual dispute replies grounded strictly in ledger events.

**Stack:** Python 3.11+, FastAPI, Uvicorn, HTTPX, Pydantic, SQLite (`sqlite3`), Google GenAI SDK (`google-genai`), PayPal Sandbox REST API, vanilla HTML/CSS/JS, and Pytest.

## Challenges We Ran Into
- Keeping AI strictly separated from rule enforcement so prompt injections cannot bypass caps or alter prices.
- Preventing AI hallucinations in dispute replies by forcing every packet field to come exclusively from the ledger and adding deterministic post-checks for order ID, dollar amount, and non-contest warnings.
- Designing safe dual-mode switches (`LLM_MODE=mock|gemini` and `PAYPAL_MODE=mock|sandbox`) so every workflow is reproducible offline and testable against real PayPal Sandbox checkout.

## Accomplishments That We're Proud Of
- All 24 scripted evaluation cases in `tests/eval_cases.json` pass (`docs/EVAL.md`), alongside 43 unit and API integration tests.
- Clean zero-framework responsive dashboard supporting light/dark mode, live 2s ledger activity polling, human approve/deny queue, Verified Mandate merchant badges, and a one-click ledger tamper demo.
- Honest labeling of every real vs. simulated boundary in both the UI and docs.

## What We Learned
- Rules belong in deterministic code; AI belongs on fuzzy parsing, risk hints, and text drafting.
- Idempotency keys (`PayPal-Request-Id`) and separated database transactions prevent half-written order states when payment calls fail.
- Hash-chained ledgers provide *tamper-evident* history, not *tamper-proof* storage without external hash anchoring or signatures.

## What Is Real vs. Simulated
- **Real:** FastAPI backend, SQLite persistence, SHA-256 hash chain & verification, deterministic Guard & heuristic risk engine, optional live Gemini API calls, and live PayPal Sandbox OAuth/Orders/Disputes API integration.
- **Simulated:** Product catalog & seller verification/domain-age signals (`data/catalog.json`), `mock` mode fallbacks when API keys are omitted, and simulated PayPal-shaped disputes when the Sandbox account has no live disputes (always labeled `source: simulated`). Mandates are user-confirmed rather than cryptographically signed.

## What's Next
- Replace user confirmation timestamps with WebAuthn/passkey cryptographic signatures.
- Anchor ledger `head_hash` snapshots externally and verify PayPal webhooks with PayPal's certificate verification API.
- Connect live merchant inventory and PayPal MCP tools (`https://mcp.sandbox.paypal.com`) for real-time catalog quotes.

## Built With
`python`, `fastapi`, `uvicorn`, `httpx`, `pydantic`, `sqlite`, `google-genai`, `gemini`, `paypal-rest-api`, `paypal-sandbox`, `html`, `css`, `javascript`, `pytest`
