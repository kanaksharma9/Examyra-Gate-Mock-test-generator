// Full-flow test of the Supabase schema on a real Postgres 17 (PGlite).  Run:  npm install && npm test
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const sql = (f) => readFileSync(path.join(here, '..', 'supabase', f), 'utf8');
const db = new PGlite();

let pass = 0, fail = 0;
const ok = (cond, msg) => { if (cond) { pass++; } else { fail++; console.log('  FAIL:', msg); } };
const section = (t) => console.log('\n# ' + t);
const near = (a, b, eps = 0.006) => Math.abs(Number(a) - Number(b)) <= eps;

// ---- Supabase stand-ins: roles, auth schema, auth.uid() --------------------------------------------
await db.exec(`
  create role anon nologin; create role authenticated nologin; create role service_role nologin;
  create schema auth;
  create table auth.users (id uuid primary key default gen_random_uuid(), email text, raw_user_meta_data jsonb default '{}'::jsonb);
  create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant usage on schema public, auth to anon, authenticated, service_role;
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
  alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
`);
for (const f of ['migrations/001_schema.sql', 'migrations/002_functions.sql', 'migrations/003_security.sql',
                 'seed/001_subjects.sql', 'seed/002_blueprints.sql']) await db.exec(sql(f));

// ---- helpers ---------------------------------------------------------------------------------------
const q = async (text, params = []) => (await db.query(text, params)).rows;
async function call(role, uid, fn, args = []) {              // run a function as a given Supabase role / user
  try {
    await db.exec(`set role ${role}`);
    await db.query(`select set_config('request.jwt.claim.sub', $1, false)`, [uid ?? '']);
    const ph = args.map((_, i) => `$${i + 1}`).join(',');
    const r = await db.query(`select ${fn}(${ph}) as r`, args);
    return { r: r.rows[0].r };
  } catch (e) { return { err: e.message, detail: e.detail }; }
  finally { await db.exec('reset role'); await db.query(`select set_config('request.jwt.claim.sub','',false)`); }
}
const as = (uid, fn, args) => call('authenticated', uid, fn, args);
const svc = (fn, args) => call('service_role', null, fn, args);

const U = { A: '00000000-0000-0000-0000-00000000000a', B: '00000000-0000-0000-0000-00000000000b',
            C: '00000000-0000-0000-0000-00000000000c', D: '00000000-0000-0000-0000-00000000000d' };
for (const [k, id] of Object.entries(U))
  await q(`insert into auth.users (id, email, raw_user_meta_data) values ($1, $2, $3::jsonb)`,
          [id, `${k}@x.com`, JSON.stringify(k === 'A' ? { display_name: 'Alice' } : {})]);

// ---- 1. profile trigger ----------------------------------------------------------------------------
section('profiles');
let prof = await q(`select id, display_name from profiles order by id`);
ok(prof.length === 4, 'profile created for each auth user');
ok(prof[0].display_name === 'Alice', 'display_name taken from metadata');
ok(/^Student-[0-9a-f]{6}$/.test(prof[1].display_name) && !prof[1].display_name.includes('@'), 'default display_name never exposes the email');

// ---- 2. load question bank through the admin function ----------------------------------------------
section('admin_upsert_questions');
const subjects = await q(`select code, id from subjects order by sort_order`);
const topicsBySubject = {};
for (const t of await q(`select t.name, s.code from topics t join subjects s on s.id = t.subject_id`)) (topicsBySubject[t.code] ??= []).push(t.name);
const rows = []; let hid = 0;
for (const s of subjects) for (const marks of [1, 2]) for (let i = 0; i < 14; i++) {
  const type = i < 8 ? 'MCQ' : i < 11 ? 'MSQ' : 'NAT';
  const topics = topicsBySubject[s.code];
  rows.push({
    topic: topics[i % topics.length], source: i % 2 ? 'pyq' : 'generated', pyq_year: i % 2 ? 2020 : null, type, marks,
    body_md: `Q-${s.code}-${marks}-${i}: a long enough question body for ${type} with $x^2$ in it`,
    options: type === 'NAT' ? null : { A: 'alpha', B: 'beta', C: 'gamma', D: 'delta' },
    answer: type === 'MCQ' ? ['B'] : type === 'MSQ' ? ['C', 'A'] : { low: 4.4, high: 4.6 },
    solution_md: i % 3 ? 'Because of reasons.' : null, solution_source: i % 3 ? 'model' : null,
    verify_agree: 0.67, status: 'approved', content_hash: `h${++hid}` });
}
const bad = [
  { ...rows[0], topic: 'No Such Topic', content_hash: 'bad1' },
  { ...rows[0], options: { A: '1', B: '2', C: '3' }, content_hash: 'bad2' },
  { ...rows[0], type: 'NAT', options: null, answer: ['A'], content_hash: 'bad3' },
  { ...rows[0], answer: ['Z'], content_hash: 'bad4' },
  { ...rows[0], marks: 3, content_hash: 'bad5' },
];
let res = await svc('admin_upsert_questions', [JSON.stringify([...rows, ...bad]), JSON.stringify({ model_version: 'test-v1' })].map((x, i) => i === 0 ? x : x));
ok(!res.err, 'upsert call works: ' + res.err);
ok(res.r.inserted === rows.length, `inserted ${rows.length} (got ${res.r.inserted})`);
ok(res.r.invalid.length === 5, `5 invalid rows rejected (got ${res.r.invalid.length}): ${JSON.stringify(res.r.invalid.map(x => x.why))}`);
res = await svc('admin_upsert_questions', [JSON.stringify(rows.slice(0, 20))]);
ok(res.r.inserted === 0 && res.r.duplicates === 20, 're-push is idempotent (duplicates ignored)');
const msqKey = await q(`select answer from question_keys k join questions q on q.id = k.question_id where q.type = 'MSQ' limit 1`);
ok(JSON.stringify(msqKey[0].answer) === '["A","C"]', 'MSQ key stored sorted');
const nSol0 = (await q(`select count(*)::int n from question_keys where solution_md is null`))[0].n;
const withSol = rows.slice(0, 20).map(r => ({ ...r, solution_md: 'Filled later by the solution script', solution_source: 'model' }));
res = await svc('admin_upsert_questions', [JSON.stringify(withSol)]);
ok(res.r.solutions_filled > 0 && (await q(`select count(*)::int n from question_keys where solution_md is null`))[0].n < nSol0, 're-push fills in missing solutions');

// ---- 3. security: what a logged-in student may NOT do ----------------------------------------------
section('security / RLS');
async function asStudent(sqlText) {
  try { await db.exec(`set role authenticated`); await db.query(`select set_config('request.jwt.claim.sub', $1, false)`, [U.A]);
        const r = await db.query(sqlText); return { rows: r.rows }; }
  catch (e) { return { err: e.message }; } finally { await db.exec('reset role'); }
}
ok((await asStudent('select * from questions')).err?.includes('permission denied'), 'students cannot read questions directly');
ok((await asStudent('select * from question_keys')).err?.includes('permission denied'), 'students cannot read answer keys');
ok((await asStudent('select * from paper_questions')).err?.includes('permission denied'), 'students cannot read paper_questions');
ok((await asStudent('select * from subjects')).rows?.length === 11, 'students can read subjects');
ok((await asStudent(`insert into attempts (paper_id,user_id,attempt_no,deadline_at,max_score) values (gen_random_uuid(),'${U.A}',1,now(),100)`)).err?.includes('permission denied'), 'students cannot insert attempts');
await asStudent(`update profiles set display_name = 'Hacker' where id = '${U.B}'`);
ok((await q(`select display_name from profiles where id = $1`, [U.B]))[0].display_name !== 'Hacker', "students cannot edit someone else's profile");
ok((await asStudent(`update profiles set display_name = 'Alice2' where id = '${U.A}'`)).err === undefined && (await q(`select display_name from profiles where id = $1`, [U.A]))[0].display_name === 'Alice2', 'students can edit their own display name');
await q(`update profiles set display_name = 'Alice' where id = $1`, [U.A]);
ok((await asStudent(`update profiles set exam = 'X' where id = '${U.A}'`)).err?.includes('permission denied'), 'other profile columns are read-only for students');
ok((await asStudent(`select count(*)::int n from profiles`)).rows[0].n === 1, 'students only see their own profile row');
ok((await as(U.A, 'admin_upsert_questions', ['[]'])).err?.includes('permission denied'), 'students cannot call admin functions');
ok((await as(U.A, 'finalize_attempt', ['00000000-0000-0000-0000-000000000000', 'submitted'])).err?.includes('permission denied'), 'students cannot call finalize_attempt');
ok((await as(U.A, 'score_attempt', ['00000000-0000-0000-0000-000000000000'])).err?.includes('permission denied'), 'students cannot call score_attempt');
ok((await call('anon', null, 'create_paper', [])).err?.includes('permission denied'), 'anonymous visitors cannot create papers');
ok((await as(null, 'create_paper', [])).err === 'not_authenticated', 'no user id -> not_authenticated');
ok((await q(`update questions set body_md = 'edited' where id = (select id from questions limit 1) returning 1`).catch(e => e.message)).toString().includes('question_immutable'), 'approved question content is immutable');

// ---- 4. paper generation ---------------------------------------------------------------------------
section('create_paper');
const idem = 'aaaaaaaa-aaaa-aaaa-aaaa-000000000001';
let r1 = await as(U.A, 'create_paper', [null, 'any', 'private', idem, null]);
ok(!r1.err, 'create_paper works: ' + r1.err);
const paperA = r1.r;
const r1b = await as(U.A, 'create_paper', [null, 'any', 'private', idem, null]);
ok(r1b.r === paperA, 'same idempotency key -> same paper (retry-safe)');
const pq = await q(`select pq.position, pq.marks, pq.qtype, s.code, s.section from paper_questions pq join subjects s on s.id = pq.subject_id where paper_id = $1 order by position`, [paperA]);
ok(pq.length === 65, '65 questions');
ok(pq.reduce((a, x) => a + x.marks, 0) === 100, 'total marks = 100');
ok(pq.filter(x => x.section === 'GA').length === 10 && pq.filter(x => x.section === 'GA' && x.marks === 1).length === 5, 'GA: 10 questions, 5 x 1-mark');
ok(pq.filter(x => x.section === 'CS' && x.marks === 1).length === 25 && pq.filter(x => x.section === 'CS' && x.marks === 2).length === 30, 'core: 25 x 1-mark + 30 x 2-mark');
ok(pq.slice(0, 10).every(x => x.section === 'GA') && pq.slice(10, 35).every(x => x.marks === 1 && x.section === 'CS') && pq.slice(35).every(x => x.marks === 2), 'GATE order: GA, then 1-mark core, then 2-mark core');
const ranges = Object.fromEntries((await q(`select code, marks_min a, marks_max b from subjects`)).map(s => [s.code, [s.a, s.b]]));
const bySubj = {}; for (const x of pq) bySubj[x.code] = (bySubj[x.code] ?? 0) + x.marks;
ok(Object.entries(bySubj).every(([c, m]) => m >= ranges[c][0] && m <= ranges[c][1]), 'every subject inside its weightage range: ' + JSON.stringify(bySubj));
ok(new Set((await q(`select question_id from paper_questions where paper_id = $1`, [paperA])).map(x => x.question_id)).size === 65, 'no question twice in a paper');
const sourceOnly = await as(U.A, 'create_paper', [null, 'pyq', 'private', null, null]);
ok((await q(`select distinct q.source from paper_questions pq join questions q on q.id = pq.question_id where pq.paper_id = $1`, [sourceOnly.r])).every(x => x.source === 'pyq'), "source filter 'pyq' works");
ok((await as(U.A, 'create_paper', [null, 'bogus'])).err === 'bad_request', 'bad source rejected');
ok((await as(U.B, 'start_attempt', [paperA])).err === 'paper_not_found', "private paper is invisible to other users");

// ---- 5. exam: start, fetch, autosave ---------------------------------------------------------------
section('attempt lifecycle');
const att = (await as(U.A, 'start_attempt', [paperA])).r;
ok((await as(U.A, 'start_attempt', [paperA])).r === att, 'start_attempt twice -> same attempt (resume / double-click safe)');
let view = (await as(U.A, 'get_attempt', [att])).r;
ok(view.questions.length === 65 && view.attempt.seconds_left > 10790, '65 questions, ~3h on the clock');
ok(!JSON.stringify(view).includes('"answer"') && !JSON.stringify(view).includes('solution_md'), 'NO answer key or solution in an in-progress exam');
ok(view.questions.filter(x => x.type === 'MCQ').every(x => near(x.negative_marks, x.marks / 3, 0.0001)) && view.questions.filter(x => x.type !== 'MCQ').every(x => Number(x.negative_marks) === 0), 'negative marks shown per question (MCQ marks/3, none for MSQ/NAT)');
ok((await as(U.B, 'get_attempt', [att])).err === 'attempt_not_found', "other users cannot open someone's attempt");

const keys = Object.fromEntries((await q(`select pq.question_id, pq.marks, pq.qtype, k.answer from paper_questions pq join question_keys k on k.question_id = pq.question_id where pq.paper_id = $1`, [paperA])).map(x => [x.question_id, x]));
const qs = view.questions;
const mcq = qs.filter(x => x.type === 'MCQ'), msq = qs.filter(x => x.type === 'MSQ'), nat = qs.filter(x => x.type === 'NAT');
const wrongLetter = (k) => ['A', 'B', 'C', 'D'].find(l => !k.answer.includes(l));
const plan = [];                                     // [question, response]
mcq.slice(0, 12).forEach(x => plan.push([x, [keys[x.question_id].answer[0]]]));            // correct
mcq.slice(12, 18).forEach(x => plan.push([x, [wrongLetter(keys[x.question_id])]]));        // wrong  -> negative marks
msq.slice(0, 1).forEach(x => plan.push([x, keys[x.question_id].answer]));                  // fully right
msq.slice(1, 2).forEach(x => plan.push([x, [keys[x.question_id].answer[0]]]));             // partial -> 0, no negative
msq.slice(2, 3).forEach(x => plan.push([x, ['D']]));                                       // wrong  -> 0, no negative
nat.slice(0, 1).forEach(x => plan.push([x, '4.5']));                                       // in range
nat.slice(1, 2).forEach(x => plan.push([x, '4.7']));                                       // out of range
nat.slice(2, 3).forEach(x => plan.push([x, '4.4']));                                       // range boundary
const expected = plan.reduce((sum, [x, resp]) => {
  const k = keys[x.question_id];
  if (x.type === 'MCQ') return sum + (k.answer.includes(resp[0]) ? x.marks : -x.marks / 3);
  if (x.type === 'MSQ') return sum + (JSON.stringify([...resp].sort()) === JSON.stringify([...k.answer].sort()) ? x.marks : 0);
  return sum + (Number(resp) >= k.answer.low - 1e-6 && Number(resp) <= k.answer.high + 1e-6 ? x.marks : 0);
}, 0);
const items = plan.map(([x, resp], i) => ({ question_id: x.question_id, response: resp, state: 'answered', time_spent_sec: 30 + i, seq: 1 }));
let sv = await as(U.A, 'save_answers', [att, JSON.stringify(items)]);
ok(sv.r?.saved === plan.length && sv.r.stale === 0, 'batch autosave saved everything: ' + JSON.stringify(sv));
const one = items[0];
await as(U.A, 'save_answers', [att, JSON.stringify([{ ...one, response: ['A'], seq: 5 }])]);
sv = await as(U.A, 'save_answers', [att, JSON.stringify([{ ...one, response: ['C'], seq: 3 }])]);
ok(sv.r.stale === 1 && sv.r.saved === 0, 'out-of-order (older seq) autosave is ignored');
await as(U.A, 'save_answers', [att, JSON.stringify([{ ...one, response: one.response, seq: 6 }])]);          // restore the correct one
ok((await as(U.A, 'save_answers', [att, JSON.stringify([{ question_id: mcq[0].question_id, response: ['A', 'B'], seq: 9 }])])).err === 'invalid_response', 'MCQ with 2 letters rejected');
ok((await as(U.A, 'save_answers', [att, JSON.stringify([{ question_id: nat[0].question_id, response: 'abc', seq: 9 }])])).err === 'invalid_response', 'non-numeric NAT rejected');
ok((await as(U.A, 'save_answers', [att, JSON.stringify([{ question_id: U.A, response: ['A'], seq: 9 }])])).err === 'question_not_in_paper', 'question from another paper rejected');
ok((await as(U.B, 'save_answers', [att, '[]'])).err === 'attempt_not_found', "cannot save into someone else's attempt");
await as(U.A, 'save_answers', [att, JSON.stringify([{ question_id: mcq[20].question_id, response: ['B'], state: 'marked', seq: 1 }])]);   // marked for review + answered -> counts
await as(U.A, 'save_answers', [att, JSON.stringify([{ question_id: mcq[20].question_id, response: null, state: 'answered_marked', seq: 2 }])]);   // cleared -> stays unanswered
const st = (await q(`select state, response from attempt_answers where attempt_id = $1 and question_id = $2`, [att, mcq[20].question_id]))[0];
ok(st.state === 'marked' && st.response === null, `cleared answer keeps the review flag and is unanswered (state=${st.state})`);
const expectedAll = expected;

// ---- 6. submit + scoring ---------------------------------------------------------------------------
section('submit / scoring / negative marking');
const sub = await as(U.A, 'submit_attempt', [att]);
ok(!sub.err, 'submit works: ' + sub.err);
ok(near(sub.r.score, expectedAll), `score ${sub.r.score} == independently computed ${expectedAll.toFixed(4)}`);
ok(sub.r.correct === 12 + 1 + 2 && sub.r.wrong === 6 + 1 + 1 + 1, `correct/wrong counts (${sub.r.correct}/${sub.r.wrong})`);
ok(sub.r.skipped === 65 - plan.length, 'skipped count');
ok(JSON.stringify((await as(U.A, 'submit_attempt', [att])).r) === JSON.stringify(sub.r), 'second submit returns the identical result (idempotent)');
ok((await as(U.A, 'save_answers', [att, '[]'])).err === 'attempt_closed', 'cannot save after submit');
view = (await as(U.A, 'get_attempt', [att])).r;
ok(view.questions.every(x => x.answer !== undefined) && view.questions.filter(x => x.solution_md).length > 0, 'after submit: answers + solutions are revealed');
ok(view.questions.filter(x => x.is_correct === true).length === sub.r.correct && view.questions.filter(x => x.is_correct === false).length === sub.r.wrong, 'per-question right/wrong flags');
const wrongMcq = view.questions.find(x => x.type === 'MCQ' && x.is_correct === false);
ok(near(wrongMcq.marks_awarded, -wrongMcq.marks / 3, 1e-6), `wrong MCQ costs -marks/3 (${wrongMcq.marks} marks -> ${Number(wrongMcq.marks_awarded).toFixed(4)})`);
ok(view.questions.filter(x => x.type !== 'MCQ' && x.is_correct === false).every(x => Number(x.marks_awarded) === 0), 'no negative marking for MSQ / NAT');
const an = (await as(U.A, 'get_attempt_analysis', [att])).r;
ok(near(an.subjects.reduce((a, s) => a + Number(s.marks_scored), 0), an.score, 0.011), 'subject marks add up to the total score');
ok(an.subjects.every(s => Number(s.marks_total) > 0) && Number(an.by_section.GA.marks_total) === 15 && Number(an.by_section.CS.marks_total) === 85, 'section totals 15 + 85');
ok(an.negative_marks_lost > 0 && an.standing.rank === 1 && an.standing.participants === 1, 'negative marks lost + standing present');

// ---- 7. public paper: leaderboard, ranks, retake ---------------------------------------------------
section('public paper, leaderboard, retake');
const pubIdem = 'aaaaaaaa-aaaa-aaaa-aaaa-000000000002';
const paperP = (await as(U.A, 'create_paper', [null, 'any', 'public', pubIdem, 'Weekend Mock 1'])).r;
async function take(user, paper, fraction, wrongCount = 0) {
  const a = (await as(user, 'start_attempt', [paper])).r;
  const v = (await as(user, 'get_attempt', [a])).r;
  const kk = Object.fromEntries((await q(`select question_id, answer from question_keys where question_id = any($1::uuid[])`, [v.questions.map(x => x.question_id)])).map(x => [x.question_id, x.answer]));
  const mcqs = v.questions.filter(x => x.type === 'MCQ');
  const n = Math.round(mcqs.length * fraction);
  const its = mcqs.slice(0, n).map((x, i) => ({ question_id: x.question_id, response: i < wrongCount ? ['D'] : [kk[x.question_id][0]], seq: 1, time_spent_sec: 20 }));
  if (its.length) await as(user, 'save_answers', [a, JSON.stringify(its)]);
  return { a, s: (await as(user, 'submit_attempt', [a])).r };
}
const tA = await take(U.A, paperP, 1.0), tB = await take(U.B, paperP, 0.5), tC = await take(U.C, paperP, 0), tD = await take(U.D, paperP, 0.5);
ok(Number(tA.s.score) > Number(tB.s.score) && Number(tB.s.score) > Number(tC.s.score), `scores ordered A(${tA.s.score}) > B(${tB.s.score}) > C(${tC.s.score})`);
ok(tA.s.standing.rank === 1, 'top scorer is rank 1 at submit time');
const lb = (await as(U.B, 'get_leaderboard', [paperP, 10, 0])).r;
ok(lb.participants === 4 && lb.entries.length === 4, 'leaderboard has 4 participants');
ok(lb.entries[0].display_name === 'Alice' && lb.entries[0].rank === 1 && lb.entries.filter(e => e.is_me).length === 1, 'leaderboard order, display names, is_me flag');
ok(JSON.stringify(lb.entries.map(e => e.rank)) === '[1,2,2,4]', 'final ranks are 1,2,2,4 (two tied on 2nd place, next is 4th): ' + JSON.stringify(lb.entries.map(e => e.rank)));
const tieRanks = lb.entries.filter(e => Number(e.score) === Number(tB.s.score)).map(e => e.rank);
ok(tieRanks.length === 2 && tieRanks[0] === tieRanks[1], 'tied scores share the same rank');
ok(!JSON.stringify(lb).includes('@'), 'leaderboard never exposes emails');
ok((await as(U.B, 'get_leaderboard', [paperA, 10, 0])).err === 'paper_not_found', "no leaderboard peek at someone's private paper");
const retake = await take(U.A, paperP, 0.2);
ok(retake.s.standing.counted_on_leaderboard === false, 'retake is not ranked');
const lb2 = (await as(U.B, 'get_leaderboard', [paperP, 10, 0])).r;
ok(lb2.participants === 4 && Number(lb2.entries[0].score) === Number(tA.s.score), 'retake does not change the leaderboard');
const hist = (await as(U.A, 'list_my_attempts', [20, 0])).r;
ok(hist.length === 3 && hist[0].attempt_no === 2, 'attempt history, newest first');
ok((await as(U.A, 'list_papers', ['public', 10, 0])).r.some(p => p.id === paperP && p.participants === 4), 'list_papers shows public paper with participant count');
const prog = (await as(U.A, 'get_my_progress', [])).r;
ok(prog.subjects.length > 0 && prog.trend.length === 3, 'progress: subject strengths + trend');

// ---- 8. time limits, auto-submit, grace ------------------------------------------------------------
section('timer / expiry');
const paperT = (await as(U.C, 'create_paper', [null, 'any', 'private', null, null])).r;
const attT = (await as(U.C, 'start_attempt', [paperT])).r;
const tq = (await as(U.C, 'get_attempt', [attT])).r.questions.find(x => x.type === 'MCQ');
await q(`update attempts set deadline_at = now() - interval '5 seconds' where id = $1`, [attT]);
ok((await as(U.C, 'save_answers', [attT, JSON.stringify([{ question_id: tq.question_id, response: [keys[tq.question_id]?.answer?.[0] ?? 'B'], seq: 1 }])])).r?.saved === 1, 'autosave 5s after the deadline still accepted (grace)');
await q(`update attempts set deadline_at = now() - interval '2 minutes' where id = $1`, [attT]);
ok((await as(U.C, 'save_answers', [attT, JSON.stringify([{ question_id: tq.question_id, response: ['A'], seq: 2 }])])).err === 'attempt_closed', 'autosave after deadline+grace rejected');
ok((await svc('expire_overdue_attempts', [])).r === 1, 'cron job auto-submits the overdue attempt');
const stT = (await q(`select status, score, submitted_at <= deadline_at as capped from attempts where id = $1`, [attT]))[0];
ok(stT.status === 'auto_submitted' && stT.capped && stT.score !== null, 'status auto_submitted, submit time capped at the deadline, scored');
ok((await svc('expire_overdue_attempts', [])).r === 0, 'cron job is idempotent');
const paperT2 = (await as(U.C, 'create_paper', [null, 'any', 'private', null, null])).r;
const attT2 = (await as(U.C, 'start_attempt', [paperT2])).r;
await q(`update attempts set deadline_at = now() - interval '3 minutes' where id = $1`, [attT2]);
const lazy = (await as(U.C, 'get_attempt', [attT2])).r;
ok(lazy.attempt.status === 'auto_submitted' && lazy.questions[0].answer !== undefined, 'opening an expired attempt auto-submits it (no cron needed)');
const attT3 = (await as(U.C, 'start_attempt', [paperT2])).r;
ok(attT3 !== attT2, 'after expiry, starting again creates a fresh attempt');

// ---- 9. feedback loop: reports hide a question -----------------------------------------------------
section('reports + regrade');
const target = qs.find(x => x.type === 'MCQ');
for (const u of [U.B, U.C, U.D]) await q(`insert into user_question_seen (user_id, question_id) values ($1,$2) on conflict do nothing`, [u, target.question_id]);   // pretend they met it
const unseen = (await q(`select id from questions where id not in (select question_id from user_question_seen where user_id = $1) limit 1`, [U.D]))[0].id;
ok((await as(U.D, 'report_question', [unseen, 'typo', null])).err === 'question_not_seen', 'cannot report a question you never saw');
ok((await as(U.D, 'report_question', [target.question_id, 'wrong_answer', null])).err === undefined, 'report #1');
await as(U.A, 'report_question', [target.question_id, 'wrong_answer', 'key looks wrong']);
await as(U.A, 'report_question', [target.question_id, 'wrong_answer', 'again']);          // same user twice
let flagged = (await q(`select flagged, report_count from questions where id = $1`, [target.question_id]))[0];
ok(flagged.report_count === 2 && flagged.flagged === false, 'same user reporting twice counts once; 2 reports do not flag yet');
await as(U.B, 'report_question', [target.question_id, 'wrong_answer', null]);
flagged = (await q(`select flagged, report_count from questions where id = $1`, [target.question_id]))[0];
ok(flagged.flagged === true && flagged.report_count === 3, 'question flagged after 3 distinct reports');
let seenAgain = false;
for (let i = 0; i < 8; i++) { const p = (await as(U.B, 'create_paper', [null, 'any', 'private', null, null])).r; if ((await q(`select 1 from paper_questions where paper_id = $1 and question_id = $2`, [p, target.question_id])).length) seenAgain = true; }
ok(!seenAgain, 'flagged question never appears in new papers (8 papers checked)');

// regrade: flip the key of a question user A got right in the first attempt
const gotRight = view.questions.find(x => x.type === 'MCQ' && x.is_correct === true && x.marks === 2) ?? view.questions.find(x => x.type === 'MCQ' && x.is_correct === true);
const before = Number((await q(`select score from attempts where id = $1`, [att]))[0].score);
const nre = await svc('admin_regrade_question', [gotRight.question_id, JSON.stringify(['D']), 'Corrected solution.']);
const after = Number((await q(`select score from attempts where id = $1`, [att]))[0].score);
const expectedDelta = -(gotRight.marks + gotRight.marks / 3);
ok(nre.r >= 1 && near(after - before, expectedDelta, 0.011), `regrade re-scores finished attempts (score ${before} -> ${after}, expected change ${expectedDelta.toFixed(2)})`);
ok((await q(`select key_version from question_keys where question_id = $1`, [gotRight.question_id]))[0].key_version === 2, 'key_version bumped');
ok((await svc('admin_regrade_question', [gotRight.question_id, JSON.stringify(['Z'])])).err === 'bad_answer', 'regrade validates the new key');

// ---- 10. missing questions -> clear error ----------------------------------------------------------
section('insufficient questions');
const dlIds = (await q(`select q.id from questions q join subjects s on s.id = q.subject_id where s.code = 'DL'`)).map(x => x.id);
await svc('admin_set_question_status', [`{${dlIds.join(',')}}`, 'retired']);
const short = await as(U.A, 'create_paper', [null, 'any', 'private', null, null]);
ok(short.err === 'insufficient_questions' && JSON.parse(short.detail).some(d => d.subject === 'DL' && d.available === 0), 'clear error listing which subject/marks is short: ' + short.detail);
await svc('admin_set_question_status', [`{${dlIds.join(',')}}`, 'approved']);
ok(!(await as(U.A, 'create_paper', [null, 'any', 'private', null, null])).err, 'works again once questions are restored');
const stats = (await svc('admin_bank_stats', [200])).r;
ok(stats.length === 11 && stats.every(s => s.still_needed > 0 && s.with_solution > 0), 'admin_bank_stats shows what is still needed per subject');

// ---- 11. global leaderboard ------------------------------------------------------------------------
section('global leaderboard');
for (let i = 0; i < 2; i++) { const p = (await as(U.A, 'create_paper', [null, 'any', 'public', null, null])).r; await take(U.A, p, 0.8); await take(U.B, p, 0.4); }
await svc('refresh_global_leaderboard', []);
const gl = (await as(U.A, 'get_global_leaderboard', [10])).r;
ok(gl.length >= 2 && gl[0].display_name === 'Alice' && gl.every(e => e.papers_taken >= 3), 'global leaderboard (min 3 papers), best average first');


// ---- 12. optional load check:  SCALE=20000 npm test --------------------------------------------------
if (process.env.SCALE) {
  section('scale (' + process.env.SCALE + ' ranked submissions on ONE paper; PGlite is single-threaded WASM, so a real server is faster)');
  const N = Number(process.env.SCALE);
  await q(`insert into auth.users (id, email) select gen_random_uuid(), 's' || g || '@x.com' from generate_series(1, $1) g`, [N]);
  await q(`insert into attempts (paper_id, user_id, attempt_no, status, started_at, deadline_at, submitted_at, score, max_score, time_spent_sec, counts_for_rank)
           select $1, p.id, 1, 'submitted', now() - interval '4 hours', now() - interval '1 hour', now() - interval '2 hours',
                  round((random() * 100)::numeric, 2), 100, (random() * 10000)::int, true
             from profiles p where p.id not in (select user_id from attempts where paper_id = $1)`, [paperP]);
  await q('analyze attempts');
  const total = (await q(`select count(*)::int n from attempts where paper_id = $1 and counts_for_rank`, [paperP]))[0].n;
  const timeIt = async (label, fn, limitMs) => { const t0 = performance.now(); const r = await fn(); const ms = performance.now() - t0; console.log(`  ${label}: ${ms.toFixed(0)} ms`); ok(ms < limitMs, `${label} < ${limitMs} ms`); return r; };
  const top = await timeIt(`leaderboard top 50 of ${total}`, () => as(U.A, 'get_leaderboard', [paperP, 50, 0]), 1500);
  ok(top.r.participants === total && top.r.entries.length === 50, 'top-50 correct');
  await timeIt('leaderboard page at offset 10000', () => as(U.A, 'get_leaderboard', [paperP, 50, 10000]), 3000);
  await timeIt('my standing (rank/percentile/avg)', () => q(`select attempt_standing($1)`, [att]), 1500);
  await timeIt('submit + score one 65-question attempt', async () => { const p = (await as(U.D, 'create_paper', [null, 'any', 'private', null, null])).r; const a = (await as(U.D, 'start_attempt', [p])).r; return as(U.D, 'submit_attempt', [a]); }, 3000);
  const plan = await q(`explain select count(*) filter (where score > 50) from attempts where paper_id = $1 and counts_for_rank and status <> 'in_progress'`, [paperP]);
  console.log('  standing query plan uses:', plan.map(r => r['QUERY PLAN']).join(' | ').match(/(Index[ A-Za-z]*Scan|Seq Scan|Bitmap[ A-Za-z]*)/)?.[0]);
}

console.log(`\n${pass} checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
