"""Playwright adapter for confirmed ChatGPT image-generation jobs."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    payload = json.load(sys.stdin)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(json.dumps({"error": "未安装 playwright，请安装 requirements/browser.txt"}, ensure_ascii=False))
        return 2
    root = Path(os.getenv("DESIGN_RAG_DATA_ROOT", r"I:\design-ai-data"))
    profile = Path(os.getenv("DESIGN_RAG_CHATGPT_PROFILE", str(root / "chatgpt-browser-profile")))
    output_dir = root / "generated" / "ai-concept-images" / str(payload["project_id"])
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_selector = os.getenv("CHATGPT_PROMPT_SELECTOR", "textarea")
    send_selector = os.getenv("CHATGPT_SEND_SELECTOR", "button[data-testid='send-button']")
    image_selector = os.getenv("CHATGPT_GENERATED_IMAGE_SELECTOR", "img[src^='blob:'], img[src*='oaidalleapiprodscus']")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(str(profile), headless=False)
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto(os.getenv("CHATGPT_IMAGE_URL", "https://chatgpt.com/"), wait_until="domcontentloaded", timeout=120000)
        page.locator(prompt_selector).last.fill(str(payload["prompt"]))
        page.locator(send_selector).last.click()
        image = page.locator(image_selector).last
        image.wait_for(state="visible", timeout=1_800_000)
        output = output_dir / f"{payload['job_id']}.png"
        image.screenshot(path=str(output))
        browser.close()
    print(json.dumps({"output_path": str(output), "asset_type": "ai_concept_image"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
