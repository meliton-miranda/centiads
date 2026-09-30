#!/usr/bin/env python3
"""Genera la app web de Centiads (Control de Ads + Banca) para desplegar en Vercel → app/index.html

La app no lleva datos: al iniciar sesión (Supabase Auth del proyecto ghl-mcp) lee de Supabase
  - centiads_payloads  → el tablero de cada cuenta (JSON gzip+base64, lo sube scripts/push_payload.py)
  - centiads_banca     → creativos en espera por cuenta/campaña
  - storage 'centiads-banca' → las imágenes de la banca
Solo los correos de centiads_members pueden leer o escribir (RLS).

  python3 scripts/build_app.py
"""
import re
from pathlib import Path

import ads_view

ROOT = Path(__file__).resolve().parent.parent
SUPABASE_URL = "https://onfxjbngwrkbnjboqksv.supabase.co"
SUPABASE_KEY = "sb_publishable_dUy-R-FmkemEos_0nZLgrA_GJ6diRn1"  # llave pública (publishable); el acceso lo controla RLS

BANCA_CSS = """
.login{max-width:380px;margin:12vh auto;background:var(--panel);border:1px solid var(--bd);border-radius:16px;padding:26px}
.login h1{font-size:20px;margin:0 0 4px}.login p{color:var(--mu);font-size:13px;margin:0 0 16px}
.login input{width:100%;background:var(--p2);border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:10px 12px;margin-bottom:10px;font:inherit}
.login .row{display:flex;gap:8px}.login .row .btn{flex:1}.btn.sec{background:var(--p2);color:var(--tx);border:1px solid var(--bd)}
.msg{font-size:13px;margin-top:10px;color:var(--mu)}.msg.err{color:var(--r)}.msg.ok{color:var(--g)}
.top{display:flex;align-items:center;gap:10px;padding:12px 20px;border-bottom:1px solid var(--bd);background:var(--p2)}
.top .br{font-weight:700}.top .sp{flex:1}.top .who{color:var(--mu);font-size:12px}
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

APP_JS = r"""
(function(){
var SB=supabase.createClient('__URL__','__KEY__');
var ME=null,ACCT=null,ITEMS=[],LOADED=null,LAST={S:null,P:null};
function $(id){return document.getElementById(id)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function show(v){$('vLogin').style.display=v=='login'?'':'none';$('vApp').style.display=v=='app'?'':'none'}
function msg(t,c){var m=$('lMsg');m.textContent=t;m.className='msg '+(c||'')}

async function gunzip(b64){var bin=Uint8Array.from(atob(b64),function(c){return c.charCodeAt(0)});
 var s=new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'));return JSON.parse(await new Response(s).text())}

async function start(){
 var r=await SB.auth.getSession();ME=r.data.session&&r.data.session.user;
 if(!ME){show('login');return}
 var m=await SB.from('centiads_members').select('email');
 if(m.error||!m.data.length){show('login');msg('Tu correo ('+ME.email+') no tiene acceso a Centiads.','err');await SB.auth.signOut();return}
 $('who').textContent=ME.email;show('app');
 var a=await SB.from('centiads_payloads').select('account_id,name,updated_at').order('name');
 if(a.error){alert('No pude leer las cuentas: '+a.error.message);return}
 var sel=document.querySelector('#ads .acsel select');
 sel.innerHTML=a.data.map(function(x){return'<option value="'+esc(x.account_id)+'">'+esc(x.name)+'</option>'}).join('');
 sel.onchange=function(){load(sel.value)};sel.form.onsubmit=function(e){e.preventDefault()};
 var want=null;try{want=localStorage.getItem('centiads.acct')}catch(e){}
 if(!a.data.some(function(x){return x.account_id==want}))want=a.data.length?a.data[0].account_id:null;
 if(want){sel.value=want;load(want)}}

async function load(id){
 ACCT=id;try{localStorage.setItem('centiads.acct',id)}catch(e){}
 var r=await SB.from('centiads_payloads').select('payload_gz,updated_at').eq('account_id',id).single();
 if(r.error){alert('No pude cargar la cuenta: '+r.error.message);return}
 var P=await gunzip(r.data.payload_gz);P.acct.id=id;
 $('upd').textContent='Datos al '+new Date(r.data.updated_at).toLocaleString('es-MX');
 await fetchBanca();window.CentiadsMount(P)}

async function fetchBanca(){
 var r=await SB.from('centiads_banca').select('*').eq('account_id',ACCT).order('created_at',{ascending:false});
 ITEMS=r.error?[]:r.data;LOADED=ACCT}

function url(p){return SB.storage.from('centiads-banca').getPublicUrl(p).data.publicUrl}
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
   return'<div class="bitem"><img src="'+esc(url(x.image_path))+'" loading="lazy" alt=""><div class="bi"><span class="pill '+s[0]+'">● '+s[1]+'</span><b>'+esc(x.name)+'</b>'+
    '<small>'+esc(camps[x.campaign_id]||'Sin campaña')+'</small>'+(x.headline?'<small>'+esc(x.headline)+'</small>':'')+(x.meta_ad_id?'<small>Anuncio Meta: '+esc(x.meta_ad_id)+'</small>':'')+'</div>'+
    '<div class="ba">'+(x.status=='en_espera'?'<button class="go" data-a="publicar" data-id="'+x.id+'">Listo para publicar</button>':'')+
    (x.status=='publicar'?'<button data-a="en_espera" data-id="'+x.id+'">Regresar a espera</button>':'')+
    (x.status!='publicado'?'<button data-a="descartado" data-id="'+x.id+'">Descartar</button>':'')+'</div></div>'}).join(''):'<div class="empty">Aún no hay diseños en la banca'+(S.camp?' de esta campaña':'')+'.</div>')+'</div>'+
  '<p class="bnote">“Listo para publicar” no gasta nada: pídele a Claude que lo publique y lo sube a Meta <b>pausado</b> en la campaña destino; tú decides cuándo activarlo.</p></div>';
 $('bForm').onsubmit=upload;
 box.querySelectorAll('.ba button').forEach(function(b){b.onclick=async function(){b.disabled=true;
  var r=await SB.from('centiads_banca').update({status:b.dataset.a,updated_at:new Date().toISOString()}).eq('id',b.dataset.id);
  if(r.error)alert(r.error.message);await fetchBanca();window.CentiadsBanca(LAST.S,LAST.P)}})};

async function upload(e){e.preventDefault();var f=e.target,file=f.f.files[0];if(!file)return;
 if(file.size>10485760){alert('La imagen pesa más de 10 MB.');return}
 var btn=$('bBtn');btn.disabled=true;btn.textContent='Subiendo…';
 var ext=(file.name.split('.').pop()||'png').toLowerCase(),path=ACCT+'/'+crypto.randomUUID()+'.'+ext;
 var up=await SB.storage.from('centiads-banca').upload(path,file,{contentType:file.type});
 if(up.error){alert('No se pudo subir la imagen: '+up.error.message);btn.disabled=false;btn.textContent='Subir a la banca';return}
 var r=await SB.from('centiads_banca').insert({account_id:ACCT,campaign_id:f.camp.value||null,name:f.name.value.trim(),
  headline:f.headline.value.trim()||null,body:f.body.value.trim()||null,image_path:path});
 if(r.error){alert('No se pudo guardar: '+r.error.message);await SB.storage.from('centiads-banca').remove([path])}
 await fetchBanca();window.CentiadsBanca(LAST.S,LAST.P)}

$('lPass').onclick=async function(){var em=$('lEmail').value.trim().toLowerCase(),pw=$('lPw').value;
 if(!em||!pw){msg('Escribe tu correo y contraseña.','err');return}msg('Entrando…');
 var r=await SB.auth.signInWithPassword({email:em,password:pw});if(r.error){msg('No pude entrar: '+r.error.message,'err');return}start()};
$('lLink').onclick=async function(){var em=$('lEmail').value.trim().toLowerCase();if(!em){msg('Escribe tu correo.','err');return}msg('Enviando…');
 var r=await SB.auth.signInWithOtp({email:em,options:{shouldCreateUser:false,emailRedirectTo:location.origin+location.pathname}});
 msg(r.error?'No pude enviar el enlace: '+r.error.message:'Te envié un enlace de acceso a '+em+'. Ábrelo en este navegador.',r.error?'err':'ok')};
$('out').onclick=async function(){await SB.auth.signOut();location.reload()};
SB.auth.onAuthStateChange(function(ev){if(ev=='SIGNED_IN'&&!ME)start()});
start();
})();
"""


def build():
    section = ads_view.render([], "", None)
    section = re.sub(r'<script type="application/json" id="adsdata">.*?</script>', "", section, flags=re.S)
    root_css = re.search(r":root\{\{(.*?)\}\}", __import__("server").PAGE, re.S).group(1)
    base_css = re.search(r"<style>(.*?)\{extra_css\}", __import__("server").PAGE, re.S).group(1)
    base_css = base_css.replace("{{", "{").replace("}}", "}")
    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Centiads by NetUs</title>
<style>{base_css}{ads_view.ADS_CSS}{BANCA_CSS}</style></head><body>
<div id="vLogin" style="display:none"><div class="login"><h1>🛰️ Centiads</h1><p>Control de Ads · NetUs. Acceso solo para el equipo autorizado.</p>
<input id="lEmail" type="email" placeholder="tu@netus.mx" autocomplete="username">
<input id="lPw" type="password" placeholder="Contraseña" autocomplete="current-password">
<div class="row"><button class="btn" id="lPass">Entrar</button><button class="btn sec" id="lLink">Enviarme un enlace</button></div>
<div class="msg" id="lMsg"></div></div></div>
<div id="vApp" style="display:none"><div class="top"><span class="br">🛰️ Centiads <span style="color:var(--mu);font-weight:400;font-size:12px">by NetUs</span></span>
<span class="sp"></span><span class="who" id="upd"></span><span class="who" id="who"></span><button class="btn sec" id="out">Salir</button></div>
<div class="wrap">{section}</div></div>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script>{ads_view.ADS_JS}</script>
<script>{APP_JS.replace("__URL__", SUPABASE_URL).replace("__KEY__", SUPABASE_KEY)}</script>
</body></html>"""
    out = ROOT / "app" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    print("app/index.html", len(html), "bytes")


if __name__ == "__main__":
    build()
