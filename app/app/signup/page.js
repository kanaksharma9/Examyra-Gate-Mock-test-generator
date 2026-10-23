import Link from "next/link";
import { Shell } from "../../components/Shell";
import { AuthForm } from "../../components/AuthForm";
export default function Signup(){return <Shell><div className="mx-auto max-w-7xl px-5 py-20"><h1 className="mb-3 text-center text-4xl font-bold">Start your prep</h1><p className="mb-8 text-center text-[var(--muted)]">Your first mock is a few clicks away.</p><AuthForm mode="signup"/><p className="mt-5 text-center text-sm text-[var(--muted)]">Already registered? <Link className="text-[var(--primary)]" href="/login">Sign in</Link></p></div></Shell>}
