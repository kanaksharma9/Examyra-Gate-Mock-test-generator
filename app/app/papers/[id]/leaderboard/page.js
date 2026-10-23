"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AuthGuard } from "../../../../components/AuthGuard";
import { Shell } from "../../../../components/Shell";
import { get } from "../../../../lib/client";
export default function PaperLeaderboard(){return <AuthGuard><Content/></AuthGuard>}
function Content(){const {id}=useParams();const [data,setData]=useState();useEffect(()=>{get(`/api/papers/${id}/leaderboard`).then(setData).catch(()=>{})},[id]);return <Shell><div className="mx-auto max-w-5xl px-5 py-10"><p className="text-sm font-semibold text-[var(--primary)]">PAPER LEADERBOARD</p><h1 className="mt-2 text-4xl font-bold">See how you stack up.</h1><p className="mt-3 text-[var(--muted)]">{data?.participants||0} ranked participants</p><div className="card mt-8 overflow-hidden">{(data?.entries||[]).map(x=><div className="flex justify-between border-b border-[var(--line)] p-5" key={`${x.rank}-${x.display_name}`}><span><b className="mr-4 text-[var(--muted)]">#{x.rank}</b>{x.display_name}{x.is_me&&<small className="ml-2 text-[var(--primary)]">(you)</small>}</span><strong>{x.score}</strong></div>)}</div></div></Shell>}
