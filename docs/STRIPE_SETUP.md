# Stripe setup

The code never contains a price. Stripe owns the products and prices; this
repository only asks Stripe to open a Checkout Session for a price that already
exists. That is deliberate: a number in code and a number in Stripe can drift,
and when they do, the customer is charged one amount and the plan is granted
against another. `phyto_shared.plans` holds **features and token allowances**,
never money (ADR-0047).

This document is the manual steps an operator performs once per Stripe account.
Nothing here is exercised by the test suite: the suite uses `MockStripeClient`
and `MockDOIRegistrar`.

---

## 1. Create the products and prices

In the Stripe Dashboard, create one **product** per paid plan and a recurring
**monthly price** for each:

| Plan | Code name | Notes |
| --- | --- | --- |
| Pro | `pro` | Single researcher, 2 000 tokens/month, API access |
| Lab | `lab` | 10 seats, 10 000 tokens/month, priority |
| Department | `department` | 200 seats, 100 000 tokens/month, SSO, audit export |

`free` and `enterprise` are not subscribable through Checkout: `free` is the
default state of an account, and `enterprise` is negotiated per contract.

The exact amounts are a business decision and intentionally absent from this
repository. Whatever you set in Stripe is the amount the webhook records as
MRR.

## 2. Configure the environment

Set these on the auth service (Secret Manager-backed in production, ADR-0032):

```bash
# Stripe API secret key. Without it, RealStripeClient refuses with 503
# rather than silently using the mock in production.
STRIPE_SECRET_KEY=sk_live_...

# One price id per subscribable plan. The code looks up the key for the
# requested plan; a missing id is a 503 with a message naming the variable.
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_LAB=price_...
STRIPE_PRICE_DEPARTMENT=price_...

# Where Checkout sends the user afterwards. Also used for share URLs and
# citation links.
PHYTO_APP_BASE_URL=https://app.example.com

# Signing secret for the webhook endpoint (Dashboard → Developers →
# Webhooks → your endpoint → "Signing secret"). Every delivery carries a
# Stripe-Signature header computed with this value; the handler verifies it
# and rejects anything that does not match. Without it the endpoint fails
# closed with 400 and grants no plan.
STRIPE_WEBHOOK_SECRET=whsec_...
```

`RealStripeClient` chooses the mock only when the app runs in dry-run mode
(`PHYTO_ENV` not `cloud`). A production deployment with no
`STRIPE_SECRET_KEY` fails loudly; it does not fall back.

The `whsec_...` above is a placeholder. The real value must never be committed:
it belongs in Secret Manager (ADR-0032) or an untracked `.env`. `.env.example`
holds the variable name with a dummy value.

## 3. Register the webhook

Create a webhook endpoint in the Dashboard pointing at:

```
POST https://<auth-service-host>/billing/webhook
```

Subscribe to `checkout.session.completed`. `POST /billing/webhook` is in
`PUBLIC_PATHS` because Stripe cannot present a Firebase token; the signature is
the credential.

The endpoint verifies `Stripe-Signature` on every delivery:

1. It reads the **raw** request body. The signature covers the exact bytes
   Stripe sent, so the handler must not re-serialize parsed JSON.
2. It calls `stripe.Webhook.construct_event(payload, sig_header, secret)` with
   `STRIPE_WEBHOOK_SECRET`, which checks the HMAC-SHA256 over
   `"{timestamp}.{payload}"` and rejects deliveries older than the default
   300-second tolerance.
3. A missing header, a bad signature, or a payload that cannot be parsed is
   rejected with **HTTP 400** before the processor runs. A missing secret is
   also a 400: with no secret we cannot distinguish Stripe from anyone else,
   so the endpoint fails closed.
4. Only a verified event reaches `WebhookProcessor`.

Copy the signing secret from the endpoint page in the Dashboard into
`STRIPE_WEBHOOK_SECRET` (§2). Each endpoint has its own secret, so rotating
the endpoint means rotating the variable.

For local testing without a Stripe account, compute a header yourself:
`t=<unix ts>,v1=HMAC_SHA256(f"{ts}.{payload}", secret)`. The test suite does
exactly this against a dummy secret (`tests/test_growth/test_stripe_webhook.py`).

## 4. What the webhook does

For `checkout.session.completed`:

1. Deduplicates on `session.id`. A Stripe retry returns the stored outcome with
   `duplicate: true` instead of re-granting the plan.
2. Reads `metadata.user_id` and `metadata.plan` from the session (both are set
   when the Checkout Session is created), falling back to
   `client_reference_id` for the user.
3. Sets the user's plan through the entitlements client.
4. Emits `plan_changed` with `{old_plan, new_plan, mrr_usd}` into the audit
   chain, where the cohort job reads it (ADR-0048).

MRR comes from `amount_total` (Stripe reports cents; the code divides by 100).
A session with no `amount_total` records `0.0`, which flatters the cohort's
margin — make sure the Dashboard price is configured if you rely on those
metrics.

Events other than `checkout.session.completed` return
`{"status": "ignored"}`. Malformed sessions return `{"status": "malformed"}`
**with HTTP 200**: Stripe retries 5xx indefinitely, and a session that is
missing its id will never become well-formed.

## 5. Cancel and downgrade

There is no subscription-deletion route yet. `plan_changed` is only emitted from
the successful-checkout path, so a cancellation in the Dashboard does not
currently downgrade the account or record revenue going to zero. Until that is
implemented, process cancellations by setting the plan through the
entitlements client directly and accepting that no `plan_changed` event is
recorded. Treat this as an operational workaround, not a supported flow.

## 6. Verify before launch

- [ ] `GET /billing/plans` returns all five plans and **no prices** (it lists
      features only).
- [ ] `POST /billing/subscribe` for `pro` returns a real `checkout_url` and a
      `cs_live_...` session id.
- [ ] Completing a test checkout moves the user's plan and appends exactly one
      `plan_changed` record to the audit chain.
- [ ] Re-delivering the same event returns `duplicate: true` and appends no
      second record.
- [ ] A request to a priced route by the upgraded user is billed against the
      new plan's allowance (ADR-0045).
- [ ] A delivery with a wrong or missing `Stripe-Signature` returns 400 and
      changes no plan.
- [ ] `STRIPE_WEBHOOK_SECRET` is set in the deployment environment and is not
      present in the repository.
