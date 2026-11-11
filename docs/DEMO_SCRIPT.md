# Mandate — 2-Minute Demo Video Script

**Before recording:**
1. Set `PAYPAL_MODE=sandbox` in `.env` with your Sandbox Client ID and Secret (and `LLM_MODE=gemini` if you have a Gemini key, or `mock`).
2. Keep your PayPal Sandbox **Personal** buyer email & password ready in a note or browser autofill.
3. Delete any old `mandate.db` so the demo starts clean, then run:
   ```bash
   uvicorn app.main:app --reload
   ```
4. Open `http://127.0.0.1:8000/` and check that the header badge says `PayPal: SANDBOX`.

---

## Timestamped Flow (2:00 Total)

### `0:00 – 0:15` | The Problem (15s)
- **On screen:** Show the clean Mandate **Shop** tab with the tagline *"Agents can spend safely. Merchants can prove it."*
- **Say:** *"AI shopping agents are starting to spend money for users, and 63% of merchants are exploring agentic AI payments according to a Merchant Risk Council 2026 report cited by Chargeflow. Yet shoppers don't trust agents with blank checks—ECOMMPAY research found roughly 21.3% won't let an agent spend at all—and merchants face friendly fraud estimated at around 75% of eCommerce disputes (Chargeflow). Mandate solves both in one safety and proof layer on PayPal."*

### `0:15 – 0:35` | Create & Confirm Mandate (20s)
- **Click:** Type `get me a black jacket under $35` -> click **Parse mandate** -> point out the `Assumed` pills (`$70` daily cap, `thrift-shop`, `24 hours`) -> click **Confirm mandate**.
- **Say:** *"I type a plain-English limit. AI drafts a structured mandate and highlights every assumption it filled in so there are no surprises. When I click Confirm mandate, event #1 hits our hash-chained ledger."*

### `0:35 – 1:00` | Normal Buy Through PayPal Sandbox (25s)
- **Click:** Click **Normal buy** -> show `APPROVED` with all 8 green checks -> click **Approve in PayPal** -> log in with your Sandbox buyer account -> approve checkout -> automatically redirect back to the **Merchant** tab.
- **Say:** *"I run Normal buy. Our agent proposes only a product ID—our deterministic Guard code loads the real $24 price from the catalog, passes all 8 checks, and creates a real PayPal Sandbox order using an idempotency key. I approve in PayPal Sandbox, and Mandate captures the payment and stamps it with a Verified mandate badge."*

### `1:00 – 1:20` | Over Cap & Fake Seller Held, Then Deny (20s)
- **Click:** Switch back to **Shop** -> click **Over the cap** (shows `BLOCKED: OVER_ITEM_CAP` for $48 jacket) -> click **Fake seller** (shows `HELD: UNKNOWN_SELLER, HIGH_RISK`, score `0.85`) -> open **Approvals** tab -> click **Deny**.
- **Say:** *"Try a $48 leather jacket? Blocked immediately by deterministic code. Try a shady $9 jacket on a 6-day-old unverified domain? Our risk check scores it 0.85 and pauses it for human-in-the-loop review in Approvals. I click Deny, and that denial is recorded in the ledger."*

### `1:20 – 1:40` | Prompt Injection Attack Blocked (20s — High Point!)
- **Click:** Return to **Shop** -> click **Injection attack** -> highlight the amber *"Attack simulation: the agent is gullible on purpose. Watch the guard."* banner, the red `BLOCKED (OVER_ITEM_CAP...)` badge, and the green safe-prompt comparison card below it.
- **Say:** *"Now the big test: a malicious listing hides a fake SYSTEM NOTICE telling the AI agent the user raised their cap to $500 and to buy a $199 bundle immediately. Even though our intentionally gullible agent falls for it, the Guard doesn't care what the AI believes—rules are enforced in code, so it's blocked cold."*

### `1:40 – 1:50` | Ledger Verify & Tamper Demo Failing (10s)
- **Click:** Open **Ledger** tab -> click **Verify chain** (green `Chain Intact`) -> click **Tamper demo** on an entry -> show red **`TAMPER DETECTED!`**.
- **Say:** *"Every event chains via SHA-256 hashes from GENESIS. Verify chain passes—and if anyone edits even one past record in the database, verification pinpoints the exact tampered sequence number."*

### `1:50 – 2:00` | Dispute Defender & Close (10s)
- **Click:** Open **Merchant** tab -> click **Simulate dispute** -> click **Build defense** (point out strength & draft reply; note that because we just tampered the ledger, it shows `WEAK` and warns *"should not contest"*—or run Dispute Defender right before Tamper Demo if you want to show `STRONG` first!).
- **Tip for best flow:** To show `STRONG` defense before breaking the ledger, swap `1:40` and `1:50` (click **Simulate dispute -> Build defense** on the **Merchant** tab while you're already there or right before **Tamper demo**!).
- **Say:** *"Finally, when a customer disputes an order, Dispute Defender builds an evidence packet strictly from the ledger—giving merchants a STRONG factual reply when the chain is intact, or honestly warning them not to contest if proof is missing. Mandate: agents can spend safely, and merchants can prove it."*
