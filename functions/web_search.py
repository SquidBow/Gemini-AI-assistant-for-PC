from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import sys
import os

import urllib
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.scrape_url import scrape_url

def web_search(query="Your Search Query", search_type="webpage/image/video", get_results=5, content=False, min_size=None):
    """
    search_type: "webpage", "image", or "video"
    min_size: string like "1920*1080", "1920:1080", or "1920;1080"
    """
    token_limit = 1000000

    if search_type == "video":
        options = Options()
        options.headless = True
        driver = webdriver.Chrome(options=options)
        try:
            url = f"https://www.google.com/search?q={query}&tbm=vid&safe=off"
            driver.get(url)
            import time
            time.sleep(2)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a"))
            )
            results = driver.find_elements(By.CSS_SELECTOR, "a")
            seen = set()
            video_infos = []
            for a in results:
                href = a.get_attribute("href")
                # Exclude TikTok and Google search/advanced search URLs
                if (
                    href
                    and not href.startswith("https://www.google.com/search")
                    and not href.startswith("https://www.google.com/advanced_video_search")
                    and "tiktok.com" not in href
                ):
                    # Normalize URL (remove fragment/query for deduplication)
                    base_href = href.split('&')[0].split('#')[0]
                    if base_href in seen:
                        continue
                    # Try to get the title from the link text or parent
                    title = a.text.strip()
                    if not title:
                        try:
                            title = a.find_element(By.XPATH, "..").text.strip()
                        except Exception:
                            title = "[No title]"
                    # Only add if it's a likely video link
                    if "/video" in href or "/watch" in href or "video" in href:
                        video_infos.append(f"{title}\n{href}")
                        seen.add(base_href)
                if len(video_infos) >= get_results:
                    break

            if not video_infos:
                return f"Google Video search results from: {url}\nNo videos found."
            return f"Google Video URLs from: {url}\n" + "\n\n".join(video_infos)
        finally:
            driver.quit()

    if search_type == "image":
        import time
        from selenium.webdriver.common.keys import Keys
        from PIL import Image
        import requests
        from io import BytesIO

        # --- Parse min_size ---
        min_width = min_height = None
        if min_size:
            for sep in ('*', ':', ';', 'x', 'X'):
                if sep in min_size:
                    try:
                        min_width, min_height = map(int, min_size.split(sep))
                    except Exception:
                        pass
                    break

        options = Options()
        options.headless = True
        driver = webdriver.Chrome(options=options)
        try:
            query_encoded = urllib.parse.quote_plus(query)
            url = f"https://duckduckgo.com/?q={query_encoded}&iax=images&ia=images&kp=-2"
            driver.get(url)
            try:
                consent_btn = driver.find_element(By.CSS_SELECTOR, "form button[type='submit']")
                consent_btn.click()
            except Exception:
                pass

            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "img"))
            )

            images_info = []
            checked_thumbs = set()
            scroll_attempts = 0

            while len(images_info) < get_results and scroll_attempts < 20:
                thumbnails = driver.find_elements(By.TAG_NAME, "img")
                for idx, thumb in enumerate(thumbnails):
                    if len(images_info) >= get_results:
                        break
                    if thumb in checked_thumbs:
                        continue
                    checked_thumbs.add(thumb)
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", thumb)
                        driver.execute_script("arguments[0].click();", thumb)
                        try:
                            WebDriverWait(driver, 0.2).until(
                                lambda d: any(
                                    (l.text.strip() in ["Переглянути файл", "View file"]) and l.get_attribute("href")
                                    for l in d.find_elements(By.TAG_NAME, "a")
                                )
                            )
                        except Exception:
                            pass

                        imgs = driver.find_elements(By.TAG_NAME, "img")
                        links = driver.find_elements(By.TAG_NAME, "a")
                        img_url = None
                        for link in links:
                            text = link.text.strip()
                            href = link.get_attribute("href")
                            if text in ["Переглянути файл", "View file"] and href and href.startswith("http"):
                                img_url = href
                                break

                        if not img_url:
                            for img in imgs:
                                src = img.get_attribute("src")
                                if src and src.startswith("http") and not src.startswith("https://duckduckgo.com/assets/"):
                                    if not src.endswith(".svg") and not src.startswith("data:"):
                                        img_url = src
                                        break

                        # --- Filter by min_size if set ---
                        if img_url and (min_width or min_height):
                            try:
                                resp = requests.get(img_url, timeout=10)
                                img = Image.open(BytesIO(resp.content))
                                width, height = img.size
                                if (min_width and width < min_width) or (min_height and height < min_height):
                                    img_url = None  # Skip this image
                            except Exception:
                                img_url = None  # Skip if can't fetch or open image

                        if img_url:
                            img_result = scrape_url(img_url)
                            if img_result.get("type") == "image":
                                images_info.append({
                                    "url": img_url,
                                    "ascii_art": img_result.get("ascii_art"),
                                    "mime_type": img_result.get("mime_type"),
                                })
                        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                    except Exception:
                        continue

                # Scroll to load more thumbnails if needed
                if len(images_info) < get_results:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(0.5)  # Reduced from 1 second to 0.2 seconds
                    scroll_attempts += 1

            if not images_info:
                return {
                    "type": "image_search",
                    "images": [],
                    "text": f"Image search results from: {url}\nNo images found.",
                    "ai_text": f"Image search results from: {url}\nNo images found."
                }

            # Prepare a text summary for the user (NO ASCII ART)
            summary_lines = []
            for img in images_info:
                summary_lines.append(f"Image: {img['url']}\n[MIME: {img['mime_type']}]\n")

            # Prepare a text summary for the AI
            ai_summary_lines = []
            for img in images_info:
                if content:
                    ai_summary_lines.append(f"Image: {img['url']}\n[MIME: {img['mime_type']}]\n{img['ascii_art']}\n")
                else:
                    ai_summary_lines.append(f"Image: {img['url']}\n[MIME: {img['mime_type']}]\n")

            return {
                "type": "image_search",
                "images": images_info,
                "text": "\n".join(summary_lines),      # For user: no ASCII art
                "ai_text": "\n".join(ai_summary_lines) # For AI: with or without ASCII art
            }
        finally:
            driver.quit()

    # Default: webpage search using hybrid logic
    try:
        results = hybrid_web_search(query, get_results=get_results)

        summary_lines = []
        aggregated = []
        total_tokens = 0
        for res in results:
            if res.get("type") == "webpage":
                text = res.get("text", "")
                if not text.strip():
                    continue
                title = res.get("title", "")
                url = res.get("url", "") if res.get("url") else res.get("source_url", "")
                # Fallback: if url is not present, use the title if it's a URL, else leave blank
                if not url:
                    url = res.get("title") if res.get("title", "").startswith("http") else ""
                # For user: summary (title + url)
                summary_lines.append(f"Site: {title}\n{url}")
                # For AI: aggregate content
                if content:
                    chunk = f"Site: {title}\n{url}\n{text.strip()}\n"
                else:
                    chunk = f"Site: {title}\n{url}\n"
                chunk_tokens = len(chunk)
                if total_tokens + chunk_tokens > token_limit:
                    chunk = chunk[:token_limit - total_tokens]
                    aggregated.append(chunk)
                    break
                aggregated.append(chunk)
                total_tokens += chunk_tokens
            if total_tokens >= token_limit:
                break

        summary = "\n\n".join(summary_lines)
        full_content = "\n".join(aggregated)

        if summary_lines:
            return {
                "summary": summary,
                "text": full_content,
                "type": "webpage",
                "ai_text": full_content,  # For AI: with or without content, depending on content
                "content": content
            }
        else:
            return {
                "summary": "No results found.",
                "text": "",
                "type": "webpage",
                "ai_text": "",
                "content": content
            }
    except Exception as e:
        return f"An error occurred: {e}", ""

def fetch_with_retries(url, retries=3, delay=2):
    """internal"""
    import requests
    for attempt in range(retries):
        try:
            return requests.get(url, timeout=15)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise e
            

def compare_scrape_methods(url):
    """internal. Compare scraping with requests+BeautifulSoup vs Selenium."""
    print(f"\n=== Scraping: {url} ===")

    # Method 1: requests + BeautifulSoup
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        print("\n--- requests + BeautifulSoup ---")
        print(text[:1000] + ("..." if len(text) > 1000 else ""))
    except Exception as e:
        print(f"requests+BeautifulSoup failed: {e}")

    # Method 2: Selenium
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        options = Options()
        options.headless = True
        driver = webdriver.Chrome(options=options)
        driver.get(url)
        selenium_text = driver.find_element(By.TAG_NAME, "body").text
        print("\n--- Selenium ---")
        print(selenium_text[:1000] + ("..." if len(selenium_text) > 1000 else ""))
        driver.quit()
    except Exception as e:
        print(f"Selenium failed: {e}")

def hybrid_scrape_url(url):
    """Internal Try requests+BeautifulSoup first, then Selenium if needed."""
    # Try requests+BeautifulSoup
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        title_tag = soup.find("title")
        title = title_tag.text.strip() if title_tag else url
        if text.strip():
            return {"type": "webpage", "title": title, "text": text, "url": url}
    except Exception:
        pass

    # Fallback: Selenium
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        options = Options()
        options.headless = True
        driver = webdriver.Chrome(options=options)
        driver.get(url)
        selenium_text = driver.find_element(By.TAG_NAME, "body").text
        title = driver.title or url
        driver.quit()
        if selenium_text.strip():
            return {"type": "webpage", "title": title, "text": selenium_text, "url": url}
    except Exception:
        pass

    return {"type": "webpage", "title": url, "text": "", "url": url}

def hybrid_web_search(query, get_results=3):
    """Internal Search DuckDuckGo and scrape each result with hybrid_scrape_url."""
    import urllib.parse
    import urllib.request
    from bs4 import BeautifulSoup

    safe_search_param = "kp=-2"
    query_encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={query_encoded}&{safe_search_param}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as response:
        html = response.read()
        soup = BeautifulSoup(html, 'html.parser')

    urls = []
    for a in soup.find_all('a', class_='result__a'):
        link = a['href']
        if link.startswith("//duckduckgo.com/l/?uddg="):
            import urllib.parse
            parsed = urllib.parse.urlparse(link)
            query_params = urllib.parse.parse_qs(parsed.query)
            real_url = query_params.get('uddg', [link])[0]
            real_url = urllib.parse.unquote(real_url)
        else:
            real_url = link
        urls.append(real_url)
        if len(urls) >= get_results:
            break

    results = []
    for idx, site_url in enumerate(urls, 1):
        result = hybrid_scrape_url(site_url)
        results.append(result)

    return results
