import os
import time
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv
from bson.objectid import ObjectId

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "telegram_sub_bot"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# Collections
plans_collection = db['plans']
groups_collection = db['groups']
subscriptions_collection = db['subscriptions']
pending_payments_collection = db['pending_payments']

# Optional: Add a transactions collection to track history
transactions_collection = db['transactions']

def init_db():
    # MongoDB creates collections lazily.
    # Allow users to have multiple subscriptions (one per plan)
    subscriptions_collection.create_index([("user_id", 1), ("plan_id", 1)], unique=True)
    pending_payments_collection.create_index("session_id", unique=True)
    groups_collection.create_index("channel_id", unique=True)

# --- Group/Channel Management ---

def add_group(channel_id, title):
    group = {
        "channel_id": channel_id,
        "title": title,
        "delete_join_messages": False,
        "delete_left_messages": False,
        "created_at": time.time()
    }
    groups_collection.update_one(
        {"channel_id": channel_id},
        {"$set": group},
        upsert=True
    )
    return group

def get_groups():
    return list(groups_collection.find({}, {'_id': 0}))

def get_group(channel_id):
    try:
        cid = int(channel_id)
        return groups_collection.find_one({"channel_id": cid}, {'_id': 0})
    except:
        return groups_collection.find_one({"channel_id": channel_id}, {'_id': 0})

def update_group_settings(channel_id, delete_join=None, delete_left=None):
    update_data = {}
    if delete_join is not None:
        update_data["delete_join_messages"] = delete_join
    if delete_left is not None:
        update_data["delete_left_messages"] = delete_left
        
    if update_data:
        try:
             cid = int(channel_id)
             groups_collection.update_one({"channel_id": cid}, {"$set": update_data})
        except:
             groups_collection.update_one({"channel_id": channel_id}, {"$set": update_data})

# --- Plan Management ---

def get_next_plan_id():
    last_plan = plans_collection.find_one(sort=[("id", -1)])
    if last_plan:
        return last_plan['id'] + 1
    return 1

def add_plan(name, price_cents, duration_days, channel_id, description="", recurring_interval=None):
    # Ensure group exists first (or we can add it lazily, but structure implies group first)
    # But for backward compatibility we keep loose coupling or ensure add_group called
    
    plan_id = get_next_plan_id()
    plan = {
        "id": plan_id,
        "name": name,
        "price_cents": price_cents,
        "duration_days": duration_days,
        "channel_id": channel_id,
        "description": description[:1000],
        "recurring_interval": recurring_interval,
        "active": True,
        "created_at": time.time()
    }
    plans_collection.insert_one(plan)
    return plan_id

def get_plans(channel_id=None):
    query = {"active": True}
    if channel_id:
        # Handle int/str mismatch. The DB might store it as int or string.
        # We need to check both or standardize.
        # Let's try OR query for robustness if input is string that looks like int
        try:
             cid_int = int(channel_id)
             cid_str = str(channel_id)
             query["channel_id"] = {"$in": [cid_int, cid_str]}
        except:
             query["channel_id"] = channel_id
            
    return list(plans_collection.find(query, {'_id': 0}))

def get_plan(plan_id):
    try:
        return plans_collection.find_one({"id": int(plan_id)}, {'_id': 0})
    except:
        return None

def get_plan_by_channel(channel_id):
    # Try finding any plan for this channel
    try:
        cid_int = int(channel_id)
        cid_str = str(channel_id)
        return plans_collection.find_one({"channel_id": {"$in": [cid_int, cid_str]}}, {'_id': 0})
    except:
        return plans_collection.find_one({"channel_id": channel_id}, {'_id': 0})

def add_pending_payment(session_id, user_id, plan_id):
    payment = {
        "session_id": session_id,
        "user_id": user_id,
        "plan_id": plan_id,
        "created_at": time.time()
    }
    res = pending_payments_collection.insert_one(payment)
    return str(res.inserted_id)

def get_pending_payment(session_id):
    return pending_payments_collection.find_one({"session_id": session_id}, {'_id': 0})

def get_pending_payment_by_id(payment_id):
    try:
        return pending_payments_collection.find_one({"_id": ObjectId(payment_id)})
    except:
        return None

def get_all_pending_payments():
    return list(pending_payments_collection.find({}, {'_id': 0, 'session_id': 1, 'user_id': 1, 'plan_id': 1}))

def set_subscription_auto_renew(user_id, plan_id, status):
    """
    Enable or disable auto-renew for a subscription.
    """
    subscriptions_collection.update_one(
        {"user_id": user_id, "plan_id": int(plan_id)},
        {"$set": {"auto_renew": status}}
    )

def activate_subscription(user_id, plan_id, stripe_sub_id=None):
    """
    Called after payment. Sets status to 'pending_join'.
    The timer will only start when they actually join.
    params:
    stripe_sub_id: Optional ID of the Stripe subscription if recurring.
    """
    
    update_data = {
        "status": "pending_join",
        "start_timestamp": None,
        "expiry_timestamp": None,
        "updated_at": time.time()
    }
    
    if stripe_sub_id:
        update_data["stripe_subscription_id"] = stripe_sub_id
        update_data["auto_renew"] = True
    
    # Upsert subscription for specific plan
    subscriptions_collection.update_one(
        {"user_id": user_id, "plan_id": plan_id},
        {"$set": update_data},
        upsert=True
    )

    # Log transaction for analytics
    plan = get_plan(plan_id)
    if plan:
        transactions_collection.insert_one({
            "user_id": user_id,
            "plan_id": plan_id,
            "amount_cents": plan['price_cents'],
            "timestamp": time.time()
        })

def start_subscription_timer(user_id, plan_id):
    """
    Called when user actually joins the channel.
    """
    plan = get_plan(plan_id)
    if not plan: return
    
    duration_days = plan['duration_days']
    start_time = time.time()
    expiry = start_time + (duration_days * 24 * 60 * 60)
    
    subscriptions_collection.update_one(
        {"user_id": user_id, "plan_id": plan_id},
        {"$set": {
            "status": "active",
            "start_timestamp": start_time,
            "expiry_timestamp": expiry,
            "updated_at": time.time()
        }}
    )

def extend_subscription_by_stripe_id(stripe_sub_id, duration_days):
    """
    Extends an existing subscription by adding duration_days.
    Works for recurring billing renewal.
    """
    sub = subscriptions_collection.find_one({"stripe_subscription_id": stripe_sub_id})
    if not sub:
        return False
        
    current_expiry = sub.get("expiry_timestamp") or time.time()
    # If already pending_join, we don't need to extend expiry yet, 
    # but for recurring payments usually the user is already active.
    # If expired, start from now. If active, extend from current expiry.
    
    new_start = max(current_expiry, time.time())
    new_expiry = new_start + (duration_days * 86400)
    
    subscriptions_collection.update_one(
        {"_id": sub["_id"]},
        {"$set": {
            "status": "active",
            "expiry_timestamp": new_expiry,
            "updated_at": time.time()
        }}
    )
    return sub['user_id']

def is_user_subscribed_to_channel(user_id, channel_id):
    # Determine the channel ID format used in plans
    possible_channel_ids = [channel_id]
    try:
        possible_channel_ids = list(set([str(channel_id), int(channel_id)]))
    except:
        pass
    
    # Get all plans from DB that match any of these channel IDs
    plans = list(plans_collection.find({"channel_id": {"$in": possible_channel_ids}}, {"id": 1}))
    plan_ids = [p['id'] for p in plans]
    
    if not plan_ids: 
        return False
        
    # Query subscriptions for this user against ANY of these plan IDs
    sub_query = {
        "user_id": user_id,
        "plan_id": {"$in": plan_ids},
        "status": {"$in": ["pending_join", "active"]}
    }
    
    subs = list(subscriptions_collection.find(sub_query))
    
    for sub in subs:
        if sub['status'] == 'pending_join': return True
        # Check expiry for active ones
        if sub.get('expiry_timestamp') and sub['expiry_timestamp'] > time.time():
            return True
            
    return False

def get_expired_active_subscriptions():
    return list(subscriptions_collection.find({
        "status": "active",
        "expiry_timestamp": {"$lt": time.time()} 
    }))

def is_user_subscribed(user_id, plan_id=None):
    """
    Checks if a user has a valid subscription (either Active or Pending Join).
    If plan_id is provided, checks specific plan.
    Otherwise checks if ANY subscription is valid.
    """
    query = {"user_id": user_id}
    if plan_id:
        query["plan_id"] = int(plan_id)
    
    # We need to find ONE valid subscription
    # A subscription is valid if:
    # 1. status is 'pending_join'
    # OR
    # 2. expiry_timestamp > now
    
    active_subs = list(subscriptions_collection.find(query))
    
    for sub in active_subs:
        if sub.get('status') == 'pending_join':
            return True
        # Check expiry
        expiry = sub.get('expiry_timestamp')
        if expiry and expiry > time.time():
            return True
            
    return False

def remove_pending_payment(session_id):
    pending_payments_collection.delete_one({"session_id": session_id})

def get_subscription(user_id, plan_id=None):
    query = {"user_id": user_id}
    if plan_id:
        query["plan_id"] = int(plan_id)
    return subscriptions_collection.find_one(query, {'_id': 0})

def get_user_subscriptions(user_id):
    """
    Get all active/pending subscriptions for a user.
    """
    subs = list(subscriptions_collection.find({"user_id": user_id}))
    valid_subs = []
    
    # Filter to show only relevant ones (Active, Pending Join, or Recently Expired)
    for sub in subs:
        if sub.get('status') in ['active', 'pending_join', 'expired']: # Don't show revoked?
             valid_subs.append(sub)
             
    return valid_subs

def revoke_subscription(user_id, plan_id):
    subscriptions_collection.update_one(
        {"user_id": user_id, "plan_id": int(plan_id)},
        {"$set": {"status": "revoked", "expiry_timestamp": 0}}
    )

def is_invite_used(invite_code):
    return subscriptions_collection.find_one({"invite_code": invite_code, "status": "active"})

def save_invite_code(user_id, plan_id, invite_code):
    subscriptions_collection.update_one(
        {"user_id": user_id, "plan_id": int(plan_id)},
        {"$set": {"invite_code": invite_code}}
    )

def get_subscription_by_invite(invite_code):
     return subscriptions_collection.find_one({"invite_code": invite_code})

# --- Analytics Functions ---

# --- Analytics Functions ---

def get_analytics():
    total_users = subscriptions_collection.count_documents({}) # Approx total unique subscribers ever
    active_now = subscriptions_collection.count_documents({"expiry_timestamp": {"$gt": time.time()}})
    
    # Revenue from transactions history
    pipeline = [
        {"$group": {"_id": None, "total": {"$sum": "$amount_cents"}}}
    ]
    res = list(transactions_collection.aggregate(pipeline))
    total_revenue = res[0]['total'] if res else 0
    
    return {
        "total_subscribers_count": total_users,
        "active_subscribers": active_now,
        "total_revenue_usd": total_revenue / 100
    }
