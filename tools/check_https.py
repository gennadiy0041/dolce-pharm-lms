"""Проверка защищённого адреса платформы с ноутбука.

Подключается к серверу так же, как телефон: доверяет только корневому сертификату платформы
и проверяет, что сертификат сервера выпущен на этот адрес.

    python tools/check_https.py                 — адрес хот-спота https://192.168.137.1:8443
    python tools/check_https.py 172.16.3.110    — другой адрес ноутбука
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("LMS_ENV", "demo")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    from app import config

    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.137.1"
    wait = float(os.environ.get("WAIT_SECONDS", "0"))
    ca = config.CERT_DIR / "dolce-lms-root-ca.crt"
    ctx = ssl.create_default_context(cafile=str(ca))        # доверяем только корневому сертификату платформы
    url = "https://%s:%d/api/health" % (host, config.HTTPS_PORT)
    deadline = time.time() + wait
    while True:
        try:
            with socket.create_connection((host, config.HTTPS_PORT), timeout=3) as raw:
                with ctx.wrap_socket(raw, server_hostname=host) as tls:
                    cert = tls.getpeercert()
            with urllib.request.urlopen(url, context=ctx, timeout=5) as r:
                body = r.read().decode("utf-8")
            break
        except Exception as exc:            # noqa: BLE001 — показываем любую причину отказа как есть
            if time.time() >= deadline:
                print(json.dumps({"url": url, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, ensure_ascii=False))
                sys.exit(1)
            time.sleep(1)
    names = [v for k, v in cert.get("subjectAltName", ())]
    print(json.dumps({
        "url": url, "ok": True, "trusted_by_platform_ca": True, "issuer": dict(x[0] for x in cert["issuer"]).get("commonName"),
        "valid_until": cert["notAfter"], "covers_this_address": host in names, "names": names, "health": body[:120],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
