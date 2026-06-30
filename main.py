"""
vidking.net m3u8 / stream URL extractor
Adapted for GitHub Actions — manual workflow_dispatch trigger.

Usage (GitHub Actions inputs are passed as CLI args):
  python extractor.py --tmdb 76479 --type tv --season 5 --episode 8
  python extractor.py --tmdb 550   --type movie
  python extractor.py --url "https://www.vidking.net/embed/tv/76479/5/8/"
"""

import argparse
import asyncio
import json
import re
import sys
import time

import requests

DEFAULT_URL = "https://www.vidking.net/embed/tv/76479/5/8/"

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
# Helpers
# ─────────────────────────────────────────────

def log(msg: str):
    """Print with flush so GitHub Actions captures output in real time."""
    print(msg, flush=True)


def parse_embed_url(url: str):
    m = re.search(r"/embed/(tv|movie)/(\d+)(?:/(\d+)/(\d+))?", url)
    if not m:
        raise ValueError(f"Cannot parse embed URL: {url}")
    return m.group(1), m.group(2), m.group(3), m.group(4)


# ─────────────────────────────────────────────
# Metadata
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


# ─────────────────────────────────────────────
# Direct API extraction
# ─────────────────────────────────────────────

def try_direct_api(
    media_type: str, tmdb_id: str, season: str, episode: str
) -> list:
    try:
        title, year, imdb_id = get_show_meta(tmdb_id, media_type)
        log(f"[*] Metadata: title={title!r}  year={year}  imdb={imdb_id}")
    except Exception as e:
        log(f"[warn] Metadata fetch failed: {e}")
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

    log("[*] Calling sources API …")
    r = requests.get(
        "https://api.videasy.net/mb-flix/sources-with-title",
        params=params,
        headers=BASE_HEADERS,
        timeout=20,
    )

    log(f"[*] Sources API status: {r.status_code}")
    if r.status_code != 200:
        return []

    text = r.text
    urls = re.findall(r'https?://[^\s"\'\\<>]+\.m3u8[^\s"\'\\<>]*', text)
    if not urls:
        urls = re.findall(r'https?://[^\s"\'\\<>]+\.mpd[^\s"\'\\<>]*', text)
    return list(dict.fromkeys(urls))


# ─────────────────────────────────────────────
# Playwright fallback
# ─────────────────────────────────────────────

async def extract_with_playwright(embed_url: str, timeout: int = 45) -> list:
    from playwright.async_api import async_playwright

    found = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=UA,
            extra_http_headers={"accept-language": "en-US,en;q=0.9"},
        )
        page = await ctx.new_page()

        async def on_response(response):
            url = response.url
            if re.search(r'\.(m3u8|mpd)(\?|$)', url):
                if url not in found:
                    log(f"[+] Intercepted stream: {url}")
                    found.append(url)
            if "sources-with-title" in url and response.status == 200:
                try:
                    body = await response.body()
                    text = body.decode("utf-8", errors="ignore")
                    for u in re.findall(
                        r'https?://[^\s"\'\\<>]+\.m3u8[^\s"\'\\<>]*', text
                    ):
                        if u not in found:
                            found.append(u)
                    for u in re.findall(
                        r'https?://[^\s"\'\\<>]+\.mpd[^\s"\'\\<>]*', text
                    ):
                        if u not in found:
                            found.append(u)
                except Exception:
                    pass

        page.on("response", on_response)

        log(f"[*] Playwright loading: {embed_url}")
        await page.goto(
            embed_url, wait_until="networkidle", timeout=timeout * 1000
        )

        deadline = time.time() + 15
        while not found and time.time() < deadline:
            await page.wait_for_timeout(1000)

        await browser.close()

    return found


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract m3u8/mpd stream URLs from vidking.net"
    )
    parser.add_argument("--url", default=None, help="Full embed URL")
    parser.add_argument("--tmdb", help="TMDB ID")
    parser.add_argument(
        "--type", dest="media_type", default="tv", choices=["tv", "movie"]
    )
    parser.add_argument("--season", default="1")
    parser.add_argument("--episode", default="1")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument(
        "--playwright-only",
        action="store_true",
        help="Skip direct API and go straight to Playwright",
    )
    args = parser.parse_args()

    # Build embed URL from TMDB or parse from --url
    if args.tmdb:
        media_type = args.media_type
        tmdb_id = args.tmdb
        season = args.season
        episode = args.episode
        embed_url = (
            f"https://www.vidking.net/embed/{media_type}/{tmdb_id}"
            + (f"/{season}/{episode}/" if media_type == "tv" else "/")
        )
    elif args.url:
        embed_url = args.url
        media_type, tmdb_id, season, episode = parse_embed_url(embed_url)
    else:
        embed_url = DEFAULT_URL
        media_type, tmdb_id, season, episode = parse_embed_url(embed_url)

    log(f"[*] Target : {embed_url}")
    log(f"[*] Type={media_type}  TMDB={tmdb_id}  S={season}  E={episode}")

    urls = []

    # ── Step 1: direct API (unless --playwright-only)
    if not args.playwright_only:
        log("\n[*] Attempting direct API extraction …")
        urls = try_direct_api(media_type, tmdb_id, season, episode)

    # ── Step 2: Playwright fallback
    if not urls:
        log("\n[*] Direct API returned no stream URLs — launching Playwright …")
        try:
            urls = asyncio.run(
                extract_with_playwright(embed_url, timeout=args.timeout)
            )
        except ImportError:
            log("[!] Playwright not installed.")
            log("    pip install playwright && playwright install chromium")
            sys.exit(1)
        except Exception as e:
            log(f"[!] Playwright error: {e}")
            sys.exit(1)

    # ── Output
    if urls:
        log(f"\n[+] Found {len(urls)} stream URL(s):\n")
        for u in urls:
            log(u)
    else:
        log("\n[-] No stream URLs found.")
        sys.exit(1)


if __name__ == "__main__":
    main()