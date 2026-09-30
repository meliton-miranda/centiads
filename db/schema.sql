-- =============================================================================
-- NetUs Ads Cockpit — esquema Postgres (Hostinger)
-- Backbone de datos. La ingesta diaria (agente Claude vía MCP de Meta + GHL PIT)
-- hace UPSERT sobre estas tablas. Vercel solo LEE.
-- Ejecutar una vez:  psql "$DATABASE_URL" -f db/schema.sql
-- =============================================================================

create schema if not exists cockpit;
set search_path to cockpit, public;

-- 1) Mapa de cuentas / clientes ----------------------------------------------
-- Un renglón por cuenta publicitaria de Meta ligada a su subcuenta GHL.
create table if not exists clients (
  id                 bigint generated always as identity primary key,
  name               text not null,
  meta_ad_account_id text not null unique,          -- id numérico de Meta (sin prefijo act_)
  meta_business_id   text,
  ghl_location_id    text,                           -- location de la subcuenta GHL
  ghl_pit            text,                           -- Private Integration Token (alta manual)
  ghl_utm_field      text default 'utm_campaign',    -- custom field del contacto que trae el campaign_id/utm
  currency           text not null default 'MXN',    -- MXN | USD | INR
  segment            text not null default 'client'  check (segment in ('client','netus')),
  mcp_enabled        boolean not null default true,  -- is_ads_mcp_enabled (Meta)
  active             boolean not null default true,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

-- 2) Métricas de campaña por día (Meta Insights vía MCP) ----------------------
create table if not exists meta_campaign_daily (
  meta_ad_account_id text not null,
  campaign_id        text not null,
  campaign_name      text,
  objective          text,
  status             text,                           -- effective_status de la campaña
  date               date not null,
  spend              numeric(14,2) not null default 0,
  impressions        bigint  not null default 0,
  clicks             bigint  not null default 0,
  ctr                numeric(8,4),                   -- %
  cpm                numeric(12,4),
  leads              integer not null default 0,     -- Lead (clientes potenciales)
  ev_solicitud       integer not null default 0,     -- SubmitApplication (solicitudes enviadas)
  ev_registro        integer not null default 0,     -- CompleteRegistration (registros completados)
  ev_cita            integer not null default 0,      -- Schedule (citas programadas)
  ev_venta           integer not null default 0,      -- Purchase (ventas)
  cpl                numeric(12,4),
  currency           text not null default 'MXN',
  spend_usd          numeric(14,2),                  -- normalizado (ver fx_rates)
  cpl_usd            numeric(12,4),
  synced_at          timestamptz not null default now(),
  primary key (meta_ad_account_id, campaign_id, date)
);
create index if not exists idx_mcd_date on meta_campaign_daily(date);
create index if not exists idx_mcd_acct on meta_campaign_daily(meta_ad_account_id);

-- 2b) Métricas por ANUNCIO por día (nivel ad — para ver qué anuncio genera conversiones)
create table if not exists meta_ad_daily (
  meta_ad_account_id text not null,
  ad_id              text not null,
  ad_name            text,
  campaign_id        text,
  adset_id           text,
  date               date not null,
  status             text,                           -- effective_status del anuncio
  spend              numeric(14,2) not null default 0,
  impressions        bigint  not null default 0,
  clicks             bigint  not null default 0,
  ctr                numeric(8,4),
  cpm                numeric(12,4),
  leads              integer not null default 0,     -- Lead
  ev_solicitud       integer not null default 0,     -- SubmitApplication
  ev_registro        integer not null default 0,     -- CompleteRegistration
  ev_cita            integer not null default 0,      -- Schedule
  ev_venta           integer not null default 0,      -- Purchase
  cpl                numeric(12,4),
  currency           text not null default 'MXN',
  spend_usd          numeric(14,2),
  synced_at          timestamptz not null default now(),
  primary key (meta_ad_account_id, ad_id, date)
);
create index if not exists idx_mad_date on meta_ad_daily(date);
create index if not exists idx_mad_campaign on meta_ad_daily(campaign_id);

-- 3) Eventos de conversión del pixel por campaña/día --------------------------
-- action_type = etiqueta de Meta (p.ej. lead, offsite_conversion.fb_pixel_lead, purchase)
create table if not exists pixel_events_daily (
  meta_ad_account_id text not null,
  campaign_id        text not null,
  date               date not null,
  action_type        text not null,
  count              integer not null default 0,
  value              numeric(14,2) not null default 0,
  synced_at          timestamptz not null default now(),
  primary key (meta_ad_account_id, campaign_id, date, action_type)
);

-- 4) Estado de anuncios: rechazos / errores de entrega / creación -------------
-- Foto de estado actual (no serie temporal). Un renglón por ad.
create table if not exists meta_ad_status (
  meta_ad_account_id text not null,
  ad_id              text not null,
  ad_name            text,
  campaign_id        text,
  adset_id           text,
  effective_status   text,                           -- ACTIVE, DISAPPROVED, WITH_ISSUES, PAUSED...
  review_feedback    jsonb,                          -- ad_review_feedback (motivo del rechazo)
  delivery_issues    jsonb,                          -- issues_info / ads_get_errors
  has_rejection      boolean generated always as
                       (coalesce(effective_status,'') in ('DISAPPROVED','WITH_ISSUES')) stored,
  created_time       timestamptz,                    -- para "anuncio nuevo esta semana"
  synced_at          timestamptz not null default now(),
  primary key (meta_ad_account_id, ad_id)
);
create index if not exists idx_mas_acct on meta_ad_status(meta_ad_account_id);

-- 5) Intereses de targeting por ad set ---------------------------------------
create table if not exists adset_interests (
  meta_ad_account_id text not null,
  adset_id           text not null,
  adset_name         text,
  interest_id        text not null,
  interest_name      text,
  synced_at          timestamptz not null default now(),
  primary key (meta_ad_account_id, adset_id, interest_id)
);
create index if not exists idx_ai_acct on adset_interests(meta_ad_account_id);

-- 6) Embudo GHL por subcuenta/día (oportunidades que alcanzaron cada stage) ---
create table if not exists ghl_funnel_daily (
  ghl_location_id text not null,
  date            date not null,
  pipeline_id     text,
  stage_id        text not null,
  stage_name      text not null,
  opp_count       integer not null default 0,       -- oportunidades que llegaron a este stage ese día
  synced_at       timestamptz not null default now(),
  primary key (ghl_location_id, date, stage_id)
);

-- 7) Atribución por oportunidad (join Meta<->GHL por UTM, a nivel subcuenta) --
create table if not exists attribution (
  ghl_location_id    text not null,
  opportunity_id     text not null,
  contact_id         text,
  meta_ad_account_id text,
  campaign_id        text,                           -- derivado del UTM del contacto
  utm_raw            text,
  current_stage      text,
  is_appointment     boolean not null default false, -- Agendó/Confirmó/Asistió
  is_sale            boolean not null default false, -- Contrató
  lead_date          date,
  appointment_date   date,
  sale_date          date,
  monetary_value     numeric(14,2),
  currency           text,
  synced_at          timestamptz not null default now(),
  primary key (ghl_location_id, opportunity_id)
);
create index if not exists idx_attr_campaign on attribution(campaign_id);
create index if not exists idx_attr_acct on attribution(meta_ad_account_id);

-- 7b) Creativos por anuncio (imagen/miniatura para la vista "Control de Ads") --
-- Foto actual: un renglón por ad. Se refresca en cada ingesta.
create table if not exists ad_creatives (
  meta_ad_account_id  text not null,
  ad_id               text not null,
  creative_id         text,
  thumbnail_url       text,                          -- URL firmada de Meta (expira; se refresca a diario)
  image_url           text,
  title               text,                          -- headline
  body                text,                          -- texto principal
  call_to_action_type text,
  link_url            text,
  synced_at           timestamptz not null default now(),
  primary key (meta_ad_account_id, ad_id)
);

-- Atribución a nivel ANUNCIO: utm_content trae el ad_id (o el nombre del anuncio) y
-- "asistió" separa a quien llegó a la cita de quien solo la agendó.
alter table attribution add column if not exists utm_content text;
alter table attribution add column if not exists is_attended boolean not null default false;

-- Meta de CPL por cuenta (Control de Ads: semáforo y detección de "fugas").
alter table clients add column if not exists target_cpl numeric(12,2) not null default 100;

-- 7c) Centiads app: usuarios, ajustes, tablero por cuenta y banca de creativos --
create extension if not exists pgcrypto;  -- gen_random_uuid() (nativo en PG13+, por si acaso)

create table if not exists app_users (
  email      text primary key check (email = lower(email)),
  pass_hash  text not null,                         -- pbkdf2$rounds$salt$hash (scripts/add_user.py)
  role       text not null default 'admin',
  active     boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists app_settings (         -- session_secret (auto) e ingest_key_sha256
  key   text primary key,
  value text not null
);

create table if not exists centiads_payloads (    -- tablero de Control de Ads (preview/build_*.py → /api/ingest)
  account_id text primary key,
  name       text not null,
  payload    jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists centiads_banca (       -- creativos en espera (imagen dentro de la fila)
  id          uuid primary key default gen_random_uuid(),
  account_id  text not null,
  campaign_id text,
  name        text not null,
  headline    text,
  body        text,
  image       bytea not null,                      -- el diseño (PNG/JPG/WebP, máx 10 MB)
  image_type  text not null,
  status      text not null default 'en_espera'
              check (status in ('en_espera', 'publicar', 'publicado', 'descartado')),
  meta_ad_id  text,
  notes       text,
  created_by  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists idx_banca_account on centiads_banca(account_id, status);

-- 8) Tipos de cambio para normalizar a USD -----------------------------------
-- rate_to_usd: 1 unidad de `currency` = rate_to_usd USD (USD se guarda con 1.0)
create table if not exists fx_rates (
  date        date not null,
  currency    text not null,
  rate_to_usd numeric(14,8) not null,
  primary key (date, currency)
);

-- 9) Auditoría de corridas de ingesta ----------------------------------------
create table if not exists sync_runs (
  id              bigint generated always as identity primary key,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  kind            text not null default 'daily',     -- daily | backfill | manual
  status          text not null default 'running',   -- running | ok | error
  accounts_ok     integer default 0,
  accounts_failed integer default 0,
  notes           text
);

-- =============================================================================
-- Vistas de conveniencia para el dashboard
-- =============================================================================

-- Salud por cuenta: leads del último día sincronizado, última fecha con leads,
-- rechazo activo y "anuncio nuevo esta semana". Alimenta las alertas.
create or replace view v_account_health as
with latest as (select coalesce(max(date), current_date - 1) as d from meta_campaign_daily)
select
  c.name,
  c.meta_ad_account_id,
  c.segment,
  c.currency,
  c.active,
  c.mcp_enabled,
  (select d from latest) as as_of,
  coalesce((select sum(m.leads) from meta_campaign_daily m
              where m.meta_ad_account_id = c.meta_ad_account_id
                and m.date = (select d from latest)), 0) as leads_asof,
  coalesce((select sum(m.spend) from meta_campaign_daily m
              where m.meta_ad_account_id = c.meta_ad_account_id
                and m.date = (select d from latest)), 0) as spend_asof,
  (select max(m.date) from meta_campaign_daily m
     where m.meta_ad_account_id = c.meta_ad_account_id and m.leads > 0) as last_lead_date,
  exists(select 1 from meta_ad_status s
           where s.meta_ad_account_id = c.meta_ad_account_id and s.has_rejection) as has_rejection,
  exists(select 1 from meta_ad_status s
           where s.meta_ad_account_id = c.meta_ad_account_id
             and s.created_time >= now() - interval '7 days') as nuevo_anuncio_semana
from clients c;

-- Rendimiento por campaña + conversión GHL (leads/citas/ventas y costos derivados).
-- Agrega toda la historia; el dashboard filtra por rango de fecha sobre las tablas base.
create or replace view v_campaign_perf as
select
  m.meta_ad_account_id,
  c.name as client_name,
  c.segment,
  m.campaign_id,
  m.campaign_name,
  sum(m.spend)        as spend,
  sum(m.spend_usd)    as spend_usd,
  sum(m.impressions)  as impressions,
  sum(m.clicks)       as clicks,
  sum(m.leads)        as leads,
  case when sum(m.impressions) > 0
       then round(100.0 * sum(m.clicks) / sum(m.impressions), 4) end as ctr,
  case when sum(m.impressions) > 0
       then round(1000.0 * sum(m.spend) / sum(m.impressions), 4) end as cpm,
  case when sum(m.leads) > 0
       then round(sum(m.spend) / sum(m.leads), 2) end as cpl,
  coalesce((select count(*) from attribution a
              where a.campaign_id = m.campaign_id and a.is_appointment), 0) as citas,
  coalesce((select count(*) from attribution a
              where a.campaign_id = m.campaign_id and a.is_sale), 0)        as ventas
from meta_campaign_daily m
join clients c on c.meta_ad_account_id = m.meta_ad_account_id
group by m.meta_ad_account_id, c.name, c.segment, m.campaign_id, m.campaign_name;
