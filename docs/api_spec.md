# MoveCred API Specification

## Base URL

Development: `http://localhost:8000`
API prefix:  `/v1`

Interactive docs: `http://localhost:8000/docs`

---

## Authentication

v1 uses a stub header-based scheme for admin and alert endpoints.
Pass `X-User-Id: {user_id}` for protected routes.

Production: replace with JWT middleware.

---

## Endpoints

### Health

#### `GET /health`

Check service and model availability.

**Response:**
```json
{
  "status": "ok",
  "app": "MoveCred",
  "version": "0.1.0",
  "environment": "development",
  "utc": "2024-11-15T18:00:00",
  "models_available": {
    "continue_3d": true,
    "continue_5d": true,
    "drawdown_gt_3pct_5d": true,
    "mean_revert_3d": true
  }
}
```

---

### Market Board

#### `GET /v1/market/active`

Daily active-stock board after market close.

**Query params:**
- `date` (optional): `YYYY-MM-DD` — defaults to last closed trading day
- `sort_by`: `active` | `deceptive` | `continuation` — default `active`
- `limit`: 1–100 — default 50

**Response:**
```json
{
  "date": "2024-11-15",
  "sort_by": "active",
  "count": 50,
  "stocks": [
    {
      "rank": 1,
      "ticker": "NVDA",
      "company_name": "NVIDIA Corporation",
      "sector": "Technology",
      "classification": "Strong continuation profile",
      "interpretation": "Participation and trend context are aligned...",
      "confidence_bucket": "high",
      "p_continue_3d": 0.68,
      "p_continue_5d": 0.61,
      "p_drawdown_5d": 0.21,
      "p_mean_revert_3d": 0.18,
      "risk_score": 0.22,
      "deception_score": 0.22,
      "continuation_score": 0.71,
      "flags": ["Trend strength improving", "Sector aligned"]
    }
  ]
}
```

---

#### `GET /v1/market/top-risks`

Ranked list of most deceptive moves today.

**Query params:**
- `date` (optional)
- `limit`: 1–50 — default 10

---

#### `GET /v1/market/top-continuations`

Ranked list of strongest continuation profiles today.

**Query params:**
- `date` (optional)
- `limit`: 1–50 — default 10

---

### Ticker Analysis

#### `GET /v1/stocks/{ticker}/analysis`

Full credibility analysis for a single ticker.

**Query params:**
- `date` (optional): defaults to last closed trading day

**Response:**
```json
{
  "ticker": "TSLA",
  "company_name": "Tesla Inc.",
  "sector": "Consumer Discretionary",
  "date": "2024-11-15",
  "classification": "Breakout with exhaustion risk",
  "interpretation": "This move is attracting heavy attention, but similar setups usually fail to build stable follow-through.",
  "confidence_bucket": "medium",
  "risk_score": 0.62,
  "deception_score": 0.62,
  "continuation_score": 0.28,
  "flags": [
    "Relative volume elevated",
    "Volatility expanding",
    "Price extended from trend",
    "Weak sector confirmation"
  ],
  "probabilities": {
    "p_continue_3d": 0.31,
    "p_continue_5d": 0.28,
    "p_drawdown_5d": 0.49,
    "p_mean_revert_3d": 0.44
  },
  "top_signals": [
    {"name": "Relative Volume", "value": 3.2, "unit": "x avg"},
    {"name": "ADX", "value": 18.4, "unit": ""},
    {"name": "Move Z-Score", "value": 2.3, "unit": "σ"}
  ],
  "market_regime": 1,
  "sector_trend_state": -1
}
```

---

#### `GET /v1/stocks/{ticker}/history`

Recent classification history for a ticker.

**Query params:**
- `days`: 1–365 — default 30

**Response:**
```json
{
  "ticker": "TSLA",
  "history": [
    {
      "date": "2024-11-15",
      "classification": "Breakout with exhaustion risk",
      "p_continue_3d": 0.31,
      "p_drawdown_5d": 0.49,
      "risk_score": 0.62,
      "flags": ["Relative volume elevated"]
    }
  ]
}
```

---

### Digest

#### `GET /v1/digest/daily`

Daily digest payload — top risks, continuations, prior outcomes.

**Query params:**
- `date` (optional)

---

### Alerts

#### `POST /v1/alerts`

Create an alert rule. Requires `X-User-Id` header.

**Request body:**
```json
{
  "ticker": "TSLA",
  "condition": "deception_score_above",
  "threshold": 0.65
}
```

**Conditions:**
- `classification_equals` — fires when classification matches threshold (as string)
- `p_continue_3d_below` — fires when continuation prob drops below threshold
- `p_continue_3d_above`
- `p_drawdown_5d_above` — fires when drawdown risk exceeds threshold
- `risk_score_above`
- `deception_score_above`

#### `GET /v1/alerts`

List active alerts. Requires `X-User-Id` header.

#### `DELETE /v1/alerts/{alert_id}`

Deactivate an alert. Requires `X-User-Id` header.

---

### Admin

#### `POST /v1/admin/jobs/ingest`

Trigger daily ingest manually.

**Query params:**
- `date` (optional): specific date to ingest

#### `POST /v1/admin/jobs/predict`

Trigger daily prediction pipeline manually.

#### `POST /v1/admin/jobs/eval`

Trigger weekly outcome evaluation.

#### `GET /v1/admin/models/status`

Check which model artifacts are available.

#### `GET /v1/admin/eval/lift`

Get lift metrics by classification bucket.

**Query params:**
- `lookback_days`: default 90

**Response:**
```json
{
  "baseline_continuation_rate": 0.54,
  "by_class": {
    "Strong continuation profile": {
      "count": 142,
      "hit_rate": 0.68,
      "lift_vs_baseline": 0.14
    },
    "Weak continuation setup": {
      "count": 98,
      "hit_rate": 0.37,
      "lift_vs_baseline": -0.17
    }
  }
}
```

---

## Classification Labels

| Label | Description |
|---|---|
| `Strong continuation profile` | High probability of continuation, trend and participation aligned |
| `Trend-confirming participation` | Solid odds, sector supportive |
| `Breakout with exhaustion risk` | High attention but overextended, weak follow-through odds |
| `Speculative spike` | Retail-style spike with poor sector context |
| `Panic flush with unstable structure` | Sharp decline, fragile structure |
| `High attention, low trust` | High volume but poor continuation odds |
| `Weak continuation setup` | Low continuation + high drawdown risk |
| `Neutral / insufficient signal` | No dominant pattern |

## Confidence Buckets

| Bucket | Meaning |
|---|---|
| `high` | Model has strong conviction, features align clearly |
| `medium` | Moderate conviction, some conflicting signals |
| `low` | Weak signal, use with extra caution |
| `insufficient` | Not enough data to classify |

## Error Codes

| Code | Meaning |
|---|---|
| 400 | Invalid date format or bad parameters |
| 401 | Authentication required |
| 404 | Stock or analysis not found |
| 500 | Internal error — check job logs |
