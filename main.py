# -*- coding: utf-8 -*-
"""
Lightning Roulette: GameData365 -> rule signals -> Telegram.

Rules:
1. 6 red in a row -> target BLACK; 6 black -> target RED.
2. 6 even in a row -> target ODD; 6 odd -> target EVEN.
3. 6 numbers in 1-18 -> target 19-36; 6 in 19-36 -> target 1-18.
4. 4 numbers in one dozen -> target the other two dozens.

Zero breaks all runs. Zero also counts as one attempt for open signals.
Each signal is checked against the NEXT 10 spins. First hit = WIN, else LOSS.

Set secrets in Railway Variables, never in GitHub:
GAMEDATA365_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timezone

import requests

API_URL = os.getenv("GAMEDATA365_API_URL", "https://api.gamedata365.com/v1/live")
HISTORY_URL = os.getenv("GAMEDATA365_HISTORY_URL", "https://api.gamedata365.com/v1/history")
API_KEY = os.getenv("GAMEDATA365_API_KEY", "").strip()
TABLE_ID = os.getenv("TABLE_ID", "lightning_roulette").strip()
PROVIDER = os.getenv("PROVIDER", "evolution").strip().lower()
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
HISTORY_LIMIT = max(20, min(500, int(os.getenv("HISTORY_LIMIT", "100"))))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_IDS = [x.strip() for x in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if x.strip()]
ATTEMPTS = 10

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("lightning-roulette")
http = requests.Session()
http.headers.update({"User-Agent": "LightningRouletteBot/3.1", "X-API-Key": API_KEY})
