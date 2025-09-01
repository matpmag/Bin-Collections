import sys
import argparse
import urllib.parse as urlparse
from typing import Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from datetime import datetime


BASE_URL = "https://online.belfastcity.gov.uk/find-bin-collection-day/Default.aspx"


def absolute_url(base: str, link: Optional[str]) -> str:
    if not link:
        return base
    return urlparse.urljoin(base, link)


def find_main_form(soup: BeautifulSoup) -> Tuple[str, Dict[str, str]]:
    """Return (action_url, fields) for the main ASP.NET form."""
    form = (
        soup.find("form", id=True)
        or soup.find("form", attrs={"name": True})
        or soup.find("form")
    )
    if not form:
        raise RuntimeError("No <form> found on page")

    action = form.get("action") or ""
    fields: Dict[str, str] = {}

    # inputs (exclude submit inputs; add explicitly when simulating a click)
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "").lower()
        if itype == "submit":
            # skip submits; include only the one we "click" later
            continue
        if itype in {"checkbox", "radio"}:
            if inp.has_attr("checked"):
                fields[name] = inp.get("value", "on")
        else:
            fields[name] = inp.get("value", "")

    # textareas
    for ta in form.find_all("textarea"):
        name = ta.get("name")
        if name:
            fields[name] = ta.text or ""

    # selects (choose first selected or first option)
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        selected = sel.find("option", selected=True)
        if not selected:
            selected = sel.find("option")
        fields[name] = selected.get("value") if selected else ""

    return action, fields


def extract_form_fields(soup: BeautifulSoup) -> Tuple[str, Dict[str, str]]:
    """Extract form action and fields, excluding submit inputs.

    Useful when moving across postbacks to ensure fresh state fields.
    """
    form = soup.find("form")
    if not form:
        raise RuntimeError("No <form> found on page")
    action = form.get("action") or ""
    fields: Dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "").lower()
        if itype == "submit":
            continue
        if itype in {"checkbox", "radio"}:
            if inp.has_attr("checked"):
                fields[name] = inp.get("value", "on")
        else:
            fields[name] = inp.get("value", "")
    return action, fields


def extract_state_fields(fields: Dict[str, str]) -> Dict[str, str]:
    """ASP.NET hidden state fields that must be posted back each request."""
    return {
        k: v
        for k, v in fields.items()
        if k.startswith("__")  # __VIEWSTATE, __EVENTVALIDATION, etc.
    }


def choose_postcode_field(fields: Dict[str, str]) -> Optional[str]:
    candidates = [
        k
        for k in fields.keys()
        if (
            ("postcode" in k.lower())
            and ("hidden" not in k.lower())
            and not k.startswith("__")
        )
    ]
    # Fallback: any single text-like field
    if not candidates:
        candidates = [k for k in fields.keys() if ("$tb" in k.lower() or "txt" in k.lower())]
    return candidates[0] if candidates else None


def pick_first_submit(fields: Dict[str, str]) -> Optional[str]:
    # With submit controls excluded from fields, this helper is not used now.
    return None


VERBOSE = False
DEBUG_LOG: list[str] = []


def vprint(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    DEBUG_LOG.append(msg)
    if VERBOSE:
        print(*args, **kwargs)


def debug_list_fields(prefix: str, fields: Dict[str, str]) -> None:
    if not VERBOSE:
        return
    print(f"{prefix} fields ({len(fields)} total):")
    for k in sorted(fields.keys()):
        val = fields[k]
        if k.startswith("__"):
            print(f"  {k}=[{len(val)} bytes]")
        else:
            shown = val if len(val) <= 60 else val[:57] + "..."
            print(f"  {k}={shown!r}")


def submit(session: requests.Session, url: str, action: str, data: Dict[str, str]):
    post_url = absolute_url(url, action)
    hdrs = {
        "Referer": url,
        "Origin": urlparse.urlsplit(url).scheme + "://" + urlparse.urlsplit(url).netloc,
    }
    return session.post(post_url, data=data, headers=hdrs)


def step1_submit_postcode(session: requests.Session, url: str, postcode: str) -> Tuple[str, BeautifulSoup]:
    # GET initial page
    # Add a browser-like UA in case the site varies by UA
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    r = session.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    action, fields = find_main_form(soup)

    # Explicit known control names for this site
    pc_field = "ctl00$MainContent$Postcode_textbox"
    radio_field = "ctl00$MainContent$searchBy_radio"
    submit_field = "ctl00$MainContent$AddressLookup_button"

    if pc_field not in fields:
        debug_list_fields("Initial page", fields)
        raise RuntimeError("Postcode input not found in form. The page may have changed.")

    payload = fields.copy()
    payload[radio_field] = "P"
    payload[pc_field] = postcode
    payload[submit_field] = "Find address"

    resp = submit(session, url, action, payload)
    resp.raise_for_status()
    return resp.url, BeautifulSoup(resp.text, "lxml")


def find_address_dropdown(soup: BeautifulSoup) -> Optional[Tuple[str, Dict[str, str]]]:
    # Prefer the explicit address list control
    addr = soup.find("select", id="lstAddresses")
    if addr and addr.get("name"):
        name = addr.get("name")
        values = {opt.get_text(strip=True): opt.get("value") for opt in addr.find_all("option") if opt.get("value")}
        return name, values
    # Fallback: any select with many options
    for sel in soup.find_all("select"):
        options = sel.find_all("option")
        if len(options) > 1 and sel.get("name"):
            values = {opt.get_text(strip=True): opt.get("value") for opt in options if opt.get("value")}
            return sel.get("name"), values
    return None


def step2_select_address(
    session: requests.Session,
    page_url: str,
    soup: BeautifulSoup,
    address_query: Optional[str] = None,
) -> Tuple[str, BeautifulSoup]:
    action, fields = find_main_form(soup)

    dd = find_address_dropdown(soup)
    if not dd:
        debug_list_fields("Postcode page", fields)
        # Extra debug: list selects to help diagnose
        selects = soup.find_all("select")
        if selects and VERBOSE:
            print("Select elements found:")
            for sel in selects:
                name = sel.get("name"); sid = sel.get("id"); opts = len(sel.find_all("option"))
                print(f"  select name={name!r} id={sid!r} options={opts}")
        raise RuntimeError("Could not locate an address dropdown after postcode. Inspect the page to adjust selectors.")

    dd_name, options = dd

    # Choose an address
    chosen_value = None
    if address_query:
        # fuzzy contains match on text
        q = address_query.lower()
        for text, val in options.items():
            if q in text.lower():
                chosen_value = val
                break
    if not chosen_value and options:
        # pick first non-empty value
        for text, val in options.items():
            if val:
                chosen_value = val
                break
    if not chosen_value:
        raise RuntimeError("No suitable address option found.")

    payload = fields.copy()
    payload[dd_name] = chosen_value
    # Click the explicit Select button if present; otherwise use event target
    select_btn = "ctl00$MainContent$SelectAddress_button"
    if select_btn:
        payload[select_btn] = "Select"
    else:
        payload["__EVENTTARGET"] = dd_name
        payload.setdefault("__EVENTARGUMENT", "")

    resp = submit(session, page_url, action, payload)
    resp.raise_for_status()
    return resp.url, BeautifulSoup(resp.text, "lxml")


def derive_street_from_hint(address_hint: Optional[str]) -> Optional[str]:
    if not address_hint:
        return None
    # Strip leading house numbers/flat indicators like '2A', '12', 'Flat 3', etc.
    parts = address_hint.strip().split()
    while parts and (parts[0].rstrip('.').upper() in {"FLAT", "APPT", "APT"} or any(ch.isdigit() for ch in parts[0])):
        parts.pop(0)
    return " ".join(parts) if parts else None


def street_flow(
    session: requests.Session,
    base_url: str,
    street_query: str,
    postcode_hint: Optional[str],
    address_hint: Optional[str],
) -> Tuple[str, BeautifulSoup]:
    # 1) GET initial and search by street
    vprint(f"street_flow: start street_query={street_query!r} postcode_hint={postcode_hint!r}")
    r = session.get(base_url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    action, fields = extract_form_fields(soup)
    fields['ctl00$MainContent$searchBy_radio'] = 'S'
    fields['ctl00$MainContent$Street_textbox'] = street_query
    fields['ctl00$MainContent$streetSearch_button'] = 'Search'
    post_url = absolute_url(base_url, action)
    resp2 = session.post(post_url, data=fields)
    vprint(f"street_flow: posted search status={resp2.status_code}")
    soup2 = BeautifulSoup(resp2.text, 'lxml')

    # 2) choose a street option (prefer one matching the postcode hint)
    streets = soup2.find('select', id='streets_listbox')
    if not streets:
        # heuristic fallback: any select whose id/name mentions 'street'
        for sel in soup2.find_all('select'):
            sid = (sel.get('id') or '').lower()
            sname = (sel.get('name') or '').lower()
            if (('street' in sid) or ('street' in sname)) and len(sel.find_all('option')) > 1:
                streets = sel
                break
    if not streets:
        vprint('street_flow: no streets select found')
        raise RuntimeError('Street search did not return any street list to select from.')
    vprint(f"street_flow: streets select id={(streets.get('id') or '')!r} name={(streets.get('name') or '')!r} options={len(streets.find_all('option'))}")

    # Try multiple matching strategies, then fall back to first non-empty value
    chosen_street_val = None
    outward = (postcode_hint or '').split()[0].upper() if postcode_hint else None
    opts = streets.find_all('option')

    def _opt_value(opt):
        # Use value attribute if present, otherwise fallback to visible text
        val = (opt.get('value') or '').strip()
        if not val:
            val = opt.get_text(strip=True)
        return val

    # 1) Match outward code in option value or text
    if outward:
        for opt in opts:
            val = _opt_value(opt)
            txt = opt.get_text(strip=True)
            if not val:
                continue
            if outward in val.upper() or outward in txt.upper():
                chosen_street_val = val
                vprint(f"street_flow: matched outward {outward} -> {txt!r}")
                break

    # 2) Match address/street hint in option text
    if not chosen_street_val and (address_hint or street_query):
        q = (address_hint or street_query or '').strip().lower()
        if q:
            for opt in opts:
                val = _opt_value(opt)
                txt = opt.get_text(strip=True)
                if val and q in txt.lower():
                    chosen_street_val = val
                    vprint(f"street_flow: matched query {q!r} -> {txt!r}")
                    break

    # 3) First option with a non-empty value
    if not chosen_street_val:
        for opt in opts:
            val = _opt_value(opt)
            if val:
                chosen_street_val = val
                vprint(f"street_flow: fallback picked {opt.get_text(strip=True)!r}")
                break

    if not chosen_street_val:
        # Optional debug listing when verbose
        if VERBOSE:
            print('Street options available:')
            for opt in opts:
                print(f"  value={_opt_value(opt)!r} text={opt.get_text(strip=True)!r}")
        vprint('street_flow: could not select a street option')
        raise RuntimeError('Could not select a street option.')

    # 3) post back to select street
    action2, fields2 = extract_form_fields(soup2)
    streets_name = streets.get('name') or 'ctl00$MainContent$streets_listbox'
    fields2[streets_name] = chosen_street_val
    # Try known button; if absent, trigger event target
    select_btn_name = 'ctl00$MainContent$btn_selectStreet'
    select_btn_present = False
    form2 = soup2.find('form')
    if form2:
        for inp in form2.find_all('input'):
            if (inp.get('type') or '').lower() == 'submit':
                nm = inp.get('name') or ''
                lbl = (inp.get('value') or '')
                if nm == select_btn_name or 'selectstreet' in nm.lower() or 'select street' in lbl.lower():
                    select_btn_name = nm or select_btn_name
                    select_btn_present = True
                    break
    if select_btn_present:
        fields2[select_btn_name] = fields2.get(select_btn_name, 'Select street') or 'Select street'
    else:
        fields2['__EVENTTARGET'] = streets.get('id') or streets_name
        fields2.setdefault('__EVENTARGUMENT', '')
    resp3 = session.post(absolute_url(base_url, action2), data=fields2)
    vprint(f"street_flow: posted select status={resp3.status_code}")
    soup3 = BeautifulSoup(resp3.text, 'lxml')

    # 4) choose address and select
    addr_sel = soup3.find('select', id='lstAddresses')
    if not addr_sel:
        raise RuntimeError('After selecting street, address list not found.')
    chosen_addr_val = None
    if address_hint:
        q = address_hint.lower()
        for opt in addr_sel.find_all('option'):
            if q in opt.get_text(strip=True).lower():
                chosen_addr_val = opt.get('value')
                break
    if not chosen_addr_val and addr_sel.find('option'):
        chosen_addr_val = addr_sel.find('option').get('value')
    if not chosen_addr_val:
        raise RuntimeError('No address option available to select.')

    action3, fields3 = extract_form_fields(soup3)
    fields3['ctl00$MainContent$lstAddresses'] = chosen_addr_val
    fields3['ctl00$MainContent$SelectAddress_button'] = 'Select'
    final_resp = session.post(absolute_url(base_url, action3), data=fields3)
    final_resp.raise_for_status()
    return final_resp.url, BeautifulSoup(final_resp.text, 'lxml')


def normalize_bin_name(raw: str) -> str:
    r = raw.strip()
    r = r.replace(" bin", "").replace(" Bin", "")
    r = r.replace(" waste", "")
    r = r.strip()
    # Known mappings
    if r.lower().startswith("general"):
        return "General"
    if r.lower().startswith("recycling"):
        return "Recycling"
    if r.lower().startswith("compost") or r.lower().startswith("brown"):
        return "Compost"
    return r.title()


def parse_bin_details(pnl: BeautifulSoup) -> Tuple[str, Dict[str, datetime]]:
    # Extract address line and bin entries with next-collection dates
    lines = [s.strip() for s in pnl.stripped_strings if s and s.strip()]
    address_line = lines[0] if lines else ""
    # Use only first part before comma and title-case it
    nice_address = address_line.split(",")[0].title()

    # Remove header labels if present
    headers = {"type of bin", "day(s)", "how often?", "next collection"}
    filtered = [x for x in lines if x.lower() not in headers]

    # Group by pattern: <bin-name>, <day>, <freq>, <date>
    entries: Dict[str, datetime] = {}
    i = 0
    while i + 3 < len(filtered):
        name = filtered[i]
        day = filtered[i + 1]
        freq = filtered[i + 2]
        date_str = filtered[i + 3]
        # Heuristic: name contains 'bin' or matches known bins
        if "bin" in name.lower() or any(k in name.lower() for k in ["general", "recycling", "compost", "brown"]):
            try:
                # Example: Mon Sep  1 2025 (double-space possible)
                dt = datetime.strptime(" ".join(date_str.split()), "%a %b %d %Y")
                entries[normalize_bin_name(name)] = dt
                i += 4
                continue
            except Exception:
                # Not a date; move on by 1
                pass
        i += 1

    return nice_address, entries


def main():
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="Fetch Belfast bin collection info via ASP.NET form POSTs.",
    )
    parser.add_argument("postcode", nargs="?", help="Postcode to search")
    parser.add_argument("address_hint", nargs="?", help="Optional address fragment to select")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print debug details of form fields and fallbacks")

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()
    global VERBOSE
    VERBOSE = bool(args.verbose)

    postcode = args.postcode
    address_hint = args.address_hint

    with requests.Session() as session:
        # Step 1: submit postcode and get page with address list
        page_url, soup = step1_submit_postcode(session, BASE_URL, postcode)

        # Step 2: select address (postback)
        try:
            page_url, soup = step2_select_address(session, page_url, soup, address_hint)
        except RuntimeError:
            # Fallback: try street-based flow using the address hint as street fragment
            street_hint = derive_street_from_hint(address_hint) or (address_hint or '')
            if not street_hint:
                raise
            vprint("Address dropdown not found; trying street-based flow with:", street_hint)
            page_url, soup = street_flow(session, BASE_URL, street_hint, postcode, address_hint)

        # Parse results panel
        pnl = soup.find(id="BinDetailsPnl")
        if not pnl:
            print("Reached page, but BinDetailsPnl not found. Dumping preview:")
            print(soup.get_text(" ", strip=True)[:1000])
            return
        # Format as requested
        addr, items = parse_bin_details(pnl)
        if not items:
            print(pnl.get_text("\n", strip=True))
            return
        # Sort by soonest date; if same day, prefer General > Recycling > Compost
        pref = {"General": 0, "Recycling": 1, "Compost": 2}
        order = sorted(items.items(), key=lambda kv: (kv[1], pref.get(kv[0], 99)))
        print(f"{addr} Bin Collections:")
        for name, dt in order:
            print(f"{name} - {dt.strftime('%d/%m/%y')}")
        print("Visit online.belfastcity.gov.uk/find-bin-collection-day")


if __name__ == "__main__":
    main()
