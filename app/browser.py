"""One isolated UC job. Invoked by the browser worker, never by the agent directly."""
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def create_driver(directory):
    options = uc.ChromeOptions()
    for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1920,1080", "--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--proxy-bypass-list=<-loopback>"):
        options.add_argument(arg)
    options.add_argument("--proxy-server=" + os.getenv("BROWSER_PROXY", "http://egress:8080"))
    options.page_load_strategy = "eager"
    options.add_experimental_option("prefs", {"download_restrictions": 3, "profile.default_content_setting_values.notifications": 2})
    # UC patches this private, writable copy. No runtime download or shared binary race.
    driver_path = str(Path(directory) / "chromedriver")
    shutil.copy2(os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver"), driver_path)
    return uc.Chrome(options=options, version_main=152,
                     driver_executable_path=driver_path,
                     browser_executable_path=os.getenv("CHROME_BINARY", "/usr/bin/google-chrome"),
                     user_data_dir=str(Path(directory) / "profile"), use_subprocess=True)


def scrape_page(payload, directory):
    driver = None
    started = time.monotonic()
    try:
        driver = create_driver(directory)
        driver.set_page_load_timeout(payload.timeout_seconds)
        driver.set_script_timeout(10)
        driver.get(payload.url)
        if payload.wait_css:
            WebDriverWait(driver, payload.timeout_seconds).until(EC.presence_of_element_located((By.CSS_SELECTOR, payload.wait_css)))
        time.sleep(payload.wait_seconds)
        html = driver.page_source
        return {"url": payload.url, "final_url": driver.current_url, "title": driver.title,
                "html": html, "html_bytes": len(html.encode("utf-8")),
                "browser_version": driver.capabilities.get("browserVersion"),
                "browser_backend": "undetected-chromedriver",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.monotonic() - started) * 1000)}
    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException:
                pass  # Worker always terminates the entire job process group.


def main():
    from app.main import MAX_RESPONSE_BYTES, ScrapeRequest
    try:
        payload = ScrapeRequest.model_validate_json(sys.stdin.buffer.read(16_385))
        result = scrape_page(payload, os.environ["BROWSER_JOB_DIR"])
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = b'{"error":"response_too_large"}'
    except TimeoutException:
        encoded = b'{"error":"browser_timeout"}'
    except Exception:
        encoded = b'{"error":"browser_error"}'
    sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    main()
