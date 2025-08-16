import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import zipfile
import time
import urllib.parse
import base64
import io
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import tempfile
import shutil

# --- SVG Logo Processing ---
your_svg_code = """
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#00aaff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path>
  <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path>
</svg>
"""

def svg_to_data_uri(svg_string):
    encoded_svg = urllib.parse.quote(svg_string)
    return f"data:image/svg+xml,{encoded_svg}"

# --- Page Configuration ---
st.set_page_config(
    page_title="StockLib",
    page_icon=svg_to_data_uri(your_svg_code),
    layout="centered"
)

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

# --- Core Backend Functions ---

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
            'application/pdf': '.pdf', 'application/vnd.ms-powerpoint': '.ppt',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
            'application/msword': '.doc', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
            'application/zip': '.zip', 'application/x-zip-compressed': '.zip', 'text/csv': '.csv',
        }
        if ct in mime_to_ext: return mime_to_ext[ct]
    try:
        parsed_url_path = urllib.parse.urlparse(url).path
        _, ext_from_url = os.path.splitext(parsed_url_path)
        if ext_from_url and 1 < len(ext_from_url) < 7: return ext_from_url.lower()
    except Exception: pass
    return '.pptx' if doc_type_for_default == 'PPT' else '.pdf'

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

def get_webpage_content(stock_name):
    url = f"https://www.screener.in/company/{stock_name}/consolidated/#documents"
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=REQUESTS_CONNECT_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.exceptions.HTTPError as e:
        return {"error": f"Stock '{stock_name}' not found. Check ticker."} if e.response.status_code == 404 else {"error": f"HTTP error for '{stock_name}'. Status: {e.response.status_code}."}
    except requests.exceptions.ConnectionError: return {"error": f"Connection error for '{stock_name}'. Check internet."}
    except requests.exceptions.Timeout: return {"error": f"Timeout fetching page for '{stock_name}'. Server slow."}
    except requests.exceptions.RequestException as e: return {"error": f"Error fetching data for '{stock_name}': {str(e)}."}

def parse_html_content(html_content):
    if not html_content: return []
    soup = BeautifulSoup(html_content, 'html.parser')
    all_links = []
    for link in soup.select('.annual-reports ul.list-links li a'):
        if (year_match := re.search(r'Financial Year (\d{4})', link.text.strip())):
            all_links.append({'date': year_match.group(1), 'type': 'Annual_Report', 'url': link['href']})
    for item in soup.select('.concalls ul.list-links li'):
        if (date_div := item.select_one('.ink-600.font-size-15')):
            date_text = date_div.text.strip()
            try: date_str = datetime.strptime(date_text, '%b %Y').strftime('%Y-%m')
            except ValueError: date_str = date_text
            for link_tag in item.find_all('a', class_='concall-link'):
                if 'Transcript' in link_tag.text: all_links.append({'date': date_str, 'type': 'Transcript', 'url': link_tag['href']})
                elif 'PPT' in link_tag.text: all_links.append({'date': date_str, 'type': 'PPT', 'url': link_tag['href']})
    return sorted(all_links, key=lambda x: x['date'], reverse=True)

def download_with_requests(url, folder_path, base_name_no_ext, doc_type):
    try:
        session = requests.Session()
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        response = session.get(url, headers=headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        response.raise_for_status()
        file_ext = get_extension_from_response(response, url, doc_type)
        path_written = os.path.join(folder_path, base_name_no_ext + file_ext)
        content_buffer = io.BytesIO()
        with open(path_written, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: f.write(chunk); content_buffer.write(chunk)
        content = content_buffer.getvalue()
        if content.strip().startswith(b'<!DOCTYPE html') or len(content) < MIN_FILE_SIZE:
            if os.path.exists(path_written): os.remove(path_written)
            return None, None, "DOWNLOAD_FAILED_INVALID_CONTENT", "File was HTML or too small."
        return path_written, content, None, None
    except requests.exceptions.RequestException as e:
        return None, None, "DOWNLOAD_FAILED_EXCEPTION", str(e)

# <<< UPDATED FOR CLOUD: No Service in cloud, no webdriver_manager >>>
def download_with_selenium(url, folder_path, base_name_no_ext, doc_type, driver=None):
    created_driver = False
    if driver is None:
        created_driver = True
        driver = None
        try:
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
            
            if os.path.exists("/home/appuser"):  # Streamlit cloud: Use system-installed chromedriver
                driver = webdriver.Chrome(options=chrome_options)  # No Service; relies on PATH
            else:  # Local: Use webdriver_manager
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=chrome_options)
            
            driver.set_page_load_timeout(SELENIUM_PAGE_LOAD_TIMEOUT)
        except Exception as e:
            return None, None, "DRIVER_CREATION_FAILED", str(e)

    try:
        driver.get(url)
        time.sleep(10)  # Increased for stability
        
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        response = requests.get(driver.current_url, headers={"User-Agent": random.choice(USER_AGENTS)}, cookies=cookies, stream=True)
        response.raise_for_status()

        file_ext = get_extension_from_response(response, driver.current_url, doc_type)
        path_written = os.path.join(folder_path, base_name_no_ext + file_ext)
        content_buffer = io.BytesIO()

        with open(path_written, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: f.write(chunk); content_buffer.write(chunk)

        content = content_buffer.getvalue()
        if content.strip().startswith(b'<!DOCTYPE html') or len(content) < MIN_FILE_SIZE:
             if os.path.exists(path_written): os.remove(path_written)
             return None, None, "DOWNLOAD_FAILED_INVALID_CONTENT", "Selenium got HTML or small file."

        return path_written, content, None, None
    except Exception as e:
        return None, None, "DOWNLOAD_FAILED_EXCEPTION_SEL", str(e)
    finally:
        if created_driver and driver:
            driver.quit()

def download_file_attempt(url, folder_path, base_name_no_ext, doc_type, driver=None):
    path_req, content_req, error_req, detail_req = download_with_requests(url, folder_path, base_name_no_ext, doc_type)
    if path_req and content_req: return path_req, content_req, None, None
    path_sel, content_sel, error_sel, detail_sel = download_with_selenium(url, folder_path, base_name_no_ext, doc_type, driver)
    if path_sel and content_sel: return path_sel, content_sel, None, None
    return None, None, error_sel or error_req or "DOWNLOAD_FAILED", detail_sel or detail_req

def download_selected_documents(links, output_folder, doc_types, progress_bar, status_text, driver=None):
    file_contents_for_zip = {}
    failed_downloads_details = []
    filtered_links = [link for link in links if link['type'] in doc_types]
    total_to_download = len(filtered_links)
    for i, link_info in enumerate(filtered_links):
        base_name = format_filename_base(link_info['date'], link_info['type'])
        status_text.text(f"Downloading {i+1}/{total_to_download}: {base_name}...")
        try:
            path, content, error, detail = download_file_attempt(link_info['url'], output_folder, base_name, link_info['type'], driver)
            if path and content:
                filename_for_zip = os.path.basename(path)
                file_contents_for_zip[filename_for_zip] = content
            else:
                failed_downloads_details.append({'url': link_info['url'], 'type': link_info['type'], 'base_name': base_name, 'reason': error, 'reason_detail': detail})
        except Exception as e:
            failed_downloads_details.append({'url': link_info.get('url', 'N/A'), 'type': link_info.get('type', 'Unknown'), 'base_name': base_name, 'reason': "LOOP_ERROR", 'reason_detail': str(e)})
        progress_bar.progress((i + 1) / total_to_download)
        time.sleep(random.uniform(0.5, 1.0))  # Increased delay for cloud stability
    status_text.text("All downloads attempted.")
    return file_contents_for_zip, failed_downloads_details

def create_zip_in_memory(file_contents_dict):
    if not file_contents_dict: return None
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_name, content in file_contents_dict.items():
            zf.writestr(file_name, content)
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

# --- Streamlit UI ---
st.markdown("""<h1 style='text-align: center;'>StockLib 📚</h1><p style='text-align: center; color: #bbb;'>Your First Step in Fundamental Analysis – Your Business Data Library!</p>""", unsafe_allow_html=True)
st.markdown("---")
with st.expander("About StockLib & Quick Guide"):
    st.markdown("""
    **StockLib + NotebookLLM = Your AI-powered business analyst.**

    StockLib helps you gather public company documents (annual reports, presentation decks, and concall transcripts), package them, and quickly analyze them with NotebookLLM. It's designed for investors, analysts, and students who want an organized, searchable library of company disclosures.

    - **What it fetches:** Annual reports, earning transcripts, investor presentations.
    - **Why it helps:** Saves time, creates a single ZIP, and enables fast LLM-based analysis.

    **How to use:**
    1. Enter the stock ticker (BSE/NSE).
    2. Choose which document types to fetch.
    3. Click "Fetch Documents" and wait for the ZIP file.

    *Sources: screener.in, official company investor relations pages, and public filings.*
    """)
with st.form(key='stock_form'):
    stock_name = st.text_input("Enter the stock name (BSE/NSE ticker):", placeholder="Example: TATAMOTORS", key="stock_name_input")
    st.markdown("<label>Select Document Types:</label>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1: get_annual_reports = st.checkbox("Annual Reports 📄", value=True)
    with col2: get_transcripts = st.checkbox("Concall Transcripts 📝", value=True)
    with col3: get_ppts = st.checkbox("Presentations 📊", value=True)
    submit_button = st.form_submit_button(label="🔍 Fetch Documents")
if submit_button:
    stock_name = stock_name.strip().upper()
    doc_types = []
    if get_annual_reports: doc_types.append("Annual_Report")
    if get_transcripts: doc_types.append("Transcript")
    if get_ppts: doc_types.append("PPT")
    if not stock_name: st.error("❌ Please enter a stock name.")
    elif not doc_types: st.warning("⚠️ Please select at least one document type.")
    else:
        with st.spinner(f"Searching for documents for '{stock_name}'..."):
            html_result = get_webpage_content(stock_name)
            if isinstance(html_result, dict) and "error" in html_result:
                st.error(f"❌ {html_result['error']}")
            else:
                links = parse_html_content(html_result)
                links_to_download = [link for link in links if link['type'] in doc_types]
                if not links_to_download:
                    st.warning(f"📭 No documents of the selected types found for '{stock_name}'.")
                else:
                    st.info(f"Found {len(links_to_download)} documents to download.")
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    driver = None
                    file_contents = {}  # Define to avoid NameError
                    failed_docs = []  # Define to avoid NameError
                    with tempfile.TemporaryDirectory() as temp_dir:
                        try:
                            chrome_options = Options()
                            chrome_options.add_argument("--headless")
                            chrome_options.add_argument("--no-sandbox")
                            chrome_options.add_argument("--disable-gpu")
                            chrome_options.add_argument("--disable-dev-shm-usage")
                            chrome_options.add_argument("--window-size=1920,1080")
                            chrome_options.add_argument("--disable-extensions")
                            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
                            chrome_options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
                            
                            if os.path.exists("/home/appuser"):  # Streamlit cloud
                                driver = webdriver.Chrome(options=chrome_options)  # No Service
                            else:  # Local
                                service = Service(ChromeDriverManager().install())
                                driver = webdriver.Chrome(service=service, options=chrome_options)

                            driver.set_page_load_timeout(SELENIUM_PAGE_LOAD_TIMEOUT)

                            file_contents, failed_docs = download_selected_documents(links_to_download, temp_dir, doc_types, progress_bar, status_text, driver)
                        except Exception as e:
                            st.error(f"Driver creation failed: {str(e)}")
                            failed_docs = []  # Ensure defined
                        finally:
                            if driver:
                                driver.quit()

                    if file_contents:
                        st.success(f"✅ Successfully downloaded {len(file_contents)}/{len(links_to_download)} documents!")
                        zip_data = create_zip_in_memory(file_contents)
                        if zip_data:
                            st.download_button(label="📥 Download Documents as ZIP", data=zip_data, file_name=f"{stock_name}_documents.zip", mime="application/zip", use_container_width=True)
                        else: st.error("Failed to create ZIP file.")
                        if failed_docs:
                            with st.expander(f"⚠️ View {len(failed_docs)} Download Failures"):
                                for failure in failed_docs:
                                    st.error(f"**{failure['base_name']}** ({failure['type']})")
                                    st.caption(f"Reason: {failure.get('reason', 'Unknown')} - {failure.get('reason_detail', 'No detail')}")
                                    st.caption(f"URL: {failure['url']}")
                    else:
                        st.error(f"❌ No documents were successfully downloaded out of {len(links_to_download)} attempted for '{stock_name}'.")
                        if failed_docs:
                            with st.expander("View All Download Failures"):
                                for failure in failed_docs:
                                    st.error(f"**{failure['base_name']}** ({failure['type']})")
                                    st.caption(f"Reason: {failure.get('reason', 'Unknown')} - {failure.get('reason_detail', 'No detail')}")
                                    st.caption(f"URL: {failure['url']}")
st.markdown("---")
st.markdown("<p style='text-align: center; color: #6c757d; font-size: 14px;'>StockLib is a tool for educational purposes only. Not financial advice.</p>", unsafe_allow_html=True)
