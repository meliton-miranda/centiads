#!/usr/bin/env python3
"""Cockpit HTML con DATOS REALES de la base — 3 vistas (Clientes / NetUs / Conexiones GHL),
3 niveles (cuenta -> campañas -> anuncios) y columnas de eventos del pixel.

  python3 scripts/render.py     # genera preview/live.html
"""
import os
import urllib.parse
from pathlib import Path
from pg8000.native import Connection

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    if os.environ.get("DATABASE_URL"):
        return
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def connect():
    u = urllib.parse.urlparse(os.environ["DATABASE_URL"])
    return Connection(user=urllib.parse.unquote(u.username or ""),
                      password=urllib.parse.unquote(u.password or ""),
                      host=u.hostname, port=u.port or 5432,
                      database=(u.path or "/").lstrip("/") or "postgres")


def m0(v):
    return "—" if v is None else "${:,.0f}".format(float(v))


def m2(v):
    return "—" if v in (None, 0) else "${:,.2f}".format(float(v))


def pct(imp, clk):
    return "{:.2f}%".format(100.0 * clk / imp) if imp else "—"


def cpm(imp, sp):
    return "${:,.2f}".format(1000.0 * sp / imp) if imp else "—"


def cpl_val(sp, leads):
    return sp / leads if leads else None


def cpl_html(sp, leads):
    v = cpl_val(sp, leads)
    if v is None:
        return '<td class="hint">—</td>'
    cls = "g" if v <= 100 else ("a" if v <= 120 else "r")
    return f'<td class="cpl-{cls}">${v:,.2f}</td>'


def lead_cls(v):
    v = int(v or 0)
    return "g" if v >= 14 else ("a" if v >= 7 else "r")


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


def daterange_bar(seg):
    opts = ("<option>Ayer</option><option selected>Últimos 7 días</option>"
            "<option>Últimos 14 días</option><option>Últimos 30 días</option>"
            "<option>Este mes</option><option>Mes pasado</option><option value='custom'>Personalizado…</option>")
    return (f'<div class="dr"><span class="lbl">📅 Periodo:</span>'
            f'<select id="p-{seg}" onchange="pr(\'{seg}\')">{opts}</select>'
            f'<span id="cr-{seg}" style="display:none"><input type="date" id="s-{seg}" value="2026-07-02"> '
            f'<span class="lbl">a</span> <input type="date" id="u-{seg}" value="2026-07-08"> '
            f'<button class="btn" onclick="pc(\'{seg}\')">Aplicar</button></span>'
            f'<span class="lbl" id="cap-{seg}">Últimos 7 días (2–8 jul) · datos reales cargados</span></div>')


def render():
    load_env()
    con = connect()
    con.run("set search_path to cockpit, public")
    accts = con.run("select meta_ad_account_id, name, segment, currency from clients order by name")
    camps = con.run("""select meta_ad_account_id, campaign_id, max(campaign_name), max(status),
        sum(spend), sum(impressions), sum(clicks), sum(leads),
        sum(ev_solicitud), sum(ev_registro), sum(ev_cita), sum(ev_venta)
        from meta_campaign_daily group by meta_ad_account_id, campaign_id""")
    ads = con.run("""select meta_ad_account_id, campaign_id, ad_id, max(ad_name), max(status),
        sum(spend), sum(impressions), sum(clicks), sum(leads),
        sum(ev_solicitud), sum(ev_registro), sum(ev_cita), sum(ev_venta)
        from meta_ad_daily group by meta_ad_account_id, campaign_id, ad_id""")
    con.close()

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

    camps_by = {}
    for r in camps:
        camps_by.setdefault(r[0], []).append(crow(r))
    ads_by = {}
    for r in ads:
        d = arow(r)
        ads_by.setdefault((d["acct"], d["cid"]), []).append(d)

    # anuncios rechazados / con error de entrega por cuenta (últimos 7 días)
    rej_by_acct = {}
    for (acct, cid), lst in ads_by.items():
        for a in lst:
            if (a["status"] or "").upper() in ("DISAPPROVED", "WITH_ISSUES"):
                rej_by_acct[acct] = rej_by_acct.get(acct, 0) + 1

    def status_tag(s):
        s = (s or "").upper()
        if s in ("DISAPPROVED", "WITH_ISSUES"):
            return '<span class="tag r">rechazo</span>'
        if s == "PAUSED":
            return '<span class="tag m">pausada</span>'
        return '<span class="tag g">activa</span>'

    def ad_table(acct, cid):
        rows = sorted(ads_by.get((acct, cid), []), key=lambda x: -x["leads"])
        if not rows:
            return '<div class="hint" style="padding:8px 12px">Sin anuncios con entrega (jala nivel anuncio para esta cuenta).</div>'
        body = ""
        for a in rows:
            body += (f'<tr><td>{a["name"]}</td><td>{status_tag(a["status"])}</td>'
                     f'<td>{m2(a["spend"])}</td><td>{pct(a["imp"], a["clk"])}</td><td>{cpm(a["imp"], a["spend"])}</td>'
                     f'{px_cells(a)}{cpl_html(a["spend"], a["leads"])}</tr>')
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
                     f'<td>{pct(c["imp"], c["clk"])}</td><td>{cpm(c["imp"], c["spend"])}</td>'
                     f'{px_cells(c)}{cpl_html(c["spend"], c["leads"])}</tr>')
            body += (f'<tr class="det" id="{cid}"><td colspan="11"><div class="sw">{ad_table(acct, c["cid"])}</div></td></tr>')
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
            rej = rej_by_acct.get(acct, 0)
            anun = (f'<td><span class="tag r">{rej} con problema</span></td>' if rej
                    else '<td><span class="tag m">ok</span></td>')
            body += (f'<tr class="acct" onclick="t(\'{rid}\')"><td><span class="ca" id="c-{rid}">▸</span> {name}</td>'
                     f'<td>{len(cs)}</td><td>{m0(agg["spend"])}</td>'
                     f'{px_cells(agg, sem=True)}{cpl_html(agg["spend"], agg["leads"])}{anun}</tr>')
            body += (f'<tr class="det" id="{rid}"><td colspan="10"><div class="sw">{camp_table(acct, rid)}</div></td></tr>')
        if not body:
            body = '<tr><td colspan="10" class="hint">Sin cuentas.</td></tr>'
        return (f'<section id="{seg}"><h2>{title}</h2>{daterange_bar(seg)}<div class="ts"><table>'
                f'<thead><tr><th rowspan="2">Cuenta</th><th rowspan="2">Camp.</th><th rowspan="2">Gasto</th>'
                f'<th colspan="5" class="grp px">Eventos del pixel</th><th rowspan="2">CPL</th><th rowspan="2">Anuncios</th></tr>'
                f'<tr>{px_head2()}</tr></thead><tbody>{body}</tbody></table></div>'
                f'<div class="lg"><span><b>CPL:</b></span><span><span class="dot" style="background:var(--g)"></span>&lt;$100</span>'
                f'<span><span class="dot" style="background:var(--a)"></span>$100–120</span><span><span class="dot" style="background:var(--r)"></span>&gt;$120</span>'
                f'<span style="margin-left:12px"><b>Leads/sem:</b></span><span><span class="dot" style="background:var(--g)"></span>≥14</span>'
                f'<span><span class="dot" style="background:var(--a)"></span>7–13</span><span><span class="dot" style="background:var(--r)"></span>&lt;7</span>'
                f'<span style="margin-left:12px">Clientes pot. = pixel Lead (Meta). Solic./Registros/Citas/Ventas se llenan al <b>conectar GHL</b>.</span></div></section>')

    # tercera vista: conexiones GHL
    ghl_rows = ""
    for a in accts:
        ghl_rows += (f'<tr><td>{a[1]}</td><td>{a[2]}</td><td class="hint">{a[0]}</td>'
                     f'<td class="hint">—</td><td><span class="tag a">falta PIT</span></td><td class="hint">utm_campaign</td></tr>')
    ghl_view = (f'<section id="ghl"><h2>Conexiones GHL (una por subcuenta)</h2>'
                f'<div class="callout">Aquí se conecta cada subcuenta de Go High Level con su <b>Private Integration Token (PIT)</b>. '
                f'Con eso el cockpit trae <b>leads reales, citas y ventas</b> (las columnas que hoy salen “—”). '
                f'Pásame el PIT + el nombre del campo UTM de un cliente y lo dejo conectado.</div>'
                f'<div class="ts"><table><thead><tr><th>Cuenta</th><th>Vista</th><th>Meta ID</th><th>GHL location</th><th>PIT</th><th>Campo UTM</th></tr></thead>'
                f'<tbody>{ghl_rows}</tbody></table></div></section>')

    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Centiads by NetUs — DATOS REALES</title>
<style>
:root{{--bg:#0b0f17;--panel:#131a26;--p2:#0f1520;--bd:#223046;--tx:#e6edf6;--mu:#8ea0b8;--ac:#4f9cff;--r:#ff5c72;--a:#ffbf47;--g:#37d39a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
.bn{{background:linear-gradient(90deg,rgba(55,211,154,.16),rgba(79,156,255,.10));border-bottom:1px solid var(--bd);padding:8px 20px;font-size:13px;color:var(--mu)}}.bn b{{color:var(--tx)}}
.nav{{display:flex;gap:4px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--bd);background:var(--p2)}}.nav .br{{font-weight:700;margin-right:16px}}
.nav button{{padding:6px 12px;border-radius:8px;color:var(--mu);background:transparent;border:none;cursor:pointer;font:inherit}}.nav button.on{{background:var(--panel);color:var(--tx)}}
.wrap{{padding:20px;max-width:1320px;margin:0 auto}}h2{{font-size:14px;color:var(--mu);text-transform:uppercase;letter-spacing:.04em;margin:6px 0 8px}}
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
.dr{{display:flex;gap:8px;align-items:center;margin:0 0 12px;flex-wrap:wrap}}.dr select,.dr input{{background:var(--p2);border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:6px 9px}}.dr .lbl{{color:var(--mu);font-size:12px}}.btn{{background:var(--ac);color:#04101f;border:none;border-radius:8px;padding:6px 12px;font-weight:700;cursor:pointer}}
section{{display:none}}section.on{{display:block}}
</style></head><body>
<div class="bn">🟢 <b>DATOS REALES</b> de tu base en EasyPanel · últimos 7 días · cuenta → campañas → anuncios.</div>
<nav class="nav"><span class="br">🛰️ Centiads <span style="color:var(--mu);font-weight:400;font-size:12px">by NetUs</span></span>
<button data-t="client" class="on">Clientes</button><button data-t="netus">NetUs</button><button data-t="ghl">Conexiones GHL</button></nav>
<div class="wrap">
{view('client', 'Vista Clientes').replace('<section id="client">', '<section id="client" class="on">')}
{view('netus', 'Vista NetUs (campañas propias)')}
{ghl_view}
</div>
<script>
document.querySelectorAll('.nav button').forEach(function(b){{b.onclick=function(){{
 document.querySelectorAll('.nav button').forEach(function(x){{x.classList.remove('on')}});
 document.querySelectorAll('section').forEach(function(s){{s.classList.remove('on')}});
 b.classList.add('on');document.getElementById(b.dataset.t).classList.add('on');}}}});
function t(id){{event.stopPropagation();document.getElementById(id).classList.toggle('on');
 var c=document.getElementById('c-'+id);c.textContent=c.textContent=='▸'?'▾':'▸';}}
function pr(seg){{var p=document.getElementById('p-'+seg).value;document.getElementById('cr-'+seg).style.display=p==='custom'?'inline':'none';if(p!=='custom')document.getElementById('cap-'+seg).textContent=p+' · (el filtro por fecha queda 100% activo en la plataforma desplegada)';}}
function pc(seg){{var s=document.getElementById('s-'+seg).value,u=document.getElementById('u-'+seg).value;document.getElementById('cap-'+seg).textContent=s+' a '+u+' · (el filtro por fecha queda 100% activo en la plataforma desplegada)';}}
</script></body></html>"""
    (ROOT / "preview" / "live.html").write_text(html)
    print("OK -> preview/live.html")


if __name__ == "__main__":
    render()
