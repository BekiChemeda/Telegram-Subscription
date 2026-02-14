import os
import logging
import stripe
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
try:
    import database as db
except ImportError:
    from . import database as db
from telegram import Bot
from telegram.constants import ParseMode
import asyncio

# Load environment variables
load_dotenv()

# Configuration
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Initialize Stripe
stripe.api_key = STRIPE_API_KEY

# Initialize FastAPI
app = FastAPI()

# Initialize Telegram Bot (for sending notifications)
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        # Invalid payload
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle the event
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        await handle_checkout_session(session)
    elif event["type"] == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        await handle_invoice_payment(invoice)

    return {"status": "success"}

async def handle_invoice_payment(invoice):
    # This event fires for subscription renewals
    sub_id = invoice.get("subscription")
    billing_reason = invoice.get("billing_reason")
    
    # We only care about renewals, not the initial payment (which is handled by checkout.session)
    if billing_reason == "subscription_create":
        return
        
    logger.info(f"Recurring payment received for subscription {sub_id}")
    
    # We need to find the plan to know how many days to extend
    # But wait, we don't store plan info in invoice easily without extra query
    # We can fetch subscription from DB to get plan_id
    
    # For now, let's assume monthly extending by 30 days is acceptable default 
    # OR better, fetch plan from DB via subscription
    
    import database as db
    sub = db.subscriptions_collection.find_one({"stripe_subscription_id": sub_id})
    if not sub:
        logger.warning(f"Subscription {sub_id} not found in DB")
        return
        
    plan = db.get_plan(sub['plan_id'])
    if not plan:
        return

    # Extend subscription
    user_id = db.extend_subscription_by_stripe_id(sub_id, plan['duration_days'])
    
    if user_id:
        try:
             await bot.send_message(
                chat_id=user_id,
                text=f"✅ **Renewal Successful!**\n\nYour subscription to **{plan['name']}** has been extended by {plan['duration_days']} days.",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
             pass

async def handle_checkout_session(session):
    user_id = session.get("metadata", {}).get("user_id")
    plan_id = session.get("metadata", {}).get("plan_id")
    
    if not user_id or not plan_id:
        logger.warning(f"Missing metadata in session {session['id']}")
        return

    user_id = int(user_id)
    plan_id = int(plan_id)
    stripe_sub_id = session.get("subscription")

    logger.info(f"Payment received from user {user_id} for plan {plan_id}")

    # 1. Update Database
    # We don't have the plan object here easily without querying DB
    plan = db.get_plan(plan_id)
    if not plan:
        logger.error(f"Plan {plan_id} not found")
        return

    # Activate subscription (or set to pending_join)
    # Note: activate_subscription handles the logic of expired/etc
    db.activate_subscription(user_id, plan_id, stripe_sub_id) 
    
    # Remove from pending payments if it was stored there
    db.remove_pending_payment(session['id'])

    # 2. Generate Invite Link & Notify User via Telegram
    plan = db.get_plan(plan_id)
    # Ensure plan exists and handle formatting
    if not plan: return
    
    try:
        # channel_id might be stored as int or str, ensure consistency for library
        channel_id = plan['channel_id']
        try: 
            channel_id = int(channel_id)
        except:
            pass
            
        invite_link_obj = await bot.create_chat_invite_link(
            chat_id=channel_id, 
            member_limit=1,
            name=f"Sub {user_id} {plan_id}"  # Underscores in name are fine but keep it simple
        )
        invite_url = invite_link_obj.invite_link
        
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"🎉 **Payment Received!**\n\n"
                f"You have subscribed to **{plan['name']}**.\n"
                f"The timer (`{plan['duration_days']} days`) will start **only when you join**.\n\n"
                f"⚠️ **Private Link**: Do not share! It works only once.\n\n"
                f"🔗 [Join Channel Now]({invite_url})"
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to send invite link to user {user_id}: {e}")
        # Fallback message
        try:
            await bot.send_message(user_id, f"✅ Payment received for **{plan['name']}**!\n\nHowever, I could not generate an invite link automatically ({e}).\n\nPlease contact the admin.", parse_mode=ParseMode.MARKDOWN)
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
