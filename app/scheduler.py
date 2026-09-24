import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import async_session
from app.scraper import sync_doe_data
from app.config import settings

logger = logging.getLogger(__name__)

# Diagnostics state
sync_state: Dict[str, Any] = {
    "last_sync_time": None,
    "last_sync_result": None,
    "is_syncing": False
}

scheduler = AsyncIOScheduler()

async def run_sync(source: str) -> None:
    """Runs the DOE sync once: guarded against re-entry, creates a session, updates sync_state.
    Shared by the scheduled job and the manual /sync endpoint."""
    if sync_state["is_syncing"]:
        logger.warning(f"{source}: sync is already running. Skipping.")
        return

    sync_state["is_syncing"] = True
    logger.info(f"{source} sync started.")

    async with async_session() as session:
        try:
            result = await sync_doe_data(session)
            sync_state["last_sync_time"] = datetime.now(timezone.utc)
            sync_state["last_sync_result"] = result
            logger.info(f"{source} sync finished. Result: {result}")
        except Exception as e:
            logger.error(f"Error in {source} sync: {e}")
            sync_state["last_sync_result"] = {"status": "error", "message": str(e)}
        finally:
            sync_state["is_syncing"] = False

def start_scheduler() -> None:
    """Starts the background scheduler and registers the sync job."""
    if not scheduler.running:
        scheduler.add_job(
            run_sync,
            args=["scheduled"],
            trigger=IntervalTrigger(hours=settings.SYNC_INTERVAL_HOURS),
            id="doe_sync_job",
            name="DOE PDF scraper synchronization",
            replace_existing=True
        )
        scheduler.start()
        logger.info(f"Background scheduler started. Sync scheduled for every {settings.SYNC_INTERVAL_HOURS} hours.")

def stop_scheduler() -> None:
    """Stops the background scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Background scheduler stopped.")
