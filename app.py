import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import zipfile
import time
import urllib.parse
import streamlit as st
import base64
import io
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
# webdriver-manager is now imported conditionally within setup_chrome_driver
import tempfile
import shutil
# import traceback

# --- Global Constants ---
MIN_FILE_SIZE = 1024
REQUESTS_CONNECT_TIMEOUT = 15  # Increased slightly
REQUESTS_READ_TIMEOUT = 300    # Keep as is for large files
SELENIUM_PAGE_LOAD_TIMEOUT = 60 # Reduced, as JS might be disabled
SELENIUM_DOWNLOAD_WAIT_TIMEOUT = 180 # Reduced

# USER_AGENTS will be managed by download_with_retry_logic
# Initial placeholder, but the retry logic will override this if used.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]
EXTENDED_USER_AGENTS = [ # Used by retry logic
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0"
]


# --- Helper Functions ---
def get_extension_from_response(response, url, doc_type_for_default):
    content_disposition = response.headers.get('Content-Disposition')
    if content_disposition:
        filenames = re.findall(r'filename\*?=(?:UTF-\d{1,2}\'\'|")?([^";\s]+)', content_disposition, re.IGNORECASE)
        if filenames:
            parsed_filename = urllib.parse.unquote(filenames[-1].strip('"'))
            _, ext = os.path.splitext(parsed_filename)
            if ext and 1 < len(ext) < 7: return ext.lower()
    content_type = response.headers.get('Content-Type')
    if content_type:
        ct = content_type.split(';')[0].strip().lower()
        mime_to_ext = {'application/pdf': '.pdf', 'application/vnd.ms-powerpoint': '.ppt', 'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx', 'application/msword': '.doc', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx', 'application/zip': '.zip', 'application/x-zip-compressed': '.zip', 'text/csv': '.csv'}
        if ct in mime_to_ext: return mime_to_ext[ct]
    try:
        parsed_url_path = urllib.parse.urlparse(url).path
        _, ext_from_url = os.path.splitext(parsed_url_path)
        if ext_from_url and 1 < len(ext_from_url) < 7: return ext_from_url.lower()
    except Exception: pass
    if doc_type_for_default == 'PPT': return '.pptx'
    return '.pdf' # Defaulting transcript to PDF too, as often they are

def format_filename_base(date_str, doc_type):
    if re.match(r'^\d{4}$', date_str): return f"{date_str}_{doc_type}"
    if re.match(r'^\d{4}-\d{2}$', date_str): year, month = date_str.split('-'); return f"{year}_{month}_{doc_type}"
    if re.match(r'^\d{2}/\d{2}/\d{4}$', date_str): day, month, year = date_str.split('/'); return f"{year}_{month}_{day}_{doc_type}"
    clean_date = re.sub(r'[^\w\.-]', '_', date_str)
    return f"{clean_date}_{doc_type}"

# --- Core Web Interaction and Parsing ---
def get_webpage_content(stock_name):
    url = f"https://www.screener.in/company/{stock_name}/consolidated/#documents"
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)} # Use the (potentially overridden) global
        response = requests.get(url, headers=headers, timeout=REQUESTS_CONNECT_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404: st.error(f"Stock '{stock_name}' not found on Screener. Check ticker.")
        else: st.error(f"HTTP error fetching Screener page for '{stock_name}'. Status: {e.response.status_code}.")
        return None
    except requests.exceptions.ConnectionError: st.error(f"Connection error for '{stock_name}'. Check internet."); return None
    except requests.exceptions.Timeout: st.error(f"Timeout fetching page for '{stock_name}'. Server slow."); return None
    except requests.exceptions.RequestException as e: st.error(f"Error fetching data for '{stock_name}': {str(e)}. Try again."); return None

def parse_html_content(html_content):
    if not html_content: return []
    soup = BeautifulSoup(html_content, 'lxml') # Using lxml as suggested for robustness
    all_links = []
    annual_reports = soup.select('.annual-reports ul.list-links li a')
    for link in annual_reports:
        year_match = re.search(r'Financial Year (\d{4})', link.text.strip())
        if year_match: all_links.append({'date': year_match.group(1), 'type': 'Annual_Report', 'url': link['href']})
    concall_items = soup.select('.concalls ul.list-links li')
    for item in concall_items:
        date_div = item.select_one('.ink-600.font-size-15')
        if date_div:
            date_text = date_div.text.strip()
            try: date_obj = datetime.strptime(date_text, '%b %Y'); date_str = date_obj.strftime('%Y-%m')
            except ValueError: date_str = date_text
            for link_tag in item.find_all('a', class_='concall-link'):
                if 'Transcript' in link_tag.text: all_links.append({'date': date_str, 'type': 'Transcript', 'url': link_tag['href']})
                elif 'PPT' in link_tag.text: all_links.append({'date': date_str, 'type': 'PPT', 'url': link_tag['href']})
    return sorted(all_links, key=lambda x: x['date'], reverse=True)


# --- New Selenium Setup Function ---
def setup_chrome_driver():
    """Enhanced Chrome driver setup for Streamlit Cloud"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-extensions")
    # chrome_options.add_argument("--disable-plugins") # Can cause issues with PDF viewing
    # chrome_options.add_argument("--disable-images") # Might break some sites' JS logic if download is JS-triggered
    # chrome_options.add_argument("--disable-javascript") # Will break most modern sites & download triggers
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}") # Use current global USER_AGENTS
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    selenium_temp_dir = tempfile.mkdtemp()
    prefs = {
        "download.default_directory": selenium_temp_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True, # Important for direct PDF downloads
        "profile.default_content_settings.popups": 0,
        "profile.default_content_setting_values.notifications": 2
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    driver = None
    service = None
    # Approach 1: Use webdriver-manager with explicit cache (good for local, might work on some cloud)
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from webdriver_manager.core.utils import ChromeType # If using specific chrome type
        
        # Using default ChromeType.GOOGLE which is usually fine.
        # cache_valid_range=0 forces re-download, 1 uses cache if valid for 1 day.
        service = Service(ChromeDriverManager(cache_valid_range=1).install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        # st.info("Selenium driver initialized using webdriver-manager.") # UI log for debugging
        return driver, selenium_temp_dir, None, None # driver, temp_dir, error_marker, error_detail
    except Exception as e1:
        # st.warning(f"Webdriver-manager approach failed: {str(e1)}") # UI log for debugging
        pass # Fall through to next approach
    
    # Approach 2: Try with system chromium (common on Linux cloud if packages.txt worked)
    # This assumes /usr/bin/chromium-browser and /usr/bin/chromedriver exist.
    # On Streamlit Cloud, if packages.txt has chromium-chromedriver, chromedriver should be in PATH.
    # So Service() without args should find it.
    if os.path.exists("/home/appuser"): # Heuristic for Streamlit Community Cloud
        try:
            # Forcing binary location can be tricky and platform-dependent
            # chrome_options.binary_location = "/usr/bin/chromium-browser" # or google-chrome
            service = Service() # Tries PATH
            driver = webdriver.Chrome(service=service, options=chrome_options)
            # st.info("Selenium driver initialized using system driver (guessed path).") # UI log
            return driver, selenium_temp_dir, None, None
        except Exception as e2:
            # st.warning(f"System chromium approach (guessed path) failed: {str(e2)}") # UI log
            pass

    # Approach 3: Default service (another attempt at finding driver in PATH or default behavior)
    # This is often redundant if webdriver-manager is the first attempt locally.
    # On cloud, if packages.txt worked, this might find chromedriver.
    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=chrome_options)
        # st.info("Selenium driver initialized using default Service().") # UI log
        return driver, selenium_temp_dir, None, None
    except Exception as e3:
        # st.warning(f"Default service approach failed: {str(e3)}") # UI log
        pass
            
    # If all approaches failed
    return None, selenium_temp_dir, "SELENIUM_DRIVER_ALL_APPROACHES_FAILED", "All attempts to initialize Chrome driver failed."


# --- Download Functions ---
def download_with_enhanced_requests(url, folder_path, base_name_no_ext, doc_type):
    path_written = None; content_buffer = io.BytesIO(); session = requests.Session()
    base_headers = {
        "User-Agent": random.choice(USER_AGENTS), # Uses current global USER_AGENTS
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8,application/pdf",
        "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none", "Cache-Control": "max-age=0"
    }
    try:
        current_headers = base_headers.copy(); response = None
        if "bseindia.com" in url:
            session.get("https://www.bseindia.com/", headers=current_headers, timeout=REQUESTS_CONNECT_TIMEOUT); time.sleep(random.uniform(0.5, 1.5))
            current_headers["Referer"] = "https://www.bseindia.com/"
            if "AnnPdfOpen.aspx" in url:
                pname_match = re.search(r'Pname=([^&]+)', url); scrip_match = re.search(r'scrip=([^&]+)', url)
                if pname_match:
                    pname_value = pname_match.group(1); scrip_value = scrip_match.group(1) if scrip_match else pname_value[:6]
                    bse_urls_to_try = [f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pname_value}", f"https://www.bseindia.com/corporates/annpdf/{pname_value}", url]
                    for bse_url_attempt in bse_urls_to_try:
                        try:
                            bse_headers = current_headers.copy(); bse_headers["Referer"] = f"https://www.bseindia.com/corporates/ann.html?scrip={scrip_value}"
                            response = session.get(bse_url_attempt, headers=bse_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
                            response.raise_for_status()
                            # Basic check if it's not HTML immediately
                            if not response.headers.get('Content-Type', '').lower().startswith('text/html'): break 
                        except requests.exceptions.RequestException:
                            if bse_url_attempt == bse_urls_to_try[-1]: raise # Reraise if last attempt fails
                            continue # Try next BSE URL
                    if not response: # Should not happen if reraise works
                        return None, None, "DOWNLOAD_FAILED_BSE_NO_RESPONSE", None
            else: response = session.get(url, headers=current_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            nse_headers = current_headers.copy()
            session.get("https://www.nseindia.com/", headers=nse_headers, timeout=REQUESTS_CONNECT_TIMEOUT); time.sleep(random.uniform(0.5, 1.5))
            nse_headers["Referer"] = "https://www.nseindia.com/"
            session.get("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports", headers=nse_headers, timeout=REQUESTS_CONNECT_TIMEOUT); time.sleep(random.uniform(0.5, 1.5))
            nse_headers["Referer"] = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
            response = session.get(url, headers=nse_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        else: response = session.get(url, headers=current_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        
        if response:
            response.raise_for_status()
            file_ext = get_extension_from_response(response, url, doc_type)
            file_name_with_ext = base_name_no_ext + file_ext
            path_written = os.path.join(folder_path, file_name_with_ext)
            with open(path_written, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk: f.write(chunk); content_buffer.write(chunk)
            content = content_buffer.getvalue()
            if content.strip().startswith(b'<!DOCTYPE html') or content.strip().startswith(b'<html'):
                if os.path.exists(path_written): os.remove(path_written)
                return None, None, "DOWNLOAD_FAILED_HTML_CONTENT", None
            if len(content) < MIN_FILE_SIZE:
                if os.path.exists(path_written): os.remove(path_written)
                return None, None, "DOWNLOAD_FAILED_TOO_SMALL", None
            return path_written, content, None, None
        else: # No response object was successfully assigned (e.g. all BSE attempts failed internally)
            return None, None, "DOWNLOAD_FAILED_NO_RESPONSE_OBJECT", None
            
    except requests.exceptions.RequestException as e:
        if path_written and os.path.exists(path_written): try: os.remove(path_written)
        except OSError: pass
        return None, None, "DOWNLOAD_FAILED_EXCEPTION", str(e)
    finally:
        if hasattr(content_buffer, 'closed') and not content_buffer.closed: content_buffer.close()
        session.close()

def download_with_selenium(url, folder_path, base_name_no_ext, doc_type):
    driver, selenium_temp_dir, driver_error_marker, driver_error_detail = setup_chrome_driver()
    if not driver:
        if selenium_temp_dir and os.path.exists(selenium_temp_dir): # Clean up temp dir from setup
            try: shutil.rmtree(selenium_temp_dir)
            except Exception: pass
        return None, None, driver_error_marker or "SELENIUM_DRIVER_INIT_ERROR", driver_error_detail or "Failed to get driver from setup."

    path_written = None # Path for file successfully written by Selenium methods
    try:
        # Pre-visit logic
        if "bseindia.com" in url: driver.get("https://www.bseindia.com/"); time.sleep(random.uniform(1,2))
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            driver.get("https://www.nseindia.com/"); time.sleep(random.uniform(1,2))
            driver.get("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"); time.sleep(random.uniform(1,2))
        driver.get(url) # Navigate to the actual download URL

        dl_temp_file_path = None; start_time = time.time()
        while time.time() - start_time < SELENIUM_DOWNLOAD_WAIT_TIMEOUT:
            completed_files = [f for f in os.listdir(selenium_temp_dir) if not f.endswith(('.crdownload', '.tmp', '.part'))]
            if completed_files: dl_temp_file_path = os.path.join(selenium_temp_dir, sorted(completed_files, key=lambda f: os.path.getmtime(os.path.join(selenium_temp_dir, f)), reverse=True)[0]); break
            time.sleep(1)
        
        if dl_temp_file_path and os.path.exists(dl_temp_file_path):
            _, original_ext = os.path.splitext(os.path.basename(dl_temp_file_path))
            # Use a mock response object for get_extension_from_response if needed
            mock_response_for_ext = type('Response', (), {'headers': {}, 'text': driver.page_source})()
            if not original_ext or not (1 < len(original_ext) < 7): original_ext = get_extension_from_response(mock_response_for_ext, url, doc_type)
            file_name_with_ext = base_name_no_ext + original_ext.lower()
            path_written = os.path.join(folder_path, file_name_with_ext)
            with open(dl_temp_file_path, 'rb') as f_src, open(path_written, 'wb') as f_dst: content_bytes = f_src.read(); f_dst.write(content_bytes)
            try: os.remove(dl_temp_file_path)
            except OSError: pass
            if len(content_bytes) >= MIN_FILE_SIZE: return path_written, content_bytes, None, None
            else:
                if os.path.exists(path_written): os.remove(path_written)
                path_written = None # Mark as invalid

        # Fallbacks (base64, requests with cookies) are less likely to work if direct download with JS disabled fails,
        # but can be kept as a last resort. For this iteration, focusing on the direct download via Selenium.
        # If the above fails, this Selenium attempt is considered failed.
        return None, None, "SELENIUM_DIRECT_DOWNLOAD_FAILED", "Browser initiated download did not complete or was invalid."

    except Exception as e_sel_general:
        return None, None, "DOWNLOAD_FAILED_EXCEPTION_SEL_GENERAL", str(e_sel_general)
    finally:
        if driver: driver.quit()
        if selenium_temp_dir and os.path.exists(selenium_temp_dir):
            try: shutil.rmtree(selenium_temp_dir)
            except Exception: pass

def download_with_retry_logic(url, folder_path, base_name_no_ext, doc_type, max_retries=2): # Reduced max_retries
    global USER_AGENTS # To allow modification as per snippet
    last_error = None; last_detail = None
    for attempt in range(max_retries):
        current_ua = EXTENDED_USER_AGENTS[attempt % len(EXTENDED_USER_AGENTS)]
        USER_AGENTS = [current_ua] # Override global for this attempt

        if attempt > 0:
            delay = random.uniform(1, 3) * attempt # Shorter delay
            # st.info(f"Retrying ({attempt+1}/{max_retries}) for {doc_type} from {url} with UA: {current_ua} after {delay:.1f}s delay...") # Debug
            time.sleep(delay)
        
        # Primary Strategy: Enhanced Requests
        path, content, error, detail = download_with_enhanced_requests(url, folder_path, base_name_no_ext, doc_type)
        if path and content: return path, content, None, None
        
        last_error, last_detail = error, detail # Store error from requests

        # Conditional Selenium Fallback (only if not Annual Report on cloud, and for specific types)
        # This part is now integrated into download_file_strategy
    return None, None, last_error, last_detail


def download_file_strategy(url, folder_path, base_name_no_ext, doc_type):
    # Use retry logic for enhanced requests first
    path_req, content_req, error_req, detail_req = download_with_retry_logic(url, folder_path, base_name_no_ext, doc_type)
    if path_req and content_req:
        return path_req, content_req, None, None
    
    # Conditional Selenium Fallback
    # Avoid Selenium for Annual Reports on cloud environments
    is_cloud_env = os.path.exists("/home/appuser") # Simple heuristic for Streamlit Cloud
    if doc_type == "Annual_Report" and is_cloud_env:
        # st.info(f"Skipping Selenium for Annual Report '{base_name_no_ext}' on cloud.") # UI info
        return None, None, error_req or "REQUESTS_ALL_RETRIES_FAILED", detail_req or "All requests attempts failed."

    # Try Selenium for Transcripts and PPTs if requests failed, or for ARs if not on cloud
    if doc_type in ["Transcript", "PPT"] or (doc_type == "Annual_Report" and not is_cloud_env) :
        # st.info(f"Requests failed for {doc_type} '{base_name_no_ext}'. Trying Selenium fallback...") # UI info
        path_sel, content_sel, error_sel, detail_sel = download_with_selenium(url, folder_path, base_name_no_ext, doc_type)
        if path_sel and content_sel:
            return path_sel, content_sel, None, None
        
        # If Selenium also fails, return its specific error, prioritizing driver init error
        if error_sel == "SELENIUM_DRIVER_INIT_ERROR":
            return None, None, error_sel, detail_sel
        # Fallback to Selenium's general error or the last requests error
        final_sel_error = error_sel if error_sel else "SELENIUM_FALLBACK_FAILED"
        final_sel_detail = detail_sel if error_sel else "Selenium fallback attempt also failed."
        return None, None, final_sel_error, final_sel_detail

    # If it's an Annual Report on cloud and requests failed, or other unhandled cases
    return None, None, error_req or "DOWNLOAD_FAILED_ALL_STRATEGIES", detail_req or "All download strategies failed."


# --- Main Application Logic (Unchanged from your last version, uses the new download_file_strategy) ---
def download_selected_documents(links, output_folder, doc_types, progress_bar, status_text_area):
    file_contents_for_zip = {}; failed_downloads_details = []
    filtered_links = [link for link in links if link['type'] in doc_types]
    total_files_to_attempt = len(filtered_links)
    if total_files_to_attempt == 0: return {}, []
    progress_step = 1.0 / total_files_to_attempt
    downloaded_successfully_count = 0; failed_count = 0
    selenium_driver_init_error_shown_this_run = False
    for i, link_info in enumerate(filtered_links):
        base_name_for_file = format_filename_base(link_info['date'], link_info['type'])
        try:
            temp_file_path, content_bytes, error_marker, error_detail_str = download_file_strategy(link_info['url'], output_folder, base_name_for_file, link_info['type'])
            if temp_file_path and content_bytes:
                filename_for_zip = os.path.basename(temp_file_path); counter = 1
                name_part, ext_part = os.path.splitext(filename_for_zip)
                while filename_for_zip in file_contents_for_zip: filename_for_zip = f"{name_part}_{counter}{ext_part}"; counter += 1
                file_contents_for_zip[filename_for_zip] = content_bytes; downloaded_successfully_count += 1
            else:
                failed_count += 1
                failed_downloads_details.append({'url': link_info['url'], 'type': link_info['type'], 'base_name': base_name_for_file, 'reason': error_marker, 'reason_detail': error_detail_str})
                if error_marker == "SELENIUM_DRIVER_INIT_ERROR" and not selenium_driver_init_error_shown_this_run:
                    st.error(f"Critical Setup Error: Could not initialize browser driver. Details: {error_detail_str}. Downloads requiring browser automation may fail.")
                    selenium_driver_init_error_shown_this_run = True
            current_progress_val = min((i + 1) * progress_step, 1.0)
            progress_bar.progress(current_progress_val)
            status_text_area.text(f"Processing: {i+1}/{total_files_to_attempt} | Downloaded: {downloaded_successfully_count} | Failed: {failed_count}")
            time.sleep(random.uniform(0.05, 0.15)) # Shorter delay now that retries have their own delays
        except Exception as e_loop:
            failed_count += 1
            failed_downloads_details.append({'url': link_info.get('url', 'N/A'), 'type': link_info.get('type', 'Unknown'), 'base_name': base_name_for_file, 'reason': "LOOP_PROCESSING_ERROR", 'reason_detail': str(e_loop)})
            current_progress_val = min((i + 1) * progress_step, 1.0)
            progress_bar.progress(current_progress_val)
            status_text_area.text(f"Processing: {i+1}/{total_files_to_attempt} | Downloaded: {downloaded_successfully_count} | Failed: {failed_count}")
    return file_contents_for_zip, failed_downloads_details

def create_zip_in_memory(file_contents_dict):
    if not file_contents_dict: return None
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_name, content in file_contents_dict.items(): zf.writestr(file_name, content)
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def main():
    try:
        st.set_page_config(page_title="StockLib", page_icon="📚", layout="centered")
        if 'show_about' not in st.session_state: st.session_state.show_about = False
        _, col_about_btn = st.columns([0.85, 0.15]) 
        with col_about_btn:
            if st.button("About", type="secondary", use_container_width=True, help="Information about StockLib"):
                st.session_state.show_about = not st.session_state.show_about
        st.markdown("<div style='text-align: center;'><h1 style='margin-bottom: 0.2em;'>StockLib 📚</h1><h4 style='color: #808080; margin-top: 0em; font-weight: normal;'>Your First Step in Fundamental Analysis – Your Business Data Library!</h4></div>", unsafe_allow_html=True)
        if st.session_state.show_about:
            with st.expander("About StockLib & Quick Guide", expanded=True):
                st.markdown("**StockLib + NotebookLLM = Your AI-Powered Business Analyst**\n1. Enter stock ticker.\n2. Select document types.\n3. Click \"Fetch Documents\".\n4. Download ZIP.\n*Sourced via screener.in (BSE/NSE/company sites).*")
            st.markdown("---")
        with st.form(key='stock_form'):
            st.markdown("---")
            stock_name = st.text_input("Enter the stock name (BSE/NSE ticker):", placeholder="Example: TATAMOTORS")
            st.markdown("### Select Document Types")
            annual_reports_cb = st.checkbox("Annual Reports 📄", value=True, key="ar_cb")
            transcripts_cb = st.checkbox("Concall Transcripts 📝", value=True, key="tr_cb")
            ppts_cb = st.checkbox("Presentations 📊", value=True, key="ppt_cb")
            submit_button = st.form_submit_button(label="🔍 Fetch Documents", use_container_width=True, type="primary")
        if submit_button and stock_name:
            doc_types_selected = []
            if annual_reports_cb: doc_types_selected.append("Annual_Report")
            if transcripts_cb: doc_types_selected.append("Transcript")
            if ppts_cb: doc_types_selected.append("PPT")
            if not doc_types_selected: st.warning("Please select at least one document type."); st.stop()
            sanitized_stock_name = stock_name.strip().upper()
            with st.spinner(f"🔍 Searching documents for '{sanitized_stock_name}'..."):
                html_content = get_webpage_content(sanitized_stock_name)
                if not html_content: st.stop()
                parsed_links = parse_html_content(html_content)
                if not parsed_links: st.warning(f"No document links found on Screener for '{sanitized_stock_name}'."); st.stop()
                links_to_download = [link for link in parsed_links if link['type'] in doc_types_selected]
                if not links_to_download: st.warning(f"No documents of selected types ({', '.join(doc_types_selected)}) found for '{sanitized_stock_name}'."); st.stop()
            st.success(f"Found {len(links_to_download)} documents for '{sanitized_stock_name}'. Preparing download...")
            with tempfile.TemporaryDirectory() as session_temp_dir:
                progress_bar_area = st.empty(); status_text_area = st.empty()
                progress_bar = progress_bar_area.progress(0)
                status_text_area.text("Initializing downloads...")
                file_contents_dict, failed_docs = download_selected_documents(links_to_download, session_temp_dir, doc_types_selected, progress_bar, status_text_area)
                actual_downloaded_count = len(file_contents_dict); total_attempted_count = len(links_to_download)
                progress_bar_area.empty()
                if actual_downloaded_count > 0:
                    final_status_msg = f"Download process complete. Successfully prepared {actual_downloaded_count}/{total_attempted_count} documents for ZIP."
                    if failed_docs: final_status_msg += f" ({len(failed_docs)} failed)."
                    status_text_area.success(final_status_msg)
                    zip_data = create_zip_in_memory(file_contents_dict)
                    if zip_data: st.download_button(label=f"📥 Download {actual_downloaded_count} Documents as ZIP ({sanitized_stock_name})", data=zip_data, file_name=f"{sanitized_stock_name}_documents.zip", mime="application/zip", use_container_width=True, type="primary")
                if failed_docs:
                    if actual_downloaded_count == 0: status_text_area.error(f"No documents downloaded out of {total_attempted_count} for '{sanitized_stock_name}'.")
                    st.markdown("---"); st.subheader("⚠️ Download Failures Reported:")
                    for failure in failed_docs:
                        reason = failure.get('reason', 'Unknown Failure')
                        detail = failure.get('reason_detail')
                        msg = f"Could not download {failure['type']}: '{failure['base_name']}'."
                        if reason == "SELENIUM_DRIVER_INIT_ERROR" or reason == "SELENIUM_DRIVER_ALL_APPROACHES_FAILED": msg += " Affected by critical browser driver initialization failure." # Detail already shown by st.error
                        elif "EXCEPTION" in reason or reason == "LOOP_PROCESSING_ERROR": msg += f" An error occurred: {detail if detail else 'Undetermined error'}."
                        elif reason == "DOWNLOAD_FAILED_HTML_CONTENT": msg += " Received HTML page instead of the expected file."
                        elif reason == "DOWNLOAD_FAILED_TOO_SMALL": msg += " Downloaded file was too small and considered invalid."
                        elif reason == "REQUESTS_ONLY_FAILED": msg += f" Requests-only approach failed. {detail if detail else ''}"
                        elif reason and reason != "DOWNLOAD_FAILED": msg += f" Reason: {reason}."
                        else: msg += " Download failed for an unspecified reason."
                        msg += f" Source: [{failure['url']}]({failure['url']})"
                        st.warning(msg)
                elif actual_downloaded_count == 0 and total_attempted_count > 0:
                    status_text_area.error(f"No documents were successfully downloaded out of {total_attempted_count} attempted for '{sanitized_stock_name}'.")
        elif submit_button and not stock_name: st.error("Please enter a stock name.")
        st.markdown("<hr style='margin-top: 2em; margin-bottom: 0.5em;'>", unsafe_allow_html=True)
        st.caption("StockLib is a tool for educational purposes only. Not financial advice.")
    except Exception as e:
        st.error(f"A critical application error occurred: {str(e)}. Please refresh and try again.")
        # import sys, traceback 
        # print(f"CRITICAL APP ERROR: {e}\n{traceback.format_exc()}", file=sys.stderr)

if __name__ == "__main__":
    main()
