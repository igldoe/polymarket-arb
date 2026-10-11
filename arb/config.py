"""Загрузка конфигурации: секреты из .env, пороги и параметры из config.yaml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


class Config:
    def __init__(self, data: dict[str, Any]):
        self.data = data

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    @staticmethod
    def env(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, default)


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    path = Path(path or os.environ.get("ARB_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        return Config(yaml.safe_load(f) or {})
