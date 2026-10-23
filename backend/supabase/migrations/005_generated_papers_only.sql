-- Student paper generation now uses generated questions only.
-- Keep p_source in the signature for compatibility with existing clients and grants.
create or replace function create_paper(
  p_blueprint_id    int default null,
  p_source          text default 'generated',
  p_visibility      text default 'private',
  p_idempotency_key text default null,
  p_title           text default null
) returns uuid
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_uid uuid := auth.uid();
  v_bp blueprints%rowtype;
  v_paper uuid;
  r record;
  v_pick uuid[];
  v_all uuid[] := '{}';
  v_missing jsonb := '[]'::jsonb;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  if p_visibility not in ('public', 'private') then raise exception 'bad_request' using detail = 'visibility'; end if;

  if p_idempotency_key is not null then
    select id into v_paper from papers where created_by = v_uid and idempotency_key = p_idempotency_key;
    if found then return v_paper; end if;
  end if;
  if (select count(*) from papers where created_by = v_uid and created_at > now() - interval '1 day') >= 30 then
    raise exception 'rate_limited' using detail = 'max 30 papers per 24h';
  end if;

  if p_blueprint_id is null then
    select * into v_bp from blueprints where active order by random() limit 1;
  else
    select * into v_bp from blueprints where id = p_blueprint_id and active;
  end if;
  if not found then raise exception 'blueprint_not_found'; end if;

  for r in select br.*, s.code from blueprint_rules br join subjects s on s.id = br.subject_id
           where br.blueprint_id = v_bp.id and br.qcount > 0 order by s.sort_order, br.marks loop
    select coalesce(array_agg(id), '{}') into v_pick from (
      select q.id from questions q
      where q.status = 'approved' and not q.flagged and q.subject_id = r.subject_id and q.marks = r.marks
        and q.source = 'generated' and not (q.id = any (v_all))
      order by exists (select 1 from user_question_seen s where s.user_id = v_uid and s.question_id = q.id), random()
      limit r.qcount
    ) t;
    if coalesce(array_length(v_pick, 1), 0) < r.qcount then
      v_missing := v_missing || jsonb_build_object('subject', r.code, 'marks', r.marks, 'needed', r.qcount, 'available', coalesce(array_length(v_pick, 1), 0));
    end if;
    v_all := v_all || v_pick;
  end loop;
  if jsonb_array_length(v_missing) > 0 then raise exception 'insufficient_questions' using detail = v_missing::text; end if;

  begin
    insert into papers (title, blueprint_id, created_by, visibility, duration_min, total_marks, total_questions, idempotency_key)
    values (coalesce(nullif(trim(p_title), ''), 'GATE CSE Mock - ' || to_char(now() at time zone 'utc', 'DD Mon YYYY HH24:MI')), v_bp.id, v_uid, p_visibility, v_bp.duration_min, v_bp.total_marks, v_bp.total_questions, p_idempotency_key)
    returning id into v_paper;
  exception when unique_violation then
    select id into v_paper from papers where created_by = v_uid and idempotency_key = p_idempotency_key;
    return v_paper;
  end;
  insert into paper_questions (paper_id, position, question_id, subject_id, qtype, marks)
  select v_paper, row_number() over (order by (s.section = 'CS'), q.marks, random()), q.id, q.subject_id, q.type, q.marks
  from questions q join subjects s on s.id = q.subject_id where q.id = any (v_all);
  return v_paper;
end $$;
