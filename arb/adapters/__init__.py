from __future__ import annotations

from ..config import Config
from .base import VenueAdapter
from .kalshi import KalshiAdapter
from .limitless import LimitlessAdapter
from .polymarket import PolymarketAdapter


def build_adapters(cfg: Config) -> dict[str, VenueAdapter]:
    """Создаёт адаптеры включённых в config.yaml площадок."""
    factories = {
        "polymarket": lambda c: PolymarketAdapter(c["gamma_url"], c["clob_url"], c.get("rate_per_sec", 20)),
        "kalshi": lambda c: KalshiAdapter(c["base_url"], c.get("rate_per_sec", 8)),
        "limitless": lambda c: LimitlessAdapter(c["base_url"], c.get("rate_per_sec", 1)),
    }
    adapters: dict[str, VenueAdapter] = {}
    for name, venue_cfg in (cfg.get("venues") or {}).items():
        if venue_cfg.get("enabled", True) and name in factories:
            adapters[name] = factories[name](venue_cfg)
    return adapters


__all__ = ["VenueAdapter", "build_adapters"]
