"""Heuristic and AI-assisted seller risk scoring for untrusted product listings."""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.llm import assess_risk_llm

PRESSURE_PHRASES = (
    "only",
    "left",
    "last chance",
    "hurry",
    "limited time",
    "act now",
    "ignore previous",
    "system notice",
)


def compute_heuristic_risk(product: dict[str, Any]) -> tuple[float, list[str]]:
    """Compute deterministic seller and pricing risk signals in [0.0, 1.0]."""
    score = 0.0
    reasons: list[str] = []

    domain_age_days = int(product.get("domain_age_days", 0))
    if domain_age_days < 30:
        score += 0.30
        reasons.append(f"New seller domain ({domain_age_days} days old < 30 days): +0.30")

    price = float(product.get("price_usd", 0.0))
    market_price = float(product.get("market_price_usd", 0.0))
    if market_price > 0 and price < 0.4 * market_price:
        score += 0.25
        reasons.append(
            f"Suspiciously low price (${price:.2f} < 40% of ${market_price:.2f} market price): +0.25"
        )

    listing_text = f"{product.get('title', '')} {product.get('description', '')}".lower()
    matched_phrases = [phrase for phrase in PRESSURE_PHRASES if phrase in listing_text]
    if matched_phrases:
        score += 0.15
        reasons.append(
            f"Listing uses urgency/pressure or injection phrasing ({', '.join(matched_phrases)}): +0.15"
        )

    verified = bool(product.get("seller_verified", False))
    if not verified:
        score += 0.15
        reasons.append("Seller is unverified: +0.15")

    clamped = round(min(1.0, score), 2)
    if not reasons:
        reasons.append("No heuristic risk signals detected (established verified seller, normal pricing).")

    return clamped, reasons


def evaluate_risk(product: dict[str, Any]) -> dict[str, Any]:
    """Evaluate seller risk using deterministic heuristics and (in gemini mode) AI."""
    heuristic_score, heuristic_reasons = compute_heuristic_risk(product)
    settings = get_settings()

    if settings.llm_mode != "gemini":
        return {
            "score": heuristic_score,
            "heuristic_score": heuristic_score,
            "ai_score": None,
            "reasons": heuristic_reasons,
            "llm_meta": {"provider": "mock", "fallback_used": False, "fallback_reason": None},
        }

    seller_facts = {
        "seller": product.get("seller"),
        "verified": bool(product.get("seller_verified", False)),
        "domain_age_days": int(product.get("domain_age_days", 0)),
        "price_usd": float(product.get("price_usd", 0.0)),
        "market_price_usd": float(product.get("market_price_usd", 0.0)),
    }
    listing_text = f"Title: {product.get('title', '')}\nDescription: {product.get('description', '')}"

    ai_result, llm_meta = assess_risk_llm(
        seller_facts=seller_facts,
        listing_text=listing_text,
        heuristic_score=heuristic_score,
        heuristic_reasons=heuristic_reasons,
    )

    ai_score = round(float(ai_result.score), 2)
    final_score = round(min(1.0, max(heuristic_score, ai_score)), 2)

    combined_reasons = list(heuristic_reasons)
    if not llm_meta.get("fallback_used"):
        for reason in ai_result.reasons:
            clean = reason.strip()
            if clean and clean not in combined_reasons:
                combined_reasons.append(f"AI risk check: {clean}")

    return {
        "score": final_score,
        "heuristic_score": heuristic_score,
        "ai_score": ai_score if not llm_meta.get("fallback_used") else None,
        "reasons": combined_reasons,
        "llm_meta": llm_meta,
    }
