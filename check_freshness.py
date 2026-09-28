"""Nightly freshness gate for the DOE sync. Run after sync.py in the scheduled workflow.

Fails (exit 1) when:
  1. the newest published_date in the DB is older than MAX_AGE_DAYS (stale data —
     e.g. DOE changed its layout again and extraction silently yields nothing), or
  2. either DOE source page is unreachable or no longer lists CloudFront PDFs
     (immediate signal for layout/CDN changes).

Read-only: only SELECTs from the database.
"""
import asyncio
import logging
import sys
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.database import async_session
from app.models import Document
from app.scraper import SOURCES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("freshness")

# DOE publishes weekly; 3 missed weeks means something broke. Generous on purpose:
# DOE itself sometimes lags several days, and false alarms teach people to ignore the alarm.
MAX_AGE_DAYS = 21
PDF_MARKER = "cloudfront.net/api/media/file/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


async def db_newest_age_days() -> int | None:
    """Returns the age in days of the newest published_date, or None if the table is empty."""
    async with async_session() as session:
        result = await session.execute(select(func.max(Document.published_date)))
        newest = result.scalar()
    if not newest:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - newest).days


def page_lists_pdfs(url: str) -> bool:
    """True when the source page loads (HTTP 200) and still links CloudFront PDFs."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            return False
        html = resp.read().decode("utf-8", errors="replace")
    return PDF_MARKER in html and ".pdf" in html


async def main() -> int:
    failures = []

    age = await db_newest_age_days()
    if age is None:
        failures.append("documents table is empty")
    elif age > MAX_AGE_DAYS:
        failures.append(f"newest published_date is {age} days old (limit is {MAX_AGE_DAYS})")
    else:
        logger.info(f"DB freshness OK: newest published_date is {age} days old")

    for source in SOURCES:
        try:
            ok = await asyncio.to_thread(page_lists_pdfs, source["url"])
        except Exception as e:
            logger.error(f"Page check failed for {source['category']}: {e}")
            ok = False
        if ok:
            logger.info(f"Page check OK: {source['category']} still lists PDFs")
        else:
            failures.append(f"{source['category']} page unreachable or lists no PDFs")

    if failures:
        for f in failures:
            logger.error(f"FRESHNESS FAIL: {f}")
        return 1
    logger.info("All freshness checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
