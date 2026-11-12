# Lightning Roulette — GameData365 API Bridge

Сервіс для Railway: опитує GameData365 API для Evolution Lightning Roulette, відкидає `backfilled=true`, запам'ятовує останній `round_id` і надсилає нові результати у Telegram.

## Railway Variables
- `GAMEDATA365_API_KEY` — новий API-ключ GameData365. Не використовуйте ключ, який був опублікований у чаті: відкличте його та створіть новий.
- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота, який має право писати в групу.
- `TELEGRAM_CHAT_ID` — ID групи/чату; кілька ID можна вказати через кому.
- `POLL_SECONDS` — інтервал опитування, за замовчуванням 10 секунд; врахуйте ліміт тарифу API.
- `TABLE_ID=lightning_roulette`
- `PROVIDER=evolution`

## Deploy
1. Розпакуйте ZIP і завантажте в окремий GitHub-репозиторій.
2. Створіть у Railway сервіс із цього репозиторію.
3. Додайте Railway Variables з переліку вище.
4. Перевірте Railway Logs.

## Важливе обмеження
Це окремий міст API → Telegram для перевірки отримання результатів. Він поки не вбудовує твої правила парності/сигналів і не підключається напряму до внутрішньої логіки іншого бота. Для цього потрібен його актуальний код або URL endpoint, який приймає результати.

Фактичний JSON-відгук API ще не був перевірений на твоєму акаунті. Парсер підтримує поширені назви полів (`number`, `result`, `winning_number`, `round_id`, `backfilled` тощо). Якщо в логах з'явиться `No result records detected`, надішли приклад JSON, приховавши ключі й токени, і парсер буде адаптовано.
