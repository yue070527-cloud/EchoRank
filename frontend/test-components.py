from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "http://localhost:8765"
OUTPUT = Path("D:/Desktop/MyBillboard/frontend/test-output")
OUTPUT.mkdir(exist_ok=True)

ENTITY_EXPECTATIONS = {"歌曲": 100, "专辑": 56, "艺人": 32}
PERIODS = {
    "日榜": "2026 年 7 月 17 日",
    "周榜": "2026 年第 29 周",
    "月榜": "2026 年 7 月",
    "年榜": "2026 年",
}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    console_errors = []
    page_errors = []
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(URL)
    page.wait_for_load_state("networkidle")
    settlement = page.locator("settlement-reveal dialog")
    if settlement.get_attribute("open") is not None:
        assert settlement.locator(".settlement-winner").count() == 2
        settlement.locator(".settlement-reveal__close").click()

    for entity, expected_count in ENTITY_EXPECTATIONS.items():
        page.locator('chart-tabs[name="entity"] [role="tab"]', has_text=entity).click()
        for period, label in PERIODS.items():
            page.locator('chart-tabs[name="period"] [role="tab"]', has_text=period).click()
            page.wait_for_function(
                "([entity, period]) => document.querySelector('[data-chart-status]').innerText.includes(entity + period)",
                arg=[entity, period],
            )
            assert label in page.locator("period-selector").inner_text()
            expected_period_count = expected_count
            if period == "年榜":
                expected_period_count = {"歌曲": 100, "专辑": 69, "艺人": 37}[entity]
            assert f"{expected_period_count} 条已发布数据" in page.locator("[data-chart-status]").inner_text()
            rows = page.locator('[data-page-region="charts"] > chart-list > section .chart-list__rows > chart-row')
            assert rows.count() == min(expected_period_count, 50), (entity, period, rows.count())
            expected_bubbling = max(expected_period_count - 50, 0)
            toggle = page.locator(".bubbling-toggle")
            if expected_bubbling:
                if toggle.get_attribute("aria-expanded") != "true":
                    toggle.click()
                assert page.locator("bubbling-section chart-row").count() == expected_bubbling
            totals = rows.locator(".chart-row__total strong").all_inner_texts()
            values = [float(value.replace(",", "")) for value in totals]
            assert values == sorted(values, reverse=True)

    page.locator('chart-tabs[name="entity"] [role="tab"]', has_text="歌曲").click()
    page.locator('chart-tabs[name="period"] [role="tab"]', has_text="周榜").click()
    page.locator('app-navigation [data-value="trends"]').click()
    aggregate = page.locator("aggregate-trend")
    assert page.locator('[data-page-region="trends"]').is_visible()
    assert not page.locator('[data-page-region="charts"]').is_visible()
    assert page.locator("trend-detail dialog").get_attribute("open") is None
    page.wait_for_function(
        "() => document.querySelectorAll('aggregate-trend .aggregate-ranking__item').length === 50"
    )
    assert aggregate.locator('[data-top-n="50"]').get_attribute("aria-pressed") == "true"
    aggregate.locator('[data-top-n="10"]').click()
    page.wait_for_function(
        "() => document.querySelectorAll('aggregate-trend .aggregate-ranking__item').length === 10"
    )
    assert aggregate.locator('[data-top-n="10"]').get_attribute("aria-pressed") == "true"
    assert aggregate.locator(".aggregate-play").is_disabled()
    aggregate.locator(".aggregate-ranking__item").nth(1).focus()
    assert aggregate.locator(".aggregate-ranking__item").nth(1).get_attribute("aria-selected") == "true"
    page.keyboard.press("ArrowDown")
    assert aggregate.locator(".aggregate-ranking__item").nth(2).get_attribute("aria-selected") == "true"
    page.locator('app-navigation [data-value="charts"]').click()
    assert page.locator('[data-page-region="charts"]').is_visible()

    page.locator('chart-tabs[name="entity"] [role="tab"]', has_text="歌曲").click()
    page.locator('chart-tabs[name="period"] [role="tab"]', has_text="日榜").click()
    first_chart_row = page.locator('[data-page-region="charts"] > chart-list chart-row button').first
    first_chart_row.click()
    dialog = page.locator("trend-detail dialog")
    assert dialog.get_attribute("open") is not None
    assert dialog.locator("canvas").count() == 1
    page.wait_for_function(
        "() => document.querySelectorAll('trend-detail .trend-table tbody tr').length >= 1"
    )
    assert dialog.locator(".trend-table tbody tr").count() >= 1
    assert dialog.locator("[data-trend-status]").inner_text()
    page.locator('trend-detail [role="tab"]', has_text="周榜").click()
    page.wait_for_function(
        "() => document.querySelector('trend-detail .trend-tab[data-value=\"weekly\"]').getAttribute('aria-selected') === 'true'"
    )
    page.keyboard.press("Escape")
    assert dialog.get_attribute("open") is None
    assert first_chart_row.evaluate("element => document.activeElement === element")

    page.locator('chart-tabs[name="entity"] [role="tab"]', has_text="歌曲").click()
    page.locator('chart-tabs[name="period"] [role="tab"]', has_text="周榜").focus()
    page.keyboard.press("ArrowRight")
    assert page.locator('chart-tabs[name="period"] [role="tab"]', has_text="月榜").get_attribute("aria-selected") == "true"
    page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)

    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.goto(URL)
    mobile.wait_for_load_state("networkidle")
    mobile_settlement = mobile.locator("settlement-reveal dialog")
    if mobile_settlement.get_attribute("open") is not None:
        assert mobile_settlement.bounding_box()["width"] <= 390
        mobile_settlement.locator(".settlement-reveal__close").click()
    first_row = mobile.locator('[data-page-region="charts"] > chart-list chart-row').first
    assert first_row.bounding_box()["width"] <= 390
    mobile.locator('app-navigation [data-value="trends"]').click()
    mobile.wait_for_function(
        "() => document.querySelectorAll('aggregate-trend .aggregate-ranking__item').length > 0"
    )
    assert mobile.locator('[data-page-region="trends"]').bounding_box()["width"] <= 390
    assert mobile.locator("aggregate-trend .aggregate-ranking").bounding_box()["width"] <= 390
    mobile.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
    browser.close()

    if console_errors or page_errors:
        raise AssertionError({"console_errors": console_errors, "page_errors": page_errors})

print("PASS: all chart views, aggregate trends, animated detail dialog, keyboard navigation, desktop and mobile layouts")
