-- =====================================================================================
-- GATE CSE Mock Platform  |  001_schema.sql  |  Supabase (Postgres 15+)
-- Run order: 001_schema.sql -> 002_functions.sql -> 003_security.sql -> seed/*.sql
-- =====================================================================================

-- ---------- reference data ------------------------------------------------------------
create table if not exists subjects (
  id          smallint generated always as identity primary key,
  code        text not null unique,                       -- GA, PDS, MATH, ALGO, OS, CN, COA, TOC, DBMS, CD, DL
  name        text not null unique,
  section     text not null check (section in ('GA','CS')),
  marks_min   smallint not null check (marks_min >= 0),   -- weightage range (marks in a 100-mark paper)
  marks_max   smallint not null,
  priority    text not null check (priority in ('Fixed','High','Medium','Low')),
  sort_order  smallint not null default 0,
  check (marks_min <= marks_max)
);

-- topics = the labels the model was trained with ("Discrete Mathematics" is a topic of Engineering Mathematics)
create table if not exists topics (
  id          smallint generated always as identity primary key,
  subject_id  smallint not null references subjects(id) on delete cascade,
  name        text not null unique
);

-- A blueprint = an exact paper recipe: "N questions of M marks from subject S". Sampled inside the weightage ranges.
create table if not exists blueprints (
  id               int generated always as identity primary key,
  name             text not null unique,
  duration_min     smallint not null default 180,
  total_questions  smallint not null,
  total_marks      smallint not null,
  active           boolean not null default true,
  created_at       timestamptz not null default now()
);
create table if not exists blueprint_rules (
  blueprint_id  int      not null references blueprints(id) on delete cascade,
  subject_id    smallint not null references subjects(id),
  marks         smallint not null check (marks in (1,2)),
  qcount        smallint not null check (qcount >= 0),
  primary key (blueprint_id, subject_id, marks)
);

-- ---------- users ---------------------------------------------------------------------
create table if not exists profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  display_name  text not null check (char_length(display_name) between 2 and 40),   -- shown on leaderboards (never the email)
  exam          text not null default 'GATE-CSE',
  created_at    timestamptz not null default now()
);

create or replace function handle_new_user() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  insert into profiles (id, display_name)
  values (new.id, coalesce(nullif(left(trim(new.raw_user_meta_data->>'display_name'), 40), ''),
                           'Student-' || substr(replace(new.id::text, '-', ''), 1, 6)))
  on conflict do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function handle_new_user();

-- ---------- question bank -------------------------------------------------------------
create table if not exists generation_batches (
  id             uuid primary key default gen_random_uuid(),
  model_version  text,
  params         jsonb,
  note           text,
  created_at     timestamptz not null default now()
);

create table if not exists questions (
  id            uuid primary key default gen_random_uuid(),
  subject_id    smallint not null references subjects(id),
  topic_id      smallint references topics(id),
  source        text not null check (source in ('pyq','generated')),
  pyq_year      smallint,
  pyq_ref       text,
  type          text not null check (type in ('MCQ','MSQ','NAT')),
  marks         smallint not null check (marks in (1,2)),
  body_md       text not null,                      -- Markdown + $LaTeX$
  options       jsonb,                              -- {"A":"..","B":"..","C":"..","D":".."}; NULL for NAT
  status        text not null default 'pending' check (status in ('pending','approved','rejected','retired')),
  flagged       boolean not null default false,     -- set automatically when users report it; excluded from new papers
  report_count  int not null default 0,
  verify_agree  numeric(3,2),                       -- solver agreement 0..1 (generated only)
  batch_id      uuid references generation_batches(id),
  content_hash  text not null unique,               -- same md5 as the local SQLite bank -> safe re-pushes
  created_at    timestamptz not null default now(),
  approved_at   timestamptz,
  check ((type = 'NAT' and options is null) or (type <> 'NAT' and options is not null))
);
create index if not exists questions_pick_idx on questions (subject_id, marks) where status = 'approved' and not flagged;

-- The answer key lives in its own table so it can never leak through a "select * from questions".
create table if not exists question_keys (
  question_id      uuid primary key references questions(id) on delete cascade,
  answer           jsonb not null,    -- MCQ ["C"] (several letters = any accepted) | MSQ ["A","C"] (all must match) | NAT {"low":4.4,"high":4.6}
  solution_md      text,
  solution_source  text check (solution_source in ('official','model','human')),
  key_version      int not null default 1,
  updated_at       timestamptz not null default now()
);

-- a question's content can't change once it is out of 'pending' (old papers must stay reproducible)
create or replace function questions_immutable() returns trigger language plpgsql as $$
begin
  if old.status <> 'pending' and (new.body_md is distinct from old.body_md or new.options is distinct from old.options
       or new.type is distinct from old.type or new.marks is distinct from old.marks
       or new.subject_id is distinct from old.subject_id) then
    raise exception 'question_immutable' using detail = 'Retire the question and insert a corrected one instead.';
  end if;
  return new;
end $$;
drop trigger if exists trg_questions_immutable on questions;
create trigger trg_questions_immutable before update on questions for each row execute function questions_immutable();

-- ---------- papers (a frozen question set) --------------------------------------------
create table if not exists papers (
  id               uuid primary key default gen_random_uuid(),
  title            text not null,
  blueprint_id     int references blueprints(id),
  created_by       uuid not null references profiles(id),
  visibility       text not null default 'private' check (visibility in ('public','private')),
  duration_min     smallint not null,
  total_marks      smallint not null,
  total_questions  smallint not null,
  idempotency_key  text,
  created_at       timestamptz not null default now(),
  unique (created_by, idempotency_key)
);
create index if not exists papers_public_idx on papers (created_at desc) where visibility = 'public';
create index if not exists papers_mine_idx   on papers (created_by, created_at desc);

create table if not exists paper_questions (
  paper_id     uuid     not null references papers(id) on delete cascade,
  position     smallint not null,
  question_id  uuid     not null references questions(id),
  subject_id   smallint not null references subjects(id),
  qtype        text     not null,
  marks        smallint not null,
  primary key (paper_id, position),
  unique (paper_id, question_id)
);
create index if not exists paper_questions_q_idx on paper_questions (question_id);

-- ---------- attempts ------------------------------------------------------------------
create table if not exists attempts (
  id               uuid primary key default gen_random_uuid(),
  paper_id         uuid not null references papers(id),
  user_id          uuid not null references profiles(id) on delete cascade,
  attempt_no       int  not null,
  status           text not null default 'in_progress' check (status in ('in_progress','submitted','auto_submitted')),
  started_at       timestamptz not null default now(),
  deadline_at      timestamptz not null,                 -- the SERVER decides when time is up
  submitted_at     timestamptz,
  score            numeric(6,2),
  max_score        smallint not null,
  correct_count    smallint,
  wrong_count      smallint,
  skipped_count    smallint,
  time_spent_sec   int,
  counts_for_rank  boolean not null default false,       -- only a user's 1st attempt of a paper is ranked
  unique (paper_id, user_id, attempt_no)
);
create unique index if not exists attempts_one_active_idx on attempts (paper_id, user_id) where status = 'in_progress';
create index if not exists attempts_user_idx   on attempts (user_id, started_at desc);
create index if not exists attempts_expiry_idx on attempts (deadline_at) where status = 'in_progress';
create index if not exists attempts_rank_idx   on attempts (paper_id, score desc, time_spent_sec)
  where counts_for_rank and status <> 'in_progress';

create table if not exists attempt_answers (
  attempt_id      uuid not null references attempts(id) on delete cascade,
  question_id     uuid not null references questions(id),
  response        jsonb,                                  -- MCQ/MSQ ["A"] / ["A","C"]  |  NAT "4.5"  |  NULL = not answered
  state           text not null default 'not_visited'
                    check (state in ('not_visited','not_answered','answered','marked','answered_marked')),
  time_spent_sec  int  not null default 0,
  client_seq      bigint not null default 0,              -- orders autosaves that arrive out of order
  updated_at      timestamptz not null default now(),
  is_correct      boolean,                                -- filled at submit
  marks_awarded   numeric,                                -- filled at submit
  primary key (attempt_id, question_id)
);

create table if not exists attempt_subject_stats (        -- precomputed at submit -> the analysis page is one cheap read
  attempt_id      uuid not null references attempts(id) on delete cascade,
  subject_id      smallint not null references subjects(id),
  questions       smallint not null,
  attempted       smallint not null,
  correct         smallint not null,
  wrong           smallint not null,
  marks_scored    numeric(6,2) not null,
  marks_total     smallint not null,
  time_spent_sec  int not null,
  primary key (attempt_id, subject_id)
);

-- which questions a user has already met -> new papers prefer questions they have not seen
create table if not exists user_question_seen (
  user_id      uuid not null references profiles(id) on delete cascade,
  question_id  uuid not null references questions(id) on delete cascade,
  first_seen   timestamptz not null default now(),
  primary key (user_id, question_id)
);

-- students flag suspicious answer keys: the feedback loop that keeps model-generated content honest
create table if not exists question_reports (
  id           bigint generated always as identity primary key,
  question_id  uuid not null references questions(id) on delete cascade,
  user_id      uuid not null references profiles(id) on delete cascade,
  reason       text not null check (reason in ('wrong_answer','wrong_solution','unclear','typo','other')),
  note         text check (char_length(note) <= 1000),
  status       text not null default 'open' check (status in ('open','resolved','dismissed')),
  created_at   timestamptz not null default now(),
  unique (question_id, user_id)
);

-- ---------- global leaderboard (refreshed on a schedule, not per request) --------------
create materialized view if not exists global_leaderboard as
  select a.user_id,
         p.display_name,
         count(*)                                                        as papers_taken,
         round(avg(100.0 * a.score / nullif(a.max_score, 0)), 2)         as avg_pct,
         round(max(100.0 * a.score / nullif(a.max_score, 0)), 2)         as best_pct
    from attempts a join profiles p on p.id = a.user_id
   where a.counts_for_rank and a.status <> 'in_progress'
   group by a.user_id, p.display_name
  having count(*) >= 3;
create unique index if not exists global_leaderboard_user_idx on global_leaderboard (user_id);
create index if not exists global_leaderboard_rank_idx on global_leaderboard (avg_pct desc);
