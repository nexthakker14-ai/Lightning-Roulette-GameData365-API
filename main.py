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

RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
DOZENS = {1: "1–12", 2: "13–24", 3: "25–36"}


def blank_state():
    return {
        "bootstrapped": False,
        "history": [],
        "seen_ids": [],
        "active_signals": [],
        "outbox": [],
    }


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state.json must be a JSON object")
        for key, value in blank_state().items():
            state.setdefault(key, value)
        return state
    except FileNotFoundError:
        return blank_state()
    except Exception:
        log.exception("State file unreadable; starting with empty state")
        return blank_state()


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def records_in(payload):
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            keys = {str(k).lower() for k in obj}
            if keys.intersection({"number", "result", "winning_number", "winningnumber", "outcome"}):
                found.append(obj)
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    walk(value)

    walk(payload)
    return found


def val(obj, *names):
    lookup = {str(k).lower(): v for k, v in obj.items()}
    for name in names:
        if lookup.get(name.lower()) is not None:
            return lookup[name.lower()]
    return None


def parse_records(payload, allow_backfilled=False):
    output, seen = [], set()
    for raw in records_in(payload):
        try:
            number = int(val(raw, "number", "result", "winning_number", "winningNumber", "outcome"))
        except (TypeError, ValueError):
            continue
        if not 0 <= number <= 36:
            continue

        table = val(raw, "table_id", "tableId", "table")
        provider = val(raw, "provider", "provider_name", "providerName")
        backfilled = str(val(raw, "backfilled", "is_backfilled", "isBackfilled")).lower() in {"true", "1", "yes"}
        if table is not None and str(table).lower() != TABLE_ID.lower():
            continue
        if provider is not None and str(provider).lower() != PROVIDER:
            continue
        if backfilled and not allow_backfilled:
            continue

        rid = val(raw, "round_id", "roundId", "game_id", "gameId", "id", "spin_id", "spinId")
        stamp = val(raw, "timestamp", "created_at", "createdAt", "time", "date")
        if rid is None:
            if stamp is None:
                continue
            rid = f"{stamp}:{number}"
        rid = str(rid)
        if rid in seen:
            continue
        seen.add(rid)

        color = "зелений" if number == 0 else "червоний" if number in RED else "чорний"
        output.append({
            "id": rid, "number": number, "color": color,
            "timestamp": str(stamp or ""), "backfilled": backfilled,
        })

    output.sort(key=lambda x: (x["timestamp"] == "", x["timestamp"], x["id"]))
    return output


def api_get(url, params):
    if not API_KEY:
        raise RuntimeError("Missing GAMEDATA365_API_KEY Railway Variable")
    response = http.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def fetch_history():
    return parse_records(
        api_get(HISTORY_URL, {"table": TABLE_ID, "limit": HISTORY_LIMIT}),
        allow_backfilled=True,
    )


def fetch_live():
    return parse_records(api_get(API_URL, {"table": TABLE_ID}), allow_backfilled=False)


def cat(number):
    if number == 0:
        return None
    return {
        "color": "червоне" if number in RED else "чорне",
        "parity": "парне" if number % 2 == 0 else "непарне",
        "half": "1–18" if number <= 18 else "19–36",
        "dozen": 1 if number <= 12 else 2 if number <= 24 else 3,
    }


def run_length(history, current, key):
    current_cat = cat(current["number"])
    if current_cat is None:
        return 0, None
    wanted = current_cat[key]
    count = 0
    for old in reversed(history):
        old_cat = cat(old["number"])
        if old_cat is None or old_cat[key] != wanted:
            break
        count += 1
    return count + 1, wanted


def make_signals(history, current):
    if current["number"] == 0:
        return []
    rules = [("color", 6, 1), ("parity", 6, 2), ("half", 6, 3), ("dozen", 4, 4)]
    result = []
    for key, threshold, rule_no in rules:
        length, value = run_length(history, current, key)
        if length != threshold:
            continue

        if key == "color":
            targets = ["чорне" if value == "червоне" else "червоне"]
            target_text = targets[0].capitalize()
            condition = f"{threshold} результатів: {value}"
            name = "Колір"
        elif key == "parity":
            targets = ["непарне" if value == "парне" else "парне"]
            target_text = targets[0].capitalize()
            condition = f"{threshold} результатів: {value}"
            name = "Парність"
        elif key == "half":
            targets = ["19–36" if value == "1–18" else "1–18"]
            target_text = targets[0]
            condition = f"{threshold} результатів у діапазоні {value}"
            name = "Діапазон"
        else:
            targets = [d for d in (1, 2, 3) if d != value]
            target_text = " та ".join(DOZENS[d] for d in targets)
            condition = f"{threshold} результати з дюжини {DOZENS[value]}"
            name = "Дюжини"

        result.append({
            "id": f"{current['id']}:{rule_no}",
            "rule_no": rule_no, "rule_name": name, "condition": condition,
            "targets": targets, "target_text": target_text, "attempts": 0,
        })
    return result


def queue(state, msg_id, message):
    if not any(x["id"] == msg_id for x in state["outbox"]):
        state["outbox"].append({"id": msg_id, "text": message})
        save_state(state)


def flush_outbox(state):
    if not state["outbox"]:
        return
    if not TG_TOKEN or not CHAT_IDS:
        log.warning("Telegram variables missing; %d message(s) queued, not sent", len(state["outbox"]))
        return

    pending = []
    for item in state["outbox"]:
        all_ok = True
        for chat_id in CHAT_IDS:
            try:
                r = http.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": item["text"]},
                    timeout=20,
                )
                r.raise_for_status()
                if not r.json().get("ok"):
                    raise RuntimeError(r.text)
            except Exception:
                all_ok = False
                log.exception("Telegram send failed for chat %s", chat_id)
        if not all_ok:
            pending.append(item)
        else:
            log.info("Telegram message delivered: %s", item["id"])
    state["outbox"] = pending
    save_state(state)


def settle(state, spin):
    remaining = []
    current = cat(spin["number"])
    for signal in state["active_signals"]:
        signal["attempts"] += 1
        rule_key = {1: "color", 2: "parity", 3: "half", 4: "dozen"}[signal["rule_no"]]
        won = current is not None and current[rule_key] in signal["targets"]

        if won:
            text = (
                f"🟢 WIN — спроба №{signal['attempts']}\n"
                f"Правило №{signal['rule_no']} — {signal['rule_name']}\n"
                f"Випало: {spin['number']} — {spin['color']}\n"
                f"Ціль: {signal['target_text']}"
            )
            queue(state, f"WIN:{signal['id']}", text)
        elif signal["attempts"] >= ATTEMPTS:
            text = (
                "🔴 LOSS — за 10 наступних спінів ціль не випала\n"
                f"Правило №{signal['rule_no']} — {signal['rule_name']}\n"
                f"Ціль: {signal['target_text']}"
            )
            queue(state, f"LOSS:{signal['id']}", text)
        else:
            remaining.append(signal)
    state["active_signals"] = remaining


def process(state, spins):
    seen = set(state["seen_ids"])
    history = state["history"]

    for spin in spins:
        if spin["id"] in seen or spin.get("backfilled"):
            continue

        # Existing signals are evaluated before a new signal is opened.
        settle(state, spin)

        for signal in make_signals(history, spin):
            message = (
                "🚨 LIGHTNING ROULETTE — СИГНАЛ\n"
                f"Правило №{signal['rule_no']} — {signal['rule_name']}\n"
                f"Умова: {signal['condition']}\n"
                f"Ціль: {signal['target_text']}\n"
                f"Останнє число: {spin['number']} — {spin['color']}\n"
                "Перевірка: наступні 10 чисел. Це не гарантія результату."
            )
            queue(state, f"SIGNAL:{signal['id']}", message)
            state["active_signals"].append(signal)
            log.info("Signal created: rule=%s target=%s", signal["rule_no"], signal["target_text"])

        history.append({"id": spin["id"], "number": spin["number"], "timestamp": spin["timestamp"]})
        history[:] = history[-100:]
        seen.add(spin["id"])
        state["seen_ids"] = list(seen)[-1000:]
        save_state(state)
        log.info("New spin: %s — %s | active signals=%d", spin["number"], spin["color"], len(state["active_signals"]))

    flush_outbox(state)


def main():
    log.info("Starting | provider=%s | table=%s | poll=%ss", PROVIDER, TABLE_ID, POLL_SECONDS)
    state = load_state()

    # On first deployment, seed past results only to establish context.
    # No historical signals are sent.
    if not state["bootstrapped"]:
        try:
            old = fetch_history()
            if not old:
                raise RuntimeError("No parseable history results; verify /v1/history response")
            state["history"] = [
                {"id": x["id"], "number": x["number"], "timestamp": x["timestamp"]}
                for x in old[-100:]
            ]
            state["seen_ids"] = [x["id"] for x in old][-1000:]
            state["bootstrapped"] = True
            save_state(state)
            log.info("Bootstrapped with %d historical results; no old alerts sent", len(old))
        except Exception:
            log.exception("History bootstrap failed; will retry on next restart")
            return

    while True:
        try:
            # History fills gaps if several spins occur between polls.
            hist = fetch_history()
            live = fetch_live()
            combined = {x["id"]: x for x in hist}
            combined.update({x["id"]: x for x in live})
            spins = sorted(
                combined.values(),
                key=lambda x: (x["timestamp"] == "", x["timestamp"], x["id"])
            )
            process(state, spins)
        except requests.HTTPError as exc:
            log.error("GameData365 HTTP error: %s", exc)
        except requests.RequestException as exc:
            log.error("Network/API error: %s", exc)
        except Exception:
            log.exception("Polling cycle failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
