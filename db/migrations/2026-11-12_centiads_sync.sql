-- Actualización diaria de Centiads (scripts/centiads_sync.py): datos de Meta guardados en el cockpit
create table if not exists cockpit.centiads_meta_daily (
  ad_id       text not null,
  d           date not null,
  account_id  text not null,
  campaign_id text,
  spend       numeric(12,2) not null default 0,
  imp         integer not null default 0,
  clk         integer not null default 0,
  leads       integer not null default 0,
  primary key (ad_id, d)
);
create index if not exists centiads_meta_daily_acct on cockpit.centiads_meta_daily(account_id, d);

create table if not exists cockpit.centiads_meta_ads (
  ad_id         text primary key,
  account_id    text not null,
  name          text,
  campaign_id   text,
  campaign_name text,
  status        text,
  img           text,
  updated_at    timestamptz not null default now()
);
create index if not exists centiads_meta_ads_acct on cockpit.centiads_meta_ads(account_id);
