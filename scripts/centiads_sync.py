"""Actualización diaria de Centiads: Meta Marketing API + GoHighLevel → cockpit.centiads_payloads.

  python scripts/centiads_sync.py                    diario: últimos 10 días de Meta + oportunidades GHL desde 2025
  python scripts/centiads_sync.py --since 2025-01-01 recarga histórica de Meta desde esa fecha
  python scripts/centiads_sync.py --only <id> ...    solo esas cuentas (id del payload)
  python scripts/centiads_sync.py --seed-ads f.json  importa nombres/imágenes de anuncios borrados (una vez)

Corre dentro del contenedor centiads (usa DATABASE_URL). La configuración y los secretos (token de Meta,
PIT de cada sub-cuenta GHL) viven en cockpit.app_settings, clave sync_config; nunca en el repo.
El token de Meta (usuario, 60 días) se renueva solo en cada corrida.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse as up
import urllib.request
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ads_view  # noqa: E402
import server  # noqa: E402

GRAPH = "https://graph.facebook.com/v21.0"
GHL = "https://services.leadconnectorhq.com"
D0 = date(2025, 1, 1)
UA = "Mozilla/5.0 (Macintosh) Chrome/120"


def log(*a):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def get_json(url, headers=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503) or "limit" in body.lower():
                time.sleep(20 * (i + 1))
                continue
            raise RuntimeError(f"HTTP {e.code}: {body}")
        except urllib.error.URLError:
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"sin respuesta tras {tries} intentos: {url.split('?')[0]}")


# ---- configuración -----------------------------------------------------------
def load_cfg(con):
    r = con.run("select value from cockpit.app_settings where key = 'sync_config'")
    if not r:
        raise SystemExit("Falta sync_config en cockpit.app_settings")
    return json.loads(r[0][0])


def save_cfg(con, cfg):
    con.run("""insert into cockpit.app_settings(key, value) values ('sync_config', :v)
               on conflict (key) do update set value = excluded.value""", v=json.dumps(cfg))


def refresh_token(con, cfg):
    """Cambia el token por uno nuevo de 60 días (Meta lo permite mientras siga vigente)."""
    try:
        q = up.urlencode({"grant_type": "fb_exchange_token", "client_id": cfg["meta_app_id"],
                          "client_secret": cfg["meta_app_secret"], "fb_exchange_token": cfg["meta_token"]})
        r = get_json(f"{GRAPH}/oauth/access_token?{q}", tries=2)
        cfg["meta_token"] = r["access_token"]
        cfg["meta_token_expires"] = int(time.time()) + int(r.get("expires_in") or 0)
        dbg = get_json(f"{GRAPH}/debug_token?" + up.urlencode(
            {"input_token": cfg["meta_token"], "access_token": f"{cfg['meta_app_id']}|{cfg['meta_app_secret']}"}))
        cfg["meta_data_access_expires"] = dbg["data"].get("data_access_expires_at")
        save_cfg(con, cfg)
        log("token Meta renovado; vence", date.fromtimestamp(cfg["meta_token_expires"]),
            "· acceso a datos hasta", date.fromtimestamp(cfg["meta_data_access_expires"] or 0))
    except Exception as e:  # noqa: BLE001 — si falla, seguimos con el token actual
        log("AVISO: no se pudo renovar el token de Meta:", e)


# ---- Meta --------------------------------------------------------------------
def meta_pages(url):
    while url:
        d = get_json(url)
        yield from d.get("data") or []
        url = (d.get("paging") or {}).get("next")


def sync_meta_daily(con, cfg, acct, since, until):
    """Insights diarios por anuncio, en tramos de 1 mes, a cockpit.centiads_meta_daily."""
    n, a = 0, since
    while a <= until:
        b = min(until, (a.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1))
        q = up.urlencode({"level": "ad", "time_increment": 1, "limit": 500, "access_token": cfg["meta_token"],
                          "fields": "ad_id,ad_name,campaign_id,campaign_name,spend,impressions,actions",
                          "time_range": json.dumps({"since": a.isoformat(), "until": b.isoformat()})})
        for r in meta_pages(f"{GRAPH}/act_{acct}/insights?{q}"):
            acts = {x["action_type"]: int(float(x["value"])) for x in r.get("actions") or []}
            con.run("""insert into cockpit.centiads_meta_daily(ad_id, d, account_id, campaign_id, spend, imp, clk, leads)
                       values (:i, :d, :a, :c, :s, :im, :cl, :l)
                       on conflict (ad_id, d) do update set spend = excluded.spend, imp = excluded.imp,
                       clk = excluded.clk, leads = excluded.leads, campaign_id = excluded.campaign_id""",
                    i=r["ad_id"], d=date.fromisoformat(r["date_start"]), a=acct, c=r["campaign_id"],
                    s=float(r.get("spend") or 0), im=int(r.get("impressions") or 0),
                    cl=acts.get("link_click", 0), l=acts.get("lead", 0))
            con.run("""insert into cockpit.centiads_meta_ads(ad_id, account_id, name, campaign_id, campaign_name)
                       values (:i, :a, :n, :c, :cn)
                       on conflict (ad_id) do update set campaign_name = excluded.campaign_name,
                       name = coalesce(cockpit.centiads_meta_ads.name, excluded.name)""",
                    i=r["ad_id"], a=acct, n=r.get("ad_name"), c=r["campaign_id"], cn=r.get("campaign_name"))
            n += 1
        a = b + timedelta(days=1)
    return n


def sync_meta_ads(con, cfg, acct):
    """Nombre, estado e imagen vigente de cada anuncio (las URLs de imagen de Meta caducan)."""
    statuses = ["ACTIVE", "PAUSED", "ARCHIVED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "DISAPPROVED",
                "WITH_ISSUES", "IN_PROCESS", "PENDING_REVIEW", "PREAPPROVED", "PENDING_BILLING_INFO"]
    q = up.urlencode({"limit": 200, "access_token": cfg["meta_token"], "effective_status": json.dumps(statuses),
                      "fields": "id,name,campaign_id,campaign{name},effective_status,creative{image_url,thumbnail_url}"})
    n = 0
    for r in meta_pages(f"{GRAPH}/act_{acct}/ads?{q}"):
        cr = r.get("creative") or {}
        con.run("""insert into cockpit.centiads_meta_ads(ad_id, account_id, name, campaign_id, campaign_name, status, img, updated_at)
                   values (:i, :a, :n, :c, :cn, :st, :img, now())
                   on conflict (ad_id) do update set name = excluded.name, campaign_id = excluded.campaign_id,
                   campaign_name = coalesce(excluded.campaign_name, cockpit.centiads_meta_ads.campaign_name),
                   status = excluded.status, img = coalesce(excluded.img, cockpit.centiads_meta_ads.img),
                   updated_at = now()""",
                i=r["id"], a=acct, n=r.get("name"), c=r.get("campaign_id"),
                cn=(r.get("campaign") or {}).get("name"), st=r.get("effective_status"),
                img=cr.get("image_url") or cr.get("thumbnail_url"))
        n += 1
    return n


# ---- GoHighLevel -------------------------------------------------------------
def ghl_opportunities(cl):
    h = {"Authorization": f"Bearer {cl['pit']}", "Version": "2021-07-28", "Accept": "application/json"}
    pipes = get_json(f"{GHL}/opportunities/pipelines?locationId={cl['loc']}", h)["pipelines"]
    out, sa, sai = {}, None, None
    while True:
        q = {"location_id": cl["loc"], "status": "all", "date": D0.strftime("%m-%d-%Y"),
             "endDate": date.today().strftime("%m-%d-%Y"), "limit": 100}
        if sa:
            q.update(startAfter=sa, startAfterId=sai)
        d = get_json(f"{GHL}/opportunities/search?" + up.urlencode(q), h)
        ops = d.get("opportunities") or []
        out.update({o["id"]: o for o in ops})
        m = d.get("meta") or {}
        if len(ops) < 100 or not m.get("startAfter"):
            break
        sa, sai = m["startAfter"], m.get("startAfterId")
        time.sleep(0.3)
    return list(out.values()), pipes


def stage_levels(cl, pipes):
    """{stageId: (nivel, pagada)} · nivel 1 = cita agendada en adelante, 2 = asistió en adelante.
    Por palabras clave en el nombre de la etapa, o por prefijos de id fijos (stages_cita / stages_asist)."""
    lvl = {}
    cita_ids, asist_ids = set(cl.get("stages_cita") or ()), set(cl.get("stages_asist") or ())
    for p in pipes:
        names = [s["name"].lower() for s in p["stages"]]
        cita = next((i for i, n in enumerate(names) if "cita" in n or "agend" in n), None)
        asist = next((i for i, n in enumerate(names) if "entrevista realizada" in n or "asist" in n), None)
        for i, s in enumerate(p["stages"]):
            if cita_ids or asist_ids:
                k = s["id"][:8]
                lv = 2 if k in asist_ids else 1 if k in cita_ids else 0
            else:
                lv = 2 if asist is not None and i >= asist else 1 if cita is not None and i >= cita else 0
            lvl[s["id"]] = (lv, "pagad" in names[i])
    return lvl


def crm_rows(cl, ops, pipes, camps):
    lvl, tz, out = stage_levels(cl, pipes), timedelta(hours=cl.get("tz", 6)), []
    for o in ops:
        at = o.get("attributions") or []
        first = ([a for a in at if a.get("isFirst")] or at[:1] or [{}])[0]
        url = first.get("pageUrl") or first.get("url") or ""
        cid = up.parse_qs(up.urlparse(url).query).get("utm_id", [None])[0]
        if not cid:
            continue
        stage_lvl, paid = lvl.get(o.get("pipelineStageId"), (0, False))
        sold = o.get("status") == "won" or (paid and not cl.get("stages_cita"))
        level = 3 if sold else stage_lvl
        d = (datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")) - tz).date()
        out.append((cid, first.get("utmContent"), d, level, (o.get("monetaryValue") or 0) if sold else 0))
        camps.setdefault(cid, (cl.get("camps") or {}).get(cid) or f"Campaña {cid}")
    return out


# ---- armado del tablero ------------------------------------------------------
def build(con, cl):
    metas = cl["meta"]
    ads = [(a, n or a, c, st or "", img) for a, n, c, st, img in con.run(
        """select ad_id, name, campaign_id, status, img from cockpit.centiads_meta_ads
           where account_id = any(:m) order by ad_id""", m=metas)]
    camps = {c: n for c, n in con.run(
        """select distinct on (campaign_id) campaign_id, campaign_name from cockpit.centiads_meta_ads
           where account_id = any(:m) and campaign_name is not null order by campaign_id, updated_at desc""", m=metas)}
    for k, v in (cl.get("camps") or {}).items():
        camps.setdefault(k, v)
    daily = [(a, d, float(s), im, ck, ld) for a, d, s, im, ck, ld in con.run(
        """select ad_id, d, spend, imp, clk, leads from cockpit.centiads_meta_daily
           where account_id = any(:m) and d >= :d0 and (spend > 0 or imp > 0)""", m=metas, d0=D0)]
    ops, pipes = ghl_opportunities(cl)
    crm = crm_rows(cl, ops, pipes, camps)
    remap = [(a, b, date.fromisoformat(c)) for a, b, c in cl.get("remap") or ()]
    account = {"id": cl["id"], "name": cl["name"], "currency": "MXN", "target_cpl": cl.get("target_cpl", 100)}
    payload = ads_view.build_payload(account, ads, daily, crm, camps, D0, date.today(),
                                     cl.get("crm_cur", "MXN"), remap=remap)
    con.run("""insert into cockpit.centiads_payloads(account_id, name, payload, updated_at)
               values (:a, :n, cast(:p as jsonb), now())
               on conflict (account_id) do update set name = excluded.name, payload = excluded.payload,
               updated_at = now()""", a=cl["id"], n=cl["name"], p=json.dumps(payload, separators=(",", ":")))
    log(f"{cl['name']}: anuncios {len(ads)} · días-anuncio {len(payload['m'])} · gasto "
        f"${sum(r[2] for r in daily):,.0f} · oport. GHL {len(ops)} (con UTM {len(crm)}) · "
        f"ventas {sum(1 for r in payload['crm'] if r[3] == 3)}")


def seed_ads(con, path):
    rows = json.load(open(path))
    for r in rows:
        con.run("""insert into cockpit.centiads_meta_ads(ad_id, account_id, name, campaign_id, campaign_name, status, img)
                   values (:i, :a, :n, :c, :cn, :st, :img)
                   on conflict (ad_id) do update set
                   name = coalesce(cockpit.centiads_meta_ads.name, excluded.name),
                   campaign_name = coalesce(cockpit.centiads_meta_ads.campaign_name, excluded.campaign_name),
                   status = coalesce(cockpit.centiads_meta_ads.status, excluded.status),
                   img = coalesce(cockpit.centiads_meta_ads.img, excluded.img)""",
                i=r["id"], a=r["account"], n=r.get("name"), c=r.get("campaign_id"),
                cn=r.get("campaign_name"), st=r.get("status"), img=r.get("img"))
    log("anuncios importados:", len(rows))


def main():
    args = sys.argv[1:]
    server.load_env()
    con = server.connect()
    if "--seed-ads" in args:
        return seed_ads(con, args[args.index("--seed-ads") + 1])
    cfg = load_cfg(con)
    refresh_token(con, cfg)
    since = date.fromisoformat(args[args.index("--since") + 1]) if "--since" in args else date.today() - timedelta(days=10)
    only = set(args[args.index("--only") + 1:]) if "--only" in args else None
    until, fallas = date.today(), []
    for cl in cfg["clients"]:
        if only and cl["id"] not in only:
            continue
        try:
            for acct in cl["meta"]:
                n = sync_meta_daily(con, cfg, acct, max(since, D0), until)
                k = sync_meta_ads(con, cfg, acct)
                log(f"  Meta {acct}: {n} filas diarias · {k} anuncios")
            build(con, cl)
        except Exception as e:  # noqa: BLE001 — un cliente con error no detiene a los demás
            fallas.append(cl["name"])
            log(f"ERROR {cl['name']}: {e}")
    con.run("""insert into cockpit.app_settings(key, value) values ('sync_last_run', :v)
               on conflict (key) do update set value = excluded.value""",
            v=json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "fallas": fallas}))
    log("listo" + (f" · con errores en: {', '.join(fallas)}" if fallas else ""))
    if fallas:
        sys.exit(1)


if __name__ == "__main__":
    main()
