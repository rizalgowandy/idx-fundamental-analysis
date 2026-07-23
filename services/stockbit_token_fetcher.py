import json
import logging
import os
import re
import subprocess
import tempfile

import undetected_chromedriver as uc
from utils.logger_config import logger


def _detect_chrome_major_version():
    """
    Return the major version of the locally installed Chrome, or None if it
    cannot be determined.

    undetected-chromedriver otherwise downloads the *latest* ChromeDriver, which
    fails with SessionNotCreatedException when it does not match the installed
    Chrome (e.g. driver for Chrome 151 vs. an installed Chrome 150).
    """
    try:
        chrome_path = uc.find_chrome_executable()
        if not chrome_path:
            return None
        output = subprocess.check_output(
            [chrome_path, "--version"],
            text=True,
            stderr=subprocess.STDOUT,
        )
        match = re.search(r"(\d+)\.\d+\.\d+", output)
        if match:
            return int(match.group(1))
    except Exception:
        return None
    return None

# Suppress noisy logs
for _name in (
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.remote.remote_connection",
    "urllib3",
):
    logging.getLogger(_name).setLevel(logging.WARNING)


class StockbitTokenFetcher:
    def __init__(self):
        self.login_url = "https://stockbit.com/login"
        self.sample_url = "exodus.stockbit.com/chat/v2/rooms/unread/count"

        profile_dir = os.path.join(
            os.path.expanduser("~"), ".idx-fundamental-stockbit-profile"
        )
        os.makedirs(profile_dir, exist_ok=True)

        options = uc.ChromeOptions()
        options.add_argument(f"--user-data-dir={profile_dir}")
        # options.add_argument(f"--disk-cache-dir={cache_dir}") # UC handles profile better without explicit cache dir split sometimes, but keeping user-data-dir is key.

        # Enable performance logging to capture headers
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        # Initialize undetected-chromedriver
        # headless=False is important for manual login
        # Pin the driver to the installed Chrome major version so uc downloads a
        # matching ChromeDriver instead of the latest, avoiding
        # SessionNotCreatedException on version mismatch.
        chrome_version = _detect_chrome_major_version()
        if chrome_version is not None:
            logger.info(f"Detected installed Chrome major version: {chrome_version}")

        self.driver = uc.Chrome(
            options=options,
            headless=False,
            use_subprocess=True,
            version_main=chrome_version,
        )

        tmp_dir = tempfile.gettempdir()
        self.token_path = os.path.join(tmp_dir, "stockbit_token.tmp")

    def fetch_tokens(self):
        driver = self.driver
        logger.info("Navigating to Stockbit login page...")
        driver.get(self.login_url)

        logger.info("Please log in to Stockbit in the opened browser.")
        input("Press Enter here AFTER login succeeds and the dashboard loads... ")

        # Scan performance logs for the sample request
        logs = driver.get_log("performance")

        access_token = None

        # We look for Network.requestWillBeSent events
        for entry in logs:
            try:
                message = json.loads(entry["message"])
                method = message.get("message", {}).get("method")
                if method == "Network.requestWillBeSent":
                    params = message["message"]["params"]
                    request = params.get("request", {})
                    url = request.get("url", "")

                    if self.sample_url in url:
                        headers = request.get("headers", {})
                        # Headers keys can be case-sensitive or not depending on browser version, usually title-cased or lowercase.
                        # We check both.
                        auth_header = headers.get("Authorization") or headers.get(
                            "authorization"
                        )

                        if auth_header and auth_header.startswith("Bearer "):
                            access_token = auth_header.split(" ", 1)[1]
                            # Don't break, keep looking for the LATEST token in the logs
            except (KeyError, json.JSONDecodeError):
                continue

        if not access_token:
            logger.error(
                "Could not find Bearer token in captured requests. Make sure the page finished loading."
            )
            return None, None

        # Capture the User-Agent used by the browser
        user_agent = driver.execute_script("return navigator.userAgent;")
        logger.info(f"User-Agent captured: {user_agent}")

        logger.info("Access token captured.")

        with open(self.token_path, "w") as f:
            f.write(access_token)

        logger.info(f"Tokens written to: {self.token_path}")

        return access_token, user_agent

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass
