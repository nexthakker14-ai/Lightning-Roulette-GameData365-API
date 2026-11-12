# -*- coding: utf-8 -*-
"""GameData365 -> Telegram bridge for Evolution Lightning Roulette."""
import os, json, time, logging
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()
API_URL = os.getenv('GAMEDATA365_API_URL', 'https://api.gamedata365.com/v1/live').strip()
API_KEY = os.getenv('GAMEDATA365_API_KEY', '').strip()
TABLE_ID = os.getenv('TABLE_ID', 'lightning_roulette').strip()
PROVIDER = os.getenv('PROVIDER', 'evolution').strip().lower()
POLL_SECONDS = max(5, int(os.getenv('POLL_SECONDS', '10')))
STATE_FILE = Path(os.getenv('STATE_FILE', 'state.json'))
TG_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
TG_CHAT_IDS = [x.strip() for x in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if x.strip()]

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger('lightning-roulette-api-bridge')
s = requests.Session()
s.headers.update({'User-Agent': 'LightningRoulette-API-Bridge/1.0'})


def load_state():
    try:
        obj = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        log.exception('Cannot read state file')
        return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(STATE_FILE)


def records_in(payload):
    out = []
    def walk(x):
        if isinstance(x, dict):
            keys = {str(k).lower() for k in x}
            if keys & {'number', 'result', 'winning_number', 'winningnumber', 'outcome'}:
                out.append(x)
            for v in x.values():
                if isinstance(v, (dict, list)): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(payload)
    return out


def getv(d, *names):
    lower = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        if lower.get(n.lower()) is not None: return lower[n.lower()]
    return None


def normalize(d):
    raw = getv(d, 'number', 'result', 'winning_number', 'winningNumber', 'outcome')
    try: number = int(raw)
    except (TypeError, ValueError): return None
    if not 0 <= number <= 36: return None
    table = getv(d, 'table_id', 'tableId', 'table')
    provider = getv(d, 'provider', 'provider_name', 'providerName')
    backfilled = getv(d, 'backfilled', 'is_backfilled', 'isBackfilled')
    if table is not None and str(table).lower() != TABLE_ID.lower(): return None
    if provider is not None and str(provider).lower() != PROVIDER: return None
    if backfilled is True or str(backfilled).lower() in ('true', '1', 'yes'): return None
    rid = getv(d, 'round_id', 'roundId', 'game_id', 'gameId', 'id', 'spin_id', 'spinId')
    stamp = getv(d, 'timestamp', 'created_at', 'createdAt', 'time', 'date')
    color = getv(d, 'color', 'colour')
    if not color:
        color = 'зелений' if number == 0 else ('червоний' if number in {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36} else 'чорний')
    uid = str(rid) if rid is not None else f'{stamp}:{number}'
    return {'id': uid, 'number': number, 'color': str(color), 'timestamp': stamp}


def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_IDS:
        log.warning('Telegram credentials missing; result will only be logged')
        return True
    ok = True
    for chat_id in TG_CHAT_IDS:
        try:
            r = s.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage', json={'chat_id': chat_id, 'text': text}, timeout=20)
            r.raise_for_status()
            if not r.json().get('ok'): raise RuntimeError(r.text[:300])
        except Exception:
            ok = False
            log.exception('Telegram delivery failed for chat %s', chat_id)
    return ok


def main():
    if not API_KEY: raise RuntimeError('Set GAMEDATA365_API_KEY in Railway Variables')
    log.info('Starting GameData365 bridge | table=%s | interval=%ss', TABLE_ID, POLL_SECONDS)
    state = load_state()
    while True:
        try:
            r = s.get(API_URL, params={'table': TABLE_ID}, headers={'X-API-Key': API_KEY}, timeout=20)
            r.raise_for_status()
            payload = r.json()
            records = records_in(payload)
            if not records:
                log.warning('No result records detected in API JSON; share a redacted sample to adapt parser')
            # API responses are usually newest-first; process in reverse to preserve order.
            for raw in reversed(records):
                item = normalize(raw)
                if not item or item['id'] == state.get('last_result_id'): continue
                msg = (f"🎰 Lightning Roulette\nРезультат: {item['number']} — {item['color']}\n"
                       f"Стіл: {TABLE_ID}\nПровайдер: {PROVIDER}\nRound ID: {item['id']}\nДжерело: GameData365 API")
                log.info('New result: %s | %s | id=%s', item['number'], item['color'], item['id'])
                if send_telegram(msg):
                    state.update(last_result_id=item['id'], last_result_number=item['number'], last_result_timestamp=item['timestamp'])
                    save_state(state)
                else:
                    log.error('State not advanced because Telegram delivery failed')
                    break
        except requests.HTTPError as e: log.error('API HTTP error: %s', e)
        except requests.RequestException as e: log.error('Network error: %s', e)
        except Exception: log.exception('Polling cycle failed')
        time.sleep(POLL_SECONDS)

if __name__ == '__main__': main()
