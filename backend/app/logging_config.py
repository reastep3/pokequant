"""Structured JSON logging (Fly.io log shipper friendly) with a readable dev fallback."""

from __future__ import annotations

import json
import logging
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_logs: bool | None = None) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if json_logs is None:
        json_logs = not sys.stdout.isatty()
    handler.setFormatter(
        JsonFormatter()
        if json_logs
        else logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
