# ==============================================================================
# INSTRUCTIONS FOR DEPLOYMENT
# ==============================================================================
#
# 1. requirements.txt:
#    Create a file named 'requirements.txt' with the following content.
#    This is the ONLY dependency file you need.
#
#    requests==2.31.0
#    beautifulsoup4==4.12.2
#    streamlit==1.29.0
#    selenium==4.15.2        # Kept for scraping, but NOT for downloads
#    webdriver-manager==4.0.1 # Kept for local testing
#    lxml==4.9.3
#    urllib3==2.1.0
#
# 2. packages.txt:
#    This file should be EMPTY or REMOVED from your repository.
#    The application no longer relies on apt-get to install chrome/chromedriver.
#
# ==============================================================================

import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import zipfile
import time
import urllib.parse
import io
import random
import tempfile
import shutil

# --- Global Constants ---
MIN_FILE_SIZE = 1024  # 1 KB
REQUESTS_CONNECT_TIMEOUT = 15
REQUESTS_READ_TIMEOUT = 120 # Increased for large annual reports

# User agents list, updated by retry logic
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0"
]

# --- Helper Functions (from original app) ---

def format_filename_base(date_str, doc_type):
    """Creates a consistent base filename from date and type."""
    if re.match(r'^\d{4}$', date_str):
        return f"{date_str}_{doc_type}"
    if re.match(r'^\d{4}-\d{2}$', date_str):
        year, month = date_str.split('-')
        return f"{year}_{month}_{doc_type}"
    if re.match(r'^\d{2}/\d{2}/\d{4}$', date_str):
        day, month, year = date_str.split('/')
        return f"{year}_{month}_{day}_{doc_type}"
    clean_date = re.sub(r'[^\w\.-]', '_', date_str)
    return f"{clean_date}_{doc_type}"

def get_extension_from_response(response, url, doc_type):
    """Determines the file extension from response headers or URL."""
    if response:
        content_type = response.headers.get('content-type', '').lower()
        if 'pdf' in content_type: return '.pdf'
        if 'zip' in content_type: return '.zip'
        if 'ppt' in content_type or 'presentation' in content_type: return '.pptx'

        content_disposition = response.headers.get('content-disposition', '')
        if content_disposition:
            filenames = re.findall('filename="?(.+)"?', content_disposition)
            if filenames:
                _, ext = os.path.splitext(filenames[0])
                if ext: return ext.lower()
    
    parsed_url = urllib.parse.urlparse(url)
    _, ext = os.path.splitext(parsed_url.path)
    if ext and len(ext) < 6: return ext.lower()

    if doc_type == "PPT": return '.pptx'
    return '.pdf'

# --- Core Web Scraping (from original app) ---

def get_webpage_content(stock_name):
    """Fetches the main screener.in page to find document links."""
    url = f"https://www.screener.in/company/{stock_name}/consolidated/#documents"
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=REQUESTS_CONNECT_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            st.error(f"Stock '{stock_name}' not found on Screener. Please check the ticker symbol.")
        else:
            st.error(f"HTTP error fetching Screener page for '{stock_name}'. Status: {e.response.status_code}.")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching data for '{stock_name}': {str(e)}. Please check your connection and try again.")
        return None

def parse_html_content(html_content):
    """Parses the HTML from screener.in to extract document links."""
    if not html_content: return []
    soup = BeautifulSoup(html_content, 'lxml')
    all_links = []
    
    # Annual Reports
    annual_reports = soup.select('.annual-reports ul.list-links li a')
    for link in annual_reports:
        year_match = re.search(r'Financial Year (\d{4})', link.text.strip())
        if year_match:
            all_links.append({'date': year_match.group(1), 'type': 'Annual_Report', 'url': link['href']})

    # Concalls (Transcripts and PPTs)
    concall_items = soup.select('.concalls ul.list-links li')
    for item in concall_items:
        date_div = item.select_one('.ink-600.font-size-15')
        if date_div:
            date_text = date_div.text.strip()
            try:
                date_obj = datetime.strptime(date_text, '%b %Y')
                date_str = date_obj.strftime('%Y-%m')
            except ValueError:
                date_str = date_text
            
            for link_tag in item.find_all('a', class_='concall-link'):
                if 'Transcript' in link_tag.text:
                    all_links.append({'date': date_str, 'type': 'Transcript', 'url': link_tag['href']})
                elif 'PPT' in link_tag.text:
                    all_links.append({'date': date_str, 'type': 'PPT', 'url': link_tag['href']})
                    
    return sorted(all_links, key=lambda x: x['date'], reverse=True)


# --- NEW, ENHANCED DOWNLOAD LOGIC ---

def download_with_enhanced_requests(url, folder_path, base_name_no_ext, doc_type):
    """Enhanced requests-only download with better session management for BSE/NSE."""
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
        "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Cache-Control": "max-age=0"
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
                if pname_match:
                    pname_value = pname_match.group(1)
                    scrip_value = pname_match.group(1)[:6]
                    bse_urls_to_try = [
                        f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pname_value}",
                        f"https://www.bseindia.com/corporates/annpdf/{pname_value}",
                        url
                    ]
                    for bse_url in bse_urls_to_try:
                        try:
                            bse_headers = current_headers.copy()
                            bse_headers["Referer"] = f"https://www.bseindia.com/corporates/ann.html?scrip={scrip_value}"
                            response = session.get(bse_url, headers=bse_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
                            response.raise_for_status()
                            initial_chunk = next(response.iter_content(1024), None)
                            if initial_chunk and not (initial_chunk.strip().lower().startswith(b'<!doctype') or initial_chunk.strip().lower().startswith(b'<html')):
                                full_content_stream = io.BytesIO(initial_chunk + response.content)
                                response._content = full_content_stream.read()
                                response.iter_content = lambda chunk_size=8192: iter([response.content])
                                break
                        except requests.exceptions.RequestException:
                            response = None # Reset response on failure to ensure loop continues
                            continue
                    if not response: raise requests.exceptions.RequestException("All BSE URL patterns failed.")
                else: response = session.get(url, headers=current_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
            else: response = session.get(url, headers=current_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        
        # Enhanced NSE handling  
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            nse_headers = current_headers.copy()
            session.get("https://www.nseindia.com/", headers=nse_headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            time.sleep(random.uniform(1, 2))
            nse_headers["Referer"] = "https://www.nseindia.com/"
            response = session.get(url, headers=nse_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        
        # Default handling for other URLs
        else:
            response = session.get(url, headers=current_headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        
        response.raise_for_status()
        
        file_ext = get_extension_from_response(response, url, doc_type)
        file_name_with_ext = base_name_no_ext + file_ext
        path_written = os.path.join(folder_path, file_name_with_ext)
        
        with open(path_written, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: f.write(chunk); content_buffer.write(chunk)
        
        content = content_buffer.getvalue()
        
        if content.strip().lower().startswith(b'<!doctype html') or content.strip().lower().startswith(b'<html'):
            if os.path.exists(path_written): os.remove(path_written)
            return None, None, "DOWNLOAD_FAILED_HTML_CONTENT", "Received an HTML login/error page instead of a file."
        
        if len(content) < MIN_FILE_SIZE:
            if os.path.exists(path_written): os.remove(path_written)
            return None, None, "DOWNLOAD_FAILED_TOO_SMALL", f"File size ({len(content)} bytes) is below threshold."
        
        return path_written, content, None, None
            
    except requests.exceptions.RequestException as e:
        if path_written and os.path.exists(path_written):
            try: os.remove(path_written)
            except OSError: pass
        return None, None, "DOWNLOAD_FAILED_EXCEPTION", str(e)
    finally:
        content_buffer.close()
        session.close()

def download_with_retry_logic(url, folder_path, base_name_no_ext, doc_type, max_retries=3):
    """Wraps the download function with retry logic."""
    last_error, last_detail = None, None
    for attempt in range(max_retries):
        global USER_AGENTS
        current_user_agent = USER_AGENTS[attempt % len(USER_AGENTS)]
        
        # Temporarily set the global list to just the one we're using for this attempt
        # so download_with_enhanced_requests uses the correct one.
        original_user_agents = USER_AGENTS
        USER_AGENTS = [current_user_agent]
        
        if attempt > 0:
            delay = random.uniform(2, 5) * attempt
            st.info(f"Retrying download for {base_name_no_ext} (Attempt {attempt + 1}/{max_retries})...")
            time.sleep(delay)
        
        try:
            path, content, error, detail = download_with_enhanced_requests(url, folder_path, base_name_no_ext, doc_type)
            if path and content:
                USER_AGENTS = original_user_agents # Restore original list
                return path, content, None, None
            last_error, last_detail = error, detail
        except Exception as e:
            last_error, last_detail = "RETRY_EXCEPTION", str(e)
        finally:
            USER_AGENTS = original_user_agents # Ensure restoration

    return None, None, last_error, last_detail

def download_file_attempt(url, folder_path, base_name_no_ext, doc_type):
    """This is the main orchestrator. It uses the robust retry logic."""
    return download_with_retry_logic(url, folder_path, base_name_no_ext, doc_type)


# --- Main Application Logic (adapted from original) ---

def download_selected_documents(links, output_folder, doc_types, progress_bar, status_text_area):
    """Main loop to process and download all selected documents."""
    file_contents_for_zip = {}; failed_downloads_details = []
    filtered_links = [link for link in links if link['type'] in doc_types]
    total_files = len(filtered_links)
    if total_files == 0: return {}, []

    for i, link_info in enumerate(filtered_links):
        base_name = format_filename_base(link_info['date'], link_info['type'])
        status_text_area.text(f"Downloading: {base_name} ({i+1}/{total_files})")

        # Call the new, robust download function
        temp_path, content, error, detail = download_file_attempt(
            link_info['url'], output_folder, base_name, link_info['type']
        )
        
        if temp_path and content:
            filename = os.path.basename(temp_path)
            file_contents_for_zip[filename] = content
        else:
            failed_downloads_details.append({
                'url': link_info['url'], 'type': link_info['type'], 
                'base_name': base_name, 'reason': error, 'reason_detail': detail
            })
        
        progress_bar.progress((i + 1) / total_files)

    return file_contents_for_zip, failed_downloads_details

def create_zip_in_memory(file_contents_dict):
    if not file_contents_dict: return None
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_name, content in file_contents_dict.items():
            zf.writestr(file_name, content)
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def main():
    st.set_page_config(page_title="StockLib", page_icon="📚", layout="centered")
    
    st.markdown("<div style='text-align: center;'><h1 style='margin-bottom: 0.2em;'>StockLib 📚</h1><h4 style='color: #808080; margin-top: 0em; font-weight: normal;'>Your First Step in Fundamental Analysis</h4></div>", unsafe_allow_html=True)
    st.info("Enter a stock ticker, select documents, and get a ZIP file ready for analysis.")
    
    with st.form(key='stock_form'):
        stock_name = st.text_input("Enter the stock ticker (e.g., TATAMOTORS, RELIANCE)", placeholder="TATAMOTORS")
        
        st.markdown("##### Select Document Types")
        cols = st.columns(3)
        with cols[0]:
            annual_reports_cb = st.checkbox("Annual Reports 📄", value=True)
        with cols[1]:
            transcripts_cb = st.checkbox("Transcripts 📝", value=True)
        with cols[2]:
            ppts_cb = st.checkbox("Presentations 📊", value=True)
        
        submit_button = st.form_submit_button(label="🔍 Fetch & Download Documents", use_container_width=True, type="primary")

    if submit_button and stock_name:
        doc_types_selected = []
        if annual_reports_cb: doc_types_selected.append("Annual_Report")
        if transcripts_cb: doc_types_selected.append("Transcript")
        if ppts_cb: doc_types_selected.append("PPT")
        
        if not doc_types_selected:
            st.warning("Please select at least one document type.")
            st.stop()
            
        sanitized_stock_name = stock_name.strip().upper()
        
        with st.spinner(f"🔍 Searching documents for '{sanitized_stock_name}' on Screener..."):
            html_content = get_webpage_content(sanitized_stock_name)
            if not html_content: st.stop()
            parsed_links = parse_html_content(html_content)
            if not parsed_links:
                st.warning(f"No document links found on Screener for '{sanitized_stock_name}'.")
                st.stop()
            
        links_to_download = [link for link in parsed_links if link['type'] in doc_types_selected]
        if not links_to_download:
            st.warning(f"No documents of the selected types found for '{sanitized_stock_name}'.")
            st.stop()

        st.success(f"Found {len(links_to_download)} documents. Starting download process...")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            progress_bar_area = st.empty()
            status_text_area = st.empty()
            progress_bar = progress_bar_area.progress(0)
            
            file_contents, failed_docs = download_selected_documents(
                links_to_download, temp_dir, doc_types_selected, progress_bar, status_text_area
            )
            
            progress_bar_area.empty()
            status_text_area.empty()

            if file_contents:
                st.success(f"Successfully downloaded {len(file_contents)} out of {len(links_to_download)} documents.")
                zip_data = create_zip_in_memory(file_contents)
                if zip_data:
                    st.download_button(
                        label=f"📥 Download {len(file_contents)} Documents ZIP",
                        data=zip_data,
                        file_name=f"{sanitized_stock_name}_documents.zip",
                        mime="application/zip",
                        use_container_width=True,
                        type="primary"
                    )
            
            if failed_docs:
                if not file_contents:
                    st.error(f"Failed to download any of the {len(links_to_download)} documents.")
                with st.expander(f"⚠️ View {len(failed_docs)} Failed Downloads"):
                    for failure in failed_docs:
                        reason = failure.get('reason', 'Unknown')
                        detail = failure.get('reason_detail', 'No details.')
                        st.warning(f"**{failure['base_name']}** ({failure['type']})")
                        st.code(f"Reason: {reason}\nDetails: {detail}\nURL: {failure['url']}", language=None)

    elif submit_button and not stock_name:
        st.error("Please enter a stock name.")
        
    st.markdown("<hr style='margin-top: 2em; margin-bottom: 0.5em;'>", unsafe_allow_html=True)
    st.caption("StockLib is for educational purposes. Not financial advice. Data sourced via screener.in.")

if __name__ == "__main__":
    main()
