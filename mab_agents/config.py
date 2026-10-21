"""Load the shop profile (shop.toml) with environment overrides."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

PKG_DIR = Path(__file__).resolve().parent
EXAMPLE = PKG_DIR / "shop.example.toml"


def shop_path() -> Path:
    env = os.getenv("MAB_SHOP")
    if env:
        return Path(env)
    for candidate in (Path.cwd() / "shop.toml", PKG_DIR.parent / "shop.toml"):
        if candidate.exists():
            return candidate
    return EXAMPLE


def load_shop(path: str | os.PathLike | None = None) -> dict[str, Any]:
    p = Path(path) if path else shop_path()
    with open(p, "rb") as fh:
        shop = tomllib.load(fh)
    shop.setdefault("carrier_rules", [])
    shop["_path"] = str(p)
    shop["_is_example"] = p == EXAMPLE
    return shop


def carrier_rule(shop: dict[str, Any], carrier: str | None) -> dict[str, Any] | None:
    if not carrier:
        return None
    needle = carrier.strip().lower()
    for rule in shop.get("carrier_rules", []):
        if rule.get("name", "").lower() == needle:
            return rule
    return None


def db_path() -> Path:
    return Path(os.getenv("MAB_DB", "mab.db"))
