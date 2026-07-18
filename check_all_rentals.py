#!/usr/bin/env python3
"""
check_all_rentals.py

Combined rental-listing checker covering all 5 working sites:
  - benhousing.nl        (Rotterdam area, under MAX_PRICE, "Te huur")
  - livresidential.nl    (Den Haag, under MAX_PRICE, "beschikbaar")
  - rentalrotterdam.nl   (all locations, under MAX_PRICE, "Te huur")
  - oudedelft.com        (all locations, under MAX_PRICE, not "VERHUURD")
  - rentvalley.nl        (Den Haag/Delft/Rotterdam, under MAX_PRICE, not "Rented")

(ikwilhuren.nu is excluded - never got that one working reliably.)

Writes ONE combined HTML report (report_all_rentals.html) with a "Bron"
(source) column, and keeps ONE combined seen.json so new listings from
ANY site get flagged as NEW regardless of source.

One-time setup:
    pip install playwright beautifulsoup4 requests --break-system-packages
    playwright install chromium

Then each time you want to check:
    python3 check_all_rentals.py

Open report_all_rentals.html afterwards.
Run this periodically (e.g. once or twice a day) via cron / Task Scheduler.
"""

import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ----------------------------------------------------------------------
# YOUR FILTER
# ----------------------------------------------------------------------
MAX_PRICE = 1500

SCRIPT_DIR = Path(__file__).resolve().parent
SEEN_FILE = SCRIPT_DIR / "seen_all_rentals.json"
REPORT_FILE = SCRIPT_DIR / "report_all_rentals.html"

COOKIE_BUTTON_TEXTS = ["Accepteren", "Akkoord", "Accept", "Alles accepteren", "OK", "Sluiten"]
LOAD_MORE_TEXTS = ["Load more", "Show more", "Meer laden", "Toon meer", "Laad meer", "Meer resultaten", "Volgende"]
MAX_LOAD_MORE_CLICKS = 30


def dismiss_cookie_banner(page):
    for text in COOKIE_BUTTON_TEXTS:
        try:
            btn = page.get_by_text(text, exact=False)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                page.wait_for_timeout(1500)
                return
        except Exception:
            continue


def click_load_more_until_gone(page, extra_texts=None):
    texts = LOAD_MORE_TEXTS + (extra_texts or [])
    clicks = 0
    while clicks < MAX_LOAD_MORE_CLICKS:
        clicked = False
        for text in texts:
            button = page.get_by_text(text, exact=False)
            try:
                if button.count() > 0 and button.first.is_visible():
                    button.first.click()
                    page.wait_for_timeout(1500)
                    clicks += 1
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            break
    return clicks


def parse_euro_amount(raw: str):
    """Shared euro-amount parser: '.' = thousands separator, ',' = decimal
    separator, trailing ',-' = whole euros."""
    raw = raw.strip().rstrip("-").strip().rstrip(",")
    if not raw:
        return None
    try:
        if "." in raw and "," in raw:
            integer_part, _, frac = raw.replace(".", "").partition(",")
            return float(f"{integer_part}.{frac}") if frac else float(integer_part)
        elif "," in raw:
            integer_part, _, frac = raw.partition(",")
            return float(f"{integer_part}.{frac}") if frac else float(integer_part)
        elif "." in raw:
            integer_part, _, frac = raw.partition(".")
            if len(frac) == 3 and frac.isdigit():
                return float(integer_part + frac)  # dot used as thousands separator
            return float(raw)
        else:
            return float(raw)
    except ValueError:
        return None


def format_price(price) -> str:
    if price == int(price):
        return f"{int(price):,}".replace(",", ".")
    return f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ========================================================================
# BEN HOUSING (benhousing.nl)
# ========================================================================
def scrape_benhousing():
    url = "https://www.benhousing.nl/aanbod/"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (personal rental-search script; contact: none)")
        page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(2000)
        click_load_more_until_gone(page, ["Laad meer"])
        html = page.content()
        browser.close()
    soup = BeautifulSoup(html, "html.parser")

    listings = {}
    detail_links = [
        a for a in soup.find_all("a", href=True)
        if re.search(r"/aanbod/[a-z0-9\-]+/?$", a["href"]) and a["href"].rstrip("/") != url.rstrip("/")
    ]

    for a in detail_links:
        href = a["href"]
        if href in listings:
            continue

        container = a
        text_block = ""
        for _ in range(8):
            container = container.parent
            if container is None:
                break
            text_block = container.get_text(" ", strip=True)
            if "Per maand" in text_block and ("Te huur" in text_block or "Verhuurd" in text_block):
                break
        if "Per maand" not in text_block:
            continue

        status = "Te huur" if re.search(r"\bTe huur\b", text_block) else (
            "Verhuurd" if re.search(r"\bVerhuurd\b", text_block) else "Onbekend"
        )
        if status != "Te huur":
            continue

        addr_match = re.match(r"^(.*?)\s*(?:Te huur|Verhuurd)", text_block)
        address = addr_match.group(1).strip().split(" - ")[-1].strip() if addr_match else href

        detail_match = re.search(
            r"(\d+)\s?m2\s*(\d+)?\s*([A-Za-zÀ-ÿ'\-\s]{2,40}?)\s*€\s?([\d.,]+)\s*Per maand\s*(incl|excl)\.?\s*g/w/e",
            text_block
        )
        size = bedrooms = wijk = price = None
        if detail_match:
            size = int(detail_match.group(1))
            bedrooms = int(detail_match.group(2)) if detail_match.group(2) else None
            wijk = detail_match.group(3).strip()
            try:
                price = int(detail_match.group(4).replace(".", "").replace(",", ""))
            except ValueError:
                price = None

        if price is None or price > MAX_PRICE:
            continue

        full_href = href if href.startswith("http") else f"https://www.benhousing.nl{href}"
        listings[href] = {
            "source": "Ben Housing", "title": address or href, "href": full_href,
            "location": wijk or "", "price": price, "size": size, "rooms": bedrooms, "extra": "",
        }

    return list(listings.values())


# ========================================================================
# LIV RESIDENTIAL (livresidential.nl) - Den Haag
# ========================================================================
def scrape_livresidential():
    city_pages = [
        "https://livresidential.nl/huurwoningen/den-haag",
        "https://livresidential.nl/huurwoningen/rotterdam",
        "https://livresidential.nl/huurwoningen/delft",
    ]
    city_keywords = ["den haag", "'s-gravenhage", "s-gravenhage", "gravenhage", "rotterdam", "delft"]

    pattern = re.compile(
        r'(\d{4}\s?[A-Z]{2})\s+([A-Za-zÀ-ÿ\'\-\s]+?)\s+\1\s+\2\s+€\s?([\d.,]+)\s*per maand\s*\((incl|excl)\.?\)\s*(\d+)\s?m\s?2\s*(\d+(?:\.\d+)?)\s?kamers?',
        re.IGNORECASE
    )
    NOT_AVAILABLE_STATUSES = {"Onder optie", "Verhuurd"}
    status_pattern = re.compile(r'^(Onder optie|Verhuurd|Beschikbaar vanaf \d{2}-\d{2}-\d{4}|Direct beschikbaar)\s*')

    def parse_city_page(soup, page_url):
        found = {}
        candidate_links = [
            a for a in soup.find_all("a", href=True)
            if "/huurwoningen/" in a["href"] and a["href"].rstrip("/") != page_url.rstrip("/")
        ]

        for a in candidate_links:
            href = a["href"]
            if href in found:
                continue
            text = a.get_text(" ", strip=True)
            m = pattern.search(text)
            if not m:
                continue

            status_m = status_pattern.match(text)
            if status_m:
                if status_m.group(1) in NOT_AVAILABLE_STATUSES:
                    continue
                addr_start = status_m.end()
            else:
                addr_start = 0
            address = text[addr_start:m.start()].strip()

            postcode, city = m.group(1), m.group(2)
            if not any(kw in city.lower() for kw in city_keywords):
                continue
            try:
                price = int(m.group(3).replace(".", "").replace(",", ""))
            except ValueError:
                price = None
            if price is None or price > MAX_PRICE:
                continue
            size = int(m.group(5))
            rooms = float(m.group(6))
            rooms = int(rooms) if rooms.is_integer() else rooms

            full_href = href if href.startswith("http") else f"https://livresidential.nl{href}"
            found[href] = {
                "source": "LIV Residential", "title": address, "href": full_href,
                "location": f"{postcode} {city}", "price": price, "size": size, "rooms": rooms, "extra": "",
            }
        return found

    all_listings = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (personal rental-search script; contact: none)")
        for url in city_pages:
            try:
                page.goto(url, wait_until="load", timeout=60000)
                page.wait_for_timeout(3000)
                dismiss_cookie_banner(page)
                click_load_more_until_gone(page)
                soup = BeautifulSoup(page.content(), "html.parser")
                all_listings.update(parse_city_page(soup, url))
            except Exception as e:
                print(f"    Error checking {url}: {e} - skipping this city, keeping results from others.")
        browser.close()

    return list(all_listings.values())


# ========================================================================
# RENTAL ROTTERDAM (rentalrotterdam.nl) - all locations
# ========================================================================
def scrape_rentalrotterdam():
    base_url = "https://www.rentalrotterdam.nl/woningaanbod/huur"
    headers = {"User-Agent": "Mozilla/5.0 (personal rental-search script; contact: none)"}
    page_size = 14
    max_pages = 20

    heading_pattern = re.compile(r'(\d{4}\s?[A-Z]{2})\s+([A-Za-zÀ-ÿ\'\-\s]+?)\s+€\s?([\d.,]+)-?\s*/mnd', re.IGNORECASE)
    detail_pattern = re.compile(r'(Appartement|Woonhuis|Parkeergelegenheid|Grond|Overig)\s+(\d+)(?:\s+\d+)*\s+(\d+)\s?m²', re.IGNORECASE)

    all_listings = {}
    skip = 0
    for _ in range(max_pages):
        url = f"{base_url}?moveunavailablelistingstothebottom=true&pricerange.maxprice={MAX_PRICE}&skip={skip}"
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        by_href = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/woningaanbod/huur/" not in href or href.rstrip("/") == base_url.rstrip("/"):
                continue
            by_href.setdefault(href, []).append(a)

        new_on_this_page = 0
        for href, anchors in by_href.items():
            if href in all_listings:
                continue
            texts = [a.get_text(" ", strip=True) for a in anchors]

            heading_anchor = heading_match = None
            for a, t in zip(anchors, texts):
                m = heading_pattern.search(t)
                if m:
                    heading_anchor, heading_match = a, m
                    break
            if heading_anchor is None:
                continue  # no price shown - rented

            postcode, city = heading_match.group(1), heading_match.group(2)
            address = texts[anchors.index(heading_anchor)][:heading_match.start()].strip()
            try:
                raw = heading_match.group(3).replace(".", "")
                price = int(raw.split(",")[0])
            except ValueError:
                price = None
            if price is None or price > MAX_PRICE:
                continue

            container = heading_anchor
            detail_match = None
            container_text = ""
            for _ in range(6):
                container = container.parent
                if container is None:
                    break
                container_text = container.get_text(" ", strip=True)
                detail_match = detail_pattern.search(container_text)
                if detail_match:
                    break
            if re.search(r'\bVerhuurd\b', container_text):
                continue

            object_type = rooms = size = None
            if detail_match:
                object_type = detail_match.group(1)
                rooms = int(detail_match.group(2))
                size = int(detail_match.group(3))

            full_href = href if href.startswith("http") else f"https://www.rentalrotterdam.nl{href}"
            all_listings[href] = {
                "source": "Rental Rotterdam", "title": address or href, "href": full_href,
                "location": f"{postcode} {city}", "price": price, "size": size, "rooms": rooms,
                "extra": object_type or "",
            }
            new_on_this_page += 1

        if new_on_this_page == 0:
            break
        skip += page_size

    return list(all_listings.values())


# ========================================================================
# OUDE DELFT (oudedelft.com) - all locations
# ========================================================================
def scrape_oudedelft():
    url = "https://oudedelft.com/huur-2/"
    nav_slugs = {
        "en", "huur-2", "woning-aanbod", "koop", "diensten", "huis-verkopen",
        "huis-verhuren", "beheer", "investeren", "expats", "inschrijven",
        "over-ons", "vacatures", "contact", "gratis-waardebepaling",
        "privacy-policy", "cookiebeleid-eu", "cookie-policy",
    }
    price_pattern = re.compile(r'€?\s?([\d.,]+)-?\s*(?:incl|excl)\.?', re.IGNORECASE)
    rooms_pattern = re.compile(r'(\d+)\s?(?:bedroom|bedrooms|slaapkamer|slaapkamers)\b', re.IGNORECASE)
    size_pattern = re.compile(r'(\d+)\s?m[²2]\b', re.IGNORECASE)
    furnished_pattern = re.compile(r'\b(unfurnished|furnished|gemeubileerd|ongemeubileerd|gestoffeerd)\b', re.IGNORECASE)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (personal rental-search script; contact: none)")
        page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        dismiss_cookie_banner(page)
        for _ in range(5):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(800)
        page.wait_for_timeout(3000)
        click_load_more_until_gone(page)
        html = page.content()
        browser.close()
    soup = BeautifulSoup(html, "html.parser")

    def is_listing_href(href):
        m = re.match(r'^https://oudedelft\.com/([a-z0-9\-]+)/?$', href)
        return bool(m) and m.group(1) not in nav_slugs and bool(m.group(1))

    def slug_to_title(href):
        return href.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()

    listings = {}
    candidate_hrefs = {a["href"] for a in soup.find_all("a", href=True) if is_listing_href(a["href"])}

    for href in candidate_hrefs:
        anchor = soup.find("a", href=href)
        if anchor is None:
            continue
        container = anchor
        container_text = ""
        for _ in range(8):
            container = container.parent
            if container is None:
                break
            container_text = container.get_text(" ", strip=True)
            if "VERHUURD" in container_text.upper() or price_pattern.search(container_text):
                break

        if "VERHUURD" in container_text.upper():
            continue

        price_match = price_pattern.search(container_text)
        if not price_match:
            continue
        price = parse_euro_amount(price_match.group(1))
        if price is None or price > MAX_PRICE:
            continue

        rooms_match = rooms_pattern.search(container_text)
        size_match = size_pattern.search(container_text)
        furnished_match = furnished_pattern.search(container_text)

        listings[href] = {
            "source": "Oude Delft", "title": slug_to_title(href), "href": href,
            "location": "", "price": price,
            "size": int(size_match.group(1)) if size_match else None,
            "rooms": int(rooms_match.group(1)) if rooms_match else None,
            "extra": furnished_match.group(1).capitalize() if furnished_match else "",
        }

    return list(listings.values())


# ========================================================================
# RENT VALLEY (rentvalley.nl) - Den Haag / Delft / Rotterdam
# ========================================================================
def scrape_rentvalley():
    url = "https://rentvalley.nl/en/listings/"
    cities = ["Den Haag", "Delft", "Rotterdam"]
    nav_slugs = {"register", "procedure", "about-us", "faq", "contact", "listings"}

    status_pattern = re.compile(r'^(Rented with reservation|Rented)\s+', re.IGNORECASE)
    city_price_pattern = re.compile(
        r'\b(' + "|".join(re.escape(c) for c in cities) + r')\b\s*€\s?([\d.,]+),?-?', re.IGNORECASE
    )
    sep = r'[\s·:]*'
    detail_type_pattern = re.compile(
        rf'\bType{sep}([A-Za-z][A-Za-z, ]*?){sep}(?:Construction year|Rooms|Bedrooms|Living surface|Available from)',
        re.IGNORECASE
    )
    detail_rooms_pattern = re.compile(rf'\bRooms{sep}(\d+)\b')
    detail_bedrooms_pattern = re.compile(rf'\bBedrooms{sep}(\d+)\b')
    detail_surface_pattern = re.compile(rf'\bLiving surface{sep}(\d+)\s?m', re.IGNORECASE)

    def is_listing_href(href):
        path = href
        m = re.match(r'^https://rentvalley\.nl/en/(.*)$', href)
        if m:
            path = m.group(1)
        elif href.startswith("http"):
            return False
        slug_match = re.match(r'^([a-z0-9\-]+)\.html/?$', path)
        return bool(slug_match) and slug_match.group(1) not in nav_slugs

    def to_full_url(href):
        return href if href.startswith("http") else f"https://rentvalley.nl/en/{href}"

    def clean_title(address):
        tokens = address.split()
        n = len(tokens)
        if n % 2 == 0:
            half = n // 2
            first, second = tokens[:half], tokens[half:]
            if first == second or (first[:-1] == second[:-1] and second[-1].startswith(first[-1])):
                return " ".join(second)
        return address

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (personal rental-search script; contact: none)")
        page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        dismiss_cookie_banner(page)
        click_load_more_until_gone(page)
        html = page.content()

        listings = {}
        candidate_anchors = [a for a in BeautifulSoup(html, "html.parser").find_all("a", href=True) if is_listing_href(a["href"])]
        for a in candidate_anchors:
            href = a["href"]
            if href in listings:
                continue
            text = a.get_text(" ", strip=True)
            if status_pattern.match(text):
                continue
            m = city_price_pattern.search(text)
            if not m:
                continue
            city = m.group(1)
            price = parse_euro_amount(m.group(2))
            if price is None or price > MAX_PRICE:
                continue
            address = clean_title(text[:m.start()].strip())
            listings[href] = {
                "source": "Rent Valley", "title": address, "href": to_full_url(href),
                "location": city, "price": price, "size": None, "rooms": None, "extra": "",
            }

        # Enrich matches with Type/Rooms/Living surface from each detail page
        for listing in listings.values():
            try:
                page.goto(listing["href"], wait_until="load", timeout=45000)
                page.wait_for_timeout(1000)
                detail_text = page.inner_text("body")
            except Exception:
                continue
            type_m = detail_type_pattern.search(detail_text)
            rooms_m = detail_rooms_pattern.search(detail_text)
            bedrooms_m = detail_bedrooms_pattern.search(detail_text)
            surface_m = detail_surface_pattern.search(detail_text)
            if type_m:
                listing["extra"] = type_m.group(1).strip().rstrip(",").strip()
            if rooms_m:
                listing["rooms"] = int(rooms_m.group(1))
            if bedrooms_m and listing["extra"]:
                listing["extra"] += f" ({bedrooms_m.group(1)} bed)"
            if surface_m:
                listing["size"] = int(surface_m.group(1))

        browser.close()

    return list(listings.values())


# ========================================================================
# VERRA MAKELAARS (verra.nl) - Den Haag / Delft / Rotterdam
# ========================================================================
def scrape_verra():
    url = "https://www.verra.nl/en/listings/rental?salesRentals=rentals"
    city_slugs = {"rotterdam", "delft", "den-haag"}
    max_pages = 25

    href_pattern = re.compile(r'^/en/listings/residential/([a-z\-]+)/([a-z0-9\-]+)/([a-f0-9]+)$')
    price_pattern = re.compile(r'€\s?([\d.,]+)\s*p\.m\.\s*(?:ex|incl)\.?', re.IGNORECASE)
    furnished_pattern = re.compile(r'(Fully furnished|Partly furnished|Unfurnished|Decorated|Shell)', re.IGNORECASE)
    availability_pattern = re.compile(r'(Directly|In consultation|\d{2}-\d{2}-\d{4})', re.IGNORECASE)

    def extract_from_soup(soup):
        found = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = href_pattern.match(href)
            if not m:
                continue
            city_slug = m.group(1)
            if city_slug not in city_slugs or href in found:
                continue

            text = a.get_text(" ", strip=True)
            price_m = price_pattern.search(text)
            if not price_m:
                continue
            price = parse_euro_amount(price_m.group(1))
            if price is None or price > MAX_PRICE:
                continue

            rest = text[price_m.end():]
            bedrooms_m = re.match(r'\s*(\d+)\b', rest)
            furnished_m = furnished_pattern.search(rest)
            availability_m = availability_pattern.search(rest)

            title = text[:price_m.start()].strip()
            city_display = city_slug.replace("-", " ").title()
            if title.lower().startswith(city_display.lower()):
                title = title[len(city_display):].strip()

            extra_parts = [p for p in [
                furnished_m.group(1) if furnished_m else "",
                availability_m.group(1) if availability_m else "",
            ] if p]

            found[href] = {
                "source": "Verra Makelaars", "title": title or href,
                "href": f"https://www.verra.nl{href}", "location": city_display,
                "price": price, "size": None,
                "rooms": int(bedrooms_m.group(1)) if bedrooms_m else None,
                "extra": " · ".join(extra_parts),
            }
        return found

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (personal rental-search script; contact: none)")
        page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        dismiss_cookie_banner(page)
        page.wait_for_timeout(2000)

        all_found = {}
        all_found.update(extract_from_soup(BeautifulSoup(page.content(), "html.parser")))

        page_num = 2
        while page_num <= max_pages:
            link = page.locator(f'a[href="#{page_num}"]')
            if link.count() == 0:
                break
            try:
                link.first.click()
                page.wait_for_timeout(2000)
            except Exception:
                break
            all_found.update(extract_from_soup(BeautifulSoup(page.content(), "html.parser")))
            page_num += 1

        browser.close()

    return list(all_found.values())


# ========================================================================
# VAN WEELDE VASTGOED (vanweeldevastgoed.nl) - Den Haag / Delft / Rotterdam
# ========================================================================
def scrape_vanweelde():
    base_url = "https://vanweeldevastgoed.nl/woningen/"
    headers = {"User-Agent": "Mozilla/5.0 (personal rental-search script; contact: none)"}
    city_keywords = ["delft", "den haag", "'s-gravenhage", "s-gravenhage", "gravenhage", "rotterdam"]
    max_pages_safety_cap = 40

    detail_href_pattern = re.compile(r'^https://vanweeldevastgoed\.nl/woningen/([a-z0-9\-]+)/?$')
    price_pattern = re.compile(r'€\s?([\d.,]+)')
    location_pattern = re.compile(r'(\d{4}\s?[A-Z]{2})\s+([A-Za-zÀ-ÿ\'\-\s]+)$')

    def fetch_page(page_num):
        url = base_url if page_num == 1 else f"{base_url}page/{page_num}/"
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    def get_max_page(soup):
        max_page = 1
        for a in soup.find_all("a", href=True):
            m = re.search(r'/woningen/page/(\d+)/?$', a["href"])
            if m:
                max_page = max(max_page, int(m.group(1)))
        return max_page

    def parse_page(soup):
        found = {}
        by_href = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if detail_href_pattern.match(href):
                by_href.setdefault(href, []).append(a)

        for href, anchors in by_href.items():
            if href in found:
                continue
            texts = [a.get_text(" ", strip=True) for a in anchors]

            price = None
            for t in texts:
                m = price_pattern.search(t)
                if m:
                    price = parse_euro_amount(m.group(1))
                    break
            if price is None or price > MAX_PRICE:
                continue

            container = anchors[0]
            container_text = ""
            for _ in range(6):
                container = container.parent
                if container is None:
                    break
                container_text = container.get_text(" ", strip=True)
                if re.search(r'\b(Huur|Koop)\b', container_text):
                    break

            if not re.search(r'\bHuur\b', container_text):
                continue
            if not any(kw in container_text.lower() for kw in city_keywords):
                continue

            title = href.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
            location = ""
            for t in texts:
                if re.search(r'\d{4}\s?[A-Z]{2}', t):
                    title = t
                    loc_m = location_pattern.search(t)
                    if loc_m:
                        location = f"{loc_m.group(1)} {loc_m.group(2)}"
                    break

            found[href] = {
                "source": "Van Weelde Vastgoed", "title": title, "href": href,
                "location": location, "price": price, "size": None, "rooms": None, "extra": "",
            }
        return found

    all_listings = {}
    first_soup = fetch_page(1)
    if first_soup is None:
        return []
    max_page = get_max_page(first_soup)
    all_listings.update(parse_page(first_soup))

    for page_num in range(2, min(max_page, max_pages_safety_cap) + 1):
        soup = fetch_page(page_num)
        if soup is None:
            break
        all_listings.update(parse_page(soup))

    return list(all_listings.values())


# ========================================================================
# IKWILHUREN.NU - Den Haag / Delft / Rotterdam (using the site's own location filter)
# ========================================================================
def scrape_ikwilhuren():
    url = "https://ikwilhuren.nu/aanbod/"
    city_queries = [
        ("'s-Gravenhage, Zuid-Holland", "gravenhage"),
        ("Delft, Zuid-Holland", "delft"),
        ("Rotterdam, Zuid-Holland", "rotterdam"),
    ]
    max_select_attempts = 5
    max_pages_safety_cap = 20

    status_pattern = re.compile(r'(Direct beschikbaar|Beschikbaar vanaf \d{2}-\d{2}-\d{2})')
    location_pattern = re.compile(
        r'\b(\d{4}\s?[A-Z]{2})\s+([A-Za-zÀ-ÿ0-9\'\-\.\s]{2,40}?)(?=\s+(?:Direct beschikbaar|Beschikbaar vanaf|Te huur|Verhuurd|$))'
    )
    price_pattern = re.compile(r'€\s?([\d.,]+),-\s*/mnd')
    size_pattern = re.compile(r'(\d+)\s?m2')
    rooms_pattern = re.compile(r'(\d+)\s?slaapkamer')
    distance_suffix_pattern = re.compile(r'\s*-\s*\d+\s?Km\.?$')

    def try_select_city(page, city_query, match_keyword):
        try:
            container = page.locator("#select2-selAdres-container")
            container.click(timeout=15000)
            page.wait_for_timeout(800)

            search_field = page.locator(".select2-search__field")
            if search_field.count() == 0:
                return False

            search_field.first.fill(city_query)
            page.wait_for_timeout(2500)  # a bit more slack for slower AJAX in CI

            options = page.locator(".select2-results__option")
            option_count = options.count()
            if option_count == 0:
                return False

            chosen_index = 0
            for i in range(option_count):
                try:
                    if match_keyword in options.nth(i).inner_text().lower():
                        chosen_index = i
                        break
                except Exception:
                    continue

            for _ in range(chosen_index):
                search_field.first.press("ArrowDown")
                page.wait_for_timeout(150)
            search_field.first.press("Enter")
            page.wait_for_timeout(1500)

            underlying_value = page.locator("#selAdres").input_value()
            displayed_text = page.locator("#select2-selAdres-container").inner_text()
            if underlying_value or match_keyword in displayed_text.lower():
                return True
        except Exception:
            pass
        return False

    def parse_page(soup):
        found = {}
        object_links = [a for a in soup.find_all("a", href=True) if "/object/" in a["href"]]

        for a in object_links:
            href = a["href"]
            title_text = a.get_text(strip=True)

            if href in found:
                if title_text and not found[href]["_has_real_title"]:
                    found[href]["title"] = title_text
                    found[href]["_has_real_title"] = True
                continue

            container = a
            text_block = ""
            for _ in range(10):
                container = container.parent
                if container is None:
                    break
                text_block = container.get_text(" ", strip=True)
                if "/mnd" in text_block:
                    break
            if "/mnd" not in text_block:
                continue

            title = title_text or href

            status_container = a
            status_text = text_block
            found_status = bool(re.search(r"\bTe huur\b|\bVerhuurd\b", status_text))
            climb_count = 0
            while not found_status and climb_count < 14:
                status_container = status_container.parent
                if status_container is None:
                    break
                status_text = status_container.get_text(" ", strip=True)
                found_status = bool(re.search(r"\bTe huur\b|\bVerhuurd\b", status_text))
                climb_count += 1

            if not re.search(r"\bTe huur\b", status_text):
                continue

            loc_match = location_pattern.search(text_block)
            postcode = loc_match.group(1) if loc_match else ""
            city = distance_suffix_pattern.sub("", loc_match.group(2)).strip() if loc_match else ""

            availability_match = status_pattern.search(text_block)
            availability = availability_match.group(1) if availability_match else ""

            price_match = price_pattern.search(text_block)
            price = None
            if price_match:
                try:
                    price = int(price_match.group(1).replace(".", ""))
                except ValueError:
                    price = None
            if price is None or price > MAX_PRICE:
                continue

            size_match = size_pattern.search(text_block)
            rooms_match = rooms_pattern.search(text_block)

            full_href = href if href.startswith("http") else f"https://ikwilhuren.nu{href}"
            found[href] = {
                "source": "ikwilhuren.nu", "title": title, "_has_real_title": bool(title_text),
                "href": full_href, "location": f"{postcode} {city}".strip(), "price": price,
                "size": int(size_match.group(1)) if size_match else None,
                "rooms": int(rooms_match.group(1)) if rooms_match else None,
                "extra": availability,
            }

        for l in found.values():
            l.pop("_has_real_title", None)
        return found

    def fetch_for_city(page, city_query, match_keyword):
        """Returns (success, listings). success=False means city selection
        itself failed after all inner attempts (as opposed to succeeding
        but genuinely finding 0 listings) - the caller uses this to decide
        whether a full browser restart is worth trying."""
        page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3500)  # extra settle time - cloud runners can be slower to finish rendering
        dismiss_cookie_banner(page)
        page.wait_for_timeout(500)

        success = False
        for attempt in range(1, max_select_attempts + 1):
            print(f"    Selecting '{city_query}' (attempt {attempt}/{max_select_attempts})...")
            if try_select_city(page, city_query, match_keyword):
                print("      Success.")
                success = True
                break
            print("      Didn't stick, retrying...")

        if not success:
            return False, {}

        try:
            toon_btn = page.get_by_text("Toon resultaten", exact=False)
            if toon_btn.count() > 0 and toon_btn.first.is_visible():
                toon_btn.first.click()
                page.wait_for_timeout(2500)
        except Exception:
            pass

        city_listings = {}
        city_listings.update(parse_page(BeautifulSoup(page.content(), "html.parser")))

        page_num = 1
        while page_num < max_pages_safety_cap:
            next_btn = page.get_by_text("Volgende", exact=False)
            try:
                if next_btn.count() == 0 or not next_btn.first.is_visible() or not next_btn.first.is_enabled():
                    break
                next_btn.first.click()
                page.wait_for_timeout(2000)
            except Exception:
                break
            new_listings = parse_page(BeautifulSoup(page.content(), "html.parser"))
            before = len(city_listings)
            city_listings.update(new_listings)
            page_num += 1
            if len(city_listings) - before == 0:
                break

        return True, city_listings

    max_browser_restarts = 2  # if all 5 inner select attempts fail, try again with a completely fresh browser

    all_listings = {}
    for city_query, match_keyword in city_queries:
        print(f"  Checking '{city_query}'...")
        succeeded = False
        for restart_num in range(1, max_browser_restarts + 1):
            if restart_num > 1:
                pause_seconds = 20
                print(f"    Pausing {pause_seconds}s before restarting with a fresh browser (attempt {restart_num}/{max_browser_restarts})...")
                time.sleep(pause_seconds)
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page(user_agent="Mozilla/5.0 (personal rental-search script; contact: none)")
                    success, city_listings = fetch_for_city(page, city_query, match_keyword)
                    browser.close()
            except Exception as e:
                print(f"    Error during browser session: {e}")
                success, city_listings = False, {}

            if success:
                all_listings.update(city_listings)
                succeeded = True
                break

        if not succeeded:
            print(f"    Could not select '{city_query}' even after {max_browser_restarts} fresh browser attempt(s). Skipping this city.")

    return list(all_listings.values())


# ========================================================================
# 070 WONEN (070wonen.nl) - Den Haag area
# ========================================================================
def scrape_070wonen():
    url = f"https://070wonen.nl/?action=epl_search&post_type=rental&property_status=&property_location=&property_price_from=&property_price_to={MAX_PRICE}"
    headers = {"User-Agent": "Mozilla/5.0 (personal rental-search script; contact: none)"}

    detail_href_pattern = re.compile(r'^https://070wonen\.nl/huurwoningen/[a-z0-9\-]+/[a-z0-9\-]+/?$')
    price_pattern = re.compile(r'€\s?([\d.,]+)\s*/\s*[Mm]aand')
    size_pattern = re.compile(r'(\d+)\s?m2')
    rooms_pattern = re.compile(r'(\d+)\s?slaapkamer')
    availability_pattern = re.compile(r'(Per direct|Vanaf \d{2}-\d{2}-\d{4})')
    furnished_pattern = re.compile(r'\b(Gestoffeerd|Gemeubileerd|Kaal)\b')
    not_available_pattern = re.compile(r'\b(Onder optie|Verkocht|Verhuurd)\b')

    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    listings = {}
    by_href = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if detail_href_pattern.match(href):
            by_href.setdefault(href, []).append(a)

    for href, anchors in by_href.items():
        if href in listings:
            continue

        title = href.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        for a in anchors:
            t = a.get_text(strip=True)
            if t and t.lower() != "meer informatie":
                title = t
                break

        container = anchors[0]
        container_text = ""
        for _ in range(8):
            container = container.parent
            if container is None:
                break
            container_text = container.get_text(" ", strip=True)
            if "/maand" in container_text.lower():
                break
        if "/maand" not in container_text.lower():
            continue

        card = anchors[0].find_parent(["article", "li"])
        status_text = card.get_text(" ", strip=True) if card is not None else container_text
        if not_available_pattern.search(status_text):
            continue

        price_match = price_pattern.search(container_text)
        if not price_match:
            continue
        price = parse_euro_amount(price_match.group(1))
        if price is None or price > MAX_PRICE:
            continue

        size_match = size_pattern.search(container_text)
        rooms_match = rooms_pattern.search(container_text)
        availability_match = availability_pattern.search(container_text)
        furnished_matches = furnished_pattern.findall(container_text)

        listings[href] = {
            "source": "070 Wonen", "title": title, "href": href,
            "location": "'s-Gravenhage", "price": price,
            "size": int(size_match.group(1)) if size_match else None,
            "rooms": int(rooms_match.group(1)) if rooms_match else None,
            "extra": ", ".join(dict.fromkeys(furnished_matches)) +
                     (f" · {availability_match.group(1)}" if availability_match else ""),
        }

    return list(listings.values())


# ========================================================================
# HEKKING NVM (hekking.nl) - Den Haag / Delft / Rotterdam
# ========================================================================
def scrape_hekking():
    url = "https://www.hekking.nl/en/listings/rental?salesRentals=rentals"
    city_slugs = {"rotterdam", "delft", "den-haag"}

    href_pattern = re.compile(r'^/en/listings/([a-z\-]+)/([a-z0-9\-]+)/([a-f0-9]+)/?$')
    price_pattern = re.compile(r'€\s?([\d.,]+)\s*p\.m\.\s*(?:ex|incl)\.?', re.IGNORECASE)
    furnished_pattern = re.compile(r'(Fully furnished|Partly furnished|Unfurnished|Decorated|Shell)', re.IGNORECASE)
    availability_pattern = re.compile(r'(Directly|In consultation|\d{2}-\d{2}-\d{4})', re.IGNORECASE)
    not_available_pattern = re.compile(r'\b(Rented|Under offer|Under bid|Sold)\b', re.IGNORECASE)

    def extract_from_soup(soup):
        found = {}
        by_href = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = href_pattern.match(href)
            if not m or m.group(1) not in city_slugs:
                continue
            by_href.setdefault(href, []).append(a)

        for href, anchors in by_href.items():
            m = href_pattern.match(href)
            city_slug = m.group(1)
            listing_slug = m.group(2)
            city_display = city_slug.replace("-", " ").title()

            texts = [a.get_text(" ", strip=True) for a in anchors]
            title = None
            for t in texts:
                if t and not not_available_pattern.fullmatch(t.strip()):
                    title = t
                    break
            if title is None:
                title = listing_slug.replace("-", " ").title()

            card = anchors[0].find_parent(["article", "li"])
            if card is not None:
                container_text = card.get_text(" ", strip=True)
            else:
                container = anchors[0]
                container_text = ""
                for _ in range(8):
                    container = container.parent
                    if container is None:
                        break
                    container_text = container.get_text(" ", strip=True)
                    if "p.m." in container_text.lower():
                        break
            if "p.m." not in container_text.lower():
                continue

            if not_available_pattern.search(container_text):
                continue

            price_m = price_pattern.search(container_text)
            if not price_m:
                continue
            price = parse_euro_amount(price_m.group(1))
            if price is None or price > MAX_PRICE:
                continue

            rest = container_text[price_m.end():]
            bedrooms_m = re.match(r'\s*(\d+)\b', rest)
            furnished_m = furnished_pattern.search(container_text)
            availability_m = availability_pattern.search(container_text)

            extra_parts = [p for p in [
                furnished_m.group(1) if furnished_m else "",
                availability_m.group(1) if availability_m else "",
            ] if p]

            found[href] = {
                "source": "Hekking NVM", "title": title,
                "href": f"https://www.hekking.nl{href}", "location": city_display,
                "price": price, "size": None,
                "rooms": int(bedrooms_m.group(1)) if bedrooms_m else None,
                "extra": " · ".join(extra_parts),
            }
        return found

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (personal rental-search script; contact: none)")

        # Cold direct loads to this URL render the Dutch blog page instead of
        # the listings, despite the URL bar showing the right address -
        # visiting the homepage first "warms up" the session enough for the
        # direct navigation afterward to actually work.
        page.goto("https://www.hekking.nl/", wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        dismiss_cookie_banner(page)

        if "/nl" in page.url:
            try:
                en_link = page.get_by_text("EN", exact=True)
                if en_link.count() > 0 and en_link.first.is_visible():
                    en_link.first.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass

        page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)

        all_listings = extract_from_soup(BeautifulSoup(page.content(), "html.parser"))
        browser.close()

    return list(all_listings.values())


# ========================================================================
# NRW WONEN (nrw-wonen.nl) - all locations
# ========================================================================
def scrape_nrwwonen():
    url = "https://nrw-wonen.nl/huur-aanbod/"
    detail_href_pattern = re.compile(r'^/aanbod/huis/\d+/?$')

    # Entire listing is packed into one anchor's text:
    # "{address} {status} {postcode} {city} € {price},= p/m {rooms} {bathrooms} {size}"
    listing_pattern = re.compile(
        r'^(.*?)\s*\b(te huur|on hold|bezichtiging vol)\b\s*'
        r'(\d{4}\s?[A-Z]{2})\s+([A-Za-zÀ-ÿ\'\-\s]+?)\s*'
        r'€\s?([\d.,]+),=\s*p/m\s*'
        r'(\d+)\s+(\d+)\s+(\d+)',
        re.IGNORECASE
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (personal rental-search script; contact: none)")
        page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        dismiss_cookie_banner(page)
        click_load_more_until_gone(page, ["Laad meer", "Meer laden", "Toon meer"])
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    listings = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not detail_href_pattern.match(href) or href in listings:
            continue

        text = a.get_text(" ", strip=True)
        m = listing_pattern.match(text)
        if not m:
            continue

        address = m.group(1).strip()
        status = m.group(2).strip().lower()
        if status != "te huur":
            continue  # excludes "on hold" and "bezichtiging vol"

        postcode = m.group(3)
        city = m.group(4).strip()
        price = parse_euro_amount(m.group(5))
        if price is None or price > MAX_PRICE:
            continue

        rooms = int(m.group(6))
        size = int(m.group(8))

        listings[href] = {
            "source": "NRW Wonen", "title": address, "href": f"https://nrw-wonen.nl{href}",
            "location": f"{postcode} {city}", "price": price, "size": size, "rooms": rooms, "extra": "",
        }

    return list(listings.values())


# ========================================================================
# TOUW VASTGOED (touwvastgoed.nl) - excludes Schiedam Centrum / Bergschenhoek
# ========================================================================
def scrape_touwvastgoed():
    url = "https://touwvastgoed.nl/aanbod/"
    headers = {"User-Agent": "Mozilla/5.0 (personal rental-search script; contact: none)"}
    excluded_neighbourhood_keywords = ["schiedam", "bergschenhoek"]

    nav_slugs = {
        "", "aanbod", "huren", "verhuren", "vastgoedbeheer", "contact",
        "maak-kennis-met-de-de-meest-betrouwbare-verhuurmakelaar-van-rotterdam",
    }
    detail_href_pattern = re.compile(r'^https://touwvastgoed\.nl/([a-z0-9\-]+)/?$')
    size_pattern = re.compile(r'(\d+)\s?m2', re.IGNORECASE)
    rooms_pattern = re.compile(r'(\d+)\s+kamers?\b', re.IGNORECASE)
    bedrooms_pattern = re.compile(r'(\d+)\s+slaapkamers?\b', re.IGNORECASE)
    price_pattern = re.compile(r'€\s?([\d.,]+)')

    def is_listing_href(href):
        m = detail_href_pattern.match(href)
        return bool(m) and m.group(1) not in nav_slugs

    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    listings = {}
    by_href = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if is_listing_href(href):
            by_href.setdefault(href, []).append(a)

    for href, anchors in by_href.items():
        if href in listings:
            continue

        title = href.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        for a in anchors:
            t = a.get_text(strip=True)
            if t and t.lower() != "lees meer":
                title = t
                break

        card = anchors[0].find_parent(["article", "li"])
        if card is not None:
            container_text = card.get_text(" ", strip=True)
        else:
            container = anchors[0]
            container_text = ""
            for _ in range(8):
                container = container.parent
                if container is None:
                    break
                container_text = container.get_text(" ", strip=True)
                if "kamers" in container_text.lower():
                    break

        if "verhuurd" in container_text.lower():
            continue  # rented

        price_match = price_pattern.search(container_text)
        if not price_match:
            continue
        price = parse_euro_amount(price_match.group(1))
        if price is None or price > MAX_PRICE:
            continue

        neighbourhood = ""
        size_match = size_pattern.search(container_text)
        if size_match:
            before_size = container_text[:size_match.start()]
            neighbourhood = before_size.split("|", 1)[0].strip()
            if title and neighbourhood.lower().startswith(title.lower()):
                neighbourhood = neighbourhood[len(title):].strip()

        if any(kw in neighbourhood.lower() for kw in excluded_neighbourhood_keywords):
            continue

        rooms_match = rooms_pattern.search(container_text)
        bedrooms_match = bedrooms_pattern.search(container_text)

        listings[href] = {
            "source": "Touw Vastgoed", "title": title, "href": href,
            "location": neighbourhood, "price": price,
            "size": int(size_match.group(1)) if size_match else None,
            "rooms": int(bedrooms_match.group(1)) if bedrooms_match else (int(rooms_match.group(1)) if rooms_match else None),
            "extra": "",
        }

    return list(listings.values())


# ========================================================================
# Seen-tracking, report building, main
# ========================================================================
def seen_key(listing):
    return f"{listing['source']}::{listing['href']}"


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False))


def build_html(matches, checked_at):
    now = datetime.now()

    def badge_tier(listing):
        """Returns 'new' (<24h), 'recent' (24-48h), or None (older), based on
        how long ago this listing was first seen - not just whether it was
        found in this specific run, so the badge survives multiple runs."""
        first_seen = listing.get("first_seen")
        if not first_seen:
            return None
        try:
            first_seen_dt = datetime.strptime(first_seen, "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        age = now - first_seen_dt
        if age < timedelta(days=1):
            return "new"
        elif age < timedelta(days=2):
            return "recent"
        return None

    def row(listing, show_source=False):
        tier = badge_tier(listing)
        if tier == "new":
            badge = '<span class="new-badge">NIEUW</span>'
            row_class = "is-new"
        elif tier == "recent":
            badge = '<span class="recent-badge">NIEUW</span>'
            row_class = "is-recent"
        else:
            badge = ""
            row_class = ""
        size = f'{listing["size"]} m²' if listing.get("size") else "–"
        rooms = f'{listing["rooms"]}' if listing.get("rooms") is not None else "–"
        price_str = format_price(listing["price"])
        source_cell = f"<td data-source=\"{listing['source']}\"><span class=\"source-badge\">{listing['source']}</span></td>" if show_source else ""
        added = listing.get("first_seen", checked_at)
        return f"""
        <tr class="{row_class}"
            data-location="{listing['location']}"
            data-price="{listing['price']}"
            data-size="{listing.get('size') if listing.get('size') is not None else ''}"
            data-rooms="{listing.get('rooms') if listing.get('rooms') is not None else ''}"
            data-added="{added}"
            data-source="{listing['source']}">
          {source_cell}
          <td>{badge}<a href="{listing['href']}" target="_blank">{listing['title']}</a></td>
          <td>{listing['location']}</td>
          <td>€ {price_str}</td>
          <td>{size}</td>
          <td>{rooms}</td>
          <td>{listing.get('extra') or '–'}</td>
          <td>{added}</td>
        </tr>"""

    def table(items, show_source=False):
        if not items:
            return '<div class="empty">Geen woningen gevonden die aan je filters voldoen.</div>'
        header_source = '<th class="sortable" data-key="source" onclick="sortTable(this)">Bron</th>' if show_source else ""
        rows = "\n".join(row(l, show_source) for l in sorted(items, key=lambda x: x["price"]))
        return f"""<table>
  <thead>
    <tr>
      {header_source}
      <th>Woning</th>
      <th class="sortable" data-key="location" onclick="sortTable(this)">Locatie</th>
      <th class="sortable" data-key="price" data-numeric="1" onclick="sortTable(this)">Prijs</th>
      <th class="sortable" data-key="size" data-numeric="1" onclick="sortTable(this)">Oppervlakte</th>
      <th class="sortable" data-key="rooms" data-numeric="1" onclick="sortTable(this)">Kamers</th>
      <th>Extra</th>
      <th class="sortable" data-key="added" onclick="sortTable(this)">Toegevoegd</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""

    # Group matches by source, preserving a sensible fixed order
    source_order = ["Ben Housing", "LIV Residential", "Rental Rotterdam", "Oude Delft", "Rent Valley",
                    "Verra Makelaars", "Van Weelde Vastgoed", "ikwilhuren.nu", "070 Wonen", "Hekking NVM",
                    "NRW Wonen", "Touw Vastgoed"]
    by_source = {s: [] for s in source_order}
    for l in matches:
        by_source.setdefault(l["source"], []).append(l)

    new_count = sum(1 for l in matches if badge_tier(l) == "new")
    recent_count = sum(1 for l in matches if badge_tier(l) == "recent")
    meta_extra = ""
    if new_count or recent_count:
        parts = []
        if new_count:
            parts.append(f"{new_count} nieuw (laatste 24u)")
        if recent_count:
            parts.append(f"{recent_count} recent (1-2 dagen)")
        meta_extra = ", waarvan " + " & ".join(parts)

    tab_buttons = ['<button class="tab-btn active" onclick="showTab(\'all\', this)">All ({0})</button>'.format(len(matches))]
    tab_panels = [f'<div class="tab-panel active" id="tab-all">{table(matches, show_source=True)}</div>']

    for source in by_source:
        items = by_source[source]
        tab_id = re.sub(r'[^a-z0-9]+', '-', source.lower()).strip('-')
        tab_buttons.append(f'<button class="tab-btn" onclick="showTab(\'{tab_id}\', this)">{source} ({len(items)})</button>')
        tab_panels.append(f'<div class="tab-panel" id="tab-{tab_id}">{table(items)}</div>')

    html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<title>Alle huurwoningen — onder €{MAX_PRICE}</title>
<style>
  body {{ font-family: -apple-system, Arial, sans-serif; background: #f7f7f8; color: #1a1a1a; padding: 24px; }}
  h1 {{ font-size: 20px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  .tabs {{ display: flex; gap: 6px; margin-bottom: 16px; flex-wrap: wrap; }}
  .tab-btn {{ background: white; border: 1px solid #ddd; border-radius: 8px; padding: 8px 14px; font-size: 13px; font-weight: 600; color: #444; cursor: pointer; }}
  .tab-btn:hover {{ background: #f0f2fa; }}
  .tab-btn.active {{ background: #33437a; color: white; border-color: #33437a; }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}
  table {{ width: 100%; border-collapse: collapse; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 14px; }}
  th {{ background: #fafafa; font-weight: 600; }}
  th.sortable {{ cursor: pointer; user-select: none; }}
  th.sortable:hover {{ background: #f0f2fa; }}
  th.sortable::after {{ content: '⇅'; color: #bbb; font-size: 11px; margin-left: 5px; }}
  th.sortable[data-asc="1"]::after {{ content: '↑'; color: #33437a; }}
  th.sortable[data-asc="0"]::after {{ content: '↓'; color: #33437a; }}
  tr.is-new {{ background: #eefbf0; }}
  tr.is-recent {{ background: #fffbea; }}
  .new-badge {{ background: #1aa251; color: white; font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; margin-right: 6px; }}
  .recent-badge {{ background: #e0a800; color: white; font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; margin-right: 6px; }}
  .source-badge {{ background: #eef1fb; color: #33437a; font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 10px; white-space: nowrap; }}
  a {{ color: #0a5cd8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .empty {{ padding: 40px; text-align: center; color: #888; }}
</style>
</head>
<body>
  <h1>Alle huurwoningen onder €{MAX_PRICE}</h1>
  <div class="meta">Laatst gecontroleerd: {checked_at} &middot; {len(matches)} match(es){meta_extra}</div>
  <div class="tabs">
    {"".join(tab_buttons)}
  </div>
  {"".join(tab_panels)}
  <script>
    function showTab(id, btn) {{
      document.querySelectorAll('.tab-panel').forEach(function(p) {{ p.classList.remove('active'); }});
      document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      document.getElementById('tab-' + id).classList.add('active');
      btn.classList.add('active');
    }}

    function sortTable(th) {{
      var table = th.closest('table');
      var key = th.dataset.key;
      var numeric = th.dataset.numeric === '1';
      var asc = th.getAttribute('data-asc') !== '1';
      table.querySelectorAll('th.sortable').forEach(function(h) {{ h.removeAttribute('data-asc'); }});
      th.setAttribute('data-asc', asc ? '1' : '0');

      var tbody = table.querySelector('tbody');
      var rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort(function(a, b) {{
        var va = a.dataset[key] || '';
        var vb = b.dataset[key] || '';
        if (numeric) {{
          va = va === '' ? -Infinity : parseFloat(va);
          vb = vb === '' ? -Infinity : parseFloat(vb);
          return asc ? va - vb : vb - va;
        }}
        return asc ? va.localeCompare(vb) : vb.localeCompare(va);
      }});
      rows.forEach(function(r) {{ tbody.appendChild(r); }});
    }}
  </script>
</body>
</html>"""
    return html


def main():
    all_listings = []

    scrapers = [
        ("Ben Housing", scrape_benhousing),
        ("LIV Residential", scrape_livresidential),
        ("Rental Rotterdam", scrape_rentalrotterdam),
        ("Oude Delft", scrape_oudedelft),
        ("Rent Valley", scrape_rentvalley),
        ("Verra Makelaars", scrape_verra),
        ("Van Weelde Vastgoed", scrape_vanweelde),
        ("ikwilhuren.nu", scrape_ikwilhuren),
        ("070 Wonen", scrape_070wonen),
        ("Hekking NVM", scrape_hekking),
        ("NRW Wonen", scrape_nrwwonen),
        ("Touw Vastgoed", scrape_touwvastgoed),
    ]

    for name, scraper_fn in scrapers:
        print(f"Checking {name}...")
        try:
            results = scraper_fn()
            print(f"  {len(results)} matching listing(s).")
            all_listings.extend(results)
        except Exception as e:
            print(f"  Error checking {name}: {e}")

    print(f"\n{len(all_listings)} listing(s) total match your filters (under €{MAX_PRICE}).")

    seen = load_seen()
    new_keys = [seen_key(l) for l in all_listings if seen_key(l) not in seen]

    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    for l in all_listings:
        k = seen_key(l)
        first_seen = seen.get(k, {}).get("first_seen", checked_at)
        l["first_seen"] = first_seen  # "moment of addition" - used for sorting in the report
        seen[k] = {
            "first_seen": first_seen,
            "last_seen": checked_at,
            "title": l["title"],
        }
    save_seen(seen)

    html = build_html(all_listings, checked_at)
    REPORT_FILE.write_text(html, encoding="utf-8")
    print(f"Report written to {REPORT_FILE}")
    if new_keys:
        print(f"{len(new_keys)} NEW listing(s) since last run!")


if __name__ == "__main__":
    main()
