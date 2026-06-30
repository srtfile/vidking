import asyncio
import re
import time
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

app = FastAPI(title="Vidking Extractor API")

# Allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://www.vidking.net",
    "referer": "https://www.vidking.net/",
    "user-agent": UA,
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "sec-fetch-site": "cross-site",
}

# ─────────────────────────────────────────────
# Core Extraction Logic
# ─────────────────────────────────────────────

def get_show_meta(tmdb_id: str, media_type: str = "tv"):
    url = f"https://db.videasy.net/3/{media_type}/{tmdb_id}"
    r = requests.get(
        url,
        params={"append_to_response": "external_ids"},
        headers=BASE_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    title = data.get("name") or data.get("title", "")
    year = (data.get("first_air_date") or data.get("release_date") or "")[:4]
    imdb_id = (data.get("external_ids") or {}).get("imdb_id", "")
    return title, year, imdb_id


def try_direct_api(media_type: str, tmdb_id: str, season: str = "", episode: str = "") -> list:
    try:
        title, year, imdb_id = get_show_meta(tmdb_id, media_type)
    except Exception as e:
        print(f"[warn] Metadata fetch failed: {e}")
        title, year, imdb_id = "", "", ""

    params = {
        "title": title,
        "mediaType": media_type,
        "year": year,
        "tmdbId": tmdb_id,
        "imdbId": imdb_id,
        "_t": str(int(time.time() * 1000)),
    }
    if media_type == "tv" and season and episode:
        params["episodeId"] = episode
        params["seasonId"] = season

    r = requests.get(
        "https://api.videasy.net/mb-flix/sources-with-title",
        params=params,
        headers=BASE_HEADERS,
        timeout=20,
    )

    if r.status_code != 200:
        return []

    text = r.text
    urls = re.findall(r'https?://[^\s"\'\\<>]+\.m3u8[^\s"\'\\<>]*', text)
    if not urls:
        urls = re.findall(r'https?://[^\s"\'\\<>]+\.mpd[^\s"\'\\<>]*', text)
    return list(dict.fromkeys(urls))


async def extract_with_playwright(embed_url: str, timeout: int = 30) -> list:
    from playwright.async_api import async_playwright
    found = []

    async with async_playwright() as p:
        # Chromium args optimized for cloud deployment (Render)
        browser = await p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(
            user_agent=UA,
            extra_http_headers={"accept-language": "en-US,en;q=0.9"},
        )
        page = await ctx.new_page()

        async def on_response(response):
            url = response.url
            if re.search(r'\.(m3u8|mpd)(\?|$)', url):
                if url not in found:
                    found.append(url)
            if "sources-with-title" in url and response.status == 200:
                try:
                    body = await response.body()
                    text = body.decode("utf-8", errors="ignore")
                    for u in re.findall(r'https?://[^\s"\'\\<>]+\.m3u8[^\s"\'\\<>]*', text):
                        if u not in found: found.append(u)
                    for u in re.findall(r'https?://[^\s"\'\\<>]+\.mpd[^\s"\'\\<>]*', text):
                        if u not in found: found.append(u)
                except Exception:
                    pass

        page.on("response", on_response)
        
        try:
            await page.goto(embed_url, wait_until="networkidle", timeout=timeout * 1000)
            deadline = time.time() + 10
            while not found and time.time() < deadline:
                await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"Playwright navigation error: {e}")
        finally:
            await browser.close()

    return found


async def get_stream_urls(media_type: str, tmdb_id: str, season: str = "", episode: str = ""):
    # 1. Try Direct API (running blocking requests in a separate thread)
    urls = await asyncio.to_thread(try_direct_api, media_type, tmdb_id, season, episode)
    
    # 2. Fallback to Playwright
    if not urls:
        embed_url = f"https://www.vidking.net/embed/{media_type}/{tmdb_id}"
        embed_url += f"/{season}/{episode}/" if media_type == "tv" else "/"
        
        try:
            urls = await extract_with_playwright(embed_url)
        except Exception as e:
            print(f"[!] Playwright failed: {e}")
            
    return urls

# ─────────────────────────────────────────────
# API Routes (Redirects to Video)
# ─────────────────────────────────────────────

@app.get("/tv/{tmdb_id}/{season}/{episode}/")
@app.get("/tv/{tmdb_id}/{season}/{episode}")
async def get_tv(tmdb_id: str, season: str, episode: str):
    urls = await get_stream_urls("tv", tmdb_id, season, episode)
    if not urls:
        raise HTTPException(status_code=404, detail="No stream URLs found.")
    
    # Redirect directly to the stream URL
    return RedirectResponse(url=urls[0], status_code=302)


@app.get("/movie/{tmdb_id}/")
@app.get("/movie/{tmdb_id}")
async def get_movie(tmdb_id: str):
    urls = await get_stream_urls("movie", tmdb_id)
    if not urls:
        raise HTTPException(status_code=404, detail="No stream URLs found.")
    
    # Redirect directly to the stream URL
    return RedirectResponse(url=urls[0], status_code=302)