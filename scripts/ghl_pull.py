#!/usr/bin/env python3
"""Jala el embudo de una subcuenta GHL (por PIT) → cockpit.ghl_funnel_daily,
y liga la location a una cuenta publicitaria de Meta.

  python3 scripts/ghl_pull.py <meta_ad_account_id> [location_id]

Lee el PIT desde .ghl_token (raíz del repo, gitignored).
"""
import sys
import os
import json
import datetime
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

import pg8000.native

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://services.leadconnectorhq.com"
VER = "2021-07-28"
# palabras que marcan una etapa "de cita en adelante" (varía por cliente)
APT = ("cita", "agend", "entrevista", "asist", "confirm", "concert", "reuni", "sesion", "consult")


def load_env():
    if os.environ.get("DATABASE_URL"):
        return
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def db():
    u = urllib.parse.urlparse(os.environ["DATABASE_URL"])
    return pg8000.native.Connection(
        user=urllib.parse.unquote(u.username or ""), password=urllib.parse.unquote(u.password or ""),
        host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/") or "postgres")


def api(pit, path):
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": f"Bearer {pit}", "Version": VER, "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:400])
        raise


def main():
    load_env()
    acct = sys.argv[1]
    loc = sys.argv[2] if len(sys.argv) > 2 else None
    pit = (ROOT / ".ghl_token").read_text().strip()

    if not loc:
        d = api(pit, "/locations/search")
        locs = d.get("locations") or d.get("data") or []
        if not locs:
            print("No pude descubrir la location automáticamente.")
            print("Vuelve a correr con el ID:  python3 scripts/ghl_pull.py", acct, "<LOCATION_ID>")
            print("respuesta:", json.dumps(d)[:400])
            return
        loc = locs[0].get("id") or locs[0].get("_id")
        print("location:", loc, "-", locs[0].get("name"))

    pipes = api(pit, f"/opportunities/pipelines?locationId={loc}")
    plist = pipes.get("pipelines") or []
    pipe = next((p for p in plist if "lead machine" in (p.get("name") or "").lower()), plist[0] if plist else None)
    if not pipe:
        print("Sin pipelines en esta location.")
        return

    rows = []
    for st in pipe["stages"]:
        q = urllib.parse.urlencode({"location_id": loc, "pipeline_id": pipe["id"],
                                    "pipeline_stage_id": st["id"], "status": "all", "limit": 1})
        n = int((api(pit, f"/opportunities/search?{q}").get("meta") or {}).get("total") or 0)
        rows.append((st["id"], st["name"], n))
    total = sum(n for _, _, n in rows)
    # citas = desde la primera etapa "de cita" en adelante (por posición del pipeline)
    idx = next((i for i, (_, sn, _) in enumerate(rows) if any(k in sn.lower() for k in APT)), None)
    citas = sum(n for _, _, n in rows[idx:]) if idx is not None else 0
    # ventas = oportunidades GANADAS (status=won) — universal entre clientes
    q = urllib.parse.urlencode({"location_id": loc, "pipeline_id": pipe["id"], "status": "won", "limit": 1})
    ventas = int((api(pit, f"/opportunities/search?{q}").get("meta") or {}).get("total") or 0)

    con = db()
    con.run("set search_path to cockpit, public")
    con.run("""update clients set ghl_location_id = :l, ghl_pit = :pit, ghl_utm_field = 'utm_campaign'
               where meta_ad_account_id = :a""", l=loc, pit=pit, a=acct)
    today = datetime.date.today().isoformat()
    store = rows + [("__CITAS__", "CITAS", citas), ("__VENTAS__", "VENTAS", ventas)]
    for sid, sname, n in store:
        con.run("""insert into ghl_funnel_daily(ghl_location_id,date,pipeline_id,stage_id,stage_name,opp_count)
                   values(:l,:d,:p,:s,:sn,:c)
                   on conflict (ghl_location_id,date,stage_id)
                   do update set opp_count = excluded.opp_count, stage_name = excluded.stage_name, synced_at = now()""",
                l=loc, d=today, p=pipe["id"], s=sid, sn=sname, c=n)
    con.close()
    print(f"OK · pipeline='{pipe['name']}' · total={total} · CITAS={citas} · VENTAS(won)={ventas}")
    print("por etapa:", {sn: c for _, sn, c in rows})


if __name__ == "__main__":
    main()
