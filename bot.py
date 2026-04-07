"""
BACKPACKRADAR — Telegram Bot + Scraper
Tourne 24/7 sur Railway/Render
Scrape Seek sans Playwright (requests + BS4)
Système freemium : 1 offre/jour gratuit, illimité payant
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
from datetime import datetime, timedelta

import anthropic
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

# === CONFIGURATION (variables d’environnement) ===

TELEGRAM_TOKEN = os.environ[“TELEGRAM_TOKEN”]
ANTHROPIC_KEY = os.environ[“ANTHROPIC_KEY”]
SUPABASE_URL = os.environ[“SUPABASE_URL”]
SUPABASE_KEY = os.environ[“SUPABASE_KEY”]

# Canal principal (ton canal public)

MAIN_CHANNEL_ID = os.environ.get(“MAIN_CHANNEL_ID”, “”)

# Intervalle de scraping (minutes)

CHECK_INTERVAL_MIN = int(os.environ.get(“CHECK_INTERVAL_MIN”, “12”))
CHECK_INTERVAL_MAX = int(os.environ.get(“CHECK_INTERVAL_MAX”, “18”))

# === VILLES ===

CITIES = {
“adelaide”: {“name”: “Adelaide”, “state”: “SA”, “postcode”: “5000”},
“perth”: {“name”: “Perth”, “state”: “WA”, “postcode”: “6000”},
“brisbane”: {“name”: “Brisbane”, “state”: “QLD”, “postcode”: “4000”},
“cairns”: {“name”: “Cairns”, “state”: “QLD”, “postcode”: “4870”},
“sydney”: {“name”: “Sydney”, “state”: “NSW”, “postcode”: “2000”},
“melbourne”: {“name”: “Melbourne”, “state”: “VIC”, “postcode”: “3000”},
“goldcoast”: {“name”: “Gold Coast”, “state”: “QLD”, “postcode”: “4217”},
“darwin”: {“name”: “Darwin”, “state”: “NT”, “postcode”: “0800”},
“hobart”: {“name”: “Hobart”, “state”: “TAS”, “postcode”: “7000”},
“canberra”: {“name”: “Canberra”, “state”: “ACT”, “postcode”: “2600”},
}

# === FILTRES (identiques à ton code) ===

REJECT_TITLE = [
“senior manager”, “general manager”, “regional manager”,
“director”, “ceo”, “cfo”, “cto”, “vice president”, “head of”,
“principal engineer”, “principal analyst”,
“registered nurse”, “enrolled nurse”,
“graduate program”, “grad program”, “traineeship”, “apprentice”,
“software engineer”, “software developer”, “full stack engineer”,
“data engineer”, “data scientist”, “databricks”,
“civil engineer”, “structural engineer”, “mechanical engineer”,
“electrical engineer”, “chemical engineer”, “mining engineer”,
“lecturer”, “professor”, “teacher”,
“solicitor”, “barrister”, “legal counsel”,
“accountant”, “management accountant”, “financial controller”,
“dentist”, “physiotherapist”, “psychologist”, “pharmacist”,
“occupational therapist”, “speech pathologist”,
“architect”, “urban planner”,
]

REJECT_CATEGORY = [
“nursing”, “midwifery”,
“engineering - software”, “civil/structural engineering”,
“mechanical engineering”, “electrical/electronic engineering”,
“teaching - tertiary”, “teaching - primary”,
]

# === LOGGING ===

logging.basicConfig(
level=logging.INFO,
format=”%(asctime)s [%(levelname)s] %(message)s”,
)
log = logging.getLogger(“backpackradar”)

# === CLIENT IA ===

ai_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# =============================================

# SUPABASE — Gestion utilisateurs + jobs

# =============================================

def supabase_headers():
return {
“apikey”: SUPABASE_KEY,
“Authorization”: f”Bearer {SUPABASE_KEY}”,
“Content-Type”: “application/json”,
}

def get_user(telegram_id: int) -> dict | None:
“”“Récupère un utilisateur depuis Supabase.”””
url = f”{SUPABASE_URL}/rest/v1/users?telegram_id=eq.{telegram_id}&select=*”
try:
r = requests.get(url, headers=supabase_headers())
data = r.json()
return data[0] if data else None
except Exception:
return None

def create_user(telegram_id: int, username: str, city: str = “”, plan: str = “free”):
“”“Crée un utilisateur dans Supabase.”””
url = f”{SUPABASE_URL}/rest/v1/users”
data = {
“telegram_id”: telegram_id,
“username”: username,
“city”: city,
“plan”: plan,
“jobs_sent_today”: 0,
“last_job_date”: datetime.utcnow().isoformat(),
“created_at”: datetime.utcnow().isoformat(),
}
headers = {**supabase_headers(), “Prefer”: “return=representation”}
try:
r = requests.post(url, headers=headers, json=data)
return r.json()[0] if r.status_code in [200, 201] else None
except Exception:
return None

def update_user(telegram_id: int, updates: dict):
“”“Met à jour un utilisateur.”””
url = f”{SUPABASE_URL}/rest/v1/users?telegram_id=eq.{telegram_id}”
headers = {**supabase_headers(), “Prefer”: “return=minimal”}
try:
requests.patch(url, headers=headers, json=updates)
except Exception:
pass

def reset_daily_counts():
“”“Remet à 0 les compteurs quotidiens de tous les users free.”””
url = f”{SUPABASE_URL}/rest/v1/users?plan=eq.free”
headers = {**supabase_headers(), “Prefer”: “return=minimal”}
try:
requests.patch(url, headers=headers, json={“jobs_sent_today”: 0})
except Exception:
pass

def get_users_by_city(city: str) -> list:
“”“Récupère tous les utilisateurs d’une ville.”””
url = f”{SUPABASE_URL}/rest/v1/users?city=eq.{city}&select=*”
try:
r = requests.get(url, headers=supabase_headers())
return r.json()
except Exception:
return []

def job_exists(job_hash: str) -> bool:
url = f”{SUPABASE_URL}/rest/v1/jobs?job_hash=eq.{job_hash}&select=id”
try:
r = requests.get(url, headers=supabase_headers())
return len(r.json()) > 0
except Exception:
return False

def save_job(job: dict, city: str, requirements: list) -> bool:
url = f”{SUPABASE_URL}/rest/v1/jobs”
headers = {**supabase_headers(), “Prefer”: “return=minimal”}
data = {
“city”: city,
“title”: job[“title”],
“company”: job.get(“company”, “”),
“location”: job.get(“location”, “”),
“sub_class”: job.get(“subClass”, “”),
“classification”: job.get(“classification”, “”),
“contract_type”: job.get(“contractType”, “”),
“salary”: job.get(“salary”, “”),
“link”: job.get(“link”, “”),
“full_text”: job.get(“fullText”, “”),
“requirements”: json.dumps(requirements),
“job_hash”: make_hash(job, city),
“created_at”: datetime.utcnow().isoformat(),
}
try:
r = requests.post(url, headers=headers, json=data)
return r.status_code in [200, 201]
except Exception:
return False

# =============================================

# SCRAPING SEEK — Sans Playwright

# =============================================

SEEK_HEADERS = {
“User-Agent”: “Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) “
“AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 “
“Mobile/15E148 Safari/604.1”,
“Accept”: “text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8”,
“Accept-Language”: “en-AU,en;q=0.9”,
}

def build_seek_url(city_key: str) -> str:
c = CITIES[city_key]
city_slug = c[“name”].replace(” “, “-”)
return (
f”https://www.seek.com.au/jobs/in-{city_slug}-{c[‘state’]}-{c[‘postcode’]}”
f”?daterange=1&sortmode=ListedDate&distance=20”
)

def build_seek_api_url(city_key: str, page: int = 1) -> str:
“”“Utilise l’API JSON interne de Seek (plus fiable que le HTML).”””
c = CITIES[city_key]
return (
f”https://www.seek.com.au/api/chalice-search/v4/search”
f”?where={c[‘name’]}+{c[‘state’]}+{c[‘postcode’]}”
f”&daterange=1&sortmode=ListedDate&distance=20”
f”&page={page}&pagesize=20”
)

def scrape_seek_api(city_key: str) -> list:
“””
Tente l’API JSON de Seek d’abord, fallback sur le HTML.
“””
jobs = []

```
# Méthode 1 : API JSON interne
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
                "link": f"https://www.seek.com.au/job/{item.get('id', '')}",
                "fullText": item.get("teaser", ""),
            }
            if job["title"]:
                jobs.append(job)
        if jobs:
            return jobs[:20]
except Exception as e:
    log.warning(f"API Seek échouée pour {city_key}: {e}")

# Méthode 2 : Fallback HTML
try:
    url = build_seek_url(city_key)
    r = requests.get(url, headers=SEEK_HEADERS, timeout=15)
    if r.status_code != 200:
        log.warning(f"Seek HTML {r.status_code} pour {city_key}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    # Cherche les données JSON embarquées dans la page
    scripts = soup.find_all("script")
    for script in scripts:
        if script.string and "window.SEEK_REDUX_DATA" in script.string:
            # Extraire le JSON du state Redux
            match = re.search(
                r"window\.SEEK_REDUX_DATA\s*=\s*({.*?});?\s*$",
                script.string,
                re.DOTALL,
            )
            if match:
                try:
                    redux_data = json.loads(match.group(1))
                    results = (
                        redux_data.get("results", {})
                        .get("results", {})
                        .get("jobs", [])
                    )
                    for item in results:
                        job = {
                            "title": item.get("title", ""),
                            "company": item.get("advertiser", {}).get(
                                "description", ""
                            ),
                            "location": item.get("location", ""),
                            "subClass": item.get("subClassification", {}).get(
                                "description", ""
                            ),
                            "classification": item.get(
                                "classification", {}
                            ).get("description", ""),
                            "contractType": item.get("workType", ""),
                            "salary": item.get("salary", ""),
                            "link": f"https://www.seek.com.au/job/{item.get('id', '')}",
                            "fullText": item.get("teaser", ""),
                        }
                        if job["title"]:
                            jobs.append(job)
                except json.JSONDecodeError:
                    pass

    # Fallback: parse les articles HTML
    if not jobs:
        cards = soup.select('article[data-testid="job-card"]')
        for card in cards:
            title_link = card.select_one('a[href*="/job/"]')
            if not title_link:
                continue
            title = title_link.get_text(strip=True)
            link = "https://www.seek.com.au" + title_link["href"].split("?")[0]
            company_link = card.select_one('a[href*="-jobs"]')
            company = company_link.get_text(strip=True) if company_link else ""
            full_text = card.get_text(" ", strip=True)[:500]

            job = {
                "title": title,
                "company": company,
                "location": "",
                "subClass": "",
                "classification": "",
                "contractType": "",
                "salary": "",
                "link": link,
                "fullText": full_text,
            }
            jobs.append(job)

except Exception as e:
    log.error(f"Scraping HTML échoué pour {city_key}: {e}")

return jobs[:20]
```

# =============================================

# FILTRAGE IA (identique à ton code)

# =============================================

def make_hash(job: dict, city: str) -> str:
raw = f”{city}|{job[‘title’]}|{job.get(‘company’, ‘’)}|{job.get(‘link’, ‘’)}”
return hashlib.md5(raw.encode()).hexdigest()

def quick_reject(job: dict) -> bool:
title_lower = job[“title”].lower()
sub_lower = job.get(“subClass”, “”).lower()
for kw in REJECT_TITLE:
if kw in title_lower:
return True
for kw in REJECT_CATEGORY:
if kw in sub_lower:
return True
return False

def analyze_with_ai(job: dict) -> bool:
try:
response = ai_client.messages.create(
model=“claude-haiku-4-5-20251001”,
max_tokens=30,
messages=[
{
“role”: “user”,
“content”: f””“I post jobs for backpackers on Working Holiday Visas in Australia. Should I post this one?

A backpacker typically has:

- No Australian qualifications
- Maybe some experience from their home country
- Basic to good English
- WHV visa (can work max 6 months per employer)

Say YES if a backpacker could realistically apply:

- Hospitality: chef, cook, kitchen hand, barista, waiter, bar staff
- Retail: shop assistant, cashier, store manager assistant
- Labour: construction labourer, farm hand, warehouse, factory
- Services: cleaner, housekeeper, driver, delivery, removalist
- Entry admin: receptionist, data entry, office assistant
- Trades helper: painter, labourer helper, scaffolder
- Mining: entry level miner, operator, labourer on site
- Tourism: tour guide, hotel staff, spa therapist
- Childcare assistant, pet care, sports coach

Say NO if:

- Title contains “Senior” + a professional field
- Requires Australian certification (Cert IV, diploma, degree, registration)
- Banking, finance professional, insurance underwriter
- IT/Software professional role
- Government role requiring citizenship
- Executive or C-level position

Title: {job[‘title’]}
Category: {job.get(‘subClass’, ‘’)}
Full text preview: {job.get(‘fullText’, ‘’)[:200]}

One word YES or NO:”””,
}
],
)
result = response.content[0].text.strip().upper()
return “YES” in result
except Exception as e:
log.warning(f”Erreur IA: {e}”)
return False

def detect_requirements(title: str, full_text: str) -> list:
text = (title + “ “ + full_text).lower()
reqs = []
if “rsa” in text or “responsible service of alcohol” in text:
reqs.append(“RSA”)
if “white card” in text or “whitecard” in text:
reqs.append(“White Card”)
if “forklift” in text and (“licence” in text or “license” in text or “ticket” in text):
reqs.append(“Forklift licence”)
if “driver” in text and (“licence” in text or “license” in text):
reqs.append(“Permis”)
return reqs

# =============================================

# FORMATAGE MESSAGE TELEGRAM

# =============================================

def format_job_message(job: dict, city_name: str, requirements: list) -> str:
“”“Formate une offre pour Telegram (Markdown).”””
# Emojis par type
type_emoji = {
“Full-time”: “🟢”,
“Part-time”: “🔵”,
“Casual”: “🟡”,
“Contract”: “🟠”,
}
ct = job.get(“contractType”, “”)
emoji = type_emoji.get(ct, “💼”)

```
lines = [
    f"{emoji} *{job['title']}*",
    f"🏢 {job.get('company', 'N/A')}",
    f"📍 {city_name}",
]
if job.get("salary"):
    lines.append(f"💰 {job['salary']}")
if ct:
    lines.append(f"📋 {ct}")
if requirements:
    lines.append(f"⚠️ {', '.join(requirements)}")
lines.append(f"\n🔗 [Postuler ici]({job['link']})")

return "\n".join(lines)
```

def format_job_teaser(job: dict, city_name: str) -> str:
“”“Version courte pour les users gratuits (sans lien).”””
return (
f”💼 *{job[‘title’]}*\n”
f”🏢 {job.get(‘company’, ‘N/A’)} — {city_name}\n\n”
f”*⭐ Passe en Pro pour voir toutes les offres, tous les liens, toutes les villes → /premium*”
)

# =============================================

# COMMANDES TELEGRAM

# =============================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Quand un user tape /start.”””
user = update.effective_user
existing = get_user(user.id)

```
if existing:
    await update.message.reply_text(
        f"Content de te revoir ! 👋\n\n"
        f"Ta ville : *{CITIES.get(existing['city'], {}).get('name', 'Non définie')}*\n"
        f"Ton plan : *{existing['plan'].upper()}*\n\n"
        f"Commandes :\n"
        f"/city — Changer de ville\n"
        f"/premium — Passer Premium\n"
        f"/status — Voir ton compte\n"
        f"/help — Aide",
        parse_mode="Markdown",
    )
    return

# Nouveau user → choisir une ville
keyboard = []
row = []
for key, city in CITIES.items():
    row.append(InlineKeyboardButton(city["name"], callback_data=f"city_{key}"))
    if len(row) == 2:
        keyboard.append(row)
        row = []
if row:
    keyboard.append(row)

await update.message.reply_text(
    "🎒 *Bienvenue sur BackpackRadar !*\n\n"
    "Je trouve les meilleurs jobs WHV/PVT en Australie pour toi.\n\n"
    "🆓 *Plan Gratuit* : 3 offres/jour pour 1 ville\n"
    "⭐ *Plan Pro* : Toutes les offres + toutes les villes + liens directs ($9.99/mois)\n\n"
    "Pour commencer, choisis ta ville :",
    parse_mode="Markdown",
    reply_markup=InlineKeyboardMarkup(keyboard),
)
```

async def cmd_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Changer de ville.”””
keyboard = []
row = []
for key, city in CITIES.items():
row.append(InlineKeyboardButton(city[“name”], callback_data=f”city_{key}”))
if len(row) == 2:
keyboard.append(row)
row = []
if row:
keyboard.append(row)

```
await update.message.reply_text(
    "📍 Choisis ta ville :",
    reply_markup=InlineKeyboardMarkup(keyboard),
)
```

async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Infos premium.”””
# Tu peux remplacer le lien par ton lien Stripe/PayPal
PAYMENT_LINK = os.environ.get(“PAYMENT_LINK”, “https://buy.stripe.com/ton-lien”)

```
await update.message.reply_text(
    "⭐ *BackpackRadar Premium*\n\n"
    "✅ Toutes les offres WHV en temps réel\n"
    "✅ Lien direct pour postuler en 1 clic\n"
    "✅ Alertes illimitées\n"
    "✅ Accès à TOUTES les villes d'Australie\n\n"
    f"💰 *$9.99 AUD/mois*\n"
    f"_(moins cher qu'un café par semaine)_\n\n"
    f"[🔗 S'abonner ici]({PAYMENT_LINK})\n\n"
    "_Après paiement, envoie ton reçu ici et tu seras activé en quelques minutes._",
    parse_mode="Markdown",
)
```

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Statut du compte.”””
user_data = get_user(update.effective_user.id)
if not user_data:
await update.message.reply_text(“Tu n’es pas encore inscrit. Tape /start !”)
return

```
city_name = CITIES.get(user_data["city"], {}).get("name", "Non définie")
plan = user_data["plan"].upper()
sent = user_data.get("jobs_sent_today", 0)

if user_data["plan"] == "free":
    remaining = max(0, 3 - sent)
    status_line = f"📊 Offres restantes aujourd'hui : *{remaining}/3*"
else:
    status_line = "📊 Offres : *Illimitées* ✨"

await update.message.reply_text(
    f"📋 *Ton compte BackpackRadar*\n\n"
    f"📍 Ville : *{city_name}*\n"
    f"💎 Plan : *{plan}*\n"
    f"{status_line}\n\n"
    f"/city — Changer de ville\n"
    f"/premium — Passer Premium",
    parse_mode="Markdown",
)
```

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“🎒 *BackpackRadar — Aide*\n\n”
“/start — S’inscrire / Accueil\n”
“/city — Changer de ville\n”
“/premium — Infos Premium\n”
“/status — Voir ton compte\n\n”
“Le bot scan Seek toutes les ~15 min et t’envoie “
“les offres adaptées aux WHV/PVT.\n\n”
“Questions ? Contacte @ton_username”,
parse_mode=“Markdown”,
)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Gère les clics sur les boutons inline.”””
query = update.callback_query
await query.answer()

```
data = query.data
user = query.from_user

# Sélection de ville
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
    await query.edit_message_text(
        f"✅ Parfait ! Tu recevras les offres WHV pour *{city_name}*.\n\n"
        f"🆓 Plan Gratuit : 3 offres/jour pour cette ville\n"
        f"⭐ /premium pour toutes les offres + toutes les villes !",
        parse_mode="Markdown",
    )
```

# =============================================

# ADMIN — Activer premium manuellement

# =============================================

ADMIN_IDS = [int(x) for x in os.environ.get(“ADMIN_IDS”, “”).split(”,”) if x]

async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Admin: /activate <telegram_id> pour passer un user en premium.”””
if update.effective_user.id not in ADMIN_IDS:
return

```
if not context.args:
    await update.message.reply_text("Usage: /activate <telegram_id>")
    return

target_id = int(context.args[0])
update_user(target_id, {"plan": "premium"})
await update.message.reply_text(f"✅ User {target_id} passé en Premium.")

# Notifier le user
try:
    bot = context.bot
    await bot.send_message(
        target_id,
        "🎉 *Ton compte est maintenant Premium !*\n\n"
        "Tu recevras toutes les offres WHV avec liens directs.\n"
        "Merci pour ton soutien ! 🙏",
        parse_mode="Markdown",
    )
except Exception:
    pass
```

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Admin: /stats pour voir les chiffres.”””
if update.effective_user.id not in ADMIN_IDS:
return

```
# Compter les users
try:
    url = f"{SUPABASE_URL}/rest/v1/users?select=plan"
    r = requests.get(url, headers=supabase_headers())
    users = r.json()
    total = len(users)
    premium = sum(1 for u in users if u.get("plan") == "premium")
    free = total - premium

    await update.message.reply_text(
        f"📊 *Stats BackpackRadar*\n\n"
        f"👥 Total users : {total}\n"
        f"🆓 Free : {free}\n"
        f"⭐ Premium : {premium}\n"
        f"💰 Revenue estimé : ${premium * 9.99:.0f}/mois",
        parse_mode="Markdown",
    )
except Exception as e:
    await update.message.reply_text(f"Erreur: {e}")
```

# =============================================

# BOUCLE DE SCRAPING

# =============================================

def get_premium_users() -> list:
“”“Récupère tous les utilisateurs premium (reçoivent TOUTES les villes).”””
url = f”{SUPABASE_URL}/rest/v1/users?plan=eq.premium&select=*”
try:
r = requests.get(url, headers=supabase_headers())
return r.json()
except Exception:
return []

async def send_job_to_users(bot: Bot, job: dict, city_key: str, requirements: list):
“”“Envoie une offre aux users concernés.
- Premium : reçoit TOUTES les villes, message complet avec lien
- Free : reçoit seulement SA ville, 3 offres/jour max, sans lien
“””
city_name = CITIES[city_key][“name”]
already_sent = set()  # éviter les doublons

```
# 1) Envoyer à tous les premium (toutes les villes)
premium_users = get_premium_users()
for user_data in premium_users:
    try:
        tid = user_data["telegram_id"]
        already_sent.add(tid)
        msg = format_job_message(job, city_name, requirements)
        await bot.send_message(tid, msg, parse_mode="Markdown")
    except Exception as e:
        log.warning(f"Erreur envoi premium à {user_data.get('telegram_id')}: {e}")

# 2) Envoyer aux free de cette ville uniquement
free_users = get_users_by_city(city_key)
for user_data in free_users:
    try:
        tid = user_data["telegram_id"]
        if tid in already_sent:
            continue  # déjà envoyé en premium
        plan = user_data.get("plan", "free")
        if plan != "free":
            continue
        sent_today = user_data.get("jobs_sent_today", 0)
        if sent_today < 3:
            msg = format_job_teaser(job, city_name)
            await bot.send_message(tid, msg, parse_mode="Markdown")
            update_user(tid, {"jobs_sent_today": sent_today + 1})
    except Exception as e:
        log.warning(f"Erreur envoi free à {user_data.get('telegram_id')}: {e}")

await asyncio.sleep(0.5)
```

async def scraping_loop(app: Application):
“”“Boucle principale de scraping qui tourne en fond.”””
bot = app.bot
cycle = 0
last_reset = datetime.utcnow().date()

```
# Attendre que le bot soit prêt
await asyncio.sleep(5)
log.info("🚀 Boucle de scraping démarrée")

while True:
    cycle += 1
    now = datetime.utcnow()

    # Reset quotidien des compteurs free
    if now.date() > last_reset:
        reset_daily_counts()
        last_reset = now.date()
        log.info("🔄 Reset quotidien des compteurs")

    log.info(f"=== CYCLE {cycle} — {now.strftime('%H:%M:%S UTC')} ===")

    total_new = 0
    for city_key in CITIES:
        try:
            jobs = scrape_seek_api(city_key)
            log.info(f"  {CITIES[city_key]['name']}: {len(jobs)} annonces trouvées")

            for job in jobs:
                jh = make_hash(job, city_key)
                if job_exists(jh):
                    continue
                if quick_reject(job):
                    continue
                if not analyze_with_ai(job):
                    continue

                requirements = detect_requirements(
                    job["title"], job.get("fullText", "")
                )
                if save_job(job, city_key, requirements):
                    total_new += 1
                    log.info(f"  ✅ {job['title']} — {job.get('company', '')}")
                    await send_job_to_users(bot, job, city_key, requirements)

        except Exception as e:
            log.error(f"  ERREUR {city_key}: {e}")

        # Pause entre les villes
        await asyncio.sleep(random.randint(5, 15))

    log.info(f"Cycle {cycle} terminé — {total_new} nouveaux jobs")

    # Attente avant prochain cycle
    wait = random.randint(CHECK_INTERVAL_MIN * 60, CHECK_INTERVAL_MAX * 60)
    log.info(f"Prochain cycle dans ~{wait // 60} min")
    await asyncio.sleep(wait)
```

# =============================================

# LANCEMENT

# =============================================

def main():
“”“Point d’entrée.”””
log.info(”============================================”)
log.info(”   BACKPACKRADAR — Telegram Bot + Scraper”)
log.info(”   10 villes | IA Filter | Freemium”)
log.info(”============================================”)

```
# Créer l'application Telegram
app = Application.builder().token(TELEGRAM_TOKEN).build()

# Commandes
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("city", cmd_city))
app.add_handler(CommandHandler("premium", cmd_premium))
app.add_handler(CommandHandler("status", cmd_status))
app.add_handler(CommandHandler("help", cmd_help))
app.add_handler(CommandHandler("activate", cmd_activate))
app.add_handler(CommandHandler("stats", cmd_stats))

# Boutons inline
app.add_handler(CallbackQueryHandler(callback_handler))

# Lancer la boucle de scraping en parallèle du bot
loop = asyncio.get_event_loop()
loop.create_task(scraping_loop(app))

# Démarrer le bot (polling — pas besoin de webhook)
app.run_polling(drop_pending_updates=True)
```

if **name** == “**main**”:
main()
