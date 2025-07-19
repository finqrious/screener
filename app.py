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
import tempfile
import shutil

# --- Global Constants ---
MIN_FILE_SIZE = 1024
REQUESTS_CONNECT_TIMEOUT = 15
REQUESTS_READ_TIMEOUT = 120

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
            'application/zip': '.zip',
            'application/x-zip-compressed': '.zip',
            'text/csv': '.csv',
        }
        if ct in mime_to_ext:
            return mime_to_ext[ct]

    try:
        parsed_url_path = urllib.parse.urlparse(url).path
        _, ext_from_url = os.path.splitext(parsed_url_path)
        if ext_from_url and 1 < len(ext_from_url) < 7:
            return ext_from_url.lower()
    except Exception:
        pass

    if doc_type_for_default == 'PPT':
        return '.pptx'
    elif doc_type_for_default == 'Transcript':
        return '.pdf'
    return '.pdf'

def format_filename_base(date_str, doc_type):
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

# --- Core Web Interaction and Parsing ---
def get_webpage_content(stock_name):
    url = f"https://www.screener.in/company/{stock_name}/consolidated/#documents"
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        response = requests.get(url, headers=headers, timeout=REQUESTS_CONNECT_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            st.error(f"Stock '{stock_name}' not found on Screener. Check ticker.")
        else:
            st.error(f"HTTP error fetching Screener page for '{stock_name}'. Status: {e.response.status_code}.")
        return None
    except requests.exceptions.ConnectionError:
        st.error(f"Connection error for '{stock_name}'. Check internet.")
        return None
    except requests.exceptions.Timeout:
        st.error(f"Timeout fetching page for '{stock_name}'. Server slow.")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching data for '{stock_name}': {str(e)}. Try again.")
        return None

def parse_html_content(html_content):
    if not html_content:
        return []
    
    soup = BeautifulSoup(html_content, 'html.parser')
    all_links = []
    
    # Annual reports
    annual_reports = soup.select('.annual-reports ul.list-links li a')
    for link in annual_reports:
        year_match = re.search(r'Financial Year (\d{4})', link.text.strip())
        if year_match:
            all_links.append({
                'date': year_match.group(1),
                'type': 'Annual_Report',
                'url': link['href']
            })
    
    # Concall items (transcripts and PPTs)
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
                    all_links.append({
                        'date': date_str,
                        'type': 'Transcript',
                        'url': link_tag['href']
                    })
                elif 'PPT' in link_tag.text:
                    all_links.append({
                        'date': date_str,
                        'type': 'PPT',
                        'url': link_tag['href']
                    })
    
    return sorted(all_links, key=lambda x: x['date'], reverse=True)

# --- Enhanced BSE Annual Report Download ---
def download_bse_annual_report_direct(url, folder_path, base_name_no_ext):
    """Direct download for BSE annual reports using updated URL patterns"""
    try:
        # Extract scrip code and file ID from old URL pattern
        match = re.search(r'/(\d+)/(\d+).pdf', url)
        if match:
            scrip_code = match.group(1)
            file_id = match.group(2).rstrip(scrip_code)  # Extract ID part

            # New BSE patterns
            possible_urls = [
                f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{file_id}.pdf",
                f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{file_id}.pdf",
                f"https://www.bseindia.com/bseplus/AnnualReport/{scrip_code}/{file_id}{scrip_code}.pdf"  # Fallback to old
            ]
            
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/pdf,*/*",
                "Referer": f"https://www.bseindia.com/stock-share-price/{scrip_code}.html",
                "Sec-Fetch-Site": "same-origin"
            }
            
            session = requests.Session()
            session.get("https://www.bseindia.com/", headers=headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            time.sleep(1)
            
            for direct_url in possible_urls:
                try:
                    response = session.get(direct_url, headers=headers, stream=True, 
                                         timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
                    response.raise_for_status()
                    
                    if response.headers.get('Content-Type', '').startswith('application/pdf'):
                        file_path = os.path.join(folder_path, base_name_no_ext + ".pdf")
                        content_buffer = io.BytesIO()
                        
                        with open(file_path, 'wb') as f:
                            for chunk in response.iter_content(8192):
                                if chunk:
                                    f.write(chunk)
                                    content_buffer.write(chunk)
                        
                        content_bytes = content_buffer.getvalue()
                        content_buffer.close()
                        
                        if len(content_bytes) >= MIN_FILE_SIZE:
                            return file_path, content_bytes, None, None
                        else:
                            if os.path.exists(file_path):
                                os.remove(file_path)
                            continue  # Try next URL
                except Exception:
                    continue
            
            return None, None, "DOWNLOAD_FAILED_NO_VALID_URL", "No valid BSE URL found"
                        
    except Exception as e:
        return None, None, "DOWNLOAD_FAILED_EXCEPTION_BSE_DIRECT", str(e)
    
    return None, None, "DOWNLOAD_FAILED_BSE_DIRECT", None

# --- Enhanced Requests Download ---
def download_with_requests(url, folder_path, base_name_no_ext, doc_type):
    path_written = None
    content_buffer = io.BytesIO()
    session = requests.Session()
    
    req_headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8,application/pdf,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    
    try:
        current_headers = req_headers.copy()
        response = None
        
        if "bseindia.com" in url:
            session.get("https://www.bseindia.com/", headers=current_headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            time.sleep(random.uniform(0.5, 1.5))
            current_headers["Referer"] = "https://www.bseindia.com/"
            current_headers["Sec-Fetch-Site"] = "same-origin"
            
            if "AnnPdfOpen.aspx" in url:
                pname_match = re.search(r'Pname=([^&]+)', url)
                if pname_match:
                    pname_value = pname_match.group(1)
                    alt_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pname_value}"
                    bse_ann_headers = current_headers.copy()
                    bse_ann_headers["Referer"] = f"https://www.bseindia.com/corporates/ann.html?scrip={pname_value[:6]}"
                    
                    try:
                        response = session.get(alt_url, headers=bse_ann_headers, stream=True, 
                                             timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
                        response.raise_for_status()
                    except requests.exceptions.RequestException:
                        response = session.get(url, headers=bse_ann_headers, stream=True, 
                                             timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
                        response.raise_for_status()
                else:
                    response = session.get(url, headers=current_headers, stream=True, 
                                         timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
                    response.raise_for_status()
            else:
                response = session.get(url, headers=current_headers, stream=True, 
                                     timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
                response.raise_for_status()
        
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            nse_initial_headers = current_headers.copy()
            session.get("https://www.nseindia.com/", headers=nse_initial_headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            time.sleep(random.uniform(0.5, 1.5))
            
            nse_download_headers = current_headers.copy()
            nse_download_headers["Referer"] = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
            nse_download_headers["Sec-Fetch-Site"] = "same-origin"
            
            session.get("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports", 
                       headers=nse_download_headers, timeout=REQUESTS_CONNECT_TIMEOUT)
            time.sleep(random.uniform(0.5, 1.5))
            
            response = session.get(url, headers=nse_download_headers, stream=True, 
                                 timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
            response.raise_for_status()
        
        else:
            response = session.get(url, headers=current_headers, stream=True, 
                                 timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT))
            response.raise_for_status()
        
        # Process the response
        file_ext = get_extension_from_response(response, url, doc_type)
        file_name_with_ext = base_name_no_ext + file_ext
        path_written = os.path.join(folder_path, file_name_with_ext)
        
        with open(path_written, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    content_buffer.write(chunk)
        
        content = content_buffer.getvalue()
        content_buffer.close()
        
        # Check if it's an HTML error page
        if content.strip().startswith(b'<!DOCTYPE') or content.strip().startswith(b'<html'):
            if os.path.exists(path_written):
                os.remove(path_written)
            return None, None, "DOWNLOAD_FAILED_HTML_RESPONSE", None
        
        if len(content) >= MIN_FILE_SIZE:
            return path_written, content, None, None
        else:
            if os.path.exists(path_written):
                os.remove(path_written)
            return None, None, "DOWNLOAD_FAILED_TOO_SMALL", None
    
    except requests.exceptions.RequestException as e:
        return None, None, "DOWNLOAD_FAILED_EXCEPTION_REQUESTS", str(e)
    except Exception as e:
        return None, None, "DOWNLOAD_FAILED_EXCEPTION_GENERAL", str(e)
    finally:
        if 'content_buffer' in locals() and hasattr(content_buffer, 'closed') and not content_buffer.closed:
            content_buffer.close()

# --- Main Download Logic ---
def download_file_attempt(url, folder_path, base_name_no_ext, doc_type):
    # Special handling for BSE annual reports
    if doc_type == 'Annual_Report' and "bseindia.com" in url:
        path_bse, content_bse, error_bse, detail_bse = download_bse_annual_report_direct(url, folder_path, base_name_no_ext)
        if path_bse and content_bse:
            return path_bse, content_bse, None, None
    
    # Try regular requests method
    path_req, content_req, error_req, detail_req = download_with_requests(url, folder_path, base_name_no_ext, doc_type)
    if path_req and content_req:
        return path_req, content_req, None, None
    
    # Return the most relevant error
    final_error = error_req or "DOWNLOAD_FAILED"
    final_detail = detail_req
    return None, None, final_error, final_detail

# --- Main Application Logic ---
def download_selected_documents(links, output_folder, doc_types, progress_bar, status_text_area):
    file_contents_for_zip = {}
    failed_downloads_details = []
    
    filtered_links = [link for link in links if link['type'] in doc_types]
    total_files_to_attempt = len(filtered_links)
    
    if total_files_to_attempt == 0:
        return {}, []
    
    progress_step = 1.0 / total_files_to_attempt
    downloaded_successfully_count = 0
    failed_count = 0
    
    for i, link_info in enumerate(filtered_links):
        base_name_for_file = format_filename_base(link_info['date'], link_info['type'])
        
        try:
            temp_file_path, content_bytes, error_marker, error_detail_str = download_file_attempt(
                link_info['url'], output_folder, base_name_for_file, link_info['type']
            )
            
            if temp_file_path and content_bytes:
                filename_for_zip = os.path.basename(temp_file_path)
                counter = 1
                name_part, ext_part = os.path.splitext(filename_for_zip)
                
                while filename_for_zip in file_contents_for_zip:
                    filename_for_zip = f"{name_part}_{counter}{ext_part}"
                    counter += 1
                
                file_contents_for_zip[filename_for_zip] = content_bytes
                downloaded_successfully_count += 1
            else:
                failed_count += 1
                failed_downloads_details.append({
                    'url': link_info['url'],
                    'type': link_info['type'],
                    'base_name': base_name_for_file,
                    'reason': error_marker,
                    'reason_detail': error_detail_str
                })
            
            current_progress_val = min((i + 1) * progress_step, 1.0)
            progress_bar.progress(current_progress_val)
            status_text_area.text(f"Processing: {i+1}/{total_files_to_attempt} | Downloaded: {downloaded_successfully_count} | Failed: {failed_count}")
            time.sleep(random.uniform(0.1, 0.2))
        
        except Exception as e_loop:
            failed_count += 1
            failed_downloads_details.append({
                'url': link_info.get('url', 'N/A'),
                'type': link_info.get('type', 'Unknown'),
                'base_name': base_name_for_file,
                'reason': "LOOP_PROCESSING_ERROR",
                'reason_detail': str(e_loop)
            })
            
            current_progress_val = min((i + 1) * progress_step, 1.0)
            progress_bar.progress(current_progress_val)
            status_text_area.text(f"Processing: {i+1}/{total_files_to_attempt} | Downloaded: {downloaded_successfully_count} | Failed: {failed_count}")
    
    return file_contents_for_zip, failed_downloads_details

def create_zip_in_memory(file_contents_dict):
    if not file_contents_dict:
        return None
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_name, content in file_contents_dict.items():
            zf.writestr(file_name, content)
    
    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def main():
    try:
        st.set_page_config(page_title="StockLib", page_icon="📚", layout="centered")
        
        if 'show_about' not in st.session_state:
            st.session_state.show_about = False
        
        _, col_about_btn = st.columns([0.85, 0.15])
        with col_about_btn:
            if st.button("About", type="secondary", use_container_width=True, help="Information about StockLib"):
                st.session_state.show_about = not st.session_state.show_about
        
        st.markdown("# StockLib 📚")
        st.markdown("**Your First Step in Fundamental Analysis – Your Business Data Library!**")
        
        if st.session_state.show_about:
            with st.expander("About StockLib", expanded=True):
                st.markdown("""
                **StockLib** is your comprehensive financial document retrieval tool designed to streamline fundamental analysis.
                
                **Features:**
                - 📄 **Annual Reports**: Download complete annual reports from BSE/NSE
                - 📝 **Concall Transcripts**: Access detailed earnings call transcripts
                - 📊 **Presentations**: Get investor presentations and earnings materials
                
                **How to Use:**
                1. Enter the stock ticker (BSE/NSE symbol) - e.g., 'RELIANCE', 'TCS', 'INFY'
                2. Select the document types you need
                3. Click 'Fetch Documents' to discover available files
                4. Download your selected documents as a convenient ZIP file
                
                **Data Source:** Documents are sourced from Screener.in, BSE, and NSE official repositories.
                
                **Note:** This tool is for educational purposes only and does not constitute financial advice.
                """)
        
        # Main input section
        stock_name = st.text_input(
            "Enter the stock name (BSE/NSE ticker):",
            placeholder="e.g., RELIANCE, TCS, INFY",
            help="Enter the exact ticker symbol as listed on BSE/NSE"
        ).strip().upper()
        
        st.markdown("### Select Document Types")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            annual_reports = st.checkbox("Annual Reports 📄", value=True)
        with col2:
            transcripts = st.checkbox("Concall Transcripts 📝", value=True)
        with col3:
            presentations = st.checkbox("Presentations 📊", value=True)
        
        doc_types = []
        if annual_reports:
            doc_types.append('Annual_Report')
        if transcripts:
            doc_types.append('Transcript')
        if presentations:
            doc_types.append('PPT')
        
        if st.button("🔍 Fetch Documents", type="primary", disabled=not stock_name or not doc_types):
            if not stock_name:
                st.error("Please enter a stock name.")
            elif not doc_types:
                st.error("Please select at least one document type.")
            else:
                with st.spinner("Fetching document list..."):
                    html_content = get_webpage_content(stock_name)
                    if html_content:
                        links = parse_html_content(html_content)
                        filtered_links = [link for link in links if link['type'] in doc_types]
                        
                        if filtered_links:
                            st.success(f"Found {len(links)} documents for '{stock_name}'. Preparing download...")
                            
                            progress_bar = st.progress(0)
                            status_text = st.empty()
                            
                            temp_folder = tempfile.mkdtemp()
                            
                            try:
                                file_contents, failed_downloads = download_selected_documents(
                                    links, temp_folder, doc_types, progress_bar, status_text
                                )
                                
                                progress_bar.progress(1.0)
                                
                                if file_contents:
                                    zip_data = create_zip_in_memory(file_contents)
                                    successful_count = len(file_contents)
                                    total_attempted = len(filtered_links)
                                    failed_count = len(failed_downloads)
                                    
                                    st.success(f"Download process complete. Successfully prepared {successful_count}/{total_attempted} documents for ZIP. ({failed_count} failed).")
                                    
                                    st.download_button(
                                        label=f"📥 Download {successful_count} Documents as ZIP ({stock_name})",
                                        data=zip_data,
                                        file_name=f"{stock_name}_documents_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                                        mime="application/zip",
                                        type="primary"
                                    )
                                
                                # Show failure details if any
                                if failed_downloads:
                                    st.markdown("### ⚠️ Download Failures Reported:")
                                    for failure in failed_downloads[:10]:  # Show first 10 failures
                                        reason_display = failure['reason'].replace('_', ' ').title()
                                        if "EXCEPTION" in failure['reason']:
                                            reason_display = f"Technical error: {failure.get('reason_detail', 'Unknown error')}"
                                        
                                        st.error(f"Could not download {failure['type']}: '{failure['base_name']}'. {reason_display}. Source: {failure['url']}")
                                    
                                    if len(failed_downloads) > 10:
                                        st.info(f"... and {len(failed_downloads) - 10} more failures.")
                                
                                else:
                                    st.error("No documents could be downloaded. Please try again or check if the stock ticker is correct.")
                            
                            finally:
                                if os.path.exists(temp_folder):
                                    shutil.rmtree(temp_folder, ignore_errors=True)
                        
                        else:
                            st.warning(f"No documents of the selected types found for '{stock_name}'.")
                    else:
                        st.error("Failed to retrieve document information. Please check the stock name and try again.")
        
        # Footer
        st.markdown("---")
        st.markdown("*StockLib is a tool for educational purposes only. Not financial advice.*")
    
    except Exception as e:
        st.error(f"Application error: {str(e)}")

if __name__ == "__main__":
    main()
