# Cold Culture Scraper

An async web scraper for [coldcultureworldwide.com](https://coldcultureworldwide.com) built with Python.

## What it does
- Scrapes all products from the store
- Collects name, price, sizes, colors, material, fit, images and categories for each product
- Exports everything to JSON and CSV
- Handles rate limiting automatically
- Runs concurrently so it's fast

## Built with
- `playwright` — controls a real browser to load pages
- `playwright-stealth` — avoids bot detection
- `selectolax` — parses the HTML
- `aiohttp` — makes async HTTP requests

## How to run it

Install dependencies:
```
pip install -r requirements.txt
playwright install chromium
```

Run the scraper:
```
python main.py
```

This will create `products.json` and `products.csv` in the same folder.

## Sample output
See `sample_output.csv` and `sample_output.json` for example data.

> Built for educational and portfolio purposes only.