-- =====================================================================================
-- 002_functions.sql  |  all exam rules live in the database (atomic, retry-safe, one place to audit)
-- Naming: functions the student API calls have no prefix; admin_* are for the service role only;
--         internal helpers (finalize_attempt, score_attempt ...) are never granted to anyone.
-- Errors are raised as  RAISE EXCEPTION '<code>'  -> the API maps the code to an HTTP status (see DESIGN doc).
-- =====================================================================================

create or replace function exam_grace() returns interval language sql immutable as
$$ select interval '20 seconds' $$;                  -- network slack after the deadline

-- ---------- small pure helpers ---------------------------------------------------------
create or replace function sort_letters(p jsonb) returns jsonb language sql immutable as $$
  select coalesce(jsonb_agg(x order by x), '[]'::jsonb) from (select distinct jsonb_array_elements_text(p) as x) t
$$;

-- validates + canonicalises what the student sent. Returns NULL for "no answer".
create or replace function normalise_response(p_type text, p_resp jsonb) returns jsonb
language plpgsql immutable as $$
declare v text; arr jsonb;
begin
  if p_resp is null or jsonb_typeof(p_resp) = 'null' then return null; end if;
  if p_type = 'NAT' then
    v := trim(p_resp #>> '{}');
    if v = '' then return null; end if;
    if v !~ '^-?[0-9]+(\.[0-9]+)?$' or length(v) > 20 then raise exception 'invalid_response'; end if;
    return to_jsonb(v);
  end if;
  if jsonb_typeof(p_resp) = 'string' then arr := jsonb_build_array(p_resp);
  elsif jsonb_typeof(p_resp) = 'array' then arr := p_resp;
  else raise exception 'invalid_response'; end if;
  if jsonb_array_length(arr) = 0 then return null; end if;
  if exists (select 1 from jsonb_array_elements(arr) e where jsonb_typeof(e) <> 'string' or (e #>> '{}') not in ('A','B','C','D')) then
    raise exception 'invalid_response';
  end if;
  arr := sort_letters(arr);
  if p_type = 'MCQ' and jsonb_array_length(arr) <> 1 then raise exception 'invalid_response'; end if;
  return arr;
end $$;

create or replace function valid_key(p_type text, p_ans jsonb) returns boolean language plpgsql immutable as $$
begin
  if p_ans is null then return false; end if;
  if p_type = 'NAT' then
    return jsonb_typeof(p_ans) = 'object' and (p_ans->>'low') ~ '^-?[0-9]+(\.[0-9]+)?$'
       and (p_ans->>'high') ~ '^-?[0-9]+(\.[0-9]+)?$' and (p_ans->>'low')::numeric <= (p_ans->>'high')::numeric;
  end if;
  return jsonb_typeof(p_ans) = 'array' and jsonb_array_length(p_ans) between 1 and 4
     and not exists (select 1 from jsonb_array_elements(p_ans) e where jsonb_typeof(e) <> 'string' or (e #>> '{}') not in ('A','B','C','D'));
end $$;

-- ====================================================================================
-- PAPER GENERATION
-- ====================================================================================
create or replace function create_paper(
  p_blueprint_id    int  default null,
  p_source          text default 'any',      -- 'any' | 'pyq' | 'generated'
  p_visibility      text default 'private',  -- 'public' papers can be taken by everyone -> shared leaderboard
  p_idempotency_key text default null,       -- client-generated UUID; a retry returns the SAME paper
  p_title           text default null
) returns uuid
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_uid     uuid := auth.uid();
  v_bp      blueprints%rowtype;
  v_paper   uuid;
  r         record;
  v_pick    uuid[];
  v_all     uuid[] := '{}';
  v_missing jsonb  := '[]'::jsonb;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  if p_source not in ('any','pyq','generated') then raise exception 'bad_request' using detail = 'source'; end if;
  if p_visibility not in ('public','private')  then raise exception 'bad_request' using detail = 'visibility'; end if;

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

  -- pick questions per rule: approved, not flagged, never twice in a paper, unseen-by-this-user first, then random
  for r in select br.*, s.code from blueprint_rules br join subjects s on s.id = br.subject_id
            where br.blueprint_id = v_bp.id and br.qcount > 0 order by s.sort_order, br.marks loop
    select coalesce(array_agg(id), '{}') into v_pick from (
      select q.id from questions q
       where q.status = 'approved' and not q.flagged
         and q.subject_id = r.subject_id and q.marks = r.marks
         and (p_source = 'any' or q.source = p_source)
         and not (q.id = any (v_all))
       order by exists (select 1 from user_question_seen s where s.user_id = v_uid and s.question_id = q.id), random()
       limit r.qcount
    ) t;
    if coalesce(array_length(v_pick, 1), 0) < r.qcount then
      v_missing := v_missing || jsonb_build_object('subject', r.code, 'marks', r.marks,
                                                   'needed', r.qcount, 'available', coalesce(array_length(v_pick, 1), 0));
    end if;
    v_all := v_all || v_pick;
  end loop;

  if jsonb_array_length(v_missing) > 0 then
    raise exception 'insufficient_questions' using detail = v_missing::text;      -- tells the admin exactly what to generate
  end if;

  begin
    insert into papers (title, blueprint_id, created_by, visibility, duration_min, total_marks, total_questions, idempotency_key)
    values (coalesce(nullif(trim(p_title), ''), 'GATE CSE Mock - ' || to_char(now() at time zone 'utc', 'DD Mon YYYY HH24:MI')),
            v_bp.id, v_uid, p_visibility, v_bp.duration_min, v_bp.total_marks, v_bp.total_questions, p_idempotency_key)
    returning id into v_paper;
  exception when unique_violation then                                           -- two identical requests raced
    select id into v_paper from papers where created_by = v_uid and idempotency_key = p_idempotency_key;
    return v_paper;
  end;

  -- GATE order: General Aptitude first, 1-mark before 2-mark, random inside each block
  insert into paper_questions (paper_id, position, question_id, subject_id, qtype, marks)
  select v_paper, row_number() over (order by (s.section = 'CS'), q.marks, random()), q.id, q.subject_id, q.type, q.marks
    from questions q join subjects s on s.id = q.subject_id
   where q.id = any (v_all);

  return v_paper;
end $$;

-- ====================================================================================
-- ATTEMPT LIFECYCLE
-- ====================================================================================
create or replace function start_attempt(p_paper_id uuid) returns uuid
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_uid uuid := auth.uid(); v_paper papers%rowtype; v_att attempts%rowtype; v_no int; v_id uuid;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select * into v_paper from papers where id = p_paper_id and (visibility = 'public' or created_by = v_uid);
  if not found then raise exception 'paper_not_found'; end if;

  perform pg_advisory_xact_lock(hashtextextended(v_uid::text || p_paper_id::text, 0));   -- double-click / two tabs

  select * into v_att from attempts where paper_id = p_paper_id and user_id = v_uid and status = 'in_progress';
  if found then
    if now() <= v_att.deadline_at + exam_grace() then return v_att.id; end if;           -- resume, deadline unchanged
    perform finalize_attempt(v_att.id, 'auto_submitted');                                -- it ran out while the user was away
  end if;

  select coalesce(max(attempt_no), 0) + 1 into v_no from attempts where paper_id = p_paper_id and user_id = v_uid;
  insert into attempts (paper_id, user_id, attempt_no, deadline_at, max_score, counts_for_rank)
  values (p_paper_id, v_uid, v_no, now() + make_interval(mins => v_paper.duration_min), v_paper.total_marks, v_no = 1)
  returning id into v_id;

  insert into attempt_answers (attempt_id, question_id) select v_id, question_id from paper_questions where paper_id = p_paper_id;
  insert into user_question_seen (user_id, question_id) select v_uid, question_id from paper_questions where paper_id = p_paper_id
  on conflict do nothing;
  return v_id;
end $$;

-- Everything the exam screen (in progress) or the review screen (after submit) needs, in one round trip.
create or replace function get_attempt(p_attempt_id uuid) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_att attempts%rowtype; v_uid uuid := auth.uid(); v_reveal boolean; v_q jsonb; v_title text;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select * into v_att from attempts where id = p_attempt_id and user_id = v_uid;
  if not found then raise exception 'attempt_not_found'; end if;
  if v_att.status = 'in_progress' and now() > v_att.deadline_at + exam_grace() then   -- lazy expiry (cron is only the backup)
    perform finalize_attempt(v_att.id, 'auto_submitted');
    select * into v_att from attempts where id = p_attempt_id;
  end if;
  v_reveal := v_att.status <> 'in_progress';
  select title into v_title from papers where id = v_att.paper_id;

  select jsonb_agg(
           jsonb_build_object(
             'position', pq.position, 'question_id', q.id, 'section', s.section, 'subject', s.name, 'subject_code', s.code,
             'type', q.type, 'marks', q.marks,
             'negative_marks', case when q.type = 'MCQ' then round(q.marks / 3.0, 4) else 0 end,
             'body_md', q.body_md, 'options', q.options,
             'response', aa.response, 'state', aa.state, 'time_spent_sec', aa.time_spent_sec)
           || case when v_reveal then jsonb_build_object('answer', k.answer, 'solution_md', k.solution_md,
                                                         'is_correct', aa.is_correct, 'marks_awarded', aa.marks_awarded)
                   else '{}'::jsonb end
           order by pq.position)
    into v_q
    from paper_questions pq
    join questions q  on q.id = pq.question_id
    join subjects s   on s.id = pq.subject_id
    join attempt_answers aa on aa.attempt_id = v_att.id and aa.question_id = pq.question_id
    left join question_keys k on k.question_id = q.id
   where pq.paper_id = v_att.paper_id;

  return jsonb_build_object(
    'server_now', now(),
    'attempt', jsonb_build_object('id', v_att.id, 'paper_id', v_att.paper_id, 'title', v_title, 'status', v_att.status,
        'attempt_no', v_att.attempt_no, 'started_at', v_att.started_at, 'deadline_at', v_att.deadline_at,
        'seconds_left', case when v_att.status = 'in_progress' then greatest(0, extract(epoch from (v_att.deadline_at - now()))::int) else 0 end,
        'submitted_at', v_att.submitted_at, 'score', v_att.score, 'max_score', v_att.max_score,
        'counts_for_rank', v_att.counts_for_rank),
    'questions', coalesce(v_q, '[]'::jsonb));
end $$;

-- Autosave. Batch-friendly (the client can flush everything it buffered while offline); out-of-order safe via seq.
create or replace function save_answers(p_attempt_id uuid, p_items jsonb) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_uid uuid := auth.uid(); v_att attempts%rowtype; it jsonb; v_qid uuid; v_type text; v_resp jsonb; v_state text;
  v_seq bigint; v_n int; v_saved int := 0; v_stale int := 0;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) > 100 then raise exception 'bad_request'; end if;
  select * into v_att from attempts where id = p_attempt_id and user_id = v_uid;
  if not found then raise exception 'attempt_not_found'; end if;
  if v_att.status <> 'in_progress' or now() > v_att.deadline_at + exam_grace() then raise exception 'attempt_closed'; end if;

  for it in select * from jsonb_array_elements(p_items) loop
    begin v_qid := (it->>'question_id')::uuid; exception when others then raise exception 'bad_request'; end;
    select qtype into v_type from paper_questions where paper_id = v_att.paper_id and question_id = v_qid;
    if not found then raise exception 'question_not_in_paper'; end if;
    v_resp  := normalise_response(v_type, it->'response');
    v_state := coalesce(it->>'state', case when v_resp is null then 'not_answered' else 'answered' end);
    if v_state not in ('not_visited','not_answered','answered','marked','answered_marked') then raise exception 'bad_request'; end if;
    if v_resp is null then                                  -- cleared answer: keep the review flag, drop 'answered'
      v_state := case v_state when 'answered' then 'not_answered' when 'answered_marked' then 'marked' else v_state end;
    elsif v_state in ('not_visited','not_answered','marked') then   -- has an answer: state must say so
      v_state := case v_state when 'marked' then 'answered_marked' else 'answered' end;
    end if;
    v_seq := coalesce((it->>'seq')::bigint, 0);

    update attempt_answers
       set response = v_resp, state = v_state,
           time_spent_sec = least(greatest(time_spent_sec, coalesce((it->>'time_spent_sec')::int, 0)), 20000),
           client_seq = v_seq, updated_at = now()
     where attempt_id = p_attempt_id and question_id = v_qid and client_seq <= v_seq;
    get diagnostics v_n = row_count;
    if v_n = 1 then v_saved := v_saved + 1; else v_stale := v_stale + 1; end if;
  end loop;

  return jsonb_build_object('saved', v_saved, 'stale', v_stale, 'server_now', now(), 'deadline_at', v_att.deadline_at,
                            'seconds_left', greatest(0, extract(epoch from (v_att.deadline_at - now()))::int));
end $$;

-- ---------- scoring (internal) ---------------------------------------------------------
-- Idempotent: recomputes everything from the stored responses + current keys. Re-used by regrade.
create or replace function score_attempt(p_attempt_id uuid) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  update attempt_answers aa
     set is_correct = g.ok,
         marks_awarded = case when aa.response is null then 0
                              when g.ok               then g.marks
                              when g.qtype = 'MCQ'    then -(g.marks / 3.0)     -- GATE: 1 mark -> -1/3, 2 marks -> -2/3
                              else 0 end                                         -- MSQ / NAT: no negative marking
    from (
      select a.question_id, pq.marks, pq.qtype,
             case when a.response is null then null
                  when pq.qtype = 'MCQ' then jsonb_exists(k.answer, a.response #>> '{0}')        -- any listed letter accepted
                  when pq.qtype = 'MSQ' then sort_letters(a.response) = sort_letters(k.answer)   -- all-or-nothing, no partial credit
                  when pq.qtype = 'NAT' then (a.response #>> '{}')::numeric
                                             between (k.answer->>'low')::numeric - 0.000001 and (k.answer->>'high')::numeric + 0.000001
             end as ok
        from attempt_answers a
        join paper_questions pq on pq.question_id = a.question_id
        join attempts at on at.id = a.attempt_id and at.paper_id = pq.paper_id
        left join question_keys k on k.question_id = a.question_id
       where a.attempt_id = p_attempt_id
    ) g
   where aa.attempt_id = p_attempt_id and aa.question_id = g.question_id;

  update attempts at set
      score         = t.score,
      correct_count = t.correct, wrong_count = t.wrong, skipped_count = t.skipped
    from (select round(coalesce(sum(marks_awarded), 0), 2) as score,
                 count(*) filter (where is_correct)            as correct,
                 count(*) filter (where is_correct is false)   as wrong,
                 count(*) filter (where response is null)      as skipped
            from attempt_answers where attempt_id = p_attempt_id) t
   where at.id = p_attempt_id;

  delete from attempt_subject_stats where attempt_id = p_attempt_id;
  insert into attempt_subject_stats (attempt_id, subject_id, questions, attempted, correct, wrong, marks_scored, marks_total, time_spent_sec)
  select aa.attempt_id, pq.subject_id, count(*), count(*) filter (where aa.response is not null),
         count(*) filter (where aa.is_correct), count(*) filter (where aa.is_correct is false),
         round(coalesce(sum(aa.marks_awarded), 0), 2), sum(pq.marks), coalesce(sum(aa.time_spent_sec), 0)
    from attempt_answers aa
    join attempts at on at.id = aa.attempt_id
    join paper_questions pq on pq.paper_id = at.paper_id and pq.question_id = aa.question_id
   where aa.attempt_id = p_attempt_id
   group by aa.attempt_id, pq.subject_id;
end $$;

create or replace function finalize_attempt(p_attempt_id uuid, p_status text) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_att attempts%rowtype; v_end timestamptz;
begin
  select * into v_att from attempts where id = p_attempt_id for update;
  if not found or v_att.status <> 'in_progress' then return; end if;           -- already done: do nothing (idempotent)
  v_end := least(now(), v_att.deadline_at);
  update attempts set status = p_status, submitted_at = v_end,
         time_spent_sec = extract(epoch from (v_end - started_at))::int where id = p_attempt_id;
  perform score_attempt(p_attempt_id);
end $$;

-- Standing of one attempt among the ranked (first-attempt) results of its paper.
create or replace function attempt_standing(p_attempt_id uuid) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare a attempts%rowtype; v_n int; v_higher int; v_lower int; v_avg numeric; v_top numeric;
begin
  select * into a from attempts where id = p_attempt_id;
  select count(*), count(*) filter (where score > a.score), count(*) filter (where score < a.score), round(avg(score), 2), max(score)
    into v_n, v_higher, v_lower, v_avg, v_top
    from attempts where paper_id = a.paper_id and counts_for_rank and status <> 'in_progress';
  return jsonb_build_object('rank', v_higher + 1, 'participants', v_n,
                            'percentile', case when v_n > 0 then round(100.0 * v_lower / v_n, 1) end,
                            'paper_avg_score', v_avg, 'paper_top_score', v_top, 'counted_on_leaderboard', a.counts_for_rank);
end $$;

create or replace function submit_attempt(p_attempt_id uuid) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_uid uuid := auth.uid(); v_att attempts%rowtype;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select * into v_att from attempts where id = p_attempt_id and user_id = v_uid;
  if not found then raise exception 'attempt_not_found'; end if;
  perform finalize_attempt(p_attempt_id, case when now() > v_att.deadline_at then 'auto_submitted' else 'submitted' end);
  select * into v_att from attempts where id = p_attempt_id;                      -- safe to call twice: returns the same result
  return jsonb_build_object('attempt_id', v_att.id, 'status', v_att.status, 'score', v_att.score, 'max_score', v_att.max_score,
      'correct', v_att.correct_count, 'wrong', v_att.wrong_count, 'skipped', v_att.skipped_count,
      'time_spent_sec', v_att.time_spent_sec) || jsonb_build_object('standing', attempt_standing(p_attempt_id));
end $$;

-- Safety net for students who closed the tab: auto-submit everything past its deadline. pg_cron runs this every minute.
create or replace function expire_overdue_attempts(p_limit int default 500) returns int
language plpgsql security definer set search_path = public, pg_temp as $$
declare r record; n int := 0;
begin
  for r in select id from attempts where status = 'in_progress' and deadline_at + exam_grace() < now()
            order by deadline_at limit p_limit for update skip locked loop     -- safe with several workers
    perform finalize_attempt(r.id, 'auto_submitted'); n := n + 1;
  end loop;
  return n;
end $$;

-- ====================================================================================
-- RESULTS, ANALYSIS, HISTORY
-- ====================================================================================
create or replace function get_attempt_analysis(p_attempt_id uuid) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_att attempts%rowtype; v_uid uuid := auth.uid(); v_subjects jsonb; v_types jsonb; v_sections jsonb; v_neg numeric;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select * into v_att from attempts where id = p_attempt_id and user_id = v_uid;
  if not found then raise exception 'attempt_not_found'; end if;
  if v_att.status = 'in_progress' then
    if now() > v_att.deadline_at + exam_grace() then
      perform finalize_attempt(v_att.id, 'auto_submitted'); select * into v_att from attempts where id = p_attempt_id;
    else raise exception 'attempt_in_progress'; end if;
  end if;

  select jsonb_agg(jsonb_build_object(
           'subject', s.name, 'code', s.code, 'section', s.section,
           'questions', st.questions, 'attempted', st.attempted, 'correct', st.correct, 'wrong', st.wrong,
           'accuracy_pct', case when st.attempted > 0 then round(100.0 * st.correct / st.attempted, 1) end,
           'marks_scored', st.marks_scored, 'marks_total', st.marks_total, 'time_spent_sec', st.time_spent_sec,
           'cohort_avg_marks', (select round(avg(x.marks_scored), 2)
                                  from attempt_subject_stats x join attempts a2 on a2.id = x.attempt_id
                                 where a2.paper_id = v_att.paper_id and a2.counts_for_rank and a2.status <> 'in_progress'
                                   and x.subject_id = st.subject_id)) order by s.sort_order)
    into v_subjects
    from attempt_subject_stats st join subjects s on s.id = st.subject_id where st.attempt_id = p_attempt_id;

  select jsonb_object_agg(qtype, jsonb_build_object('questions', n, 'attempted', att, 'correct', cor, 'marks', m)) into v_types
    from (select pq.qtype, count(*) n, count(*) filter (where aa.response is not null) att, count(*) filter (where aa.is_correct) cor,
                 round(sum(aa.marks_awarded), 2) m
            from attempt_answers aa join paper_questions pq on pq.question_id = aa.question_id and pq.paper_id = v_att.paper_id
           where aa.attempt_id = p_attempt_id group by pq.qtype) t;

  select jsonb_object_agg(section, jsonb_build_object('marks_scored', sc, 'marks_total', tot, 'correct', cor, 'wrong', wr)) into v_sections
    from (select s.section, sum(st.marks_scored) sc, sum(st.marks_total) tot, sum(st.correct) cor, sum(st.wrong) wr
            from attempt_subject_stats st join subjects s on s.id = st.subject_id
           where st.attempt_id = p_attempt_id group by s.section) t;

  select round(coalesce(-sum(marks_awarded) filter (where marks_awarded < 0), 0), 2) into v_neg
    from attempt_answers where attempt_id = p_attempt_id;

  return jsonb_build_object(
    'attempt_id', v_att.id, 'paper_id', v_att.paper_id, 'status', v_att.status, 'score', v_att.score, 'max_score', v_att.max_score,
    'correct', v_att.correct_count, 'wrong', v_att.wrong_count, 'skipped', v_att.skipped_count,
    'time_spent_sec', v_att.time_spent_sec, 'negative_marks_lost', v_neg,
    'standing', attempt_standing(p_attempt_id), 'subjects', coalesce(v_subjects, '[]'::jsonb),
    'by_type', coalesce(v_types, '{}'::jsonb), 'by_section', coalesce(v_sections, '{}'::jsonb));
end $$;

create or replace function list_my_attempts(p_limit int default 20, p_offset int default 0) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_uid uuid := auth.uid();
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  return coalesce((select jsonb_agg(row_to_json(t)::jsonb) from (
    select a.id as attempt_id, a.paper_id, p.title, a.attempt_no, a.status, a.started_at, a.submitted_at, a.score, a.max_score,
           a.counts_for_rank, case when a.status = 'in_progress' then greatest(0, extract(epoch from (a.deadline_at - now()))::int) end as seconds_left
      from attempts a join papers p on p.id = a.paper_id
     where a.user_id = v_uid order by a.started_at desc limit least(p_limit, 100) offset greatest(p_offset, 0)) t), '[]'::jsonb);
end $$;

-- subject-wise strengths/weaknesses over ALL the user's finished attempts + score trend
create or replace function get_my_progress() returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_uid uuid := auth.uid(); v_subj jsonb; v_trend jsonb;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select jsonb_agg(jsonb_build_object('subject', s.name, 'code', s.code, 'questions', q, 'attempted', att, 'correct', cor, 'wrong', wr,
           'accuracy_pct', case when att > 0 then round(100.0 * cor / att, 1) end,
           'marks_pct', case when tot > 0 then round(100.0 * sc / tot, 1) end, 'avg_time_per_question_sec', case when q > 0 then round(tm::numeric / q, 0) end)
           order by s.sort_order)
    into v_subj
    from (select st.subject_id, sum(st.questions) q, sum(st.attempted) att, sum(st.correct) cor, sum(st.wrong) wr,
                 sum(st.marks_scored) sc, sum(st.marks_total) tot, sum(st.time_spent_sec) tm
            from attempt_subject_stats st join attempts a on a.id = st.attempt_id
           where a.user_id = v_uid and a.status <> 'in_progress' group by st.subject_id) x
    join subjects s on s.id = x.subject_id;
  select jsonb_agg(jsonb_build_object('attempt_id', id, 'submitted_at', submitted_at, 'score', score, 'max_score', max_score) order by submitted_at)
    into v_trend from (select * from attempts where user_id = v_uid and status <> 'in_progress' order by submitted_at desc limit 20) t;
  return jsonb_build_object('subjects', coalesce(v_subj, '[]'::jsonb), 'trend', coalesce(v_trend, '[]'::jsonb));
end $$;

create or replace function list_papers(p_scope text default 'public', p_limit int default 20, p_offset int default 0) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_uid uuid := auth.uid();
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  return coalesce((select jsonb_agg(row_to_json(t)::jsonb) from (
    select p.id, p.title, p.visibility, p.duration_min, p.total_marks, p.total_questions, p.created_at,
           (select count(*) from attempts a where a.paper_id = p.id and a.counts_for_rank and a.status <> 'in_progress') as participants,
           (select a.id from attempts a where a.paper_id = p.id and a.user_id = v_uid order by a.attempt_no desc limit 1) as my_latest_attempt
      from papers p
     where (p_scope = 'public' and p.visibility = 'public') or (p_scope = 'mine' and p.created_by = v_uid)
     order by p.created_at desc limit least(p_limit, 100) offset greatest(p_offset, 0)) t), '[]'::jsonb);
end $$;

-- ====================================================================================
-- LEADERBOARDS
-- ====================================================================================
create or replace function get_leaderboard(p_paper_id uuid, p_limit int default 50, p_offset int default 0) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_uid uuid := auth.uid(); v_n int; v_rows jsonb;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  if not exists (select 1 from papers where id = p_paper_id and (visibility = 'public' or created_by = v_uid)) then
    raise exception 'paper_not_found';
  end if;
  select count(*) into v_n from attempts where paper_id = p_paper_id and counts_for_rank and status <> 'in_progress';
  select coalesce(jsonb_agg(row_to_json(t)::jsonb), '[]'::jsonb) into v_rows from (
    select rank() over (order by a.score desc) as rank, pr.display_name, a.score, a.time_spent_sec, (a.user_id = v_uid) as is_me
      from attempts a join profiles pr on pr.id = a.user_id
     where a.paper_id = p_paper_id and a.counts_for_rank and a.status <> 'in_progress'
     order by a.score desc, a.time_spent_sec asc
     limit least(p_limit, 100) offset greatest(p_offset, 0)) t;
  return jsonb_build_object('participants', v_n, 'entries', v_rows);
end $$;

create or replace function get_global_leaderboard(p_limit int default 50) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_uid uuid := auth.uid();
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  return coalesce((select jsonb_agg(row_to_json(t)::jsonb) from (
    select rank() over (order by avg_pct desc) as rank, display_name, papers_taken, avg_pct, best_pct, (user_id = v_uid) as is_me
      from global_leaderboard order by avg_pct desc, papers_taken desc limit least(p_limit, 100)) t), '[]'::jsonb);
end $$;

create or replace function refresh_global_leaderboard() returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin refresh materialized view concurrently global_leaderboard; exception when others then refresh materialized view global_leaderboard; end $$;

-- ====================================================================================
-- FEEDBACK LOOP
-- ====================================================================================
create or replace function report_question(p_question_id uuid, p_reason text, p_note text default null) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_uid uuid := auth.uid(); v_open int;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  if not exists (select 1 from user_question_seen where user_id = v_uid and question_id = p_question_id) then
    raise exception 'question_not_seen';                                          -- only people who met the question can report it
  end if;
  insert into question_reports (question_id, user_id, reason, note) values (p_question_id, v_uid, p_reason, left(p_note, 1000))
  on conflict (question_id, user_id) do update set reason = excluded.reason, note = excluded.note, status = 'open';
  select count(*) into v_open from question_reports where question_id = p_question_id and status = 'open';
  update questions set report_count = v_open, flagged = (v_open >= 3) where id = p_question_id;   -- 3 reports -> hidden from new papers
end $$;

-- ====================================================================================
-- ADMIN (service role only)
-- ====================================================================================
-- p_rows: [{topic, source, pyq_year, pyq_ref, type, marks, body_md, options, answer, solution_md, solution_source,
--           verify_agree, status, content_hash}]    (<= 500 rows per call)
create or replace function admin_upsert_questions(p_rows jsonb, p_batch jsonb default null) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  it jsonb; v_topic topics%rowtype; v_batch uuid; v_id uuid; v_idx int := 0; v_ins int := 0; v_dup int := 0; v_enriched int := 0;
  v_bad jsonb := '[]'::jsonb; v_status text; v_type text; v_hash text; v_opts jsonb; v_ans jsonb;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) > 500 then raise exception 'bad_request'; end if;
  if p_batch is not null then
    insert into generation_batches (model_version, params, note)
    values (p_batch->>'model_version', p_batch->'params', p_batch->>'note') returning id into v_batch;
  end if;

  for it in select * from jsonb_array_elements(p_rows) loop
    v_idx := v_idx + 1;
    v_type := it->>'type'; v_hash := it->>'content_hash'; v_opts := it->'options'; v_ans := it->'answer';
    v_status := coalesce(it->>'status', 'pending');
    select * into v_topic from topics where name = it->>'topic';
    if not found then v_bad := v_bad || jsonb_build_object('i', v_idx, 'why', 'unknown_topic:' || coalesce(it->>'topic','null')); continue; end if;
    if v_type not in ('MCQ','MSQ','NAT') or coalesce(it->>'marks','') not in ('1','2') or coalesce(length(it->>'body_md'), 0) < 10
       or v_hash is null or v_status not in ('pending','approved','rejected') then
      v_bad := v_bad || jsonb_build_object('i', v_idx, 'why', 'bad_row'); continue;
    end if;
    if v_type = 'NAT' then v_opts := null; elsif v_opts is null or jsonb_typeof(v_opts) <> 'object' or (select count(*) from jsonb_object_keys(v_opts)) <> 4 then
      v_bad := v_bad || jsonb_build_object('i', v_idx, 'why', 'bad_options'); continue;
    end if;
    if v_type <> 'NAT' and v_ans is not null and jsonb_typeof(v_ans) = 'array' then v_ans := sort_letters(v_ans); end if;
    if not valid_key(v_type, v_ans) then
      v_bad := v_bad || jsonb_build_object('i', v_idx, 'why', 'bad_answer'); continue;
    end if;

    insert into questions (subject_id, topic_id, source, pyq_year, pyq_ref, type, marks, body_md, options, status, verify_agree,
                           batch_id, content_hash, approved_at)
    values (v_topic.subject_id, v_topic.id, coalesce(it->>'source', 'generated'), (it->>'pyq_year')::smallint, it->>'pyq_ref', v_type,
            (it->>'marks')::smallint, it->>'body_md', v_opts, v_status, (it->>'verify_agree')::numeric, v_batch, v_hash,
            case when v_status = 'approved' then now() end)
    on conflict (content_hash) do nothing returning id into v_id;

    if v_id is not null then
      insert into question_keys (question_id, answer, solution_md, solution_source)
      values (v_id, v_ans, nullif(it->>'solution_md', ''), nullif(it->>'solution_source', ''));
      v_ins := v_ins + 1;
    else                                                         -- already there: fill a missing solution, promote pending -> approved
      v_dup := v_dup + 1;
      select id into v_id from questions where content_hash = v_hash;
      update question_keys set solution_md = nullif(it->>'solution_md', ''), solution_source = nullif(it->>'solution_source', ''), updated_at = now()
       where question_id = v_id and solution_md is null and nullif(it->>'solution_md', '') is not null;
      if found then v_enriched := v_enriched + 1; end if;
      if v_status = 'approved' then update questions set status = 'approved', approved_at = now() where id = v_id and status = 'pending'; end if;
    end if;
    v_id := null;
  end loop;
  return jsonb_build_object('inserted', v_ins, 'duplicates', v_dup, 'solutions_filled', v_enriched, 'invalid', v_bad, 'batch_id', v_batch);
end $$;

-- how many approved questions exist per subject / marks (what to generate next)
create or replace function admin_bank_stats(p_target int default 200) returns jsonb
language sql security definer set search_path = public, pg_temp as $$
  select coalesce(jsonb_agg(row_to_json(t)::jsonb order by t.sort_order), '[]'::jsonb) from (
    select s.code, s.name, s.sort_order,
           count(q.id) filter (where q.status = 'approved' and not q.flagged)                      as approved,
           count(q.id) filter (where q.status = 'approved' and not q.flagged and q.marks = 1)      as one_mark,
           count(q.id) filter (where q.status = 'approved' and not q.flagged and q.marks = 2)      as two_mark,
           count(q.id) filter (where q.status = 'pending')                                         as pending,
           count(q.id) filter (where q.flagged)                                                    as flagged,
           count(k.question_id) filter (where q.status = 'approved' and k.solution_md is not null) as with_solution,
           greatest(0, p_target - count(q.id) filter (where q.status = 'approved' and not q.flagged)) as still_needed
      from subjects s left join questions q on q.subject_id = s.id left join question_keys k on k.question_id = q.id
     group by s.id) t
$$;

create or replace function admin_set_question_status(p_ids uuid[], p_status text) returns int
language plpgsql security definer set search_path = public, pg_temp as $$
declare n int;
begin
  if p_status not in ('pending','approved','rejected','retired') then raise exception 'bad_request'; end if;
  update questions set status = p_status, approved_at = case when p_status = 'approved' then coalesce(approved_at, now()) else approved_at end,
         flagged = case when p_status = 'approved' then false else flagged end
   where id = any (p_ids);
  get diagnostics n = row_count; return n;
end $$;

-- Wrong key discovered? Fix it and re-score every finished attempt that contained the question.
create or replace function admin_regrade_question(p_question_id uuid, p_answer jsonb, p_solution_md text default null) returns int
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_type text; r record; n int := 0;
begin
  select type into v_type from questions where id = p_question_id;
  if not found then raise exception 'question_not_found'; end if;
  if v_type <> 'NAT' and jsonb_typeof(p_answer) = 'array' then p_answer := sort_letters(p_answer); end if;
  if not valid_key(v_type, p_answer) then raise exception 'bad_answer'; end if;
  update question_keys set answer = p_answer, solution_md = coalesce(p_solution_md, solution_md),
         solution_source = case when p_solution_md is null then solution_source else 'human' end,
         key_version = key_version + 1, updated_at = now() where question_id = p_question_id;
  update question_reports set status = 'resolved' where question_id = p_question_id and status = 'open';
  update questions set flagged = false, report_count = 0 where id = p_question_id;
  for r in select distinct aa.attempt_id from attempt_answers aa join attempts a on a.id = aa.attempt_id
            where aa.question_id = p_question_id and a.status <> 'in_progress' loop
    perform score_attempt(r.attempt_id); n := n + 1;
  end loop;
  return n;                                                                       -- number of attempts re-scored
end $$;
