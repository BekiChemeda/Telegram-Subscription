import os
import logging
import stripe
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, 
    ContextTypes, 
    CommandHandler, 
    CallbackQueryHandler, 
    ChatMemberHandler, 
    MessageHandler,
    ConversationHandler,
    filters
)
from telegram.constants import ChatMemberStatus, ParseMode
import database as db
from datetime import datetime

# Load environment variables
load_dotenv()

# Configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

# Initialize Stripe
stripe.api_key = STRIPE_API_KEY

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- States ---
PLAN_NAME, PLAN_PRICE, PLAN_DURATION, PLAN_CHANNEL, PLAN_RECURRING = range(5)

# --- Helper Functions ---

async def delete_previous_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list):
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.warning(f"Failed to delete message {msg_id}: {e}")

async def get_main_menu_keyboard(user_id):
    keyboard = [
        [InlineKeyboardButton("💎 Subscribe", callback_data="menu_subscribe")],
        [InlineKeyboardButton("ℹ️ My Status", callback_data="menu_status")]
    ]
    
    # Check if user is admin
    if str(user_id) == str(ADMIN_ID):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="menu_admin")])
    
    return InlineKeyboardMarkup(keyboard)

async def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Add Plan", callback_data="admin_add_plan")],
        [InlineKeyboardButton("⚙️ Group Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("📊 Analytics", callback_data="admin_analytics")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def check_stripe_payments(context: ContextTypes.DEFAULT_TYPE):
    """
    Periodic task to check Stripe for completed payments.
    replaces usage of Webhooks for local simplicity.
    """
    pending_payments = db.get_all_pending_payments()
    
    for payment in pending_payments:
        session_id = payment['session_id']
        user_id = payment['user_id']
        plan_id = payment['plan_id']
        
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status == 'paid':
                plan = db.get_plan(plan_id)
                if plan:
                    db.activate_subscription(user_id, plan_id) # Just marks as paid/pending_join
                    db.remove_pending_payment(session_id)
                    
                    try:
                        # Generate Single-Use Invite Link
                        invite_link_obj = await context.bot.create_chat_invite_link(
                            chat_id=plan['channel_id'], 
                            member_limit=1,
                            name=f"Sub_{user_id}_{plan_id}" # Helps identify
                        )
                        invite_url = invite_link_obj.invite_link
                        
                        # Store this invite code to track usage if needed?
                        # Actually telegram doesn't give us the "code" easily in updates, but we can verify join via user_id
                        
                        await context.bot.send_message(
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
                        logger.warning(f"Could not gen link or notify user {user_id}: {e}")
                        await context.bot.send_message(user_id, "✅ Payment received! Use /status to check. Contact admin for link if not received.")

            elif session.status == 'expired':
                 db.remove_pending_payment(session_id)
                 
        except Exception as e:
            logger.error(f"Error checking session {session_id}: {e}")

async def validate_members_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Periodic task to validate active memberships.
    """
    # Get all expired active subscriptions from DB
    expired_subs = db.get_expired_active_subscriptions()
    
    for sub in expired_subs:
        try:
            user_id = sub['user_id']
            plan_id = sub['plan_id']
            plan = db.get_plan(plan_id)
            
            if not plan:
                logger.warning(f"Plan {plan_id} not found for sub {sub['_id']}")
                continue
                
            chat_id = plan['channel_id']
            
            # Kick user
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.unban_chat_member(chat_id, user_id) # Unban to allow re-joining later
            except Exception as e:
                logger.error(f"Could not kick user {user_id} from {chat_id}: {e}")
                
            # Mark as expired in DB
            db.subscriptions_collection.update_one(
                {"_id": sub['_id']},
                {"$set": {"status": "expired"}}
            )
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🚫 Your subscription to **{plan['name']}** has expired. Please renew to rejoin.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
                
        except Exception as outer_e:
            logger.error(f"Error processing expired sub {sub.get('_id')}: {outer_e}")

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reply_markup = await get_main_menu_keyboard(user_id)
    
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                text="👋 Welcome to the Subscription Bot!\nChoose an option below:",
                reply_markup=reply_markup
            )
        except:
             await update.callback_query.message.reply_text(
                "👋 Welcome to the Subscription Bot!\nChoose an option below:",
                reply_markup=reply_markup
            )
    else:
        # Check for deep linking arguments (success/cancel)
        if context.args:
            if context.args[0] == 'success':
                await update.message.reply_text(
                    "✅ **Payment Verification**\n\n"
                    "We are verifying your payment with Stripe. \n"
                    "You should receive an **Invite Link** in a separate message shortly.\n\n"
                    "check your status if you don't receive it.",
                    parse_mode=ParseMode.MARKDOWN
                )
            elif context.args[0] == 'cancel':
                await update.message.reply_text(
                    "❌ **Payment Cancelled**\nValues are not saved.",
                    parse_mode=ParseMode.MARKDOWN
                )

        await update.message.reply_text(
            "👋 Welcome to the Subscription Bot!\nChoose an option below:",
            reply_markup=reply_markup
        )

# --- Navigation Handlers ---

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def subscribe_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Get total groups
    groups = db.get_groups()
    if not groups:
         await query.edit_message_text(
            "😢 No channels set up.", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])
         )
         return

    keyboard = []
    for group in groups:
        keyboard.append([InlineKeyboardButton(f"📂 {group['title']}", callback_data=f"groupplans_{group['channel_id']}")])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await query.edit_message_text("👇 Choose a Channel:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_group_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    channel_id = data.split("_", 1)[1]
    user_id = query.from_user.id
    
    # Check subscription status FIRST to warn user
    # But allow them to see plans (maybe they want to extend/upgrade)
    is_subbed = db.is_user_subscribed_to_channel(user_id, channel_id)
    
    msg_prefix = ""
    if is_subbed:
         msg_prefix = "⚠️ **Note:** You already have an active subscription for this group.\n\n"

    plans = db.get_plans(channel_id=channel_id)
    group = db.get_group(channel_id)
    title = group['title'] if group else "Channel"

    if not plans:
        await query.edit_message_text(f"🚫 No active plans for {title}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_subscribe")]]))
        return

    keyboard = []
    for plan in plans:
        cycle_lbl = f"/{plan.get('recurring_interval')}" if plan.get('recurring_interval') else ""
        price = f"${plan['price_cents']/100:.2f}"
        btn_text = f"{plan['name']} - {price}{cycle_lbl}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"buy_{plan['id']}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_subscribe")])
    
    await query.edit_message_text(
        f"{msg_prefix}💎 **Plans for {title}**\nSelect one to continue:", 
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def status_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = query.from_user.id
    
    msg = ""
    # We used to check 'is_user_subscribed', but that now returns valid for pending_join too
    # We need robust details
    
    # Let's get all active subscriptions for the user
    subs = db.get_user_subscriptions(user_id)
    
    if not subs:
         msg = "❌ You do not have an active subscription."
    else:
         msg = "✅ **Your Subscriptions:**\n\n"
         for sub in subs:
             plan = db.get_plan(sub['plan_id'])
             plan_name = plan['name'] if plan else f"Plan {sub['plan_id']}"
             
             status = sub.get('status', 'unknown')
             
             if status == 'pending_join':
                 msg += f"📌 **{plan_name}**\nStatus: Pending Join (Timer starts when you join)\n\n"
             elif status == 'active':
                 expiry_ts = sub.get('expiry_timestamp')
                 # Safely format date
                 expiry_str = "Never"
                 if expiry_ts:
                    try:
                        expiry_str = datetime.fromtimestamp(expiry_ts).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        expiry_str = "Invalid Date"
                 
                 msg += f"🟢 **{plan_name}**\nExpires: {expiry_str}\n\n"
             elif status == 'expired':
                 msg += f"🔴 **{plan_name}**\nExpired\n\n"
    
    # Show toggle buttons for auto-renewal if applicable
    keyboard = []
    
    if subs:
         for sub in subs:
             if sub.get('stripe_subscription_id'):
                 plan = db.get_plan(sub['plan_id'])
                 plan_name = plan['name'] if plan else f"Plan {sub['plan_id']}"
                 is_auto = sub.get('auto_renew', True)
                 btn_text = f"Turn {'OFF' if is_auto else 'ON'} Renewal: {plan_name}"
                 callback = f"toggle_renew_{sub['plan_id']}_{'off' if is_auto else 'on'}"
                 keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback)])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def toggle_renewal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    
    # format: toggle_renew_PLANID_STATUS
    parts = data.split("_")
    plan_id = int(parts[2])
    action = parts[3] # 'on' or 'off'
    
    new_status = (action == 'on')
    
    db.set_subscription_auto_renew(user_id, plan_id, new_status)
    
    # Check if we need to sync with Stripe? 
    # For now, we just flag it locally. Ideally we cancel in Stripe if OFF.
    # But user asked to "choose to automatically renew or notify them"
    # If they choose notify, we disable auto_renew and just let expire/msg.
    
    if not new_status:
        # If turning off, we might want to cancel stripe sub at period end
        sub = db.get_subscription(user_id, plan_id)
        stripe_sub_id = sub.get('stripe_subscription_id')
        if stripe_sub_id:
            try:
                stripe.Subscription.modify(stripe_sub_id, cancel_at_period_end=True)
            except Exception as e:
                logger.error(f"Stripe cancel error: {e}")
    else:
        # Reactivate
        sub = db.get_subscription(user_id, plan_id)
        stripe_sub_id = sub.get('stripe_subscription_id')
        if stripe_sub_id:
             try:
                stripe.Subscription.modify(stripe_sub_id, cancel_at_period_end=False)
             except Exception as e:
                logger.error(f"Stripe reactivate error: {e}")

    # Refresh status
    await status_check(update, context)


async def manage_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if str(query.from_user.id) != str(ADMIN_ID): return
    
    groups = db.get_groups()
    if not groups:
         await query.edit_message_text("No groups added yet.", reply_markup=await get_admin_keyboard())
         return

    keyboard = []
    for g in groups:
        keyboard.append([InlineKeyboardButton(f"⚙️ {g['title']}", callback_data=f"setting_group_{g['channel_id']}")])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_admin")])
    await query.edit_message_text("Select a group to manage settings:", reply_markup=InlineKeyboardMarkup(keyboard))

async def group_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    channel_id = query.data.split("_", 2)[2]
    
    group = db.get_group(channel_id)
    join_status = "✅ ON" if group.get('delete_join_messages') else "❌ OFF"
    left_status = "✅ ON" if group.get('delete_left_messages') else "❌ OFF"
    
    text = (f"⚙️ Settings for **{group['title']}**\n\n"
            "Here you can toggle automatic deletion of service messages.")
            
    keyboard = [
        [InlineKeyboardButton(f"Delete Join Msgs: {join_status}", callback_data=f"toggle_join_{channel_id}")],
        [InlineKeyboardButton(f"Delete Left Msgs: {left_status}", callback_data=f"toggle_left_{channel_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_settings")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def toggle_group_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    parts = data.split("_")
    action = parts[1] # join or left
    channel_id = parts[2]
    
    group = db.get_group(channel_id)
    new_val = False
    
    if action == "join":
        new_val = not group.get('delete_join_messages', False)
        db.update_group_settings(channel_id, delete_join=new_val)
    elif action == "left":
        new_val = not group.get('delete_left_messages', False)
        db.update_group_settings(channel_id, delete_left=new_val)
        
    # Refresh menu
    # Modifying the query data so we can call group_settings_menu directly?
    # Better to just call it with constructed context or redirect
    query.data = f"setting_group_{channel_id}"
    await group_settings_menu(update, context)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if str(query.from_user.id) != str(ADMIN_ID):
        await query.edit_message_text("🚫 Unauthorized.", reply_markup=await get_main_menu_keyboard(query.from_user.id))
        return

    reply_markup = await get_admin_keyboard()
    await query.edit_message_text("⚙️ Admin Panel", reply_markup=reply_markup)

async def show_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if str(query.from_user.id) != str(ADMIN_ID):
        return

    stats = db.get_analytics()
    
    msg = (
        "📊 **Analytics Report**\n\n"
        f"👥 Total Subscribers: `{stats['total_subscribers_count']}`\n"
        f"🟢 Active Now: `{stats['active_subscribers']}`\n"
        f"💰 Total Revenue: `${stats['total_revenue_usd']:.2f}`"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_admin")]]
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# --- Add Plan Conversation ---

async def add_plan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📝 Enter the **Plan Name** (e.g., VIP, Gold):\n\nType 'cancel' to stop.",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data['last_bot_msg_id'] = query.message.message_id
    return PLAN_NAME

async def receive_plan_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    
    # Delete user's message to keep chat clean
    try:
        await update.message.delete()
    except:
        pass
    
    if user_text.lower() == 'cancel':
        return await cancel_add_plan(update, context)

    context.user_data['new_plan_name'] = user_text
    
    last_msg_id = context.user_data.get('last_bot_msg_id')
    
    try:
        # Prompt for Price
        msg = await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=last_msg_id,
            text=f"💲 Plan: **{user_text}**\nNow enter **Price in USD** (e.g. 10.50):",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['last_bot_msg_id'] = msg.message_id
    except:
        msg = await context.bot.send_message(chat_id, f"💲 Plan: **{user_text}**\nNow enter **Price in USD** (e.g. 10.50):", parse_mode=ParseMode.MARKDOWN)
        context.user_data['last_bot_msg_id'] = msg.message_id
        
    return PLAN_PRICE

async def receive_plan_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except:
        pass

    if user_text.lower() == 'cancel':
        return await cancel_add_plan(update, context)

    try:
        price = float(user_text)
        context.user_data['new_plan_price'] = price
        
        last_msg_id = context.user_data.get('last_bot_msg_id')
        msg = await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=last_msg_id,
            text=f"💲 Price: **${price}**\nNow enter **Duration in Days** (e.g. 30):",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['last_bot_msg_id'] = msg.message_id
        return PLAN_DURATION
    except ValueError:
        # If invalid, we can't easily edit to error and back without complex logic
        last_msg_id = context.user_data.get('last_bot_msg_id')
        try:
             await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_msg_id,
                text=f"❌ Invalid price. Try again.\n💲 Price: **${user_text}** (Invalid)\nEnter **Price in USD** (e.g. 10.50):",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
             pass
        return PLAN_PRICE

async def receive_plan_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except:
        pass

    if user_text.lower() == 'cancel':
        return await cancel_add_plan(update, context)

    try:
        days = int(user_text)
        context.user_data['new_plan_days'] = days
        
        last_msg_id = context.user_data.get('last_bot_msg_id')
        msg = await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=last_msg_id,
            text=f"📅 Duration: **{days} days**\nNow enter **Channel/Group ID** (e.g. -100123456789 or @mychannel):\n\n⚠️ **Important**: Add me as Admin to that channel FIRST!",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['last_bot_msg_id'] = msg.message_id
        return PLAN_CHANNEL
        
    except ValueError:
        last_msg_id = context.user_data.get('last_bot_msg_id')
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_msg_id,
                text="❌ Invalid duration. Please enter a whole number (e.g. 30):"
            )
        except:
            pass
        return PLAN_DURATION

async def receive_plan_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except:
        pass

    if user_text.lower() == 'cancel':
        return await cancel_add_plan(update, context)

    raw_channel_id = user_text.strip()
    verified_id = None
    group_title = None

    # Helper to check logic
    async def check_admin(chat_id):
        try:
             chat_member = await context.bot.get_chat_member(chat_id, context.bot.id)
             if chat_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                 chat_info = await context.bot.get_chat(chat_id)
                 return True, chat_info.title
             return False, None
        except:
             return False, None

    # 1. Try As Is
    is_admin, title = await check_admin(raw_channel_id)
    if is_admin:
        verified_id = raw_channel_id
        group_title = title
    
    # 2. Try adding -100 prefix if it's just digits
    elif raw_channel_id.lstrip('-').isdigit() and not raw_channel_id.startswith('-100'):
        alt_id = f"-100{raw_channel_id}"
        is_admin, title = await check_admin(alt_id)
        if is_admin:
            verified_id = alt_id
            group_title = title
            
    if not verified_id:
        last_msg_id = context.user_data.get('last_bot_msg_id')
        retry_text = (
            f"❌ **Error**: I cannot access `{raw_channel_id}` or I am not an Admin there.\n\n"
            "1. Add this bot to the channel/group as **Admin**.\n"
            "2. Make sure you entered the correct ID.\n"
            "   (If you copied a number like 12345, I already tried -10012345)\n"
            "3. Try again:"
        )
        try:
             await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_msg_id,
                text=retry_text,
                parse_mode=ParseMode.MARKDOWN
            )
        except:
             pass
        return PLAN_CHANNEL

    # Save Group Info to DB First
    db.add_group(verified_id, group_title)

    # Store channel ID and move to next step
    context.user_data['new_plan_channel'] = verified_id
    
    last_msg_id = context.user_data.get('last_bot_msg_id')
    
    kb = [
        [
            InlineKeyboardButton("One-Time Only", callback_data="recur_none")
        ],
        [
            InlineKeyboardButton("Monthly", callback_data="recur_month"),
            InlineKeyboardButton("Yearly", callback_data="recur_year")
        ]
    ]
    
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=last_msg_id,
        text=f"📢 Channel Verified: `{verified_id}`\n\n🔄 **Billing Cycle?**\nChoose how often users are charged:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN
    )
    return PLAN_RECURRING

async def receive_plan_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id
    last_msg_id = context.user_data.get('last_bot_msg_id')
    
    if data == "recur_cancel":
        return await cancel_add_plan(update, context)
        
    recurring_interval = None
    recurring_text = "One-Time"
    
    if data == "recur_month":
        recurring_interval = "month"
        recurring_text = "Monthly"
    elif data == "recur_year":
        recurring_interval = "year"
        recurring_text = "Yearly"
        
    # Finalize
    name = context.user_data['new_plan_name']
    price = context.user_data['new_plan_price']
    days = context.user_data['new_plan_days']
    channel_id = context.user_data['new_plan_channel']
    
    db.add_plan(name, int(price * 100), days, channel_id, recurring_interval=recurring_interval)
    
    keyboard = await get_admin_keyboard()
    
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=last_msg_id,
        text=f"✅ Plan Created!\n\n📌 **{name}**\n💰 ${price}\n📅 {days} days access\n🔄 Cycle: {recurring_text}\n📢 Channel: `{channel_id}`",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def cancel_add_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    last_msg_id = context.user_data.get('last_bot_msg_id')
    
    keyboard = await get_admin_keyboard()
    msg_text = "🚫 Plan creation cancelled."
    
    if last_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=last_msg_id,
                text=msg_text,
                reply_markup=keyboard
            )
        except:
            await context.bot.send_message(chat_id, msg_text, reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id, msg_text, reply_markup=keyboard)
        
    return ConversationHandler.END

# --- Payment Handlers ---

async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("buy_"):
        try:
            plan_id = int(data.split("_")[1])
            plan = db.get_plan(plan_id)

            if not plan:
                await query.edit_message_text("Plan not found.", reply_markup=await get_main_menu_keyboard(user_id))
                return

            # Check for existing subscription to THIS channel
            if db.is_user_subscribed_to_channel(user_id, plan['channel_id']):
                await query.answer("⚠️ You are already subscribed to this channel!", show_alert=True)
                return

            bot_username = context.bot.username
            success_url = f"https://t.me/{bot_username}?start=success"
            cancel_url = f"https://t.me/{bot_username}?start=cancel"
            
            # Determine payment mode (One-time vs Recurring)
            is_recurring = bool(plan.get('recurring_interval'))
            mode = 'subscription' if is_recurring else 'payment'
            
            price_data = {
                'currency': 'usd',
                'product_data': {'name': plan['name']},
                'unit_amount': plan['price_cents'],
            }
            
            if is_recurring:
                price_data['recurring'] = {'interval': plan['recurring_interval']}
            
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{'price_data': price_data, 'quantity': 1}],
                mode=mode,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={'user_id': user_id, 'plan_id': plan_id}
            )

            payment_oid = db.add_pending_payment(session.id, user_id, plan_id)

            # URL Button
            pay_btn = InlineKeyboardButton("💸 Pay Now", url=session.url)
            # We keep 'Check Status' just in case polling is slow
            check_btn = InlineKeyboardButton("🔄 Check Status", callback_data=f"verify_{payment_oid}")
            back_btn = InlineKeyboardButton("🔙 Cancel", callback_data="menu_subscribe")
            
            reply_markup = InlineKeyboardMarkup([[pay_btn], [check_btn], [back_btn]])

            await query.edit_message_text(
                f"💳 **Pay for {plan['name']}**\n\n"
                f"Click the button below to pay securely via Stripe.\n"
                "Once paid, the bot will automatically activate your subscription within a minute.\n\n"
                "ℹ️ **TEST MODE**: Use Card `4242 4242 4242 4242`",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )

        except Exception as e:
            logger.error(f"Stripe Error: {e}")
            await query.edit_message_text(f"Error creating payment link:\n{e}")

    elif data.startswith("verify_"):
        payment_oid = data.split("_")[1]
        pending = db.get_pending_payment_by_id(payment_oid)

        if not pending:
            await query.edit_message_text("Payment session expired or invalid.", reply_markup=await get_main_menu_keyboard(user_id))
            return

        session_id = pending['session_id']
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status == 'paid':
                plan = db.get_plan(pending['plan_id'])
                db.activate_subscription(pending['user_id'], pending['plan_id'])
                db.remove_pending_payment(session_id)
                
                await query.edit_message_text(
                    f"🎉 **Success!**\n\nYou are now subscribed to **{plan['name']}**.\nEnjoy!",
                    reply_markup=await get_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.answer("❌ Payment not received yet Use Test Card 4242...", show_alert=True)
                
        except Exception as e:
            logger.error(f"Verification Error: {e}")
            await query.answer("Error verifying payment.", show_alert=True)

# --- Chat Membership ---

async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    
    # Handle NEW Members
    if result.new_chat_member.status in [ChatMemberStatus.MEMBER] and \
       result.old_chat_member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        
        user_id = result.new_chat_member.user.id
        chat_id = update.effective_chat.id
        
        # Check settings for this group
        group_settings = db.get_group(chat_id)
        if group_settings:
            if group_settings.get("delete_join_messages"):
                 try:
                    await update.message.delete()
                 except:
                    pass

        # Check if this chat is protected by a plan
        # We need to find if ANY plan exists for this channel ID
        plans = db.get_plans(channel_id=chat_id)
        if not plans:
            return  # Open group?

        # Check subscription
        if not db.is_user_subscribed_to_channel(user_id, chat_id):
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.unban_chat_member(chat_id, user_id)
                try:
                    chat_info = await context.bot.get_chat(chat_id)
                    title = chat_info.title
                    await context.bot.send_message(user_id, f"🚫 You were removed from **{title}**.\nSubscription required.", parse_mode=ParseMode.MARKDOWN)
                    await context.bot.send_message(user_id, "Choose an option to subscribe:", reply_markup=await get_main_menu_keyboard(user_id))
                except:
                    pass
            except Exception as e:
                logger.error(f"Failed to kick user {user_id}: {e}")
        else:
            # Activate timer if pending
            # Find the specific pending plan for this user
            # db.start_subscription_timer_if_pending(user_id, chat_id)
            pass

    # Handle LEFT Members (Optional Delete Message)
    elif result.new_chat_member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
         chat_id = update.effective_chat.id
         # Telegram "User Left" is actually a service message, NOT a chat_member_update strictly speaking for the message deletion part.
         # The message update comes separately often.
         # But if we want to delete service messages, we need a MessageHandler for StatusUpdate.new_chat_members
         pass

async def handle_service_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes join/left messages if configured."""
    chat_id = update.effective_chat.id
    settings = db.get_group(chat_id)
    if not settings: return
    
    if update.message.new_chat_members and settings.get('delete_join_messages'):
        try: await update.message.delete()
        except: pass
    elif update.message.left_chat_member and settings.get('delete_left_messages'):
         try: await update.message.delete()
         except: pass

# --- Main App ---

if __name__ == '__main__':
    db.init_db()
    
    if not TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        exit(1)

    app = ApplicationBuilder().token(TOKEN).build()
    
    # Add JobQueue Task for Stripe Polling
    job_queue = app.job_queue
    # job_queue.run_repeating(check_stripe_payments, interval=15, first=5)  # Disabled in favor of Webhooks
    
    # Add JobQueue Task for Membership Validation (Every hour)
    job_queue.run_repeating(validate_members_job, interval=3600, first=30)

    # Conversation Handler for Adding Plans
    add_plan_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_plan_start, pattern='^admin_add_plan$')],
        states={
            PLAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_name)],
            PLAN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_price)],
            PLAN_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_duration)],
            PLAN_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_channel)],
            PLAN_RECURRING: [CallbackQueryHandler(receive_plan_recurring)],
        },
        fallbacks=[CommandHandler('cancel', cancel_add_plan)]
    )

    app.add_handler(add_plan_conv)
    app.add_handler(CommandHandler("start", start))
    
    # Navigation
    app.add_handler(CallbackQueryHandler(main_menu, pattern='^main_menu$'))
    app.add_handler(CallbackQueryHandler(subscribe_menu, pattern='^menu_subscribe$'))
    app.add_handler(CallbackQueryHandler(status_check, pattern='^menu_status$'))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern='^menu_admin$'))
    app.add_handler(CallbackQueryHandler(show_analytics, pattern='^admin_analytics$'))
    
    # Plan browsing
    app.add_handler(CallbackQueryHandler(show_group_plans, pattern='^groupplans_'))
    app.add_handler(CallbackQueryHandler(toggle_renewal, pattern='^toggle_renew_'))
    
    # Payment
    app.add_handler(CallbackQueryHandler(handle_payment, pattern='^(buy_|verify_)'))

    # Membership
    app.add_handler(ChatMemberHandler(chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    
    # Service Messages (Join/Left)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_service_messages))

    print("Bot is running...")
    app.run_polling()
