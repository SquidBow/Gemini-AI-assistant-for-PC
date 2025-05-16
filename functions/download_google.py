import requests
import os
from urllib.parse import urlparse

def download_google(url="Your URL", filepath="C:/Other Programs/Programing/Projects/Projects VSCode/PersonalAI/downloads"):
    """Downloads content from a URL to a specified filepath.
    If the URL is a YouTube video, attempts to download using pytube if available.
    """
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()

    # Check for YouTube URL
    if "youtube.com" in domain or "youtu.be" in domain:
        try:
            from pytube import YouTube
            yt = YouTube(url)
            stream = yt.streams.get_highest_resolution()
            if os.path.isdir(filepath):
                out_path = filepath
            else:
                out_path = os.path.dirname(filepath)
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

        # Extract filename from URL if not provided
        if os.path.isdir(filepath):
            url_path = urlparse(url).path
            filename = os.path.basename(url_path)
            if not filename:
                filename = "downloaded_file"
            filepath = os.path.join(filepath, filename)

        # Check if file exists and modify filename if necessary
        if os.path.exists(filepath):
            name, ext = os.path.splitext(filepath)
            count = 1
            while os.path.exists(f"{name}_{count}{ext}"):
                count += 1
            filepath = f"{name}_{count}{ext}"

        with open(filepath, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

        return f"Downloaded {url} to {filepath}"

    except requests.exceptions.RequestException as e:
        return f"Error downloading {url}: {e}"
    except Exception as e:
        return f"An error occurred: {e}"


if __name__ == '__main__':
    # Example usage (replace with your desired URL and filepath)
    download_url = "https://www.easygifanimator.net/images/samples/video-to-gif-sample.gif"
    save_path = "C:/Other Programs/Programing/Projects/Projects VSCode/PersonalAI/downloads"
    result = download_google(url=download_url, filepath=save_path)
    print(result)
