#!/usr/bin/env python3
"""Sube el tablero de una o más cuentas a Centiads (EasyPanel /api/ingest, o Supabase centiads_payloads).

  python3 scripts/push_payload.py --url https://<dominio-centiads> preview/control-de-ads-*.html   (EasyPanel)
  python3 scripts/push_payload.py preview/control-de-ads-*.html                                  (Supabase)

Toma el payload embebido en cada HTML generado (preview/build_*.py), lo comprime (gzip+base64)
y lo manda a la función centiads-ingest con la clave de .centiads_ingest_key (gitignored).
"""
import base64
import gzip
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FN = "https://onfxjbngwrkbnjboqksv.supabase.co/functions/v1/centiads-ingest"
# llave anónima (pública) solo para pasar el verify_jwt de la función; la autorización real es x-centiads-key
ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9uZnhqYm5nd3JrYm5qYm9xa3N2Iiwicm9sZSI6"
        "ImFub24iLCJpYXQiOjE3NzgxNzQ3ODAsImV4cCI6MjA5Mzc1MDc4MH0.E2luw_xxdZYCGaBRXahseHO84g9dqbmuqr87qE9RZPM")


def push(path):
    raw = re.search(r'id="adsdata">(.*?)</script>', Path(path).read_text(), re.S).group(1).replace("<\\/", "</")
    acct = json.loads(raw)["acct"]
    if not acct.get("id"):
        raise SystemExit(f"{path}: el payload no trae acct.id")
    body = json.dumps({"account_id": acct["id"], "name": acct["name"],
                       "payload_gz": base64.b64encode(gzip.compress(raw.encode(), 9)).decode()}).encode()
    req = urllib.request.Request(FN, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {ANON}",
        "x-centiads-key": (ROOT / ".centiads_ingest_key").read_text().strip()})
    with urllib.request.urlopen(req, timeout=60) as r:
        print(acct["name"], "→", json.loads(r.read()))


def push_easypanel(path, base):
    """Sube el payload a la plataforma en EasyPanel (/api/ingest), guardado como jsonb en el Postgres del cockpit."""
    raw = re.search(r'id="adsdata">(.*?)</script>', Path(path).read_text(), re.S).group(1).replace("<\\/", "</")
    body = json.dumps({"payload": json.loads(raw)}, separators=(",", ":")).encode()
    req = urllib.request.Request(base.rstrip("/") + "/api/ingest", data=body, method="POST", headers={
        "Content-Type": "application/json", "x-centiads-key": (ROOT / ".centiads_ingest_key").read_text().strip()})
    with urllib.request.urlopen(req, timeout=120) as r:
        print(Path(path).name, "→", json.loads(r.read()))


if __name__ == "__main__":
    # --url https://centiads.tudominio  → EasyPanel;  sin --url → Supabase (ghl-mcp)
    args = sys.argv[1:]
    if args and args[0] == "--url":
        for p in args[2:]:
            push_easypanel(p, args[1])
    else:
        for p in args:
            push(p)
