# Telegram Recall Bot — Setup

Message your grocery list to a Telegram bot, get back active FDA/USDA recalls
filtered to California/nationwide. Free to run.

## 1. Create the bot (2 minutes)

1. In Telegram, message **@BotFather**.
2. Send `/newbot`, follow the prompts (pick a name and a username ending in `bot`).
3. BotFather gives you a token like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
   Save it — this is `TELEGRAM_BOT_TOKEN`.

## 2. Push the code to GitLab

Files needed in the repo root: `app.py`, `requirements.txt`, `render.yaml`.

```bash
git init
git add app.py requirements.txt render.yaml
git commit -m "Telegram recall bot"
git remote add origin <your-gitlab-repo-url>
git push -u origin main
```

## 3. Deploy to Render

1. Go to https://render.com → sign up/log in (free).
2. **New → Blueprint**, connect your GitLab account, pick the repo.
   Render reads `render.yaml` and sets up the web service automatically.
   (If Render doesn't detect it, create manually: **New → Web Service**,
   connect the repo, build command `pip install -r requirements.txt`,
   start command `gunicorn app:app`.)
3. When prompted for `TELEGRAM_BOT_TOKEN`, paste the token from step 1.
4. Deploy. Once live, note your service URL, something like:
   `https://grocery-recall-bot.onrender.com`

## 4. Point Telegram at your deployed bot

Run this once (replace both placeholders):

```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://grocery-recall-bot.onrender.com/webhook"
```

You should get back `{"ok":true,"result":true,...}`.

## 5. Use it

Open a chat with your bot in Telegram and send your list, e.g.:

```
ketchup
milk
frozen blueberries
eggs
```

You'll get a reply listing which items are clear and which have active
recalls. Section headers (ALL CAPS lines) and store-name lines with slashes
are automatically skipped, so you can paste your list mostly as-is.

## Notes

- **Cold starts**: Render's free tier sleeps after 15 min of no traffic. The
  first message after a quiet spell can take 30-50 seconds to get a reply —
  Telegram will keep the request alive, so it'll still arrive, just slower.
- **Cost**: $0, as long as you stay on Render's free web service tier.
- **Privacy**: the bot only reads messages sent directly to it, not any other
  chats. Your `TELEGRAM_BOT_TOKEN` is set as a Render env var, not in code.
