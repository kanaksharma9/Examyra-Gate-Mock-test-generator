"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Flame, Target, TrendingUp } from "lucide-react";
import { AuthGuard } from "../../components/AuthGuard";
import { Shell } from "../../components/Shell";
import { get } from "../../lib/client";

export default function Dashboard() {
  return <AuthGuard><DashboardContent /></AuthGuard>;
}

function DashboardContent() {
  const [progress, setProgress] = useState({ subjects: [], trend: [] });
  const [attempts, setAttempts] = useState([]);
  const [rows, setRows] = useState([]);

  useEffect(() => {
    Promise.all([get("/api/me/progress"), get("/api/me/attempts"), get("/api/leaderboard/global")])
      .then(([nextProgress, nextAttempts, nextRows]) => {
        setProgress(nextProgress);
        setAttempts(nextAttempts || []);
        setRows(nextRows || []);
      })
      .catch(() => {});
  }, []);

  return <Shell><div className="mx-auto max-w-7xl px-5 py-10">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="text-sm font-semibold text-[var(--primary)]">YOUR CONTROL ROOM</p><h1 className="mt-2 text-4xl font-bold">Keep the streak alive.</h1><p className="mt-2 text-[var(--muted)]">Small, focused sessions add up to a big score.</p></div>
      <Link href="/papers" className="flex items-center gap-2 rounded-xl bg-[var(--primary)] px-5 py-3 font-semibold text-white">Generate a mock <ArrowRight size={17} /></Link>
    </div>
    <div className="mt-8 grid gap-5 md:grid-cols-3">
      <div className="card bg-[var(--primary)] p-6 text-white"><Flame /><p className="mt-5 text-3xl font-bold">{attempts.length}</p><p className="text-white/70">attempts completed</p></div>
      <div className="card p-6"><Target className="text-[var(--primary)]" /><p className="mt-5 text-3xl font-bold">{progress.subjects?.length || 0}</p><p className="text-[var(--muted)]">subjects tracked</p></div>
      <div className="card p-6"><TrendingUp className="text-[var(--green)]" /><p className="mt-5 text-3xl font-bold">{progress.trend?.[0]?.score ?? "—"}</p><p className="text-[var(--muted)]">latest score</p></div>
    </div>
    <div className="mt-8 grid gap-5 lg:grid-cols-[1.3fr_.7fr]">
      <div className="card p-6"><h2 className="text-xl font-bold">Score trend</h2><div className="mt-6 flex h-48 items-end gap-2 border-b border-l border-[var(--line)] p-4">{(progress.trend || []).slice().reverse().map((x, i) => <div key={x.attempt_id || i} className="group flex h-full flex-1 flex-col justify-end"><div className="mx-auto w-full max-w-10 rounded-t-lg bg-[var(--primary)]" style={{ height: `${Math.max(8, Number(x.score || 0) / Number(x.max_score || 100) * 100)}%` }} title={`${x.score}/${x.max_score}`} /><span className="mt-2 text-center text-xs text-[var(--muted)]">{i + 1}</span></div>)}</div></div>
      <div className="card p-6"><h2 className="text-xl font-bold">Focus areas</h2><div className="mt-5 space-y-4">{(progress.subjects || []).slice(0, 5).map((s) => <div key={s.code}><div className="flex justify-between text-sm"><span>{s.subject}</span><b>{s.accuracy_pct ?? 0}%</b></div><div className="mt-2 h-2 rounded bg-[var(--primary-soft)]"><div className="h-2 rounded bg-[var(--primary)]" style={{ width: `${Math.min(100, s.accuracy_pct || 0)}%` }} /></div></div>)}</div></div>
    </div>
    <div className="mt-5 grid gap-5 lg:grid-cols-2">
      <div className="card p-6"><h2 className="text-xl font-bold">Recent attempts</h2><div className="mt-4 divide-y divide-[var(--line)]">{attempts.slice(0, 5).map((a) => <Link href={`/result/${a.attempt_id}`} className="flex justify-between py-4" key={a.attempt_id}><span><b>{a.title}</b><small className="ml-3 text-[var(--muted)]">{a.status}</small></span><strong>{a.score ?? "—"} / {a.max_score}</strong></Link>)}</div></div>
      <div className="card p-6"><h2 className="text-xl font-bold">Global ranking</h2><p className="mt-2 text-sm text-[var(--muted)]">Requires at least 3 completed papers.</p><div className="mt-4 divide-y divide-[var(--line)]">{rows.slice(0, 5).map((x) => <div className="flex items-center justify-between py-3 text-sm" key={`${x.rank}-${x.display_name}`}><span><b>#{x.rank}</b><span className="ml-3">{x.display_name}{x.is_me && " (you)"}</span></span><strong>{x.avg_pct}%</strong></div>)}</div></div>
    </div>
  </div></Shell>;
}
