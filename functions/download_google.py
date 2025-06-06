import requests
import os
from urllib.parse import urlparse
import webbrowser


def download_google(url="Your URL", path="C:\Other Programs\Programing\Projects\Projects VSCode\Personal Projects\PersonalAI"):
    """Downloads content from a URL to a specified path.
    If the URL is a YouTube video, attempts to download using pytube if available.
    """
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    file_exts = [".pdf", ".zip", ".rar", ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4", ".docx", ".xlsx", ".exe", ".msi", ".7z", ".tar", ".gz"]

    # Check for YouTube URL
    if "youtube.com" in domain or "youtu.be" in domain:
        try:
            from pytube import YouTube
            yt = YouTube(url)
            stream = yt.streams.get_highest_resolution()
            if os.path.isdir(path):
                out_path = path
            else:
                out_path = os.path.dirname(path)
            stream.download(output_path=out_path)
            return f"Downloaded YouTube video: {yt.title} to {out_path}"
        except ImportError:
            return f"YouTube video detected. Install pytube to enable downloading. Video URL: {url}"
        except Exception as e:
            return f"Could not download YouTube video. You can watch or download it manually: {url}\nError: {e}"

    # Regular file download
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        _, ext = os.path.splitext(path)
        # Always treat as directory if no extension
        if not ext:
            url_path = urlparse(url).path
            filename = os.path.basename(url_path)
            if not filename:
                filename = "downloaded_file"
            path = os.path.join(path, filename)

        # Check if file exists and modify filename if necessary
        if os.path.exists(path):
            name, ext = os.path.splitext(path)
            count = 1
            while os.path.exists(f"{name}_{count}{ext}"):
                count += 1
            path = f"{name}_{count}{ext}"

        os.makedirs(os.path.dirname(path), exist_ok=True)  # Always create parent dirs

        with open(path, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

        return f"Downloaded {url} to {path}"

    except requests.exceptions.RequestException as e:
        url_lower = url.lower()
        # If error, and URL ends with file extension and has "download" in it, open in browser
        file_exts = [".pdf", ".zip", ".rar", ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4", ".docx", ".xlsx", ".exe", ".msi", ".7z", ".tar", ".gz"]
        if any(url_lower.endswith(ext) for ext in file_exts) and "download" in url_lower:
            try:
                webbrowser.open(url, new=2)
                return f"Could not download directly, opened URL in browser: {url}"
            except Exception as browser_e:
                return f"Error opening URL in browser: {browser_e}"
        return f"Error downloading {url}: {e}"
    except Exception as e:
        return f"An error occurred: {e}"

