import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import zipfile
import time
import urllib.parse # For get_extension_from_response
import streamlit as st
import base64
import io
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import tempfile
import shutil
# import traceback # For detailed server-side exception logging if needed

# --- Global Constants ---
MIN_FILE_SIZE = 1024
REQUESTS_CONNECT_TIMEOUT = 15
REQUESTS_READ_TIMEOUT = 300
SELENIUM_PAGE_LOAD_TIMEOUT = 300
SELENIUM_DOWNLOAD_WAIT_TIMEOUT = 300

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"
]

# --- Helper Functions ---
def get_extension_from_response(response, url, doc_type_for_default):
    content_disposition = response.headers.get('Content-Disposition')
    if content_disposition:
        filenames = re.findall(r'filename\*?=(?:UTF-\d{1,2}\'\'|")?([^";\s]+)', content_disposition, re.IGNORECASE)
        if filenames:
            parsed_filename = urllib.parse.unquote(filenames[-1].strip('"'))
            _, ext = os.path.splitext(parsed_filename)
            if ext and 1 < len(ext) < 7:
                return ext.lower()
    content_type = response.headers.get('Content-Type')
    if content_type:
        ct = content_type.split(';')[0].strip().lower()
        mime_to_ext = {
            'application/pdf': '.pdf',
            'application/vnd.ms-powerpoint': '.ppt',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
            'application/msword': '.doc',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
            'application/zip': '.zip', 'application/x-zip-compressed': '.zip',
            'text/csv': '.csv',
        }
        if ct in mime_to_ext: return mime_to_ext[ct]
    try:
        parsed_url_path = urllib.parse.urlparse(url).path
        _, ext_from_url = os.path.splitext(parsed_url_path)
        if ext_from_url and 1 < len(ext_from_url) < 7: return ext_from_url.lower()
    except Exception: pass
    if doc_type_for_default == 'PPT': return '.pptx'
    elif doc_type_for_default == 'Transcript': return '.pdf'
    return '.pdf'

def format_filename_base(date_str, doc_type):
    if re.match(r'^\d{4}$', date_str): return f"{date_str}_{doc_type}"
    if re.match(r'^\d{4}-\d{2}$', date_str):
        year, month = date_str.split('-')
        return f"{year}_{month}_{doc_type}"
    if re.match(r'^\d{2}/\d{2}/\d{4}$', date_str):
        day, month, year = date_str.split('/')
        return f"{year}_{month}_{day}_{doc_type}"
    clean_date = re.sub(r'[^\w\.-]', '_', date_str)
    return f"{clean_date}_{doc_type}"

# --- Core Web Interaction and Parsing ---
def get_webpage_content(stock_name):
    url = f"https://www.screener.in/company/{stock_name}/consolidated/#documents"
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
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
    soup = BeautifulSoup(html_content, 'html.parser')
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

# --- Download Functions (Return path, content, error_marker, error_detail_str) ---
def download_with_requests(url, folder_path, base_name_no_ext, doc_type):
    path_written = None; content_buffer = io.BytesIO(); session = requests.Session()
    req_headers = {"User-Agent": random.choice(USER_AGENTS), "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8,application/pdf,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation", "Accept-Language": "en-US,en;q=0.5", "DNT": "1", "Connection": "keep-alive", "Upgrade-Insecure-Requests": "1"}
    try:
        current_headers = req_headers.copy(); response = None
        if "bseindia.com" in url:
            session.get("https://www.bseindia.com/", headers=current_headers, timeout=REQUESTS_CONNECT_TIMEOUT); time.sleep(random.uniform(0.5, 1.5))
            current_headers["Referer"] = "https://www.bseindia.com/"; current_headers["Sec-Fetch-Site"] = "same-origin"
            if "AnnPdfOpen.aspx" in url:
                pname_match = re.search(r'Pname=([^&]+)', url)
                if pname_match:
                    pname_value = pname_match.group(1); alt_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pname_value}"
                    bse_ann_headers = current_headers.copy(); bse_ann_headers["Referer"] = f"https://www.bseindia.com/corporates/ann.html?scrip={pname_value[:6]}"
                    try: response = session.get(alt_url, headers=bse_ann_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT)); response.raise_for_status()
                    except requests.exceptions.RequestException: response = session.get(url, headers=bse_ann_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT)); response.raise_for_status()
                else: response = session.get(url, headers=current_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT)); response.raise_for_status()
            else: response = session.get(url, headers=current_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT)); response.raise_for_status()
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            nse_initial_headers = current_headers.copy()
            session.get("https://www.nseindia.com/", headers=nse_initial_headers, timeout=REQUESTS_CONNECT_TIMEOUT); time.sleep(random.uniform(0.5, 1.5))
            nse_download_headers = current_headers.copy(); nse_download_headers["Referer"] = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"; nse_download_headers["Sec-Fetch-Site"] = "same-origin"
            session.get("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports", headers=nse_download_headers, timeout=REQUESTS_CONNECT_TIMEOUT); time.sleep(random.uniform(0.5, 1.5))
            response = session.get(url, headers=nse_download_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT)); response.raise_for_status()
        else: response = session.get(url, headers=current_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT)); response.raise_for_status()

        file_ext = get_extension_from_response(response, url, doc_type)
        file_name_with_ext = base_name_no_ext + file_ext
        path_written = os.path.join(folder_path, file_name_with_ext)
        with open(path_written, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: f.write(chunk); content_buffer.write(chunk)
        content = content_buffer.getvalue()
        if content.strip().startswith(b'<!DOCTYPE html') or content.strip().startswith(b'<html'):
             if os.path.exists(path_written): os.remove(path_written)
             html_detail = "HTML page received. Could be a CAPTCHA or error page."
             try:
                soup = BeautifulSoup(content, 'html.parser')
                if soup.title and soup.title.string:
                    html_detail = f"HTML page with title: '{soup.title.string.strip()}'"
             except: pass
             return None, None, "DOWNLOAD_FAILED_HTML_CONTENT", html_detail
        if len(content) < MIN_FILE_SIZE:
            if os.path.exists(path_written): os.remove(path_written)
            return None, None, "DOWNLOAD_FAILED_TOO_SMALL", None
        return path_written, content, None, None
    except requests.exceptions.RequestException as e:
        return None, None, "DOWNLOAD_FAILED_EXCEPTION", str(e)
    finally:
        if hasattr(content_buffer, 'closed') and not content_buffer.closed: content_buffer.close()
        if path_written and os.path.exists(path_written) and ('content' not in locals() or not content or len(content) < MIN_FILE_SIZE):
            try: os.remove(path_written)
            except OSError: pass

def download_with_selenium(url, folder_path, base_name_no_ext, doc_type):
    driver = None; selenium_temp_dir = None; path_written = None
    try:
        chrome_options = Options()

        # --- UPDATED SECTION FOR PRODUCTION (STREAMLIT CLOUD) ---
        # When deploying, we use chromium-browser installed via packages.txt
        # This makes the code work both locally (with default Chrome) and on the server (with Chromium).
        if os.path.exists("/usr/bin/chromium-browser"):
            chrome_options.binary_location = "/usr/bin/chromium-browser"
        # -----------------------------------------------------------

        chrome_options.add_argument("--headless"); chrome_options.add_argument("--disable-gpu"); chrome_options.add_argument("--no-sandbox"); chrome_options.add_argument("--disable-dev-shm-usage"); chrome_options.add_argument("--window-size=1920,1080"); chrome_options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}"); chrome_options.add_argument('--disable-extensions')
        selenium_temp_dir = tempfile.mkdtemp()
        prefs = {"download.default_directory": selenium_temp_dir, "download.prompt_for_download": False, "download.directory_upgrade": True, "plugins.always_open_pdf_externally": True}
        chrome_options.add_experimental_option("prefs", prefs)
        service = None
        try:
            # For Streamlit Community Cloud, chromedriver is in the PATH after installing from packages.txt
            if os.path.exists("/home/appuser") or os.path.exists("/usr/bin/chromium-driver"):
                service = Service()
            else: # For local development
                service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.set_page_load_timeout(SELENIUM_PAGE_LOAD_TIMEOUT)
        except Exception as driver_error: return None, None, "SELENIUM_DRIVER_INIT_ERROR", str(driver_error)

        if "bseindia.com" in url: driver.get("https://www.bseindia.com/"); time.sleep(random.uniform(1,2))
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            driver.get("https://www.nseindia.com/"); time.sleep(random.uniform(1,2))
            driver.get("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"); time.sleep(random.uniform(1,2))
        driver.get(url)
        dl_temp_file_path = None; start_time = time.time()
        while time.time() - start_time < SELENIUM_DOWNLOAD_WAIT_TIMEOUT:
            completed_files = [f for f in os.listdir(selenium_temp_dir) if not f.endswith(('.crdownload', '.tmp', '.part'))]
            if completed_files: dl_temp_file_path = os.path.join(selenium_temp_dir, sorted(completed_files, key=lambda f: os.path.getmtime(os.path.join(selenium_temp_dir, f)), reverse=True)[0]); break
            time.sleep(1)
        if dl_temp_file_path and os.path.exists(dl_temp_file_path):
            _, original_ext = os.path.splitext(os.path.basename(dl_temp_file_path))
            if not original_ext or not (1 < len(original_ext) < 7):
                mock_response = type('Response', (), {'headers': {}, 'text': driver.page_source})()
                original_ext = get_extension_from_response(mock_response, url, doc_type)
            file_name_with_ext = base_name_no_ext + original_ext.lower()
            path_written = os.path.join(folder_path, file_name_with_ext)
            with open(dl_temp_file_path, 'rb') as f_src, open(path_written, 'wb') as f_dst: content_bytes = f_src.read(); f_dst.write(content_bytes)
            try: os.remove(dl_temp_file_path)
            except OSError: pass
            if len(content_bytes) >= MIN_FILE_SIZE: return path_written, content_bytes, None, None
            else:
                if os.path.exists(path_written): os.remove(path_written)
                path_written = None
        if doc_type != 'PPT':
            try:
                if "application/pdf" in driver.page_source.lower() or driver.current_url.lower().endswith(".pdf"):
                    b64_src = driver.execute_script("var e=document.querySelector('embed[type=\"application/pdf\"]'); if(e)return e.src; var i=document.querySelector('iframe'); if(i&&i.src&&i.src.startsWith('data:application/pdf'))return i.src; return null;")
                    if b64_src and b64_src.startswith("data:application/pdf;base64,"):
                        content_bytes = base64.b64decode(b64_src.split(",")[1])
                        path_written = os.path.join(folder_path, base_name_no_ext + ".pdf")
                        with open(path_written, 'wb') as f: f.write(content_bytes)
                        if len(content_bytes) >= MIN_FILE_SIZE: return path_written, content_bytes, None, None
                        else:
                            if os.path.exists(path_written): os.remove(path_written)
                            path_written = None
            except Exception: pass
        try:
            cookies_dict = {c['name']: c['value'] for c in driver.get_cookies()}
            sel_req_headers = {"User-Agent": random.choice(USER_AGENTS), "Accept": "application/pdf,text/html,*/*", "Referer": driver.current_url}
            response = requests.get(url, headers=sel_req_headers, cookies=cookies_dict, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
            response.raise_for_status()
            file_ext = get_extension_from_response(response, url, doc_type)
            path_written = os.path.join(folder_path, base_name_no_ext + file_ext)
            content_buffer = io.BytesIO()
            with open(path_written, 'wb') as f:
                for chunk in response.iter_content(8192):
                    if chunk: f.write(chunk); content_buffer.write(chunk)
            content_bytes = content_buffer.getvalue(); content_buffer.close()
            if content_bytes.strip().startswith(b'<!DOCTYPE html') or content_bytes.strip().startswith(b'<html'):
                if os.path.exists(path_written): os.remove(path_written)
                return None, None, "DOWNLOAD_FAILED_HTML_CONTENT", None
            if len(content_bytes) >= MIN_FILE_SIZE: return path_written, content_bytes, None, None
            else:
                if os.path.exists(path_written): os.remove(path_written)
                return None, None, "DOWNLOAD_FAILED_TOO_SMALL", None
        except requests.exceptions.RequestException as e_req_cookie: return None, None, "DOWNLOAD_FAILED_EXCEPTION_SEL_COOKIE", str(e_req_cookie)
        finally:
            if 'content_buffer' in locals() and hasattr(content_buffer, 'closed') and not content_buffer.closed: content_buffer.close()
        return None, None, "DOWNLOAD_FAILED", None
    except Exception as e_sel_general: return None, None, "DOWNLOAD_FAILED_EXCEPTION_SEL_GENERAL", str(e_sel_general)
    finally:
        if driver: driver.quit()
        if selenium_temp_dir and os.path.exists(selenium_temp_dir):
            try: shutil.rmtree(selenium_temp_dir)
            except Exception: pass

def download_file_attempt(url, folder_path, base_name_no_ext, doc_type):
    path_req, content_req, error_req, detail_req = download_with_requests(url, folder_path, base_name_no_ext, doc_type)
    if path_req and content_req: return path_req, content_req, None, None
    st.write(f"Requests failed for {base_name_no_ext} ({detail_req}). Trying with browser automation...") # Add some debug info
    path_sel, content_sel, error_sel, detail_sel = download_with_selenium(url, folder_path, base_name_no_ext, doc_type)
    if path_sel and content_sel: return path_sel, content_sel, None, None
    if error_sel == "SELENIUM_DRIVER_INIT_ERROR": return None, None, "SELENIUM_DRIVER_INIT_ERROR", detail_sel
    if error_sel and "EXCEPTION" in error_sel: return None, None, error_sel, detail_sel # Prioritize Selenium's exception detail
    if error_req and "EXCEPTION" in error_req: return None, None, error_req, detail_req # Then Request's exception detail
    final_error = error_sel if error_sel else error_req if error_req else "DOWNLOAD_FAILED"
    final_detail = detail_sel if error_sel else detail_req # Pass detail if any
    return None, None, final_error, final_detail

# --- Main Application Logic ---
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
            temp_file_path, content_bytes, error_marker, error_detail_str = download_file_attempt(link_info['url'], output_folder, base_name_for_file, link_info['type'])
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
            time.sleep(random.uniform(0.1, 0.2))
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
                        if reason == "SELENIUM_DRIVER_INIT_ERROR": msg += " Affected by critical browser driver initialization failure." # Detail already shown by st.error
                        elif reason.endswith("EXCEPTION") or reason == "LOOP_PROCESSING_ERROR": msg += f" An error occurred: {detail if detail else 'Undetermined error'}."
                        elif reason == "DOWNLOAD_FAILED_HTML_CONTENT": msg += f" Received HTML page instead of file. {detail if detail else ''}"
                        elif reason == "DOWNLOAD_FAILED_TOO_SMALL": msg += " Downloaded file was too small and considered invalid."
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
        # import sys, traceback # For server-side debugging
        # print(f"CRITICAL APP ERROR: {e}\n{traceback.format_exc()}", file=sys.stderr)

if __name__ == "__main__":
    main()
