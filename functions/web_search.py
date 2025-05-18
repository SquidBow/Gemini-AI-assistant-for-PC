from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
import time
import sys
import os
import urllib
from PIL import Image
import requests
from io import BytesIO
import re

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.scrape_url import scrape_url

def web_search(query="Your Search Query", search_type="webpage/image/video", get_results=5, content=False, min_img_size="100x100"):
    """
    search_type: "webpage", "image", or "video"
    min_size: string like "1920*1080", "1920:1080", or "1920;1080"
    """
    token_limit = 1000000

    if search_type == "video":
        options = Options()
        options.add_argument("--headless=new")  # Use the new headless mode
        driver = webdriver.Chrome(options=options)
        try:
            url = f"https://www.google.com/search?q={query}&tbm=vid&safe=off"
            driver.get(url)
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
    # Use the improved Google Images search
        return search_google_images(query, get_results=get_results, min_img_size=min_img_size, content=content)

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
        from selenium.webdriver.common.by import By
        options = Options()
        options.add_argument("--headless=new")  # Use the new headless mode
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
        options.add_argument("--headless=new")
        driver = webdriver.Chrome(options=options)
        driver.get(url)
        selenium_text = driver.find_element(By.TAG_NAME, "body").text
        title = driver.title or url
        driver.quit()
        if selenium_text.strip():
            return {"type": "webpage", "title": title, "text": selenium_text, "url": url}
    except Exception:
        # Fallback: undetected-chromedriver
        try:
            import undetected_chromedriver as uc
            options = uc.ChromeOptions()
            # Uncomment and set your profile if needed:
            # options.add_argument(r'--user-data-dir=C:\Users\denis\AppData\Local\Google\Chrome\User Data')
            # options.add_argument(r'--profile-directory=Profile 4')
            driver = uc.Chrome(options=options)
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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    }
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


def search_google_images(query, get_results=5, min_img_size=None, content=False):
    """
    Clicks each Google Images thumbnail (only large ones) to open the side panel.
    Returns a dict with image search results, similar to web_search.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import urllib

    min_min_width = min_min_height = 100  # Always filter out tiny thumbnails (not user-configurable)
    min_width = min_height = 100          # Default min size for final image
    if min_img_size:
        for sep in ('*', ':', ';', 'x', 'X'):
            if sep in min_img_size:
                try:
                    min_width, min_height = map(int, min_img_size.split(sep))
                except Exception:
                    pass
                break

    options = Options()
    options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=options)
    images_info = []
    try:
        query_encoded = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?tbm=isch&q={query_encoded}&safe=off"
        driver.get(url)

        scroll_attempts = 0
        clicked = 0
        checked_thumbs = set()
        while clicked < get_results and scroll_attempts < 20:
            thumbnails = driver.find_elements(By.CSS_SELECTOR, "img.YQ4gaf")
            for idx, thumb in enumerate(thumbnails):
                if clicked >= get_results:
                    break
                if thumb in checked_thumbs:
                    continue
                checked_thumbs.add(thumb)
                try:
                    width = int(thumb.get_attribute("width") or 0)
                    height = int(thumb.get_attribute("height") or 0)
                    alt = thumb.get_attribute("alt")
                    # Always skip tiny thumbnails
                    if width < min_min_width or height < min_min_height:
                        continue
                    if not thumb.is_displayed() or not thumb.is_enabled():
                        continue
                    driver.execute_script("arguments[0].scrollIntoView(true);", thumb)
                    driver.execute_script("arguments[0].click();", thumb)
                    try:
                        side_img = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "img[jsname='kn3ccd']"))
                        )
                        WebDriverWait(driver, 3).until(
                            lambda d: driver.execute_script("return arguments[0].naturalWidth;", side_img) > 1
                        )
                    except Exception as e:
                        print(f"Side panel image did not load: {e}")
                        continue
                    src = side_img.get_attribute("src")
                    real_url = None
                    img_width = img_height = 0
                    if src and src.startswith("http"):
                        real_url = src
                        img_width = driver.execute_script("return arguments[0].naturalWidth;", side_img)
                        img_height = driver.execute_script("return arguments[0].naturalHeight;", side_img)

                    # Now filter by your actual min_size requirement
                    if real_url and img_width >= min_width and img_height >= min_height:
                        img_result = scrape_url(real_url)
                        images_info.append({
                            "url": real_url,
                            "ascii_art": img_result.get("ascii_art"),
                            "mime_type": img_result.get("mime_type"),
                            "image_bytes": img_result.get("image_bytes"),
                            "text": alt or "[Image]",
                            "path": None,
                        })
                        clicked += 1
                except Exception as e:
                    print(f"Exception: {e}")
                    continue
            if clicked < get_results:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                WebDriverWait(driver, 2).until(
                    lambda d: len(d.find_elements(By.CSS_SELECTOR, "img.YQ4gaf")) > len(checked_thumbs)
                )
                scroll_attempts += 1
    finally:
        driver.quit()

    if content:
        return {
            "type": "image_search",
            "images": [
                {
                    "type": "image",
                    "image_bytes": img["image_bytes"],
                    "mime_type": img["mime_type"],
                    "ascii_art": img["ascii_art"],
                    "text": img["text"],
                    "path": img["url"],
                }
                for img in images_info
            ]
        }
    else:
        summary = "\n".join([f"Image: {img['text']}\nURL: {img['url']}\n" for img in images_info])
        return {
            "type": "text",
            "content": summary
        }