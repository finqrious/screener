# app.py

# ==============================================================================
# INSTRUCTIONS FOR DEPLOYMENT
# ==============================================================================
#
# 1. requirements.txt:
#    Create a file named 'requirements.txt' with the following content:
#
#    requests==2.31.0
#    beautifulsoup4==4.12.2
#    streamlit==1.29.0
#    selenium==4.15.2
#    webdriver-manager==4.0.1
#    lxml==4.9.3
#    urllib3==2.1.0
#
# 2. packages.txt:
#    This file should be empty or completely removed from your repository.
#    The application no longer depends on system-level packages.
#
# ==============================================================================

import streamlit as st
import requests
import os
import time
import random
import re
import io
import tempfile
import shutil
from urllib.parse import urlparse

# Selenium imports (needed for the provided setup function)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# --- Constants and Global Variables ---

# User agents list, can be modified by the retry logic
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0"
]

# Timeouts for requests in seconds (connect, read)
REQUESTS_CONNECT_TIMEOUT = 15
REQUESTS_READ_TIMEOUT = 60

# Minimum file size in bytes to be considered a valid download
MIN_FILE_SIZE = 1024  # 1 KB

# --- Helper Functions ---

def get_extension_from_response(response, url, doc_type):
    """Determines the file extension from response headers or URL."""
    # Check Content-Type header
    if response:
        content_type = response.headers.get('content-type', '').lower()
        if 'pdf' in content_type: return '.pdf'
        if 'zip' in content_type: return '.zip'
        if 'ppt' in content_type or 'presentation' in content_type: return '.pptx'

        # Check Content-Disposition header
        content_disposition = response.headers.get('content-disposition', '')
        if content_disposition:
            filenames = re.findall('filename="?(.+)"?', content_disposition)
            if filenames:
                _, ext = os.path.splitext(filenames[0])
                if ext: return ext.lower()

    # Check URL path if response-based methods fail
    parsed_url = urlparse(url)
    _, ext = os.path.splitext(parsed_url.path)
    if ext and len(ext) < 6:
        return ext.lower()

    # Fallback based on doc_type
    if doc_type == "Annual_Report": return '.pdf'
    if doc_type == "Transcript": return '.pdf'
    if doc_type == "PPT": return '.pptx'
        
    return '.pdf' # Default fallback

# --- Selenium Functions (Provided for completeness, but not used in the final download chain) ---

def setup_chrome_driver():
    """Enhanced Chrome driver setup for Streamlit Cloud"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-images")
    chrome_options.add_argument("--disable-javascript")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    # Download directory setup
    selenium_temp_dir = tempfile.mkdtemp()
    prefs = {
        "download.default_directory": selenium_temp_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "profile.default_content_settings.popups": 0,
        "profile.default_content_setting_values.notifications": 2
    }
    chrome_options.add_experimental_option("prefs", prefs)

    try:
        # Try multiple approaches for driver initialization
        driver = None
        
        # Approach 1: Use webdriver-manager with explicit cache
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from webdriver_manager.core.utils import ChromeType
            
            # Force fresh download and specify cache
            service = Service(ChromeDriverManager(
                chrome_type=ChromeType.CHROMIUM,
                cache_valid_range=1
            ).install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            return driver, selenium_temp_dir
        except Exception as e1:
            st.warning(f"Webdriver-manager approach failed: {str(e1)}")
        
        # Approach 2: Try with system chromium (common on Streamlit Cloud)
        try:
            # Check for standard chromium paths
            if os.path.exists("/usr/bin/chromium-browser"):
                chrome_options.binary_location = "/usr/bin/chromium-browser"
                service = Service("/usr/bin/chromedriver")
                driver = webdriver.Chrome(service=service, options=chrome_options)
                return driver, selenium_temp_dir
        except Exception as e2:
            st.warning(f"System chromium approach failed: {str(e2)}")
        
        # Approach 3: Default service (local execution)
        try:
            service = Service()
            driver = webdriver.Chrome(service=service, options=chrome_options)
            return driver, selenium_temp_dir
        except Exception as e3:
            st.warning(f"Default service approach failed: {str(e3)}")
            
        return None, selenium_temp_dir
        
    except Exception as e:
        st.error(f"All Chrome driver initialization approaches failed: {str(e)}")
        return None, selenium_temp_dir

# --- Core Download Logic ---

def download_with_enhanced_requests(url, folder_path, base_name_no_ext, doc_type):
    """Enhanced requests-only download with better session management"""
    path_written = None
    content_buffer = io.BytesIO()
    session = requests.Session()

    base_headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8,application/pdf",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0"
    }

    try:
        current_headers = base_headers.copy()
        response = None
        
        # Enhanced BSE handling
        if "bseindia.com" in url:
            session.get("https://www.bseindia.com/", headers=current_headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            time.sleep(random.uniform(1, 2))
            
            if "AnnPdfOpen.aspx" in url:
                pname_match = re.search(r'Pname=([^&]+)', url)
                scrip_match = re.search(r'scrip=([^&]+)', url)
                
                if pname_match:
                    pname_value = pname_match.group(1)
                    scrip_value = scrip_match.group(1) if scrip_match else pname_value[:6]
                    
                    bse_urls = [
                        f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pname_value}",
                        f"https://www.bseindia.com/corporates/annpdf/{pname_value}",
                        url
                    ]
                    
                    for bse_url in bse_urls:
                        try:
                            bse_headers = current_headers.copy()
                            bse_headers["Referer"] = f"https://www.bseindia.com/corporates/ann.html?scrip={scrip_value}"
                            response = session.get(bse_url, headers=bse_headers, stream=True, 
                                                 timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
                            response.raise_for_status()
                            
                            initial_chunk = next(response.iter_content(1024))
                            if not (initial_chunk.strip().lower().startswith(b'<!doctype') or 
                                   initial_chunk.strip().lower().startswith(b'<html')):
                                # We seem to have a valid file, reconstruct the stream
                                full_content_stream = io.BytesIO(initial_chunk + response.content)
                                response._content = full_content_stream.read()
                                response.iter_content = lambda chunk_size: iter([response.content])
                                break # Success
                        except Exception:
                            continue
                    else: # If all BSE URLs fail, let the exception propagate below
                        raise requests.exceptions.RequestException("All BSE URL patterns failed.")
                else: # Fallback to original URL if params not found
                    response = session.get(url, headers=current_headers, stream=True,
                                        timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
            else: # Other BSE URLs
                response = session.get(url, headers=current_headers, stream=True,
                                     timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        
        # Enhanced NSE handling  
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            nse_headers = current_headers.copy()
            session.get("https://www.nseindia.com/", headers=nse_headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            time.sleep(random.uniform(1, 2))
            nse_headers["Referer"] = "https://www.nseindia.com/"
            session.get("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports", 
                       headers=nse_headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            time.sleep(random.uniform(1, 2))
            nse_headers["Referer"] = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
            response = session.get(url, headers=nse_headers, stream=True,
                                 timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        
        # Default handling for other URLs
        else:
            response = session.get(url, headers=current_headers, stream=True,
                                 timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        
        # Process response
        if response:
            response.raise_for_status()
            file_ext = get_extension_from_response(response, url, doc_type)
            file_name_with_ext = base_name_no_ext + file_ext
            path_written = os.path.join(folder_path, file_name_with_ext)
            
            with open(path_written, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        content_buffer.write(chunk)
            
            content = content_buffer.getvalue()
            
            if content.strip().lower().startswith(b'<!doctype html') or content.strip().lower().startswith(b'<html'):
                if os.path.exists(path_written): os.remove(path_written)
                return None, None, "DOWNLOAD_FAILED_HTML_CONTENT", None
            
            if len(content) < MIN_FILE_SIZE:
                if os.path.exists(path_written): os.remove(path_written)
                return None, None, "DOWNLOAD_FAILED_TOO_SMALL", None
            
            return path_written, content, None, None
            
    except requests.exceptions.RequestException as e:
        if path_written and os.path.exists(path_written):
            try:
                os.remove(path_written)
            except OSError:
                pass
        return None, None, "DOWNLOAD_FAILED_EXCEPTION", str(e)
    finally:
        content_buffer.close()
        session.close()

def download_with_retry_logic(url, folder_path, base_name_no_ext, doc_type, max_retries=3):
    """Download with retry logic and different user agents"""
    extended_user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0"
    ]
    last_error, last_detail = None, None

    for attempt in range(max_retries):
        global USER_AGENTS
        USER_AGENTS = [extended_user_agents[attempt % len(extended_user_agents)]]
        
        if attempt > 0:
            delay = random.uniform(2, 5) * attempt
            st.info(f"Retry attempt {attempt + 1}/{max_retries} for {doc_type} after {delay:.1f}s delay...")
            time.sleep(delay)
        
        try:
            path, content, error, detail = download_with_enhanced_requests(url, folder_path, base_name_no_ext, doc_type)
            if path and content:
                return path, content, None, None
            last_error, last_detail = error, detail
        except Exception as e:
            last_error, last_detail = "RETRY_EXCEPTION", str(e)
            continue
    
    return None, None, last_error, last_detail

# --- Main Download Function to be Called by UI ---

def download_file_attempt(url, folder_path, base_name_no_ext, doc_type):
    """
    This is the primary download orchestrator. As per the latest instructions,
    it uses the robust retry logic with the enhanced requests function.
    Selenium is not used in this path to ensure stability on cloud platforms.
    """
    return download_with_retry_logic(url, folder_path, base_name_no_ext, doc_type)


# --- Streamlit User Interface ---

def main():
    st.set_page_config(page_title="Enhanced File Downloader", layout="wide")
    st.title("📄 Enhanced File Downloader")
    st.markdown("""
    This app uses a robust method to download files, especially Annual Reports from sources like BSE and NSE. 
    It primarily uses an advanced `requests`-based approach with session management and multiple retries.
    """)

    # Create a temporary directory for all downloads in this session
    if 'download_folder' not in st.session_state:
        st.session_state.download_folder = tempfile.mkdtemp(prefix="st_downloads_")

    doc_type = st.selectbox(
        "Select Document Type:",
        ("Annual_Report", "Transcript", "PPT", "Other"),
        help="This helps in determining the file type if it's not clear from the URL."
    )

    url = st.text_input("Enter the URL of the file to download:", placeholder="https://www.bseindia.com/...")

    if st.button("Download File", type="primary"):
        if not url or not url.startswith(('http://', 'https://')):
            st.warning("Please enter a valid URL.")
            st.stop()

        with st.spinner(f"Attempting to download {doc_type}... This may take a moment."):
            try:
                # Generate a safe base filename from the URL
                safe_path = re.sub(r'[^a-zA-Z0-9_-]', '_', urlparse(url).path)
                base_name_no_ext = f"{doc_type}_{safe_path.split('/')[-1] or 'download'}"
                base_name_no_ext = base_name_no_ext[:70] # Truncate for sanity

                # THE MAIN CALL to the download orchestrator
                file_path, file_content, error, detail = download_file_attempt(
                    url, 
                    st.session_state.download_folder, 
                    base_name_no_ext, 
                    doc_type
                )
                
                if file_path and file_content:
                    st.success(f"✅ Download successful!")
                    st.balloons()
                    
                    st.download_button(
                        label=f"Click to save: {os.path.basename(file_path)}",
                        data=file_content,
                        file_name=os.path.basename(file_path),
                        mime="application/octet-stream"
                    )
                else:
                    st.error(f"❌ Download failed after all retries.")
                    st.json({
                        "final_error_code": error,
                        "details": detail,
                        "url_attempted": url
                    })

            except Exception as e:
                st.error(f"An unexpected error occurred: {str(e)}")

if __name__ == "__main__":
    main()
