import asyncio
import hashlib
import json
import logging
import random
import re
import hmac
import time
from datetime import datetime
from aiohttp import web

import requests
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ============ CONFIG ============
TELEGRAM_TOKEN = "8760392511:AAEXiLXWXcy9ZTjs2wNSp6wDo_9RJYy4VDI"
ANTHROPIC_KEY = __import__('os').environ.get("ANTHROPIC_KEY", "")
SUPABASE_URL = "https://ephveuabosmvrwbnqpdn.supabase.co"
SUPABASE_KEY = "sb_publishable_YS2C4C5s4VyYKNmN-AMwaQ_Q_E6Ox0P"
STRIPE_PAYMENT_LINK = "https://buy.stripe.com/28EaEWdyq2dVemNecS1sQ00"
STRIPE_WEBHOOK_SECRET = "whsec_0FYVWpCFaVxnyZtN8Ag4LiVN0t0yqKFy"
STRIPE_PORTAL_URL = "https://billing.stripe.com/p/login/28EaEWdyq2dVemNecS1sQ00"
SCRAPER_API_KEY = "5099bc637688fdd9abf7db48c9fec7e9"
CHECK_INTERVAL_MIN = 40
CHECK_INTERVAL_MAX = 50
ADMIN_IDS = [8416016131]
WEBHOOK_PORT = 8080

BOT_INSTANCE = None

# ============ CITIES + CHANNELS ============
CITIES = {
    "adelaide": {
        "name": "Adelaide",
        "state": "SA",
        "postcode": "5000",
        "free": "@bpr_adelaide_free",
        "pro": "-1003773634232",
    },
    "perth": {
        "name": "Perth",
        "state": "WA",
        "postcode": "6000",
        "free": "@bpr_perth_free",
        "pro": "-1003805967218",
    },
    "brisbane": {
        "name": "Brisbane",
        "state": "QLD",
        "postcode": "4000",
        "free": "@bpr_brisbane_free",
        "pro": "-1003770760115",
    },
    "sydney": {
        "name": "Sydney",
        "state": "NSW",
        "postcode": "2000",
        "free": "@bpr_sydney_free",
        "pro": "-1003857190771",
    },
    "melbourne": {
        "name": "Melbourne",
        "state": "VIC",
        "postcode": "3000",
        "free": "@bpr_melbourne_free",
        "pro": "-1003830717489",
    },
}

REJECT_TITLE = [
    "senior manager", "general manager", "regional manager",
    "director", "ceo", "cfo", "cto", "vice president", "head of",
    "principal engineer", "principal analyst",
    "registered nurse", "enrolled nurse",
    "graduate program", "grad program", "traineeship", "apprentice",
    "software engineer", "software developer", "full stack engineer",
    "data engineer", "data scientist", "databricks",
    "civil engineer", "structural engineer", "mechanical engineer",
    "electrical engineer", "chemical engineer", "mining engineer",
    "lecturer", "professor", "teacher",
    "solicitor", "barrister", "legal counsel",
    "accountant", "management accountant", "financial controller",
    "dentist", "physiotherapist", "psychologist", "pharmacist",
    "occupational therapist", "speech pathologist",
    "architect", "urban planner",
]

REJECT_CATEGORY = [
    "nursing", "midwifery",
    "engineering - software", "civil/structural engineering",
    "mechanical engineering", "electrical/electronic engineering",
    "teaching - tertiary", "teaching - primary",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backpackradar")


def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def free_link(city_key):
    name = CITIES[city_key]["free"].replace("@", "")
    return "https://t.me/" + name


# ============ SUPABASE ============

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
        "Content-Type": "application/json",
    }


def get_user(telegram_id):
    url = SUPABASE_URL + "/rest/v1/users?telegram_id=eq." + str(telegram_id) + "&select=*"
    try:
        r = requests.get(url, headers=supabase_headers())
        data = r.json()
        if data and len(data) > 0:
            return data[0]
    except Exception:
        pass
    return None


def get_user_by_stripe_customer(customer_id):
    url = SUPABASE_URL + "/rest/v1/users?stripe_customer_id=eq." + str(customer_id) + "&select=*"
    try:
        r = requests.get(url, headers=supabase_headers())
        data = r.json()
        if data and len(data) > 0:
            return data[0]
    except Exception:
        pass
    return None


def create_user(telegram_id, username, city="", plan="free"):
    url = SUPABASE_URL + "/rest/v1/users"
    data = {
        "telegram_id": telegram_id,
        "username": username,
        "city": city,
        "plan": plan,
        "jobs_sent_today": 0,
        "created_at": datetime.utcnow().isoformat(),
    }
    headers = supabase_headers()
    headers["Prefer"] = "return=representation"
    try:
        r = requests.post(url, headers=headers, json=data)
        if r.status_code in [200, 201]:
            result = r.json()
            if result and len(result) > 0:
                return result[0]
    except Exception:
        pass
    return None


def update_user(telegram_id, updates):
    url = SUPABASE_URL + "/rest/v1/users?telegram_id=eq." + str(telegram_id)
    headers = supabase_headers()
    headers["Prefer"] = "return=minimal"
    try:
        requests.patch(url, headers=headers, json=updates)
    except Exception:
        pass


def reset_daily_counts():
    url = SUPABASE_URL + "/rest/v1/users?plan=eq.free"
    headers = supabase_headers()
    headers["Prefer"] = "return=minimal"
    try:
        requests.patch(url, headers=headers, json={"jobs_sent_today": 0})
    except Exception:
        pass


def job_exists(job_hash):
    url = SUPABASE_URL + "/rest/v1/jobs?job_hash=eq." + job_hash + "&select=id"
    try:
        r = requests.get(url, headers=supabase_headers())
        data = r.json()
        return len(data) > 0
    except Exception:
        return False


def save_job(job, city, requirements):
    url = SUPABASE_URL + "/rest/v1/jobs"
    headers = supabase_headers()
    headers["Prefer"] = "return=minimal"
    data = {
        "city": city,
        "title": job["title"],
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "sub_class": job.get("subClass", ""),
        "classification": job.get("classification", ""),
        "contract_type": job.get("contractType", ""),
        "salary": job.get("salary", ""),
        "link": job.get("link", ""),
        "full_text": job.get("fullText", ""),
        "requirements": json.dumps(requirements),
        "job_hash": make_hash(job, city),
        "created_at": datetime.utcnow().isoformat(),
    }
    try:
        r = requests.post(url, headers=headers, json=data)
        return r.status_code in [200, 201]
    except Exception:
        return False


def save_invite_link(telegram_id, city, invite_link):
    url = SUPABASE_URL + "/rest/v1/invite_links"
    headers = supabase_headers()
    headers["Prefer"] = "return=minimal"
    data = {
        "telegram_id": telegram_id,
        "city": city,
        "invite_link": invite_link,
        "created_at": datetime.utcnow().isoformat(),
    }
    try:
        requests.post(url, headers=headers, json=data)
    except Exception:
        pass


def get_invite_links(telegram_id):
    url = SUPABASE_URL + "/rest/v1/invite_links?telegram_id=eq." + str(telegram_id) + "&select=*"
    try:
        r = requests.get(url, headers=supabase_headers())
        return r.json()
    except Exception:
        return []


def delete_invite_links(telegram_id):
    url = SUPABASE_URL + "/rest/v1/invite_links?telegram_id=eq." + str(telegram_id)
    headers = supabase_headers()
    try:
        requests.delete(url, headers=headers)
    except Exception:
        pass


# ============ SEEK SCRAPING VIA SCRAPERAPI ============

def scrape_seek(city_key):
    jobs = []
    c = CITIES[city_key]
    city_slug = c["name"].replace(" ", "-")
    seek_url = "https://www.seek.com.au/jobs/in-" + city_slug + "-" + c["state"] + "-" + c["postcode"] + "?daterange=1&sortmode=ListedDate&distance=20"
    proxy_url = "http://api.scraperapi.com?api_key=" + SCRAPER_API_KEY + "&url=" + seek_url + "&render=true"
    log.info("Scraping " + city_key + " via ScraperAPI...")
    try:
        r = requests.get(proxy_url, timeout=90)
        log.info("ScraperAPI response for " + city_key + ": " + str(r.status_code) + " len=" + str(len(r.text)))
        if r.status_code != 200:
            return []
        html = r.text
        json_match = re.search(r'window\.SEEK_REDUX_DATA\s*=\s*(\{.+?\});', html, re.DOTALL)
        if json_match:
            try:
                redux = json.loads(json_match.group(1))
                job_list = redux.get("results", {}).get("results", {}).get("jobs", [])
                for item in job_list:
                    job = {
                        "title": item.get("title", ""),
                        "company": item.get("advertiser", {}).get("description", ""),
                        "location": item.get("location", ""),
                        "subClass": item.get("subClassification", {}).get("description", ""),
                        "classification": item.get("classification", {}).get("description", ""),
                        "contractType": item.get("workType", ""),
                        "salary": item.get("salary", ""),
                        "link": "https://www.seek.com.au/job/" + str(item.get("id", "")),
                        "fullText": item.get("teaser", ""),
                    }
                    if job["title"]:
                        jobs.append(job)
                log.info(city_key + ": found " + str(len(jobs)) + " jobs via REDUX")
                if jobs:
                    return jobs[:20]
            except Exception as e:
                log.warning("Redux parse failed: " + str(e))
        json_blobs = re.findall(r'\{"title":"[^"]+","id":\d+[^}]+\}', html)
        for blob in json_blobs:
            try:
                item = json.loads(blob)
                job = {
                    "title": item.get("title", ""),
                    "company": item.get("advertiser", {}).get("description", "") if isinstance(item.get("advertiser"), dict) else "",
                    "location": item.get("location", "") if isinstance(item.get("location"), str) else "",
                    "subClass": "",
                    "classification": "",
                    "contractType": item.get("workType", ""),
                    "salary": "",
                    "link": "https://www.seek.com.au/job/" + str(item.get("id", "")),
                    "fullText": item.get("teaser", ""),
                }
                if job["title"] and job["link"] != "https://www.seek.com.au/job/":
                    jobs.append(job)
            except Exception:
                pass
        if jobs:
            log.info(city_key + ": found " + str(len(jobs)) + " jobs via JSON blobs")
            return jobs[:20]
        title_matches = re.findall(r'<a[^>]*href="(/job/(\d+)[^"]*)"[^>]*>([^<]+)</a>', html)
        seen_ids = set()
        for href, job_id, title in title_matches:
            if job_id in seen_ids:
                continue
            if len(title) < 5:
                continue
            seen_ids.add(job_id)
            job = {
                "title": title.strip(),
                "company": "",
                "location": "",
                "subClass": "",
                "classification": "",
                "contractType": "",
                "salary": "",
                "link": "https://www.seek.com.au/job/" + job_id,
                "fullText": "",
            }
            jobs.append(job)
        log.info(city_key + ": found " + str(len(jobs)) + " jobs via HTML regex")
    except Exception as e:
        log.error("Scrape failed for " + city_key + ": " + str(e))
    return jobs[:20]


# ============ FILTERING ============

def make_hash(job, city):
    raw = city + "|" + job["title"] + "|" + job.get("company", "") + "|" + job.get("link", "")
    return hashlib.md5(raw.encode()).hexdigest()


def quick_reject(job):
    title_lower = job["title"].lower()
    sub_lower = job.get("subClass", "").lower()
    for kw in REJECT_TITLE:
        if kw in title_lower:
            return True
    for kw in REJECT_CATEGORY:
        if kw in sub_lower:
            return True
    return False


def analyze_with_ai(job):
    prompt_text = "I post jobs for backpackers on Working Holiday Visas in Australia. Should I post this one?\n\n"
    prompt_text += "A backpacker typically has:\n"
    prompt_text += "- No Australian qualifications\n"
    prompt_text += "- Maybe some experience from their home country\n"
    prompt_text += "- Basic to good English\n"
    prompt_text += "- WHV visa (can work max 6 months per employer)\n\n"
    prompt_text += "Say YES if a backpacker could realistically apply:\n"
    prompt_text += "- Hospitality: chef, cook, kitchen hand, barista, waiter, bar staff\n"
    prompt_text += "- Retail: shop assistant, cashier, store manager assistant\n"
    prompt_text += "- Labour: construction labourer, farm hand, warehouse, factory\n"
    prompt_text += "- Services: cleaner, housekeeper, driver, delivery, removalist\n"
    prompt_text += "- Entry admin: receptionist, data entry, office assistant\n"
    prompt_text += "- Trades helper: painter, labourer helper, scaffolder\n"
    prompt_text += "- Mining: entry level miner, operator, labourer on site\n"
    prompt_text += "- Tourism: tour guide, hotel staff, spa therapist\n"
    prompt_text += "- Childcare assistant, pet care, sports coach\n\n"
    prompt_text += "Say NO if:\n"
    prompt_text += "- Title contains Senior + a professional field\n"
    prompt_text += "- Requires Australian certification\n"
    prompt_text += "- Banking, finance professional, insurance underwriter\n"
    prompt_text += "- IT/Software professional role\n"
    prompt_text += "- Government role requiring citizenship\n"
    prompt_text += "- Executive or C-level position\n\n"
    prompt_text += "Title: " + job["title"] + "\n"
    prompt_text += "Category: " + job.get("subClass", "") + "\n"
    prompt_text += "Full text preview: " + job.get("fullText", "")[:200] + "\n\n"
    prompt_text += "One word YES or NO:"
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 30,
                "messages": [{"role": "user", "content": prompt_text}],
            },
            timeout=15,
        )
        data = r.json()
        result = data["content"][0]["text"].strip().upper()
        return "YES" in result
    except Exception as e:
        log.warning("AI error: " + str(e))
        return False


def detect_requirements(title, full_text):
    text = (title + " " + full_text).lower()
    reqs = []
    if "rsa" in text or "responsible service of alcohol" in text:
        reqs.append("RSA")
    if "white card" in text or "whitecard" in text:
        reqs.append("White Card")
    if "forklift" in text and ("licence" in text or "license" in text or "ticket" in text):
        reqs.append("Forklift licence")
    if "driver" in text and ("licence" in text or "license" in text):
        reqs.append("Permis")
    return reqs


# ============ MESSAGE FORMATTING (HTML) ============

def format_job_pro(job, city_name, requirements):
    ct = job.get("contractType", "")
    emoji = {"Full-time": "🟢", "Part-time": "🔵", "Casual": "🟡", "Contract": "🟠"}.get(ct, "💼")
    lines = []
    lines.append(emoji + " <b>" + html_escape(job["title"]) + "</b>")
    lines.append("🏢 " + html_escape(job.get("company", "N/A")))
    lines.append("📍 " + html_escape(city_name))
    if job.get("salary"):
        lines.append("💰 " + html_escape(job["salary"]))
    if ct:
        lines.append("📋 " + html_escape(ct))
    if requirements:
        lines.append("⚠️ " + html_escape(", ".join(requirements)))
    lines.append("")
    lines.append('🔗 <a href="' + job["link"] + '">Postuler ici</a>')
    return "\n".join(lines)


def format_job_free(job, city_name, requirements):
    ct = job.get("contractType", "")
    emoji = {"Full-time": "🟢", "Part-time": "🔵", "Casual": "🟡", "Contract": "🟠"}.get(ct, "💼")
    lines = []
    lines.append(emoji + " <b>" + html_escape(job["title"]) + "</b>")
    lines.append("🏢 " + html_escape(job.get("company", "N/A")))
    lines.append("📍 " + html_escape(city_name))
    if job.get("salary"):
        lines.append("💰 " + html_escape(job["salary"]))
    if ct:
        lines.append("📋 " + html_escape(ct))
    if requirements:
        lines.append("⚠️ " + html_escape(", ".join(requirements)))
    lines.append("")
    lines.append("Lien reserve aux membres Pro")
    lines.append("👉 @backpackradar_bot puis /premium")
    return "\n".join(lines)


# ============ ACTIVATE / DEACTIVATE ============

async def do_activate(bot, target_id, stripe_customer_id=None):
    user_data = get_user(target_id)
    if not user_data:
        log.warning("do_activate: user " + str(target_id) + " not found")
        return False
    updates = {"plan": "premium"}
    if stripe_customer_id:
        updates["stripe_customer_id"] = stripe_customer_id
    update_user(target_id, updates)
    log.info("do_activate: user " + str(target_id) + " -> premium")

    invite_links = []
    for city_key in CITIES:
        try:
            invite = await bot.create_chat_invite_link(chat_id=CITIES[city_key]["pro"], member_limit=1)
            invite_links.append(CITIES[city_key]["name"] + " : " + invite.invite_link)
            save_invite_link(target_id, city_key, invite.invite_link)
        except Exception as e:
            log.warning("Invite error " + city_key + ": " + str(e))
    if invite_links:
        msg = "🎉 <b>Ton compte est maintenant Pro !</b>\n\n"
        msg += "👉 Rejoins tes canaux Pro :\n\n"
        for link in invite_links:
            msg += link + "\n"
        msg += "\n📋 Gerer ton abonnement : " + STRIPE_PORTAL_URL
        msg += "\nMerci ! 🙏"
        try:
            await bot.send_message(target_id, msg, parse_mode="HTML")
        except Exception as e:
            log.warning("Failed to send activation msg: " + str(e))
    return True


async def do_deactivate(bot, target_id):
    user_data = get_user(target_id)
    if not user_data:
        return False
    update_user(target_id, {"plan": "free"})
    old_links = get_invite_links(target_id)
    for link_data in old_links:
        city_key = link_data.get("city", "")
        invite_link = link_data.get("invite_link", "")
        if city_key in CITIES:
            try:
                await bot.ban_chat_member(chat_id=CITIES[city_key]["pro"], user_id=target_id)
                await asyncio.sleep(1)
                await bot.unban_chat_member(chat_id=CITIES[city_key]["pro"], user_id=target_id)
            except Exception:
                pass
            try:
                await bot.revoke_chat_invite_link(chat_id=CITIES[city_key]["pro"], invite_link=invite_link)
            except Exception:
                pass
    delete_invite_links(target_id)

    keyboard = []
    row = []
    for key, city in CITIES.items():
        row.append(InlineKeyboardButton(city["name"], callback_data="city_" + key))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    payment_url = STRIPE_PAYMENT_LINK + "?client_reference_id=" + str(target_id)
    keyboard.append([InlineKeyboardButton("⭐ Se reabonner Pro", url=payment_url)])

    msg = "😔 <b>Ton abonnement Pro est termine.</b>\n\n"
    msg += "Tu as ete retire des canaux Pro.\n\n"
    msg += "Tu peux continuer a utiliser le canal gratuit de ta ville.\n"
    msg += "👇 Choisis ta ville gratuite ou reabonne-toi :"
    try:
        await bot.send_message(target_id, msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        log.warning("Failed to send deactivation msg: " + str(e))
    return True


# ============ TELEGRAM COMMANDS ============

async def cmd_start(update, context):
    user = update.effective_user
    existing = get_user(user.id)
    if existing:
        city_key = existing.get("city", "")
        city_info = CITIES.get(city_key, {})
        city_name = city_info.get("name", "Non definie")
        plan = existing.get("plan", "free")
        msg = "Content de te revoir ! 👋\n\n"
        msg += "Ta ville : <b>" + html_escape(city_name) + "</b>\n"
        msg += "Ton plan : <b>" + plan.upper() + "</b>\n\n"
        if city_key in CITIES:
            msg += "📢 Canal FREE : " + free_link(city_key) + "\n"
            if plan == "premium":
                msg += "⭐ Canaux PRO : acces via tes liens d'invitation\n"
                msg += "📋 Gerer ton abo : " + STRIPE_PORTAL_URL + "\n"
        msg += "\n/city - Changer de ville\n"
        msg += "/premium - Passer Pro\n"
        msg += "/status - Voir ton compte\n\n"
        msg += "Questions ? @Backpackradarapp"
        await update.message.reply_text(msg, parse_mode="HTML")
        return
    keyboard = []
    row = []
    for key, city in CITIES.items():
        row.append(InlineKeyboardButton(city["name"], callback_data="city_" + key))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    msg = "🎒 <b>Bienvenue sur BackpackRadar !</b>\n\n"
    msg += "Je trouve les meilleurs jobs WHV/PVT en Australie et je les poste dans des canaux Telegram par ville.\n\n"
    msg += "🆓 <b>Plan Gratuit</b> : Canal FREE de ta ville (offres sans lien)\n"
    msg += "⭐ <b>Plan Pro</b> ($9.99/mois) : Canal PRO avec liens directs pour postuler\n\n"
    msg += "Choisis ta ville pour commencer :"
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_city(update, context):
    keyboard = []
    row = []
    for key, city in CITIES.items():
        row.append(InlineKeyboardButton(city["name"], callback_data="city_" + key))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    await update.message.reply_text("📍 Choisis ta ville :", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_premium(update, context):
    user = update.effective_user
    existing = get_user(user.id)
    if existing and existing.get("plan") == "premium":
        msg = "Tu es deja Pro ! ✨\n\n"
        msg += "📋 Gerer ton abonnement (resilier, changer de carte) :\n"
        msg += STRIPE_PORTAL_URL
        await update.message.reply_text(msg)
        return
    payment_url = STRIPE_PAYMENT_LINK + "?client_reference_id=" + str(user.id)
    msg = "⭐ <b>BackpackRadar Pro</b>\n\n"
    msg += "✅ Toutes les offres WHV en temps reel\n"
    msg += "✅ Lien direct pour postuler en 1 clic\n"
    msg += "✅ Toutes les villes d'Australie\n\n"
    msg += "💰 <b>$9.99 AUD/mois</b>\n"
    keyboard = [[InlineKeyboardButton("💳 S'abonner", url=payment_url)]]
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_status(update, context):
    user_data = get_user(update.effective_user.id)
    if not user_data:
        await update.message.reply_text("Tape /start pour commencer !")
        return
    city_key = user_data.get("city", "")
    city_info = CITIES.get(city_key, {})
    city_name = city_info.get("name", "Non definie")
    plan = user_data.get("plan", "free").upper()
    msg = "📋 <b>Ton compte</b>\n\n"
    msg += "📍 Ville : <b>" + html_escape(city_name) + "</b>\n"
    msg += "💎 Plan : <b>" + plan + "</b>\n"
    if city_key in CITIES:
        msg += "📢 Canal FREE : " + free_link(city_key) + "\n"
    if user_data.get("plan") == "premium":
        msg += "⭐ Canaux PRO : acces via tes liens d'invitation\n"
        msg += "\n📋 Gerer / resilier ton abo :\n" + STRIPE_PORTAL_URL + "\n"
        await update.message.reply_text(msg, parse_mode="HTML")
    else:
        payment_url = STRIPE_PAYMENT_LINK + "?client_reference_id=" + str(update.effective_user.id)
        keyboard = [[InlineKeyboardButton("⭐ Passer Pro", url=payment_url)]]
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_help(update, context):
    msg = "🎒 <b>BackpackRadar</b>\n\n"
    msg += "/start - Accueil\n"
    msg += "/city - Changer de ville\n"
    msg += "/premium - Passer Pro\n"
    msg += "/status - Ton compte\n\n"
    msg += "Questions ? @Backpackradarapp"
    await update.message.reply_text(msg, parse_mode="HTML")


async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    if data.startswith("city_"):
        city_key = data.replace("city_", "")
        if city_key not in CITIES:
            await query.edit_message_text("Ville non reconnue.")
            return
        existing = get_user(user.id)
        if existing:
            update_user(user.id, {"city": city_key})
        else:
            create_user(user.id, user.username or str(user.id), city_key, "free")
        city = CITIES[city_key]
        msg = "✅ Parfait !\n\n"
        msg += "Tu recevras les offres WHV pour <b>" + html_escape(city["name"]) + "</b>.\n\n"
        msg += "⭐ Pour les liens directs : /premium"
        fl = free_link(city_key)
        keyboard = [[InlineKeyboardButton("👉 Rejoins le canal " + city["name"], url=fl)]]
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ============ ADMIN COMMANDS ============

async def cmd_activate(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("Usage: /activate <telegram_id>")
        return
    target_id = int(context.args[0])
    result = await do_activate(context.bot, target_id)
    if result:
        await update.message.reply_text("✅ " + str(target_id) + " active en Pro.")
    else:
        await update.message.reply_text("User pas trouve.")


async def cmd_deactivate(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("Usage: /deactivate <telegram_id>")
        return
    target_id = int(context.args[0])
    result = await do_deactivate(context.bot, target_id)
    if result:
        await update.message.reply_text(str(target_id) + " desactive.")
    else:
        await update.message.reply_text("User pas trouve.")


async def cmd_stats(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        url = SUPABASE_URL + "/rest/v1/users?select=plan"
        r = requests.get(url, headers=supabase_headers())
        users = r.json()
        total = len(users)
        premium = sum(1 for u in users if u.get("plan") == "premium")
        msg = "📊 <b>Stats</b>\n👥 " + str(total) + " users | ⭐ " + str(premium) + " pro | 💰 $" + str(int(premium * 9.99)) + "/mois"
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text("Erreur: " + str(e))


# ============ POST TO CHANNELS ============

async def post_job_to_channels(bot, job, city_key, requirements):
    city = CITIES[city_key]
    city_name = city["name"]
    try:
        pro_msg = format_job_pro(job, city_name, requirements)
        await bot.send_message(chat_id=city["pro"], text=pro_msg, parse_mode="HTML")
        log.info("Posted to PRO " + city_key)
    except Exception as e:
        log.warning("PRO post failed " + city_key + ": " + str(e))
    try:
        free_msg = format_job_free(job, city_name, requirements)
        await bot.send_message(chat_id=city["free"], text=free_msg, parse_mode="HTML")
        log.info("Posted to FREE " + city_key)
    except Exception as e:
        log.warning("FREE post failed " + city_key + ": " + str(e))
    await asyncio.sleep(0.5)


# ============ SCRAPING LOOP ============

async def scraping_loop(app):
    bot = app.bot
    cycle = 0
    last_reset = datetime.utcnow().date()
    log.info("Waiting 10s before first scrape...")
    await asyncio.sleep(10)
    log.info("=== SCRAPING LOOP STARTED ===")
    while True:
        cycle += 1
        now = datetime.utcnow()
        if now.date() > last_reset:
            reset_daily_counts()
            last_reset = now.date()
        log.info("=== CYCLE " + str(cycle) + " - " + now.strftime("%H:%M:%S UTC") + " ===")
        total_new = 0
        for city_key in CITIES:
            try:
                log.info("Starting scrape for " + city_key)
                jobs = scrape_seek(city_key)
                log.info(city_key + ": " + str(len(jobs)) + " jobs found")
                for job in jobs:
                    jh = make_hash(job, city_key)
                    if job_exists(jh):
                        continue
                    if quick_reject(job):
                        continue
                    if not analyze_with_ai(job):
                        continue
                    requirements = detect_requirements(job["title"], job.get("fullText", ""))
                    if save_job(job, city_key, requirements):
                        total_new += 1
                        log.info("NEW JOB: " + job["title"])
                        await post_job_to_channels(bot, job, city_key, requirements)
            except Exception as e:
                log.error("ERROR " + city_key + ": " + str(e))
            pause = random.randint(10, 20)
            log.info("Pause " + str(pause) + "s before next city")
            await asyncio.sleep(pause)
        log.info("Cycle " + str(cycle) + " done - " + str(total_new) + " new jobs")
        wait = random.randint(CHECK_INTERVAL_MIN * 60, CHECK_INTERVAL_MAX * 60)
        log.info("Next cycle in ~" + str(wait // 60) + " min")
        await asyncio.sleep(wait)


# ============ STRIPE WEBHOOK SERVER ============

async def stripe_webhook_handler(request):
    global BOT_INSTANCE
    payload = await request.read()
    sig_header = request.headers.get("Stripe-Signature", "")
    log.info("Stripe webhook received, payload length: " + str(len(payload)))

    try:
        elements = {}
        for element in sig_header.split(","):
            key, value = element.strip().split("=", 1)
            elements[key] = value
        timestamp = elements.get("t", "")
        signature = elements.get("v1", "")
        if not timestamp or not signature:
            log.warning("Stripe: missing t or v1")
            return web.Response(status=400, text="Bad signature")
        signed_payload = timestamp + "." + payload.decode("utf-8")
        expected_sig = hmac.new(
            STRIPE_WEBHOOK_SECRET.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            log.warning("Stripe: signature mismatch")
            return web.Response(status=400, text="Bad signature")
        log.info("Stripe: signature OK")
    except Exception as e:
        log.warning("Stripe: signature error: " + str(e))
        return web.Response(status=400, text="Signature error")

    try:
        event = json.loads(payload)
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    event_type = event.get("type", "")
    log.info("Stripe event: " + event_type)

    bot = BOT_INSTANCE
    if not bot:
        log.error("BOT_INSTANCE not set!")
        return web.Response(status=500, text="Bot not ready")

    if event_type == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        client_ref = session.get("client_reference_id", "")
        customer_id = session.get("customer", "")
        log.info("Checkout: ref=" + str(client_ref) + " customer=" + str(customer_id))
        if client_ref:
            try:
                target_id = int(client_ref)
                result = await do_activate(bot, target_id, stripe_customer_id=customer_id)
                log.info("Activation result: " + str(result))
            except Exception as e:
                log.error("Activation error: " + str(e))
        else:
            log.warning("No client_reference_id!")

    elif event_type == "customer.subscription.deleted":
        sub = event.get("data", {}).get("object", {})
        customer_id = sub.get("customer", "")
        log.info("Subscription deleted: customer=" + customer_id)
        if customer_id:
            user_data = get_user_by_stripe_customer(customer_id)
            if user_data:
                target_id = user_data["telegram_id"]
                log.info("Deactivating user " + str(target_id))
                try:
                    await do_deactivate(bot, target_id)
                    log.info("User " + str(target_id) + " deactivated OK")
                except Exception as e:
                    log.error("Deactivation error: " + str(e))
            else:
                log.warning("No user found for customer " + customer_id)

    return web.Response(status=200, text="OK")


async def health_check(request):
    return web.Response(status=200, text="BackpackRadar running")


async def start_webhook_server():
    app_web = web.Application()
    app_web.router.add_post("/stripe-webhook", stripe_webhook_handler)
    app_web.router.add_get("/", health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    log.info("Webhook server started on port " + str(WEBHOOK_PORT))


# ============ MAIN ============

async def post_init(app):
    global BOT_INSTANCE
    BOT_INSTANCE = app.bot
    log.info("BOT_INSTANCE set OK")
    asyncio.create_task(scraping_loop(app))
    log.info("Scraping task created")
    await start_webhook_server()
    log.info("Webhook server started")


def main():
    log.info("============================================")
    log.info("   BACKPACKRADAR - FULL AUTO STRIPE")
    log.info("============================================")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("city", cmd_city))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("deactivate", cmd_deactivate))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()