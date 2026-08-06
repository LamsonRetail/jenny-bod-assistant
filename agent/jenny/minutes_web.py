"""Lấy transcript từ trang Lark Minutes bằng trình duyệt headless (account Jenny).

Lý do: Lark chặn API tải bản ghi/transcript cho cả user token ("Export minutes"
cannot be granted) lẫn tenant token (per-object deny) — nhưng account Jenny XEM
được trang Minutes. Phiên đăng nhập web lưu ở LARK_WEB_STATE (storage_state.json,
tạo trên Mac bằng scripts/lark_web_login.py rồi copy sang VPS).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

STATE_PATH = os.environ.get("LARK_WEB_STATE", "/opt/jenny/lark-web-auth/storage_state.json")
PAGE_TIMEOUT_MS = 90_000


async def fetch_transcript(minutes_url: str) -> str:
    """Mở trang Minutes, chờ transcript render, cuộn hết và trích text."""
    if not Path(STATE_PATH).exists():
        raise RuntimeError("Chưa có phiên web Lark của Jenny trên VPS "
                           "(chạy scripts/lark_web_login.py rồi copy storage_state)")
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=STATE_PATH,
                                        viewport={"width": 1440, "height": 2400})
        page = await ctx.new_page()
        try:
            await page.goto(minutes_url, timeout=PAGE_TIMEOUT_MS,
                            wait_until="domcontentloaded")
            # chờ nội dung transcript xuất hiện (đoạn văn bản có timestamp)
            await page.wait_for_timeout(8000)
            if "accounts." in page.url or "/login" in page.url:
                raise RuntimeError("Phiên web Lark hết hạn — cần đăng nhập lại")

            # cuộn để nạp hết transcript (danh sách ảo hóa)
            prev_len, stable = 0, 0
            for _ in range(120):
                text = await page.evaluate("document.body.innerText")
                if len(text) <= prev_len:
                    stable += 1
                    if stable >= 4:
                        break
                else:
                    stable = 0
                prev_len = len(text)
                await page.keyboard.press("End")
                await page.mouse.wheel(0, 4000)
                await page.wait_for_timeout(1200)

            text = await page.evaluate("document.body.innerText")
            transcript = _clean(text)
            if len(transcript) < 200:
                raise RuntimeError(f"Trang không có transcript đọc được "
                                   f"(chỉ thấy {len(transcript)} ký tự)")
            log.info("Minutes web: lấy được %d ký tự transcript", len(transcript))
            return transcript
        finally:
            await browser.close()


def _clean(body_text: str) -> str:
    """Lọc phần transcript từ innerText của trang (bỏ menu/nav)."""
    lines = [ln.strip() for ln in body_text.splitlines()]
    out, started = [], False
    import re
    ts = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
    for i, ln in enumerate(lines):
        if not ln:
            continue
        if ts.match(ln):
            started = True
        if started:
            out.append(ln)
    return "\n".join(out) if out else "\n".join(l for l in lines if l)
