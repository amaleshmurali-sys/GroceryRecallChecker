"""
Telegram Recall Bot
--------------------
Webhook-based Telegram bot. Message it your grocery list (one item per line)
and it replies with active FDA/USDA recalls filtered to California/nationwide.

Deploy this as a Render free web service. Render's free web services spin
down after 15 min idle and cold-start on the next request (~30-50s) — that's
fine here since Telegram retries delivery, you'll just get a slightly slower
first reply after a quiet period.
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

FDA_URL = "https://api.fda.gov/food/enforcement.json"
USDA_URL = "https://www.fsis.usda.gov/fsis/api/recall/v/1"


# ---------- recall lookup (same logic as the standalone script) ----------

def location_matches(text):
    if not text:
        return False
    t = text.lower()
    return "california" in t or "nationwide" in t


def check_fda(term):
    params = {
        "search": f'product_description:"{term}"+AND+status:"Ongoing"',
        "sort": "report_date:desc",
        "limit": 25,
    }
    try:
        r = requests.get(FDA_URL, params=params, timeout=15)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        results = r.json().get("results", [])
        matches = [x for x in results if location_matches(x.get("distribution_pattern"))]
        return [
            {
                "source": "FDA",
                "brand": m.get("recalling_firm", "Unknown brand"),
                "product": (m.get("product_description") or "")[:90],
                "reason": m.get("reason_for_recall", "No reason listed"),
                "classification": m.get("classification", ""),
                "date": m.get("report_date", ""),
            }
            for m in matches
        ]
    except requests.RequestException:
        return []


def check_usda(term):
    params = {"field_product_items_value": term, "field_active_notice": "True"}
    try:
        r = requests.get(USDA_URL, params=params, timeout=15)
        r.raise_for_status()
        results = r.json()
        if not isinstance(results, list):
            return []
        matches = [x for x in results if location_matches(x.get("field_states"))]
        return [
            {
                "source": "USDA",
                "brand": m.get("field_establishment", "Unknown establishment"),
                "product": (m.get("field_title") or "")[:100],
                "reason": m.get("field_recall_reason", "No reason listed"),
                "classification": m.get("field_recall_classification", ""),
                "date": m.get("field_recall_date", ""),
            }
            for m in matches
        ]
    except requests.RequestException:
        return []


# ---------- press-release scraping (catches very recent recalls the ----------
# ---------- classification-lagged APIs above haven't picked up yet)  ----------

FDA_PRESS_URL = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"
FSIS_PRESS_URL = "https://www.fsis.usda.gov/recalls"

HEADERS = {"User-Agent": "Mozilla/5.0 (grocery-recall-bot/1.0)"}


FDA_FOOD_TAXONOMY_ID = "2323"  # "Regulated Product: Food & Beverages" filter term


def check_fda_press(term):
    """Query FDA's press-release page using its own server-side filters:
    full-text search for the item, restricted to Food & Beverages. No
    classification lag, and no need to paginate or guess — FDA does the
    matching and the product-type filtering for us."""
    try:
        r = requests.get(
            FDA_PRESS_URL,
            params={
                "search_api_fulltext": term,
                "field_regulated_product_field": FDA_FOOD_TAXONOMY_ID,
            },
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        matches = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) < 6:
                    continue
                date, brand, product, ptype, reason, company = cells[:6]
                matches.append({
                    "source": "FDA press release",
                    "brand": company or brand,
                    "product": product,
                    "reason": reason,
                    "classification": "",
                    "date": date,
                })
        return matches
    except requests.RequestException:
        return []


def check_fsis_press(term):
    """Scrape FSIS's recall/alert list page the same way, for meat/poultry/egg."""
    term_l = term.lower()
    try:
        r = requests.get(FSIS_PRESS_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        matches = []
        # FSIS lists recalls as headline links followed by a summary paragraph
        for link in soup.find_all("a"):
            headline = link.get_text(" ", strip=True)
            if not headline or len(headline) < 15:
                continue
            summary = ""
            sibling = link.find_next("p")
            if sibling:
                summary = sibling.get_text(" ", strip=True)
            haystack = f"{headline} {summary}".lower()
            if term_l in haystack:
                matches.append({
                    "source": "USDA press release",
                    "brand": headline,
                    "product": "",
                    "reason": summary[:150],
                    "classification": "",
                    "date": "",
                })
        # de-dupe by headline, cap results
        seen = set()
        deduped = []
        for m in matches:
            if m["brand"] not in seen:
                seen.add(m["brand"])
                deduped.append(m)
        return deduped[:5]
    except requests.RequestException:
        return []


def check_item(term):
    term = term.strip()
    if not term:
        return []
    return (
        check_fda(term)
        + check_usda(term)
        + check_fda_press(term)
        + check_fsis_press(term)
    )


# ---------- parsing a pasted grocery list into item terms ----------

BULLET_RE = re.compile(r"^[\s•\-\*\u2022]+")
QTY_RE = re.compile(r"\(\d+\)\s*$")


def parse_list(text):
    items = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = BULLET_RE.sub("", line).strip()
        line = QTY_RE.sub("", line).strip()
        if not line:
            continue
        # skip section headers / store lines: ALL CAPS, or a slash-joined store list
        letters = [c for c in line if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            continue
        if "/" in line and " " not in line:
            continue
        items.append(line)
    return items


def build_reply(items):
    if not items:
        return "Didn't find any grocery items in that message — send one item per line."

    lines = ["🛒 Recall check (California / nationwide):\n"]
    any_recalls = False
    for item in items:
        matches = check_item(item)
        # de-dupe near-identical entries between the API and press-release scrape
        seen_keys = set()
        deduped = []
        for m in matches:
            key = (m["brand"][:30].lower(), m["reason"][:30].lower())
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(m)
        matches = deduped
        if matches:
            any_recalls = True
            lines.append(f"⚠️ {item} — {len(matches)} active recall(s)")
            for m in matches[:3]:
                lines.append(f"   [{m['source']}] {m['brand']} — {m['reason']}")
        else:
            lines.append(f"✅ {item} — clear")
    if not any_recalls:
        lines.append("\nNothing on this list has an active CA recall right now.")
    return "\n".join(lines)


# ---------- Telegram webhook ----------

def send_message(chat_id, text):
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message or "text" not in message:
        return "ok"

    chat_id = message["chat"]["id"]
    text = message["text"]

    if text.strip().lower() in ("/start", "/help"):
        send_message(
            chat_id,
            "Send me your grocery list (one item per line) and I'll check it "
            "against active FDA/USDA recalls for California.",
        )
        return "ok"

    items = parse_list(text)
    send_message(chat_id, build_reply(items))
    return "ok"


@app.route("/", methods=["GET"])
def health():
    return "Recall bot is running."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
