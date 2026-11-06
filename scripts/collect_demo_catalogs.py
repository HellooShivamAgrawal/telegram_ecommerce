"""Collect reproducible, source-attributed demo catalog snapshots.

This is deliberately separate from the live bot database. It reads public
storefront product data, retains source fields, and does not invent stock or MRP.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "demo_catalogs"
UA = "Mozilla/5.0 (compatible; TelegramEcommerceDemoCatalog/1.0)"


def get(url, attempts=4):
    for n in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=25) as response:
                return response.read().decode("utf-8")
        except Exception:
            if n == attempts - 1:
                raise
            time.sleep(1 + n)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())


def strip_html(markup):
    parser = PlainText()
    parser.feed(markup or "")
    return " ".join(parser.parts)


def write_records(name, records):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.jsonl"
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(name, len(records), path)


def shopify_products(host, collection=None):
    prefix = f"https://{host}"
    if collection:
        prefix += f"/collections/{collection}"
    products = []
    for page in range(1, 10):
        url = f"{prefix}/products.json?limit=250&page={page}"
        batch = json.loads(get(url))["products"]
        products.extend(batch)
        if len(batch) < 250:
            break
        time.sleep(0.5)
    return products


def normalize_shopify(brand, host, product, observed_at, catalog_groups=None):
    variants = product.get("variants") or []
    prices = [float(v["price"]) for v in variants if v.get("price")]
    mrps = [float(v["compare_at_price"]) for v in variants if v.get("compare_at_price") and float(v["compare_at_price"]) > 0]
    images = [
        {"url": image["src"], "alt": image.get("alt"), "position": image.get("position")}
        for image in product.get("images") or [] if image.get("src")
    ]
    options = product.get("options") or []
    custom = {"product_type": product.get("product_type")}
    if catalog_groups:
        custom["catalog_groups"] = sorted(catalog_groups)
    option_values = {}
    for option in options:
        name = (option.get("name") or "").strip().lower().replace(" ", "_")
        values = [v for v in option.get("values") or [] if v and v != "Default Title"]
        if name and name != "title" and values:
            option_values[name] = values
    if option_values:
        custom["option_values"] = option_values
    if brand == "nicobar":
        filters = {}
        for tag in product.get("tags") or []:
            match = re.match(r"filter([a-z]+)_(.+)", tag, re.I)
            if match:
                key = match.group(1).lower()
                filters.setdefault(key, [])
                if match.group(2) not in filters[key]:
                    filters[key].append(match.group(2))
        custom.update(filters)
    if brand == "ellementry":
        materials = {"ceramic", "glass", "wood", "mango wood", "metal", "marble", "terracotta", "stoneware", "brass", "teak", "bamboo"}
        matched = [tag for tag in product.get("tags") or [] if tag.strip().lower() in materials]
        if matched:
            custom["material"] = matched
    return {
        "brand": brand, "source_platform": "shopify", "source_product_id": str(product["id"]),
        "source_url": f"https://{host}/products/{product['handle']}",
        "observed_at": observed_at, "currency": "INR",
        "name": product["title"], "description": strip_html(product.get("body_html")),
        "description_html": product.get("body_html"), "vendor": product.get("vendor"),
        "category": product.get("product_type"), "tags": product.get("tags") or [],
        "price": min(prices) if prices else None, "mrp": min(mrps) if mrps else None,
        "available": any(v.get("available") for v in variants),
        "images": images, "options": options, "variants": variants,
        "custom": custom,
        "source_raw": product,
    }


def collect_shopify(brand, host, collections=None):
    groups = {}
    products = {}
    for collection in collections or [None]:
        for p in shopify_products(host, collection):
            if p.get("images") and p.get("published_at"):
                products[p["id"]] = p
                groups.setdefault(p["id"], set()).add(collection or "all")
    observed_at = datetime.now(timezone.utc).isoformat()
    records = [normalize_shopify(brand, host, p, observed_at, groups[p["id"]])
               for p in products.values()]
    write_records(brand, records)


def angular_state(url):
    html = get(url)
    match = re.search(r'<script id="ng-state" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        raise ValueError(f"Angular state missing from {url}")
    return json.loads(match.group(1))


def recursive_texts(value):
    if isinstance(value, dict):
        for key, part in value.items():
            if key == "text" and isinstance(part, str):
                yield part
            else:
                yield from recursive_texts(part)
    elif isinstance(value, list):
        for part in value:
            yield from recursive_texts(part)


def dailyobjects_options(tree):
    options = {}
    def walk(node):
        if not isinstance(node, dict):
            return
        key = node.get("key")
        if key:
            options.setdefault(key, [])
        for value in node.get("values") or []:
            label = (value.get("title") or {}).get("text")
            if key and label and label not in options[key]:
                options[key].append(label)
            walk(value.get("children"))
    walk(tree)
    return options


def collect_dailyobjects():
    home = get("https://www.dailyobjects.com/")
    category_ids = ["1607"] + list(dict.fromkeys(re.findall(r"cid~(\d+)", home)))
    cards = {}
    for cid in category_ids:
        try:
            state = angular_state(f"https://www.dailyobjects.com/lp?f=cid~{cid}")
            feed = next(v["data"]["feed"] for k, v in state.items() if k.startswith("lpContent"))
            for card in feed:
                if card.get("id") and card.get("link") and card.get("image"):
                    title = (card.get("title") or {}).get("text") or ""
                    if cid == "1101" and ("case" in title.lower() or "cover" in title.lower()):
                        continue
                    item = cards.setdefault(card["id"], {**card, "listing_category_ids": []})
                    if cid not in item["listing_category_ids"]:
                        item["listing_category_ids"].append(cid)
            print("dailyobjects category", cid, len(feed), "unique", len(cards))
        except Exception as error:
            print("dailyobjects category failed", cid, str(error)[:100])
        time.sleep(0.4)
    sitemap = ElementTree.fromstring(get("https://www.dailyobjects.com/sitemap_other_products.xml"))
    for node in sitemap:
        link = node.findtext("{*}loc")
        if link and "/dp?" in link:
            product_id = re.search(r"pid~([^&]+)", link)
            if product_id:
                cards.setdefault(product_id.group(1), {"id": product_id.group(1), "link": link,
                                                       "image": True, "listing_category_ids": []})
    print("dailyobjects non-case sitemap products", len(cards))
    observed_at = datetime.now(timezone.utc).isoformat()
    records = []
    def collect_card(card):
        url = urljoin("https://www.dailyobjects.com", card["link"].split("&")[0])
        url = quote(url, safe=":/?&=~,-_")
        try:
            state = angular_state(url)
            page = state["dp_page_data"]
            details = page["details"]
            seo = page.get("seo") or {}
            images = [{"url": x["url"], "alt": None, "position": i + 1}
                      for i, x in enumerate(page.get("carousel", {}).get("feed") or [])
                      if x.get("type") == "image" and x.get("url")]
            description = (seo.get("productDetail") or {}).get("description")
            if not description:
                description = " ".join(recursive_texts(details.get("info") or []))
            price = re.sub(r"[^\d.]", "", details.get("price", {}).get("text") or "")
            mrp = re.sub(r"[^\d.]", "", (details.get("mrp") or {}).get("text") or "")
            crumbs = seo.get("breadCrumbs") or []
            category = crumbs[-2].get("name") if len(crumbs) >= 3 else None
            record = {
                "brand": "dailyobjects", "source_platform": "dailyobjects", "source_product_id": page["id"],
                "source_url": url, "observed_at": observed_at, "currency": "INR",
                "name": details.get("title", {}).get("text"), "description": strip_html(description),
                "description_html": description, "vendor": "DailyObjects", "category": category,
                "tags": [x.strip() for x in (seo.get("meta") or {}).get("keywords", "").split(",") if x.strip()],
                "price": float(price) if price else None, "mrp": float(mrp) if mrp else None,
                "images": images, "options": state.get("dp_variants_data"), "variants": [],
                "custom": {"product_info": details.get("productInfo"), "product_details": details.get("info"),
                           "rating": seo.get("rating"), "reviews_count": seo.get("reviewsCount"),
                           "option_values": dailyobjects_options(state.get("dp_variants_data")),
                           "listing_category_ids": card["listing_category_ids"]},
                "source_raw": {"page": page, "variants": state.get("dp_variants_data")},
            }
            if (record["name"] and "phone case" not in record["name"].lower()
                    and record["price"] is not None and images):
                return record
        except Exception as error:
            print("dailyobjects product failed", url, str(error)[:100])
        return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(collect_card, card) for card in cards.values()]
        for future in as_completed(futures):
            record = future.result()
            if record:
                records.append(record)
    if not records:
        raise RuntimeError("dailyobjects: no complete products")
    write_records("dailyobjects", records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("brand", choices=["dailyobjects", "blue_tokai", "nicobar", "ellementry"])
    args = parser.parse_args()
    if args.brand == "dailyobjects":
        collect_dailyobjects()
    elif args.brand == "blue_tokai":
        collect_shopify("blue_tokai", "bluetokaicoffee.com")
    elif args.brand == "nicobar":
        collect_shopify("nicobar", "www.nicobar.com",
                        ["all-women", "all-men", "all-home", "all-jewellery", "all-accessories"])
    else:
        collect_shopify("ellementry", "www.ellementry.com")


if __name__ == "__main__":
    main()
