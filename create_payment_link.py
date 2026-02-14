import stripe
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set your Stripe API key
stripe.api_key = os.getenv("STRIPE_API_KEY")

def create_checkout_session():
    try:
        # Create a checkout session
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Premium Subscription',
                    },
                    'unit_amount': 2000,  # Amount in cents (2000 = $20.00)
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url='https://example.com/success',
            cancel_url='https://example.com/cancel',
        )
        
        print("Payment URL created successfully:")
        print(session.url)
        return session.url
        
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

if __name__ == "__main__":
    if not stripe.api_key:
        print("Error: STRIPE_API_KEY not found in environment variables.")
        print("Please create a .env file with your Stripe secret key.")
    else:
        create_checkout_session()
