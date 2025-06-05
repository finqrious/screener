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

def download_pdf(url, folder_path, file_name):
    try:
        # More comprehensive browser-like headers
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.bseindia.com/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0"
        }
        
        # Create a session to maintain cookies
        session = requests.Session()
        
        # Special handling for BSE India URLs
        if "bseindia.com" in url:
            # Visit the BSE homepage first to get cookies
            session.get("https://www.bseindia.com/", headers=headers)            
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
                        response = session.get(alt_url, headers=headers, stream=True, timeout=50)
                        response.raise_for_status()
                    except:
                        # If that fails, try the original with modified headers
                        headers["Referer"] = "https://www.bseindia.com/stock-share-price/"
                        response = session.get(url, headers=headers, stream=True, timeout=50)
                        response.raise_for_status()
                else:
                    # If we can't extract Pname, try the original URL
                    response = session.get(url, headers=headers, stream=True, timeout=50)
                    response.raise_for_status()
            else:
                # For other BSE URLs
                response = session.get(url, headers=headers, stream=True, timeout=50)
                response.raise_for_status()
        else:
            # For non-BSE URLs
            response = session.get(url, headers=headers, stream=True, timeout=50)
            response.raise_for_status()

        file_path = os.path.join(folder_path, file_name)
        content = response.content  # Store content before writing to file
        
        with open(file_path, 'wb') as file:
            file.write(content)

        return file_path, content
    except requests.exceptions.RequestException as e:
        st.error(f"Error downloading {url}: {e}")
        return None, None

def download_selected_documents(links, output_folder, doc_types, progress_bar, status_text):
    os.makedirs(output_folder, exist_ok=True)
    successful_downloads = []
    file_contents = {}
    
    total_files = sum(1 for link in links if link['type'] in doc_types)
    progress_step = 1.0 / total_files if total_files > 0 else 0
    current_progress = 0.0
    downloaded_count = 0
    
    for link in links:
        if link['type'] in doc_types:
            try:
                file_name = format_filename(link['date'], link['type'])
                file_path, content = download_pdf(link['url'], output_folder, file_name)
                if file_path:
                    successful_downloads.append(file_path)
                    file_contents[file_name] = content
                    downloaded_count += 1
                current_progress += progress_step
                progress_bar.progress(min(current_progress, 1.0))
                status_text.text(f"Downloading: {downloaded_count}/{total_files} documents")
                time.sleep(1)
            except Exception as e:
                st.warning(f"Skipped {link['date']}_{link['type']}: {str(e)}")
                continue

    return successful_downloads, file_contents

def create_zip_in_memory(file_contents):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_name, content in file_contents.items():
            zipf.writestr(file_name, content)
    
    zip_buffer.seek(0)
    return zip_buffer.getvalue()  # Return the bytes directly
def main():
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
                    st.error("Failed to fetch webpage content. Please check the stock ticker and try again.")
                    return
                
                try:
                    links = parse_html_content(html_content)
                    if not links:
                        st.warning("📭 No documents found for this stock.")
                        return
                    
                    filtered_links = [link for link in links if link['type'] in doc_types]
                    if not filtered_links:
                        st.warning("📭 No documents found for the selected types.")
                        return
                    
                    # Create containers for different states
                    progress_container = st.container()
                    download_container = st.container()
                    
                    with progress_container:
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                    pdf_folder = f"{stock_name}_documents"
                    downloaded_files, file_contents = download_selected_documents(
                        filtered_links, pdf_folder, doc_types, progress_bar, status_text
                    )
                    # Clear the searching spinner
                    st.spinner(None)
                    # Add Twitter contact link at the bottom
                    st.markdown("<br><br><hr>", unsafe_allow_html=True)
                    st.markdown(
                        '<div style="text-align: center; padding: 10px; color: #666666;">'
                        'For any query please contact: '
                        '<a href="https://x.com/PatilInvests" target="_blank">@patilinvests</a>'
                        '</div>',
                        unsafe_allow_html=True
                    )
                    if downloaded_files:
                        progress_bar.progress(1.0)
                        status_text.success(f"✅ Downloaded {len(downloaded_files)} out of {len(filtered_links)} documents")
                        
                        with download_container:
                            zip_data = create_zip_in_memory(file_contents)
                            st.download_button(
                                label="📦 Download All Documents as ZIP",
                                data=zip_data,
                                file_name=f"{stock_name}_documents.zip",
                                mime="application/zip",
                                key="download_button"
                            )
                    else:
                        st.error("❌ No files could be downloaded.")
                        
                except Exception as e:
                    st.error(f"❌ Error: {e}")
    # Add Twitter contact link in the footer container
    with footer_container:
        st.markdown("""
            <div style="position: fixed; bottom: 0; left: 0; right: 0; background-color: white; padding: 10px; border-top: 1px solid #e5e5e5;">
                <div style="text-align: center; color: #666666;">
                    For any query please contact: 
                    <a href="https://x.com/PatilInvests" target="_blank" style="color: #1DA1F2; text-decoration: none;">@patilinvests</a>
                </div>
            </div>
        """, unsafe_allow_html=True)
if __name__ == "__main__":
    main()
