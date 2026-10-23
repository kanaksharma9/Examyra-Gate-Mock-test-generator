# GATE CSE Mock-Test Platform: Backend Design

Diagrams are in `diagrams/` (`01_content_pipeline`, `02_runtime`, `03_attempt_states`, `04_er`, each as .mmd + .png).

## 1. Where everything runs

| Piece | Where | Notes |
|---|---|---|
| Fine-tuned Qwen model | Google Colab (free T4), **offline batch only** | Not hosted anywhere. Students never touch it. |
| Question bank (working copy) | SQLite file `model/database/question_bank.db` | Review and edit here before publishing. |
| Production database + auth | **Supabase** (Postgres + Auth) | All exam rules live in Postgres functions. |
| API / website | Next.js (its own backend routes) | Thin layer: authenticate, call one RPC, map errors. |
| LangChain | Not needed for the core flow | Add later only for extras (e.g. "explain this differently"). |

"Run Colab, store in Supabase, re-run when new questions are needed" is the right flow: `generate_questions.py --fill-to 200` tops up only what is missing per subject.

## 2. Content pipeline (once, then whenever you need more questions)

PYQ PDFs -> `build_dataset.py` -> `tag_subjects.py` -> `train_qwen.py` -> `generate_questions.py` -> SQLite bank -> `review_bank.py` -> `generate_solutions.py` -> `push_to_supabase.py` -> Supabase.

Students can report a wrong key from the app; that sends you back to review/regrade.

## 3. Schema (what each table is for)

- `subjects` (11 exam subjects, with min/max marks and priority) and `topics` (the 14 labels the model is trained on; Engineering Mathematics has 4 topics).
- `blueprints`, `blueprint_rules`: exact per-subject count of 1-mark and 2-mark questions for a paper (the migration bootstrap creates `Standard-01`; the generated seed adds the remaining "Standard-NN" blueprints).
- `questions`: content only (body, options, marks, type, source, status). No answers here.
- `question_keys`: the answer key, in a separate closed table. `questions.status` is pending / approved / rejected; flagged questions are excluded from new papers.
- `generation_batches`: which model version produced which rows.
- `papers` + `paper_questions`: a generated paper is a frozen set of questions in a fixed order (GA, then 1-mark core, then 2-mark core).
- `attempts`, `attempt_answers`, `attempt_subject_stats`: one user's run of a paper, per-question responses, per-subject analysis.
- `user_question_seen`, `question_reports`: feedback loop.
- `profiles`: auto-created on sign-up with display name `Student-xxxxxx` (never the email).
- `global_leaderboard`: materialized view.

Answer formats: MCQ `["C"]`; MSQ `["A","C"]` (all must match, no partial credit); NAT `{"low":4.4,"high":4.6}`. Responses: MCQ/MSQ a letter array, NAT a string.

## 4. Rules

- **Blueprint**: per-subject marks are sampled inside the weightage ranges so core totals exactly 25x1 + 30x2 and the paper is 65 Q / 100 marks (GA 5x1 + 5x2).
- **Negative marking**: MCQ wrong = -marks/3 (1 mark: -1/3, 2 marks: -2/3). None for MSQ/NAT. Stored unrounded, score rounded to 2 dp.
- **Timer**: server-authoritative. `deadline_at = start + duration`, 20 s grace. Late reads/writes lazily auto-submit; daily pg_cron `expire_overdue_attempts` is a backup.
- **Ranking**: only a user's first attempt of a paper counts. Ties share a rank (1,2,2,4); display order breaks ties by time. Global leaderboard needs at least 3 papers, refreshed every 10 min.
- **Visibility**: public papers can be taken by anyone (shared leaderboard); private ones only by the creator.
- **Immutability**: an approved question's content cannot be edited. Fix a wrong key with `admin_regrade_question`, which re-scores all finished attempts.
- **Reports**: only users who saw the question can report it; at 3 distinct open reports it is flagged and leaves new papers.

## 5. Endpoints

All student RPCs run with the user's JWT (`auth.uid()`).

| Next.js route | Postgres RPC | Purpose |
|---|---|---|
| `POST /api/papers/generate` | `create_paper` | Build a paper (idempotency key supported; 30 per 24 h) |
| `GET /api/papers` | `list_papers` | Browse papers |
| `POST /api/papers/:id/attempts` | `start_attempt` | Start, or resume the active attempt |
| `GET /api/attempts/:id` | `get_attempt` | Exam payload (no answers) or review after submit |
| `PUT /api/attempts/:id/answers` | `save_answers` | Batched autosave, ordered by `client_seq` |
| `POST /api/attempts/:id/submit` | `submit_attempt` | Idempotent submit |
| `GET /api/attempts/:id/analysis` | `get_attempt_analysis` | Score, rank, subject-wise analysis, solutions |
| `GET /api/me/attempts` | `list_my_attempts` | History |
| `GET /api/me/progress` | `get_my_progress` | Trend |
| `GET /api/papers/:id/leaderboard` | `get_leaderboard` | Per-paper ranking |
| `GET /api/leaderboard/global` | `get_global_leaderboard` | Global ranking |
| `POST /api/questions/:id/report` | `report_question` | Report a wrong question/key |
| admin routes (server only, service_role) | `admin_upsert_questions`, `admin_bank_stats`, `admin_set_question_status`, `admin_regrade_question` | Content management |
| `GET /api/cron/expire` (optional) | `expire_overdue_attempts` | Fallback if pg_cron is unavailable |

### Error code to HTTP status

| Database error | HTTP |
|---|---|
| `not_authenticated` | 401 |
| `attempt_not_found`, `paper_not_found`, `blueprint_not_found`, `question_not_found` | 404 |
| `attempt_closed`, `attempt_in_progress`, `insufficient_questions` (detail = JSON of missing subject/marks) | 409 |
| `rate_limited` | 429 |
| `bad_request`, `invalid_response`, `question_not_in_paper`, `bad_answer` | 400 |
| `question_not_seen`, permission denied | 403 |
| anything else | 500 |

## 6. Failure handling

| Situation | Behaviour |
|---|---|
| Retry of create paper | Same idempotency key returns the same paper |
| Retry or double-click on start | Resumes the one active attempt (advisory lock + unique partial index) |
| Retry of submit | Returns the same result |
| Network drops during exam | Client buffers answers with a sequence number and resends; stale writes are ignored |
| Two tabs open | Higher `client_seq` wins; one active attempt per user and paper |
| Timer runs out / browser closed | Auto-submitted by cron or on next request |
| Not enough questions for a blueprint | `insufficient_questions` with exact deficits |
| Wrong answer key | Report -> flag at 3 -> you fix -> `admin_regrade_question` re-scores everyone |
| Abuse | RLS closed by default, rate limit on paper creation |

## 7. Security

- RLS on every table; no policies or grants on `questions`, `question_keys`, `paper_questions`, so a student cannot read keys or any bank content directly.
- The in-progress exam payload has no answers or solutions; they appear only after submit.
- `SECURITY DEFINER` functions with fixed `search_path`; all privileges revoked from anon/authenticated/public, then specific EXECUTE grants.
- The `service_role` key lives only in Colab Secrets and server env vars. Never in the frontend or git.
- After adding any new table, re-run the REVOKE block in `003_security.sql`.

## 8. Runbook

1. Unzip into your project root; make sure `model/common.py` exists (this fixes `No module named 'common'`).
2. Apply migrations in order, including `migrations/004_bootstrap_reference_data.sql`. The migration bootstrap makes paper generation available on migration-only deployments; optionally run `seed/001_subjects.sql` and `seed/002_blueprints.sql` afterward for the complete generated blueprint set. Enable the pg_cron extension (Database > Extensions) and re-run its section of 003 if it printed a notice.
3. Colab: `tag_subjects.py`, then `train_qwen.py`.
4. `generate_questions.py --status`, then `--fill-to 200 --verify` (small `--count` first to measure yield; overshoot defaults to 1.5x).
5. `review_bank.py` to approve rows; `generate_solutions.py`.
6. Colab Secrets `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`; `push_to_supabase.py --dry-run`, then push, then `--stats`.
7. Student-generated papers use approved questions from `source = 'generated'` only. Keep PYQs for separate official-paper workflows.

## 9. Tested vs untested

Tested (PGlite = Postgres 17 in WASM, 92 checks; 98 at 20,000 attempts): profile trigger, upsert and validation, RLS and privileges, paper composition, idempotency, attempt lifecycle, autosave ordering, scoring against an independent calculation, negative marking, retake not ranked, ranks, expiry (grace, cron, lazy), reports and flagging, regrade, insufficient-questions error, global leaderboard.

Not tested: a live Supabase project, true concurrent connections, the Next.js routes, the model training and generation (not run yet), pg_cron on Supabase.

## 10. Quality caveats

- The model is not trained yet. Fine-tuning teaches style, not correctness; the 3x solver agreement check reduces but does not remove wrong keys. Review before approving.
- NAT answers for generated questions are exact values (low = high).
- Training data has no explanations; solutions come from the solver pass or `generate_solutions.py`, written by a small model.
- PYQ math text from PDFs is lossy; check a sample. Mind PYQ copyright if you make the site public.
- Yield and time per question are unknown until the first real run.
