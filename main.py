import asyncio
from selectolax.parser import HTMLParser
from urllib.parse import urljoin
from dataclasses import asdict, dataclass, fields
from decimal import Decimal
import json
import csv
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import aiohttp

@dataclass
class Item:
    name: str | None
    price: Decimal | None
    sizes: list[str] | None
    colors: list[str] | None
    fit: list[str] | None
    material: list[str] | None
    details: list[str] | None
    images: list[str] | None
    categories: list[str] | None

async def get_html_playwright(url, page):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("load", timeout=60000)
        try:
            await page.wait_for_selector("ul.swatch-view li.swatch-view-item div.swatch-image", timeout=10000)
        except:
            pass
        return HTMLParser(await page.content())
    except Exception as e:
        print(f"Failed to load {url}: {e}")
        return None

def parse_search_page(html: HTMLParser):
    products = html.css("div.collection__main product-list.product-list product-card.product-card")
    for product in products:
        href = product.css_first("a").attributes["href"]
        handle = href.split("/products/")[-1].strip("/")
        url = urljoin("https://coldcultureworldwide.com", href)
        yield handle, url

def parse_item_page(html, page, collections=None):
    price_raw = html.css_first("sale-price").text().strip()
    price = Decimal(
        price_raw.replace("€", "").replace("Sale price", "").replace(",", ".").strip()
    ) if price_raw else None

    sizes = []
    for label in html.css("label.block-swatch"):
        size_name = label.attributes.get("data-size")
        classes = label.attributes.get("class", "")
        available = "is-disabled" not in classes
        sizes.append({"size": size_name, "available": available})

    seen = set()
    unique_sizes = []
    for s in sizes:
        key = s["size"]
        if key not in seen:
            seen.add(key)
            unique_sizes.append(s)
    sizes = unique_sizes

    base_url = "https://coldcultureworldwide.com"
    color_urls = []
    for div in html.css("ul.swatch-view li.swatch-view-item div.swatch-image"):
        relative_url = div.attributes.get("swatch-url")
        if relative_url:
            full_url = urljoin(base_url, relative_url)
            color_urls.append(full_url)

    detail_paragraphs = [
        p.text().replace("\xa0", " ").strip()
        for p in html.css("div.product-info__block-item:first-of-type div.accordion__content.prose p")
        if p.text().strip() and p.text().strip() != "\xa0"
    ]
    detail_paragraphs = [
        p for p in detail_paragraphs
        if p != "For an oversize fit choose one size above yours."
    ]

    images = []
    for div in html.css("div.product-gallery__media"):
        img = div.css_first("img")
        if img:
            src = img.attributes.get("src", "")
            if src.startswith("//"):
                src = "https:" + src
            images.append(src)
    images = list(dict.fromkeys(images))

    color_names = []
    for div in html.css("ul.swatch-view li.swatch-view-item div.swatch-image"):
        value = div.attributes.get("orig-value", "")
        if value:
            color_names.append(value.title())
    colors = color_names

    new_item = Item(
        name=extract_text(html, "h1.product-title"),
        price=price,
        sizes=sizes,
        categories=list(collections) if collections else [],
        fit=detail_paragraphs[0] if len(detail_paragraphs) > 0 else None,
        material=detail_paragraphs[1] if len(detail_paragraphs) > 1 else None,
        colors=colors,
        details=" ".join(detail_paragraphs[3:]) if len(detail_paragraphs) > 3 else None,
        images=images,
    )
    return asdict(new_item)


def extract_text(html, sel):
    try:
        return html.css_first(sel).text()
    except AttributeError:
        return None


def export_to_json(products):
    with open("products.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=4, default=str)
    print("saved to json")


def export_to_csv(products):
    field_names = [field.name for field in fields(Item)]
    with open("products.csv", "w") as f:
        writer = csv.DictWriter(f, field_names)
        writer.writeheader()
        writer.writerows(products)
    print("saved to csv")


def append_to_csv(product):
    field_names = [field.name for field in fields(Item)]
    with open("appendcsv.csv", "a") as f:
        writer = csv.DictWriter(f, field_names)
        writer.writerow(product)


async def scroll_me(page):
    previous_height = None
    while True:
        current_height = await page.evaluate("document.body.scrollHeight")
        if current_height == previous_height:
            break
        previous_height = current_height
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)


async def scrape_product(url, context, semaphore, stealth, collections=None):
    async with semaphore:
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        try:
            html_item = await get_html_playwright(url, page)
            if html_item is None:
                return None
            product = parse_item_page(html_item, page, collections=collections)
            append_to_csv(product)
            return product
        finally:
            await page.close()


async def build_product_collections_map(session, page):
    await page.goto("https://coldcultureworldwide.com", wait_until="domcontentloaded")
    html = HTMLParser(await page.content())
    valid_slugs = set()
    for a in html.css("a[href*='/collections/']"):
        href = a.attributes.get("href", "")
        slug = href.split("/collections/")[-1].strip("/").split("?")[0]
        if slug:
            valid_slugs.add(slug)

    resp = await session.get("https://coldcultureworldwide.com/collections.json?limit=250")
    data = await resp.json(content_type=None)
    all_collections = [c for c in data["collections"] if c["handle"] in valid_slugs]

    print(f"Using {len(all_collections)} nav-visible collections")

    product_collections = {}
    for col in all_collections:
        slug = col["handle"]
        title = col["title"].lower().strip()
        print(f"Fetching collection: {slug}")
        page_num = 1
        while True:
            url = f"https://coldcultureworldwide.com/collections/{slug}/products.json?limit=250&page={page_num}"
            for attempt in range(5):
                resp = await session.get(url)
                if resp.status == 429:
                    wait = 2 ** attempt
                    print(f"Rate limited on {slug} page {page_num}, waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                break
            if resp.status != 200:
                break
            data = await resp.json(content_type=None)
            products = data.get("products", [])
            if not products:
                break
            for p in products:
                handle = p["handle"]
                if handle not in product_collections:
                    product_collections[handle] = set()
                product_collections[handle].add(title)
            page_num += 1
            await asyncio.sleep(0.3)

    return product_collections


async def main():
    url = "https://coldcultureworldwide.com/collections/all-products"
    semaphore = asyncio.Semaphore(20)
    stealth = Stealth()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 OPR/127.0.0.0"
        )

        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        await page.set_viewport_size({"width": 1280, "height": 1080})
        await page.goto(url)
        await asyncio.sleep(2)
        try:
            await page.click("button.cc-allow", timeout=3000)
        except:
            pass
        try:
            await page.wait_for_selector('button[aria-label="Close dialog"]', timeout=15000)
            await page.click('button[aria-label="Close dialog"]')
        except:
            pass
        await scroll_me(page)

        html = HTMLParser(await page.content())

        products_map = {}
        for handle, url in parse_search_page(html):
            products_map[handle] = {"url": url, "collections": set()}

        print(f"Found {len(products_map)} products in all-products")

        async with aiohttp.ClientSession() as session:
            product_collections = await build_product_collections_map(session, page)

        for handle, data in products_map.items():
            data["collections"] = product_collections.get(handle, set())

        await page.close()

        print(f"Total unique products: {len(products_map)}")

        results = await asyncio.gather(
            *[scrape_product(data["url"], context, semaphore, stealth, collections=data["collections"])
              for handle, data in products_map.items()],
            return_exceptions=True
        )

        products = [r for r in results if r is not None and not isinstance(r, Exception)]

    export_to_csv(products)
    export_to_json(products)

if __name__ == "__main__":
    asyncio.run(main())