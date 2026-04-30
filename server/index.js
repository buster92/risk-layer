'use strict';
require('dotenv').config();

const crypto  = require('crypto');
const express = require('express');
const cors    = require('cors');
const helmet  = require('helmet');
const rateLimit = require('express-rate-limit');
const { Pool } = require('pg');
const { ClerkExpressRequireAuth, clerkClient } = require('@clerk/clerk-sdk-node');

const app  = express();
// Strip sslmode from connection string — Supabase appends ?sslmode=require which
// pg-connection-string v3 now treats as verify-full, blocking self-signed certs.
const dbUrl = (process.env.DATABASE_URL || '').replace(/[?&]sslmode=[^&]*/g, '');
const pool = new Pool({
  connectionString: dbUrl,
  ssl: process.env.NODE_ENV === 'production' ? { rejectUnauthorized: false } : false,
});

// ── Trust proxy ──────────────────────────────────────────────────
// Render terminates TLS at its edge — we must trust X-Forwarded-* for
// correct client IP (needed by rate limiting) and secure-cookie handling.
// Trust only the first proxy hop to avoid IP spoofing via forged headers.
app.set('trust proxy', 1);

// ── Security middleware ──────────────────────────────────────────
// Helmet sets sensible defaults: X-Content-Type-Options, X-DNS-Prefetch-Control,
// X-Download-Options, X-Frame-Options, Strict-Transport-Security, etc.
// We disable its CSP because this service is pure JSON API — no HTML served.
app.use(helmet({
  contentSecurityPolicy: false,
  crossOriginResourcePolicy: { policy: 'same-site' },
  referrerPolicy: { policy: 'strict-origin-when-cross-origin' },
}));

// ── CORS ─────────────────────────────────────────────────────────
// Fail-closed: if ALLOWED_ORIGIN is not configured, refuse all cross-origin
// requests rather than silently falling back to "*". Because /me uses
// credentials: 'include', browsers reject wildcard origins anyway — so a
// missing env var should fail loudly in the logs, not silently.
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN;
if (!ALLOWED_ORIGIN) {
  console.error('[server] FATAL: ALLOWED_ORIGIN is not set. Refusing to start with open CORS.');
  process.exit(1);
}
app.use(cors({
  origin: ALLOWED_ORIGIN,
  credentials: true,
  methods: ['GET', 'POST', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization'],
  maxAge: 600,
}));

// ── Rate limiting ────────────────────────────────────────────────
// Global soft cap — protects against accidental floods / basic abuse.
// Clerk and Lemon Squeezy traffic bypass this via dedicated limiters below.
const globalLimiter = rateLimit({
  windowMs: 60 * 1000,     // 1 minute
  max: 120,                // 120 req/min per IP
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests' },
});
app.use(globalLimiter);

// Tighter limiter for /me — authenticated endpoint that hits the DB + Clerk.
const meLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 30,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests' },
});

// /config is public and unauthenticated — keep it cheap.
const configLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 30,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests' },
});

// Webhook route needs raw body for HMAC verification — register BEFORE json().
// Cap the payload at 64 KB: Lemon Squeezy events are a few KB at most.
app.post(
  '/webhook/lemonsqueezy',
  express.raw({ type: 'application/json', limit: '64kb' }),
  handleLemonSqueezyWebhook
);

// JSON body parser for everything else, with a tight limit (no endpoint
// accepts large payloads today).
app.use(express.json({ limit: '16kb' }));

// ── Public config ─────────────────────────────────────────────────
// Returns non-secret config the frontend needs to bootstrap Clerk + payments
app.get('/config', configLimiter, (_, res) => {
  res.set('Cache-Control', 'no-store');
  res.json({
    clerkPublishableKey: process.env.CLERK_PUBLISHABLE_KEY || null,
    checkoutUrl:         process.env.LEMONSQUEEZY_CHECKOUT_URL || null,
  });
});

// ── Health check ─────────────────────────────────────────────────
// Lightweight liveness probe — no DB query, no auth.
app.get('/healthz', (_, res) => res.json({ ok: true }));

// ── Database init ─────────────────────────────────────────────────
async function initDb() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS risklayer_users (
      clerk_user_id        TEXT PRIMARY KEY,
      email                TEXT,
      plan                 TEXT    NOT NULL DEFAULT 'free',
      status               TEXT    NOT NULL DEFAULT 'active',
      ls_customer_id       TEXT,
      ls_subscription_id   TEXT,
      current_period_end   TIMESTAMPTZ,
      created_at           TIMESTAMPTZ DEFAULT NOW(),
      updated_at           TIMESTAMPTZ DEFAULT NOW()
    )
  `);
  console.log('[db] risklayer_users table ready');
}

// ── GET /me ───────────────────────────────────────────────────────
// Returns the authenticated user's plan + status.
// Called by the dashboard on every load.
app.get('/me', meLimiter, ClerkExpressRequireAuth(), async (req, res) => {
  res.set('Cache-Control', 'no-store');
  try {
    const userId = req.auth.userId;

    // Ensure a row exists for this user
    let { rows } = await pool.query(
      'SELECT * FROM risklayer_users WHERE clerk_user_id = $1',
      [userId]
    );

    if (!rows.length) {
      // First visit — create free record, pull email from Clerk
      let email = null;
      try {
        const clerkUser = await clerkClient.users.getUser(userId);
        email = clerkUser.emailAddresses?.[0]?.emailAddress || null;
      } catch { /* non-fatal */ }

      const insert = await pool.query(
        `INSERT INTO risklayer_users (clerk_user_id, email, plan, status)
         VALUES ($1, $2, 'free', 'active')
         ON CONFLICT (clerk_user_id) DO NOTHING
         RETURNING *`,
        [userId, email]
      );
      rows = insert.rows.length ? insert.rows : (
        await pool.query('SELECT * FROM risklayer_users WHERE clerk_user_id = $1', [userId])
      ).rows;
    }

    const user = rows[0];
    res.json({
      plan:               user.plan,
      status:             user.status,
      current_period_end: user.current_period_end,
    });
  } catch (err) {
    console.error('[/me]', err.message);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// ── POST /webhook/lemonsqueezy ────────────────────────────────────
async function handleLemonSqueezyWebhook(req, res) {
  const sig    = req.headers['x-signature'];
  const secret = process.env.LEMONSQUEEZY_WEBHOOK_SECRET;

  if (!sig || !secret) {
    return res.status(401).json({ error: 'Missing signature or secret' });
  }

  // Verify HMAC-SHA256 signature
  const digest = crypto
    .createHmac('sha256', secret)
    .update(req.body)
    .digest('hex');

  if (!crypto.timingSafeEqual(Buffer.from(digest), Buffer.from(sig))) {
    console.warn('[webhook] Invalid signature');
    return res.status(401).json({ error: 'Invalid signature' });
  }

  let payload;
  try {
    payload = JSON.parse(req.body.toString());
  } catch {
    return res.status(400).json({ error: 'Invalid JSON' });
  }

  const event       = payload.meta?.event_name;
  const custom      = payload.meta?.custom_data || {};
  const clerkUserId = custom.clerk_user_id || null;
  const attrs       = payload.data?.attributes || {};
  const email       = attrs.user_email || null;
  const subsId      = String(payload.data?.id || '');
  const customerId  = String(attrs.customer_id || '');
  const endsAt      = attrs.renews_at || attrs.ends_at || null;

  console.log(`[webhook] ${event} clerk=${clerkUserId} email=${email}`);

  if (!clerkUserId && !email) {
    return res.json({ received: true, note: 'No user identifier in payload' });
  }

  try {
    switch (event) {
      case 'subscription_created':
      case 'subscription_updated':
      case 'order_created': {
        // Upsert by clerk_user_id if available, else by email
        const upsertQuery = clerkUserId
          ? `INSERT INTO risklayer_users (clerk_user_id, email, plan, status, ls_subscription_id, ls_customer_id, current_period_end, updated_at)
             VALUES ($1, $2, 'pro', 'active', $3, $4, $5, NOW())
             ON CONFLICT (clerk_user_id) DO UPDATE SET
               plan = 'pro', status = 'active',
               ls_subscription_id = $3, ls_customer_id = $4,
               current_period_end = $5, updated_at = NOW()`
          : `UPDATE risklayer_users SET
               plan = 'pro', status = 'active',
               ls_subscription_id = $3, ls_customer_id = $4,
               current_period_end = $5, updated_at = NOW()
             WHERE email = $2`;
        await pool.query(upsertQuery, [clerkUserId, email, subsId, customerId, endsAt]);
        break;
      }

      case 'subscription_cancelled':
      case 'subscription_expired':
        await pool.query(
          `UPDATE risklayer_users SET plan = 'free', status = 'canceled', updated_at = NOW()
           WHERE clerk_user_id = $1 OR email = $2`,
          [clerkUserId, email]
        );
        break;

      case 'subscription_payment_failed':
        await pool.query(
          `UPDATE risklayer_users SET status = 'past_due', updated_at = NOW()
           WHERE clerk_user_id = $1 OR email = $2`,
          [clerkUserId, email]
        );
        break;

      default:
        // Unhandled event — log and ignore
        console.log(`[webhook] unhandled event: ${event}`);
    }
  } catch (err) {
    console.error('[webhook] DB error:', err.message);
    return res.status(500).json({ error: 'DB error' });
  }

  res.json({ received: true });
}

// ── Start ─────────────────────────────────────────────────────────
const PORT = process.env.PORT || 3001;

// Generic 404 handler — keeps error shape consistent for the frontend.
app.use((_, res) => res.status(404).json({ error: 'Not found' }));

// Last-resort error handler — log internally, leak nothing externally.
// eslint-disable-next-line no-unused-vars
app.use((err, _req, res, _next) => {
  console.error('[server] unhandled error:', err && err.message ? err.message : err);
  if (res.headersSent) return;
  res.status(500).json({ error: 'Internal server error' });
});

initDb()
  .then(() => {
    app.listen(PORT, () => {
      console.log(`[server] RiskLayer API running on port ${PORT}`);
      console.log(`  ALLOWED_ORIGIN = ${ALLOWED_ORIGIN}`);
      console.log(`  /healthz              → liveness`);
      console.log(`  /config               → public bootstrap config`);
      console.log(`  /me                   → user plan (authenticated)`);
      console.log(`  /webhook/lemonsqueezy → payment events`);
    });
  })
  .catch(err => {
    console.error('[server] Failed to init DB:', err.message);
    process.exit(1);
  });
