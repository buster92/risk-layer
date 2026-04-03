'use strict';
require('dotenv').config();

const path    = require('path');
const crypto  = require('crypto');
const express = require('express');
const cors    = require('cors');
const { Pool } = require('pg');
const { ClerkExpressRequireAuth, clerkClient } = require('@clerk/clerk-sdk-node');

const app  = express();
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const FRONTEND_DIR = path.join(__dirname, '..', 'frontend');

// ── Middleware ────────────────────────────────────────────────────
app.use(cors({ origin: process.env.ALLOWED_ORIGIN || '*', credentials: true }));

// Webhook route needs raw body for HMAC verification — register BEFORE json()
app.post(
  '/webhook/lemonsqueezy',
  express.raw({ type: 'application/json' }),
  handleLemonSqueezyWebhook
);

app.use(express.json());

// ── Public config ─────────────────────────────────────────────────
// Returns non-secret config the frontend needs to bootstrap Clerk + payments
app.get('/config', (_, res) => {
  res.json({
    clerkPublishableKey: process.env.CLERK_PUBLISHABLE_KEY || null,
    checkoutUrl:         process.env.LEMONSQUEEZY_CHECKOUT_URL || null,
  });
});

// ── Static files ──────────────────────────────────────────────────
// Landing page
app.get('/', (_, res) => res.sendFile(path.join(FRONTEND_DIR, 'landing.html')));
// Dashboard (protected by Clerk middleware below)
app.get('/app', (_, res) => res.sendFile(path.join(FRONTEND_DIR, 'index.html')));
// Account page
app.get('/account', (_, res) => res.sendFile(path.join(FRONTEND_DIR, 'account.html')));
// Serve any other static assets (CSS, JS, images)
app.use(express.static(FRONTEND_DIR));

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
app.get('/me', ClerkExpressRequireAuth(), async (req, res) => {
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

initDb()
  .then(() => {
    app.listen(PORT, () => {
      console.log(`[server] RiskLayer running at http://localhost:${PORT}`);
      console.log(`  /           → landing page`);
      console.log(`  /app        → dashboard`);
      console.log(`  /account    → account/billing`);
      console.log(`  /me         → user plan (authenticated)`);
      console.log(`  /webhook/lemonsqueezy → payment events`);
    });
  })
  .catch(err => {
    console.error('[server] Failed to init DB:', err.message);
    process.exit(1);
  });
