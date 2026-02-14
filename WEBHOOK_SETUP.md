# Stripe Webhooks Setup Guide

You have upgraded to a professional Stripe Webhook implementation.
This replaces the old polling method (every 15s check) with instant notifications.

## 1. Prerequisites
Ensure you have the new dependencies installed:
```bash
uv add fastapi uvicorn
```

## 2. Local Development (Testing)
Since your bot is running on `localhost`, Stripe cannot send events directly to it.
You need a tunnel.

### Option A: Stripe CLI (Recommended)
1.  Download and install Stripe CLI from [stripe.com/docs/stripe-cli](https://stripe.com/docs/stripe-cli).
2.  Login: `stripe login`
3.  Start forwarding events:
    ```bash
    stripe listen --forward-to localhost:8000/stripe/webhook
    ```
4.  Copy the **Webhook Signing Secret** (starts with `whsec_...`) printed in the terminal.
5.  Add it to your `.env` file:
    ```ini
    STRIPE_WEBHOOK_SECRET=whsec_your_secret_here
    ```

### Option B: Ngrok
1.  Install ngrok.
2.  Run `ngrok http 8000`.
3.  Copy the HTTPS URL (e.g., `https://abc.ngrok.io`).
4.  Go to Stripe Dashboard > Developers > Webhooks > Add Endpoint.
5.  URL: `https://abc.ngrok.io/stripe/webhook`
6.  Select event: `checkout.session.completed`
7.  Get the Signing Secret and add to `.env`.

## 3. Running the System
You now need to run TWO processes:

### Terminal 1: The Telegram Bot
Handles user interactions and commands.
```bash
uv run app/bot.py
```

### Terminal 2: The Webhook Server
Listens for Stripe payments.
```bash
uv run uvicorn app.webhook_server:app --reload --port 8000
```

## 4. Production Deployment
When deploying to a server (VPS, Heroku, etc.):
1.  Set the `STRIPE_WEBHOOK_SECRET` environment variable on the server.
2.  Configure your server (Nginx/Reverse Proxy) to forward traffic to the uvicorn process.
3.  Ensure your domain has HTTPS (Stripe requires HTTPS for live mode webhooks).
