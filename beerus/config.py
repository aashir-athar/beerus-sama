"""Config loader: YAML defaults + live overrides saved in the database."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.getenv("BEERUS_DATA_DIR", ROOT / "data"))

_MISSING = object()


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class Config:
    """Dotted-path access, e.g. cfg.get("spam.flood.max_messages")."""

    def __init__(self) -> None:
        self.base: dict = {}
        self.lexicon: dict = {}
        self.overrides: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        self.base = _load_yaml("rules.yaml")
        self.lexicon = _load_yaml("lexicon.yaml")

    def get(self, path: str, default: Any = None) -> Any:
        if path in self.overrides:
            return self.overrides[path]
        node: Any = self.base
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def has_path(self, path: str) -> bool:
        node: Any = self.base
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return False
        return not isinstance(node, dict)

    def flat(self) -> dict[str, Any]:
        out: dict[str, Any] = {}

        def walk(prefix: str, node: Any) -> None:
            if isinstance(node, dict) and node:
                for k, v in node.items():
                    walk(f"{prefix}.{k}" if prefix else k, v)
            else:
                out[prefix] = node

        walk("", copy.deepcopy(self.base))
        out.update(self.overrides)
        return out

    def set_override(self, path: str, value: Any) -> None:
        self.overrides[path] = value

    def clear_override(self, path: str) -> None:
        self.overrides.pop(path, None)

    @staticmethod
    def parse_value(raw: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
