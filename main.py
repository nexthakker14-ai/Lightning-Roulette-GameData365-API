# -*- coding: utf-8 -*-
"""GameData365 -> Lightning Roulette rules -> Telegram.

Signals are created only when a qualifying run is completed. Each signal is
tracked against the next 10 roulette results and closed on the first hit.
Zero breaks every run, but still counts as one of the 10 attempts for an
already-open signal.
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
session.headers.update({"User-Agent": "LightningRoulette-RulesBot/2.0"})

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
DOZEN_LABELS = {1: "1–12", 2: "13–24", 3: "25–36"}


def empty_state():
    return {"last_result_id": None, "history": [], "seen_ids": [], "active_signals": []}


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state must be an object")
        state.setdefault("last_result_id", None)
        state.setdefault("history", [])
        state.setdefault("seen_ids", [])
        state.setdefault("active_signals", [])
        return state
    except FileNotFoundError:
        return empty_state()
    except Exception:
        log.exception("Cannot read state file; starting fresh")
        return empty_state()


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

    color = "зелений" if number == 0 else ("червоний" if number in RED_NUMBERS else "чорний")
    rid = str(round_id) if round_id is not None else f"{timestamp}:{number}"
    if rid in {"None:", ":None"}:
        rid = f"{TABLE_ID}:{timestamp}:{number}"
    return {"id": rid, "number": number, "color": color, "timestamp": timestamp}


def category(number):
    if number == 0:
        return None
    return {
        "color": "червоне" if number in RED_NUMBERS else "чорне",
        "parity": "парне" if number % 2 == 0 else "непарне",
        "half": "1–18" if number <= 18 else "19–36",
        "dozen": 1 if number <= 12 else (2 if number <= 24 else 3),
    }


def run_length(history, current, key):
    """Length of the current uninterrupted category run, including current."""
    current_cat = category(current["number"])
    if current_cat is None:
        return 0, None
    value = current_cat[key]
    count = 0
    for item in reversed(history):
        item_cat = category(item["number"])
        if item_cat is None or item_cat[key] != value:
            break
        count += 1
    return count + 1, value


def build_new_signals(history, current):
    """Return signals only on the exact result that completes each series."""
    if current["number"] == 0:
        return []

    rules = [
        ("color", 6, 1),
        ("parity", 6, 2),
        ("half", 6, 3),
        ("dozen", 4, 4),
    ]
    result = []
    for key, threshold, rule_no in rules:
        length, value = run_length(history, current, key)
        if length != threshold:
            continue

        if key == "color":
            targets = ["чорне" if value == "червоне" else "червоне"]
            condition = f"6 червоних" if value == "червоне" else "6 чорних"
            target_text = targets[0].capitalize()
            title = "Колір"
        elif key == "parity":
            targets = ["непарне" if value == "парне" else "парне"]
            condition = f"6 {value}"
            target_text = targets[0].capitalize()
            title = "Парність"
        elif key == "half":
            targets = ["19–36" if value == "1–18" else "1–18"]
            condition = f"6 чисел {value}"
            target_text = targets[0]
            title = "Діапазон"
        else:
            targets = [d for d in (1, 2, 3) if d != value]
            condition = f"4 з {DOZEN_LABELS[value]}"
            target_text = " та ".join(DOZEN_LABELS[d] for d in targets)
            title = "Дюжини"

        result.append({
            "id": f"{current['id']}:{rule_no}",
            "rule_no": rule_no,
            "rule_name": title,
            "condition": condition,
            "targets": targets,
            "target_text": target_text,
            "attempts": 0,
            "trigger_number": current["number"],
        })
    return result


def send_telegram(message):
    if not TG_TOKEN or not TG_CHAT_IDS:
        log.warning("Telegram credentials are not configured; message only logged")
        log.info("TELEGRAM MESSAGE:\n%s", message)
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


def signal_message(signal, current):
    return (
        "🚨 LIGHTNING ROULETTE — СИГНАЛ\n"
        f"Правило №{signal['rule_no']} — {signal['rule_name']}\n"
        f"Умова: {signal['condition']}\n"
        f"Ставка: {signal['target_text']}\n"
        f"Останній результат: {current['number']} — {current['color']}\n"
        "Перевірка: наступні 10 чисел."
    )


def settle_active_signals(state, current):
    """Check each existing signal against this next spin, first hit wins."""
    remaining = []
    cats = category(current["number"])
    for signal in state["active_signals"]:
        signal["attempts"] = int(signal.get("attempts", 0)) + 1
        attempt = signal["attempts"]
        won = cats is not None and cats["dozen" if signal["rule_no"] == 4 else {
            1: "color", 2: "parity", 3: "half"
        }[signal["rule_no"]]] in signal["targets"]

        if won:
            target_unit = "-му"
            message = (
                f"🟢 WIN — виграш на {attempt}{target_unit} числі\n"
                f"Правило №{signal['rule_no']} — {signal['rule_name']}\n"
                f"Випало: {current['number']} — {current['color']}\n"
                f"Ціль: {signal['target_text']}"
            )
            send_telegram(message)
        elif attempt >= 10:
            message = (
                "🔴 LOSS — за 10 чисел успіху немає\n"
                f"Правило №{signal['rule_no']} — {signal['rule_name']}\n"
                f"Ставка: {signal['target_text']}"
            )
            send_telegram(message)
        else:
            remaining.append(signal)
    state["active_signals"] = remaining


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

    # API returns newest first on the supported feed; process chronologically.
    records.reverse()
    seen = set(state.get("seen_ids", []))
    history = state.get("history", [])

    for item in records:
        if item["id"] in seen:
            continue

        # Existing signals are checked first: this spin is attempt 1..10 for them.
        settle_active_signals(state, item)

        # Detect completed runs using only prior history plus the current result.
        new_signals = build_new_signals(history, item)
        for signal in new_signals:
            if not send_telegram(signal_message(signal, item)):
                log.error("Could not deliver signal %s", signal["id"])
                # Keep processing state; Telegram failures are visible in logs.
            state["active_signals"].append(signal)

        history.append({"id": item["id"], "number": item["number"], "timestamp": item["timestamp"]})
        history = history[-100:]
        seen.add(item["id"])
        state["last_result_id"] = item["id"]
        state["history"] = history
        state["seen_ids"] = list(seen)[-500:]
        save_state(state)

        log.info("New spin: %s | active signals=%s", f"{item['number']} — {item['color']}", len(state["active_signals"]))


def main():
    log.info("Started | provider=%s | table=%s | poll=%ss", PROVIDER, TABLE_ID, POLL_SECONDS)
    log.info("Rules: colour x6; parity x6; range x6; dozen x4; 10 attempts; no pre-alerts")
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
