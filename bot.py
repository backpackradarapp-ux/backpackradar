import asyncio
import hashlib
import json
import logging
import random
import re
from datetime import datetime
from threading import Thread

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
ANTHROPIC_KEY = "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"
SUPABASE_URL = "https://ephveuabosmvrwbnqpdn.supabase.co"
SUPABASE_KEY = "sb_publishable_YS2C4C5s4VyYKNmN-AMwaQ_Q_E6Ox0P"
STRIPE_PAYMENT_LINK = "https://buy.stripe.com/28EaEWdyq2dVemNecS1sQ00"
CHECK_INTERVAL_MIN = 12
CHECK_INTERVAL_MAX = 18
ADMIN_IDS = [8416016131]

# ============ CITIES + CHANNELS ============
CITIES = {
    "adelaide": {
        "name": "Adelaide",
        "state": "SA",
        "postcode": "5000",
        "free": "@bpr_adelaide_free",
        "pro": "@bpr_adelaide_pro",
    },
    "perth": {
        "name": "Perth",
        "state": "WA",
        "postcode": "6000",
        "free": "@bpr_perth_free",
        "pro": "@bpr_perth_pro",
    },
    "brisbane": {
        "name": "Brisbane",
        "state": "QLD",
        "postcode": "4000",
        "free": "@bpr_brisbane_free",
        "pro": "@bpr_brisbane_pro",
    },
    "sydney": {
        "name": "Sydney",
        "state": "NSW",
        "postcode": "2000",
        "free": "@bpr_sydney_free",
        "pro": "@bpr_sydney_pro",
    },
    "melbourne": {
        "name": "Melbourne",
        "state": "VIC",
        "postcode": "3000",
        "free": "@bpr_melbourne_free",
        "pro": "@bpr_melbourne_pro",
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
        return None
    except Exception:
        return None


def create_user(telegram_id, username, city="", plan="free"):
    url = SUPABASE_URL + "/rest/v1/users"
    data = {
        "telegram_id": telegram_id,
        "username": username,
        "city": city,
        "plan": plan,
        "jobs_sent_today": 0,
        "last_job_date": datetime.utcnow().isoformat(),
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
        return None
    except Exception:
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
        return len(r.json()) > 0
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


# ============ SEEK SCRAPING ============

SEEK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}


def build_seek_api_url(city_key, page=1):
    c = CITIES[city_key]
    return "https://www.seek.com.au/api/chalice-search/v4/search?where=" + c["name"] + "+" + c["state"] + "+" + c["postcode"] + "&daterange=1&sortmode=ListedDate&distance=20&page=" + str(page) + "&pagesize=20"


def scrape_seek_api(city_key):
    jobs = []
    try:
        url = build_seek_api_url(city_key)
        r = requests.get(url, headers=SEEK_HEADERS, timeout=15)
        log.info("Seek response for " + city_key + ": " + str(r.status_code))
        if r.status_code == 200:
            data = r.json()
            for item in data.get("data", []):
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
    except Exception as e:
        log.warning("Seek API failed for " + city_key + ": " + str(e))
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


# ============ MESSAGE FORMATTING ============

def format_job_pro(job, city_name, requirements):
    ct = job.get("contractType", "")
    emoji = {"Full-time": "🟢", "Part-time": "🔵", "Casual": "🟡", "Contract": "🟠"}.get(ct, "💼")
    lines = []
    lines.append(emoji + " *" + job["title"] + "*")
    lines.append("🏢 " + job.get("company", "N/A"))
    lines.append("📍 " + city_name)
    if job.get("salary"):
        lines.append("💰 " + job["salary"])
    if ct:
        lines.append("📋 " + ct)
    if requirements:
        lines.append("⚠️ " + ", ".join(requirements))
    lines.append("")
    lines.append("🔗 [Postuler ici](" + job["link"] + ")")
    return "\n".join(lines)


def format_job_free(job, city_name, requirements):
    ct = job.get("contractType", "")
    emoji = {"Full-time": "🟢", "Part-time": "🔵", "Casual": "🟡", "Contract": "🟠"}.get(ct, "💼")
    lines = []
    lines.append(emoji + " *" + job["title"] + "*")
    lines.append("🏢 " + job.get("company", "N/A"))
    lines.append("📍 " + city_name)
    if job.get("salary"):
        lines.append("💰 " + job["salary"])
    if ct:
        lines.append("📋 " + ct)
    if requirements:
        lines.append("⚠️ " + ", ".join(requirements))
    lines.append("")
    lines.append("_⭐ Lien de candidature reserve aux membres Pro -> @backpackradar\\_bot_")
    return "\n".join(lines)


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
        msg += "Ta ville : *" + city_name + "*\n"
        msg += "Ton plan : *" + plan.upper() + "*\n\n"
        if city_key in CITIES:
            msg += "📢 Ton canal FREE : " + CITIES[city_key]["free"] + "\n"
            if plan == "premium":
                msg += "⭐ Ton canal PRO : " + CITIES[city_key]["pro"] + "\n"
        msg += "\n/city - Changer de ville\n"
        msg += "/premium - Passer Pro\n"
        msg += "/status - Voir ton compte"
        await update.message.reply_text(msg, parse_mode="Markdown")
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

    msg = "🎒 *Bienvenue sur BackpackRadar !*\n\n"
    msg += "Je trouve les meilleurs jobs WHV/PVT en Australie et je les poste dans des canaux Telegram par ville.\n\n"
    msg += "🆓 *Plan Gratuit* : 3 offres/jour dans le canal de ta ville (sans lien)\n"
    msg += "⭐ *Plan Pro* ($9.99/mois) : Toutes les offres + liens directs pour postuler\n\n"
    msg += "Choisis ta ville pour commencer :"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


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
        await update.message.reply_text("Tu es deja Pro ! ✨")
        return

    payment_url = STRIPE_PAYMENT_LINK + "?client_reference_id=" + str(user.id)

    msg = "⭐ *BackpackRadar Pro*\n\n"
    msg += "✅ Toutes les offres WHV en temps reel\n"
    msg += "✅ Lien direct pour postuler en 1 clic\n"
    msg += "✅ Toutes les villes d Australie\n\n"
    msg += "💰 *$9.99 AUD/mois*\n\n"

    keyboard = [[InlineKeyboardButton("💳 S abonner maintenant", url=payment_url)]]
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_status(update, context):
    user_data = get_user(update.effective_user.id)
    if not user_data:
        await update.message.reply_text("Tape /start pour commencer !")
        return
    city_key = user_data.get("city", "")
    city_info = CITIES.get(city_key, {})
    city_name = city_info.get("name", "Non definie")
    plan = user_data.get("plan", "free").upper()
    msg = "📋 *Ton compte*\n\n"
    msg += "📍 Ville : *" + city_name + "*\n"
    msg += "💎 Plan : *" + plan + "*\n"
    if city_key in CITIES:
        msg += "📢 Canal FREE : " + CITIES[city_key]["free"] + "\n"
        if user_data.get("plan") == "premium":
            msg += "⭐ Canal PRO : " + CITIES[city_key]["pro"] + "\n"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update, context):
    msg = "🎒 *BackpackRadar*\n\n"
    msg += "/start - Accueil\n"
    msg += "/city - Changer de ville\n"
    msg += "/premium - Passer Pro\n"
    msg += "/status - Ton compte"
    await update.message.reply_text(msg, parse_mode="Markdown")


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
        msg += "👉 Rejoins ton canal FREE : " + city["free"] + "\n\n"
        msg += "Tu y recevras 3 offres WHV par jour pour *" + city["name"] + "*.\n\n"
        msg += "⭐ Pour toutes les offres + liens directs :\n"
        msg += "/premium"
        await query.edit_message_text(msg, parse_mode="Markdown")


# ============ ADMIN COMMANDS ============

async def cmd_activate(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("Usage: /activate <telegram_id>")
        return
    target_id = int(context.args[0])
    user_data = get_user(target_id)
    if not user_data:
        await update.message.reply_text("User pas trouve.")
        return
    update_user(target_id, {"plan": "premium"})
    city_key = user_data.get("city", "")
    if city_key in CITIES:
        try:
            await context.bot.unban_chat_member(
                chat_id=CITIES[city_key]["pro"],
                user_id=target_id,
                only_if_banned=True
            )
        except Exception:
            pass
        pro_link = CITIES[city_key]["pro"]
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=pro_link,
                member_limit=1
            )
            msg = "🎉 *Ton compte est maintenant Pro !*\n\n"
            msg += "👉 Rejoins ton canal Pro : " + invite.invite_link + "\n\n"
            msg += "Merci ! 🙏"
            await context.bot.send_message(target_id, msg, parse_mode="Markdown")
        except Exception as e:
            await context.bot.send_message(target_id, "🎉 *Ton compte est Pro !*\nContacte @backpackradar\\_support pour le lien.", parse_mode="Markdown")
            log.warning("Could not create invite: " + str(e))
    await update.message.reply_text("✅ User " + str(target_id) + " active en Pro.")


async def cmd_deactivate(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("Usage: /deactivate <telegram_id>")
        return
    target_id = int(context.args[0])
    user_data = get_user(target_id)
    if not user_data:
        await update.message.reply_text("User pas trouve.")
        return
    update_user(target_id, {"plan": "free"})
    city_key = user_data.get("city", "")
    if city_key in CITIES:
        try:
            await context.bot.ban_chat_member(
                chat_id=CITIES[city_key]["pro"],
                user_id=target_id
            )
        except Exception:
            pass
    try:
        await context.bot.send_message(target_id, "Ton abonnement Pro a expire. /premium pour te re-abonner.")
    except Exception:
        pass
    await update.message.reply_text("User " + str(target_id) + " desactive.")


async def cmd_stats(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        url = SUPABASE_URL + "/rest/v1/users?select=plan"
        r = requests.get(url, headers=supabase_headers())
        users = r.json()
        total = len(users)
        premium = sum(1 for u in users if u.get("plan") == "premium")
        msg = "📊 *Stats*\n\n"
        msg += "👥 Total : " + str(total) + "\n"
        msg += "⭐ Pro : " + str(premium) + "\n"
        msg += "💰 ~$" + str(int(premium * 9.99)) + "/mois"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("Erreur: " + str(e))


# ============ POST TO CHANNELS ============

async def post_job_to_channels(bot, job, city_key, requirements):
    city = CITIES[city_key]
    city_name = city["name"]

    # Post to PRO channel (with link)
    try:
        pro_msg = format_job_pro(job, city_name, requirements)
        await bot.send_message(chat_id=city["pro"], text=pro_msg, parse_mode="Markdown")
    except Exception as e:
        log.warning("Failed to post to PRO " + city_key + ": " + str(e))

    # Post to FREE channel (without link, max 3/day tracked in channel)
    try:
        free_msg = format_job_free(job, city_name, requirements)
        await bot.send_message(chat_id=city["free"], text=free_msg, parse_mode="Markdown")
    except Exception as e:
        log.warning("Failed to post to FREE " + city_key + ": " + str(e))

    await asyncio.sleep(0.5)


# ============ SCRAPING LOOP ============

async def scraping_loop(app):
    bot = app.bot
    cycle = 0
    last_reset = datetime.utcnow().date()

    log.info("Waiting 10s before starting scrape loop...")
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
                jobs = scrape_seek_api(city_key)
                log.info(CITIES[city_key]["name"] + ": " + str(len(jobs)) + " jobs found")

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
                        log.info("OK: " + job["title"])
                        await post_job_to_channels(bot, job, city_key, requirements)
            except Exception as e:
                log.error("ERROR " + city_key + ": " + str(e))
            await asyncio.sleep(random.randint(5, 15))

        log.info("Cycle " + str(cycle) + " done - " + str(total_new) + " new jobs")
        wait = random.randint(CHECK_INTERVAL_MIN * 60, CHECK_INTERVAL_MAX * 60)
        log.info("Next cycle in ~" + str(wait // 60) + " min")
        await asyncio.sleep(wait)


# ============ MAIN ============

async def post_init(app):
    asyncio.create_task(scraping_loop(app))
    log.info("Scraping task created")


def main():
    log.info("============================================")
    log.info("   BACKPACKRADAR v2 - Channels + Stripe")
    log.info("   5 cities | AI Filter | Freemium")
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