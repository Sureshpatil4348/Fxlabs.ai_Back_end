-- Supabase schema for Heatmap Custom Indicator Tracker Alerts (single-alert model)

-- 1) Alerts table
create table if not exists public.heatmap_indicator_tracker_alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade,
  user_email text not null,
  pairs jsonb not null, -- array of 1-3 symbols
  timeframe text not null check (timeframe in ('5M','15M','30M','1H','4H','1D','1W')),
  indicator text not null check (indicator in ('ema21','ema50','ema200','macd','rsi','utbot','ichimokuclone')),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ind_pairs_bounds check (
    jsonb_typeof(pairs) = 'array' and jsonb_array_length(pairs) between 1 and 3
  ),
  constraint uniq_heatmap_indicator_tracker_user unique (user_id)
);

create index if not exists idx_heatmap_indicator_tracker_alerts_email on public.heatmap_indicator_tracker_alerts (user_email);

-- Triggers table removed per product decision

-- updated_at trigger
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists set_heatmap_indicator_tracker_alerts_updated_at on public.heatmap_indicator_tracker_alerts;
create trigger set_heatmap_indicator_tracker_alerts_updated_at
before update on public.heatmap_indicator_tracker_alerts
for each row execute function public.set_updated_at();

-- RLS
alter table public.heatmap_indicator_tracker_alerts enable row level security;
-- (no trigger table)

-- Policies: alerts (owner by user_id or user_email)
drop policy if exists heatmap_indicator_tracker_alerts_select on public.heatmap_indicator_tracker_alerts;
create policy heatmap_indicator_tracker_alerts_select on public.heatmap_indicator_tracker_alerts
  for select using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists heatmap_indicator_tracker_alerts_ins on public.heatmap_indicator_tracker_alerts;
create policy heatmap_indicator_tracker_alerts_ins on public.heatmap_indicator_tracker_alerts
  for insert with check (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists heatmap_indicator_tracker_alerts_upd on public.heatmap_indicator_tracker_alerts;
create policy heatmap_indicator_tracker_alerts_upd on public.heatmap_indicator_tracker_alerts
  for update using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  ) with check (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists heatmap_indicator_tracker_alerts_del on public.heatmap_indicator_tracker_alerts;
create policy heatmap_indicator_tracker_alerts_del on public.heatmap_indicator_tracker_alerts
  for delete using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

-- (no trigger policies)


