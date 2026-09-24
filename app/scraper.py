import asyncio
import re
import json
import logging
import io
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Set
import calendar

import httpx
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
from sqlalchemy.future import select
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Document
from app.security import validate_and_resolve_url

logger = logging.getLogger(__name__)

# Target Sources
SOURCES = [
    {
        "url": "https://doe.gov.ph/data-and-prices/liquid-fuels/retail-pump-prices/price-adjustments",
        "category": "Price Adjustments"
    },
    {
        "url": "https://doe.gov.ph/data-and-prices/liquid-fuels/retail-pump-prices/north-luzon-pump-prices",
        "category": "North Luzon Pump Prices"
    }
]

# Month abbreviation lookup for attachment title date parsing
MONTH_ABBREVS: dict[str, int] = {m[:3].lower(): i for i, m in enumerate(calendar.month_abbr) if m}

def parse_attachment_date(title: str) -> Optional[datetime]:
    """Extracts the start date of a DOE price-notice date range from an attachment
    title or link text. Handles '15-21 Sep 2026', 'Sep 1-7', '01-07 September 2026',
    'September 15-21' and 'DATED SEPTEMBER 17 2026' — anchored on the month token, so a
    day-range before the month wins ('15-21 Sep' → 15, not 21). Returns the range's
    start day, or None if no month appears in the text."""
    t = title.replace("%20", " ").lower().replace("/", " ")
    pos, month = None, None
    for abbr, val in MONTH_ABBREVS.items():
        found = t.find(abbr)
        if found != -1 and (pos is None or found < pos):
            pos, month = found, val
    if not month:
        return None
    token = t[pos:pos + 3]
    # Day after the month, e.g. 'Sep 8-14', 'September 17 2026'
    after = re.search(rf"{re.escape(token)}\w*\s*(\d{{1,2}})(?!\d)", t)
    # Day range before the month, e.g. '15-21 Sep 2026', '01-07 September 2026'
    before = re.search(rf"(\d{{1,2}})(?:-\d{{1,2}})?\s+{re.escape(token)}\w*", t)
    day_match = after or before
    day = int(day_match.group(1)) if day_match else 1
    window = t[max(0, pos - 30):pos + 30]
    year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", window)
    # ponytail: year-less titles ('Sep 1-7') assume the current year; a stale year-less
    # file revisiting an old week in a future September would slip past the 2-month filter.
    # Upgrade: pass the page's own anchor year, or cross-check the PDF filename.
    year = int(year_match.group(1)) if year_match else datetime.now(timezone.utc).year
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
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
            for page in doc:
                pix = page.get_pixmap(dpi=150)
                img_data = pix.tobytes("png")
                
                img = Image.open(io.BytesIO(img_data))
                if img.mode == 'RGBA':
                    img = img.convert('RGB')
                img_np = np.array(img)
                
                result, _ = engine(img_np)
                
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
                    
            return bytes(pdf_bytes)
    except Exception as e:
        logger.error(f"Error during PDF download stream from {url}: {e}")
        raise e

PDF_FILE_URL_RE = re.compile(r"cloudfront\.net/api/media/file/.+\.pdf", re.IGNORECASE)

async def scrape_source_page(client: httpx.AsyncClient, source_url: str, category: str) -> List[dict]:
    """Scrapes a DOE category page and returns the price-notice PDF records.

    DOE reworked its site (Sept 2026): each Retail Pump Prices category page now lists
    its PDFs as plain <a href="https://d24qbtp4vooyzi.cloudfront.net/api/media/file/*.pdf">
    links inside the CMS content — no article list to unwrap. The anchor text is the
    notice title (Price Adjustments) or the price-week date range (North Luzon), from
    which the published date is parsed."""
    if not validate_and_resolve_url(source_url):
        logger.error(f"Security Policy Blocked: Source URL {source_url} is invalid or unsafe")
        return []

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    try:
        logger.info(f"Fetching source list page: {source_url}")
        response = await fetch_with_backoff(client, source_url, headers=headers, timeout=settings.HTTP_TIMEOUT_SECONDS)
    except Exception as e:
        logger.error(f"Failed to fetch list page {source_url}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    two_months_ago = datetime.now(timezone.utc) - timedelta(days=60)
    records = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not PDF_FILE_URL_RE.search(href):
            continue
        # The CDN serves the same file under dev/media vs dev/media-0 query prefixes — treat as one.
        # (split('?') keeps them distinct from filenames sharing a real URL-escaped suffix)
        key = href.split("?")[0]
        if key in seen:
            continue
        seen.add(key)

        title = a.get_text(strip=True) or key.split("/")[-1]
        published_dt = parse_attachment_date(title)
        if not published_dt:
            logger.info(f"Skipping '{title}': cannot parse a date from the link text")
            continue
        if published_dt < two_months_ago:
            logger.info(f"Filtering out '{title}' — published {published_dt.date()} is older than 2 months")
            continue

        records.append({
            "source_category": category,
            "title": title,
            "source_url": source_url,
            "pdf_url": href,
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
