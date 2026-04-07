import asyncio
import hashlib
import json
import logging
import random
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
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

TELEGRAM_TOKEN = "8760392511:AAEXiLXWXcy9ZTjs2wNSp6wDo_9RJYy4VDI"
ANTHROPIC_KEY = "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"
SUPABASE_URL = "https://ephveuabosmvrwbnqpdn.supabase.co"
SUPABASE_KEY = "sb_publishable_YS2C4C5s4VyYKNmN-AMwaQ_Q_E6Ox0P"
CHECK_INTERVAL_MIN = 12
CHECK_INTERVAL_MAX = 18
ADMIN_IDS = [8416016131]

CITIES = {
    "adelaide": {"name": "Adelaide", "state": "SA", "postcode": "5000"},
    "perth": {"name": "Perth", "state": "WA", "postcode": "6000"},
    "brisbane": {"name": "Brisbane", "state": "QLD", "postcode": "4000"},
    "cairns": {"name": "Cairns", "state": "QLD", "postcode": "4870"},
    "sydney": {"name": "Sydney", "state": "NSW", "postcode": "2000"},
    "melbourne": {"name": "Melbourne", "state": "VIC", "postcode": "3000"},
    "goldcoast": {"name": "Gold Coast", "state": "QLD", "postcode": "4217"},
    "darwin": {"name": "Darwin", "state": "NT", "postcode": "0800"},
    "hobart": {"name": "Hobart", "state": "TAS", "postcode": "7000"},
    "canberra": {"name": "Canberra", "state": "ACT", "postcode": "2600"},
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


def get_users_by_city(city):
    url = SUPABASE_URL + "/rest/v1/users?city=eq." + city + "&select=*"
    try:
        r = requests.get(url, headers=supabase_headers())
        return r.json()
    except Exception:
        return []


def get_premium_users():
    url = SUPABASE_URL + "/rest/v1/users?plan=eq.premium&select=*"
    try:
        r = requests.get(url, headers=supabase_headers())
        return r.json()
    except Exception:
        return []


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


def format_job_message(job, city_name, requirements):
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


def format_job_teaser(job, city_name):
    msg = "💼 *" + job["title"] + "*\n"
    msg += "🏢 " + job.get("company", "N/A") + " - " + city_name + "\n\n"
    msg += "_⭐ Passe en Pro pour voir toutes les offres -> /premium_"
    return msg


async def cmd_start(update, context):
    user = update.effective_user
    existing = get_user(user.id)
    if existing:
        city_info = CITIES.get(existing.get("city", ""), {})
        city_name = city_info.get("name", "Non definie")
        msg = "Content de te revoir ! 👋\n\n"
        msg += "Ta ville : *" + city_name + "*\n"
        msg += "Ton plan : *" + existing.get("plan", "free").upper() + "*\n\n"
        msg += "/city - Changer de ville\n"
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
    msg += "Je trouve les meilleurs jobs WHV/PVT en Australie pour toi.\n\n"
    msg += "🆓 *Plan Gratuit* : 3 offres/jour pour 1 ville\n"
    msg += "⭐ *Plan Pro* : Toutes les offres + toutes les villes ($9.99/mois)\n\n"
    msg += "Choisis ta ville :"
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
    msg = "⭐ *BackpackRadar Pro*\n\n"
    msg += "✅ Toutes les offres WHV en temps reel\n"
    msg += "✅ Lien direct pour postuler\n"
    msg += "✅ Toutes les villes d Australie\n\n"
    msg += "💰 *$9.99 AUD/mois*\n\n"
    msg += "Contacte @backpackradar\\_support pour t abonner"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_status(update, context):
    user_data = get_user(update.effective_user.id)
    if not user_data:
        await update.message.reply_text("Tape /start pour commencer !")
        return
    city_info = CITIES.get(user_data.get("city", ""), {})
    city_name = city_info.get("name", "Non definie")
    plan = user_data.get("plan", "free").upper()
    sent = user_data.get("jobs_sent_today", 0)
    msg = "📋 *Ton compte*\n\n"
    msg += "📍 Ville : *" + city_name + "*\n"
    msg += "💎 Plan : *" + plan + "*\n"
    if user_data.get("plan", "free") == "free":
        remaining = max(0, 3 - sent)
        msg += "📊 Offres restantes : *" + str(remaining) + "/3*"
    else:
        msg += "📊 Offres : *Illimitees* ✨"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update, context):
    msg = "🎒 *BackpackRadar*\n\n"
    msg += "/start - Accueil\n"
    msg += "/city - Changer de ville\n"
    msg += "/premium - Infos Pro\n"
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
        city_name = CITIES[city_key]["name"]
        msg = "✅ Tu recevras les offres WHV pour *" + city_name + "*.\n\n"
        msg += "⭐ /premium pour toutes les villes !"
        await query.edit_message_text(msg, parse_mode="Markdown")


async def cmd_activate(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("Usage: /activate <telegram_id>")
        return
    target_id = int(context.args[0])
    update_user(target_id, {"plan": "premium"})
    await update.message.reply_text("✅ User " + str(target_id) + " active en Pro.")
    try:
        await context.bot.send_message(target_id, "🎉 *Ton compte est Pro !*\nMerci ! 🙏", parse_mode="Markdown")
    except Exception:
        pass


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
        msg += "⭐ Premium : " + str(premium) + "\n"
        msg += "💰 ~$" + str(int(premium * 9.99)) + "/mois"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text("Erreur: " + str(e))


async def send_job_to_users(bot, job, city_key, requirements):
    city_name = CITIES[city_key]["name"]
    already_sent = set()
    for user_data in get_premium_users():
        try:
            tid = user_data["telegram_id"]
            already_sent.add(tid)
            await bot.send_message(tid, format_job_message(job, city_name, requirements), parse_mode="Markdown")
        except Exception:
            pass
    for user_data in get_users_by_city(city_key):
        try:
            tid = user_data["telegram_id"]
            if tid in already_sent:
                continue
            if user_data.get("plan", "free") != "free":
                continue
            sent_today = user_data.get("jobs_sent_today", 0)
            if sent_today < 3:
                await bot.send_message(tid, format_job_teaser(job, city_name), parse_mode="Markdown")
                update_user(tid, {"jobs_sent_today": sent_today + 1})
        except Exception:
            pass
    await asyncio.sleep(0.5)


async def scraping_loop(app):
    bot = app.bot
    cycle = 0
    last_reset = datetime.utcnow().date()
    await asyncio.sleep(5)
    log.info("Scraping loop started")
    while True:
        cycle += 1
        now = datetime.utcnow()
        if now.date() > last_reset:
            reset_daily_counts()
            last_reset = now.date()
        log.info("=== CYCLE " + str(cycle) + " ===")
        total_new = 0
        for city_key in CITIES:
            try:
                jobs = scrape_seek_api(city_key)
                log.info(CITIES[city_key]["name"] + ": " + str(len(jobs)) + " jobs")
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
                        log.info("OK " + job["title"])
                        await send_job_to_users(bot, job, city_key, requirements)
            except Exception as e:
                log.error("ERROR " + city_key + ": " + str(e))
            await asyncio.sleep(random.randint(5, 15))
        log.info("Cycle done - " + str(total_new) + " new")
        wait = random.randint(CHECK_INTERVAL_MIN * 60, CHECK_INTERVAL_MAX * 60)
        await asyncio.sleep(wait)


def main():
    log.info("BACKPACKRADAR starting")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("city", cmd_city))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(callback_handler))
    loop = asyncio.get_event_loop()
    loop.create_task(scraping_loop(app))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()