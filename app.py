from __future__ import annotations
from flask import Flask, request, jsonify, abort
from groq import Groq
from dotenv import load_dotenv
from pathlib import Path
from queue import Queue
from services.downloader import Download
from services.supabase import DBConnection
from typing import List, Dict, Callable
from threading import Thread
from urllib.parse import urlparse, urljoin, parse_qs, urlencode, urlunparse

import nest_asyncio
import asyncio
import html
import httpx
import os
import re
import requests
import tempfile
from utils.logger import logger
import xml.etree.ElementTree as ET


app = Flask(__name__)
downloader = Download()
nest_asyncio.apply()
rss_url = "https://www.maariv.co.il/Rss/RssFeedsAllNews?id=msn"

load_dotenv()  # pulls variables from .env into process env

if os.name == "nt":                              
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

groq_client = Groq()

META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\']?\s*\d+\s*;\s*url=(.*?)["\'>]',
    re.IGNORECASE
)

def _follow_meta_refresh(html_text: str, base_url: str) -> str | None:
    """
    Look for <meta http-equiv="refresh" …> and return the absolute URL, or None.
    """
    match = META_REFRESH_RE.search(html_text)
    if not match:
        return None
    target = html.unescape(match.group(1).strip())
    return urljoin(base_url, target)

def resolve_url(url: str,
                *,
                timeout: float = 10.0,
                max_hops: int = 10,
                follow_meta: bool = False) -> str:
    """
    Return the final landing URL after following up to `max_hops` redirects.

    Parameters
    ----------
    url : str
        The starting URL.
    timeout : float, default 10 s
        Network timeout for each request.
    max_hops : int, default 10
        Safety limit to avoid redirect loops.
    follow_meta : bool, default False
        Also chase HTML meta-refresh redirects (one extra GET at most).
    """
    session = requests.Session()
    session.max_redirects = max_hops       # extra guard

    try:
        # Try HEAD first – it’s lighter, but some sites forbid it.
        resp = session.head(url,
                            allow_redirects=True,
                            timeout=timeout,
                            headers={"User-Agent": "python-redirect-check/1.0"})
        final_url = resp.url
        if resp.is_redirect:               # still in a redirect chain
            final_url = resp.headers["Location"]
    except requests.exceptions.RequestException:
        # Fall back to GET (handles sites that disallow HEAD)
        resp = session.get(url,
                           allow_redirects=True,
                           timeout=timeout,
                           headers={"User-Agent": "python-redirect-check/1.0"})
        final_url = resp.url

    # Optional: follow one level of <meta http-equiv="refresh"> in the landing page
    if follow_meta and resp.ok and "text/html" in resp.headers.get("content-type", ""):
        next_url = _follow_meta_refresh(resp.text, final_url)
        if next_url:
            # one extra GET to verify (avoids infinite loops)
            try:
                resp = session.get(next_url,
                                   allow_redirects=True,
                                   timeout=timeout)
                final_url = resp.url
            except requests.exceptions.RequestException:
                pass

    return final_url

@app.route("/information", methods=["GET"])
def server_info():
    logger.info("Server information")
    return jsonify("Utility Service"), 200

def canonical_youtube_url(url: str, keep_params=('v',)) -> str:
    """
    Return a cleaned-up YouTube URL that keeps only the query
    parameters listed in *keep_params* (defaults to just 'v').
    
    Examples
    --------
    >>> canonical_youtube_url(
    ...     "https://www.youtube.com/watch?v=JiJeZOHx0ow&pp=0gcJCdgAo7VqN5tD")
    'https://www.youtube.com/watch?v=JiJeZOHx0ow'
    
    >>> canonical_youtube_url(
    ...     "https://youtu.be/JiJeZOHx0ow?t=60", keep_params=())
    'https://youtu.be/JiJeZOHx0ow'
    """
    parsed = urlparse(url)
    
    # Short youtu.be links rarely need any changes—just drop the query/fragment.
    if parsed.netloc.endswith("youtu.be"):
        return f"https://{parsed.netloc}{parsed.path}"
    
    # Long form: https://www.youtube.com/watch?v=...
    if parsed.netloc.endswith("youtube.com") and parsed.path == "/watch":
        qs = parse_qs(parsed.query)
        # Retain only the desired parameters (order-preserving).
        new_qs = [(k, v) for k, vs in qs.items() for v in vs if k in keep_params]
        new_query = urlencode(new_qs, doseq=True)
        cleaned = parsed._replace(query=new_query, fragment="")
        return urlunparse(cleaned)
    
    # Anything else: return untouched.
    return url

"""
Translate from audio url using Groq.
"""
@app.route("/audio/translation", methods=["POST"])
def audio_translation():
    """
    POST  { "audio_url": "https://www.youtube.com/watch?v=eWRfhZUzrAc" }
    └─▶  { "text": "…transcript…" }
    """
    if not request.is_json:
        abort(400, description="Body must be JSON")

    audio_url = request.get_json(silent=True, force=True).get("audio_url")
    if not audio_url:
        abort(400, description="`audio_url` is required")

    final_url = resolve_url(audio_url)

    if "youtube" in final_url.lower():
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                
                logger.info("Youtube Translation Processing")
                youtube_url = canonical_youtube_url(final_url)
                                
                # ── 1.  Download the file safely to a temp location ────────────────
                logger.info("Downloading Youtube started.")

                downloaded_path = downloader.download_youtube_audio(youtube_url, temp_dir)
                
                logger.info("Downloading Youtube started.")
                logger.info("Donwloading Youtube completed successfully.")

                # ── 2.  Translation with Groq ────────────────────────────────────
                logger.info("Audio Translation Started.")
                
                with open(downloaded_path, "rb") as file:
                    translation = groq_client.audio.translations.create(
                        file=(downloaded_path, file.read()),
                        model="whisper-large-v3",
                        response_format="json",  # Optional
                        temperature=0.0  # Optional
                        )

                logger.info("\nAudio Translation Completed.\n")                            
                return jsonify(text=translation.text), 200
            
        except Exception as e:
            logger.error("Youtube Translation Error Failed.")                            
            abort(400, description=f"Downloading Youtube or Translation Failed\n: {e}")
    else : 
        print("TODO")

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

async def update_rss(source: str | Path,
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

    logger.info(f"News Count : {len(items)}")

    # ---- sort by guid ----
    items.sort(key=lambda d: sort_key(d.get("guid", "")), reverse=descending)
    try : 
        # Initialize database
        db = DBConnection()
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
        
        await db.disconnect()
        logger.info(f"Updated Database")
        
    
    except Exception as e :
        logger.error(f"Supabase Database Initialization failed : {e}")
        raise

def run_async(coroutine):
    result_queue = Queue()

    def wrapper():
        result = asyncio.run(coroutine)
        result_queue.put(result)

    thread = Thread(target=wrapper)
    thread.start()
    thread.join()
    return result_queue.get()

"""
Save updated news data into Supabase Database.
"""
@app.route("/update-rss", methods=["GET"])
async def update_news_content():
    try:
        run_async(update_rss(rss_url))
        logger.info("update_rss(%s)", rss_url)
        return jsonify({"status": "ok"}), 200
    
    except Exception as exc:
        logger.exception("RSS update failed for %s", rss_url)
        return jsonify({
            "status": "error",
            "message": str(exc),
        }), 500

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
