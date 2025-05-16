import requests
from bs4 import BeautifulSoup
import os
import uuid
import hashlib
from readability import Document
from PIL import Image, ImageEnhance
from functions.function_calls import image_to_ascii_color

def scrape_url(url="scrape site for its contents or a url of an image to see it"):
    """
    internal. Scrapes a URL for text and image URLs.
    If the URL is a direct image, downloads and returns it as an image.
    """
    try:
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            )
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
            filepath = os.path.join(img_folder, filename)
            # If file already exists, use it
            if not os.path.isfile(filepath):
                try:
                    img_data = requests.get(url, headers=headers, timeout=10).content
                    with open(filepath, "wb") as f:
                        f.write(img_data)
                except Exception as e:
                    return {
                        "type": "error",
                        "error": f"Failed to download image: {e}"
                    }
            else:
                with open(filepath, "rb") as f:
                    img_data = f.read()
            # Generate ASCII art
            try:
                img = Image.open(filepath)
                img = img.convert("RGB")
                img = ImageEnhance.Brightness(img).enhance(1)
                ascii_art = image_to_ascii_color(img)
            except Exception:
                ascii_art = "[Could not generate ASCII art for this image.]"
            # Delete the file after processing
            try:
                os.remove(filepath)
            except Exception:
                pass
            return {
                "type": "image",
                "text": f"[Downloaded image from {url}]",
                "image_bytes": img_data,
                "image_url": url,
                "ascii_art": ascii_art,
                "mime_type": "image/png" if ext.lower() == ".png" else "image/jpeg"
            }

        # Otherwise, treat as a webpage
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        html = response.text

        # Extract all paragraphs for better coverage (especially Wikipedia)
        soup = BeautifulSoup(html, 'html.parser')
        paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all('p')]
        text = "\n\n".join(paragraphs)

        # Fallback: if still too short, use the whole body text
        if len(text) < 500:
            body = soup.body
            if body:
                text = body.get_text(separator="\n", strip=True)
            else:
                text = soup.get_text(separator="\n", strip=True)

        # Get images from the whole page, not just main content
        img_urls = []
        for img in soup.find_all('img'):
            src = img.get('src')
            if src and not src.lower().endswith('.svg'):
                # Make relative URLs absolute
                if not src.startswith("http"):
                    src = requests.compat.urljoin(url, src)
                img_urls.append(src)
        # Limit to 3 images for performance
        img_urls = img_urls[:3]

        return {
            "type": "webpage",
            "text": text,
            "images": img_urls
        }
    except Exception as e:
        import traceback
        return {"type": "error", "error": str(e), "trace": traceback.format_exc()}