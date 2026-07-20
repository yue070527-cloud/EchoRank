insert into storage.buckets (id, name, public)
values ('echorank-state', 'echorank-state', false)
on conflict (id) do update
set public = false;
