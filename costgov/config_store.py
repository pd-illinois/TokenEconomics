"""
Config store (control plane <-> data plane contract).

The single source of truth for the cost knobs. The data plane READS it each request;
the decision binding WRITES it to change behavior at runtime with no code deploy.
Stand-in for Azure App Configuration / a versioned config service.
"""

from __future__ import annotations
import copy
import json


class ConfigStore:
    def __init__(self, path: str):
        self.path = path
        with open(path, encoding="utf-8") as fh:
            self._data = json.load(fh)
        self.history = [copy.deepcopy(self._data)]  # audit trail of knob changes

    @property
    def data(self) -> dict:
        return self._data

    def update(self, path_keys, value, reason: str) -> None:
        """Set a nested knob, e.g. update(['routing','mode'], 'balanced', 'eval regression')."""
        node = self._data
        for k in path_keys[:-1]:
            node = node[k]
        old = node.get(path_keys[-1])
        node[path_keys[-1]] = value
        self._data.setdefault("_changelog", []).append(
            {"knob": ".".join(path_keys), "from": old, "to": value, "reason": reason})
        self.history.append(copy.deepcopy(self._data))
