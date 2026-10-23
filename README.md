# GATE CSE Mock Test Generator

The deployable web application is in [`app/`](./app). It is a Next.js App Router
application backed by Supabase, with the database migrations and backend design
documentation in [`backend/`](./backend).

## Run locally

```powershell
cd app
Copy-Item .env.example .env.local
npm ci
npm run dev
```

Fill in the Supabase values and server-only secrets in `app/.env.local` before
using authenticated routes. See [`app/README.md`](./app/README.md) for the
complete environment contract and smoke test.

## Deploy to Vercel

1. Import this repository into Vercel.
2. Set **Root Directory** to `app`.
3. Set the five required environment variables from `app/.env.example` for
   Production, Preview, and Development as appropriate.
4. Deploy. `app/vercel.json` registers the protected expiry cron endpoint.

Never commit `.env` files or a Supabase service-role key.
