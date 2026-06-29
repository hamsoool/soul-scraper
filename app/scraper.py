import asyncio
import re
import json
import logging
import io
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional, Set
import calendar
import threading
import time

class SmoothProgress:
    def __init__(self, start_percent, end_percent, duration_est=6.0):
        self.start = start_percent
        self.end = end_percent
        self.duration = duration_est
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run)
        
    def start_track(self):
        self.thread.start()
        
    def stop_track(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join()
        
    def _run(self):
        steps = 150
        interval = self.duration / steps
        for i in range(steps):
            if self.stop_event.wait(timeout=interval):
                break
            t = (i + 1) / steps
            current_val = self.start + (self.end - self.start) * t * 0.95
            
            bar_length = 20
            filled_length = int(round(bar_length * current_val / 100))
            bar = '#' * filled_length + '-' * (bar_length - filled_length)
            
            print(f"\rOCR Progress: [{bar}] {current_val:.1f}%", end="", flush=True)


import httpx
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
from sqlalchemy.future import select
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urljoin, urlparse

from app.config import settings
from app.models import Document
from app.security import validate_and_resolve_url

logger = logging.getLogger(__name__)

# Target Sources
SOURCES = [
    {
        "url": "https://doe.gov.ph/articles/group/liquid-fuels?maincat=Retail%20Pump%20Prices&subcategory=Price%20Adjustments&display_type=Card",
        "category": "Price Adjustments"
    },
    {
        "url": "https://doe.gov.ph/articles/group/liquid-fuels?maincat=Retail%20Pump%20Prices&subcategory=North%20Luzon%20Pump%20Prices&display_type=Card",
        "category": "North Luzon Pump Prices"
    }
]

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}

# Month abbreviation lookup for PDF URL date parsing
MONTH_ABBREVS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

def parse_date_from_text(text: str, fallback_date: datetime = None) -> datetime:
    """Extracts a datetime object from a text snippet, resolving patterns like 'June 2 to 8, 2026' or MMDDYYYY."""
    if not text:
        return fallback_date or datetime.now(timezone.utc)
        
    text_lower = text.lower()
    
    # 1. Search for MMDDYYYY in string, e.g. 05262026
    match_digits = re.search(r'\b(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(20\d{2})\b', text)
    if match_digits:
        m, d, y = map(int, match_digits.groups())
        try:
            return datetime(y, m, d, tzinfo=timezone.utc)
        except ValueError:
            pass
            
    # 2. Search for named month patterns like "June 2 to 8, 2026" or "May 26-June 1, 2026"
    month_name = None
    month_val = 1
    for m, val in MONTHS.items():
        if m in text_lower:
            month_name = m
            month_val = val
            break
            
    if not month_name:
        return fallback_date or datetime.now(timezone.utc)
        
    # Search for a 4-digit year starting with 20
    year_match = re.search(r'\b(20\d{2})\b', text)
    year = int(year_match.group(1)) if year_match else (fallback_date.year if fallback_date else datetime.now().year)
    
    # Search for day numbers (1 to 31)
    numbers = [int(n) for n in re.findall(r'\b(\d{1,2})\b', text) if 1 <= int(n) <= 31]
    
    day = 1
    if numbers:
        # Use the first number representing start of date range
        day = numbers[0]
        
    try:
        return datetime(year, month_val, day, tzinfo=timezone.utc)
    except Exception:
        return fallback_date or datetime.now(timezone.utc)

def parse_date_from_pdf_url(url: str) -> Optional[datetime]:
    """
    Extracts the start date from a DOE pump price PDF URL filename.

    Strategy: anchor on the 4-digit year (most reliable), then find the month
    abbreviation appearing before it, then extract the first day number.
    This handles all known DOE naming patterns including cross-month ranges:

      lf-price-monitoring-for-june-16-22-2026-pdf  → June 16, 2026
      lf-price-monitoring-for-may-26-june-1-2026-pdf → May 26, 2026  (cross-month: use first month)
      lf-price-monitoring-for-dec-10-16-2024-pdf   → December 10, 2024
      nluz_regiii_dec-10-16_2024-pdf               → December 10, 2024

    Returns None if year or month cannot be found — caller should REJECT the doc.
    """
    try:
        # Isolate filename and normalise
        filename = url.rstrip("/").split("/")[-1].lower()
        # Strip trailing -pdf, -pdf1, -pdf2 … suffixes
        filename = re.sub(r"-pdf\d*$", "", filename)

        # Step 1: find the 4-digit year (always appears near the end)
        year_m = re.search(r'(?<!\d)(20\d{2})(?!\d)', filename)
        if not year_m:
            return None
        year = int(year_m.group(1))

        # Step 2: find ALL month abbreviations that appear BEFORE the year
        before_year = filename[:year_m.start()]
        month_hits = list(re.finditer(
            r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*',
            before_year
        ))
        if not month_hits:
            return None

        # Use the FIRST month (start of the date range)
        first_hit = month_hits[0]
        month = MONTH_ABBREVS.get(first_hit.group(1)[:3], 0)
        if not month:
            return None

        # Step 3: find the first day number that appears after the month
        after_first_month = before_year[first_hit.end():]
        day_m = re.search(r'[-_](\d{1,2})(?=[-_]|$)', after_first_month)
        day = int(day_m.group(1)) if day_m else 1

        return datetime(year, month, day, tzinfo=timezone.utc)

    except Exception:
        pass
    return None

def parse_nuxt_state(js_content: str) -> Optional[Tuple[List[str], str, List]]:
    """Parses a Nuxt state block and extracts (parameters, body_str, arguments_list)."""
    func_start = js_content.find('(function(')
    if func_start == -1:
        return None
        
    param_start = func_start + len('(function(')
    param_end = js_content.find(')', param_start)
    params_str = js_content[param_start:param_end]
    params = [p.strip() for p in params_str.split(',')]
    
    return_str = '{return '
    return_idx = js_content.find(return_str, param_end)
    if return_idx == -1:
        return None
    body_start = return_idx + len(return_str)
    
    # Look for the boundary matching } ( ... ) at the end
    matches = list(re.finditer(r'\}\s*\(', js_content))
    if not matches:
        return None
    boundary_match = matches[-1]
    body_end = boundary_match.start()
    arg_start = boundary_match.end() - 1
    
    body_str = js_content[body_start:body_end]
    
    # Track parentheses to extract arguments list
    balance = 0
    arg_end = -1
    for idx in range(arg_start, len(js_content)):
        char = js_content[idx]
        if char == '(':
            balance += 1
        elif char == ')':
            balance -= 1
            if balance == 0:
                arg_end = idx
                break
                
    if arg_end == -1:
        return None
        
    args_str = js_content[arg_start+1:arg_end]
    
    # Decode string escape patterns for arguments
    try:
        args_decoded = args_str.encode('utf-8').decode('unicode-escape')
    except Exception as e:
        logger.error(f"Unicode decode error on Nuxt JS arguments: {e}")
        args_decoded = args_str
        
    args_json = args_decoded
    args_json = re.sub(r'void 0', 'null', args_json)
    args_json = re.sub(r'new Date\(\d+\)', 'null', args_json)
    
    try:
        args_list = json.loads(f"[{args_json}]")
        return params, body_str, args_list
    except Exception as e:
        logger.error(f"Failed to load Nuxt arguments list as JSON: {e}")
        return None

async def fetch_with_backoff(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """Fetches a URL with exponential backoff retry mechanism."""
    retries = 3
    delay = 1.0
    max_delay = 10.0
    
    for attempt in range(retries):
        try:
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except Exception as e:
            if attempt == retries - 1:
                logger.error(f"HTTP request failed for {url} after {retries} attempts: {e}")
                raise e
            logger.warning(f"HTTP attempt {attempt+1} failed for {url}: {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
    raise httpx.RequestError("Retries exhausted")

def extract_pdf_text_sync(pdf_bytes: bytes) -> str:
    """Synchronous PDF text extraction using PyMuPDF."""
    text_content = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                text_content.append(page.get_text())
        return "\n".join(text_content)
    except Exception as e:
        logger.error(f"Error parsing PDF bytes with PyMuPDF: {e}")
        return ""

async def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Wraps the blocking PyMuPDF parser in an async thread pool executor."""
    return await asyncio.to_thread(extract_pdf_text_sync, pdf_bytes)

def parse_price_range(val: str) -> Optional[List[float]]:
    """
    Parses a string containing price range (e.g. "53.58 53.58", "71.70  80.60")
    into a list of floats, or None.
    """
    if not val:
        return None
    val = val.strip()
    if val in ("", "-", "0.00", "0.00 - 0.00", "#N/A"):
        return None
    parts = [p for p in val.replace("-", " ").split() if p]
    floats = []
    for p in parts:
        try:
            val_float = float(p)
            if 30.0 <= val_float <= 150.0:
                floats.append(val_float)
        except ValueError:
            pass
    return floats if floats else None

def parse_float(val: str) -> Optional[float]:
    """Parses a float value from a cell, handling signs and empty/null states."""
    if not val:
        return None
    val = val.strip().replace("\n", " ").replace(" ", "")
    if val in ("", "-", "0.00", "#N/A"):
        return None
    try:
        if val.startswith("+"):
            val = val[1:]
        return float(val)
    except ValueError:
        return None

def clean_cell(cell) -> str:
    if cell is None:
        return ""
    return str(cell).strip().replace("\n", " ").replace("  ", " ")

def extract_prices_from_pdf_sync(pdf_bytes: bytes) -> Optional[str]:
    """
    Synchronously extracts Zambales (Olongapo City & Subic) gas prices from PDF tables.
    Returns a JSON string, or None if no Zambales data is found.
    """
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            results = {}
            for page in doc:
                tables = page.find_tables()
                if not tables or not tables.tables:
                    continue
                    
                for table in tables.tables:
                    data = table.extract()
                    if not data or len(data) < 2:
                        continue
                        
                    headers = [clean_cell(h).upper() for h in data[0]]
                    
                    province_idx = -1
                    city_idx = -1
                    product_idx = -1
                    range_idx = -1
                    
                    for idx, h in enumerate(headers):
                        if "PROVINCE" in h:
                            province_idx = idx
                        elif "CITY" in h or "MUNICIPALITY" in h:
                            city_idx = idx
                        elif "PRODUCT" in h:
                            product_idx = idx
                        elif "RANGE" in h or "OVERALL" in h:
                            range_idx = idx
                    
                    if province_idx == -1: province_idx = 0
                    if city_idx == -1: city_idx = 1
                    if product_idx == -1: product_idx = 2
                    if range_idx == -1: range_idx = len(headers) - 2
                    
                    station_cols = {}
                    for idx in range(product_idx + 1, range_idx):
                        h_name = data[0][idx]
                        if h_name:
                            station_cols[idx] = clean_cell(h_name)
                    
                    current_province = ""
                    current_city = ""
                    
                    for row in data[1:]:
                        if len(row) <= max(province_idx, city_idx, product_idx, range_idx):
                            continue
                            
                        prov_val = clean_cell(row[province_idx])
                        if prov_val:
                            current_province = prov_val
                        
                        city_val = clean_cell(row[city_idx])
                        if city_val:
                            current_city = city_val
                        
                        prod_val = clean_cell(row[product_idx])
                        if not prod_val:
                            continue
                        
                        if "ZAMBALES" not in current_province.upper():
                            continue
                            
                        city_upper = current_city.upper()
                        if "OLONGAPO" not in city_upper and "SUBIC" not in city_upper:
                            continue
                        
                        norm_city = "OLONGAPO CITY" if "OLONGAPO" in city_upper else "SUBIC"
                        
                        station_prices = {}
                        for idx, station_name in station_cols.items():
                            if idx < len(row):
                                price_val = clean_cell(row[idx])
                                parsed_price = parse_price_range(price_val)
                                station_prices[station_name] = parsed_price
                        
                        overall_range = clean_cell(row[range_idx])
                        common_price = clean_cell(row[range_idx + 1]) if range_idx + 1 < len(row) else ""
                        
                        if norm_city not in results:
                            results[norm_city] = {}
                        
                        results[norm_city][prod_val] = {
                            "stations": station_prices,
                            "overall_range": parse_price_range(overall_range),
                            "common_price": parse_price_range(common_price)
                        }
            
            if not results:
                return None
            return json.dumps(results)
    except Exception as e:
        logger.error(f"Error during extract_prices_from_pdf_sync: {e}")
        return None

def extract_prices_from_pdf_ocr(pdf_bytes: bytes) -> Optional[str]:
    """
    OCR-based extraction fallback for scanned image PDFs to extract Zambales prices.
    Uses RapidOCR to parse scanned tables.
    """
    try:
        logger.info("Scanned PDF detected or vector extraction yielded no data. Falling back to OCR extraction...")
        engine = RapidOCR()
        results = {}
        
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            total_pages = len(doc)
            for page_idx, page in enumerate(doc):
                base_percent = int(page_idx / total_pages * 100)
                next_percent = int((page_idx + 1) / total_pages * 100)
                tracker = SmoothProgress(base_percent, next_percent, duration_est=6.0)
                tracker.start_track()
                
                try:
                    pix = page.get_pixmap(dpi=150)
                    img_data = pix.tobytes("png")
                    
                    img = Image.open(io.BytesIO(img_data))
                    if img.mode == 'RGBA':
                        img = img.convert('RGB')
                    img_np = np.array(img)
                    
                    result, _ = engine(img_np)
                finally:
                    tracker.stop_track()
                    bar_length = 20
                    filled_length = int(round(bar_length * next_percent / 100))
                    bar = '#' * filled_length + '-' * (bar_length - filled_length)
                    print(f"\rOCR Progress: [{bar}] {next_percent:.1f}%", end="", flush=True)
                    
                if not result:
                    continue
                
                blocks = []
                for line in result:
                    coords, text, score = line
                    text_str = text.strip()
                    if not text_str:
                        continue
                    x = min(pt[0] for pt in coords)
                    y = min(pt[1] for pt in coords)
                    w = max(pt[0] for pt in coords) - x
                    h = max(pt[1] for pt in coords) - y
                    blocks.append({
                        "text": text_str,
                        "cx": x + w / 2,
                        "cy": y + h / 2,
                        "w": w,
                        "h": h
                    })
                
                if not blocks:
                    continue
                
                blocks.sort(key=lambda b: b["cy"])
                rows = []
                current_row = []
                last_cy = None
                
                for b in blocks:
                    if last_cy is None:
                        current_row.append(b)
                        last_cy = b["cy"]
                    elif abs(b["cy"] - last_cy) < 8:
                        current_row.append(b)
                        last_cy = sum(item["cy"] for item in current_row) / len(current_row)
                    else:
                        current_row.sort(key=lambda item: item["cx"])
                        rows.append(current_row)
                        current_row = [b]
                        last_cy = b["cy"]
                if current_row:
                    current_row.sort(key=lambda item: item["cx"])
                    rows.append(current_row)
                
                header_row = None
                header_idx = -1
                for idx, r in enumerate(rows):
                    row_texts = [b["text"].upper() for b in r]
                    if any("PROV" in t for t in row_texts) and any("PRODUCT" in t for t in row_texts) and any("CITY" in t or "MUN" in t for t in row_texts):
                        header_row = r
                        header_idx = idx
                        break
                
                if not header_row:
                    continue
                
                province_cx = None
                city_cx = None
                product_cx = None
                range_cx = None
                common_cx = None
                station_columns = []
                
                for b in header_row:
                    txt = b["text"].upper()
                    if "PROV" in txt:
                        province_cx = b["cx"]
                    elif "CITY" in txt or "MUN" in txt:
                        city_cx = b["cx"]
                    elif "PRODUCT" in txt:
                        product_cx = b["cx"]
                    elif "RANGE" in txt or "OVERALL" in txt:
                        range_cx = b["cx"]
                    elif "COMMON" in txt or "PRICE" in txt:
                        common_cx = b["cx"]
                    else:
                        brand = b["text"].strip()
                        if brand:
                            station_columns.append((b["cx"], brand))
                
                if province_cx is None: province_cx = 200
                if city_cx is None: city_cx = 350
                if product_cx is None: product_cx = 450
                
                province_anchors = []
                city_anchors = []
                
                for r in rows[header_idx + 1:]:
                    for b in r:
                        txt_upper = b["text"].upper()
                        if abs(b["cx"] - province_cx) < 60:
                            if "TARLAC" in txt_upper or "ZAMBALES" in txt_upper:
                                province_anchors.append((b["cy"], txt_upper))
                        elif abs(b["cx"] - city_cx) < 60:
                            if any(c in txt_upper for c in ["OLONGAPO", "SUBIC", "TARLAC"]):
                                city_anchors.append((b["cy"], txt_upper))
                
                for r in rows[header_idx + 1:]:
                    prod_b = None
                    price_blocks = []
                    
                    for b in r:
                        if abs(b["cx"] - product_cx) < 60:
                            txt = b["text"].upper()
                            if any(p in txt for p in ["RON", "DIESEL", "KEROSENE"]):
                                prod_b = b
                        
                        txt = b["text"]
                        if txt in ("#NIA", "NIA", "#N/A", "N/A", "-", "0.00", "批NIA", "批N/A", "桂NIA") or any(char.isdigit() for char in txt):
                            price_blocks.append(b)
                    
                    if not prod_b:
                        continue
                    
                    row_cy = prod_b["cy"]
                    closest_province = ""
                    if province_anchors:
                        closest_province = min(province_anchors, key=lambda a: abs(a[0] - row_cy))[1]
                        
                    closest_city = ""
                    if city_anchors:
                        closest_city = min(city_anchors, key=lambda a: abs(a[0] - row_cy))[1]
                    
                    if "OLONGAPO" in closest_city:
                        norm_city = "OLONGAPO CITY"
                    elif "SUBIC" in closest_city:
                        norm_city = "SUBIC"
                    else:
                        norm_city = closest_city
                    
                    if norm_city not in ["OLONGAPO CITY", "SUBIC"] and "ZAMBALES" not in closest_province:
                        continue
                    
                    prod_val = prod_b["text"].strip().upper()
                    if "RON" in prod_val:
                        parts = prod_val.replace(" ", "").split("RON")
                        if len(parts) > 1:
                            prod_val = f"RON {parts[1]}"
                    elif "DIESEL" in prod_val:
                        if "PLUS" in prod_val or "ULTRA" in prod_val:
                            prod_val = "DIESEL PLUS"
                        else:
                            prod_val = "DIESEL"
                    
                    station_prices = {}
                    overall_range = None
                    common_price = None
                    
                    for _, brand in station_columns:
                        station_prices[brand.upper()] = None
                        
                    for pb in price_blocks:
                        dists = []
                        if range_cx is not None:
                            dists.append((abs(pb["cx"] - range_cx), "range"))
                        if common_cx is not None:
                            dists.append((abs(pb["cx"] - common_cx), "common"))
                            
                        for cx, brand in station_columns:
                            dists.append((abs(pb["cx"] - cx), brand.upper()))
                            
                        if not dists:
                            continue
                            
                        best_dist, target = min(dists)
                        if best_dist > 80:
                            continue
                            
                        parsed_val = parse_price_range(pb["text"].replace("NIA", "#N/A").replace("批", "").replace("桂", ""))
                        
                        if target == "range":
                            overall_range = parsed_val
                        elif target == "common":
                            common_price = parsed_val
                        else:
                            station_prices[target] = parsed_val
                    
                    if norm_city not in results:
                        results[norm_city] = {}
                        
                    results[norm_city][prod_val] = {
                        "stations": station_prices,
                        "overall_range": overall_range,
                        "common_price": common_price
                    }
        print()
        
        if results:
            logger.info("Successfully extracted Zambales data using OCR!")
            return json.dumps(results)
    except Exception as e:
        logger.error(f"Error during extract_prices_from_pdf_ocr: {e}")
    return None

def extract_price_adjustments_sync(pdf_bytes: bytes) -> Optional[str]:
    """
    Synchronously extracts price adjustments (Gasoline, Diesel, Kerosene) by Oil Company from PDF tables.
    Returns a JSON string, or None if extraction fails or yields no adjustments.
    """
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            results = {}
            for page in doc:
                tables = page.find_tables()
                if not tables or not tables.tables:
                    continue
                    
                for table in tables.tables:
                    data = table.extract()
                    if not data or len(data) < 3:
                        continue
                        
                    row0_cleaned = [clean_cell(c).upper() for c in data[0]]
                    has_company = any("COMPANY" in c or "OIL" in c for c in row0_cleaned)
                    has_effectivity = any("EFFECTIVITY" in c or "EFFECTIVE" in c for c in row0_cleaned)
                    
                    if not (has_company and has_effectivity):
                        continue
                        
                    company_idx = -1
                    received_idx = -1
                    effective_idx = -1
                    
                    for idx, c in enumerate(row0_cleaned):
                        if "COMPANY" in c or "OIL" in c:
                            company_idx = idx
                        elif "RECEIVED" in c or "MESSAGE" in c:
                            received_idx = idx
                        elif "EFFECTIVITY" in c or "EFFECTIVE" in c:
                            effective_idx = idx
                    
                    if company_idx == -1: company_idx = 0
                    if received_idx == -1: received_idx = 1
                    if effective_idx == -1: effective_idx = 2
                    
                    row1_cleaned = [clean_cell(c).upper() for c in data[1]]
                    
                    gasoline_idx = -1
                    diesel_idx = -1
                    kerosene_idx = -1
                    
                    for idx, c in enumerate(row1_cleaned):
                        if "GASOLINE" in c:
                            gasoline_idx = idx
                        elif "DIESEL" in c:
                            diesel_idx = idx
                        elif "KEROSENE" in c:
                            kerosene_idx = idx
                    
                    if gasoline_idx == -1: gasoline_idx = 3
                    if diesel_idx == -1: diesel_idx = 4
                    if kerosene_idx == -1: kerosene_idx = 5
                    
                    for row in data[2:]:
                        if len(row) <= max(company_idx, received_idx, effective_idx, gasoline_idx, diesel_idx, kerosene_idx):
                            continue
                            
                        company_name = clean_cell(row[company_idx])
                        if not company_name or "OIL COMPANY" in company_name.upper():
                            continue
                            
                        received_val = clean_cell(row[received_idx])
                        effective_val = clean_cell(row[effective_idx])
                        
                        gasoline_adj = parse_float(row[gasoline_idx])
                        diesel_adj = parse_float(row[diesel_idx])
                        kerosene_adj = parse_float(row[kerosene_idx])
                        
                        results[company_name] = {
                            "received": received_val,
                            "effective": effective_val,
                            "gasoline": gasoline_adj,
                            "diesel": diesel_adj,
                            "kerosene": kerosene_adj
                        }
            
            if not results:
                return None
            return json.dumps(results)
    except Exception as e:
        logger.error(f"Error during extract_price_adjustments_sync: {e}")
        return None

async def extract_pdf_content(pdf_bytes: bytes, category: str) -> str:
    """
    Extracts text content or parsed JSON tables from PDF bytes depending on category and layout.
    Wraps blocking extraction functions in thread pool executor.
    """
    if category == "North Luzon Pump Prices":
        try:
            parsed_json = await asyncio.to_thread(extract_prices_from_pdf_sync, pdf_bytes)
            if parsed_json:
                return parsed_json
            parsed_json = await asyncio.to_thread(extract_prices_from_pdf_ocr, pdf_bytes)
            if parsed_json:
                return parsed_json
        except Exception as e:
            logger.error(f"Failed to parse North Luzon Pump Prices tables: {e}")
    elif category == "Price Adjustments":
        try:
            parsed_json = await asyncio.to_thread(extract_price_adjustments_sync, pdf_bytes)
            if parsed_json:
                return parsed_json
        except Exception as e:
            logger.error(f"Failed to parse Price Adjustments tables: {e}")
            
    # Default fallback to raw text extraction
    return await extract_pdf_text(pdf_bytes)

async def download_pdf_stream(client: httpx.AsyncClient, url: str) -> bytes:
    """Streams a PDF file with strict size checks and timeout handling."""
    if not validate_and_resolve_url(url):
        raise ValueError(f"Security Policy Blocked: Unsafe URL {url}")
        
    try:
        async with client.stream("GET", url, timeout=settings.HTTP_TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            
            # Check Content-Length header if available
            content_length = response.headers.get("Content-Length")
            total_size = None
            if content_length:
                try:
                    total_size = int(content_length)
                    if total_size > settings.MAX_PDF_SIZE_BYTES:
                        raise ValueError(f"PDF exceeds size limit: {total_size} bytes (limit is {settings.MAX_PDF_SIZE_BYTES})")
                except ValueError as e:
                    if "exceeds size limit" in str(e):
                        raise e
                        
            # Download and accumulate bytes up to the limit
            pdf_bytes = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                pdf_bytes.extend(chunk)
                if len(pdf_bytes) > settings.MAX_PDF_SIZE_BYTES:
                    raise ValueError(f"PDF exceeded size limit during download: {len(pdf_bytes)} bytes")
                
                # Show dynamic download progress percentage
                if total_size:
                    percent = len(pdf_bytes) / total_size * 100
                    bar_length = 20
                    filled_length = int(round(bar_length * percent / 100))
                    bar = '#' * filled_length + '-' * (bar_length - filled_length)
                    print(f"\rDownloading PDF: [{bar}] {percent:.1f}% ({len(pdf_bytes)}/{total_size} bytes)", end="", flush=True)
                else:
                    print(f"\rDownloading PDF: {len(pdf_bytes)} bytes", end="", flush=True)
            print() # Print newline once download finishes
                    
            return bytes(pdf_bytes)
    except Exception as e:
        logger.error(f"Error during PDF download stream from {url}: {e}")
        raise e

async def scrape_source_page(client: httpx.AsyncClient, source_url: str, category: str) -> List[dict]:
    """Scrapes a target DOE page and returns extracted document metadata records."""
    if not validate_and_resolve_url(source_url):
        logger.error(f"Security Policy Blocked: Source URL {source_url} is invalid or unsafe")
        return []
        
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        logger.info(f"Fetching source list page: {source_url}")
        response = await fetch_with_backoff(client, source_url, headers=headers, timeout=settings.HTTP_TIMEOUT_SECONDS)
        html = response.text
    except Exception as e:
        logger.error(f"Failed to fetch list page {source_url}: {e}")
        return []
        
    # Extract Nuxt state script
    soup = BeautifulSoup(html, "html.parser")
    script_tags = soup.find_all("script")
    nuxt_script = None
    for script in script_tags:
        if script.string and "__NUXT__" in script.string:
            nuxt_script = script.string
            break
            
    if not nuxt_script:
        logger.error(f"Nuxt script tag not found on page {source_url}")
        return []
        
    parsed = parse_nuxt_state(nuxt_script)
    if not parsed:
        logger.error(f"Failed to parse Nuxt state for {source_url}")
        return []
        
    params, body_str, args_list = parsed
    mapping = dict(zip(params, args_list))
    
    # Extract individual articles from raw body string
    articles_start = body_str.find("articles:[")
    if articles_start == -1:
        logger.warning(f"No articles list found in Nuxt state for {source_url}")
        return []
        
    # Find matching closing bracket for the articles array
    balance = 0
    articles_end = -1
    for idx in range(articles_start + len("articles:[") - 1, len(body_str)):
        char = body_str[idx]
        if char == '[':
            balance += 1
        elif char == ']':
            balance -= 1
            if balance == 0:
                articles_end = idx
                break
                
    if articles_end == -1:
        logger.warning("Unbalanced articles list brackets in Nuxt state")
        return []
        
    articles_str = body_str[articles_start:articles_end+1]
    article_indices = [m.start() for m in re.finditer(r'\{id:', articles_str)]
    
    records = []
    
    for i, start_idx in enumerate(article_indices):
        end_idx = article_indices[i+1] if i+1 < len(article_indices) else len(articles_str) - 1
        art_segment = articles_str[start_idx:end_idx]
        
        # Extract article fields
        id_match = re.search(r'id:(\d+)', art_segment)
        art_id = id_match.group(1) if id_match else None
        if not art_id:
            continue
            
        title_match = re.search(r'title:([^,]+)', art_segment)
        title_var = title_match.group(1).strip() if title_match else ""
        title = mapping.get(title_var, title_var) if title_var in mapping else title_var
        if isinstance(title, str):
            if (title.startswith('"') and title.endswith('"')) or (title.startswith("'") and title.endswith("'")):
                try:
                    title = title[1:-1].encode('utf-8').decode('unicode-escape')
                except Exception:
                    title = title[1:-1]
            else:
                try:
                    title = title.encode('utf-8').decode('unicode-escape')
                except Exception:
                    pass
            
        date_match = re.search(r'datePublished:([^,|}]+)', art_segment)
        date_var = date_match.group(1).strip() if date_match else ""
        date_val = mapping.get(date_var, date_var) if date_var in mapping else date_var
        if isinstance(date_val, str):
            try:
                date_val = date_val.encode('utf-8').decode('unicode-escape')
            except Exception:
                pass
        
        # Convert publish date to datetime
        fallback_dt = None
        if date_val:
            try:
                # ISO date string handling
                fallback_dt = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
            except Exception:
                pass
        if not fallback_dt:
            fallback_dt = datetime.now(timezone.utc)
            
        # Parse content field (extract raw JSON string first)
        content_match = re.search(r'content:("(?:[^"\\]|\\.)*")', art_segment)
        content_html = ""
        if content_match:
            try:
                content_html_raw = json.loads(content_match.group(1))
                content_html = content_html_raw.encode('utf-8').decode('unicode-escape')
            except Exception as e:
                logger.error(f"Error decoding content HTML of article {art_id}: {e}")
                content_html = content_match.group(1)
                
        if not content_html:
            continue
            
        # Parse links using BeautifulSoup inside the article content
        art_soup = BeautifulSoup(content_html, "html.parser")
        links = art_soup.find_all("a")
        
        for a in links:
            href = a.get("href")
            if not href:
                continue
                
            # We are interested in Liferay guest document links (/documents/d/guest/...)
            if "/documents/" in href and "guest" in href:
                # Resolve it to absolute URL on the prod-cms host
                cms_base = "https://prod-cms.doe.gov.ph"
                pdf_url = urljoin(cms_base, href)
                
                link_text = a.get_text(strip=True)
                
                # Determine title
                if category == "Price Adjustments":
                    # For price adjustments, the link text is often the article title or adjustment name
                    doc_title = link_text if len(link_text) > 10 else title
                else:
                    # For North Luzon pump prices, the link text is the date range (e.g. June 2 to 8, 2026)
                    # We combine it with the main article title for a better record description
                    doc_title = f"{title} - {link_text}"
                    
                # Determine published date from link text, fallback to article date
                published_dt = parse_date_from_text(link_text, fallback_dt)
                
                # Source page URL for reference
                # If we have article slug, we can construct the direct article URL
                slug_match = re.search(r'friendlyUrlPath:([^,]+)', art_segment)
                slug_var = slug_match.group(1).strip() if slug_match else ""
                slug = mapping.get(slug_var, slug_var) if slug_var in mapping else slug_var
                if isinstance(slug, str):
                    if (slug.startswith('"') and slug.endswith('"')) or (slug.startswith("'") and slug.endswith("'")):
                        try:
                            slug = slug[1:-1].encode('utf-8').decode('unicode-escape')
                        except Exception:
                            slug = slug[1:-1]
                    else:
                        try:
                            slug = slug.encode('utf-8').decode('unicode-escape')
                        except Exception:
                            pass
                    
                source_article_url = urljoin("https://doe.gov.ph", f"/articles/{slug}") if slug else source_url
                
                # Apply Date Filters:
                # 1. Price Adjustments: only the past 2 weeks (14 days)
                # 2. North Luzon Pump Prices: use PDF URL date (most reliable — link text
                #    is often just a region label like "Region III" with no date info,
                #    which causes fallback to article publish date and lets all 100+
                #    historical PDFs through. URL filenames encode the actual date.)
                now = datetime.now(timezone.utc)
                two_months_ago = now - timedelta(days=60)
                if category == "Price Adjustments":
                    if published_dt < two_months_ago:
                        logger.info(f"Filtering out '{doc_title}' published at {published_dt} (older than 2 months)")
                        continue
                elif category == "North Luzon Pump Prices":
                    # Always use the URL-extracted date — it's the most reliable signal.
                    # Do NOT fall back to link-text date: the parent article's datePublished
                    # is always the page's last-updated date (June 2026), so ALL historical
                    # PDFs linked on that page would pass a link-text-based filter.
                    url_date = parse_date_from_pdf_url(pdf_url)
                    if not url_date:
                        logger.info(
                            f"Skipping '{doc_title}': cannot parse date from URL '{pdf_url}'"
                        )
                        continue
                    if url_date < two_months_ago:
                        logger.info(
                            f"Filtering out '{doc_title}' — PDF date {url_date.date()} "
                            f"is older than 2 months"
                        )
                        continue
                    # Use the URL-extracted date as the canonical published date
                    published_dt = url_date
                
                records.append({
                    "source_category": category,
                    "title": doc_title,
                    "source_url": source_article_url,
                    "pdf_url": pdf_url,
                    "published_date": published_dt
                })
                
    return records

async def cleanup_outdated_records(db_session: AsyncSession) -> dict:
    """
    Cleans up the documents table by:
    1. Deleting records whose published_date is older than 2 months.
    2. Deleting duplicate records that share the same PDF filename,
       keeping only the most recently created one per filename.

    Returns a summary dict with counts of removed records.
    """
    now = datetime.now(timezone.utc)
    two_months_ago = now - timedelta(days=60)
    outdated_deleted = 0
    duplicate_deleted = 0

    # --- 1. Delete outdated records (published_date older than 2 months) ---
    try:
        outdated_stmt = (
            delete(Document)
            .where(Document.published_date < two_months_ago)
            .where(Document.published_date.is_not(None))
        )
        result = await db_session.execute(outdated_stmt)
        outdated_deleted = result.rowcount
        if outdated_deleted:
            logger.info(f"Cleanup: Deleted {outdated_deleted} outdated record(s) older than {two_months_ago.date()}.")
    except Exception as e:
        logger.error(f"Cleanup: Error deleting outdated records: {e}")

    # --- 2. Delete duplicate records sharing the same PDF filename ---
    # Fetch all documents ordered newest-first so we can keep the first seen per filename.
    try:
        all_docs_stmt = select(Document.id, Document.pdf_url).order_by(Document.created_at.desc())
        all_docs_result = await db_session.execute(all_docs_stmt)
        rows = all_docs_result.all()  # list of (id, pdf_url) tuples

        seen_filenames: Set[str] = set()
        duplicate_ids: List[int] = []

        for doc_id, pdf_url in rows:
            # Extract just the filename portion of the URL
            filename = pdf_url.rstrip("/").split("/")[-1].lower() if pdf_url else ""
            if not filename:
                continue
            if filename in seen_filenames:
                duplicate_ids.append(doc_id)
            else:
                seen_filenames.add(filename)

        if duplicate_ids:
            dup_stmt = delete(Document).where(Document.id.in_(duplicate_ids))
            dup_result = await db_session.execute(dup_stmt)
            duplicate_deleted = dup_result.rowcount
            logger.info(f"Cleanup: Deleted {duplicate_deleted} duplicate record(s) by filename.")
    except Exception as e:
        logger.error(f"Cleanup: Error deleting duplicate records: {e}")

    # Commit cleanup changes
    if outdated_deleted or duplicate_deleted:
        try:
            await db_session.commit()
            logger.info(f"Cleanup committed: {outdated_deleted} outdated, {duplicate_deleted} duplicates removed.")
        except Exception as e:
            await db_session.rollback()
            logger.error(f"Cleanup: Failed to commit deletions: {e}")

    return {
        "outdated_deleted": outdated_deleted,
        "duplicate_deleted": duplicate_deleted,
        "cutoff_date": two_months_ago.isoformat()
    }


async def sync_doe_data(db_session: AsyncSession) -> dict:
    """
    Core sync execution orchestrator:
    1. Visits the two target pages.
    2. Extracts and normalizes metadata.
    3. Filters out existing URLs in DB.
    4. Downloads and extracts text from new PDFs.
    5. Saves records to PostgreSQL.
    """
    logger.info("Starting sync_doe_data execution...")
    start_time = datetime.now(timezone.utc)
    processed_count = 0
    errors = []

    # Run cleanup before scraping new data
    cleanup_summary = await cleanup_outdated_records(db_session)
    logger.info(f"Pre-sync cleanup: {cleanup_summary}")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        # Step 1 & 2: Visit both pages and extract PDF links
        all_metadata = []
        for source in SOURCES:
            try:
                records = await scrape_source_page(client, source["url"], source["category"])
                all_metadata.extend(records)
                logger.info(f"Extracted {len(records)} document links from {source['category']}")
            except Exception as e:
                err_msg = f"Failed to scrape source page {source['category']}: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                
        # Deduplicate links found during this execution session
        unique_metadata = {}
        for item in all_metadata:
            unique_metadata[item["pdf_url"]] = item
        deduplicated_items = list(unique_metadata.values())
        
        logger.info(f"Total deduplicated candidate links to check: {len(deduplicated_items)}")
        
        # Step 3: Compare against database
        for item in deduplicated_items:
            try:
                # Check if unique pdf_url already exists
                stmt = select(Document).filter(Document.pdf_url == item["pdf_url"])
                result = await db_session.execute(stmt)
                existing = result.scalars().first()
                
                if existing:
                    # Already processed, skip
                    logger.debug(f"Document already exists: {item['pdf_url']}")
                    continue
                    
                logger.info(f"Processing new PDF: {item['pdf_url']}")
                
                # Step 4: Stream download new PDF
                pdf_bytes = await download_pdf_stream(client, item["pdf_url"])
                
                # Step 5: Extract content
                text_content = await extract_pdf_content(pdf_bytes, item["source_category"])
                
                # Step 6: Create database record
                new_doc = Document(
                    source_category=item["source_category"],
                    title=item["title"],
                    source_url=item["source_url"],
                    pdf_url=item["pdf_url"],
                    content=text_content,
                    published_date=item["published_date"]
                )
                
                db_session.add(new_doc)
                processed_count += 1
                
            except Exception as e:
                err_msg = f"Failed to process document {item['pdf_url']}: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                
        # Commit all new additions to database
        if processed_count > 0:
            try:
                await db_session.commit()
                logger.info(f"Successfully sync'd and saved {processed_count} new documents to DB")
            except Exception as e:
                await db_session.rollback()
                err_msg = f"Database commit failed during sync session: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    
    return {
        "status": "success" if not errors else "partial_success",
        "processed_count": processed_count,
        "duration_seconds": duration,
        "errors": errors,
        "cleanup": cleanup_summary
    }
