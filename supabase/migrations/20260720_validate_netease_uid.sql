alter table public.user_settings
  add constraint user_settings_netease_uid_digits
  check (netease_uid is null or netease_uid ~ '^[0-9]+$')
  not valid;

alter table public.user_settings
  validate constraint user_settings_netease_uid_digits;
