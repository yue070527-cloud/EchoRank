from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "http://localhost:8765"
OUTPUT = Path("D:/Desktop/MyBillboard/frontend/test-output")
OUTPUT.mkdir(exist_ok=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    console_errors = []
    page_errors = []

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(URL)
    page.wait_for_load_state("networkidle")

    assert page.get_by_text("PERSONAL CHART 50").is_visible()
    assert page.locator("chart-list chart-row").count() == 8
    assert page.get_by_text("Midnight Index").is_visible()
    assert "2026 年 7 月 17 日" in page.locator("period-selector").inner_text()
    assert "16 条已发布数据" in page.locator("[data-chart-status]").inner_text()
    assert page.get_by_role("button", name="下一期").is_disabled()

    page.get_by_role("button", name="上一期").click()
    page.wait_for_function("document.querySelector('period-selector').innerText.includes('2026 年 7 月 16 日')")
    assert page.get_by_role("button", name="上一期").is_disabled()
    assert page.get_by_role("button", name="下一期").is_enabled()

    page.get_by_role("tab", name="专辑").click()
    assert "暂无专辑日榜数据" in page.locator("[data-chart-status]").inner_text()
    assert page.locator("chart-row").count() == 0

    page.get_by_role("tab", name="歌曲").click()
    page.wait_for_function("document.querySelectorAll('chart-list chart-row').length === 8")
    page.get_by_role("tab", name="周榜").focus()
    page.keyboard.press("ArrowRight")
    assert page.get_by_role("tab", name="月榜").get_attribute("aria-selected") == "true"
    assert "暂无歌曲月榜数据" in page.locator("[data-chart-status]").inner_text()

    page.get_by_role("tab", name="日榜").click()
    page.wait_for_function("document.querySelectorAll('chart-list chart-row').length === 8")
    toggle = page.locator(".bubbling-toggle")
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert page.locator("bubbling-section chart-row").count() == 8

    page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)

    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.goto(URL)
    mobile.wait_for_load_state("networkidle")
    assert mobile.get_by_text("Midnight Index").is_visible()
    first_row = mobile.locator("chart-list chart-row").first
    assert first_row.bounding_box()["width"] <= 390
    mobile.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)

    browser.close()

    if console_errors or page_errors:
        raise AssertionError({"console_errors": console_errors, "page_errors": page_errors})

print("PASS: JSON loading, period navigation, unavailable views, tabs, bubbling section, desktop and mobile layouts")
