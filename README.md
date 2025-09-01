Bin Collections API (Vercel Python)
===================================

A tiny serverless API that fetches bin collection dates from Belfast City Council’s online service and returns them as plain text or JSON. Deployed on Vercel; scraping logic lives in `app.py`, the API handler in `api/bin.py`.

Overview
--------
- Endpoint: `/api/bin`
- Runtime: Vercel Python (serverless)
- Scraper: Posts to the ASP.NET form at Belfast City Council’s site and parses the results panel.

Query Parameters
----------------
- `postcode` (required): Full postcode, e.g. `BT00 0AA`.
- `address` (optional): Address fragment to disambiguate/select an address, e.g. `1 EXAMPLE STREET`.
- `format` (optional): `text` (default) or `json`.
- `debug` (optional): `1`/`true` to include a short trace for troubleshooting.

Responses
---------
- `200 text/plain` (format=text):
  - First line: `<Address> bin collections`
  - Following lines: `<BinType> - DD/MM/YY`
- `200 application/json` (format=json):
  - `{ "address": "...", "collections": [{"type": "General", "date": "YYYY-MM-DD"}, ...] }`
- `400` on input/selection errors; with `debug=1` a brief trace is appended to help diagnose selectors/postbacks.

Examples
--------
- Text: `/api/bin?postcode=BT00%200AA&address=1%20EXAMPLE%20STREET&format=text`
- JSON: `/api/bin?postcode=BT00%200AA&address=1%20EXAMPLE%20STREET&format=json`

Note: Replace with your own postcode/address; do not commit personal data.

Local Development
-----------------
- Python 3.10+
- Install deps: `pip install -r requirements.txt`
- CLI (debug): `python app.py -v "BT00 0AA" "1 EXAMPLE STREET"`
  - Prints additional details during scraping to stdout.

Project Structure
-----------------
- `app.py` — scraping and parsing utilities; also a CLI for local testing.
- `api/bin.py` — Vercel serverless handler exposing `/api/bin`.
- `requirements.txt` — Python dependencies (`requests`, `beautifulsoup4`, `lxml`).

Debugging Tips
--------------
- Use `debug=1` in requests to include a short trace in error responses (does not log personal data to source).
- If the site markup changes, adjust selectors/postbacks in `app.py` (e.g., select IDs, button names, `__EVENTTARGET`).

Privacy & Safety
----------------
- Do not commit personal addresses/postcodes to the repository.
- Consider adding a local pre-commit hook to block postcode/address-like strings in staged changes.

Deployment (Vercel)
-------------------
- Push to `main` triggers deployment.
- Python serverless file is `api/bin.py` and defines `Handler` based on `BaseHTTPRequestHandler`.

Disclaimer
----------
This project scrapes a public service. Respect the site’s terms of use and rate limits. Functionality may break if the upstream page changes.

