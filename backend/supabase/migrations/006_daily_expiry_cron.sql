-- Run the expiry safety net once per day at 00:00 UTC.
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.unschedule('expire-overdue-attempts');
    perform cron.schedule('expire-overdue-attempts', '0 0 * * *', 'select public.expire_overdue_attempts()');
  end if;
end $$;
