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

def video_search(query, number_of_results="5"):
    """
    Search for videos on Google Video search and return only video titles (names) and lengths (durations), ensuring unique results.
    """
    # Convert string to int
    if isinstance(number_of_results, str):
        number_of_results = int(number_of_results)
    
    options = Options()
    options.add_argument("--headless=new")  # Use the new headless mode
    driver = webdriver.Chrome(options=options)
    try:
        url = f"https://www.google.com/search?q={query}&tbm=vid&safe=off"
        driver.get(url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a"))
        )
        results = driver.find_elements(By.CSS_SELECTOR, 'a[class*="rIRoqf"]')
        seen_titles = set()
        video_infos = []
        for a in results:
            href = a.get_attribute("href")
            if not href:
                continue
            title = a.get_attribute("aria-label") or a.text.strip()
            if not title:
                try:
                    title = a.find_element(By.XPATH, "..").text.strip()
                except Exception:
                    title = "[No title]"
            # Try to find the duration (length) in the parent or sibling elements
            duration = ""
            try:
                parent = a.find_element(By.XPATH, "../..")
                time_spans = parent.find_elements(By.XPATH, ".//span")
                for span in time_spans:
                    text = span.text.strip()
                    if re.match(r"^\d{1,2}:\d{2}$", text) or re.match(r"^\d{1,2}:\d{2}:\d{2}$", text):
                        duration = text
                        break
            except Exception:
                pass
            result_str = f"{title} [{duration}]" if duration else title
            if result_str in seen_titles:
                continue
            seen_titles.add(result_str)
            video_infos.append(result_str)
            if len(video_infos) >= number_of_results:
                break
        if not video_infos:
            return f"Google Video search results from: {url}\nNo videos found."
        return "\n".join(video_infos)
    finally:
        driver.quit()

def web_search(query="Your Search Query", number_of_results="3", content=False):
    """
    search_type: "webpage", "image", or "video"
    min_size: string like "1920*1080", "1920:1080", or "1920;1080"
    """
    token_limit = 1000000

    try:
        # Ensure number_of_results is an integer
        if isinstance(number_of_results, str):
            number_of_results = int(number_of_results)
        
        results = hybrid_web_search(query, number_of_results=number_of_results)
        seen_urls = set()
        summary_lines = []
        aggregated = []
        total_tokens = 0
        for res in results:
            url = res.get("url", "") if res.get("url") else res.get("source_url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if res.get("type") == "webpage":
                text = res.get("text", "")
                if not text.strip():
                    continue
                title = res.get("title", "")
                # Fallback: if url is not present, use the title if it's a URL, else leave blank
                if not url:
                    url = res.get("title") if res.get("title", "").startswith("http") else ""
                # For user: summary (title + url)
                summary_lines.append(f"Site: {title}\n{url}")
                # For AI: aggregate content
                if content:
                    chunk = f"Scraped site: {title}\n{url}\n{text.strip()}\n"
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
                "ai_text": full_content,
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

def hybrid_web_search(query, number_of_results=3):
    """Internal Search DuckDuckGo and scrape each result with hybrid_scrape_url."""
    import urllib.parse
    import urllib.request
    from bs4 import BeautifulSoup

    # Ensure number_of_results is an integer
    if isinstance(number_of_results, str):
        number_of_results = int(number_of_results)
    
    if number_of_results is None:
        number_of_results = 3  # or any sensible default

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
        if len(urls) >= number_of_results:
            break

    results = []
    for idx, site_url in enumerate(urls, 1):
        result = hybrid_scrape_url(site_url)
        results.append(result)

    return results


def image_search(query, number_of_results="5",  min_img_size="100x100", exact_size = False, content=False):
    """
    Clicks each Google Images thumbnail (only large ones) to open the side panel.
    Returns a dict with image search results, similar to web_search. Ensures unique image URLs.
    """
    # Convert string to int
    if isinstance(number_of_results, str):
        number_of_results = int(number_of_results)
    
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import urllib

    min_min_width = min_min_height = 100  # Always filter out tiny thumbnails (not user-configurable)
    min_width = min_height = 100          # Default min size for final image
    if  min_img_size:
        for sep in ('*', ':', ';', 'x', 'X'):
            if sep in  min_img_size:
                try:
                    min_width, min_height = map(int,  min_img_size.split(sep))
                except Exception:
                    pass
                break

    options = Options()
    options.add_argument("--headless=new")  # Use the new headless mode
    driver = webdriver.Chrome(options=options)
    driver.minimize_window()
    images_info = []
    seen_urls = set()
    try:
        query_encoded = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?tbm=isch&q={query_encoded}&safe=off"
        driver.get(url)

        scroll_attempts = 0
        clicked = 0
        checked_thumbs = set()
        while clicked < number_of_results and scroll_attempts < 40:
            thumbnails = driver.find_elements(By.CSS_SELECTOR, "img.YQ4gaf")
            for idx, thumb in enumerate(thumbnails):
                if clicked >= number_of_results:
                    break
                if thumb in checked_thumbs:
                    continue
                checked_thumbs.add(thumb)
                try:
                    width = int(thumb.get_attribute("width") or 0)
                    height = int(thumb.get_attribute("height") or 0)
                    alt = thumb.get_attribute("alt")
                    if width < min_min_width or height < min_min_height:
                        continue
                    if not thumb.is_displayed() or not thumb.is_enabled():
                        continue
                    driver.execute_script("arguments[0].scrollIntoView(true);", thumb)
                    driver.execute_script("arguments[0].click();", thumb)
                    try:
                        WebDriverWait(driver, 8).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.AQyBn[jsname='To2LVe']"))
                        )
                    except Exception:
                        pass
                    side_img = None
                    selectors = [
                        "img[jsname='kn3ccd']",
                        "img[jsname='JuXqh']",
                    ]
                    for selector in selectors:
                        try:
                            side_img = WebDriverWait(driver, 3).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                            )
                            if side_img:
                                break
                        except Exception:
                            continue
                    if not side_img:
                        continue
                    try:
                        WebDriverWait(driver, 8).until(
                            lambda d: driver.execute_script("return arguments[0].naturalWidth;", side_img) > 1
                        )
                    except Exception:
                        continue
                    src = side_img.get_attribute("src")
                    real_url = None
                    img_width = img_height = 0
                    if src and src.startswith("http"):
                        real_url = src
                        img_width = driver.execute_script("return arguments[0].naturalWidth;", side_img)
                        img_height = driver.execute_script("return arguments[0].naturalHeight;", side_img)
                    if real_url and real_url not in seen_urls and (
                        (exact_size and img_width == min_width and img_height == min_height) or
                        (not exact_size and img_width >= min_width and img_height >= min_height)
                    ):
                        img_result = scrape_url(real_url)
                        images_info.append({
                            "url": real_url,
                            "ascii_art": img_result.get("ascii_art"),
                            "mime_type": img_result.get("mime_type"),
                            "image_bytes": img_result.get("image_bytes"),
                            "text": alt or "[Image]",
                            "path": None,
                        })
                        seen_urls.add(real_url)
                        clicked += 1
                except Exception as e:
                    continue
            if clicked < number_of_results:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                try:
                    WebDriverWait(driver, 2).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "img.YQ4gaf")) > len(checked_thumbs)
                    )
                except Exception:
                    break
                scroll_attempts += 1
    finally:
        driver.quit()

    if content:
        ascii_summary = "\n\n".join([
            f"Image: {img['text']}\nURL: {img['url']}\n\n{img['ascii_art'] or '[No ASCII Art]'}\n"
            for img in images_info
        ])
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
            ],
            "ascii_summary": ascii_summary
        }
    else:
        summary = "\n".join([f"Image: {img['text']}\nURL: {img['url']}\n" for img in images_info])
        return {
            "type": "text",
            "content": summary
        }