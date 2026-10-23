-- =====================================================================================
-- 003_security.sql  |  Row Level Security, grants, scheduled jobs
-- Principle: the browser (anon/authenticated roles) can read almost nothing directly.
--   * questions, question_keys, paper_questions are completely closed (no policy, no grant) -> answers cannot leak
--   * students call the RPC functions from 002 (SECURITY DEFINER, they check auth.uid() themselves)
--   * the service_role key (server only / Colab) is the only thing that may call admin_* functions
-- NEW TABLES: Supabase auto-grants them to anon/authenticated. Re-run the REVOKE block below after adding any table.
-- =====================================================================================

alter table subjects              enable row level security;
alter table topics                enable row level security;
alter table blueprints            enable row level security;
alter table blueprint_rules       enable row level security;
alter table profiles              enable row level security;
alter table generation_batches    enable row level security;
alter table questions             enable row level security;
alter table question_keys         enable row level security;
alter table papers                enable row level security;
alter table paper_questions       enable row level security;
alter table attempts              enable row level security;
alter table attempt_answers       enable row level security;
alter table attempt_subject_stats enable row level security;
alter table user_question_seen    enable row level security;
alter table question_reports      enable row level security;

-- public reference data (the UI can show the syllabus / weightage without a function call)
drop policy if exists ref_subjects  on subjects;        create policy ref_subjects  on subjects        for select to anon, authenticated using (true);
drop policy if exists ref_topics    on topics;          create policy ref_topics    on topics          for select to anon, authenticated using (true);
drop policy if exists ref_blueprints on blueprints;     create policy ref_blueprints on blueprints     for select to authenticated using (active);
drop policy if exists ref_bprules   on blueprint_rules; create policy ref_bprules   on blueprint_rules for select to authenticated using (true);

-- a user sees and edits only their own profile
drop policy if exists profiles_own_select on profiles;  create policy profiles_own_select on profiles for select to authenticated using (id = auth.uid());
drop policy if exists profiles_own_update on profiles;  create policy profiles_own_update on profiles for update to authenticated
  using (id = auth.uid()) with check (id = auth.uid());

-- papers: public ones + your own
drop policy if exists papers_visible on papers;
create policy papers_visible on papers for select to authenticated using (visibility = 'public' or created_by = auth.uid());

-- results: only your own attempts (answers/scores are written by the DB functions, never by the client)
drop policy if exists attempts_own on attempts;                create policy attempts_own on attempts for select to authenticated using (user_id = auth.uid());
drop policy if exists attempt_answers_own on attempt_answers;  create policy attempt_answers_own on attempt_answers for select to authenticated
  using (exists (select 1 from attempts a where a.id = attempt_id and a.user_id = auth.uid()));
drop policy if exists attempt_stats_own on attempt_subject_stats; create policy attempt_stats_own on attempt_subject_stats for select to authenticated
  using (exists (select 1 from attempts a where a.id = attempt_id and a.user_id = auth.uid()));

-- ---------- table privileges ----------------------------------------------------------
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on global_leaderboard from anon, authenticated;

grant select on subjects, topics to anon, authenticated;
grant select on blueprints, blueprint_rules, papers, attempts, attempt_answers, attempt_subject_stats to authenticated;
grant select on profiles to authenticated;
grant update (display_name) on profiles to authenticated;

-- ---------- function privileges -------------------------------------------------------
revoke all on all functions in schema public from public, anon, authenticated;

grant execute on function
  create_paper(int, text, text, text, text), start_attempt(uuid), get_attempt(uuid), save_answers(uuid, jsonb), submit_attempt(uuid),
  get_attempt_analysis(uuid), list_my_attempts(int, int), get_my_progress(), list_papers(text, int, int),
  get_leaderboard(uuid, int, int), get_global_leaderboard(int), report_question(uuid, text, text)
  to authenticated;

grant execute on function
  admin_upsert_questions(jsonb, jsonb), admin_bank_stats(int), admin_set_question_status(uuid[], text),
  admin_regrade_question(uuid, jsonb, text), expire_overdue_attempts(int), refresh_global_leaderboard()
  to service_role;

-- ---------- scheduled jobs (pg_cron: enable it in Dashboard > Database > Extensions) ----
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.schedule('expire-overdue-attempts', '0 0 * * *',    'select public.expire_overdue_attempts()');
    perform cron.schedule('refresh-global-leaderboard', '*/10 * * * *', 'select public.refresh_global_leaderboard()');
  else
    raise notice 'pg_cron is not enabled: enable it, or call /api/cron/expire daily from Vercel Cron instead.';
  end if;
end $$;
