# RiskLayer — Deploy & Security Checklist

This file captures the action items you need to complete on the outside-of-repo
systems (Render dashboard, Clerk, Lemon Squeezy) for the refactor to work end
to end. Everything inside the repo is already updated.

## What changed in the repo

- `render.yaml` now defines **three** services:
  1. `risk-layer`    (FastAPI, unchanged)
  2. `risk-layer-web` (Node — now a pure JSON API; no more static files)
  3. `risk-layer-ui`     (new **Render Static Site** serving `/frontend`)
- `server/index.js`:
  - Removed dead static-file routes (`/`, `/app`, `/account`, `express.static`)
  - **Fail-closed CORS** — refuses to start if `ALLOWED_ORIGIN` isn’t set
  - Added `helmet` (security headers) and `express-rate-limit`
  - Added `/healthz` liveness probe
  - Body size capped (16 KB JSON, 64 KB webhook raw)
  - Generic 404 + last-resort error handler
- `server/package.json` adds `helmet` and `express-rate-limit`.
- `frontend/{index,landing,account}.html`:
  - All API calls go through `authUrl()` against `window.AUTH_ORIGIN`
  - Clerk SDK pinned to `@6` (no more `@latest`)
  - `escapeHtml()` applied to every API-string sink in the dashboard
  - Removed mock-data mode & the extra “Upgrade” header button
  - `/admin/weekly-eval` → `/admin/eval/lift` (correct backend route)

## Action items you need to do manually

### 1. Set env vars on each Render service

On **`risk-layer`** (FastAPI):
- `DATABASE_URL` — Supabase pooler connection string
- Confirm `ALLOW_ORIGINS` is `["https://risk-layer-ui.onrender.com"]`
  (add custom domains here when you wire one up)

On **`risk-layer-web`** (Node):
- `DATABASE_URL` — same Supabase string
- `CLERK_PUBLISHABLE_KEY` — from Clerk dashboard → API Keys
- `CLERK_SECRET_KEY` — from Clerk dashboard → API Keys
- `LEMONSQUEEZY_WEBHOOK_SECRET` — from Lemon Squeezy → Settings → Webhooks
- `LEMONSQUEEZY_CHECKOUT_URL` — from Lemon Squeezy → your product checkout URL
- Confirm `ALLOWED_ORIGIN` is `https://risk-layer-ui.onrender.com`
  (the server now **refuses to boot** if this is missing — this is intentional)

### 2. Deploy the new static site

After pushing `render.yaml` to your repo, open the Render dashboard and it
should offer to create the `risk-layer-ui` service automatically. If not,
create it manually:
- Type: **Static Site**
- Root: repo root (do **not** set `rootDir`)
- Build command: (empty)
- Publish path: `./frontend`

Once deployed, confirm the URL is `https://risk-layer-ui.onrender.com` (if
Render assigns a different slug, update the `ALLOWED_ORIGIN` and
`ALLOW_ORIGINS` env vars on the other two services to match).

### 3. Clerk dashboard — allowed origins & redirect URLs

In Clerk → **Domains** (or **Allowed origins** depending on plan):
- Add `https://risk-layer-ui.onrender.com`
- Remove the old `risk-layer.onrender.com` (Node) origin if it was listed for
  frontend use — it’s API-only now.

In Clerk → **Paths**:
- Sign-in URL: `/app`
- Sign-up URL: `/app`
- After sign-in URL: `/app?upgrade=1` (if user came from Upgrade button;
  landing → `/app` otherwise)
- After sign-up URL: `/app?upgrade=1`

### 4. Lemon Squeezy webhook URL

The webhook still lives on the Node service. In Lemon Squeezy → **Settings →
Webhooks**, point the webhook at:

```
https://risk-layer-web.onrender.com/webhook/lemonsqueezy
```

Make sure the signing secret there matches `LEMONSQUEEZY_WEBHOOK_SECRET` on
the Node service.

### 5. Re-deploy the Node service

The Node service needs a fresh deploy so that the new `helmet` and
`express-rate-limit` packages install. Render should do this automatically
after the next push, but verify the build log shows:

```
added 2 packages …  helmet  express-rate-limit
```

If the deploy fails with `FATAL: ALLOWED_ORIGIN is not set`, the env var
wasn’t saved — re-apply it in the Render dashboard and trigger a manual
redeploy.

### 6. Smoke test

After everything is live, verify from the static site URL:
- `GET  https://risk-layer-ui.onrender.com/`            → landing page loads
- `GET  https://risk-layer-ui.onrender.com/app`          → dashboard loads
- `GET  https://risk-layer-ui.onrender.com/account`      → account page loads
- In devtools, confirm no CSP violations in the console
- Sign in → dashboard should fetch `/me` from the Node service and populate
  plan info; **no "Missing publishableKey" error**
- Click "Upgrade to Pro" while logged out → Clerk sign-up modal opens
- Click "Upgrade to Pro" while logged in → Lemon Squeezy checkout opens with
  `clerk_user_id` prefilled
- Admin tab → "Outcome context" should load without the 404 on `weekly-eval`

## Known follow-ups (not blocking)

- **SRI hash for Clerk script.** Right now `clerk.browser.js` is pinned to
  `@6`, loaded from jsdelivr with `crossorigin="anonymous"`. Ideally we’d
  also set a Subresource Integrity hash, but jsdelivr’s `@6` tag is a moving
  target (updates minor versions). Either:
  - pin to an exact version (e.g. `@clerk/clerk-js@6.7.0`) and compute SRI, or
  - self-host the script on the static site and set SRI against the hosted copy.
- **Custom domain.** When you wire up a real domain, update three places:
  Render dashboard (static site), `ALLOW_ORIGINS` on the FastAPI service,
  `ALLOWED_ORIGIN` on the Node service, and Clerk allowed origins.
- **Rate-limit tuning.** Current limits (120 req/min global, 30 req/min
  on `/me` and `/config`) are conservative. Revisit after a week of real
  traffic.
- **Helmet CSP on API.** The Node service disables Helmet’s CSP because it
  returns JSON. If you later serve any HTML from it, re-enable it.
