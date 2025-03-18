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
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, stream=True, timeout=50)
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
# Add after the existing imports
import time

# Add before the main() function
@st.cache_data(ttl=60)
def get_ticker_suggestions(query):
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        suggestions = []
        for item in data.get("quotes", []):
            symbol = item["symbol"]
            name = item.get("shortname", "Unknown")
            if symbol.endswith(".NS") or symbol.endswith(".BO") or symbol.startswith("^"):
                suggestions.append((symbol, name))
        return suggestions
    except Exception as e:
        st.error(f"Error fetching suggestions: {e}")
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

def on_search_change():
    query = st.session_state.search_query
    current_time = time.time()
    
    if current_time - st.session_state.last_search_time > 0.3 and len(query) >= 2:
        st.session_state.suggestions = get_ticker_suggestions(query)
        st.session_state.last_search_time = current_time
    elif len(query) < 2:
        st.session_state.suggestions = []

def select_stock(ticker, name):
    st.session_state.selected_ticker = ticker
    st.session_state.selected_name = name
    st.session_state.suggestions = []
    st.session_state.analyze_flag = True
    # Force analysis tab selection
    st.session_state.active_tab = "analysis"
    # Add this to force a rerun to apply the tab change immediately
    st.rerun()

# Move analyze functions outside of main()
def analyze_stock(ticker):
    st.write(f"**Using Ticker:** {ticker}")
    
    with st.spinner("Downloading data..."):
        stock_data = yf.download(ticker, period="max")
    
    if stock_data.empty:
        st.error("Error: Invalid ticker or no data available.")
        return
    
    st.success(f"Downloaded {len(stock_data)} rows of data.")
    
    # Process the data
    df = stock_data[['Close']].copy()
    close_series = df['Close'].squeeze()
    df['ATH'] = close_series.cummax()
    df['Drawdown'] = (close_series - df['ATH']) / df['ATH']
    
    # Define drawdown threshold
    threshold = -0.25
    df['In_Drawdown'] = df['Drawdown'] <= threshold
    
    # Identify drawdown periods
    drawdown_periods = []
    in_drawdown = False
    start_date = None
    
    for date, row in df.iterrows():
        # Fix: Use boolean value explicitly with .item() or .bool()
        is_in_drawdown = row['In_Drawdown'].item() if hasattr(row['In_Drawdown'], 'item') else bool(row['In_Drawdown'])
        
        if is_in_drawdown and not in_drawdown:
            # Start of a drawdown period
            in_drawdown = True
            start_date = date
        elif not is_in_drawdown and in_drawdown:
            # End of a drawdown period
            in_drawdown = False
            drawdown_periods.append((start_date, date))
            start_date = None
    
    # If we're still in a drawdown at the end of the data
    if in_drawdown and start_date is not None:
        drawdown_periods.append((start_date, df.index[-1]))
    
    # Improved chart layout
    col1, col2 = st.columns([1, 0.05])
    with col1:
        # Price chart with Matplotlib
        fig1, ax1 = plt.subplots(figsize=(12, 6))
        ax1.plot(df.index, df['Close'], label='Close Price', color='blue')
        ax1.plot(df.index, df['ATH'], label='All-Time High', color='green', linestyle='--')
        
        # Highlight drawdown periods
        for start, end in drawdown_periods:
            ax1.axvspan(start, end, alpha=0.2, color='red')
        
        ax1.set_title(f"{ticker} Price and All-Time High")
        ax1.set_xlabel("Date")
        ax1.set_ylabel("Price")
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        plt.tight_layout(pad=2.0)
        st.pyplot(fig1, use_container_width=True)
        
        # Drawdown chart with Plotly
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=df.index, 
            y=df['Drawdown'] * 100, 
            mode='lines', 
            name='Drawdown (%)', 
            line=dict(color='red')
        ))
        
        # Add a horizontal line at the threshold
        fig2.add_shape(
            type="line",
            x0=df.index[0],
            y0=threshold * 100,
            x1=df.index[-1],
            y1=threshold * 100,
            line=dict(color="red", width=1, dash="dash"),
        )
        
        fig2.update_layout(
            title=f"{ticker} Drawdowns",
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
            template="plotly_white",
            height=450,
            width=None,  # Allow auto-width
            margin=dict(l=50, r=20, t=50, b=50)
        )
        
        st.plotly_chart(fig2, use_container_width=True)
        
        # Remove the drawdown periods table and only keep the ATH recovery table
        if True:  # Always show ATH table regardless of drawdown periods
            st.markdown("### Days to hit next All time high")
            
            # Find all ATH dates and their values
            ath_data = []
            prev_ath = 0
            
            # Identify all the ATH points
            for date, row in df.iterrows():
                current_ath = float(row['ATH'].iloc[0]) if isinstance(row['ATH'], pd.Series) else float(row['ATH'])
                if current_ath > prev_ath:
                    ath_data.append({
                        'date': date,
                        'value': current_ath
                    })
                    prev_ath = current_ath
            
            # Create table data showing progression from one ATH to the next
            ath_table_data = []
            
            for i in range(len(ath_data) - 1):
                current_ath = ath_data[i]
                next_ath = ath_data[i + 1]
                
                days_between = (next_ath['date'] - current_ath['date']).days
                
                # Only include entries where days between ATHs are more than 30
                if days_between > 30:
                    ath_table_data.append({
                        "ATH Date": current_ath['date'].strftime('%Y-%m-%d'),
                        "ATH Value": f"{current_ath['value']:.2f}",
                        "Next ATH Date": next_ath['date'].strftime('%Y-%m-%d'),
                        "Next ATH Value": f"{next_ath['value']:.2f}",
                        "Days Between": str(days_between)  # Convert to string
                    })
            
            # Add the last ATH with no next ATH if it's been more than 30 days
            if ath_data:
                last_ath = ath_data[-1]
                days_since = (df.index[-1] - last_ath['date']).days
                
                if days_since > 30:
                    ath_table_data.append({
                        "ATH Date": last_ath['date'].strftime('%Y-%m-%d'),
                        "ATH Value": f"{last_ath['value']:.2f}",
                        "Next ATH Date": "Current",
                        "Next ATH Value": "N/A",
                        "Days Between": f"{days_since} (ongoing)"  # Modified format
                    })
            
            # Create DataFrame and display as table
            if ath_table_data:
                ath_df = pd.DataFrame(ath_table_data)
                st.dataframe(ath_df, use_container_width=True)
            else:
                st.info("No ATH recovery periods longer than 30 days found.")

def analyze_drawdowns(stock_name):
    if not any(x in stock_name for x in ['.', '^']):
        stock_name += ".NS"
    analyze_stock(stock_name)

def main():
    st.set_page_config(page_title="StockLib", page_icon="📚")
    
    # Initialize session state for About modal and active tab
    if 'show_about' not in st.session_state:
        st.session_state.show_about = False
    if 'active_tab' not in st.session_state:
        st.session_state.active_tab = "documents"
    
    # About button and modal code
    with st.container():
        # Create a right-aligned container for buttons
        _, right_col = st.columns([3, 1])
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
                8. Analyze stock drawdowns and recovery periods:
                   - Track historical price corrections
                   - View time taken to reach new all-time highs
                """)
            
            st.caption("Note: All documents belong to BSE/NSE/respective companies and are fetched from screener.in")
            
            if st.button("Close", key="close_about_button"):
                st.session_state.show_about = False
                st.rerun()
                
    # Improved header styling
    st.markdown("""
        <h1 style='text-align: center;'>StockLib 📚</h1>
        <h4 style='text-align: center; color: #666666;'>Your First Step in Fundamental Analysis – Your Business and stock Data Library!</h4>
        <hr>
    """, unsafe_allow_html=True)
    
    # Create containers for main content and footer
    main_container = st.container()
    footer_container = st.container()
    
    with main_container:
        # Replace tabs with sections that appear one after another
        st.markdown("## 📚 Documents")
        st.markdown("---")
        
        # Document section content
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
            else:
                with st.spinner("🔍 Searching for documents..."):
                    html_content = get_webpage_content(stock_name)
                    
                    if not html_content:
                        st.error("Failed to fetch webpage content. Please check the stock ticker and try again.")
                    else:
                        try:
                            links = parse_html_content(html_content)
                            if not links:
                                st.warning("📭 No documents found for this stock.")
                            else:
                                filtered_links = [link for link in links if link['type'] in doc_types]
                                if not filtered_links:
                                    st.warning("📭 No documents found for the selected types.")
                                else:
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
        
        # Add a separator between sections
        st.markdown("---")
        
        # Analysis section
        st.markdown("## 📊 Analysis")
        st.markdown("---")
        
        # Analysis section content
        st.markdown("### Stock Drawdown Analysis 📊")
        init_session_state()
        
        # Search input with button - better alignment
        search_col1, search_col2 = st.columns([3, 1])
        with search_col1:
            st.text_input(
                "Search for stock or index:",
                placeholder="Example: TATAMOTORS, NIFTY50, SENSEX",
                key="search_query",
                label_visibility="visible"
            )
        with search_col2:
            # Reduced vertical spacing
            st.markdown("<div style='margin: 1.6em'></div>", unsafe_allow_html=True)
            search_button = st.button("Search", use_container_width=True, type="primary")
            if search_button:
                on_search_change()
        
        # Display suggestions
        if st.session_state.suggestions:
            cols = st.columns(2)
            for i, (symbol, name) in enumerate(st.session_state.suggestions[:8]):
                col_idx = i % 2
                with cols[col_idx]:
                    if st.button(
                        f"{name} ({symbol})",
                        key=f"suggestion_{i}",
                        use_container_width=True,
                        type="secondary"
                    ):
                        select_stock(symbol, name)
        
        # Show analysis when stock is selected
        if st.session_state.selected_ticker:
            analyze_drawdowns(st.session_state.selected_ticker)
if __name__ == "__main__":
    main()
