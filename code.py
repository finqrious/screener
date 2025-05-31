import requests
from bs4 import BeautifulSoup
import re
import streamlit as st
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import numpy as np
from datetime import datetime
import os
import zipfile
import time
import urllib.parse # For urljoin
import io
import random # For randomized sleep

# --- Global Configuration & Session for Screener.in ---
BASE_URL_SCREENER = "https://www.screener.in"
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1", # Do Not Track
    "Upgrade-Insecure-Requests": "1"
}

screener_session = requests.Session()
screener_session.headers.update(COMMON_HEADERS)
# --- End Global Configuration ---

def get_webpage_content(stock_name):
    url = f"{BASE_URL_SCREENER}/company/{stock_name}/consolidated/#documents"
    try:
        response = screener_session.get(url, timeout=20) # Increased timeout
        response.raise_for_status()  # Will raise HTTPError for 4xx/5xx
        return response.text
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            st.error(f"Stock '{stock_name}' not found on Screener.in (404 error). Please check the ticker symbol or if the company is listed on Screener.in.")
        elif e.response.status_code == 403:
            st.error(f"Access Forbidden (403 error) when trying to access Screener.in for '{stock_name}'. The website might be blocking automated access. Try again later.")
        else:
            st.error(f"HTTP error {e.response.status_code} when accessing Screener.in for '{stock_name}': {e}")
        return None
    except requests.exceptions.ConnectionError as e:
        st.error(f"Connection error for Screener.in: Unable to connect. Please check your internet connection. Details: {e}")
        return None
    except requests.exceptions.Timeout as e:
        st.error(f"Request timed out for Screener.in. The server might be slow or your connection unstable. Details: {e}")
        return None
    except requests.exceptions.RequestException as e: # Catch-all for other requests errors
        st.error(f"Error fetching data for '{stock_name}' from Screener.in: {e}")
        return None

def parse_html_content(html_content):
    if not html_content:
        return []
        
    soup = BeautifulSoup(html_content, 'html.parser')
    all_links = []
    
    # Annual Reports
    annual_reports_section = soup.find('div', id='documents-annual-reports')
    if annual_reports_section:
        annual_report_links = annual_reports_section.select('ul.list-links li a')
        for link_tag in annual_report_links:
            year_match = re.search(r'Financial Year (\d{4})', link_tag.text.strip())
            if year_match and link_tag.has_attr('href'):
                absolute_url = urllib.parse.urljoin(BASE_URL_SCREENER, link_tag['href'])
                all_links.append({'date': year_match.group(1), 'type': 'Annual_Report', 'url': absolute_url})

    # Concall Transcripts and PPTs
    concall_section = soup.find('div', id='documents-concall-transcripts-and-presentations')
    if concall_section:
        concall_items = concall_section.select('ul.list-links li')
        for item in concall_items:
            date_div = item.select_one('.ink-600.font-size-15')
            if date_div:
                date_text = date_div.text.strip()
                try:
                    date_obj = datetime.strptime(date_text, '%b %Y') # Example: "Mar 2023"
                    parsed_date = date_obj.strftime('%Y-%m')
                except ValueError:
                    try:
                        date_obj = datetime.strptime(date_text, '%d %b %Y') # Example: "23 Mar 2023"
                        parsed_date = date_obj.strftime('%Y-%m-%d')
                    except ValueError:
                        parsed_date = date_text.replace(" ", "_") # Fallback
                    
                for link_tag in item.find_all('a', class_='concall-link'):
                    if link_tag.has_attr('href'):
                        absolute_url = urllib.parse.urljoin(BASE_URL_SCREENER, link_tag['href'])
                        doc_text_lower = link_tag.text.lower()
                        if 'transcript' in doc_text_lower:
                            all_links.append({'date': parsed_date, 'type': 'Transcript', 'url': absolute_url})
                        elif 'ppt' in doc_text_lower or 'presentation' in doc_text_lower:
                            all_links.append({'date': parsed_date, 'type': 'PPT', 'url': absolute_url})
    
    return sorted(all_links, key=lambda x: x['date'], reverse=True)


def format_filename(date_str, doc_type):
    # If date is just a year (e.g., "2023")
    if re.match(r'^\d{4}$', date_str):
        return f"{date_str}_{doc_type}.pdf"
    
    # If date is in YYYY-MM format
    if re.match(r'^\d{4}-\d{2}$', date_str):
        year, month = date_str.split('-')
        return f"{year}_{month}_{doc_type}.pdf"

    # If date is in YYYY-MM-DD format
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        year, month, day = date_str.split('-')
        return f"{year}_{month}_{day}_{doc_type}.pdf"
    
    # If date is in DD/MM/YYYY format, convert to YYYY_MM_DD
    if re.match(r'^\d{2}/\d{2}/\d{4}$', date_str):
        day, month, year = date_str.split('/')
        return f"{year}_{month}_{day}_{doc_type}.pdf"
    
    # For any other format, just replace spaces and slashes with underscores
    clean_date = re.sub(r'[^\w\-]', '_', date_str) # Replace non-alphanumeric (except hyphen) with underscore
    return f"{clean_date}_{doc_type}.pdf"


def download_pdf(url, folder_path, file_name, stock_page_url):
    try:
        # Create specific headers for download, including Referer
        download_headers = COMMON_HEADERS.copy()
        download_headers["Referer"] = stock_page_url # Key change for download authorization

        response = screener_session.get(url, headers=download_headers, stream=True, timeout=90) # Increased timeout for downloads
        response.raise_for_status()

        file_path = os.path.join(folder_path, file_name)
        content = response.content
        
        with open(file_path, 'wb') as file:
            file.write(content)

        return file_path, content
    except requests.exceptions.HTTPError as e:
        st.warning(f"HTTP error {e.response.status_code} downloading {file_name} from {url}. Referer: {stock_page_url}. Error: {e}")
        return None, None
    except requests.exceptions.ConnectionError as e:
        st.warning(f"Connection error downloading {file_name} from {url}. Error: {e}")
        return None, None
    except requests.exceptions.Timeout as e:
        st.warning(f"Timeout downloading {file_name} from {url}. Error: {e}")
        return None, None
    except requests.exceptions.RequestException as e:
        st.warning(f"General error downloading {file_name} from {url}. Error: {e}")
        return None, None


def download_selected_documents(stock_name, links, output_folder, doc_types, progress_bar, status_text):
    os.makedirs(output_folder, exist_ok=True)
    successful_downloads = []
    file_contents = {}
    
    # Filter links first based on selected doc_types to get an accurate total
    links_to_download = [link for link in links if link['type'] in doc_types]
    total_files = len(links_to_download)

    if total_files == 0:
        status_text.info("No documents found matching your selection.")
        progress_bar.progress(0)
        return [], {}

    progress_step = 1.0 / total_files
    current_progress = 0.0
    downloaded_count = 0
    
    # The referer URL is the page from which the document links were obtained
    stock_page_url = f"{BASE_URL_SCREENER}/company/{stock_name}/consolidated/"

    for link_item in links_to_download: # Use the filtered list
        try:
            file_name = format_filename(link_item['date'], link_item['type'])
            status_text.text(f"Downloading: {file_name} ({downloaded_count + 1}/{total_files})")
            
            file_path, content = download_pdf(link_item['url'], output_folder, file_name, stock_page_url)
            
            if file_path and content:
                successful_downloads.append(file_path)
                file_contents[file_name] = content
                downloaded_count += 1
            else:
                st.warning(f"Failed to download {file_name}. Check logs for details.")

            current_progress += progress_step
            progress_bar.progress(min(current_progress, 1.0))
            
            time.sleep(random.uniform(1.0, 2.5)) # Polite delay, slightly randomized
        except Exception as e:
            st.warning(f"Skipped processing for document {link_item.get('type', 'Unknown')} ({link_item.get('date', 'Unknown')}): {str(e)}")
            # To ensure progress bar still updates if an unexpected error occurs before download_pdf is called
            current_progress += progress_step 
            progress_bar.progress(min(current_progress, 1.0))
            continue

    status_text.text(f"Download process completed. {downloaded_count}/{total_files} documents downloaded.")
    return successful_downloads, file_contents


def create_zip_in_memory(file_contents):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_name, content in file_contents.items():
            zipf.writestr(file_name, content)
    
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


@st.cache_data(ttl=60) # Cache for 1 minute
def get_ticker_suggestions(query):
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(query)}" # URL encode query
    # Yahoo Finance typically uses a different User-Agent than browsers, but COMMON_HEADERS should be fine.
    # yfinance itself handles its session and headers.
    try:
        response = requests.get(url, headers=COMMON_HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()
        suggestions = []
        for item in data.get("quotes", []):
            symbol = item.get("symbol")
            name = item.get("shortname", item.get("longname", "Unknown Name"))
            if symbol and (symbol.endswith((".NS", ".BO")) or symbol.startswith("^")): # Check for Indian exchanges or indices
                suggestions.append((symbol, name))
        return suggestions
    except requests.exceptions.HTTPError as e:
        st.error(f"HTTP error {e.response.status_code} fetching suggestions from Yahoo Finance for '{query}': {e}")
        return []
    except requests.exceptions.ConnectionError as e:
        st.error(f"Connection error fetching suggestions from Yahoo Finance: {e}")
        return []
    except requests.exceptions.Timeout as e:
        st.error(f"Timeout fetching suggestions from Yahoo Finance: {e}")
        return []
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching suggestions from Yahoo Finance: {e}")
        return []
    except ValueError as e: # For JSON decoding errors
        st.error(f"Error parsing suggestions data from Yahoo Finance: {e}")
        return []

def init_session_state():
    if 'selected_ticker' not in st.session_state:
        st.session_state.selected_ticker = ""
    if 'selected_name' not in st.session_state:
        st.session_state.selected_name = ""
    if 'search_query' not in st.session_state:
        st.session_state.search_query = ""
    if 'suggestions' not in st.session_state:
        st.session_state.suggestions = []
    if 'last_search_time' not in st.session_state:
        st.session_state.last_search_time = 0
    if 'analyze_flag' not in st.session_state:
        st.session_state.analyze_flag = False
    if 'show_about' not in st.session_state: # For About modal
        st.session_state.show_about = False
    if 'active_tab' not in st.session_state: # For tab navigation
        st.session_state.active_tab = "documents"


def on_search_change():
    query = st.session_state.search_query
    current_time = time.time()
    
    if current_time - st.session_state.last_search_time > 0.3 and len(query) >= 2: # Debounce
        st.session_state.suggestions = get_ticker_suggestions(query)
        st.session_state.last_search_time = current_time
    elif len(query) < 2:
        st.session_state.suggestions = []


def select_stock(ticker, name):
    st.session_state.selected_ticker = ticker
    st.session_state.selected_name = name
    st.session_state.suggestions = [] # Clear suggestions
    st.session_state.analyze_flag = True # Signal to analyze
    st.session_state.active_tab = "analysis" # Switch tab
    st.rerun() # Rerun to reflect tab change and trigger analysis


def analyze_stock(ticker_symbol): # Renamed from 'ticker' to 'ticker_symbol' for clarity
    st.write(f"**Analyzing Ticker:** {ticker_symbol} ({st.session_state.get('selected_name', '')})")
    
    try:
        with st.spinner(f"Downloading historical data for {ticker_symbol}..."):
            # yfinance handles its own session and headers.
            # If issues arise, one might need to pass a session:
            # yf_session = requests.Session()
            # yf_session.headers.update(COMMON_HEADERS)
            # stock_data = yf.download(ticker_symbol, period="max", session=yf_session)
            stock_data = yf.download(ticker_symbol, period="max", progress=False) # progress=False for cleaner UI
        
        if stock_data.empty:
            st.error(f"Error: No data available for '{ticker_symbol}'. It might be delisted, a new listing, or an invalid ticker for Yahoo Finance.")
            return
        
        st.success(f"Downloaded {len(stock_data)} rows of historical data for {ticker_symbol}.")
        
        df = stock_data[['Close']].copy()
        close_series = df['Close'].squeeze() # Ensure it's a Series
        df['ATH'] = close_series.cummax()
        df['Drawdown'] = (close_series - df['ATH']) / df['ATH']
        
        threshold = -0.25
        df['In_Drawdown'] = df['Drawdown'] <= threshold
        
        drawdown_periods = []
        in_drawdown_flag = False # Renamed from 'in_drawdown' to avoid conflict
        start_date = None
        
        for date_idx, row_data in df.iterrows():
            is_in_drawdown_now = row_data['In_Drawdown'] # Directly access boolean
            
            if is_in_drawdown_now and not in_drawdown_flag:
                in_drawdown_flag = True
                start_date = date_idx
            elif not is_in_drawdown_now and in_drawdown_flag:
                in_drawdown_flag = False
                if start_date: drawdown_periods.append((start_date, date_idx))
                start_date = None
        
        if in_drawdown_flag and start_date:
            drawdown_periods.append((start_date, df.index[-1]))
        
        # --- Plotting ---
        st.markdown("### Price and All-Time High")
        fig1, ax1 = plt.subplots(figsize=(12, 6))
        ax1.plot(df.index, df['Close'], label='Close Price', color='blue', linewidth=1.5)
        ax1.plot(df.index, df['ATH'], label='All-Time High', color='green', linestyle='--', linewidth=1)
        
        for start, end in drawdown_periods:
            ax1.axvspan(start, end, alpha=0.2, color='red', label='_nolegend_') # Avoid multiple legend entries
        if drawdown_periods: # Add legend for drawdown only if present
             ax1.axvspan(pd.NaT, pd.NaT, alpha=0.2, color='red', label=f'> {abs(threshold*100)}% Drawdown')


        ax1.set_title(f"{ticker_symbol} Price and All-Time High", fontsize=16)
        ax1.set_xlabel("Date", fontsize=12)
        ax1.set_ylabel("Price", fontsize=12)
        ax1.legend()
        ax1.grid(True, linestyle=':', alpha=0.7)
        plt.tight_layout()
        st.pyplot(fig1, use_container_width=True)
        
        st.markdown("### Drawdown History")
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=df.index, y=df['Drawdown'] * 100, mode='lines', name='Drawdown (%)', 
            line=dict(color='rgba(255,0,0,0.7)', width=1.5)
        ))
        fig2.add_shape(
            type="line", x0=df.index[0], y0=threshold * 100, x1=df.index[-1], y1=threshold * 100,
            line=dict(color="red", width=1, dash="dash"), name=f"Threshold ({threshold*100}%)"
        )
        fig2.update_layout(
            title=f"{ticker_symbol} Drawdown Percentage", xaxis_title="Date", yaxis_title="Drawdown (%)",
            template="plotly_white", height=450, margin=dict(l=50, r=20, t=50, b=50)
        )
        st.plotly_chart(fig2, use_container_width=True)
        
        # --- ATH Recovery Table ---
        st.markdown("### Time to Reach New All-Time Highs")
        ath_data = []
        prev_ath_val = 0
        
        # Ensure ATH is numeric if coming from a single-column DataFrame selection
        df_ath_series = df['ATH'].squeeze()

        for date_idx, current_ath_val in df_ath_series.items():
            if current_ath_val > prev_ath_val:
                ath_data.append({'date': date_idx, 'value': current_ath_val})
                prev_ath_val = current_ath_val
        
        ath_table_data = []
        if len(ath_data) > 1:
            for i in range(len(ath_data) - 1):
                current_ath_event = ath_data[i]
                next_ath_event = ath_data[i+1]
                days_between = (next_ath_event['date'] - current_ath_event['date']).days
                
                if days_between > 30: # Only show significant recovery periods
                    ath_table_data.append({
                        "Previous ATH Date": current_ath_event['date'].strftime('%Y-%m-%d'),
                        "Previous ATH Value": f"{current_ath_event['value']:.2f}",
                        "New ATH Date": next_ath_event['date'].strftime('%Y-%m-%d'),
                        "New ATH Value": f"{next_ath_event['value']:.2f}",
                        "Days to New ATH": str(days_between)
                    })
        
        if ath_data: # Handle current period since last ATH
            last_ath_event = ath_data[-1]
            days_since_last_ath = (df.index[-1] - last_ath_event['date']).days
            if days_since_last_ath > 30:
                 ath_table_data.append({
                    "Previous ATH Date": last_ath_event['date'].strftime('%Y-%m-%d'),
                    "Previous ATH Value": f"{last_ath_event['value']:.2f}",
                    "New ATH Date": "Current",
                    "New ATH Value": f"{df['Close'].iloc[-1]:.2f} (Current Price)",
                    "Days to New ATH": f"{days_since_last_ath} (Ongoing)"
                })

        if ath_table_data:
            ath_df_display = pd.DataFrame(ath_table_data)
            st.dataframe(ath_df_display.style.set_properties(**{'text-align': 'left'}), use_container_width=True)
        else:
            st.info("No significant ATH recovery periods (longer than 30 days) found, or stock is consistently making new highs.")

    except Exception as e:
        st.error(f"An error occurred during stock analysis for {ticker_symbol}: {e}")
        import traceback
        st.error(f"Traceback: {traceback.format_exc()}")


def analyze_drawdowns_ui(stock_name_input): # Renamed for clarity from analyze_drawdowns
    ticker_to_analyze = stock_name_input
    if not any(x in stock_name_input for x in ['.', '^']): # Append .NS if no exchange/index specified
        ticker_to_analyze += ".NS"
    analyze_stock(ticker_to_analyze)


def main():
    st.set_page_config(page_title="StockLib", page_icon="📚", layout="wide")
    init_session_state() # Initialize session state variables

    # --- About Modal ---
    if st.session_state.show_about:
        with st.sidebar.expander("About StockLib 📚", expanded=True):
            st.caption("StockLib + NotebookLLM = Your AI-Powered Business Analyst")
            st.markdown("""
            **Quick Guide:**
            1.  **Documents Tab:** Enter stock name (e.g., TATAMOTORS, RELIANCE) to fetch from Screener.in.
            2.  Select document types (Annual Reports, Transcripts, PPTs).
            3.  Click "Fetch Documents" to download them into a ZIP file.
            4.  Upload this ZIP to NotebookLLM or your preferred analysis tool.
            5.  **Analysis Tab:** Search for a stock ticker (e.g., TATAMOTORS.NS, ^NSEI for Nifty 50).
            6.  View price charts, drawdowns, and ATH recovery times.
            
            **Goal:** Simplify data gathering for fundamental analysis and provide quick technical insights.
            """)
            st.caption("Note: Documents are fetched from screener.in. Stock data from Yahoo Finance.")
            if st.button("Close About", key="close_about_button_sidebar", use_container_width=True):
                st.session_state.show_about = False
                st.rerun()
    
    # --- Header and Tab Navigation ---
    col_title, col_about_button = st.columns([0.85, 0.15])
    with col_title:
        st.markdown("<h1 style='text-align: left; margin-bottom: 0px;'>StockLib 📚</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: left; color: #666666; margin-top: 0px;'>Your Toolkit for Fundamental & Technical Stock Data</p>", unsafe_allow_html=True)
    with col_about_button:
        if st.button("About", key="show_about_button_main", use_container_width=True):
            st.session_state.show_about = not st.session_state.show_about # Toggle
            st.rerun()
    
    st.markdown("---")

    tab1_title = "📄 Documents (from Screener.in)"
    tab2_title = "📈 Stock Analysis (from Yahoo Finance)"
    
    selected_tab = st.radio(
        "Choose a section:",
        [tab1_title, tab2_title],
        key="active_tab_radio", # Use a different key if 'active_tab' is used elsewhere for direct control
        horizontal=True,
        index=0 if st.session_state.active_tab == "documents" else 1
    )
    
    # Update session state if radio button changes tab
    if selected_tab == tab1_title and st.session_state.active_tab != "documents":
        st.session_state.active_tab = "documents"
        st.rerun() # Rerun to ensure UI consistency if needed
    elif selected_tab == tab2_title and st.session_state.active_tab != "analysis":
        st.session_state.active_tab = "analysis"
        st.rerun()


    # --- Tab 1: Document Downloader ---
    if st.session_state.active_tab == "documents":
        st.subheader(tab1_title)
        with st.form(key='stock_document_form'):
            screener_stock_name = st.text_input(
                "Enter stock name (as on Screener.in, e.g., TATAMOTORS, RELIANCE):", 
                placeholder="Example: HDFCBANK"
            )
            
            st.markdown("###### Select Document Types to Download:")
            doc_col1, doc_col2, doc_col3 = st.columns(3)
            with doc_col1:
                annual_reports_cb = st.checkbox("Annual Reports", value=True, key="ar_cb")
            with doc_col2:
                transcripts_cb = st.checkbox("Concall Transcripts", value=True, key="tr_cb")
            with doc_col3:
                ppts_cb = st.checkbox("Presentations (PPTs)", value=True, key="ppt_cb")
            
            fetch_button = st.form_submit_button(label="🔍 Fetch & Download Documents", use_container_width=True)
        
        if fetch_button and screener_stock_name:
            selected_doc_types = []
            if annual_reports_cb: selected_doc_types.append("Annual_Report")
            if transcripts_cb: selected_doc_types.append("Transcript")
            if ppts_cb: selected_doc_types.append("PPT")
            
            if not selected_doc_types:
                st.warning("Please select at least one document type.")
            else:
                with st.spinner(f"🔍 Searching for documents for '{screener_stock_name}' on Screener.in..."):
                    html_content = get_webpage_content(screener_stock_name)
                
                if html_content:
                    try:
                        links = parse_html_content(html_content)
                        if not links:
                            st.warning(f"📭 No document links found for '{screener_stock_name}'. The page structure might have changed or no documents are available.")
                        else:
                            filtered_links = [link for link in links if link['type'] in selected_doc_types]
                            if not filtered_links:
                                st.warning(f"📭 No documents found for '{screener_stock_name}' matching your selected types.")
                            else:
                                st.info(f"Found {len(filtered_links)} documents to download.")
                                progress_bar = st.progress(0.0)
                                status_text = st.empty()
                                status_text.text("Starting downloads...")

                                pdf_folder_name = f"{screener_stock_name}_documents_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                                
                                downloaded_files_paths, file_contents_dict = download_selected_documents(
                                    screener_stock_name, filtered_links, pdf_folder_name, 
                                    selected_doc_types, progress_bar, status_text
                                )
                                
                                if file_contents_dict:
                                    progress_bar.progress(1.0) # Ensure it reaches 100%
                                    status_text.success(f"✅ Downloaded {len(file_contents_dict)} documents for '{screener_stock_name}'. Preparing ZIP file...")
                                    
                                    zip_bytes = create_zip_in_memory(file_contents_dict)
                                    zip_filename = f"{screener_stock_name}_documents.zip"
                                    
                                    st.download_button(
                                        label=f"📥 Download {zip_filename}",
                                        data=zip_bytes,
                                        file_name=zip_filename,
                                        mime="application/zip",
                                        use_container_width=True
                                    )
                                    # Clean up the temporarily created local folder if needed, though it's named uniquely.
                                    # For pure in-memory, ensure download_pdf doesn't save to disk if not necessary.
                                    # Current setup saves then reads for zipping. If disk saving is an issue for deployment,
                                    # download_pdf should return content directly, and zipping happens from that.
                                    # For now, this is fine for most environments.
                                    try:
                                        import shutil
                                        if os.path.exists(pdf_folder_name):
                                            shutil.rmtree(pdf_folder_name) # Clean up local files
                                            # st.caption(f"Cleaned up temporary folder: {pdf_folder_name}")
                                    except Exception as e_clean:
                                        st.caption(f"Note: Could not automatically clean up folder {pdf_folder_name}: {e_clean}")


                                else:
                                    status_text.warning("No documents were successfully downloaded. Check messages above for errors.")
                                    progress_bar.progress(0.0) # Reset progress bar
                    except Exception as e_parse:
                        st.error(f"An error occurred while parsing document links: {e_parse}")
                        import traceback
                        st.error(f"Traceback: {traceback.format_exc()}")
                # else: Error message handled by get_webpage_content

    # --- Tab 2: Stock Analysis ---
    elif st.session_state.active_tab == "analysis":
        st.subheader(tab2_title)
        st.text_input(
            "Search for a stock ticker (e.g., HDFCBANK.NS, INFY.BO, ^NSEI):",
            key="search_query",
            on_change=on_search_change,
            placeholder="Type 2+ characters for suggestions..."
        )

        if st.session_state.suggestions:
            st.write("Suggestions:")
            # Display suggestions in a more compact way, perhaps columns or smaller buttons
            # For simplicity, one button per line for now
            for ticker, name in st.session_state.suggestions:
                if st.button(f"{name} ({ticker})", key=f"select_{ticker}"):
                    select_stock(ticker, name) # This will trigger a rerun and switch tab if needed
        
        if st.session_state.selected_ticker and st.session_state.analyze_flag:
            analyze_drawdowns_ui(st.session_state.selected_ticker)
            st.session_state.analyze_flag = False # Reset flag after analysis

    # --- Footer ---
    st.markdown("---")
    st.caption("StockLib by Your Name/Organization | Data from Screener.in & Yahoo Finance | For educational purposes only.")


if __name__ == "__main__":
    main()