"""Centiads autoalojado (EasyPanel): sesión, API del Control de Ads, banca de creativos e ingesta.

Todo vive en el Postgres del cockpit (schema cockpit); no requiere variables ni volúmenes extra:
  cockpit.app_users          usuarios (correo + hash PBKDF2)
  cockpit.app_settings       session_secret (se genera solo) e ingest_key_sha256 (clave de carga)
  cockpit.centiads_payloads  tablero por cuenta (JSON que generan preview/build_*.py)
  cockpit.centiads_banca     creativos en espera por cuenta/campaña, con la imagen (bytea)
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from email.parser import BytesParser
from email.policy import default as email_policy

SESSION_DAYS = 14
COOKIE = "centiads_s"
MAX_UPLOAD = 10 * 1024 * 1024
IMG_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
STATUSES = ("en_espera", "publicar", "publicado", "descartado")


SETTINGS = {}


def load_settings(con):
    """Lee app_settings y genera session_secret la primera vez."""
    con.run("""insert into cockpit.app_settings(key, value) values ('session_secret', :v)
               on conflict (key) do nothing""", v=secrets.token_urlsafe(48))
    SETTINGS.update(dict(con.run("select key, value from cockpit.app_settings")))


# ---- contraseñas y sesión ----------------------------------------------------
def hash_password(pw, salt=None, rounds=240_000):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, rounds)
    return f"pbkdf2${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def check_password(pw, stored):
    try:
        _, rounds, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except Exception:
        return False


def _secret():
    s = os.environ.get("SESSION_SECRET") or SETTINGS.get("session_secret")
    if not s:
        raise RuntimeError("Falta session_secret (cockpit.app_settings)")
    return s.encode()


def make_cookie(email):
    exp = int(time.time()) + SESSION_DAYS * 86400
    body = base64.urlsafe_b64encode(json.dumps({"e": email, "x": exp}).encode()).decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return (f"{COOKIE}={body}.{sig}; Path=/; HttpOnly; Secure; SameSite=Lax; "
            f"Max-Age={SESSION_DAYS * 86400}")


def read_session(headers):
    raw = headers.get("Cookie") or ""
    m = re.search(COOKIE + r"=([^;]+)", raw)
    if not m or "." not in m.group(1):
        return None
    body, sig = m.group(1).rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(body.encode()))
    except Exception:
        return None
    return d["e"] if d.get("x", 0) > time.time() else None


def login(con, email, pw):
    rows = con.run("select pass_hash from cockpit.app_users where email = :e and active", e=email.lower().strip())
    return bool(rows) and check_password(pw, rows[0][0])


def ingest_ok(headers):
    want = os.environ.get("CENTIADS_INGEST_KEY_SHA256") or SETTINGS.get("ingest_key_sha256", "")
    got = hashlib.sha256((headers.get("x-centiads-key") or "").encode()).hexdigest()
    return bool(want) and hmac.compare_digest(want, got)


# ---- API ---------------------------------------------------------------------
def api_accounts(con):
    return [{"account_id": a, "name": n, "updated_at": u.isoformat()} for a, n, u in
            con.run("select account_id, name, updated_at from cockpit.centiads_payloads order by name")]


def api_payload(con, acct):
    r = con.run("select payload, updated_at from cockpit.centiads_payloads where account_id = :a", a=acct)
    return None if not r else {"payload": r[0][0], "updated_at": r[0][1].isoformat()}


def api_ingest(con, body):
    d = json.loads(body)
    p = d["payload"]
    acct = str(p["acct"]["id"])
    con.run("""insert into cockpit.centiads_payloads(account_id, name, payload, updated_at)
               values (:a, :n, cast(:p as jsonb), now())
               on conflict (account_id) do update set name = excluded.name, payload = excluded.payload,
               updated_at = now()""", a=acct, n=p["acct"]["name"], p=json.dumps(p, separators=(",", ":")))
    return {"ok": True, "account_id": acct}


BANCA_COLS = "id, account_id, campaign_id, name, headline, body, image_type, status, meta_ad_id, created_by, created_at"


def api_banca_list(con, acct):
    rows = con.run(f"select {BANCA_COLS} from cockpit.centiads_banca where account_id = :a order by created_at desc", a=acct)
    keys = [c.strip() for c in BANCA_COLS.split(",")]
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if k == "id" else v)
             for k, v in zip(keys, r)} for r in rows]


def parse_multipart(headers, body):
    msg = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + headers.get("Content-Type", "").encode() + b"\r\n\r\n" + body)
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if part.get_filename() is not None:
            files[name] = (part.get_content_type(), part.get_payload(decode=True) or b"")
        else:
            fields[name] = (part.get_payload(decode=True) or b"").decode("utf-8", "replace").strip()
    return fields, files


def api_banca_create(con, email, headers, body):
    fields, files = parse_multipart(headers, body)
    ctype, data = files.get("f", (None, b""))
    acct = re.sub(r"[^0-9A-Za-z_-]", "", fields.get("acct", ""))
    if not acct or not fields.get("name"):
        raise ValueError("Faltan la cuenta o el nombre del anuncio")
    if ctype not in IMG_TYPES or not data or len(data) > MAX_UPLOAD:
        raise ValueError("La imagen debe ser PNG, JPG o WebP de máximo 10 MB")
    con.run("""insert into cockpit.centiads_banca(account_id, campaign_id, name, headline, body, image, image_type, created_by)
               values (:a, :c, :n, :h, :b, :i, :t, :u)""",
            a=acct, c=fields.get("camp") or None, n=fields["name"][:200], h=fields.get("headline") or None,
            b=fields.get("body") or None, i=data, t=ctype, u=email)
    return {"ok": True}


def api_banca_status(con, item_id, body):
    st = json.loads(body).get("status")
    if st not in STATUSES:
        raise ValueError("Estado inválido")
    con.run("update cockpit.centiads_banca set status = :s, updated_at = now() where id = cast(:i as uuid)", s=st, i=item_id)
    return {"ok": True}


def banca_image(con, item_id):
    if not re.fullmatch(r"[0-9a-f-]{36}", item_id or ""):
        return None
    r = con.run("select image, image_type from cockpit.centiads_banca where id = cast(:i as uuid)", i=item_id)
    return (bytes(r[0][0]), r[0][1]) if r else None


# ---- páginas -----------------------------------------------------------------
LOGIN_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Centiads · Entrar</title><style>
:root{--bg:#0b0f17;--panel:#131a26;--p2:#0f1520;--bd:#223046;--tx:#e6edf6;--mu:#8ea0b8;--ac:#4f9cff;--r:#ff5c72}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.login{max-width:360px;margin:12vh auto;background:var(--panel);border:1px solid var(--bd);border-radius:16px;padding:26px}
h1{font-size:20px;margin:0 0 4px}p{color:var(--mu);font-size:13px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;background:var(--p2);border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:10px 12px;margin-bottom:10px;font:inherit}
button{width:100%;background:var(--ac);color:#04101f;border:none;border-radius:8px;padding:10px;font-weight:700;cursor:pointer;font:inherit;font-weight:700}
.err{color:var(--r);font-size:13px;margin-top:10px}
@media(max-width:420px){.login{margin:8vh 16px}}
</style></head><body><form class="login" method="post" action="/login"><h1>🛰️ Centiads</h1>
<p>Plataforma de anuncios NetUs. Acceso solo para el equipo autorizado.</p>
<input name="email" type="email" placeholder="tu@netus.mx" autocomplete="username" required>
<input name="password" type="password" placeholder="Contraseña" autocomplete="current-password" required>
<input type="hidden" name="next" value="{next}"><button>Entrar</button>{err}</form></body></html>"""


def login_page(next_url="/", err=False):
    safe = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"
    return LOGIN_HTML.replace("{next}", safe.replace('"', "")).replace(
        "{err}", '<div class="err">Correo o contraseña incorrectos.</div>' if err else "")


APP_JS = r"""
(function(){
var ACCT=null,ITEMS=[],LAST={S:null,P:null};
function $(id){return document.getElementById(id)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
async function api(p,o){var r=await fetch(p,o||{});if(r.status==401){location.href='/login?next=/app';throw new Error('sesión')}
 var j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
async function start(){
 var a=await api('/api/accounts'),sel=document.querySelector('#ads .acsel select');
 sel.innerHTML=a.map(function(x){return'<option value="'+esc(x.account_id)+'">'+esc(x.name)+'</option>'}).join('');
 sel.onchange=function(){load(sel.value)};sel.form.onsubmit=function(e){e.preventDefault()};
 var want=null;try{want=localStorage.getItem('centiads.acct')}catch(e){}
 if(!a.some(function(x){return x.account_id==want}))want=a.length?a[0].account_id:null;
 if(want){sel.value=want;load(want)}else $('xCards').innerHTML='<div class="empty">Aún no hay cuentas cargadas.</div>'}
async function load(id){ACCT=id;try{localStorage.setItem('centiads.acct',id)}catch(e){}
 var r=await api('/api/payload?acct='+encodeURIComponent(id));var P=r.payload;P.acct.id=id;
 $('upd').textContent='Datos al '+new Date(r.updated_at).toLocaleString('es-MX');
 await fetchBanca();window.CentiadsMount(P)}
async function fetchBanca(){try{ITEMS=await api('/api/banca?acct='+encodeURIComponent(ACCT))}catch(e){ITEMS=[]}}
function url(id){return'/banca-img/'+id}
var ST={en_espera:['a','En espera'],publicar:['g','Listo para publicar'],publicado:['m','Publicado'],descartado:['r','Descartado']};
window.CentiadsBanca=function(S,P){
 LAST={S:S,P:P};var box=$('xBanca');if(!box)return;
 var camps=P.camps,list=ITEMS.filter(function(x){return x.status!='descartado'&&(!S.camp||x.campaign_id==S.camp)});
 var opts=Object.keys(camps).map(function(c){return'<option value="'+esc(c)+'"'+(S.camp==c?' selected':'')+'>'+esc(camps[c])+'</option>'}).join('');
 box.innerHTML='<div class="banca"><h3>Banca de creativos · '+(S.camp?esc(camps[S.camp]):'todas las campañas')+' <span class="cap">('+list.filter(function(x){return x.status=='en_espera'}).length+' en espera)</span></h3>'+
  '<form class="bup" id="bForm"><div><label>Diseño (PNG, JPG o WebP · máx 10 MB)</label><input type="file" name="f" accept="image/png,image/jpeg,image/webp" required></div>'+
  '<div><label>Nombre del anuncio</label><input name="name" required placeholder="PPR_Retiro_v4"></div>'+
  '<div><label>Campaña destino</label><select name="camp">'+opts+'</select></div>'+
  '<div><label>Titular</label><input name="headline"></div><div><label>Texto principal</label><textarea name="body"></textarea></div>'+
  '<div><button class="btn" type="submit" id="bBtn">Subir a la banca</button></div></form>'+
  '<div class="bgrid">'+(list.length?list.map(function(x){var s=ST[x.status]||['m',x.status];
   return'<div class="bitem"><img src="'+esc(url(x.id))+'" loading="lazy" alt=""><div class="bi"><span class="pill '+s[0]+'">● '+s[1]+'</span><b>'+esc(x.name)+'</b>'+
    '<small>'+esc(camps[x.campaign_id]||'Sin campaña')+'</small>'+(x.headline?'<small>'+esc(x.headline)+'</small>':'')+(x.meta_ad_id?'<small>Anuncio Meta: '+esc(x.meta_ad_id)+'</small>':'')+'</div>'+
    '<div class="ba">'+(x.status=='en_espera'?'<button class="go" data-a="publicar" data-id="'+x.id+'">Listo para publicar</button>':'')+
    (x.status=='publicar'?'<button data-a="en_espera" data-id="'+x.id+'">Regresar a espera</button>':'')+
    (x.status!='publicado'?'<button data-a="descartado" data-id="'+x.id+'">Descartar</button>':'')+'</div></div>'}).join(''):'<div class="empty">Aún no hay diseños en la banca'+(S.camp?' de esta campaña':'')+'.</div>')+'</div>'+
  '<p class="bnote">“Listo para publicar” no gasta nada: pídele a Claude que lo publique y lo sube a Meta <b>pausado</b> en la campaña destino; tú decides cuándo activarlo.</p></div>';
 $('bForm').onsubmit=upload;
 box.querySelectorAll('.ba button').forEach(function(b){b.onclick=async function(){b.disabled=true;
  try{await api('/api/banca/'+b.dataset.id+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:b.dataset.a})})}catch(e){alert(e.message)}
  await fetchBanca();window.CentiadsBanca(LAST.S,LAST.P)}})};
async function upload(e){e.preventDefault();var f=e.target,file=f.f.files[0];if(!file)return;
 if(file.size>10485760){alert('La imagen pesa más de 10 MB.');return}
 var btn=$('bBtn');btn.disabled=true;btn.textContent='Subiendo…';
 var fd=new FormData(f);fd.append('acct',ACCT);
 try{await api('/api/banca',{method:'POST',body:fd})}catch(err){alert('No se pudo subir: '+err.message)}
 await fetchBanca();window.CentiadsBanca(LAST.S,LAST.P)}
start().catch(function(e){if(e.message!='sesión')alert('Error: '+e.message)});
})();
"""

APP_CSS = """
.top{display:flex;align-items:center;gap:10px;padding:12px 20px;border-bottom:1px solid var(--bd);background:var(--p2);flex-wrap:wrap}
.top .br{font-weight:700}.top .sp{flex:1}.top .who{color:var(--mu);font-size:12px}.top a.nl{color:var(--mu);padding:4px 8px;border-radius:6px}
.btn.sec{background:var(--p2);color:var(--tx);border:1px solid var(--bd)}
.banca{background:var(--panel);border:1px solid var(--bd);border-radius:14px;padding:14px;margin-top:22px}
.banca h3{margin:0 0 10px}.bup{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;align-items:end;margin-bottom:12px}
.bup label{font-size:11px;color:var(--mu);display:block}.bup input,.bup select,.bup textarea{width:100%;background:var(--p2);border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:7px 9px;font:inherit;font-size:13px}
.bup textarea{min-height:36px;resize:vertical}.bgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
.bitem{background:var(--p2);border:1px solid var(--bd);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.bitem img{width:100%;aspect-ratio:4/5;object-fit:cover;display:block;background:#0a0e15}
.bitem .bi{padding:8px 10px;font-size:12px}.bitem .bi b{display:block;font-size:13px}.bitem .bi small{color:var(--mu);display:block}
.bitem .ba{display:flex;gap:6px;flex-wrap:wrap;padding:0 10px 10px}.bitem .ba button{font:inherit;font-size:11px;border-radius:6px;border:1px solid var(--bd);background:var(--panel);color:var(--tx);padding:4px 8px;cursor:pointer}
.bitem .ba button.go{background:var(--ac);color:#04101f;border-color:var(--ac);font-weight:700}
.bnote{color:var(--mu);font-size:12px;margin:8px 0 0}
"""


def app_page(email, page_tpl, ads_view):
    """Control de Ads + Banca; los datos llegan por /api (no se incrustan en el HTML)."""
    section = re.sub(r'<script type="application/json" id="adsdata">.*?</script>', "",
                     ads_view.render([], "", None), flags=re.S)
    base_css = re.search(r"<style>(.*?)\{extra_css\}", page_tpl, re.S).group(1).replace("{{", "{").replace("}}", "}")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Centiads · Control de Ads</title>
<style>{base_css}{ads_view.ADS_CSS}{APP_CSS}</style></head><body>
<div class="top"><span class="br">🛰️ Centiads <span style="color:var(--mu);font-weight:400;font-size:12px">by NetUs</span></span>
<a class="nl" href="/?tab=client">Clientes</a><a class="nl" href="/?tab=netus">NetUs</a><a class="nl" href="/?tab=ghl">Cuentas · GHL</a>
<span class="sp"></span><span class="who" id="upd"></span><span class="who">{email}</span>
<form method="post" action="/logout" style="margin:0"><button class="btn sec">Salir</button></form></div>
<div class="wrap">{section}</div>
<script>{ads_view.ADS_JS}</script><script>{APP_JS}</script></body></html>"""
