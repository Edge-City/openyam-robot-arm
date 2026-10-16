# Deploying the playground on Coolify (serenity)

From a read-only review of the private `Nhajela/serenity-cloud` docs (2026-10-16). Needs a human for DNS and the Coolify UI.

1. **DNS (Cloudflare):** A record `yam` → `170.187.250.12` (grey cloud until the first certificate is issued; if orange, SSL mode must be Full (strict)).
2. **Coolify** (https://coolify.positivesumexperiments.com): Project `openyam-playground` → New Resource → Application → public GitHub repo `https://github.com/Nhajela/openyam-playground` → Build Pack **Dockerfile** → exposed port **3000** → domain `https://yam.positivesumexperiments.com`.
3. **Env vars** (mark as secrets; values via `secrets get NAME` on serenity):
   - `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (set explicitly), **or** `LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `GEMINI_MODEL` (the key is Vertex-only, and the app calls `aiplatform.googleapis.com`)
   - `PUBLIC_BASE_URL=https://yam.positivesumexperiments.com`
   - `IP_HASH_SALT=<random string>`, optional `RATE_LIMIT_PER_HOUR` (default 10)
   - optional `DATABASE_URL`: create a Coolify PostgreSQL resource and use its internal URL (the table is created on first use or by `npm run migrate`). Without it, saved motions vanish on restart, but offline QR links keep working.
4. Deploy, enable auto-deploy on push, then do one live check: `curl -X POST https://yam.positivesumexperiments.com/api/generate -H 'content-type: application/json' -d '{"prompt":"wave hello"}'`.
5. serenity house rules: add a CHANGELOG/docs entry in serenity-cloud for the new app.

Vercel also works (same env vars; `DATABASE_URL` required, e.g. Neon with `?sslmode=require`).
