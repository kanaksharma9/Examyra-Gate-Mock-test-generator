"use client";
import Link from "next/link";
import { ArrowLeft, Moon, Sun, LogOut } from "lucide-react";
import { BRAND } from "../lib/brand";
import { useApp } from "./Providers";
export function Shell({ children, user, backHref, backLabel = "Back to dashboard" }) {
  const { theme, toggle, supabase, user: sessionUser } = useApp();
  const currentUser = user || sessionUser;
  return <div className="min-h-screen"><header className="border-b border-[var(--line)] bg-[var(--surface)]"><div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4"><div className="flex items-center gap-4"><Link href="/" className="flex items-center gap-3 font-bold"><span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--primary)] text-white">GM</span><span className="text-lg">{BRAND.name}<small className="ml-2 hidden font-normal text-[var(--muted)] sm:inline">{BRAND.tagline}</small></span></Link>{backHref && <Link href={backHref} className="flex items-center gap-1 rounded-lg px-2 py-2 text-sm font-semibold text-[var(--muted)] hover:bg-[var(--primary-soft)] sm:px-3"><ArrowLeft size={16}/><span className="hidden sm:inline">{backLabel}</span></Link>}</div><div className="flex items-center gap-2"><button aria-label="Toggle theme" onClick={toggle} className="rounded-lg p-2 hover:bg-[var(--primary-soft)]">{theme === "light" ? <Moon size={18}/> : <Sun size={18}/>}</button>{currentUser ? <><Link href="/profile" className="hidden rounded-full bg-[var(--primary-soft)] px-3 py-2 text-sm sm:block">{currentUser.email?.split("@")[0]}</Link><button aria-label="Sign out" onClick={() => supabase.auth.signOut()} className="rounded-lg p-2 hover:bg-[var(--primary-soft)]"><LogOut size={18}/></button></> : <Link href="/login" className="rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-semibold text-white">Sign in</Link>}</div></div></header><main>{children}</main></div>;
}
export function PageLoader() { return <div className="mx-auto max-w-7xl animate-pulse p-8"><div className="h-10 w-1/3 rounded bg-[var(--line)]"/><div className="mt-6 h-56 rounded-2xl bg-[var(--line)]"/></div>; }
