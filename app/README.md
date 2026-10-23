# GATE CSE Mock API

## Local development

1. Copy `.env.example` to `.env.local`.
2. Set the Supabase URL and publishable/anon key.
3. Set `SUPABASE_SERVICE_ROLE_KEY`, `ADMIN_TOKEN`, and `CRON_SECRET` to
   randomly generated values. The service-role key is server-only.
4. Install and start the app:

   ```powershell
   npm ci
   npm run dev
   ```

The app runs at `http://localhost:3000`. Run `npm test` for unit tests and
`npm run build` to verify a production build.

## Vercel deployment

When importing the repository into Vercel, set **Root Directory** to `app`.
Add the following environment variables in the Vercel project settings for
each environment that should run the app:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `ADMIN_TOKEN`
- `CRON_SECRET`

The cron endpoint in `vercel.json` calls `/api/cron/expire` once per day at
00:00 UTC. Vercel supplies
the `Authorization: Bearer <CRON_SECRET>` header when `CRON_SECRET` is set.
Do not put server-only secrets in variables prefixed with `NEXT_PUBLIC_`.

## API smoke test

Set `SMOKE_EMAIL` and `SMOKE_PASSWORD` to a Supabase test user's credentials,
start the app, then run:

```powershell
npm run smoke
```

The smoke test exercises paper generation, attempt creation, answer saving,
submission, and analysis using the same-origin API.
