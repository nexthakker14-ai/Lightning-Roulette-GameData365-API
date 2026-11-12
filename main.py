# -*- coding: utf-8 -*-
"""
GameData365 -> Lightning Roulette rules -> Telegram.

Rules are evaluated on each NEW, non-backfilled result:
1) 6 same colours in a row -> signal the opposite colour.
2) 6 same parity results in a row -> signal the opposite parity.
3) 6 results in the same half (1-18 or 19-36) -> signal the opposite half.
4) 4 results in the same dozen (1-12 / 13-24 / 25-36) -> signal either of the other two dozens.

Zero breaks all runs and is never treated as red/black, even/odd, half, or dozen.
Signals are informational; the bot does not place bets.
"""
import os
import json
import time
import logging
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("GAMEDATA365_API_URL", "https://api.gamedata365.com/v1/live").strip()
API_KEY = os.getenv("GAMEDATA365_API_KEY", "").strip()
TABLE_ID = os.getenv("TABLE_ID", "lightning_roulette").strip()
PROVIDER = os.getenv("PROVIDER", "evolution").strip().lower()
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_IDS = [x.strip() for x in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if x.strip()]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("lightning-roulette-rules")
session = requests.Session()
session.headers.update({"User-Agent": "LightningRoulette-RulesBot/1.0"})


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state must be an object")
        state.setdefault("last_result_id", None)
        state.setdefault("history", [])
        state.setdefault("seen_ids", [])
        return state
    except FileNotFoundError:
        return {"last_result_id": None, "history": [], "seen_ids": []}
    except Exception:
        log.exception("Cannot read state file; starting fresh")
        return {"last_result_id": None, "history": [], "seen_ids": []}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def find_records(payload: Any):
    records = []

    def walk(obj):
        if isinstance(obj, dict):
            keys = {str(k).lower() for k in obj}
            if keys.intersection({"number", "result", "winning_number", "winningnumber", "outcome"}):
                records.append(obj)
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    walk(value)

    walk(payload)
    unique, ids = [], set()
    for rec in records:
        if id(rec) not in ids:
            ids.add(id(rec))
            unique.append(rec)
    return unique


def get_value(record, names):
    lookup = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        if lookup.get(name.lower()) is not None:
            return lookup[name.lower()]
    return None


def parse_record(record):
    raw_number = get_value(record, ["number", "result", "winning_number", "winningNumber", "outcome"])
    try:
        number = int(raw_number)
    except (TypeError, ValueError):
        return None
    if not 0 <= number <= 36:
        return None

    table = get_value(record, ["table_id", "tableId", "table"])
    provider = get_value(record, ["provider", "provider_name", "providerName"])
    backfilled = get_value(record, ["backfilled", "is_backfilled", "isBackfilled"])
    round_id = get_value(record, ["round_id", "roundId", "game_id", "gameId", "id", "spin_id", "spinId"])
    timestamp = get_value(record, ["timestamp", "created_at", "createdAt", "time", "date"])

    if table is not None and str(table).lower() != TABLE_ID.lower():
        return None
    if provider is not None and str(provider).lower() != PROVIDER:
        return None
    if backfilled is True or str(backfilled).lower() in {"true", "1", "yes"}:
        return None

    if number == 0:
        color = "зелений"
    elif number in {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}:
        color = "червоний"
    else:
        color = "чорний"

    rid = str(round_id) if round_id is not None else f"{timestamp}:{number}"
    if rid in {"None:", ":None"}:
        rid = f"{TABLE_ID}:{timestamp}:{number}"
    return {"id": rid, "number": number, "color": color, "timestamp": timestamp}


def category(number):
    if number == 0:
        return None
    return {
        "color": "червоний" if number in {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36} else "чорний",
        "parity": "парне" if number % 2 == 0 else "непарне",
        "half": "1–18" if number <= 18 else "19–36",
        "dozen": 1 if number <= 12 else (2 if number <= 24 else 3),
    }


def evaluate_rules(history, current):
    """Return signal messages triggered by the just-arrived result."""
    n = current["number"]
    cats = category(n)
    if cats is None:
        return []

    signals = []
    # Rule 1: 6 same roulette colours, then bet opposite colour.
    for key, count, rule_name in [
        ("color", 6, "Колір"),
        ("parity", 6, "Парність"),
        ("half", 6, "Діапазон"),
        ("dozen", 4, "Дюжина"),
    ]:
        if len(history) < count:
            continue
        recent = history[-count:]
        recent_cats = [category(x["number"]) for x in recent]
        if any(x is None for x in recent_cats):
            continue
        values = [x[key] for x in recent_cats]
        if len(set(values)) != 1:
            continue

        last_value = values[-1]
        if key == "color":
            target = "чорний" if last_value == "червоний" else "червоний"
            signals.append((1, f"🎨 Колір: 6 підряд {last_value}. Сигнал на наступний спін: {target}."))
        elif key == "parity":
            target = "непарне" if last_value == "парне" else "парне"
            signals.append((2, f"🔢 Парність: 6 підряд — {last_value}. Сигнал на наступний спін: {target}."))
        elif key == "half":
            target = "19–36" if last_value == "1–18" else "1–18"
            signals.append((3, f"📊 Діапазон: 6 підряд у {last_value}. Сигнал на наступний спін: {target}."))
        elif key == "dozen":
            targets = [d for d in (1, 2, 3) if d != last_value]
            labels = {1: "1–12", 2: "13–24", 3: "25–36"}
            signals.append((4, f"🟦 Дюжина: 4 підряд у {labels[last_value]}. Сигнал на наступний спін: {labels[targets[0]]} або {labels[targets[1]]}."))

    return signals


def send_telegram(message):
    if not TG_TOKEN or not TG_CHAT_IDS:
        log.warning("Telegram credentials are not configured; message only logged")
        return True
    ok = True
    for chat_id in TG_CHAT_IDS:
        try:
            response = session.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=20,
            )
            response.raise_for_status()
            if not response.json().get("ok"):
                raise RuntimeError(response.text)
        except Exception:
            ok = False
            log.exception("Telegram delivery failed for chat %s", chat_id)
    return ok


def fetch_live():
    if not API_KEY:
        raise RuntimeError("Set GAMEDATA365_API_KEY in Railway Variables")
    response = session.get(
        API_URL,
        params={"table": TABLE_ID},
        headers={"X-API-Key": API_KEY},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def handle_payload(payload, state):
    raw_records = find_records(payload)
    records = [x for x in (parse_record(r) for r in raw_records) if x]
    if not records:
        log.warning("No valid live results found in API response. Check JSON schema in logs.")
        log.debug("API JSON: %s", json.dumps(payload, ensure_ascii=False)[:4000])
        return

    # Keep API order safe: sort by timestamp when numeric; otherwise retain response order.
    records.reverse()
    seen = set(state.get("seen_ids", []))
    history = state.get("history", [])
    for item in records:
        if item["id"] in seen:
            continue

        # Add to sequence and evaluate only the current, non-backfilled result.
        history.append({"id": item["id"], "number": item["number"], "timestamp": item["timestamp"]})
        history = history[-100:]
        seen.add(item["id"])

        label = f"{item['number']} — {item['color']}"
        log.info("New spin: %s | round=%s", label, item["id"])
        signals = evaluate_rules(history, item)
        if signals:
            for rule_no, detail in signals:
                message = (
                    "🚨 LIGHTNING ROULETTE — СИГНАЛ\n"
                    f"Правило №{rule_no}\n"
                    f"Останній результат: {label}\n"
                    f"{detail}\n"
                    "Наступний спін; автоматичні ставки не виконуються."
                )
                if not send_telegram(message):
                    # Do not mark this round as processed; it will retry next poll.
                    return
        else:
            log.info("No rule triggered for this result.")

        state["last_result_id"] = item["id"]
        state["history"] = history
        state["seen_ids"] = list(seen)[-500:]
        save_state(state)


def main():
    log.info("Started | provider=%s | table=%s | poll=%ss", PROVIDER, TABLE_ID, POLL_SECONDS)
    log.info("Rules enabled: 1 colour x6; 2 parity x6; 3 half x6; 4 dozen x4")
    state = load_state()
    while True:
        try:
            handle_payload(fetch_live(), state)
        except requests.HTTPError as exc:
            log.error("API HTTP error: %s", exc)
        except requests.RequestException as exc:
            log.error("Network error: %s", exc)
        except Exception:
            log.exception("Polling cycle failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
