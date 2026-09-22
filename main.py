import requests
import re
from cloakbrowser import launch_async
import asyncio
import time
import firebase_admin
from firebase_admin import credentials, db

import os
import logging
from dotenv import load_dotenv
import sys
import functools

if sys.platform == 'win32':
    from asyncio.proactor_events import _ProactorBasePipeTransport
    from asyncio.base_subprocess import BaseSubprocessTransport
    
    def silence_exception(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception:
                pass
        return wrapper
        
    _ProactorBasePipeTransport.__del__ = silence_exception(_ProactorBasePipeTransport.__del__)
    BaseSubprocessTransport.__del__ = silence_exception(BaseSubprocessTransport.__del__)

load_dotenv()

# Configure logging to save to a file, using thread-safe logging
logging.basicConfig(
    filename='processed_urls.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

cred = credentials.Certificate(os.environ.get("FIREBASE_KEY_PATH", "firebase-key.json"))
firebase_admin.initialize_app(cred, {
    'databaseURL': os.environ.get("FIREBASE_DATABASE_URL")
})

email = os.environ.get("MIXDROP_EMAIL")
key = os.environ.get("MIXDROP_KEY")
folder = os.environ.get("MIXDROP_FOLDER", "/")

streamtape_api_id = os.environ.get("STREAMTAPE_API_ID")
streamtape_api_key = os.environ.get("STREAMTAPE_API_KEY")

# URL = "https://fast-dl.one/dl/6b1a11"

async def extract_download_link(url, proxy_dict=None):
    browser_args = ['--no-sandbox', '--disable-setuid-sandbox']
    if proxy_dict:
        # format proxy for playwright
        pw_proxy = {
            "server": f"http://{proxy_dict['server']}",
            "username": proxy_dict.get("username", ""),
            "password": proxy_dict.get("password", "")
        }
        browser = await launch_async(proxy=pw_proxy, args=browser_args, headless=True)
    else:
        browser = await launch_async(args=browser_args, headless=True)
        
    print("browser launched")
    page = await browser.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    print("page loaded")
    
    try:
        await page.get_by_text("click to verify", exact=False).click(timeout=30000)
        print("clicked to verify")
    except Exception as e:
        print(f"Timeout or error clicking verify: {e}")
        print("falling back to fetcher function....")
        await browser.close()
        await asyncio.sleep(0.25)
        return fetcher(url, proxy_dict)

    if url.__contains__("fast-dl"):
        print("searching vd")
        download_url = await page.locator("#vd").get_attribute("href")
    elif url.__contains__("vgmlinks"):
        print("searching mixdrop")
        button = page.locator("button:has-text('MIXDROP')")
        download_url = await button.locator("xpath=..").get_attribute("href")
    elif url.__contains__("nexdrive.dev"):
        print("skipping this one....")
        download_url = None
    elif url.__contains__("nexdrive.pics")  or url.__contains__("nexdrive.help"):
        print("calling fetcher function....")
        download_url = fetcher(url, proxy_dict)
    await browser.close()
    await asyncio.sleep(0.25)
    return download_url


def fetcher(url, proxy_dict=None):
    try:
        proxies = None
        if proxy_dict:
            proxy_url = f"http://{proxy_dict.get('username')}:{proxy_dict.get('password')}@{proxy_dict['server']}"
            proxies = {
                "http": proxy_url,
                "https": proxy_url
            }
        
        response = requests.get(url, proxies=proxies)
        response.raise_for_status()
        html = response.text
        
        # Extract hrefs that point to fast-dl.one or vgmlinks.live
        pattern = r'href=["\'](https?://(?:fast-dl\.one|vgmlinks\.live)[^"\']+)["\']'
        links = re.findall(pattern, html)
        
        # Remove duplicates while preserving order
        unique_links = []
        seen = set()
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)
                
        return unique_links
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []    

def upload_to_mixdrop(url):
    session = requests.Session()
    params = {
        "email": email,
        "key": key,
        "url": url,
        "folder": folder
    }
    result = session.get("https://api.mixdrop.ag/remoteupload", params=params)
    data = result.json()
    id = data['result']['id']
    fileref = data['result']['fileref']
    return id, fileref

def check_status(id):
    params = {
        "email": email,
        "key": key,
        "id": id
    }
    result = requests.get("https://api.mixdrop.ag/remotestatus", params=params)
    data = result.json()
    # mixdrop can return multiple results in remotestatus, handle it if needed
    status = data.get('result', {}).get('status')
    filename = data.get('result', {}).get('filename') or data.get('result', {}).get('name')
    return status, filename

def rename_file(fileref, filename):
    filename = check_filename_patterns(filename)
    session = requests.Session()
    import urllib.parse
    params = {
        "email": email,
        "key": key,
        "ref": fileref,
        "title": filename
    }
    encoded_params = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    exact_url = f"https://api.mixdrop.ag/filerename?{encoded_params}"
    
    result = session.get(exact_url)
    try:
        data = result.json()
        logging.info(f"Rename response: {data}")
        return data.get('result', {}).get('status', 'Unknown')
    except Exception as e:
        logging.error(f"Failed to parse rename response: {result.text}")
        return None

def file_health_check_on_mix(fileref):
    params = {
        "email": email,
        "key": key,
        "ref[]": fileref
    }
    result = requests.get("https://api.mixdrop.ag/fileinfo2", params=params)
    try:
        data = result.json()
        if data.get("success"):
            file_info = data.get("result", {}).get(fileref, {})
            isvideo = file_info.get("isvideo")
            if isvideo is False:
                return 1
    except Exception as e:
        logging.error(f"Error checking file health on mixdrop for {fileref}: {e}")
    return 0

def upload_to_streamtape(download_url):
    params = {
        "login": streamtape_api_id,
        "key": streamtape_api_key,
        "url": download_url
    }
    result = requests.get("https://api.streamtape.com/remotedl/add", params=params)
    try:
        data = result.json()
        logging.info(f"Streamtape upload response: {data}")
        return data
    except Exception as e:
        logging.error(f"Error uploading to streamtape for {download_url}: {e}")
        return None

def check_filename_patterns(filename):
    if filename:
        filename = filename.replace('"', '').strip()
        if "." in filename:
            parts = filename.rsplit(".", 1)
            return parts[0].replace(".", " ") + "." + parts[1]
    return filename

def get_unprocessed_urls():
    # Fetch all items from the root of Firebase Realtime Database
    ref = db.reference('/')
    data = ref.get()
    movies_data = []
    
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.values()
    else:
        items = []
        
    for movie in items:
        if not movie:
            continue
        if movie.get("status") == "found":
            title = movie.get("title", "Unknown Title")
            unprocessed_url = movie.get("download_urls", [])
            if unprocessed_url:
                movies_data.append((title, unprocessed_url))

    return movies_data

def process_url(title, url, proxies, proxy_dict=None, retries=0):
    if not proxy_dict and proxies:
        proxy_dict = random.choice(proxies)
        
    try:
        proxy_address = proxy_dict['server'] if proxy_dict else 'None'
        msg = f"Processing {url} (Title: {title}) using proxy: {proxy_address}"
        print(msg)
        logging.info(msg)
        
        download_url = asyncio.run(extract_download_link(url, proxy_dict))
        if not download_url:
            msg = f"No download url found for {url}."
            print(msg)
            logging.warning(msg)
            return

        if isinstance(download_url, list):
            # If fetcher returns a list of URLs, recursively process each of them
            if not download_url:
                msg = f"Fetcher returned empty list for {url}."
                print(msg)
                logging.warning(msg)
                return
            
            msg = f"Fetcher returned {len(download_url)} URLs. Processing them recursively."
            print(msg)
            logging.info(msg)
            
            for sub_url in download_url:
                process_url(title, sub_url, proxies, proxy_dict)
            return

        msg = f"Download url found for {url}: {download_url}"
        print(msg)
        logging.info(msg)
        
        id, fileref = upload_to_mixdrop(download_url)
        print("uploading started")
        status, actual_filename = check_status(id)
        while status != "Complete":
            if status is None or str(status).lower() in ["error", "none"]:
                msg = f"Upload failed with status '{status}' for {url}. Skipping."
                print(msg)
                logging.warning(msg)
                return
                
            time.sleep(10)
            status, actual_filename = check_status(id)
            print("uploading status:", status)
            
        print("uploading completed")
        
        # Check health of the uploaded file on Mixdrop
        health_status = file_health_check_on_mix(fileref)
        if health_status == 1:
            msg = f"File isvideo is false for {fileref}. Falling back to Streamtape."
            print(msg)
            logging.info(msg)
            upload_to_streamtape(download_url)
            return
            
        # Use the actual filename from mixdrop response instead of title + .mp4
        filename = check_filename_patterns(actual_filename) if actual_filename else (title + ".mp4")
        print(f"renaming file to: {filename}")
        rename_file(fileref, filename)
        
        msg = f"Successfully uploaded and renamed: {filename} (Original URL: {url})"
        print(msg)
        logging.info(msg)
        
    except Exception as e:
        error_msg = str(e)
        if "ERR_TUNNEL_CONNECTION_FAILED" in error_msg and proxies and retries < 5:
            # msg = f"Tunnel connection failed for {url}. Retrying with new proxy... ({retries+1}/5)"
            # print(msg)
            # logging.warning(msg)
            # new_proxy = random.choice(proxies)
            # return process_url(title, url, proxies, new_proxy, retries + 1)
            pass

        msg = f"Error processing {url}: {e}"
        # print(msg)
        logging.error(msg, exc_info=True)

def load_proxies():
    api_token = os.environ.get("WEBSHARE_API_TOKEN")
    proxies = []
    if not api_token:
        print("WEBSHARE_API_TOKEN not found in .env, skipping proxies.")
        return proxies
        
    try:
        response = requests.get(
            "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100",
            headers={"Authorization": f"Token {api_token}"}
        )
        response.raise_for_status()
        data = response.json()
        
        for item in data.get("results", []):
            proxy = {
                "server": f"{item['proxy_address']}:{item['port']}",
                "username": item.get("username", ""),
                "password": item.get("password", "")
            }
            proxies.append(proxy)
        print(f"Loaded {len(proxies)} proxies from Webshare API.")
    except Exception as e:
        print(f"Failed to load proxies from Webshare API: {e}")
        
    return proxies

import random
from concurrent.futures import ProcessPoolExecutor, as_completed
import signal

def orchestrator():
    max_workers = int(os.environ.get("MAX_WORKERS", "5"))
    proxies = load_proxies()
    
    movies = get_unprocessed_urls()
    tasks = []
    
    # We flatten the tasks to process them concurrently
    for title, urls in movies:
        for url in urls:
            tasks.append((title, url))
            
    executor = ProcessPoolExecutor(max_workers=max_workers)
    futures = []
    
    try:
        for title, url in tasks:
            futures.append(executor.submit(process_url, title, url, proxies, None, 0))
        
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.error(f"Task generated an exception: {e}")
                
    except KeyboardInterrupt:
        print("\nCtrl+C detected. Gracefully stopping all processes...")
        for pid in list(executor._processes.keys()):
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        executor.shutdown(wait=False, cancel_futures=True)
        print("All processes stopped.")
        sys.exit(0)

if __name__ == "__main__":
    orchestrator()