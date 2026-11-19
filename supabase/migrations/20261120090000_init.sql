-- Gem-bot store. Run once in Supabase -> SQL Editor (personal project).
-- Row Level Security is ON with no policies: only the service-role key (server side, on the Pi)
-- can read or write. The public anon key sees nothing.

create table if not exists public.sources (
  id          text primary key,              -- 'gem', 'iitism', 'coalindia', 'jharkhand', 'cppp'
  name        text not null,
  url         text,
  status      text not null default 'ok',    -- ok | blocked | needs-login | manual
  notes       text,
  last_run_at timestamptz,
  last_error  text
);

create table if not exists public.bids (
  id               bigint generated always as identity primary key,
  source_id        text not null references public.sources(id),
  external_id      text not null,             -- GeM bid no. / tender id / institute tender no.
  bid_number       text,
  org              text,
  location         text,
  consignee_pin    text,
  region_confirmed boolean,
  item             text,
  category         text,
  bid_type         text,                      -- goods | services | works
  qty              text,
  est_value        numeric,
  emd              numeric,
  epbg_pct         numeric,
  start_date       timestamptz,
  end_date         date,
  mse_relax        text,
  startup_relax    text,
  make             text,
  model            text,
  indenter         text,
  buyer            text,
  oem_auth_required boolean,
  delivery_days    int,
  doc_url          text,
  facts            jsonb not null default '{}'::jsonb,  -- every fact with page + quote
  first_seen       timestamptz not null default now(),
  status           text not null default 'new',         -- new | watch | skip | applied
  unique (source_id, external_id)
);
create index if not exists bids_end_date_idx on public.bids (end_date);
create index if not exists bids_status_idx on public.bids (status);
create index if not exists bids_first_seen_idx on public.bids (first_seen);

create table if not exists public.analyses (
  id          bigint generated always as identity primary key,
  bid_id      bigint not null references public.bids(id) on delete cascade,
  entity      text not null,                  -- 'A' | 'B'
  rating      text not null,                  -- GREEN | AMBER | RED
  reasons     jsonb not null default '[]'::jsonb,
  blockers    jsonb not null default '[]'::jsonb,
  actions     jsonb not null default '[]'::jsonb,
  model       text,                           -- 'rules' or the Gemini model that helped
  created_at  timestamptz not null default now()
);
create index if not exists analyses_bid_idx on public.analyses (bid_id);

create table if not exists public.notifications (
  bid_id      bigint not null references public.bids(id) on delete cascade,
  sent_at     timestamptz not null default now(),
  message_id  bigint,
  primary key (bid_id, message_id)
);

alter table public.sources       enable row level security;
alter table public.bids          enable row level security;
alter table public.analyses      enable row level security;
alter table public.notifications enable row level security;

insert into public.sources (id, name, url, status, notes) values
  ('iitism',    'IIT (ISM) Dhanbad tenders',     'https://www.iitism.ac.in/tenders',           'ok',     'public HTML table'),
  ('gem',       'GeM BidPlus',                   'https://bidplus.gem.gov.in/all-bids',        'ok',     'public list, per-district keyword search'),
  ('coalindia', 'Coal India e-tender (GePNIC)',  'https://coalindiatenders.nic.in',            'ok',     'org list + details public; documents need captcha'),
  ('jharkhand', 'Jharkhand e-tenders (GePNIC)',  'https://jharkhandtenders.gov.in',            'ok',     'org list + details public; documents need captcha'),
  ('cppp',      'CPPP eprocure (GePNIC)',        'https://eprocure.gov.in',                    'ok',     'org list + details public; documents need captcha'),
  ('ireps',     'IREPS (Railways)',              'https://www.ireps.gov.in',                   'manual', 'robots.txt disallows all bots - forward links to the bot'),
  ('dvc',       'DVC',                           'https://www.dvc.gov.in',                     'manual', 'robots.txt disallows all bots - forward links to the bot')
on conflict (id) do nothing;
