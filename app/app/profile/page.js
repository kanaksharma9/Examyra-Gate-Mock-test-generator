"use client";
import { useEffect, useState } from "react";
import { AuthGuard } from "../../components/AuthGuard";
import { Shell } from "../../components/Shell";
import { useApp } from "../../components/Providers";
import { get } from "../../lib/client";
export default function Profile(){return <AuthGuard><ProfileContent/></AuthGuard>}
function ProfileContent(){const {supabase}=useApp();const [user,setUser]=useState();const [attempts,setAttempts]=useState([]);useEffect(()=>{supabase.auth.getUser().then(({data})=>setUser(data.user));get("/api/me/attempts").then(setAttempts).catch(()=>{})},[supabase]);return <Shell user={user}><div className="mx-auto max-w-5xl px-5 py-10"><h1 className="text-4xl font-bold">Your profile</h1><div className="card mt-8 flex items-center gap-5 p-6"><div className="grid h-16 w-16 place-items-center rounded-full bg-[var(--primary)] text-2xl text-white">👤</div><div><h2 className="text-xl font-bold">{user?.user_metadata?.display_name||`Student-${user?.id?.replaceAll("-","").slice(0,6)}`}</h2><p className="text-[var(--muted)]">{user?.email}</p></div><button onClick={()=>supabase.auth.signOut()} className="ml-auto rounded-xl border border-[var(--line)] px-4 py-2">Sign out</button></div><div className="card mt-5 p-6"><h2 className="text-xl font-bold">Attempt history</h2>{attempts.map(x=><div className="flex justify-between border-b border-[var(--line)] py-4" key={x.attempt_id}><span>{x.title}</span><b>{x.score??"—"} / {x.max_score}</b></div>)}</div></div></Shell>}
