"""Dual-stack uvicorn launcher.

Fly's edge proxy dials a machine over its private IPv4 address, while other apps on the 6PN
network (the Next.js server) dial it over IPv6. asyncio marks an explicit `::` listener
IPV6_V6ONLY, so `uvicorn --host ::` drops the proxy and `--host 0.0.0.0` drops 6PN. Binding
one socket per family serves both.

    python -m app.serve      # honours PORT (default 8000)
"""

from __future__ import annotations

import asyncio
import os
import socket

import uvicorn

from app.main import app


def _listen(family: int, addr: str, port: int) -> socket.socket:
    s = socket.socket(family, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    s.bind((addr, port))
    s.listen(2048)
    s.setblocking(False)
    return s


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    sockets = [_listen(socket.AF_INET, "0.0.0.0", port)]
    try:
        sockets.append(_listen(socket.AF_INET6, "::", port))
    except OSError:  # IPv6 disabled on this host
        pass
    config = uvicorn.Config(app, proxy_headers=True, forwarded_allow_ips="*", log_config=None,
                            timeout_graceful_shutdown=20)
    asyncio.run(uvicorn.Server(config).serve(sockets=sockets))


if __name__ == "__main__":
    main()
