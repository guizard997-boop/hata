import os
import time
import json
import re
import statistics
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8876666395:AAFqZNNnqcz-TPiwuVIGzWWxUBHwas-orNg")
CHAT_ID = os.getenv("CHAT_ID", "8569472160")

CHECK_INTERVAL = 40                 # секунд между проверками
DISCOUNT_THRESHOLD = 0.15            # 15% и больше
MIN_COMPARABLES = 4
SEEN_FILE = "seen_apartments.json"
USD_KGS_RATE = 87.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

BASE_URL = "https://www.house.kg"
LISTING_URL = "https://www.house.kg/kupit-kvartiru?region=1&page={page}"

# ================================================

def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_seen(seen):
    recent = list(seen)[-5000:]
    with open(SEEN_FILE, "w") as f:
        json.dump(recent, f)

def send_telegram(text, photo_url=None):
    try:
        if photo_url:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            data = {
                "chat_id": CHAT_ID,
                "photo": photo_url,
                "caption": text[:1024],
                "parse_mode": "HTML"
            }
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print("Ошибка отправки в Telegram:", e)

def parse_price(text):
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if digits:
        try:
            return float(digits)
        except:
            return None
    return None

def parse_title(title):
    rooms = None
    area = None
    floor = None

    if not title:
        return rooms, area, floor

    m = re.search(r"(\d+)\s*[-–]?\s*комн", title, re.IGNORECASE)
    if m:
        rooms = int(m.group(1))
    elif "студи" in title.lower():
        rooms = 0

    m = re.search(r"([\d.,]+)\s*м2", title.replace(",", "."), re.IGNORECASE)
    if m:
        try:
            area = float(m.group(1))
        except:
            pass

    m = re.search(r"(\d+)\s*этаж", title, re.IGNORECASE)
    if m:
        floor = m.group(0)

    return rooms, area, floor

def get_listings(page=1):
    url = LISTING_URL.format(page=page)
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"Ошибка запроса страницы {page}:", e)
        return []

    soup = BeautifulSoup(r.text, "lxml")
    cards = soup.find_all(class_="listing")
    results = []

    for card in cards:
        try:
            link_tag = card.find("a", href=re.compile(r"/details/"))
            if not link_tag:
                continue

            href = link_tag.get("href", "")
            ad_id = href.split("/")[-1] if href else None
            full_url = BASE_URL + href if href.startswith("/") else href

            title_tag = card.select_one("p.title") or card.select_one(".title") or card.find(itemprop="name")
            title = title_tag.get_text(strip=True) if title_tag else ""

            price_tag = card.select_one(".price")
            price_text = price_tag.get_text(" ", strip=True) if price_tag else ""
            price_usd = parse_price(price_text)

            add_tag = card.select_one(".price-addition")
            price_kgs_text = add_tag.get_text(" ", strip=True) if add_tag else ""

            rooms, area, floor = parse_title(title)

            addr_tag = card.find(itemprop="address") or card.select_one(".address")
            address = addr_tag.get_text(strip=True) if addr_tag else ""

            img = card.find("img")
            photo = None
            if img:
                photo = img.get("src") or img.get("data-src")
                if photo and photo.startswith("//"):
                    photo = "https:" + photo
                elif photo and photo.startswith("/"):
                    photo = BASE_URL + photo

            if not ad_id or not price_usd or not area or area < 15:
                continue

            results.append({
                "id": ad_id,
                "title": title,
                "url": full_url,
                "price_usd": price_usd,
                "price_kgs_text": price_kgs_text,
                "rooms": rooms,
                "area": area,
                "floor": floor,
                "address": address,
                "photo": photo,
                "price_per_m2": price_usd / area if area else None
            })
        except Exception as e:
            print("Ошибка парсинга карточки:", e)
            continue

    return results

def get_market_price_per_m2(rooms, area, all_recent_listings):
    prices = []
    for item in all_recent_listings:
        if item["rooms"] != rooms:
            continue
        if not item["area"] or abs(item["area"] - area) > area * 0.25:
            continue
        if item["price_per_m2"] and 400 < item["price_per_m2"] < 4000:
            prices.append(item["price_per_m2"])

    if len(prices) < MIN_COMPARABLES:
        return None, len(prices)
    return statistics.median(prices), len(prices)

def analyze_and_notify(ad, seen, market_listings):
    ad_id = ad["id"]
    if ad_id in seen:
        return

    if not ad["price_per_m2"] or ad["price_usd"] < 15000:
        seen.add(ad_id)
        return

    market_ppm2, count = get_market_price_per_m2(ad["rooms"], ad["area"], market_listings)

    if not market_ppm2:
        seen.add(ad_id)
        return

    discount = (market_ppm2 - ad["price_per_m2"]) / market_ppm2

    if discount >= DISCOUNT_THRESHOLD:
        text = (
            f"🏠 <b>Выгодная квартира!</b>\n\n"
            f"<b>{ad['title']}</b>\n"
            f"📍 {ad['address'] or 'Бишкек'}\n"
            f"💰 Цена: <b>${ad['price_usd']:,.0f}</b>"
        )
        if ad["price_kgs_text"]:
            text += f" ({ad['price_kgs_text']})"
        text += (
            f"\n📐 Площадь: {ad['area']} м²\n"
            f"💵 Цена за м²: <b>${ad['price_per_m2']:.0f}</b>\n"
            f"📊 Рыночная за м²: \~${market_ppm2:.0f}\n"
            f"📉 Дешевле рынка на: <b>{discount*100:.1f}%</b>\n"
            f"🔍 Похожих объявлений: {count}\n\n"
            f"<a href='{ad['url']}'>Открыть объявление</a>"
        )

        send_telegram(text, ad.get("photo"))
        print(f"[{datetime.now()}] Отправлено: {ad['title'][:50]} | -{discount*100:.1f}%")

    seen.add(ad_id)

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Ошибка: не заданы BOT_TOKEN или CHAT_ID")
        return

    print("Бот по квартирам House.kg запущен...")
    send_telegram(
        "✅ Бот мониторинга квартир House.kg запущен\n"
        "• Бишкек + Чуйская область\n"
        "• Только продажа квартир\n"
        "• Порог: -15% от рыночной цены за м²\n"
        f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

    seen = load_seen()

    while True:
        try:
            print(f"[{datetime.now()}] Проверяю новые объявления...")

            all_listings = []
            for page in [1, 2]:
                page_listings = get_listings(page)
                all_listings.extend(page_listings)
                time.sleep(1.5)

            print(f"Получено объявлений: {len(all_listings)}")

            for ad in all_listings:
                analyze_and_notify(ad, seen, all_listings)

            save_seen(seen)
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print("Ошибка в основном цикле:", e)
            time.sleep(60)

if __name__ == "__main__":
    main()