# app.py
import os, re, time, io, zipfile, base64, random, tempfile, shutil, urllib.parse
from datetime import datetime
from typing import Dict, List, Tuple, Optional

import requests
from bs4 import BeautifulSoup
import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ------------------------------------------------------------------------------
# CONSTANTS
# ------------------------------------------------------------------------------
MIN_FILE_SIZE               = 1024
REQ_CONNECT_TO, REQ_READ_TO = 15, 120
SELENIUM_LOAD, SELENIUM_DL  = 300, 300

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
]

# ------------------------------------------------------------------------------
# UTILS
# ------------------------------------------------------------------------------
def get_extension(r: requests.Response, url: str, default: str) -> str:
    cd = r.headers.get("Content-Disposition")
    if cd:
        fn = re.findall(r'filename\*?=(?:UTF-\d+\'\')?([^;\s"]+)', cd, re.I)
        if fn:
            _, ext = os.path.splitext(urllib.parse.unquote(fn[-1].strip('"')))
            if 1 < len(ext) < 7:
                return ext.lower()

    ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    MIME = {
        "application/pdf": ".pdf",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/zip": ".zip",
        "text/csv": ".csv",
    }
    if ct in MIME:
        return MIME[ct]

    try:
        _, ext = os.path.splitext(urllib.parse.urlparse(url).path)
        if 1 < len(ext) < 7:
            return ext.lower()
    except Exception:
        pass

    return { "PPT": ".pptx", "Transcript": ".pdf" }.get(default, ".pdf")


def clean_filename(date_str: str, doc_type: str) -> str:
    for pat, fmt in [
        (r"^\d{4}$", "{}_{}"),
        (r"^\d{4}-\d{2}$", "{}_{}"),
        (r"^\d{2}/\d{2}/\d{4}$", "{}_{}"),
    ]:
        if re.match(pat, date_str):
            return fmt.format(date_str.replace("-", "_").replace("/", "_"), doc_type)
    return f"{re.sub(r'[^\\w.-]', '_', date_str)}_{doc_type}"

# ------------------------------------------------------------------------------
# SCRAPER
# ------------------------------------------------------------------------------
def fetch_links(ticker: str) -> List[Dict]:
    url = f"https://www.screener.in/company/{ticker}/consolidated/#documents"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = requests.get(url, headers=headers, timeout=REQ_CONNECT_TO)
        r.raise_for_status()
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            st.error(f"Stock '{ticker}' not found on Screener.")
        else:
            st.error(f"HTTP {e.response.status_code} while fetching screener page.")
        return []
    except Exception as e:
        st.error(f"Network error: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []

    # Annual reports
    for a in soup.select(".annual-reports a"):
        m = re.search(r"Financial Year (\d{4})", a.text)
        if m:
            results.append({"date": m.group(1), "type": "Annual_Report", "url": a["href"]})

    # Concalls
    for li in soup.select(".concalls li"):
        date_div = li.select_one(".ink-600")
        if not date_div:
            continue
        date_str = date_div.get_text(strip=True)
        try:
            date_str = datetime.strptime(date_str, "%b %Y").strftime("%Y-%m")
        except ValueError:
            pass
        for a in li.select("a"):
            if "Transcript" in a.text:
                results.append({"date": date_str, "type": "Transcript", "url": a["href"]})
            elif "PPT" in a.text:
                results.append({"date": date_str, "type": "PPT", "url": a["href"]})

    return sorted(results, key=lambda x: x["date"], reverse=True)

# ------------------------------------------------------------------------------
# DOWNLOADER (requests only – selenium kept for fall-back)
# ------------------------------------------------------------------------------
def bse_cdn_fallback_url(old_url: str) -> Optional[str]:
    """
    Convert legacy /bseplus/AnnualReport/<scrip>/<fname> to new CDN URL.
    Returns None if url is not a legacy annual-report link.
    """
    m = re.match(r".*/bseindia\.com/bseplus/AnnualReport/\d+/(.+\.pdf)$", old_url)
    if not m:
        return None
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{m.group(1)}"

def download_once(url: str, path_no_ext: str, doc_type: str, session: requests.Session) -> Tuple[Optional[str], Optional[bytes], str]:
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = session.get(url, headers=headers, stream=True, timeout=(REQ_CONNECT_TO, REQ_READ_TO))
        r.raise_for_status()
    except Exception as e:
        return None, None, str(e)

    ext = get_extension(r, url, doc_type)
    full_path = path_no_ext + ext
    content = io.BytesIO()
    for chunk in r.iter_content(8192):
        if chunk:
            content.write(chunk)
    data = content.getvalue()
    if data.startswith(b"<!DOCTYPE") or len(data) < MIN_FILE_SIZE:
        return None, None, "small or html"

    with open(full_path, "wb") as f:
        f.write(data)
    return full_path, data, ""

def download_with_retry(link: Dict, folder: str) -> Tuple[Optional[str], Optional[bytes], str]:
    base = clean_filename(link["date"], link["type"])
    session = requests.Session()

    # 1st attempt – original URL
    path, data, err = download_once(link["url"], os.path.join(folder, base), link["type"], session)
    if path:
        return path, data, ""

    # legacy BSE annual report? try CDN fallback
    cdn = bse_cdn_fallback_url(link["url"])
    if cdn:
        path, data, err = download_once(cdn, os.path.join(folder, base), link["type"], session)
        if path:
            return path, data, ""

    # Selenium fallback (kept for non-BSE or other quirks)
    return selenium_fallback(link, folder, base)

def selenium_fallback(link: Dict, folder: str, base: str) -> Tuple[Optional[str], Optional[bytes], str]:
    chrome_opts = Options()
    chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--window-size=1920,1080")
    chrome_opts.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")

    tmp_dir = tempfile.mkdtemp()
    prefs = {
        "download.default_directory": tmp_dir,
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    }
    chrome_opts.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_opts)
    driver.set_page_load_timeout(SELENIUM_LOAD)

    try:
        driver.get(link["url"])
        deadline = time.time() + SELENIUM_DL
        while time.time() < deadline:
            done = [f for f in os.listdir(tmp_dir) if not f.endswith((".crdownload", ".tmp"))]
            if done:
                tmp_path = os.path.join(tmp_dir, done[0])
                with open(tmp_path, "rb") as f:
                    data = f.read()
                if len(data) >= MIN_FILE_SIZE:
                    ext = os.path.splitext(done[0])[1] or get_extension(
                        type("", (), {"headers": {}})(), link["url"], link["type"]
                    )
                    final_path = os.path.join(folder, base + ext)
                    shutil.move(tmp_path, final_path)
                    return final_path, data, ""
                break
            time.sleep(1)
    except Exception as e:
        return None, None, str(e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        driver.quit()
    return None, None, "selenium could not resolve"

# ------------------------------------------------------------------------------
# ZIP
# ------------------------------------------------------------------------------
def zip_files(contents: Dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in contents.items():
            z.writestr(name, data)
    buf.seek(0)
    return buf.getvalue()

# ------------------------------------------------------------------------------
# STREAMLIT UI
# ------------------------------------------------------------------------------
def main():
    st.set_page_config("StockLib 📚", layout="centered")
    st.markdown(
        "<h1 style='text-align:center;'>StockLib 📚</h1>"
        "<h4 style='text-align:center;color:grey;font-weight:normal'>"
        "Your First Step in Fundamental Analysis – Your Business Data Library!</h4>",
        unsafe_allow_html=True,
    )

    with st.form("form"):
        ticker = st.text_input("Enter stock ticker (BSE / NSE):", placeholder="e.g. TATAMOTORS").strip().upper()
        ar = st.checkbox("Annual Reports 📄", True)
        tr = st.checkbox("Concall Transcripts 📝", True)
        ppt = st.checkbox("Presentations 📊", True)
        submitted = st.form_submit_button("🔍 Fetch Documents", use_container_width=True, type="primary")

    if submitted:
        if not ticker:
            st.error("Please enter a ticker."); st.stop()
        types = []
        if ar: types.append("Annual_Report")
        if tr: types.append("Transcript")
        if ppt: types.append("PPT")
        if not types:
            st.warning("Select at least one document type."); st.stop()

        with st.spinner(f"Searching documents for {ticker}…"):
            links = fetch_links(ticker)
            links = [l for l in links if l["type"] in types]
            if not links:
                st.warning("No documents found."); st.stop()

        st.success(f"Found {len(links)} documents – starting download…")
        with tempfile.TemporaryDirectory() as tmp:
            progress = st.progress(0)
            status = st.empty()
            ok, fail = 0, 0
            contents = {}
            for idx, link in enumerate(links, 1):
                path, data, err = download_with_retry(link, tmp)
                if path:
                    name = os.path.basename(path)
                    counter = 1
                    while name in contents:
                        name = f"{os.path.splitext(name)[0]}_{counter}{os.path.splitext(name)[1]}"
                        counter += 1
                    contents[name] = data
                    ok += 1
                else:
                    fail += 1
                progress.progress(idx / len(links))
                status.text(f"Downloaded {ok} | Failed {fail}")
            progress.empty()
            if contents:
                zip_data = zip_files(contents)
                st.download_button(
                    label=f"📥 Download {ok} documents ({ticker}).zip",
                    data=zip_data,
                    file_name=f"{ticker}_documents.zip",
                    mime="application/zip",
                    use_container_width=True,
                    type="primary",
                )
            if fail:
                st.warning(f"{fail} documents could not be downloaded.")

if __name__ == "__main__":
    main()
