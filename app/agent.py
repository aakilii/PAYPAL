"""Shopper agent orchestration and prompt-injection attack simulation."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.catalog import get_product, list_products
from app.guard import evaluate_purchase
from app.llm import pick_product_llm
from app.mandate import get_mandate_by_id


def run(
    conn: sqlite3.Connection,
    mandate_id: str,
    request_text: str,
    attack: str = "none",
) -> dict[str, Any]:
    """Let the shopper agent propose one product_id and pass it to the deterministic Guard.

    Notice the agent never supplies price or seller to the Guard; the Guard loads
    authoritative values directly from data/catalog.json.
    """
    normalized_attack = attack.strip().lower() if attack else "none"
    if normalized_attack not in ("none", "injection"):
        raise ValueError(f"Unsupported attack mode '{attack}'. Must be 'none' or 'injection'.")

    mandate = get_mandate_by_id(conn, mandate_id)
    if mandate is None:
        raise KeyError(f"Mandate '{mandate_id}' not found.")

    catalog = list_products()
    pick, llm_meta = pick_product_llm(request_text, catalog, attack=normalized_attack)

    chosen_product = get_product(pick.product_id)
    if chosen_product is None:
        raise ValueError(f"Agent proposed unknown product_id '{pick.product_id}'.")

    attempt = evaluate_purchase(
        conn,
        mandate_id=mandate_id,
        product_id=pick.product_id,
        request_text=request_text,
    )

    safe_comparison: dict[str, Any] | None = None
    if normalized_attack == "injection":
        safe_pick, safe_meta = pick_product_llm(request_text, catalog, attack="none")
        safe_prod = get_product(safe_pick.product_id)
        safe_comparison = {
            "product_id": safe_pick.product_id,
            "product_title": safe_prod["title"] if safe_prod else safe_pick.product_id,
            "price_usd": safe_prod["price_usd"] if safe_prod else None,
            "why": safe_pick.why,
            "llm_meta": safe_meta,
            "note": (
                "The safe prompt wrapped catalog descriptions in <untrusted> data tags and resisted this attack. "
                "However, no AI prompt is 100% immune—the deterministic Guard enforces hard caps in code."
            ),
        }

    return {
        "agent_pick": {
            "product_id": pick.product_id,
            "why": pick.why,
            "attack": normalized_attack,
            "attack_label": (
                "Attack simulation: the agent is gullible on purpose. Watch the guard."
                if normalized_attack == "injection"
                else None
            ),
            "llm_meta": llm_meta,
        },
        "attempt": attempt,
        "safe_agent_comparison": safe_comparison,
        "order": None,
    }
