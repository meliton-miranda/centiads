// =============================================================================
// NetUs Ads Cockpit — loader determinista de la ingesta a Postgres
//
// El agente Claude arma un payload JSON con lo que sacó del MCP de Meta y de GHL
// (ver ingest/routine.md para el shape) y este script hace el UPSERT.
//
//   node scripts/load.mjs ingest/staging/2026-07-05.json
//   cat payload.json | node scripts/load.mjs -
//
// Requiere: DATABASE_URL en el entorno (Postgres de Hostinger).
// =============================================================================
import fs from 'node:fs';
import pg from 'pg';

const src = process.argv[2];
if (!src) {
  console.error('uso: node scripts/load.mjs <payload.json | ->');
  process.exit(1);
}
const raw = src === '-' ? fs.readFileSync(0, 'utf8') : fs.readFileSync(src, 'utf8');
const payload = JSON.parse(raw);

const { DATABASE_URL } = process.env;
if (!DATABASE_URL) {
  console.error('falta DATABASE_URL en el entorno');
  process.exit(1);
}

const pool = new pg.Pool({
  connectionString: DATABASE_URL,
  ssl: process.env.PGSSL === 'disable' ? false : { rejectUnauthorized: false },
});

// El MCP de Meta devuelve valores formateados como texto ("$1,386.46 MXN", "2.13%",
// "13,578"). numify limpia cualquier símbolo y deja el número (o null).
const numify = (v) => {
  if (v == null) return null;
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  const cleaned = String(v).replace(/[^0-9.\-]/g, '');
  if (cleaned === '' || cleaned === '-' || cleaned === '.') return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
};

// fx: { MXN: 0.058, INR: 0.012, USD: 1 }
const fxMap = Object.fromEntries((payload.fx ?? []).map((r) => [r.currency, numify(r.rate_to_usd)]));
fxMap.USD ??= 1;
const toUsd = (amount, currency) => {
  const a = numify(amount);
  const r = fxMap[currency];
  return a == null || r == null ? null : Number((a * r).toFixed(4));
};

async function run() {
  const client = await pool.connect();
  let ok = 0;
  let failed = 0;
  const runRes = await client.query(
    `insert into cockpit.sync_runs(kind, status) values($1,'running') returning id`,
    [payload.kind ?? 'daily'],
  );
  const runId = runRes.rows[0].id;

  try {
    // ---- fx_rates -----------------------------------------------------------
    for (const [currency, rate] of Object.entries(fxMap)) {
      await client.query(
        `insert into cockpit.fx_rates(date, currency, rate_to_usd) values($1,$2,$3)
         on conflict (date, currency) do update set rate_to_usd = excluded.rate_to_usd`,
        [payload.as_of_date, currency, rate],
      );
    }

    // ---- Meta, por cuenta ---------------------------------------------------
    for (const acct of payload.accounts ?? []) {
      const A = acct.meta_ad_account_id;
      try {
        await client.query('begin');

        for (const c of acct.campaigns_daily ?? []) {
          const spend = numify(c.spend) ?? 0;
          const impressions = numify(c.impressions) ?? 0;
          const clicks = numify(c.clicks) ?? 0;
          const ctr = numify(c.ctr);
          const cpm = numify(c.cpm);
          const leads = numify(c.leads) ?? 0;
          // CPL: usar el provisto, o calcular spend/leads (Meta no expone cost_per_lead directo)
          let cpl = numify(c.cpl);
          if ((cpl == null || cpl === 0) && leads > 0) cpl = Number((spend / leads).toFixed(4));
          await client.query(
            `insert into cockpit.meta_campaign_daily
               (meta_ad_account_id,campaign_id,campaign_name,objective,status,date,
                spend,impressions,clicks,ctr,cpm,leads,ev_solicitud,ev_registro,ev_cita,ev_venta,
                cpl,currency,spend_usd,cpl_usd)
             values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
             on conflict (meta_ad_account_id,campaign_id,date) do update set
               campaign_name=excluded.campaign_name, objective=excluded.objective,
               status=excluded.status, spend=excluded.spend, impressions=excluded.impressions,
               clicks=excluded.clicks, ctr=excluded.ctr, cpm=excluded.cpm, leads=excluded.leads,
               ev_solicitud=excluded.ev_solicitud, ev_registro=excluded.ev_registro,
               ev_cita=excluded.ev_cita, ev_venta=excluded.ev_venta,
               cpl=excluded.cpl, currency=excluded.currency, spend_usd=excluded.spend_usd,
               cpl_usd=excluded.cpl_usd, synced_at=now()`,
            [A, c.campaign_id, c.campaign_name, c.objective, c.status, c.date,
             spend, impressions, clicks, ctr, cpm, leads,
             numify(c.ev_solicitud) ?? 0, numify(c.ev_registro) ?? 0, numify(c.ev_cita) ?? 0, numify(c.ev_venta) ?? 0,
             cpl, c.currency, toUsd(spend, c.currency), toUsd(cpl, c.currency)],
          );
        }

        // métricas por anuncio (nivel ad) → meta_ad_daily
        for (const d of acct.ads_daily ?? []) {
          const spend = numify(d.spend) ?? 0;
          const leads = numify(d.leads) ?? 0;
          let cpl = numify(d.cpl);
          if ((cpl == null || cpl === 0) && leads > 0) cpl = Number((spend / leads).toFixed(4));
          await client.query(
            `insert into cockpit.meta_ad_daily
               (meta_ad_account_id,ad_id,ad_name,campaign_id,adset_id,date,status,
                spend,impressions,clicks,ctr,cpm,leads,ev_solicitud,ev_registro,ev_cita,ev_venta,
                cpl,currency,spend_usd)
             values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
             on conflict (meta_ad_account_id,ad_id,date) do update set
               ad_name=excluded.ad_name, campaign_id=excluded.campaign_id, adset_id=excluded.adset_id,
               status=excluded.status, spend=excluded.spend, impressions=excluded.impressions,
               clicks=excluded.clicks, ctr=excluded.ctr, cpm=excluded.cpm, leads=excluded.leads,
               ev_solicitud=excluded.ev_solicitud, ev_registro=excluded.ev_registro,
               ev_cita=excluded.ev_cita, ev_venta=excluded.ev_venta, cpl=excluded.cpl,
               currency=excluded.currency, spend_usd=excluded.spend_usd, synced_at=now()`,
            [A, d.ad_id, d.ad_name, d.campaign_id, d.adset_id, d.date, d.status,
             spend, numify(d.impressions) ?? 0, numify(d.clicks) ?? 0, numify(d.ctr), numify(d.cpm), leads,
             numify(d.ev_solicitud) ?? 0, numify(d.ev_registro) ?? 0, numify(d.ev_cita) ?? 0, numify(d.ev_venta) ?? 0,
             cpl, d.currency, toUsd(spend, d.currency)],
          );
        }

        for (const p of acct.pixel_events_daily ?? []) {
          await client.query(
            `insert into cockpit.pixel_events_daily
               (meta_ad_account_id,campaign_id,date,action_type,count,value)
             values ($1,$2,$3,$4,$5,$6)
             on conflict (meta_ad_account_id,campaign_id,date,action_type) do update set
               count=excluded.count, value=excluded.value, synced_at=now()`,
            [A, p.campaign_id, p.date, p.action_type, numify(p.count) ?? 0, numify(p.value) ?? 0],
          );
        }

        // estado de ads e intereses = foto actual: refrescar (delete + insert)
        if (acct.ad_status) {
          await client.query(`delete from cockpit.meta_ad_status where meta_ad_account_id=$1`, [A]);
          for (const s of acct.ad_status) {
            await client.query(
              `insert into cockpit.meta_ad_status
                 (meta_ad_account_id,ad_id,ad_name,campaign_id,adset_id,effective_status,
                  review_feedback,delivery_issues,created_time)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
              [A, s.ad_id, s.ad_name, s.campaign_id, s.adset_id, s.effective_status,
               s.review_feedback == null ? null : JSON.stringify(s.review_feedback),
               s.delivery_issues == null ? null : JSON.stringify(s.delivery_issues),
               s.created_time],
            );
          }
        }
        if (acct.adset_interests) {
          await client.query(`delete from cockpit.adset_interests where meta_ad_account_id=$1`, [A]);
          for (const i of acct.adset_interests) {
            await client.query(
              `insert into cockpit.adset_interests
                 (meta_ad_account_id,adset_id,adset_name,interest_id,interest_name)
               values ($1,$2,$3,$4,$5)
               on conflict (meta_ad_account_id,adset_id,interest_id) do nothing`,
              [A, i.adset_id, i.adset_name, i.interest_id, i.interest_name],
            );
          }
        }

        await client.query('commit');
        ok += 1;
      } catch (e) {
        await client.query('rollback');
        failed += 1;
        console.error(`cuenta ${A} falló:`, e.message);
      }
    }

    // ---- GHL, por subcuenta -------------------------------------------------
    for (const loc of payload.ghl ?? []) {
      const L = loc.ghl_location_id;
      try {
        await client.query('begin');
        for (const f of loc.funnel_daily ?? []) {
          await client.query(
            `insert into cockpit.ghl_funnel_daily
               (ghl_location_id,date,pipeline_id,stage_id,stage_name,opp_count)
             values ($1,$2,$3,$4,$5,$6)
             on conflict (ghl_location_id,date,stage_id) do update set
               opp_count=excluded.opp_count, stage_name=excluded.stage_name, synced_at=now()`,
            [L, f.date, f.pipeline_id, f.stage_id, f.stage_name, f.opp_count ?? 0],
          );
        }
        for (const a of loc.attribution ?? []) {
          await client.query(
            `insert into cockpit.attribution
               (ghl_location_id,opportunity_id,contact_id,meta_ad_account_id,campaign_id,utm_raw,
                current_stage,is_appointment,is_sale,lead_date,appointment_date,sale_date,
                monetary_value,currency)
             values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
             on conflict (ghl_location_id,opportunity_id) do update set
               contact_id=excluded.contact_id, meta_ad_account_id=excluded.meta_ad_account_id,
               campaign_id=excluded.campaign_id, utm_raw=excluded.utm_raw,
               current_stage=excluded.current_stage, is_appointment=excluded.is_appointment,
               is_sale=excluded.is_sale, lead_date=excluded.lead_date,
               appointment_date=excluded.appointment_date, sale_date=excluded.sale_date,
               monetary_value=excluded.monetary_value, currency=excluded.currency, synced_at=now()`,
            [L, a.opportunity_id, a.contact_id, a.meta_ad_account_id, a.campaign_id, a.utm_raw,
             a.current_stage, a.is_appointment ?? false, a.is_sale ?? false,
             a.lead_date, a.appointment_date, a.sale_date, a.monetary_value, a.currency],
          );
        }
        await client.query('commit');
        ok += 1;
      } catch (e) {
        await client.query('rollback');
        failed += 1;
        console.error(`location ${L} falló:`, e.message);
      }
    }

    await client.query(
      `update cockpit.sync_runs set finished_at=now(), status=$2, accounts_ok=$3, accounts_failed=$4 where id=$1`,
      [runId, failed ? 'error' : 'ok', ok, failed],
    );
    console.log(`load ok — run ${runId}: ${ok} bloques ok, ${failed} fallidos`);
  } catch (e) {
    await client.query(
      `update cockpit.sync_runs set finished_at=now(), status='error', notes=$2 where id=$1`,
      [runId, String(e.message)],
    );
    throw e;
  } finally {
    client.release();
    await pool.end();
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
