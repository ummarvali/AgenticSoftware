"""Minimal in-process counters exposed in a Prometheus-like text format."""

import threading


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters = {}

    def inc(self, name: str, value: int = 1):
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def render(self) -> str:
        lines = []
        with self._lock:
            for name, value in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"
