# Engineering Decisions Log

This file records important architectural choices, trade-offs, and any differences found when checking official PayPal and Gemini documentation against `AGENT_BRIEF.md`.

## D1. Project Root Directory (`c:\Users\Aakil\paypal`)
- **What:** Placed the project directly in the workspace root rather than inside a nested `mandate/` subdirectory.
- **Why:** Running `uvicorn app.main:app --reload` works directly from the workspace root with no extra `cd` step.
- **Alternatives:** Nested `mandate/` directory (adds unnecessary path depth inside an already dedicated workspace).

## D2. PayPal Orders v2 Uses `payment_source.paypal.experience_context`
- **What:** Confirmed in PayPal's official Orders v2 API docs (`developer.paypal.com`) that `payment_source.paypal.experience_context` (with `shipping_preference: "NO_SHIPPING"`, `user_action: "PAY_NOW"`, `return_url`, `cancel_url`) is the current replacement for the older `application_context`.
- **Why:** PayPal's official documentation marks `application_context` as legacy/deprecated and uses `rel: "payer-action"` (with fallback check for `rel: "approve"`) in the `links` array when `payment_source.paypal.experience_context` is passed.
- **Alternatives:** Using deprecated `application_context` (rejected so we stay aligned with current PayPal docs).

## D3. PayPal Sandbox-Only Startup Enforcement
- **What:** `app/config.py` validates `PAYPAL_BASE_URL` on startup and raises a `RuntimeError` if it is anything other than `https://api-m.sandbox.paypal.com` (defaulting an empty string to the sandbox URL).
- **Why:** Prevents accidental live-money PayPal requests during development, tests, or demos.
- **Alternatives:** Warning-only log (rejected as unsafe for a hackathon demo handling payment credentials).

## D4. Gemini Model Selection (`gemini-3.8-flash` via `google-genai` SDK)
- **What:** Verified in `ai.google.dev` documentation and `google-genai` v3.1.0 SDK that `types.GenerateContentConfig(response_mime_type="application/json", response_schema=PydanticModel)` enforces structured JSON output, with `gemini-3.8-flash` as the current fast Flash model (configurable via `GEMINI_MODEL` in `.env`).
- **Why:** Gives reliable structured JSON with Pydantic validation and a clean one-retry fallback to mock mode on parse failures.
- **Alternatives:** Unstructured text prompting + manual regex parsing of LLM responses (more error-prone).
