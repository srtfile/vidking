"""
vidking.net m3u8 / stream URL extractor
Render-deployable FastAPI web service.

Endpoints:
  GET /extract?tmdb=76479&type=tv&season=5&episode=8
  GET /extract?tmdb=550&type=movie
  GET /extract?url=https://www.vidking.net/embed/tv/76479/5/8/
"""

import asyncio
import re
import time

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="VidKing Stream Extractor")

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

def parse_embed_url(url: str):
    m = re.search(r"/embed/(tv|movie)/(\d+)(?:/(\d+)/(\d+))?", url)
    if not m:
        raise ValueError(f"Cannot parse embed URL: {url}")
    return m.group(1), m.group(2), m.group(3), m.group(4)


# ─────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────

def get_show_meta(tmdb_id: str, media_type: str = "tv"):
    url = f"https://db.videasy.to/3/{media_type}/{tmdb_id}"
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

def try_direct_api(media_type: str, tmdb_id: str, season: str, episode: str) -> list:
    try:
        title, year, imdb_id = get_show_meta(tmdb_id, media_type)
    except Exception:
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
                    found.append(url)
            if "sources-with-title" in url and response.status == 200:
                try:
                    body = await response.body()
                    text = body.decode("utf-8", errors="ignore")
                    for u in re.findall(r'https?://[^\s"\'\\<>]+\.m3u8[^\s"\'\\<>]*', text):
                        if u not in found:
                            found.append(u)
                    for u in re.findall(r'https?://[^\s"\'\\<>]+\.mpd[^\s"\'\\<>]*', text):
                        if u not in found:
                            found.append(u)
                except Exception:
                    pass

        page.on("response", on_response)
        await page.goto(embed_url, wait_until="networkidle", timeout=timeout * 1000)

        deadline = time.time() + 15
        while not found and time.time() < deadline:
            await page.wait_for_timeout(1000)

        await browser.close()

    return found


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>VidKing Stream Extractor</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background: #0f0f0f;
      color: #e0e0e0;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 40px 16px;
    }
    h1 { font-size: 1.8rem; color: #ff5555; margin-bottom: 6px; }
    p.sub { color: #888; margin-bottom: 32px; font-size: 0.95rem; }
    .card {
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 12px;
      padding: 28px;
      width: 100%;
      max-width: 560px;
    }
    label { display: block; font-size: 0.85rem; color: #aaa; margin-bottom: 4px; margin-top: 16px; }
    input, select {
      width: 100%;
      padding: 10px 12px;
      background: #111;
      border: 1px solid #333;
      border-radius: 8px;
      color: #e0e0e0;
      font-size: 0.95rem;
      outline: none;
    }
    input:focus, select:focus { border-color: #ff5555; }
    .row { display: flex; gap: 12px; }
    .row > div { flex: 1; }
    button {
      margin-top: 24px;
      width: 100%;
      padding: 12px;
      background: #ff5555;
      color: #fff;
      font-size: 1rem;
      font-weight: 600;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      transition: background 0.2s;
    }
    button:hover { background: #e03c3c; }
    button:disabled { background: #555; cursor: not-allowed; }
    #result { margin-top: 28px; width: 100%; max-width: 560px; }
    .result-box {
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 12px;
      padding: 20px;
    }
    .result-box h2 { font-size: 1rem; color: #aaa; margin-bottom: 12px; }
    .url-item {
      background: #111;
      border: 1px solid #2a2a2a;
      border-radius: 8px;
      padding: 10px 14px;
      margin-bottom: 10px;
      word-break: break-all;
      font-size: 0.85rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    .url-item a { color: #ff5555; text-decoration: none; flex: 1; }
    .url-item a:hover { text-decoration: underline; }
    .copy-btn {
      background: #2a2a2a;
      color: #ccc;
      border: none;
      border-radius: 6px;
      padding: 4px 10px;
      font-size: 0.75rem;
      cursor: pointer;
      white-space: nowrap;
      margin-top: 0;
      width: auto;
    }
    .copy-btn:hover { background: #ff5555; color: #fff; }
    .error { color: #ff5555; font-size: 0.9rem; margin-top: 12px; }
    .spinner {
      display: none;
      margin-top: 20px;
      text-align: center;
      color: #888;
      font-size: 0.9rem;
    }
    .spinner.active { display: block; }
  </style>
</head>
<body>
  <h1>🎬 VidKing Extractor</h1>
  <p class="sub">Extract m3u8 / mpd stream URLs from vidking.net</p>

  <div class="card">
    <label>TMDB ID</label>
    <input id="tmdb" type="text" placeholder="e.g. 76479" />

    <label>Media Type</label>
    <select id="type">
      <option value="tv">TV Show</option>
      <option value="movie">Movie</option>
    </select>

    <div class="row" id="ep-row">
      <div>
        <label>Season</label>
        <input id="season" type="number" value="1" min="1" />
      </div>
      <div>
        <label>Episode</label>
        <input id="episode" type="number" value="1" min="1" />
      </div>
    </div>

    <button id="btn" onclick="extract()">Extract Stream URL</button>
    <div class="spinner" id="spinner">⏳ Extracting... this may take up to 45s</div>
  </div>

  <div id="result"></div>

  <script>
    document.getElementById('type').addEventListener('change', function() {
      document.getElementById('ep-row').style.display = this.value === 'movie' ? 'none' : 'flex';
    });

    async function extract() {
      const tmdb = document.getElementById('tmdb').value.trim();
      if (!tmdb) { alert('Please enter a TMDB ID.'); return; }

      const type = document.getElementById('type').value;
      const season = document.getElementById('season').value;
      const episode = document.getElementById('episode').value;

      const btn = document.getElementById('btn');
      const spinner = document.getElementById('spinner');
      const result = document.getElementById('result');

      btn.disabled = true;
      spinner.classList.add('active');
      result.innerHTML = '';

      let url = `/extract?tmdb=${tmdb}&type=${type}`;
      if (type === 'tv') url += `&season=${season}&episode=${episode}`;

      try {
        const res = await fetch(url);
        const data = await res.json();

        if (!res.ok) {
          result.innerHTML = `<p class="error">❌ ${data.detail || 'No stream URLs found.'}</p>`;
        } else {
          let html = `<div class="result-box"><h2>✅ Found ${data.count} stream URL(s)</h2>`;
          data.urls.forEach((u, i) => {
            html += `<div class="url-item">
              <a href="${u}" target="_blank">Stream ${i+1}: ${u}</a>
              <button class="copy-btn" onclick="copyUrl('${u}', this)">Copy</button>
            </div>`;
          });
          html += '</div>';
          result.innerHTML = html;
        }
      } catch (e) {
        result.innerHTML = `<p class="error">❌ Request failed: ${e.message}</p>`;
      } finally {
        btn.disabled = false;
        spinner.classList.remove('active');
      }
    }

    function copyUrl(url, btn) {
      navigator.clipboard.writeText(url);
      btn.textContent = 'Copied!';
      setTimeout(() => btn.textContent = 'Copy', 2000);
    }
  </script>
</body>
</html>
"""


@app.get("/extract")
async def extract(
    tmdb: str = Query(None, description="TMDB ID"),
    type: str = Query("tv", description="tv or movie"),
    season: str = Query("1"),
    episode: str = Query("1"),
    url: str = Query(None, description="Full embed URL (overrides tmdb)"),
    playwright_only: bool = Query(False),
    timeout: int = Query(45),
):
    # Build embed URL
    if url:
        embed_url = url
        media_type, tmdb_id, season, episode = parse_embed_url(url)
    elif tmdb:
        media_type = type
        tmdb_id = tmdb
        embed_url = (
            f"https://www.vidking.net/embed/{media_type}/{tmdb_id}"
            + (f"/{season}/{episode}/" if media_type == "tv" else "/")
        )
    else:
        raise HTTPException(status_code=400, detail="Provide either 'tmdb' or 'url' query param.")

    urls = []

    # Step 1: direct API
    if not playwright_only:
        urls = try_direct_api(media_type, tmdb_id, season, episode)

    # Step 2: Playwright fallback
    if not urls:
        try:
            urls = await extract_with_playwright(embed_url, timeout=timeout)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Playwright error: {e}")

    if not urls:
        raise HTTPException(status_code=404, detail="No stream URLs found.")

    return JSONResponse({"embed_url": embed_url, "count": len(urls), "urls": urls})
