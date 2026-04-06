import os
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, Page
from config import (
    OUTPUT_DIR, CHART_LAYOUT_ID, TIMEFRAME_MAP,
    SYMBOL_EXCHANGE_MAP, MAX_RETRIES, RETRY_BACKOFF,
)
from cookies_manager import load_cookies, load_headers

os.makedirs(OUTPUT_DIR, exist_ok=True)


def build_chart_url(symbol, timeframe=None):
    if ":" not in symbol:
        mapped = SYMBOL_EXCHANGE_MAP.get(symbol.upper())
        if mapped:
            symbol = mapped
        else:
            symbol = f"BINANCE:{symbol}.P"

    url = f"https://cn.tradingview.com/chart/{CHART_LAYOUT_ID}/?symbol={symbol}"
    if timeframe and timeframe in TIMEFRAME_MAP:
        url += f"&interval={TIMEFRAME_MAP[timeframe]}"
    return url


def _count_visible_spinners(page):
    return page.evaluate("""() => {
        const spinners = document.querySelectorAll(
            '.loader-spinner, .tv-spinner, [class*="spinner"], [class*="loading"]'
        );
        let count = 0;
        spinners.forEach(s => {
            const rect = s.getBoundingClientRect();
            const style = window.getComputedStyle(s);
            if (rect.width > 0 && rect.height > 0 &&
                style.display !== 'none' && style.visibility !== 'hidden') {
                count++;
            }
        });
        return count;
    }""")


def _has_calculation_timeout(page):
    return page.evaluate("""() => {
        const text = document.body.innerText || '';
        return text.includes('Calculation timed out') || text.includes('计算超时');
    }""")


def wait_for_indicators_ready(page, timeout=60):
    time.sleep(2)
    start = time.time()
    stable_count = 0
    spinners_ever_seen = False
    required_stable = 6

    while time.time() - start < timeout:
        spinners = _count_visible_spinners(page)
        if spinners > 0:
            spinners_ever_seen = True
            stable_count = 0
        else:
            stable_count += 1

        threshold = required_stable if spinners_ever_seen else 12
        if stable_count >= threshold:
            return not _has_calculation_timeout(page)

        if _has_calculation_timeout(page):
            return False

        time.sleep(0.5)

    return not _has_calculation_timeout(page)


def _click_screenshot_and_download(page, output_path):
    btn = page.locator("button:has(#header-toolbar-screenshot)")
    btn.click()
    time.sleep(1)

    download_btn = None
    for selector in [
        'div[data-name="save-chart-image"]',
        ':text("下载图片")',
        ':text("Download image")',
    ]:
        loc = page.locator(selector)
        if loc.count() > 0:
            download_btn = loc.first
            break

    if not download_btn:
        raise RuntimeError("Download button not found")

    with page.expect_download(timeout=15000) as dl_info:
        download_btn.click()

    download = dl_info.value
    download.save_as(str(output_path))
    return output_path


def capture_chart(symbol, timeframes, headless=True):
    try:
        cookies = load_cookies()
    except Exception as e:
        raise RuntimeError(f"Failed to load cookies: {e}")

    try:
        headers = load_headers()
    except Exception:
        headers = {}

    valid_tfs = []
    for tf in timeframes:
        if tf in TIMEFRAME_MAP and tf not in valid_tfs:
            valid_tfs.append(tf)

    if not valid_tfs:
        raise ValueError(f"No valid timeframes in: {timeframes}")

    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=headers.get("user-agent", "Mozilla/5.0"),
            locale="zh-CN",
        )
        context.add_cookies(cookies)

        pages = {}
        for tf in valid_tfs:
            url = build_chart_url(symbol, tf)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            pages[tf] = page

        for tf, page in pages.items():
            for attempt in range(MAX_RETRIES):
                ready = wait_for_indicators_ready(page)
                if ready:
                    break
                if attempt < MAX_RETRIES - 1:
                    backoff = RETRY_BACKOFF[attempt]
                    print(f"[{symbol}|{tf}] Retry {attempt + 1}, waiting {backoff}s")
                    time.sleep(backoff)
                    page.reload(wait_until="domcontentloaded")

            try:
                ts = time.strftime("%Y%m%d_%H%M%S")
                tmp_name = f"_tmp_{os.getpid()}_{uuid.uuid4().hex[:8]}.png"
                tmp_path = Path(OUTPUT_DIR) / tmp_name
                _click_screenshot_and_download(page, tmp_path)

                final_name = f"{symbol}_{tf}_{ts}.png"
                final_path = Path(OUTPUT_DIR) / final_name
                tmp_path.rename(final_path)
                results.append(str(final_path))
            except Exception as e:
                print(f"[{symbol}|{tf}] Screenshot failed: {e}")

        context.close()
        browser.close()

    return results
