-- Supabase schema for RSI Tracker Alerts (single-alert model)

-- 1) Alerts table
create table if not exists public.rsi_tracker_alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade,
  user_email text not null,
  timeframe text not null check (timeframe in ('5M','15M','30M','1H','4H','1D','1W')),
  rsi_period int2 not null check (rsi_period = 14) default 14,
  rsi_overbought int2 not null check (rsi_overbought between 60 and 90) default 70,
  rsi_oversold int2 not null check (rsi_oversold between 10 and 40) default 30,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint rsi_ob_gt_os check (rsi_overbought > rsi_oversold),
  constraint uniq_rsi_tracker_user unique (user_id)
);

create index if not exists idx_rsi_tracker_alerts_email on public.rsi_tracker_alerts (user_email);

-- Triggers table removed per product decision

-- updated_at trigger
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists set_rsi_tracker_alerts_updated_at on public.rsi_tracker_alerts;
create trigger set_rsi_tracker_alerts_updated_at
before update on public.rsi_tracker_alerts
for each row execute function public.set_updated_at();

-- RLS
alter table public.rsi_tracker_alerts enable row level security;
-- (no trigger table)

-- Policies: alerts (owner by user_id or user_email)
drop policy if exists rsi_tracker_alerts_select on public.rsi_tracker_alerts;
create policy rsi_tracker_alerts_select on public.rsi_tracker_alerts
  for select using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists rsi_tracker_alerts_ins on public.rsi_tracker_alerts;
create policy rsi_tracker_alerts_ins on public.rsi_tracker_alerts
  for insert with check (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists rsi_tracker_alerts_upd on public.rsi_tracker_alerts;
create policy rsi_tracker_alerts_upd on public.rsi_tracker_alerts
  for update using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  ) with check (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists rsi_tracker_alerts_del on public.rsi_tracker_alerts;
create policy rsi_tracker_alerts_del on public.rsi_tracker_alerts
  for delete using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

-- (no trigger policies)


