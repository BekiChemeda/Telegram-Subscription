# Telegram Subscription Bot

A professional Telegram bot for managing paid channel access with Stripe integration.

## Features

- **Automated Payments**: Users pay via Stripe (Credit Card, etc.).
- **Instant Access**: One-time invite links generated immediately after payment.
- **Fair Timer**: Subscription timer starts only when the user *joins* the channel.
- **Recurring Subscriptions**: Supports daily, weekly, monthly, and yearly auto-renewing plans.
- **Webhooks**: Real-time payment processing (no polling).
- **Enforcement**:
    - **Kick Unpaid**: Automatically removes users when their subscription expires.
    - **Duplicate Protection**: Prevents users from double-subscribing.
    - **Join Check**: Kicks users who join without a valid subscription.
- **Admin Dashboard**:
    - Create plans (One-time or Recurring).
    - View Analytics (Revenue, Active Users).

## Setup

1.  **Environment Variables**:
    Create a `.env` file:
    ```ini
    TELEGRAM_BOT_TOKEN=your_token
    STRIPE_API_KEY=sk_test_...
    STRIPE_WEBHOOK_SECRET=whsec_...
    ADMIN_ID=123456789
    MONGO_URI=mongodb://localhost:27017/ (Optional, defaults to local)
    ```

2.  **Run the Bot**:
    ```bash
    uv run app/bot.py
    ```

3.  **Run the Webhook Server**:
    ```bash
    uv run uvicorn app.webhook_server:app --port 8000
    ```

4.  **Stripe Configuration**:
    - Use `stripe listen` or Ngrok to forward events to `http://localhost:8000/stripe/webhook`.
    - Enable events: `checkout.session.completed`, `invoice.payment_succeeded`.

## Usage

- **User**: `/start` to view plans and subscribe.
- **Admin**: `/start` -> `Admin Panel` to add plans and view stats.

## Project Structure

- `app/bot.py`: Main Telegram bot logic.
- `app/webhook_server.py`: FastAPI server for Stripe events.
- `app/database.py`: MongoDB interface.
