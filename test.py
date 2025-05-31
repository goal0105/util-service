#!/usr/bin/env python3
import json, re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Callable
from utils.logger import logger
from services.supabase import DBConnection
import os
import httpx

# ——— helpers ————————————————————————————————————————————————————————
def _strip_ns(tag: str) -> str:
    """Remove XML namespace from a tag."""
    return re.sub(r"\{.*?\}", "", tag)

def _guid_key(val: str) -> str | int:
    """
    Choose the best sortable key for a guid:
    • If it's all digits → return int(val)
    • else → return the raw string (lexicographic sort)
    """
    return int(val) if val and val.isdigit() else val

db = DBConnection()

# ——— core ————————————————————————————————————————————————————————————
async def parse_rss(source: str | Path,
              sort_key: Callable[[str], str | int] = _guid_key,
              descending: bool = False) -> List[Dict[str, str]]:
    """Parse *path* and return a list[dict] sorted by guid."""
    
    if source.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(source)
            r.raise_for_status()
            xml_bytes = r.content
        root = ET.fromstring(xml_bytes)
    else:                                        # treat as local file
        root = ET.parse(source).getroot()
    items: List[Dict[str, str]] = []
    for item in root.findall(".//item"):
        rec: Dict[str, str] = { _strip_ns(c.tag): (c.text or "").strip()
                                for c in item }
        items.append(rec)

    logger.info(f"News Count : {items.count}")

    # ---- sort by guid ----
    items.sort(key=lambda d: sort_key(d.get("guid", "")), reverse=descending)
    global db 
    try : 
        # Initialize database
        await db.initialize()
        client = await db.client
        logger.info(f"Updating Database...")
        for item in items:
            data = {
                    "item_id":          item.get("itemID"),
                    "title":            item.get("title"),
                    "link":             item.get("link"),
                    "photographer":     item.get("Photographer"),
                    "pub_date":         item.get("pubDate"),     # consider parsing to datetime
                    "description":      item.get("description"),
                    "content":          item.get("encoded"),
                    "dcterms_modified": item.get("modified"),
                    "is_video":         item.get("isVideo"),
                    "dc_creator":       item.get("creator"),
                    "media_keywords":   item.get("keywords"),
                    "category":         item.get("category"),
            }
            resp = await (client.table('rss_feed')
                    .upsert(
                        data, 
                        on_conflict="item_id",
                        ignore_duplicates=True
                        )
                    .execute()
            )

            if resp.data:                           #  ← only true when a row was returned
                item_id = resp.data[0]['item_id']
                logger.info("Inserted item_id=%s", item_id)
            else:
                logger.debug("Skipped duplicate %s", data["item_id"])
        
        logger.info(f"Updated Database...")

        
    
    except Exception as e :
        logger.error(f"Supabase Database Initialization failed : {e}")
        raise

import asyncio

if os.name == "nt":                              
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main() -> None:
    rss_file = "https://www.maariv.co.il/Rss/RssFeedsAllNews?id=msn"          # adjust as needed
    await parse_rss(rss_file)          # ← await the async function
    # await db.disconnect()
    # await asyncio.sleep(0)

if __name__ == "__main__":
    asyncio.run(main())