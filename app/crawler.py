from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import Browser, async_playwright

from app.config import settings
from app.metrics import TopPage


@dataclass
class PageArtifact:
    url: str
    visits: int
    title: str
    desktop_screenshot: str
    mobile_screenshot: str
    text_excerpt: str


def _extract_text(html: str, max_chars: int = 5000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:max_chars]


async def _capture_page(browser: Browser, url: str, viewport: dict[str, int], screenshot_path: Path) -> tuple[str, str]:
    context = await browser.new_context(viewport=viewport)
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(1000)
    html = await page.content()
    title = await page.title()
    await page.screenshot(path=str(screenshot_path), full_page=True)
    await context.close()
    return title, _extract_text(html)


async def collect_page_artifacts(top_pages: list[TopPage], output_dir: Path) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[PageArtifact] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.headless)
        try:
            for idx, page_data in enumerate(top_pages, start=1):
                safe_name = f"{idx:02d}" 
                desktop_path = output_dir / f"{safe_name}_desktop.png"
                mobile_path = output_dir / f"{safe_name}_mobile.png"
                title, text = "", ""

                try:
                    title, text = await _capture_page(
                        browser,
                        page_data.url,
                        {"width": 1920, "height": 1080},
                        desktop_path,
                    )
                    _, _ = await _capture_page(
                        browser,
                        page_data.url,
                        {"width": 390, "height": 844},
                        mobile_path,
                    )
                except Exception as exc:  # noqa: BLE001
                    title = f"ERROR: {exc}"
                    text = ""

                artifacts.append(
                    PageArtifact(
                        url=page_data.url,
                        visits=page_data.visits,
                        title=title,
                        desktop_screenshot=str(desktop_path),
                        mobile_screenshot=str(mobile_path),
                        text_excerpt=text,
                    )
                )
        finally:
            await browser.close()

    return [asdict(item) for item in artifacts]
