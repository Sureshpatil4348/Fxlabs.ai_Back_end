-- Supabase schema for Currency Strength Tracker Alerts (single-alert model)

-- 1) Alerts table
create table if not exists public.currency_strength_alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade,
  user_email text not null,
  timeframe text not null check (timeframe in ('5M','15M','30M','1H','4H','1D','1W')),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uniq_currency_strength_user unique (user_id)
);

create index if not exists idx_currency_strength_alerts_email on public.currency_strength_alerts (user_email);

-- Triggers table removed per product decision

-- updated_at trigger (shared)
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists set_currency_strength_alerts_updated_at on public.currency_strength_alerts;
create trigger set_currency_strength_alerts_updated_at
before update on public.currency_strength_alerts
for each row execute function public.set_updated_at();

-- RLS
alter table public.currency_strength_alerts enable row level security;

-- Policies: alerts (owner by user_id or user_email)
drop policy if exists currency_strength_alerts_select on public.currency_strength_alerts;
create policy currency_strength_alerts_select on public.currency_strength_alerts
  for select using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists currency_strength_alerts_ins on public.currency_strength_alerts;
create policy currency_strength_alerts_ins on public.currency_strength_alerts
  for insert with check (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists currency_strength_alerts_upd on public.currency_strength_alerts;
create policy currency_strength_alerts_upd on public.currency_strength_alerts
  for update using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  ) with check (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

drop policy if exists currency_strength_alerts_del on public.currency_strength_alerts;
create policy currency_strength_alerts_del on public.currency_strength_alerts
  for delete using (
    (auth.uid() is not null and user_id = auth.uid()) or (auth.jwt()->>'email') = user_email
  );

-- (no trigger policies)

