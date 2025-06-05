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
from webdriver_manager.chrome import ChromeDriverManager
import tempfile

def get_webpage_content(stock_name):
    url = f"https://www.screener.in/company/{stock_name}/consolidated/#documents"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        if "404" in str(e):
            st.error(f"Stock '{stock_name}' not found. Please check the ticker symbol.")
        elif "Connection" in str(e):
            st.error("Unable to connect. Please check your internet connection.")
        else:
            st.error(f"Error: Unable to fetch data for '{stock_name}'. Please try again later.")
        return None

def parse_html_content(html_content):
    if not html_content:
        return []
        
    soup = BeautifulSoup(html_content, 'html.parser')

    all_links = []
    
    # Annual Reports
    annual_reports = soup.select('.annual-reports ul.list-links li a')
    for link in annual_reports:
        year = re.search(r'Financial Year (\d{4})', link.text.strip())
        if year:
            all_links.append({'date': year.group(1), 'type': 'Annual_Report', 'url': link['href']})

    # Concall Transcripts and PPTs
    concall_items = soup.select('.concalls ul.list-links li')
    for item in concall_items:
        date_div = item.select_one('.ink-600.font-size-15')
        if date_div:
            date_text = date_div.text.strip()
            try:
                date_obj = datetime.strptime(date_text, '%b %Y')
                date = date_obj.strftime('%Y-%m')
            except:
                date = date_text
                
            for link in item.find_all('a', class_='concall-link'):
                if 'Transcript' in link.text:
                    all_links.append({'date': date, 'type': 'Transcript', 'url': link['href']})
                elif 'PPT' in link.text:
                    all_links.append({'date': date, 'type': 'PPT', 'url': link['href']})

    return sorted(all_links, key=lambda x: x['date'], reverse=True)

def format_filename(date_str, doc_type):
    # If date is just a year (e.g., "2023")
    if re.match(r'^\d{4}$', date_str):
        return f"{date_str}_{doc_type}.pdf"
    
    # If date is in YYYY-MM format
    if re.match(r'^\d{4}-\d{2}$', date_str):
        year, month = date_str.split('-')
        return f"{year}_{month}_{doc_type}.pdf"
    
    # If date is in DD/MM/YYYY format, convert to YYYY_MM_DD
    if re.match(r'^\d{2}/\d{2}/\d{4}$', date_str):
        day, month, year = date_str.split('/')
        return f"{year}_{month}_{day}_{doc_type}.pdf"
    
    # For any other format, just replace spaces and slashes with underscores
    clean_date = date_str.replace(' ', '_').replace('/', '_')
    return f"{clean_date}_{doc_type}.pdf"

# Add this function to handle Selenium-based downloads
def download_with_selenium(url, folder_path, file_name):
    driver = None # Initialize driver to None
    temp_dir = None # Initialize temp_dir to None
    try:
        # Set up Chrome options
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        
        # Add random user agent
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:90.0) Gecko/20100101 Firefox/90.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15"
        ]
        chrome_options.add_argument(f"--user-agent={random.choice(user_agents)}")
        
        # Set up download preferences
        temp_dir = tempfile.mkdtemp()
        prefs = {
            "download.default_directory": temp_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        # Fix the ChromeDriverManager issue - the error message suggests the issue is in install()
        # The current try/except handles the fallback, so we'll keep it.
        # The user might need to update webdriver-manager if this persists.
        try:
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=chrome_options
            )
        except Exception as driver_error:
            st.warning(f"Chrome driver error: {str(driver_error)}. Falling back to requests method.")
            # Indicate failure so download_pdf can use requests
            return None, None

        # First visit the main site to get cookies
        if "bseindia.com" in url:
            driver.get("https://www.bseindia.com/")
            time.sleep(3)  # Increased wait time
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            # Enhanced NSE handling - visit multiple pages
            driver.get("https://www.nseindia.com/")
            time.sleep(3)
            driver.get("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports")
            time.sleep(3)
            
        # Now navigate to the actual URL
        driver.get(url)
        
        # --- Download Detection Logic ---
        # Wait for a file to appear in the download directory for up to 30 seconds
        download_timeout = 30
        start_time = time.time()
        downloaded_file_path = None

        while time.time() - start_time < download_timeout:
            downloaded_files = os.listdir(temp_dir)
            # Look for files that are not partial downloads (e.g., .crdownload)
            completed_files = [f for f in downloaded_files if not f.endswith('.crdownload')]
            if completed_files:
                # Assuming the first completed file is the one we want
                downloaded_file_path = os.path.join(temp_dir, completed_files[0])
                break
            time.sleep(1) # Wait a bit before checking again

        # Check if a file was successfully downloaded
        if downloaded_file_path and os.path.exists(downloaded_file_path):
             # Read the content
            with open(downloaded_file_path, 'rb') as file:
                content = file.read()
            
            # Save to the target location
            final_file_path = os.path.join(folder_path, file_name)
            with open(final_file_path, 'wb') as file:
                file.write(content)
            
            # Clean up temp file
            os.remove(downloaded_file_path)
            
            # Check content size before returning
            if len(content) > 100: # Use the same size check as in download_selected_documents
                 return final_file_path, content
            else:
                 st.warning(f"Downloaded file {file_name} is too small ({len(content)} bytes). Treating as failed.")
                 return None, None # Treat as failed download

        # If no file was downloaded to temp directory, try getting content via base64 embed (less reliable)
        try:
            # Check if we're on a PDF page and try to get base64 content
            if "application/pdf" in driver.page_source or driver.current_url.lower().endswith(".pdf"):
                 pdf_content_base64 = driver.execute_script("return document.querySelector('embed').src")
                 if pdf_content_base64 and pdf_content_base64.startswith("data:application/pdf;base64,"):
                     pdf_data = pdf_content_base64.split(",")[1]
                     content = base64.b64decode(pdf_data)
                     
                     # Save the file
                     final_file_path = os.path.join(folder_path, file_name)
                     with open(final_file_path, 'wb') as file:
                         file.write(content)
                     
                     # Check content size before returning
                     if len(content) > 100:
                         return final_file_path, content
                     else:
                         st.warning(f"Extracted base64 content for {file_name} is too small ({len(content)} bytes). Treating as failed.")
                         return None, None
                 else:
                     st.warning(f"Could not extract base64 PDF content for {url}.")
        except Exception as base64_error:
            st.warning(f"Error extracting base64 content for {url}: {str(base64_error)}")
            pass # Continue if base64 extraction fails

        # If neither download nor base64 extraction worked, try getting content via requests with Selenium cookies
        try:
            cookies = driver.get_cookies()
            
            # Convert Selenium cookies to requests format
            cookies_dict = {cookie['name']: cookie['value'] for cookie in cookies}
            
            # Make request with the cookies
            headers = {
                "User-Agent": random.choice(user_agents),
                "Accept": "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8", # Adjust accept header
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": driver.current_url, # Use the current URL as referrer
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin" if "bseindia.com" in url or "nseindia.com" in url else "none"
            }
            
            response = requests.get(url, headers=headers, cookies=cookies_dict, stream=True, timeout=50)
            response.raise_for_status()
            
            final_file_path = os.path.join(folder_path, file_name)
            content = response.content
            
            # Check if content looks like an error page (e.g., HTML instead of PDF)
            if content.strip().startswith(b'<!DOCTYPE html') or content.strip().startswith(b'<html'):
                 st.warning(f"Downloaded content via requests+cookies for {url} appears to be an HTML page, not a PDF.")
                 return None, None # Treat as failed download

            with open(final_file_path, 'wb') as file:
                file.write(content)
            
            # Check content size before returning
            if len(content) > 100:
                 return final_file_path, content
            else:
                 st.warning(f"Downloaded content via requests+cookies for {file_name} is too small ({len(content)} bytes). Treating as failed.")
                 return None, None

        except Exception as requests_cookie_error:
            st.error(f"Requests+cookies download error for {url}: {str(requests_cookie_error)}")
            return None, None # Indicate failure

        # If none of the methods worked
        st.error(f"Failed to download {url} using all methods.")
        return None, None
        
    except Exception as e:
        st.error(f"Selenium download error for {url}: {str(e)}")
        return None, None # Indicate failure
    finally:
        # Ensure driver is quit and temp directory is cleaned up
        if driver:
            driver.quit()
        if temp_dir and os.path.exists(temp_dir):
             try:
                 # Clean up any remaining files in the temp directory
                 for f in os.listdir(temp_dir):
                     os.remove(os.path.join(temp_dir, f))
                 os.rmdir(temp_dir)
             except Exception as cleanup_error:
                 st.warning(f"Error cleaning up temp directory {temp_dir}: {cleanup_error}")

# Add this new function for requests-based downloads
def download_with_requests(url, folder_path, file_name):
    try:
        # More comprehensive browser-like headers
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document", # Add more sec-fetch headers
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none", # Start with none, might change later
            "Sec-Fetch-User": "?1"
        }
        
        # Create a session to maintain cookies
        session = requests.Session()
        
        # Special handling for BSE India URLs
        if "bseindia.com" in url:
            # Visit the BSE homepage first to get cookies
            session.get("https://www.bseindia.com/", headers=headers)
            headers["Referer"] = "https://www.bseindia.com/" # Set referrer after visiting homepage
            headers["Sec-Fetch-Site"] = "same-origin" # Change sec-fetch-site
            
            # If it's an AnnPdfOpen URL, we need to handle it differently
            if "AnnPdfOpen.aspx" in url:
                # Extract the Pname parameter
                pname = re.search(r'Pname=([^&]+)', url)
                if pname:
                    pname_value = pname.group(1)
                    
                    # Construct a different URL that might work better
                    alt_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pname_value}"
                    
                    # Try the alternative URL
                    try:
                        # Use headers with referrer pointing to stock page
                        alt_headers = headers.copy()
                        alt_headers["Referer"] = "https://www.bseindia.com/stock-share-price/"
                        response = session.get(alt_url, headers=alt_headers, stream=True, timeout=50)
                        response.raise_for_status()
                    except:
                        # If that fails, try the original with modified headers
                        # Use headers with referrer pointing to stock page
                        original_headers = headers.copy()
                        original_headers["Referer"] = "https://www.bseindia.com/stock-share-price/"
                        response = session.get(url, headers=original_headers, stream=True, timeout=50)
                        response.raise_for_status()
                else:
                    # If we can't extract Pname, try the original URL with modified headers
                    original_headers = headers.copy()
                    original_headers["Referer"] = "https://www.bseindia.com/stock-share-price/"
                    response = session.get(url, headers=original_headers, stream=True, timeout=50)
                    response.raise_for_status()
            else:
                # For other BSE URLs, use headers with referrer pointing to homepage
                response = session.get(url, headers=headers, stream=True, timeout=50)
                response.raise_for_status()
        elif "nseindia.com" in url or "archives.nseindia.com" in url:
            # Enhanced NSE handling - visit multiple NSE pages to get proper cookies
            headers["Referer"] = "https://www.nseindia.com/" # Set initial referrer
            headers["Sec-Fetch-Site"] = "same-origin" # Change sec-fetch-site
            
            # Visit multiple NSE pages to get proper cookies
            session.get("https://www.nseindia.com/", headers=headers)
            time.sleep(1)
            # Update referrer for the next visit
            headers["Referer"] = "https://www.nseindia.com/"
            session.get("https://www.nseindia.com/companies-listing/corporate-filings-annual-reports", headers=headers)
            time.sleep(1)
            
            # Now try the actual download URL
            # Use headers with referrer pointing to the filings page
            download_headers = headers.copy()
            download_headers["Referer"] = "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
            download_headers["Accept"] = "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8" # Adjust accept header for PDF
            download_headers["Sec-Fetch-Dest"] = "document" # Should be document for PDF
            
            response = session.get(url, headers=download_headers, stream=True, timeout=50)
            response.raise_for_status()
        else:
            # For non-BSE/NSE URLs
            response = session.get(url, headers=headers, stream=True, timeout=50)
            response.raise_for_status()

        file_path = os.path.join(folder_path, file_name)
        content = response.content  # Store content before writing to file
        
        # Check if content looks like an error page (e.g., HTML instead of PDF)
        # Simple check: if it starts with '<!DOCTYPE html' or '<html'
        if content.strip().startswith(b'<!DOCTYPE html') or content.strip().startswith(b'<html'):
             st.warning(f"Downloaded content for {url} appears to be an HTML page, not a PDF.")
             return None, None # Treat as failed download

        with open(file_path, 'wb') as file:
            file.write(content)

        return file_path, content
    except requests.exceptions.RequestException as e:
        st.error(f"Error downloading {url}: {e}")
        return None, None

def download_pdf(url, folder_path, file_name):
    # For BSE and NSE URLs, try requests first (more reliable than Selenium in this case)
    if "bseindia.com" in url or "nseindia.com" in url or "archives.nseindia.com" in url:
        # Try regular requests method
        result = download_with_requests(url, folder_path, file_name)
        if result and result[0]:  # If successful (check result is not None and path exists)
            return result
        # If requests failed, try Selenium as fallback
        st.info(f"Requests failed for {url}. Trying Selenium.") # Add info message for fallback
        return download_with_selenium(url, folder_path, file_name)
    
    # For other URLs, use the regular requests approach
    return download_with_requests(url, folder_path, file_name)


def main():
    try:
        st.set_page_config(page_title="StockLib", page_icon="📚")
        
        # Initialize session state for About modal
        if 'show_about' not in st.session_state:
            st.session_state.show_about = False
        
        # Add About button and modal
        with st.container():
            # Create a right-aligned container for buttons
            _, right_col = st.columns([3, 1])  # Adjusted ratio to give more space for buttons
            with right_col:
                if st.button("About", type="secondary", use_container_width=True):
                    st.session_state.show_about = True
            
            if st.session_state.show_about:
                # Use Streamlit's native components instead of custom HTML/CSS
                st.subheader("About StockLib 📚")
                st.caption("StockLib + NotebookLLM = Your AI-Powered Business Analyst")
                
                with st.expander("Quick Guide", expanded=True):
                    st.markdown("""
                    1. Enter stock name (Example: TATAMOTORS, HDFCBANK)
                    2. Select documents you want to download
                    3. Avoid the hassle of downloading documents one by one from screener
                    4. Get your ZIP file with all documents in single click
                    5. Upload these docs to NotebookLLM easily
                    6. Ask questions like:
                       - "What's the company's business model?"
                       - "Explain their growth strategy"
                       - "What are their key products?"
                    7. Get instant insights from years of business data! 🚀
                    """)
                
                st.caption("Note: All documents belong to BSE/NSE/respective companies and are fetched from screener.in")
                
                # Standard Streamlit close button
                if st.button("Close", key="close_about_button"):
                    st.session_state.show_about = False
                    st.rerun()
                    
        # Improved header styling
        st.markdown("""
            <h1 style='text-align: center;'>StockLib 📚</h1>
            <h4 style='text-align: center; color: #666666;'>Your First Step in Fundamental Analysis – Your Business Data Library!</h4>
            <hr>
        """, unsafe_allow_html=True)
        
        # Create a container for the main content
        main_container = st.container()
        
        # Create a container for the footer
        footer_container = st.container()
        
        with main_container:
            # Add form with improved styling
            with st.form(key='stock_form'):
                stock_name = st.text_input("Enter the stock name (BSE/NSE ticker):", placeholder="Example: TATAMOTORS")
                
                st.markdown("### Select Document Types")
                col1, col2, col3 = st.columns(3)
                with col1:
                    annual_reports = st.checkbox("Annual Reports 📄", value=True)
                with col2:
                    transcripts = st.checkbox("Concall Transcripts 📝", value=True)
                with col3:
                    ppts = st.checkbox("Presentations 📊", value=True)
                
                submit_button = st.form_submit_button(label="🔍 Fetch Documents")
        
            # Process form submission
            if submit_button and stock_name:
                doc_types = []
                if annual_reports:
                    doc_types.append("Annual_Report")
                if transcripts:
                    doc_types.append("Transcript")
                if ppts:
                    doc_types.append("PPT")
                
                if not doc_types:
                    st.warning("No document types selected.")
                    return
                
                with st.spinner("🔍 Searching for documents..."):
                    html_content = get_webpage_content(stock_name)
                    
                    if not html_content:
                        return
                    
                    links = parse_html_content(html_content)
                    
                    if not links:
                        st.warning(f"No documents found for {stock_name}.")
                        return
                    
                    # Filter links by selected document types
                    filtered_links = [link for link in links if link['type'] in doc_types]
                    
                    if not filtered_links:
                        st.warning(f"No {', '.join(doc_types)} found for {stock_name}.")
                        return
                    
                    # Display document count
                    st.success(f"Found {len(filtered_links)} documents for {stock_name}!")
                    
                    # Create a temporary directory for downloads
                    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_downloads")
                    os.makedirs(temp_dir, exist_ok=True)
                    
                    # Create progress bar and status text
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    # Download documents
                    successful_downloads, file_contents = download_selected_documents(
                        filtered_links, temp_dir, doc_types, progress_bar, status_text
                    )
                    
                    # Update status
                    if successful_downloads:
                        actual_count = len(successful_downloads)
                        total_count = len(filtered_links)  # Define total_count here
                        status_text.text(f"Successfully downloaded {actual_count}/{total_count} documents!")
                        
                        # Create ZIP file
                        zip_data = create_zip_in_memory(file_contents)
                        
                        # Offer download button with accurate count
                        st.download_button(
                            label=f"📥 Download {actual_count} Documents (ZIP)",
                            data=zip_data,
                            file_name=f"{stock_name}_documents.zip",
                            mime="application/zip",
                            use_container_width=True
                        )
                        
                        # Show warning if some downloads failed
                        if actual_count < total_count:
                            st.warning(f"{total_count - actual_count} documents could not be downloaded due to access restrictions or other errors.")
                    else:
                        status_text.text("No documents were successfully downloaded.")
        
        with footer_container:
            st.markdown("""
                <hr>
                <p style='text-align: center; color: #888888; font-size: 0.8em;'>
                    StockLib is a tool for educational purposes only. Not financial advice.
                </p>
            """, unsafe_allow_html=True)
            
    except Exception as e:
        st.error(f"Application error: {str(e)}")
        st.info("Please try refreshing the page. If the problem persists, contact support.")

# Add these missing functions
def download_selected_documents(links, output_folder, doc_types, progress_bar, status_text):
    os.makedirs(output_folder, exist_ok=True)
    successful_downloads = []
    file_contents = {}
    
    # Only count links that match the selected document types
    filtered_links = [link for link in links if link['type'] in doc_types]
    total_files = len(filtered_links)
    
    progress_step = 1.0 / total_files if total_files > 0 else 0
    current_progress = 0.0
    downloaded_count = 0
    failed_count = 0
    
    for i, link in enumerate(filtered_links):
        try:
            file_name = format_filename(link['date'], link['type'])
            
            # Check if this filename already exists in our collection (avoid duplicates)
            if file_name in file_contents:
                # Modify filename to make it unique
                base_name, ext = os.path.splitext(file_name)
                file_name = f"{base_name}_{i}{ext}"
                
            file_path, content = download_pdf(link['url'], output_folder, file_name)
            
            if file_path and content and len(content) > 100:  # Ensure content is substantial
                successful_downloads.append(file_path)
                file_contents[file_name] = content
                downloaded_count += 1
            else:
                failed_count += 1
                st.warning(f"Failed to download {link['date']}_{link['type']}")
            
            current_progress += progress_step
            progress_bar.progress(min(current_progress, 1.0))
            status_text.text(f"Downloading: {downloaded_count}/{total_files} documents (Failed: {failed_count})")
            time.sleep(1)
        except Exception as e:
            failed_count += 1
            st.warning(f"Skipped {link['date']}_{link['type']}: {str(e)}")
            current_progress += progress_step
            progress_bar.progress(min(current_progress, 1.0))
            continue

    return successful_downloads, file_contents

def create_zip_in_memory(file_contents):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_name, content in file_contents.items():
            zipf.writestr(file_name, content)
    
    zip_buffer.seek(0)
    return zip_buffer.getvalue()  # Return the bytes directly

# Add this at the end of the file to run the app
if __name__ == "__main__":
    main()
