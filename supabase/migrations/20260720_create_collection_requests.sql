create table public.collection_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  request_type text not null default 'initial' check (request_type = 'initial'),
  status text not null default 'pending' check (status in ('pending', 'processing', 'completed', 'failed')),
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  error text
);

create index collection_requests_pending_idx
  on public.collection_requests (requested_at)
  where status = 'pending';

create unique index collection_requests_active_user_idx
  on public.collection_requests (user_id, request_type)
  where status in ('pending', 'processing');

alter table public.collection_requests enable row level security;

create policy "Users can view own collection requests"
  on public.collection_requests
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy "Users can request own initial collection"
  on public.collection_requests
  for insert
  to authenticated
  with check (
    auth.uid() = user_id
    and request_type = 'initial'
    and status = 'pending'
    and started_at is null
    and completed_at is null
    and error is null
  );

grant select, insert on public.collection_requests to authenticated;
revoke update, delete on public.collection_requests from authenticated;
