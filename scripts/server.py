#!/usr/bin/env python3
"""NetUs Ads Cockpit — servidor web (plataforma).
Sirve el dashboard con 3 vistas, 3 niveles y filtro por fecha REAL contra Postgres.

  DATABASE_URL=... PORT=8080 python3 scripts/server.py

Sin dependencias de Node. Solo pg8000 (Python puro).
"""
import os
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pg8000.native

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    if os.environ.get("DATABASE_URL"):
        return
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def connect():
    u = urllib.parse.urlparse(os.environ["DATABASE_URL"])
    return pg8000.native.Connection(
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname, port=u.port or 5432,
        database=(u.path or "/").lstrip("/") or "postgres")


# ---- formato ---------------------------------------------------------------
def m0(v):
    return "—" if v is None else "${:,.0f}".format(float(v))


def m2(v):
    return "—" if v in (None, 0) else "${:,.2f}".format(float(v))


def pctf(imp, clk):
    return "{:.2f}%".format(100.0 * clk / imp) if imp else "—"


def cpmf(imp, sp):
    return "${:,.2f}".format(1000.0 * sp / imp) if imp else "—"


def cpl_html(sp, leads):
    if not leads:
        return '<td class="hint">—</td>'
    v = sp / leads
    cls = "g" if v <= 100 else ("a" if v <= 120 else "r")
    return f'<td class="cpl-{cls}">${v:,.2f}</td>'


def lead_cls(v):
    v = int(v or 0)
    return "g" if v >= 14 else ("a" if v >= 7 else "r")


def status_tag(s):
    s = (s or "").upper()
    if s in ("DISAPPROVED", "WITH_ISSUES"):
        return '<span class="tag r">rechazo</span>'
    if s == "PAUSED":
        return '<span class="tag m">pausada</span>'
    return '<span class="tag g">activa</span>'


def px_head2():
    return ('<th class="px">Clientes pot.</th><th class="px">Solic. env.</th>'
            '<th class="px">Registros</th><th class="px">Citas</th><th class="px">Ventas</th>')


def px_cells(row, sem=False):
    leads = int(row.get("leads") or 0)
    lc = f' lead-{lead_cls(leads)}' if sem else ''
    out = f'<td class="px{lc}" style="font-weight:700">{leads}</td>'
    for k in ("ev_solicitud", "ev_registro", "ev_cita", "ev_venta"):
        v = int(row.get(k) or 0)
        out += f'<td class="px">{v}</td>' if v else '<td class="px hint">—</td>'
    return out


# ---- rango de fechas -------------------------------------------------------
PRESETS = [("yesterday", "Ayer"), ("last_7d", "Últimos 7 días"), ("last_14d", "Últimos 14 días"),
           ("last_30d", "Últimos 30 días"), ("this_month", "Este mes"), ("last_month", "Mes pasado"),
           ("custom", "Personalizado")]


def compute_range(preset, since, until):
    today = date.today()
    if preset == "custom" and since and until:
        return date.fromisoformat(since), date.fromisoformat(until)
    if preset == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if preset == "last_14d":
        return today - timedelta(days=14), today
    if preset == "last_30d":
        return today - timedelta(days=30), today
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        first = today.replace(day=1)
        last_prev = first - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    return today - timedelta(days=7), today  # last_7d por defecto


# ---- datos -----------------------------------------------------------------
def fetch(con, df, dt):
    con.run("set search_path to cockpit, public")
    accts = con.run("select meta_ad_account_id, name, segment, currency from clients order by name")
    camps = con.run("""select meta_ad_account_id, campaign_id, max(campaign_name), max(status),
        sum(spend), sum(impressions), sum(clicks), sum(leads),
        sum(ev_solicitud), sum(ev_registro), sum(ev_cita), sum(ev_venta)
        from meta_campaign_daily where date >= :df and date <= :dt
        group by meta_ad_account_id, campaign_id""", df=df, dt=dt)
    ads = con.run("""select meta_ad_account_id, campaign_id, ad_id, max(ad_name), max(status),
        sum(spend), sum(impressions), sum(clicks), sum(leads),
        sum(ev_solicitud), sum(ev_registro), sum(ev_cita), sum(ev_venta)
        from meta_ad_daily where date >= :df and date <= :dt
        group by meta_ad_account_id, campaign_id, ad_id""", df=df, dt=dt)
    return accts, camps, ads


def crow(r):
    return dict(acct=r[0], cid=r[1], name=r[2], status=r[3], spend=float(r[4] or 0),
                imp=int(r[5] or 0), clk=int(r[6] or 0), leads=int(r[7] or 0),
                ev_solicitud=int(r[8] or 0), ev_registro=int(r[9] or 0),
                ev_cita=int(r[10] or 0), ev_venta=int(r[11] or 0))


def arow(r):
    return dict(acct=r[0], cid=r[1], aid=r[2], name=r[3], status=r[4], spend=float(r[5] or 0),
                imp=int(r[6] or 0), clk=int(r[7] or 0), leads=int(r[8] or 0),
                ev_solicitud=int(r[9] or 0), ev_registro=int(r[10] or 0),
                ev_cita=int(r[11] or 0), ev_venta=int(r[12] or 0))


# ---- render ----------------------------------------------------------------
def build_page(tab, preset, since, until):
    df, dt = compute_range(preset, since, until)
    con = connect()
    try:
        accts, camps, ads = fetch(con, df, dt)
    finally:
        con.close()

    camps_by, ads_by = {}, {}
    for r in camps:
        camps_by.setdefault(r[0], []).append(crow(r))
    for r in ads:
        d = arow(r)
        ads_by.setdefault((d["acct"], d["cid"]), []).append(d)
    rej_by = {}
    for (acct, cid), lst in ads_by.items():
        for a in lst:
            if (a["status"] or "").upper() in ("DISAPPROVED", "WITH_ISSUES"):
                rej_by[acct] = rej_by.get(acct, 0) + 1

    def ad_table(acct, cid):
        rows = sorted(ads_by.get((acct, cid), []), key=lambda x: -x["leads"])
        if not rows:
            return '<div class="hint" style="padding:8px 12px">Sin anuncios con entrega en el periodo.</div>'
        body = "".join(
            f'<tr><td>{a["name"]}</td><td>{status_tag(a["status"])}</td><td>{m2(a["spend"])}</td>'
            f'<td>{pctf(a["imp"], a["clk"])}</td><td>{cpmf(a["imp"], a["spend"])}</td>'
            f'{px_cells(a)}{cpl_html(a["spend"], a["leads"])}</tr>' for a in rows)
        return (f'<table class="sub"><thead><tr><th rowspan="2">Anuncio</th><th rowspan="2">Estado</th>'
                f'<th colspan="3" class="grp">Rendimiento</th><th colspan="5" class="grp px">Eventos del pixel</th>'
                f'<th rowspan="2">CPL</th></tr><tr><th>Gasto</th><th>CTR</th><th>CPM</th>{px_head2()}</tr></thead>'
                f'<tbody>{body}</tbody></table>')

    def camp_table(acct, tag):
        rows = sorted(camps_by.get(acct, []), key=lambda x: -x["spend"])
        if not rows:
            return '<div class="hint" style="padding:8px 12px">Sin campañas con entrega en el periodo.</div>'
        body = ""
        for i, c in enumerate(rows):
            cid = f"{tag}c{i}"
            body += (f'<tr class="camp" onclick="t(\'{cid}\')"><td><span class="ca" id="c-{cid}">▸</span> {c["name"]}</td>'
                     f'<td>{status_tag(c["status"])}</td><td>{m2(c["spend"])}</td>'
                     f'<td>{pctf(c["imp"], c["clk"])}</td><td>{cpmf(c["imp"], c["spend"])}</td>'
                     f'{px_cells(c)}{cpl_html(c["spend"], c["leads"])}</tr>'
                     f'<tr class="det" id="{cid}"><td colspan="11"><div class="sw">{ad_table(acct, c["cid"])}</div></td></tr>')
        return (f'<table class="sub"><thead><tr><th rowspan="2">Campaña</th><th rowspan="2">Estado</th>'
                f'<th colspan="3" class="grp">Rendimiento</th><th colspan="5" class="grp px">Eventos del pixel</th>'
                f'<th rowspan="2">CPL</th></tr><tr><th>Gasto</th><th>CTR</th><th>CPM</th>{px_head2()}</tr></thead>'
                f'<tbody>{body}</tbody></table>')

    def view(seg, title):
        rows = [a for a in accts if a[2] == seg]
        body = ""
        for i, a in enumerate(rows):
            acct, name = a[0], a[1]
            cs = camps_by.get(acct, [])
            agg = {k: sum(c[k] for c in cs) for k in ("spend", "leads", "ev_solicitud", "ev_registro", "ev_cita", "ev_venta")}
            rid = f"{seg}{i}"
            rej = rej_by.get(acct, 0)
            anun = (f'<td><span class="tag r">{rej} con problema</span></td>' if rej else '<td><span class="tag m">ok</span></td>')
            body += (f'<tr class="acct" onclick="t(\'{rid}\')"><td><span class="ca" id="c-{rid}">▸</span> {name}</td>'
                     f'<td>{len(cs)}</td><td>{m0(agg["spend"])}</td>'
                     f'{px_cells(agg, sem=True)}{cpl_html(agg["spend"], agg["leads"])}{anun}</tr>'
                     f'<tr class="det" id="{rid}"><td colspan="10"><div class="sw">{camp_table(acct, rid)}</div></td></tr>')
        if not body:
            body = '<tr><td colspan="10" class="hint">Sin cuentas.</td></tr>'
        on = " on" if tab == seg else ""
        return (f'<section id="{seg}" class="v{on}">{daterange_bar(seg, preset, since, until, df, dt)}<div class="ts"><table>'
                f'<thead><tr><th rowspan="2">Cuenta</th><th rowspan="2">Camp.</th><th rowspan="2">Gasto</th>'
                f'<th colspan="5" class="grp px">Eventos del pixel</th><th rowspan="2">CPL</th><th rowspan="2">Anuncios</th></tr>'
                f'<tr>{px_head2()}</tr></thead><tbody>{body}</tbody></table></div>'
                f'<div class="lg"><span><b>CPL:</b></span><span><span class="dot" style="background:var(--g)"></span>&lt;$100</span>'
                f'<span><span class="dot" style="background:var(--a)"></span>$100–120</span><span><span class="dot" style="background:var(--r)"></span>&gt;$120</span>'
                f'<span style="margin-left:12px"><b>Leads/sem:</b></span><span><span class="dot" style="background:var(--g)"></span>≥14</span>'
                f'<span><span class="dot" style="background:var(--a)"></span>7–13</span><span><span class="dot" style="background:var(--r)"></span>&lt;7</span>'
                f'<span style="margin-left:12px">Solic./Registros/Citas/Ventas se llenan al <b>conectar GHL</b>.</span></div></section>')

    ghl_rows = "".join(
        f'<tr><td>{a[1]}</td><td>{a[2]}</td><td class="hint">{a[0]}</td><td class="hint">—</td>'
        f'<td><span class="tag a">falta PIT</span></td><td class="hint">utm_campaign</td></tr>' for a in accts)
    ghl_on = " on" if tab == "ghl" else ""
    ghl = (f'<section id="ghl" class="v{ghl_on}"><div class="callout">Aquí se conecta cada subcuenta de Go High Level con su '
           f'<b>Private Integration Token (PIT)</b> para traer <b>leads reales, citas y ventas</b> '
           f'(las columnas que hoy salen “—”).</div><div class="ts"><table><thead><tr><th>Cuenta</th><th>Vista</th>'
           f'<th>Meta ID</th><th>GHL location</th><th>PIT</th><th>Campo UTM</th></tr></thead><tbody>{ghl_rows}</tbody></table></div></section>')

    def navlink(t, label):
        on = " on" if tab == t else ""
        return f'<a class="nl{on}" href="/?tab={t}&preset={preset}&since={since or ""}&until={until or ""}">{label}</a>'

    return PAGE.format(nav=navlink("client", "Clientes") + navlink("netus", "NetUs") + navlink("ghl", "Conexiones GHL"),
                       client=view("client", "Clientes"), netus=view("netus", "NetUs (campañas propias)"), ghl=ghl)


def daterange_bar(seg, preset, since, until, df, dt):
    opts = "".join(f'<option value="{v}"{" selected" if v == (preset or "last_7d") else ""}>{lbl}</option>' for v, lbl in PRESETS)
    return (f'<form class="dr" method="get"><input type="hidden" name="tab" value="{seg}">'
            f'<span class="lbl">📅 Periodo:</span>'
            f'<select name="preset" onchange="if(this.value!=\'custom\')this.form.submit()">{opts}</select>'
            f'<input type="date" name="since" value="{since or ""}"> <span class="lbl">a</span> '
            f'<input type="date" name="until" value="{until or ""}"> <button class="btn">Aplicar</button>'
            f'<span class="lbl">Mostrando {df} → {dt}</span></form>')


PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>NetUs Ads Cockpit</title>
<style>
:root{{--bg:#0b0f17;--panel:#131a26;--p2:#0f1520;--bd:#223046;--tx:#e6edf6;--mu:#8ea0b8;--ac:#4f9cff;--r:#ff5c72;--a:#ffbf47;--g:#37d39a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
a{{color:inherit;text-decoration:none}}
.nav{{display:flex;gap:4px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--bd);background:var(--p2)}}.nav .br{{font-weight:700;margin-right:16px}}
.nl{{padding:6px 12px;border-radius:8px;color:var(--mu)}}.nl.on{{background:var(--panel);color:var(--tx)}}
.wrap{{padding:20px;max-width:1320px;margin:0 auto}}
.ts,.sw{{overflow-x:auto;border:1px solid var(--bd);border-radius:12px;margin-bottom:8px}}.sw{{border:none;margin:0}}
table{{width:100%;border-collapse:collapse;background:var(--panel)}}th,td{{padding:8px 11px;text-align:right;border-bottom:1px solid var(--bd);white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}th{{color:var(--mu);font-weight:600;font-size:12px;background:var(--p2)}}tr:last-child td{{border-bottom:none}}
.grp{{text-align:center !important;border-bottom:1px solid var(--bd)}}thead tr:nth-child(2) th:first-child{{text-align:right}}
.px{{background:rgba(79,156,255,.06)}}th.px{{background:#0d1726}}th.grp.px{{color:var(--ac)}}
.acct,.camp{{cursor:pointer}}.acct:hover,.camp:hover{{background:rgba(79,156,255,.06)}}.ca{{display:inline-block;width:14px;color:var(--mu)}}
.det{{display:none}}.det.on{{display:table-row}}.det>td{{background:var(--p2);padding:0 0 0 16px}}.sub th{{background:#0c121c}}
.tag{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}}.tag.r{{background:rgba(255,92,114,.15);color:var(--r)}}.tag.a{{background:rgba(255,191,71,.15);color:var(--a)}}.tag.g{{background:rgba(55,211,154,.15);color:var(--g)}}.tag.m{{background:rgba(142,160,184,.15);color:var(--mu)}}
.cpl-g,.lead-g{{color:var(--g);font-weight:700}}.cpl-a,.lead-a{{color:var(--a);font-weight:700}}.cpl-r,.lead-r{{color:var(--r);font-weight:700}}
.hint{{color:var(--mu);font-size:12px}}.callout{{background:var(--panel);border:1px solid var(--bd);border-left:3px solid var(--ac);border-radius:10px;padding:10px 14px;margin:6px 0 14px;color:var(--mu);font-size:13px}}.callout b{{color:var(--tx)}}
.lg{{display:flex;gap:14px;color:var(--mu);font-size:12px;margin:8px 0 4px;flex-wrap:wrap}}.lg b{{color:var(--tx)}}.dot{{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:5px}}
.dr{{display:flex;gap:8px;align-items:center;margin:12px 0;flex-wrap:wrap}}.dr select,.dr input{{background:var(--p2);border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:6px 9px}}.dr .lbl{{color:var(--mu);font-size:12px}}.btn{{background:var(--ac);color:#04101f;border:none;border-radius:8px;padding:6px 12px;font-weight:700;cursor:pointer}}
.v{{display:none}}.v.on{{display:block}}
</style></head><body>
<nav class="nav"><span class="br">🛰️ NetUs Ads Cockpit</span>{nav}</nav>
<div class="wrap">{client}{netus}{ghl}</div>
<script>
function t(id){{event.stopPropagation();document.getElementById(id).classList.toggle('on');
 var c=document.getElementById('c-'+id);if(c)c.textContent=c.textContent=='▸'?'▾':'▸';}}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path not in ("/", "/index.html"):
            self.send_response(404); self.end_headers(); return
        q = urllib.parse.parse_qs(u.query)
        try:
            html = build_page(q.get("tab", ["client"])[0], q.get("preset", ["last_7d"])[0],
                              (q.get("since", [""])[0] or None), (q.get("until", [""])[0] or None))
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:  # noqa
            msg = f"Error: {e}".encode("utf-8")
            self.send_response(500); self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers(); self.wfile.write(msg)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    load_env()
    port = int(os.environ.get("PORT", "8080"))
    print(f"NetUs Ads Cockpit en http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
