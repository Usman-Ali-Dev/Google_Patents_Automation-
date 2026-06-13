import asyncio
import json
import random
import os
from datetime import datetime
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfs")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

USER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_profile")
os.makedirs(USER_DATA_DIR, exist_ok=True)

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress.json")


def load_progress():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return {"page_num": 0, "article_index": 0, "completed": False, "downloads": []}


def save_progress(progress):
    with open(LOG_FILE, "w") as f:
        json.dump(progress, f, indent=2)


async def human_delay(min_ms=50, max_ms=200):
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def human_type(element, text, min_delay=60, max_delay=180):
    for char in text:
        await element.type(char, delay=random.randint(min_delay, max_delay))
        if random.random() < 0.15:
            await asyncio.sleep(random.uniform(0.1, 0.4))


async def human_move(page, x, y):
    steps = random.randint(5, 12)
    for i in range(steps):
        nx = x * (i / steps) + random.randint(-5, 5)
        ny = y * (i / steps) + random.randint(-5, 5)
        await page.mouse.move(nx, ny)
        await asyncio.sleep(random.uniform(0.01, 0.03))


async def navigate_to_patents(page):
    await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=90000)
    await asyncio.sleep(random.uniform(2, 4))

    consent_btn = await page.query_selector("button[id='L2AGLb'], button[aria-label*='Accept'], button[aria-label*='agree']")
    if consent_btn:
        box = await consent_btn.bounding_box()
        if box:
            await human_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await consent_btn.click()
        await asyncio.sleep(random.uniform(1, 2))

    search_box = await page.wait_for_selector("textarea[name='q'], input[name='q']", timeout=15000)
    box = await search_box.bounding_box()
    if box:
        await human_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await search_box.click()
    await human_delay(300, 600)
    await human_type(search_box, "google patents")
    await human_delay(500, 1000)
    await page.keyboard.press("Enter")

    await page.wait_for_load_state("networkidle", timeout=90000)
    await asyncio.sleep(random.uniform(3, 5))

    await page.evaluate("window.scrollBy(0, 200)")
    await asyncio.sleep(random.uniform(1, 2))

    patents_link = await page.query_selector("a[href*='patents.google.com']")
    if patents_link:
        box = await patents_link.bounding_box()
        if box:
            await human_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await patents_link.click()
        await asyncio.sleep(1)
    else:
        print("No link found, going direct")
        await page.goto("https://patents.google.com/", wait_until="domcontentloaded", timeout=90000)

    await page.wait_for_load_state("networkidle", timeout=90000)
    await asyncio.sleep(random.uniform(3, 5))


async def search_patents(page):
    search_input = await page.wait_for_selector("input[name='q'], input[aria-label*='Search']", timeout=15000)
    box = await search_input.bounding_box()
    if box:
        await human_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await search_input.click()
    await human_delay(300, 600)
    await search_input.fill("")
    await human_type(search_input, "(patents) country:US")
    await human_delay(500, 1200)
    await page.keyboard.press("Enter")
    await page.wait_for_load_state("networkidle", timeout=90000)
    await asyncio.sleep(random.uniform(3, 5))


async def download_patent_pdf(context, patent_url, progress):
    filename_guess = patent_url.split("/")[-2] + ".pdf"
    already_downloaded = any(d["url"] == patent_url for d in progress["downloads"])
    if already_downloaded:
        print(f"Already downloaded: {patent_url}")
        return True

    new_page = await context.new_page()
    try:
        await new_page.goto(patent_url, wait_until="load", timeout=90000)
        await asyncio.sleep(random.uniform(2, 4))

        pdf_link = await new_page.wait_for_selector("a.style-scope.patent-result[href$='.pdf']", timeout=60000)
        pdf_url = await pdf_link.get_attribute("href")
        if pdf_url:
            filename = pdf_url.split("/")[-1]
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            resp = await new_page.request.get(pdf_url)
            with open(filepath, "wb") as f:
                f.write(await resp.body())
            print(f"Downloaded: {filename}")
            progress["downloads"].append({"url": patent_url, "filename": filename, "time": datetime.now().isoformat()})
            save_progress(progress)
            return True
    except Exception as e:
        print(f"Failed on {patent_url}: {e}")
        progress["downloads"].append({"url": patent_url, "filename": None, "time": datetime.now().isoformat(), "error": str(e)})
        save_progress(progress)
        return False
    finally:
        await new_page.close()


async def process_page(page, context, page_num, article_start_index, progress):
    if page_num == 0:
        await search_patents(page)
    else:
        url = f"https://patents.google.com/?q=(patents)&country=US&page={page_num}"
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_load_state("networkidle", timeout=90000)
        await asyncio.sleep(random.uniform(2, 4))

    try:
        await page.wait_for_selector("article.result", timeout=60000)
    except Exception:
        content = await page.content()
        print(f"Page {page_num} - no articles loaded")
        return False, 0

    articles = await page.query_selector_all("article.result")
    if not articles:
        print(f"No articles found on page {page_num}")
        return False, 0

    print(f"Page {page_num}: Found {len(articles)} articles, starting from index {article_start_index}")

    for i in range(article_start_index, len(articles)):
        article = articles[i]
        state_mod = await article.query_selector("state-modifier")
        if not state_mod:
            continue

        data_result = await state_mod.get_attribute("data-result")
        if data_result:
            patent_url = f"https://patents.google.com/{data_result}"
            await download_patent_pdf(context, patent_url, progress)

        progress["page_num"] = page_num
        progress["article_index"] = i + 1
        save_progress(progress)

    return True, len(articles)


async def main():
    progress = load_progress()

    if progress["completed"]:
        print("All pages already completed. Delete progress.json to restart.")
        return

    stealth = Stealth(
        navigator_webdriver=True,
        navigator_plugins=True,
        navigator_platform=True,
        chrome_app=True,
        chrome_csi=True,
        chrome_load_times=True,
        chrome_runtime=True,
        navigator_user_agent=True,
        navigator_vendor=True,
        webgl_vendor=True,
        iframe_content_window=True,
        media_codecs=True,
        navigator_hardware_concurrency=True,
        navigator_languages=True,
        navigator_permissions=True,
        hairline=True,
        sec_ch_ua=True,
        navigator_user_agent_data=True,
        error_prototype=True,
    )
    async with stealth.use_async(async_playwright()) as p:
        context = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--lang=en-US,en",
            ],
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York",
            accept_downloads=True,
            ignore_default_args=["--enable-automation"],
        )

        page = context.pages[0] if context.pages else await context.new_page()

        start_page = progress["page_num"]
        start_article = progress["article_index"]

        if start_page == 0:
            await navigate_to_patents(page)

        page_num = start_page
        article_start = start_article if page_num == start_page else 0

        while True:
            found, count = await process_page(page, context, page_num, article_start, progress)
            if not found:
                break
            page_num += 1
            article_start = 0

        progress["completed"] = True
        save_progress(progress)
        print("All pages completed!")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())