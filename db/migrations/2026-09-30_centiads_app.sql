-- Centiads: Control de Ads + Banca + login (aditivo; no borra ni cambia datos existentes)
begin;
set search_path to cockpit, public;
create extension if not exists pgcrypto;

create table if not exists ad_creatives (
  meta_ad_account_id text not null, ad_id text not null, creative_id text, thumbnail_url text, image_url text,
  title text, body text, call_to_action_type text, link_url text,
  synced_at timestamptz not null default now(), primary key (meta_ad_account_id, ad_id));
alter table attribution add column if not exists utm_content text;
alter table attribution add column if not exists is_attended boolean not null default false;
alter table clients add column if not exists target_cpl numeric(12,2) not null default 100;

create table if not exists app_users (
  email text primary key check (email = lower(email)), pass_hash text not null,
  role text not null default 'admin', active boolean not null default true, created_at timestamptz not null default now());
create table if not exists app_settings (key text primary key, value text not null);
create table if not exists centiads_payloads (
  account_id text primary key, name text not null, payload jsonb not null, updated_at timestamptz not null default now());
create table if not exists centiads_banca (
  id uuid primary key default gen_random_uuid(), account_id text not null, campaign_id text, name text not null,
  headline text, body text, image bytea not null, image_type text not null,
  status text not null default 'en_espera' check (status in ('en_espera','publicar','publicado','descartado')),
  meta_ad_id text, notes text, created_by text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now());
create index if not exists idx_banca_account on centiads_banca(account_id, status);

insert into app_settings(key, value) values ('ingest_key_sha256', '<sha256 de .centiads_ingest_key>')
  on conflict (key) do update set value = excluded.value;
commit;
