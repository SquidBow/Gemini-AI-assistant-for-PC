import shutil
import requests
from bs4 import BeautifulSoup, Comment
import os
import uuid
import hashlib
from readability import Document
from PIL import Image, ImageEnhance
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time
import re
import undetected_chromedriver as uc
from urllib.parse import urlparse, urljoin
import mimetypes

def scrape_url(url="scrape site for its contents or a url of an image to see it"):
    """
    Scrapes a URL for text and image URLs, excluding navigation, footer, header, ads, and other non-content elements.
    If the URL is a direct image, downloads and returns it as an image.
    """

    try:
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            ),
            'Referer': 'https://www.google.com/',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Connection': 'keep-alive',
        }
        # Check if the URL is a direct image
        if any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]) or "image" in requests.head(url, headers=headers, allow_redirects=True).headers.get("content-type", ""):
            img_folder = "scraped_images"
            os.makedirs(img_folder, exist_ok=True)
            url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
            ext = os.path.splitext(url)[1]
            if not ext or len(ext) > 5:
                ext = ".jpg"
            filename = f"{url_hash}{ext}"
            path = os.path.join(img_folder, filename)
            # If file already exists, use it
            if not os.path.isfile(path):
                try:
                    img_data = requests.get(url, headers=headers, timeout=10).content
                    with open(path, "wb") as f:
                        f.write(img_data)
                except Exception as e:
                    return {
                        "type": "error",
                        "error": f"Failed to download image: {e}"
                    }
            else:
                with open(path, "rb") as f:
                    img_data = f.read()
            # Generate ASCII art
            try:
                img = Image.open(path)
                img = img.convert("RGB")
                img = ImageEnhance.Brightness(img).enhance(1)
                ascii_art = image_to_ascii_color(img)
            except Exception:
                ascii_art = "[Could not generate ASCII art for this image.]"
            # Delete the file after processing
            try:
                os.remove(path)
            except Exception:
                pass
            # After downloading the image
            mime_type = requests.head(url, headers=headers, allow_redirects=True).headers.get("content-type")
            if not mime_type:
                mime_type, _ = mimetypes.guess_type(url)
            if not mime_type:
                mime_type = "application/octet-stream"
            return {
                "type": "image",
                "image_bytes": img_data,  # The raw bytes you downloaded
                "mime_type": mime_type, # Or whatever is correct
                "ascii_art": ascii_art,   # Optional
            }

        # Otherwise, treat as a webpage
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        # Remove unwanted elements
        for tag in soup(['nav', 'footer', 'header', 'aside', 'form', 'script', 'style', 'noscript', 'svg', 'canvas', 'iframe', 'input', 'button', 'figure', 'figcaption', 'link', 'meta', 'object', 'embed', 'ads', 'advertisement', 'ul', 'ol']):
            tag.decompose()
        for tag in soup.find_all(class_=["filters", "refinements", "sidebar", "breadcrumbs"]):
            tag.decompose()

        # Remove comments
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        # Get all visible text
        def visible_text(element):
            if element.parent.name in ['style', 'script', 'head', 'title', 'meta', '[document]']:
                return False
            if isinstance(element, Comment):
                return False
            return True

        # After soup = BeautifulSoup(html, 'html.parser')
        # Try to find the main article container(s)
        article_selectors = [
            {'name': 'section', 'attrs': {'class': 'article-body'}},
            {'name': 'div', 'attrs': {'class': 'article-body'}},
            {'name': 'article'},
            # Add more selectors as needed for the site
        ]

        article_texts = []
        for sel in article_selectors:
            for tag in soup.find_all(sel.get('name'), sel.get('attrs', {})):
                article_texts.append(tag.get_text(separator="\n", strip=True))

        if article_texts:
            text = "\n\n".join(article_texts)
        else:
            text = html_to_text_with_links(soup, url)

        # Remove lines containing "Advertisement" or "Continue Reading"
        lines = [line for line in text.split('\n') if "Advertisement" not in line and "Continue Reading" not in line]
        text = "\n".join(lines)

        # Get up to 3 images from the remaining content
        img_urls = []
        for img in soup.find_all('img'):
            src = img.get('src')
            if src and not src.lower().endswith('.svg'):
                if not src.startswith("http"):
                    src = requests.compat.urljoin(url, src)
                img_urls.append(src)
        img_urls = img_urls[:3]

        # Get the first non-empty paragraph as summary, and prepend [Scraped url]: {url}
        first_paragraph = next((p.strip() for p in text.split('\n') if p.strip()), "")
        title = soup.title.string.strip() if soup.title and soup.title.string else first_paragraph
        summary = f"[Scraped url]: {title}\n{url}"

        return {
            "type": "webpage",
            "summary": summary,
            "text": text,
            "images": img_urls
        }
    except Exception as e:
        import traceback
        error_str = str(e)
        # Try backup if forbidden, blocked, or other HTTP errors
        if (
            "403" in error_str
            or "Forbidden" in error_str
            or "blocked" in error_str.lower()
            or "Access Denied" in error_str
            or "timed out" in error_str.lower()
        ):
            try:
                return scrape_url_undetected(url)
            except Exception as e2:
                return {
                    "type": "error",
                    "error": f"Primary scrape failed with: {error_str}\nBackup scrape failed with: {e2}",
                    "trace": traceback.format_exc()
                }
        else:
            return {
                "type": "error",
                "error": error_str,
                "trace": traceback.format_exc()
            }
    

def image_to_ascii_color(img, width=None):
    """internal"""
    ascii_chars = "█▓▒░ "
    if width is None:
        try:
            width = shutil.get_terminal_size().columns
        except Exception:
            width = 80  # fallback
    img = img.convert("RGB")
    w, h = img.size
    aspect_ratio = h / w
    new_height = int(aspect_ratio * width * 0.55)
    img = img.resize((width, new_height))
    pixels = list(img.getdata())
    ascii_str = ""
    for i, (r, g, b) in enumerate(pixels):
        brightness = int(0.299*r + 0.587*g + 0.114*b)
        char = ascii_chars[brightness * (len(ascii_chars)-1) // 255]
        ascii_str += f"\033[38;2;{r};{g};{b}m{char}\033[0m"
        if (i + 1) % width == 0:
            ascii_str += "\n"
    return ascii_str

def scrape_url_undetected(url):
    """
    Internal. Scrapes a URL for text and image URLs using undetected_chromedriver (for JS-heavy or protected sites).
    """
    options = uc.ChromeOptions()
    options.add_argument("--window-position=2000,0")  # Move window off-screen (if your screen isn't that wide, use a large value)
    options.add_argument("--window-size=300,200")     # Make the window small
    driver = uc.Chrome(options=options)
    driver.minimize_window()
    try:
        driver.get(url)
        time.sleep(3)  # Give JS time to render; adjust as needed
        html = driver.page_source
    finally:
        driver.quit()
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted elements
    for tag in soup(['nav', 'footer', 'header', 'aside', 'form', 'script', 'style', 'noscript', 'svg', 'canvas', 'iframe', 'input', 'button', 'figure', 'figcaption', 'link', 'meta', 'object', 'embed', 'ads', 'advertisement', 'ul', 'ol']):
        tag.decompose()
    for tag in soup.find_all(class_=["filters", "refinements", "sidebar", "breadcrumbs"]):
        tag.decompose()

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    def visible_text(element):
        if element.parent.name in ['style', 'script', 'head', 'title', 'meta', '[document]']:
            return False
        if isinstance(element, Comment):
            return False
        return True

    article_selectors = [
        {'name': 'section', 'attrs': {'class': 'article-body'}},
        {'name': 'div', 'attrs': {'class': 'article-body'}},
        {'name': 'article'},
    ]

    article_texts = []
    for sel in article_selectors:
        for tag in soup.find_all(sel.get('name'), sel.get('attrs', {})):
            article_texts.append(tag.get_text(separator="\n", strip=True))

    if article_texts:
        text = "\n\n".join(article_texts)
    else:
        text = html_to_text_with_links(soup, url)

    lines = [line for line in text.split('\n') if "Advertisement" not in line and "Continue Reading" not in line]
    text = "\n".join(lines)

    img_urls = []
    for img in soup.find_all('img'):
        src = img.get('src')
        if src and not src.lower().endswith('.svg'):
            if not src.startswith("http"):
                src = requests.compat.urljoin(url, src)
            img_urls.append(src)
    img_urls = img_urls[:3]

    first_paragraph = next((p.strip() for p in text.split('\n') if p.strip()), "")
    title = soup.title.string.strip() if soup.title and soup.title.string else first_paragraph
    summary = f"[Scraped url]: {title}\n{url}"

    return {
        "type": "webpage",
        "summary": summary,
        "text": text,
        "images": img_urls
    }

def html_to_text_with_links(soup, page_url=None):
    """
    Internal. Convert soup to text, formatting hyperlinks as hypertext(url:url).
    For product links, always use the full site URL.
    """
    from urllib.parse import urlparse, urljoin

    # Dynamically determine base URL from the page_url
    base_url = ""
    if page_url:
        parsed = urlparse(page_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Check for <base href="..."> tag
    base_tag = soup.find('base', href=True)
    if base_tag:
        base_url = base_tag['href']

    for a in soup.find_all('a', href=True):
        link_text = a.get_text(strip=True)
        href = a['href']
        # Make relative links absolute using urljoin
        href = urljoin(base_url, href)
        if link_text and href.startswith("http"):
            a.replace_with(f"{link_text}(hypertext_url:{href})")
        else:
            a.replace_with(link_text)
    # Now get all visible text as before
    def visible_text(element):
        from bs4 import Comment
        if element.parent.name in ['style', 'script', 'head', 'title', 'meta', '[document]']:
            return False
        if isinstance(element, Comment):
            return False
        return True
    texts = soup.find_all(string=True)
    visible_texts = filter(visible_text, texts)
    return "\n".join(t.strip() for t in visible_texts if t.strip())
