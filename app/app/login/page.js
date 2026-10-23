import Link from "next/link";
import { Shell } from "../../components/Shell";
import { AuthForm } from "../../components/AuthForm";
export default function Login(){return <Shell><div className="mx-auto max-w-7xl px-5 py-20"><h1 className="mb-3 text-center text-4xl font-bold">Welcome back</h1><p className="mb-8 text-center text-[var(--muted)]">Continue your preparation.</p><AuthForm mode="login"/><p className="mt-5 text-center text-sm text-[var(--muted)]">New here? <Link className="text-[var(--primary)]" href="/signup">Create an account</Link></p></div></Shell>}
