import os
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, Page
from config import (
    OUTPUT_DIR, CHART_LAYOUT_ID, TIMEFRAME_MAP,
    SYMBOL_EXCHANGE_MAP, MAX_RETRIES, RETRY_BACKOFF,
    INDICATOR_WAIT_TIMEOUT, PER_TF_BUDGET, NAV_TIMEOUT,
    LAUNCH_TIMEOUT, ACTION_TIMEOUT,
)
from cookies_manager import load_cookies, load_headers

os.makedirs(OUTPUT_DIR, exist_ok=True)


def _quiet_close(fn):
    try:
        fn()
    except Exception as e:
        print(f"[cleanup] {fn.__qualname__} failed: {e}")


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


def wait_for_indicators_ready(page, timeout=INDICATOR_WAIT_TIMEOUT):
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
        # launch 默认无超时：Chromium 起不来就永久阻塞 worker，job 走不到 finally，
        # 队列雪崩且 task_inflight 永不归零。所有阻塞操作都必须有上界。
        browser = pw.chromium.launch(headless=headless, timeout=LAUNCH_TIMEOUT * 1000)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=headers.get("user-agent", "Mozilla/5.0"),
            locale="zh-CN",
        )
        # 兜底所有未显式传 timeout 的操作（click / evaluate / expect_download 等）
        context.set_default_timeout(ACTION_TIMEOUT * 1000)
        context.set_default_navigation_timeout(NAV_TIMEOUT * 1000)
        context.add_cookies(cookies)

        # 严格串行：一次只开一个图表页，截完立刻关。
        # 曾经是先并行 goto 所有周期再逐个等——N 个 TradingView 页面同时算 Pine 指标
        # 会互相抢 CPU，spinner 一直不消失，每个周期都熬满预算。单周期 ~10s 的活
        # 三周期能拖到 5 分钟以上。串行反而快得多，内存占用也恒定。
        for tf in valid_tfs:
            deadline = time.time() + PER_TF_BUDGET
            page = context.new_page()
            try:
                page.goto(build_chart_url(symbol, tf), wait_until="domcontentloaded",
                          timeout=NAV_TIMEOUT * 1000)

                for attempt in range(MAX_RETRIES):
                    remaining = deadline - time.time()
                    if remaining <= 5:
                        print(f"[{symbol}|{tf}] 预算耗尽，用当前画面截图（指标可能未算完）")
                        break
                    ready = wait_for_indicators_ready(
                        page, timeout=min(INDICATOR_WAIT_TIMEOUT, remaining))
                    if ready:
                        break
                    if attempt < MAX_RETRIES - 1:
                        backoff = RETRY_BACKOFF[attempt]
                        print(f"[{symbol}|{tf}] Retry {attempt + 1}, waiting {backoff}s "
                              f"(剩余预算 {int(deadline - time.time())}s)")
                        time.sleep(backoff)
                        page.reload(wait_until="domcontentloaded",
                                    timeout=NAV_TIMEOUT * 1000)

                ts = time.strftime("%Y%m%d_%H%M%S")
                tmp_name = f"_tmp_{os.getpid()}_{uuid.uuid4().hex[:8]}.png"
                tmp_path = Path(OUTPUT_DIR) / tmp_name
                _click_screenshot_and_download(page, tmp_path)

                final_name = f"{symbol}_{tf}_{ts}.png"
                final_path = Path(OUTPUT_DIR) / final_name
                tmp_path.rename(final_path)
                results.append(str(final_path))
                print(f"[{symbol}|{tf}] OK ({int(time.time() - (deadline - PER_TF_BUDGET))}s)")
            except Exception as e:
                # 单周期失败不影响其余周期
                print(f"[{symbol}|{tf}] Screenshot failed: {e}")
            finally:
                _quiet_close(page.close)

        # 收尾同样可能卡住，卡了也不能让 job 悬着——浏览器进程有 init 兜底回收
        _quiet_close(context.close)
        _quiet_close(browser.close)

    return results
