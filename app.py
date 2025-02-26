# Stock Transcript Downloader
# Author: Your Name
# -----------------------------------------

import os
import re
import time
import requests
import zipfile
import streamlit as st
from bs4 import BeautifulSoup
import html2text

def scrape_to_markdown(url):
    """Scrapes webpage and converts to markdown format."""
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://www.screener.in'
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            converter = html2text.HTML2Text()
            converter.ignore_links = False
            markdown_text = converter.handle(str(soup))
            return {"markdown": markdown_text}
        else:
            return {"error": f"Failed to retrieve page. Status code: {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}

def extract_document_links(markdown):
    """Extracts transcript, annual report, and PPT PDF links from markdown content with dates."""
    doc_types = {
        "transcripts": {"section": "### Concalls", "links": [], "dates": []},
        "annual_reports": {"section": "### Annual reports", "links": [], "dates": []},
        "ppts": {"section": "### PPTs", "links": [], "dates": []},
    }

    lines = markdown.split("\n")
    current_section = None

    for line in lines:
        for doc_type, details in doc_types.items():
            if details["section"] in line:
                current_section = doc_type
                continue

        if current_section and "http" in line:
            # Extract date from the line (usually in format like "31 Dec 2023" or "2023-24")
            date_match = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}|\d{4}-\d{2})', line)
            date = date_match.group(1) if date_match else "Date not found"
            
            url_match = re.search(r'https?://[^\s)]+\.pdf', line)
            if url_match:
                doc_types[current_section]["links"].append(url_match.group())
                doc_types[current_section]["dates"].append(date)

    return {key: {"links": value["links"], "dates": value["dates"]} for key, value in doc_types.items()}

def download_pdfs(document_data, stock_symbol):
    """Downloads PDFs for transcripts, annual reports, and PPTs into a zip file."""
    pdf_folder = "downloaded_documents"
    os.makedirs(pdf_folder, exist_ok=True)

    pdf_files = []
    total_files = sum(len(data["links"]) for data in document_data.values())
    downloaded_count = 0

    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.text(f"Downloading documents for {stock_symbol}...")

    # Create a temporary directory for downloads
    temp_dir = os.path.join(pdf_folder, stock_symbol)
    os.makedirs(temp_dir, exist_ok=True)

    for doc_type, data in document_data.items():
        for index, (url, date) in enumerate(zip(data["links"], data["dates"])):
            # Create a filename with date
            date_str = re.sub(r'[^\w\-]', '_', date)  # Clean date string for filename
            filename = os.path.join(temp_dir, f"{stock_symbol}_{doc_type}_{date_str}_{index+1}.pdf")

            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://nseindia.com" if "nseindia" in url else "https://bseindia.com"
            }

            try:
                response = requests.get(url, headers=headers, stream=True, timeout=50)
                if response.status_code == 200:
                    with open(filename, "wb") as file:
                        file.write(response.content)
                    pdf_files.append(filename)
                    downloaded_count += 1
                    progress_bar.progress(downloaded_count / total_files)
                else:
                    st.error(f"Failed to download {url}")
            except Exception as e:
                st.error(f"Error downloading {url}: {e}")

            time.sleep(0.5)

    if pdf_files:
        zip_filename = f"{stock_symbol}_documents.zip"
        zip_path = os.path.join(pdf_folder, zip_filename)
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for file in pdf_files:
                zipf.write(file, os.path.basename(file))
        
        # Read the ZIP file and create download button
        with open(zip_path, "rb") as f:
            st.download_button(
                label=f"⬇️ Download {stock_symbol} Documents (ZIP)",
                data=f,
                file_name=zip_filename,
                mime="application/zip"
            )
        
        status_text.text(f"✓ Downloaded {downloaded_count}/{total_files} PDFs.")
        
        # Clean up temporary files
        for file in pdf_files:
            os.remove(file)
        os.rmdir(temp_dir)
        
        return zip_filename
    return None

def main():
    # Configure the page
    st.set_page_config(
        page_title="Stock Document Downloader",
        page_icon="📊",
        layout="wide"
    )

    # Custom CSS for better styling
    st.markdown("""
        <style>
        .main {
            padding: 2rem;
        }
        .stButton>button {
            width: 100%;
            background-color: #FF4B4B;
            color: white;
        }
        .stButton>button:hover {
            background-color: #FF6B6B;
            color: white;
        }
        #GithubIcon {
            visibility: hidden;
        }
        </style>
    """, unsafe_allow_html=True)

    # Header section with logo and title
    col1, col2 = st.columns([1, 4])
    with col1:
        st.image("https://cdn-icons-png.flaticon.com/512/6941/6941697.png", width=100)
    with col2:
        st.title("Stock Document Downloader")
        st.markdown("##### Download transcripts, annual reports, and presentations for Indian stocks")

    # Main content in a card-like container
    with st.container():
        st.markdown("---")
        
        # Input section
        col1, col2 = st.columns([3, 1])
        with col1:
            stock_symbol = st.text_input(
                "Enter Stock Symbol",
                placeholder="e.g., HDFCBANK, INFY, RELIANCE",
                help="Enter the stock symbol as listed on NSE/BSE"
            ).strip().upper()
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)  # Add spacing
            search_button = st.button("🔍 Search Documents", use_container_width=True)
        if search_button and stock_symbol:
            try:
                with st.spinner(f"🔍 Searching for {stock_symbol}..."):
                    url = f'https://www.screener.in/company/{stock_symbol}/consolidated/#documents'
                    scrape_result = scrape_to_markdown(url)

                    markdown_content = scrape_result.get("markdown", "")
                    if not markdown_content:
                        st.error("⚠️ No data found!")
                        return

                    # Show document summary
                    doc_links = extract_document_links(markdown_content)
                    total_docs = sum(len(links) for links in doc_links.values())
                    
                    if total_docs > 0:
                        st.success(f"Found {total_docs} documents:")
                        for doc_type, data in doc_links.items():
                            if data["links"]:
                                st.markdown(f"#### {doc_type.replace('_', ' ').title()}")
                                for link, date in zip(data["links"], data["dates"]):
                                    filename = link.split('/')[-1][:30] + "..." if len(link.split('/')[-1]) > 30 else link.split('/')[-1]
                                    st.markdown(f"- [{filename}]({link}) ({date})")
                        
                        # Direct download without confirmation
                        zip_file = download_pdfs(doc_links, stock_symbol)
                        if not zip_file:
                            st.warning("No documents were downloaded.")
                    else:
                        st.warning("No documents found for this stock.")

            except Exception as e:
                st.error(f"❌ Error: {e}")
    # Footer
    st.markdown("---")
    st.markdown(
        """
        <div style='text-align: center'>
            <p style='color: #666666; font-size: 0.8em;'>
                Made with ❤️ for Indian Stock Market Investors
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )
if __name__ == "__main__":
    main()
