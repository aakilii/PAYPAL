"""Loads verified product and seller ground truth from data/catalog.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.json"


@lru_cache(maxsize=1)
def _load_raw_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_products() -> list[dict[str, Any]]:
    """Return all products enriched with their authoritative seller metadata."""
    raw = _load_raw_catalog()
    sellers: dict[str, dict[str, Any]] = raw.get("sellers", {})
    products: list[dict[str, Any]] = []
    for item in raw.get("products", []):
        seller_id = str(item["seller"])
        seller_meta = sellers.get(seller_id, {"verified": False, "domain_age_days": 0})
        products.append(
            {
                **item,
                "seller_verified": bool(seller_meta.get("verified", False)),
                "domain_age_days": int(seller_meta.get("domain_age_days", 0)),
            }
        )
    return products


def list_public_catalog() -> list[dict[str, Any]]:
    """Return storefront-safe product fields for the UI API."""
    return [
        {
            "id": p["id"],
            "title": p["title"],
            "tags": p["tags"],
            "price_usd": p["price_usd"],
            "seller": p["seller"],
            "seller_verified": p["seller_verified"],
            "description": p["description"],
        }
        for p in list_products()
    ]


def get_product(product_id: str) -> dict[str, Any] | None:
    """Look up authoritative product price, seller, and metadata by product_id."""
    for product in list_products():
        if product["id"] == product_id:
            return product
    return None


def get_seller_meta(seller_name: str) -> dict[str, Any]:
    """Return authoritative seller metadata (verified, domain_age_days)."""
    raw = _load_raw_catalog()
    sellers: dict[str, dict[str, Any]] = raw.get("sellers", {})
    meta = sellers.get(seller_name)
    if meta is None:
        return {"seller": seller_name, "verified": False, "domain_age_days": 0}
    return {
        "seller": seller_name,
        "verified": bool(meta.get("verified", False)),
        "domain_age_days": int(meta.get("domain_age_days", 0)),
    }
