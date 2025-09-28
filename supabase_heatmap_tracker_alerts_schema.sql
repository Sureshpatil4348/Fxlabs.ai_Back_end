-- Supabase schema for Heatmap/Quantum Analysis Tracker Alerts (single-alert model)

-- 1) Alerts table
create table if not exists public.heatmap_tracker_alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade,
  user_email text not null,
  pairs jsonb not null, -- array of 1-3 symbols
  trading_style text not null check (trading_style in ('scalper','swingTrader')),
  buy_threshold int2 not null check (buy_threshold between 0 and 100) default 70,
  sell_threshold int2 not null check (sell_threshold between 0 and 100) default 30,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint heatmap_pairs_bounds check (
    jsonb_typeof(pairs) = 'array' and jsonb_array_length(pairs) between 1 and 3
  ),
  constraint uniq_heatmap_tracker_user unique (user_id)
);

create index if not exists idx_heatmap_tracker_alerts_email on public.heatmap_tracker_alerts (user_email);

-- 2) Triggers table (append-only)
create table if not exists public.heatmap_tracker_alert_triggers (
  id uuid primary key default gen_random_uuid(),
  alert_id uuid not null references public.heatmap_tracker_alerts(id) on delete cascade,
  triggered_at timestamptz not null default now(),
  symbol text not null,
  trigger_type text not null check (trigger_type in ('buy','sell')),
  buy_percent numeric(5,2) null,
  sell_percent numeric(5,2) null,
  final_score numeric(6,2) null,
  created_at timestamptz not null default now()
);

-- updated_at trigger
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists set_heatmap_tracker_alerts_updated_at on public.heatmap_tracker_alerts;
create trigger set_heatmap_tracker_alerts_updated_at
before update on public.heatmap_tracker_alerts
for each row execute function public.set_updated_at();

-- RLS
alter table public.heatmap_tracker_alerts enable row level security;
alter table public.heatmap_tracker_alert_triggers enable row level security;

-- Policies: alerts (owner by user_id or user_email)
drop policy if exists heatmap_tracker_alerts_select on public.heatmap_tracker_alerts;
create policy heatmap_tracker_alerts_select on public.heatmap_tracker_alerts
  for select using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists heatmap_tracker_alerts_ins on public.heatmap_tracker_alerts;
create policy heatmap_tracker_alerts_ins on public.heatmap_tracker_alerts
  for insert with check (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists heatmap_tracker_alerts_upd on public.heatmap_tracker_alerts;
create policy heatmap_tracker_alerts_upd on public.heatmap_tracker_alerts
  for update using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  ) with check (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists heatmap_tracker_alerts_del on public.heatmap_tracker_alerts;
create policy heatmap_tracker_alerts_del on public.heatmap_tracker_alerts
  for delete using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

-- Policies: triggers (read/insert own only)
drop policy if exists heatmap_triggers_select on public.heatmap_tracker_alert_triggers;
create policy heatmap_triggers_select on public.heatmap_tracker_alert_triggers
  for select using (
    exists (
      select 1 from public.heatmap_tracker_alerts a
      where a.id = alert_id
        and (
          (auth.uid() is not null and a.user_id = auth.uid()) or (auth.jwt()->>'email') = a.user_email
        )
    )
  );

drop policy if exists heatmap_triggers_insert on public.heatmap_tracker_alert_triggers;
create policy heatmap_triggers_insert on public.heatmap_tracker_alert_triggers
  for insert with check (
    exists (
      select 1 from public.heatmap_tracker_alerts a
      where a.id = alert_id
        and (
          (auth.uid() is not null and a.user_id = auth.uid()) or (auth.jwt()->>'email') = a.user_email
        )
    )
  );


