"""Vista "Control de Ads" — galería interactiva por anuncio con empate Meta ↔ CRM (GHL).

El servidor (o la vista previa) arma un *payload* con TODA la historia diaria y la vista
calcula en el navegador: al cambiar el periodo, la campaña o el filtro se recalculan
KPIs, embudo, CRM, barra lateral y tarjetas al instante (sin recargar).

Payload (build_payload):
  d0     primer día (ISO); los días se guardan como enteros = días desde d0
  ads    [[aid, nombre, campaign_id, estado, imagen]]
  m      [[i_ad, día, gasto, impresiones, clics_enlace, leads]]        (Meta, diario)
  crm    [[campaign_id, i_ad | -1, día_lead, nivel, valor]]            (GHL, por oportunidad)
         nivel: 0 lead · 1 agendó cita · 2 asistió · 3 contrató
  camps  {campaign_id: nombre}

Convenciones:
  - Banca: anuncio PAUSADO cuyo nombre contiene "BANCA".
  - Fuga: anuncio ACTIVO que en el periodo gastó ≥ 1× CPL meta sin leads, o CPL > meta +30 %.
  - Empate CRM: utm_id = campaña; utm_content = ad_id o nombre del anuncio. Si la UTM no
    identifica el anuncio —o apunta a uno que no tuvo entrega en los 7 días previos al lead
    (utm_content copiada entre anuncios)— la oportunidad cuenta solo a nivel campaña.
"""
import html
import json
import re
from datetime import date, timedelta


def esc(v):
    return html.escape(str(v if v is not None else ""))


def _norm(x):
    """'Angulo 6- V1' ≈ 'angulo6-v1' ≈ 'vm_1'→'v1' para empatar utm_content con el nombre del anuncio."""
    return re.sub(r"[^a-z0-9]", "", (x or "").lower()).replace("vm", "v")


def match_ad(ads, cid, content):
    """Índice del anuncio al que apunta utm_content dentro de la campaña, o -1."""
    content = content or ""
    for i, a in enumerate(ads):
        if a[0] == content:
            return i
    n = _norm(content)
    if n:
        for i, a in enumerate(ads):
            if a[2] == cid and _norm(a[1]) == n:
                return i
    return -1


LAG = 7  # días máximos entre la última entrega del anuncio y la entrada del lead


def build_payload(account, ads, daily, crm, camps, d0, today, crm_cur, demo=False, remap=()):
    """ads: [(aid, name, cid, status, img)] · daily: [(aid, date, spend, imp, clk, leads)]
    crm: [(cid, utm_content, lead_date, nivel, valor)] · camps: {cid: name}
    remap: [(cid_origen, cid_destino, desde)] — los leads de cid_origen que entraron desde esa fecha
           cuentan en cid_destino (p. ej. una campaña recreada tras rechazos con los mismos anuncios)."""
    ads = [list(a) for a in ads]
    idx = {a[0]: i for i, a in enumerate(ads)}
    day = lambda d: (d - d0).days
    m = [[idx[aid], day(d), round(float(sp or 0), 2), int(im or 0), int(cl or 0), int(ld or 0)]
         for aid, d, sp, im, cl, ld in daily if aid in idx and d >= d0]
    # días con entrega por anuncio: una UTM de anuncio solo se acepta si ese anuncio tuvo entrega
    # en los LAG días previos al lead (evita atribuir a un anuncio apagado cuya utm_content se copió a otro)
    live = {}
    for r in m:
        if r[2] > 0 or r[3] > 0:
            live.setdefault(r[0], set()).add(r[1])
    k = []
    for cid, content, d, lvl, val in crm:
        if not d or d < d0:
            continue
        for src, dst, since in remap:
            if cid == src and d >= since:
                cid = dst
        i, dd = match_ad(ads, cid, content), day(d)
        if i >= 0 and not any(dd - x in live.get(i, ()) for x in range(LAG + 1)):
            i = -1
        k.append([cid, i, dd, int(lvl), float(val or 0)])
    return dict(acct=dict(id=str(account.get("id") or ""), name=account["name"], cur=account.get("currency") or "MXN",
                          target=float(account.get("target_cpl") or 100), crmCur=crm_cur),
                d0=d0.isoformat(), today=day(today), ads=ads, m=m, crm=k, camps=camps, demo=demo)


def render(accounts, account_id, payload):
    acct_opts = "".join(f'<option value="{esc(a[0])}"{" selected" if a[0] == account_id else ""}>{esc(a[1])}</option>'
                        for a in accounts)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<section class="v on" id="ads">
<div class="ah"><div><div class="hint">Control de Ads</div><h1 id="xName"></h1></div>
<form method="get" class="acsel"><input type="hidden" name="tab" value="ads">
<select name="acct" onchange="this.form.submit()">{acct_opts}</select></form></div>
<div id="xBanner"></div>
<div class="dates"><span class="lbl">📅 Periodo</span><div class="pre" id="xPre"></div>
<input type="date" id="xFrom"><span class="lbl">a</span><input type="date" id="xTo"><span class="lbl" id="xRange"></span></div>
<div class="sel-t" id="xSel"></div><div class="kpis" id="xKpis"></div>
<h3 id="xFunT"></h3><div class="funnel" id="xFun"></div>
<div class="chips" id="xChips"></div>
<div class="grid2"><aside class="side" id="xSide"></aside><div class="cards" id="xCards"></div></div>
<div id="xBanca"></div>
<h3 id="xCpaT" style="margin-top:22px"></h3><div class="ts cpa" id="xCpa"></div>
<div class="lg"><span><b>CRM:</b> oportunidades de GHL empatadas por UTM (utm_id = campaña, utm_content = anuncio), contadas por la fecha en que entró el lead.</span>
<span><b>Banca:</b> pausados con “BANCA” en el nombre.</span><span><b>Fuga:</b> activo con CPL &gt; meta +30 % o que gastó ≥ 1 CPL sin leads.</span></div>
<script type="application/json" id="adsdata">{data}</script>
</section>"""


ADS_CSS = """
.ah{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;margin-top:4px}
.ah h1{margin:0;font-size:22px}.acsel select{background:var(--p2);border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:7px 10px;max-width:260px}
.demo{background:rgba(255,191,71,.08);border:1px solid rgba(255,191,71,.35);border-radius:10px;padding:10px 14px;margin:12px 0 0;color:var(--mu);font-size:13px}.demo b{color:var(--a)}
.dates{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:14px 0 12px}.dates .lbl{color:var(--mu);font-size:12px}
.dates input{background:var(--p2);border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:6px 9px;color-scheme:dark}
.pre{display:flex;gap:4px;flex-wrap:wrap}.pre button{background:var(--panel);border:1px solid var(--bd);color:var(--mu);border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit;font-size:12px}
.pre button.on{background:var(--ac);border-color:var(--ac);color:#04101f;font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:0 0 16px}
.kpi{background:var(--panel);border:1px solid var(--bd);border-radius:12px;padding:12px 14px}.kpi span{color:var(--mu);font-size:12px;display:block}
.kpi b{font-size:22px;display:block;margin:2px 0}.kpi small{color:var(--mu);font-size:11px}.kpi.c{border-color:rgba(79,156,255,.45)}
h3{font-size:13px;color:var(--mu);font-weight:600;margin:4px 0 8px}
.funnel{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:16px}
.fs{background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:10px 12px;text-align:center}.fs span{color:var(--mu);font-size:12px;display:block}
.fs b{font-size:20px;display:block}.fs small{color:var(--mu);font-size:11px}.fs.c{border-color:rgba(79,156,255,.45)}
.cpa{margin-bottom:16px}.sel-t{font-size:15px;font-weight:700;margin:0 0 10px;scroll-margin-top:12px}.sel-t small{color:var(--mu);font-weight:400;font-size:12px;margin-left:8px}.sel-t a{color:var(--ac);cursor:pointer;font-weight:400;font-size:12px;margin-left:10px}.cpa tr.row{cursor:pointer}.cpa tr.row:hover td{background:rgba(79,156,255,.06)}
.cpa tr.sel td{background:rgba(79,156,255,.12)}.cpa tfoot td{font-weight:700;background:var(--p2)}
.cpa td.big{font-size:15px;font-weight:800}.cpa .dim{color:var(--mu)}.cpa th.c,.cpa td.c{background:rgba(79,156,255,.06)}
.cpa .cap{font-size:11px;color:var(--mu);font-weight:400}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.chip{background:var(--panel);border:1px solid var(--bd);color:var(--mu);border-radius:999px;padding:6px 12px;cursor:pointer;font:inherit;font-size:13px}
.chip em{font-style:normal;background:var(--p2);border-radius:999px;padding:0 7px;margin-left:4px;font-size:12px}
.chip.on{background:var(--tx);color:var(--bg);border-color:var(--tx)}.chip.on em{background:rgba(0,0,0,.12)}
.chip[data-f=fugas] em{color:var(--r)}.chip[data-f=banca] em{color:var(--a)}.chip[data-f=ventas] em{color:var(--g)}
.grid2{display:grid;grid-template-columns:250px 1fr;gap:14px;align-items:start}.grid2>*{min-width:0}
.side{display:flex;flex-direction:column;gap:6px;position:sticky;top:10px}
.sc{background:var(--panel);border:1px solid var(--bd);color:var(--tx);border-radius:10px;padding:9px 11px;text-align:left;cursor:pointer;font:inherit;font-size:13px;font-weight:600}
.sc small{display:block;color:var(--mu);font-weight:400;font-size:11px;margin-top:2px}.sc.on{border-color:var(--ac);background:rgba(79,156,255,.1)}
.sc .sx{display:block;color:var(--ac);font-weight:400;font-size:11px;margin-top:3px}.sc .sx b{color:var(--g)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--bd);border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
.card.win{border-color:rgba(55,211,154,.55)}
.card header{display:flex;justify-content:space-between;align-items:center;gap:6px;padding:8px 10px}
.cn{color:var(--mu);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:50%}
.pill{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;white-space:nowrap}
.pill.g{background:rgba(55,211,154,.15);color:var(--g)}.pill.r{background:rgba(255,92,114,.15);color:var(--r)}
.pill.a{background:rgba(255,191,71,.15);color:var(--a)}.pill.m{background:rgba(142,160,184,.15);color:var(--mu)}
.th{aspect-ratio:4/5;background:#0a0e15;overflow:hidden}.th img{width:100%;height:100%;object-fit:cover;display:block}
.th.ph{background:linear-gradient(160deg,var(--c),#0b0f17 85%);display:flex;flex-direction:column;justify-content:flex-end;padding:16px}
.th.ph .hl{font-size:21px;font-weight:800;line-height:1.15}
.nm{padding:8px 10px 2px;font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mx{display:grid;grid-template-columns:repeat(3,1fr);gap:6px 4px;padding:6px 10px}
.mx span{display:block;color:var(--mu);font-size:10px;text-transform:uppercase;letter-spacing:.04em}.mx b{font-size:14px}
.g{color:var(--g)}.a{color:var(--a)}.r{color:var(--r)}
.crm{border-top:1px dashed var(--bd);margin:4px 10px 0;padding-top:6px}.crm .mx{padding:4px 0}
.crm .ct{font-size:10px;color:var(--ac);text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.crm.none{color:var(--mu);font-size:11px;padding:8px 0 10px}.crm .ing{font-size:12px;color:var(--g);font-weight:700;padding:0 0 8px}
.spark{display:flex;align-items:flex-end;gap:2px;height:32px;padding:0 10px 10px;margin-top:auto}
.spark i{flex:1;background:var(--ac);border-radius:2px;opacity:.8}.spark i.z{background:var(--bd)}
.empty{color:var(--mu);padding:30px;text-align:center;grid-column:1/-1}
@media(max-width:760px){.wrap{padding:16px}.nav{overflow-x:auto;white-space:nowrap}.acsel,.acsel select{width:100%;max-width:none}
.kpis{grid-template-columns:repeat(2,1fr)}.cards{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}.grid2{grid-template-columns:1fr}
.side{position:static;flex-direction:row;overflow-x:auto}.sc{min-width:200px}.funnel{grid-template-columns:repeat(2,1fr)}}
"""

ADS_JS = r"""
window.CentiadsMount=function(P){
var A=P.acct,T=A.target,D0=new Date(P.d0+'T00:00:00'),TODAY=P.today;
var S={from:0,to:TODAY,camp:'',f:'todos'};
var FILTERS=[['todos','Todos'],['activos','Activos'],['sin_entregar','Sin entregar'],['pausados','Pausados'],['banca','Banca'],['fugas','Fugas'],['ventas','Con ventas']];
var PAL=['#1f6feb','#8957e5','#1a7f64','#bf5700','#cf222e','#0969da','#6639ba'];
function $(id){return document.getElementById(id)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function money(v,d){if(v==null||isNaN(v))return'—';return'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:d||0,maximumFractionDigits:d||0})}
function num(v){return Number(v).toLocaleString('en-US')}
function iso(n){var d=new Date(D0.getTime()+n*864e5);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
function dnum(s){return Math.round((new Date(s+'T00:00:00')-D0)/864e5)}
function clampD(n){return Math.max(0,Math.min(TODAY,n))}
function tdate(){return new Date(D0.getTime()+TODAY*864e5)}
function presets(){var t=tdate(),y=t.getFullYear(),mo=t.getMonth();
 function d(Y,M,Dd){return clampD(Math.round((new Date(Y,M,Dd)-D0)/864e5))}
 return[['7 días',clampD(TODAY-6),TODAY],['30 días',clampD(TODAY-29),TODAY],['90 días',clampD(TODAY-89),TODAY],
  ['Este mes',d(y,mo,1),TODAY],['Mes pasado',d(y,mo-1,1),d(y,mo,0)],[String(y),d(y,0,1),TODAY],[String(y-1),d(y-1,0,1),d(y-1,11,31)],['Todo',0,TODAY]]}
function cplCls(c){return c==null?'':c<=T?'g':c<=T*1.2?'a':'r'}
function zc(){return{leads:0,citas:0,asistio:0,ventas:0,ingreso:0,n:0}}
function addc(o,r){o.n++;o.leads++;if(r[3]>=1)o.citas++;if(r[3]>=2)o.asistio++;if(r[3]>=3){o.ventas++;o.ingreso+=r[4]}}

function compute(){
 var ads=P.ads.map(function(a,i){return{i:i,aid:a[0],name:a[1],cid:a[2],status:(a[3]||'').toUpperCase(),img:a[4],spend:0,imp:0,clk:0,leads:0,byDay:{},crm:null}});
 P.m.forEach(function(r){if(r[1]<S.from||r[1]>S.to)return;var a=ads[r[0]];a.spend+=r[2];a.imp+=r[3];a.clk+=r[4];a.leads+=r[5];if(r[5])a.byDay[r[1]]=(a.byDay[r[1]]||0)+r[5]});
 var camp={},adLevel={},loose={};
 P.crm.forEach(function(r){if(r[2]<S.from||r[2]>S.to)return;(camp[r[0]]=camp[r[0]]||zc());addc(camp[r[0]],r);
  if(r[1]>=0){var a=ads[r[1]];a.crm=a.crm||zc();addc(a.crm,r);adLevel[r[0]]=1}else loose[r[0]]=(loose[r[0]]||0)+1});
 ads.forEach(function(a){if(!a.crm&&adLevel[a.cid]&&!loose[a.cid])a.crm=zc();a.campOnly=!a.crm&&!!camp[a.cid];a.loose=loose[a.cid]||0;a.tags=tags(a)});
 return{ads:ads,camp:camp}}
function tags(a){var t={todos:1},s=a.status;if(a.crm&&a.crm.ventas)t.ventas=1;
 if(s=='ACTIVE'){t.activos=1;if(!a.imp)t.sin_entregar=1;if((!a.leads&&a.spend>=T)||(a.leads&&a.spend/a.leads>T*1.3))t.fugas=1}
 else if(s=='DISAPPROVED'||s=='WITH_ISSUES'){t.activos=1;t.fugas=1}
 else if(/BANCA/i.test(a.name))t.banca=1;else t.pausados=1;return t}
function relevant(a){return a.spend>0||(a.crm&&a.crm.n)||a.status=='ACTIVE'||a.tags.banca}

function pill(a){var t=a.tags,s=a.status;
 if(s=='DISAPPROVED'||s=='WITH_ISSUES')return'<span class="pill r">● Rechazado</span>';
 if(t.banca)return'<span class="pill a">● En banca</span>';if(t.fugas)return'<span class="pill r">● Fuga</span>';
 if(t.sin_entregar)return'<span class="pill m">● Sin entregar</span>';if(t.activos)return'<span class="pill g">● Activo en Meta</span>';
 return'<span class="pill m">● Pausado</span>'}
function spark(a){var n=S.to-S.from+1,b=Math.min(30,n),size=n/b,v=[];for(var i=0;i<b;i++)v.push(0);var any=0;
 for(var d in a.byDay){var k=Math.min(b-1,Math.floor((d-S.from)/size));v[k]+=a.byDay[d];any=1}
 if(!any)return'';var mx=Math.max.apply(null,v)||1;
 return'<div class="spark" title="Leads de Meta en el periodo">'+v.map(function(x){return'<i'+(x?'':' class="z"')+' style="height:'+Math.max(4,Math.round(28*x/mx))+'px"></i>'}).join('')+'</div>'}
function crmBlock(a){var c=a.crm;
 if(!c)return a.campOnly?'<div class="crm none">CRM: '+a.loose+' leads de esta campaña no traen un anuncio identificable en la UTM; sus resultados están en la campaña.</div>':'';
 return'<div class="crm"><div class="ct">En el CRM (GHL)</div><div class="mx">'+
  '<div><span>Leads CRM</span><b>'+c.leads+'</b></div><div><span>Citas</span><b>'+c.citas+'</b></div><div><span>Asistió</span><b>'+c.asistio+'</b></div>'+
  '<div><span>Ventas</span><b class="'+(c.ventas?'g':'')+'">'+c.ventas+'</b></div>'+
  '<div><span>Costo/cita</span><b>'+(c.citas?money(a.spend/c.citas):'—')+'</b></div>'+
  '<div><span>Costo/venta</span><b>'+(c.ventas?money(a.spend/c.ventas):'—')+'</b></div></div>'+
  (c.ingreso?'<div class="ing">Ingreso: '+money(c.ingreso)+' '+esc(A.crmCur)+'</div>':'')+'</div>'}
function card(a){var cpl=a.leads?a.spend/a.leads:null,img=a.img?'<div class="th"><img src="'+esc(a.img)+'" loading="lazy" alt=""></div>':
  '<div class="th ph" style="--c:'+PAL[a.i%PAL.length]+'"><div class="hl">'+esc(a.name)+'</div></div>';
 return'<article class="card'+(a.tags.ventas?' win':'')+'"><header>'+pill(a)+'<span class="cn" title="'+esc(P.camps[a.cid]||'')+'">'+esc(P.camps[a.cid]||'—')+'</span></header>'+img+
  '<div class="nm" title="'+esc(a.name)+'">'+esc(a.name)+'</div><div class="mx">'+
  '<div><span>Gasto</span><b>'+money(a.spend)+'</b></div><div><span>Leads</span><b>'+a.leads+'</b></div>'+
  '<div><span>CPL</span><b class="'+cplCls(cpl)+'">'+(cpl==null?'—':money(cpl))+'</b></div>'+
  '<div><span>CTR</span><b>'+(a.imp?(100*a.clk/a.imp).toFixed(2)+'%':'—')+'</b></div>'+
  '<div><span>CPC</span><b>'+(a.clk?money(a.spend/a.clk,2):'—')+'</b></div>'+
  '<div><span>CPM</span><b>'+(a.imp?money(1000*a.spend/a.imp,2):'—')+'</b></div></div>'+crmBlock(a)+spark(a)+'</article>'}
function cpaTable(R,byC){
 var rows=Object.keys(P.camps).map(function(c){var b=byC[c]||{spend:0,leads:0},x=R.camp[c]||zc();
  return{c:c,name:P.camps[c],spend:b.spend,leads:b.leads,crm:x.leads,citas:x.citas,asistio:x.asistio,ventas:x.ventas,ing:x.ingreso,cpa:x.ventas?b.spend/x.ventas:null}});
 rows.sort(function(a,b){if(!!a.ventas!=!!b.ventas)return a.ventas?-1:1;return a.ventas?a.cpa-b.cpa:b.spend-a.spend});
 var T0={spend:0,leads:0,crm:0,citas:0,asistio:0,ventas:0,ing:0};rows.forEach(function(r){for(var k in T0)T0[k]+=r[k]});
 function tr(r,cls,label){var act=r.spend>0||r.crm>0;
  return'<tr class="'+cls+(S.camp==r.c?' sel':'')+'" data-c="'+esc(r.c||'')+'"><td>'+label+'</td>'+
  '<td class="'+(act?'':'dim')+'">'+money(r.spend)+'</td><td>'+num(r.leads)+'</td><td>'+(r.leads?money(r.spend/r.leads):'—')+'</td>'+
  '<td class="c">'+num(r.crm)+'</td><td class="c">'+r.citas+'</td><td class="c">'+(r.citas?money(r.spend/r.citas):'—')+'</td>'+
  '<td class="c">'+r.asistio+'</td><td class="c '+(r.ventas?'g':'')+'" style="font-weight:700">'+r.ventas+'</td>'+
  '<td class="c big '+(r.ventas?'g':(r.spend?'r':'dim'))+'">'+(r.ventas?money(r.spend/r.ventas):(r.spend?'Sin ventas':'—'))+'</td>'+
  '<td class="c">'+(r.ing?money(r.ing)+' <span class="cap">'+esc(A.crmCur)+'</span>':'—')+'</td></tr>'}
 $('xCpaT').innerHTML='Costo por adquisición por campaña · '+iso(S.from)+' → '+iso(S.to)+' <span class="cap">(clic en una fila para filtrar)</span>';
 $('xCpa').innerHTML='<table><thead><tr><th>Campaña</th><th>Gasto</th><th>Leads Meta</th><th>CPL</th><th class="c">Leads CRM</th><th class="c">Citas</th>'+
  '<th class="c">Costo/cita</th><th class="c">Asistió</th><th class="c">Clientes</th><th class="c">CPA</th><th class="c">Ingreso</th></tr></thead><tbody>'+
  rows.map(function(r){return tr(r,'row',esc(r.name))}).join('')+'</tbody><tfoot>'+
  tr({c:'',spend:T0.spend,leads:T0.leads,crm:T0.crm,citas:T0.citas,asistio:T0.asistio,ventas:T0.ventas,ing:T0.ing},'row','Total cuenta')+'</tfoot></table>'}
function sum(ads,k){return ads.reduce(function(s,a){return s+a[k]},0)}

function render(){
 var R=compute(),inCamp=R.ads.filter(function(a){return!S.camp||a.cid==S.camp}),rel=inCamp.filter(relevant);
 var t={spend:sum(inCamp,'spend'),imp:sum(inCamp,'imp'),clk:sum(inCamp,'clk'),leads:sum(inCamp,'leads')},k=zc();
 Object.keys(R.camp).forEach(function(c){if(!S.camp||c==S.camp){var x=R.camp[c];['leads','citas','asistio','ventas','ingreso','n'].forEach(function(f){k[f]+=x[f]})}});
 var cpl=t.leads?t.spend/t.leads:null;
 $('xSel').innerHTML=(S.camp?'📊 '+esc(P.camps[S.camp]||S.camp)+'<a data-c="">× ver todas</a>':'📊 Todas las campañas')+'<small>'+iso(S.from)+' → '+iso(S.to)+'</small>';
 $('xRange').textContent=iso(S.from)+' → '+iso(S.to)+(S.camp?' · '+(P.camps[S.camp]||S.camp):' · todas las campañas');
 $('xFrom').value=iso(S.from);$('xTo').value=iso(S.to);
 document.querySelectorAll('#xPre button').forEach(function(b){b.classList.toggle('on',+b.dataset.f==S.from&&+b.dataset.t==S.to)});
 $('xKpis').innerHTML=
  '<div class="kpi"><span>Gasto Meta</span><b>'+money(t.spend)+'</b><small>'+esc(A.cur)+'</small></div>'+
  '<div class="kpi"><span>Leads Meta</span><b>'+num(t.leads)+'</b><small>CPL <b class="'+cplCls(cpl)+'" style="display:inline;font-size:11px">'+(cpl==null?'—':money(cpl))+'</b> · meta '+money(T)+'</small></div>'+
  '<div class="kpi"><span>Impresiones</span><b>'+num(t.imp)+'</b><small>CTR '+(t.imp?(100*t.clk/t.imp).toFixed(2)+'%':'—')+' · CPM '+(t.imp?money(1000*t.spend/t.imp,2):'—')+'</small></div>'+
  '<div class="kpi c"><span>Leads en CRM</span><b>'+num(k.leads)+'</b><small>'+(k.leads?money(t.spend/k.leads)+' por lead CRM':'sin leads en GHL')+'</small></div>'+
  '<div class="kpi c"><span>Citas (CRM)</span><b>'+k.citas+'</b><small>'+(k.citas?money(t.spend/k.citas)+' por cita · '+k.asistio+' asistieron':'—')+'</small></div>'+
  '<div class="kpi c"><span>Clientes (CRM)</span><b class="'+(k.ventas?'g':'')+'">'+k.ventas+'</b><small>'+(k.citas?(100*k.ventas/k.citas).toFixed(0)+'% de las citas cierran':'—')+'</small></div>'+
  '<div class="kpi c"><span>CPA · costo por cliente</span><b class="'+(k.ventas?'':'r')+'">'+(k.ventas?money(t.spend/k.ventas):'Sin ventas')+'</b><small>'+(k.ventas?'gasto ÷ clientes del periodo':money(t.spend)+' gastados sin cliente')+'</small></div>'+
  '<div class="kpi c"><span>Ingreso (CRM)</span><b>'+money(k.ingreso)+'</b><small>'+esc(A.crmCur)+'</small></div>';
 $('xFunT').textContent='Embudo real — '+(S.camp?(P.camps[S.camp]||''):'todas las campañas')+' · dónde se cae el prospecto';
 var st=[['Impresiones',t.imp],['Clics al enlace',t.clk],['Leads Meta',t.leads],['Leads en CRM',k.leads,1],['Agendó cita',k.citas,1],['Asistió',k.asistio,1],['Contrató',k.ventas,1]],prev=null;
 $('xFun').innerHTML=st.map(function(s){var r=prev?(100*s[1]/prev).toFixed(1)+'% del paso previo':'&nbsp;';prev=s[1]||null;
  return'<div class="fs'+(s[2]?' c':'')+'"><span>'+s[0]+'</span><b>'+num(s[1])+'</b><small>'+r+'</small></div>'}).join('');
 var cnt={};FILTERS.forEach(function(f){cnt[f[0]]=0});rel.forEach(function(a){for(var x in a.tags)cnt[x]++});
 $('xChips').innerHTML=FILTERS.map(function(f){return'<button class="chip'+(S.f==f[0]?' on':'')+'" data-f="'+f[0]+'">'+f[1]+' <em>'+cnt[f[0]]+'</em></button>'}).join('');
 var byC={};R.ads.forEach(function(a){var c=byC[a.cid]=byC[a.cid]||{n:0,spend:0,leads:0};if(relevant(a))c.n++;c.spend+=a.spend;c.leads+=a.leads});
 var cids=Object.keys(P.camps).filter(function(c){return(byC[c]&&byC[c].spend>0)||R.camp[c]}).sort(function(x,y){return(byC[y]?byC[y].spend:0)-(byC[x]?byC[x].spend:0)});
 var all=R.ads.filter(relevant).length,tk=zc();Object.keys(R.camp).forEach(function(c){tk.ventas+=R.camp[c].ventas});
 $('xSide').innerHTML='<button class="sc'+(S.camp?'':' on')+'" data-c="">Todas las campañas<small>'+all+' anuncios · '+money(sum(R.ads,'spend'))+'</small>'+
  '<span class="sx">'+tk.ventas+' clientes en el periodo</span></button>'+
  cids.map(function(c){var b=byC[c]||{n:0,spend:0,leads:0},x=R.camp[c];
   return'<button class="sc'+(S.camp==c?' on':'')+'" data-c="'+esc(c)+'">'+esc(P.camps[c])+'<small>'+b.n+' anuncios · '+money(b.spend)+' · '+b.leads+' leads'+(b.leads?' · '+money(b.spend/b.leads)+' CPL':'')+'</small>'+
    (x?'<span class="sx">CRM: '+x.leads+' leads · '+x.citas+' citas · '+x.asistio+' asistieron · <b>'+x.ventas+' clientes</b></span>':'')+
    '<span class="sx">CPA: <b class="'+(x&&x.ventas?'g':'r')+'">'+(x&&x.ventas?money(b.spend/x.ventas):'sin ventas')+'</b></span></button>'}).join('');
 cpaTable(R,byC);
 var order={fugas:0,ventas:1,activos:2,sin_entregar:3,banca:4,pausados:5};
 function rank(a){var r=9;for(var x in a.tags)if(order[x]!=null&&order[x]<r)r=order[x];return r}
 var list=rel.filter(function(a){return a.tags[S.f]}).sort(function(x,y){return rank(x)-rank(y)||((y.crm?y.crm.ventas:0)-(x.crm?x.crm.ventas:0))||y.spend-x.spend});
 $('xCards').innerHTML=list.length?list.map(card).join(''):'<div class="empty">Sin anuncios con actividad en este periodo'+(S.camp?' para esta campaña':'')+'.</div>';
 if(window.CentiadsBanca)window.CentiadsBanca(S,P)}

$('xName').textContent=A.name;
if(P.demo)$('xBanner').innerHTML='<div class="demo">🧪 <b>Datos de ejemplo.</b> Esta cuenta todavía no tiene campañas en Meta. En cuanto publiques tu primer anuncio y corra la ingesta diaria, aquí verás los datos reales.</div>';
$('xPre').innerHTML=presets().map(function(p){return'<button data-f="'+p[1]+'" data-t="'+p[2]+'">'+p[0]+'</button>'}).join('');
$('xPre').onclick=function(e){var b=e.target.closest('button');if(!b)return;S.from=+b.dataset.f;S.to=+b.dataset.t;render()};
$('xFrom').min=$('xTo').min=iso(0);$('xFrom').max=$('xTo').max=iso(TODAY);
$('xFrom').onchange=function(){if(this.value){S.from=clampD(dnum(this.value));if(S.from>S.to)S.to=S.from;render()}};
$('xTo').onchange=function(){if(this.value){S.to=clampD(dnum(this.value));if(S.to<S.from)S.from=S.to;render()}};
$('xChips').onclick=function(e){var b=e.target.closest('.chip');if(!b)return;S.f=b.dataset.f;render()};
function pick(c){S.camp=c;render();$('xSel').scrollIntoView({behavior:'smooth',block:'start'})}
$('xCpa').onclick=function(e){var r=e.target.closest('tr.row');if(r)pick(r.dataset.c)};
$('xSel').onclick=function(e){if(e.target.closest('a'))pick('')};
$('xSide').onclick=function(e){var b=e.target.closest('.sc');if(b)pick(b.dataset.c)};
render();
};
(function(){var el=document.getElementById('adsdata');if(el&&el.textContent.trim())window.CentiadsMount(JSON.parse(el.textContent))})();
"""


def demo_payload(account, today):
    """Anuncios de ejemplo (seguros) para cuentas sin actividad todavía: 120 días de historia."""
    import random
    rnd = random.Random(7)
    camps = {"demo_gmm": "Gastos Médicos Mayores · Leads", "demo_vida": "Seguro de Vida · Leads",
             "demo_ppr": "Retiro PPR · WhatsApp"}
    spec = [("GMM_Familia_v1", "demo_gmm", "ACTIVE", 140, 1.8, 1.9), ("GMM_Hospital_v2", "demo_gmm", "ACTIVE", 120, 1.4, 1.6),
            ("GMM_Carrusel_Precios", "demo_gmm", "ACTIVE", 90, 0.3, 0.7), ("Vida_Mama_v1", "demo_vida", "ACTIVE", 110, 1.3, 2.2),
            ("Vida_Testimonio_v1", "demo_vida", "ACTIVE", 80, 0.0, 0.9), ("PPR_Retiro_45", "demo_ppr", "ACTIVE", 100, 1.1, 1.4),
            ("PPR_Deducible_v1", "demo_ppr", "ACTIVE", 0, 0, 0), ("Vida_Joven_v1", "demo_vida", "PAUSED", 60, 0.8, 1.2),
            ("BANCA_GMM_Doctor_v3", "demo_gmm", "PAUSED", 0, 0, 0), ("BANCA_PPR_Calculadora", "demo_ppr", "PAUSED", 0, 0, 0)]
    d0 = today - timedelta(days=119)
    ads, daily, crm = [], [], []
    for i, (name, cid, st, sp, lp, ctr) in enumerate(spec):
        aid = f"demo{i}"
        ads.append((aid, name, cid, st, None))
        for n in range(120):
            if not sp or (st == "PAUSED" and n > 60):
                continue
            d = d0 + timedelta(days=n)
            s = sp * rnd.uniform(0.75, 1.2)
            im = int(s / rnd.uniform(0.045, 0.07))
            ld = max(0, int(round(rnd.gauss(lp, 0.9)))) if lp else 0
            daily.append((aid, d, s, im, int(im * ctr / 100 * rnd.uniform(0.8, 1.2)), ld))
            for _ in range(ld):
                r = rnd.random()
                crm.append((cid, aid, d, 3 if r < .08 else 2 if r < .2 else 1 if r < .35 else 0, 9800 if r < .08 else 0))
    return build_payload(account, ads, daily, crm, camps, d0, today, account.get("currency") or "MXN", demo=True)
