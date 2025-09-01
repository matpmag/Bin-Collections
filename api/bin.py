import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional, Dict, Tuple

import requests

import app as appmod
from app import (
    BASE_URL,
    step1_submit_postcode,
    step2_select_address,
    street_flow,
    parse_bin_details,
    derive_street_from_hint,
)


def _process(postcode: str, address: Optional[str], out_format: str) -> Tuple[str, str, str]:
    """Return (body, content_type, status_code) as strings."""
    with requests.Session() as session:
        try:
            page_url, soup = step1_submit_postcode(session, BASE_URL, postcode)
            page_url, soup = step2_select_address(session, page_url, soup, address)
        except Exception:
            # Fallback to street flow using a derived street fragment
            street_hint = derive_street_from_hint(address or "") or (address or "").strip()
            if not street_hint:
                raise
            page_url, soup = street_flow(session, BASE_URL, street_hint, postcode, address)

    pnl = soup.find(id="BinDetailsPnl")
    if not pnl:
        raise RuntimeError("BinDetailsPnl not found in response")

    addr, items = parse_bin_details(pnl)
    pref = {"General": 0, "Recycling": 1, "Compost": 2}
    order = sorted(items.items(), key=lambda kv: (kv[1], pref.get(kv[0], 99)))

    if out_format == "json":
        data = {
            "address": addr,
            "collections": [
                {"type": name, "date": dt.strftime("%Y-%m-%d")}
                for name, dt in order
            ],
        }
        return json.dumps(data), "application/json", "200"

    lines = [f"{addr} bin collections"]
    lines.extend(f"{name} - {dt.strftime('%d/%m/%y')}" for name, dt in order)
    return "\n".join(lines), "text/plain; charset=utf-8", "200"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(length) if length > 0 else b""
            payload = {}
            if body:
                try:
                    payload = json.loads(body.decode('utf-8'))
                except Exception:
                    payload = {}
            postcode = (payload.get('postcode') or '').strip()
            address = (payload.get('address') or '').strip() or None
            out_format = (payload.get('format') or 'text').lower()
            debug_flag = (str(payload.get('debug', '')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                appmod.VERBOSE = True
                appmod.DEBUG_LOG.clear()
            if not postcode:
                raise ValueError("Missing required field: postcode")
            try:
                body_text, content_type, status = _process(postcode, address, out_format)
            except Exception as e:
                if debug_flag:
                    trace = "\n".join(appmod.DEBUG_LOG[-50:])
                    raise RuntimeError(f"{e}\n--- debug trace ---\n{trace}")
                raise
            self.send_response(int(status))
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(body_text.encode('utf-8'))
        except Exception as e:
            self.send_response(400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode('utf-8'))

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            postcode = (qs.get('postcode', [''])[0]).strip()
            address = (qs.get('address', [''])[0]).strip() or None
            out_format = (qs.get('format', ['text'])[0]).lower()
            debug_flag = (qs.get('debug', [''])[0]).lower() in ('1', 'true', 'yes')
            if debug_flag:
                appmod.VERBOSE = True
                appmod.DEBUG_LOG.clear()
            if not postcode:
                raise ValueError("Missing required query parameter: postcode")
            try:
                body_text, content_type, status = _process(postcode, address, out_format)
            except Exception as e:
                if debug_flag:
                    trace = "\n".join(appmod.DEBUG_LOG[-50:])
                    raise RuntimeError(f"{e}\n--- debug trace ---\n{trace}")
                raise
            self.send_response(int(status))
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(body_text.encode('utf-8'))
        except Exception as e:
            self.send_response(400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode('utf-8'))

    def log_message(self, format, *args):
        # Suppress default logging to keep output clean
        pass
