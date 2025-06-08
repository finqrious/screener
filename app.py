import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import zipfile
import time
import urllib.parse
import streamlit as st
import io
import random

# --- Global Constants ---
MIN_FILE_SIZE = 1024
REQUESTS_CONNECT_TIMEOUT = 15
REQUESTS_READ_TIMEOUT = 180

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
]

# --- Helper Functions (Unchanged) ---
def get_extension_from_response(response, url, doc_type_for_default):
    content_disposition = response.headers.get('Content-Disposition')
    if content_disposition:
        filenames = re.findall(r'filename\*?=(?:UTF-\d{1,2}\'\'|")?([^";\s]+)', content_disposition, re.IGNORECASE)
        if filenames:
            parsed_filename = urllib.parse.unquote(filenames[-1].strip('"'))
            _, ext = os.path.splitext(parsed_filename)
            if ext and 1 < len(ext) < 7: return ext.lower()
    content_type = response.headers.get('Content-Type', '').lower()
    mime_to_ext = {'application/pdf': '.pdf', 'application/vnd.ms-powerpoint': '.ppt', 'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx', 'application/msword': '.doc', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx', 'application/zip': '.zip', 'application/x-zip-compressed': '.zip', 'text/csv': '.csv'}
    if content_type.split(';')[0].strip() in mime_to_ext:
        return mime_to_ext[content_type.split(';')[0].strip()]
    try:
        _, ext_from_url = os.path.splitext(urllib.parse.urlparse(url).path)
        if ext_from_url and 1 < len(ext_from_url) < 7: return ext_from_url.lower()
    except Exception: pass
    return '.pptx' if doc_type_for_default == 'PPT' else '.pdf'

def format_filename_base(date_str, doc_type):
    if re.match(r'^\d{4}$', date_str): return f"{date_str}_{doc_type}"
    if re.match(r'^\d{4}-\d{2}$', date_str): year, month = date_str.split('-'); return f"{year}_{month}_{doc_type}"
    if re.match(r'^\d{2}/\d{2}/\d{4}$', date_str): day, month, year = date_str.split('/'); return f"{year}_{month}_{day}_{doc_type}"
    clean_date = re.sub(r'[^\w\.-]', '_', date_str)
    return f"{clean_date}_{doc_type}"

# --- Core Web Interaction and Parsing (Unchanged) ---
def get_webpage_content(stock_name):
    url = f"https://www.screener.in/company/{stock_name}/consolidated/#documents"
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=REQUESTS_CONNECT_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching data from Screener.in: {e}")
        return None

def parse_html_content(html_content):
    if not html_content: return []
    soup = BeautifulSoup(html_content, 'html.parser')
    all_links = []
    for link in soup.select('.annual-reports ul.list-links li a'):
        if year_match := re.search(r'Financial Year (\d{4})', link.text.strip()):
            all_links.append({'date': year_match.group(1), 'type': 'Annual_Report', 'url': link['href']})
    for item in soup.select('.concalls ul.list-links li'):
        if date_div := item.select_one('.ink-600.font-size-15'):
            try: date_str = datetime.strptime(date_div.text.strip(), '%b %Y').strftime('%Y-%m')
            except ValueError: date_str = date_div.text.strip()
            for link_tag in item.find_all('a', class_='concall-link'):
                doc_type = 'Transcript' if 'Transcript' in link_tag.text else 'PPT' if 'PPT' in link_tag.text else None
                if doc_type: all_links.append({'date': str(date_str), 'type': doc_type, 'url': link_tag['href']})
    return sorted(all_links, key=lambda x: x['date'], reverse=True)

# --- NEW AND FINAL ROBUST DOWNLOAD LOGIC ---
def download_file_attempt(url, base_name_no_ext, doc_type):
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    # Check if this is a BSE Annual Report link that needs special handling
    if "bseindia.com" in url and "AnnPdfOpen.aspx" in url:
        if pname_match := re.search(r'Pname=([^&]+)', url):
            pname_value = pname_match.group(1)
            
            # This is the direct attachment link format
            download_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pname_value}"
            
            # *** THIS IS THE CRITICAL FIX ***
            # BSE requires a plausible Referer to prevent hotlinking.
            # We construct one that looks like we came from the announcements page.
            scrip_code_match = re.search(r'scripcode=(\d+)', url, re.IGNORECASE)
            scrip_code = scrip_code_match.group(1) if scrip_code_match else pname_value[:6]
            headers["Referer"] = f"https://www.bseindia.com/corporates/ann.html?scrip={scrip_code}"
            
        else: # If we can't parse it, use the original URL
            download_url = url
    else:
        # For all other links (NSE, company sites, etc.), use the URL as is
        download_url = url

    try:
        response = requests.get(download_url, headers=headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
        response.raise_for_status()

        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' in content_type:
            return None, None, "DOWNLOAD_FAILED_HTML", "Server returned an HTML page instead of a file."

        content = response.content
        if len(content) < MIN_FILE_SIZE:
            return None, None, "DOWNLOAD_FAILED_TOO_SMALL", "Downloaded file was too small to be valid."

        file_ext = get_extension_from_response(response, download_url, doc_type)
        filename = base_name_no_ext + file_ext
        return filename, content, None, None

    except requests.exceptions.RequestException as e:
        return None, None, "DOWNLOAD_FAILED", str(e)


# --- Main Application Logic ---
def download_selected_documents(links, doc_types, progress_bar, status_text_area):
    file_contents_for_zip = {}; failed_downloads_details = []
    links_to_download = [link for link in links if link['type'] in doc_types]
    total_to_attempt = len(links_to_download)
    if total_to_attempt == 0: return {}, []

    for i, link_info in enumerate(links_to_download):
        base_name = format_filename_base(link_info['date'], link_info['type'])
        status_text_area.text(f"Downloading: {base_name} ({i+1}/{total_to_attempt})...")
        filename, content, error, detail = download_file_attempt(link_info['url'], base_name, link_info['type'])
        
        if filename and content:
            file_contents_for_zip[filename] = content
        else:
            failed_downloads_details.append({'url': link_info['url'], 'type': link_info['type'], 'base_name': base_name, 'reason': error, 'reason_detail': detail})
        
        progress_bar.progress((i + 1) / total_to_attempt)
        time.sleep(0.1)
    return file_contents_for_zip, failed_downloads_details

def create_zip_in_memory(file_contents_dict):
    if not file_contents_dict: return None
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_name, content in file_contents_dict.items(): zf.writestr(file_name, content)
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def main():
    st.set_page_config(page_title="StockLib", page_icon="📚", layout="centered")

    try:
        st.markdown("<div style='text-align: center;'><h1 style='margin-bottom: 0.2em;'>StockLib 📚</h1><h4 style='color: #808080; margin-top: 0em; font-weight: normal;'>Your First Step in Fundamental Analysis</h4></div>", unsafe_allow_html=True)

        with st.form(key='stock_form'):
            stock_name = st.text_input("Enter stock ticker (e.g., TATAMOTORS):", key="stock_name")
            st.markdown("##### Select Document Types")
            col1, col2, col3 = st.columns(3)
            with col1: annual_reports_cb = st.checkbox("Annual Reports 📄", value=True)
            with col2: transcripts_cb = st.checkbox("Concall Transcripts 📝", value=True)
            with col3: ppts_cb = st.checkbox("Presentations 📊", value=True)
            submit_button = st.form_submit_button(label="🔍 Fetch & Download Documents", use_container_width=True, type="primary")

        if submit_button and stock_name:
            doc_types = [dtype for flag, dtype in [(annual_reports_cb, "Annual_Report"), (transcripts_cb, "Transcript"), (ppts_cb, "PPT")] if flag]
            if not doc_types:
                st.warning("Please select at least one document type."); st.stop()
            
            sanitized_name = stock_name.strip().upper()
            with st.spinner(f"🔍 Searching documents for '{sanitized_name}'..."):
                html_content = get_webpage_content(sanitized_name)
                if not html_content: st.stop()
                parsed_links = parse_html_content(html_content)
            
            links_to_download = [link for link in parsed_links if link['type'] in doc_types]
            if not links_to_download:
                st.warning(f"No documents of the selected types found for '{sanitized_name}'."); st.stop()

            st.success(f"Found {len(links_to_download)} documents. Starting download...")
            progress_bar_area = st.empty(); status_text_area = st.empty()
            progress_bar = progress_bar_area.progress(0)

            file_contents, failed_docs = download_selected_documents(links_to_download, doc_types, progress_bar, status_text_area)
            
            progress_bar_area.empty(); status_text_area.empty()

            if file_contents:
                st.success(f"Successfully downloaded {len(file_contents)} out of {len(links_to_download)} documents.")
                zip_data = create_zip_in_memory(file_contents)
                st.download_button(label=f"📥 Download {len(file_contents)} Documents as ZIP", data=zip_data, file_name=f"{sanitized_name}_documents.zip", mime="application/zip", use_container_width=True)
            
            if failed_docs:
                st.error(f"Failed to download {len(failed_docs)} documents.")
                with st.expander("View failed download details"):
                    for failure in failed_docs:
                        st.warning(f"**{failure['base_name']}**: {failure.get('reason_detail', 'Unknown error')}")
                        st.caption(f"Source: {failure['url']}")

    except Exception as e:
        st.error(f"A critical application error occurred: {e}")

if __name__ == "__main__":
    main()
