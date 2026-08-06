"""Đăng nhập web Lark bằng account Jenny và lưu phiên (chạy trên Mac).

Dùng: ~/.jenny-nblm/bin/python scripts/lark_web_login.py
→ cửa sổ trình duyệt mở ra → đăng nhập account Jenny - BOD Assistant
→ khi vào được Lark, phiên tự lưu ~/.jenny-nblm/lark_web_state.json
→ copy file đó sang VPS: /opt/jenny/lark-web-auth/storage_state.json
"""
import asyncio
from pathlib import Path

OUT = Path.home() / ".jenny-nblm" / "lark_web_state.json"
START = "https://o4pvcegwn6b.sg.larksuite.com/minutes/me"


async def main() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, channel="chrome")
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(START)
        print("→ Đăng nhập Lark bằng account Jenny trong cửa sổ vừa mở...")
        for _ in range(600):  # chờ tối đa 10 phút
            await asyncio.sleep(1)
            url = page.url
            if "accounts." not in url and "login" not in url and "minutes" in url:
                break
        await asyncio.sleep(3)
        await ctx.storage_state(path=str(OUT))
        print(f"✓ Đã lưu phiên: {OUT}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
