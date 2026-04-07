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

TELEGRAM_TOKEN = "8760392511:AAEXiLXWXcy9ZTjs2wNSp6wDo_9RJYy4VDI"
ANTHROPIC_KEY = "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"
SUPABASE_URL = "https://ephveuabosmvrwbnqpdn.supabase.co"
SUPABASE_KEY = "sb_publishable_YS2C4C5s4VyYKNmN-AMwaQ_Q_E6Ox0P"
MAIN_CHANNEL_ID = ""
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("backpackradar")

ai_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


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
        "title​​​​​​​​​​​​​​​​
