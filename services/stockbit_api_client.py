import os
import tempfile
import time

import requests

from utils.logger_config import logger
from services.stockbit_token_fetcher import StockbitTokenFetcher


class StockbitApiClient:
    """
    Handles HTTP requests to the Stockbit API, including authentication and retries.
    """

    def __init__(self, auto_authenticate: bool = True):
        """
        Initializes the StockbitHttpRequest with a URL and optional headers.
        Authenticates with the Stockbit API upon initialization.

        Parameters:
        - auto_authenticate (bool): When True (default), validate/renew the token
          on construction. Set False for the interactive bootstrap so constructing
          the client does not trigger a login before ``bootstrap_login`` runs.
        """
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:137.0) Gecko/20100101 Firefox/137.0",
        }

        self.auto_authenticate = auto_authenticate

        self.is_authorise = False

        # Where token files live. Defaults to the system temp dir, but on a
        # server /tmp can be wiped on reboot, so allow a persistent, explicit
        # location via STOCKBIT_TOKEN_DIR.
        token_dir = os.environ.get("STOCKBIT_TOKEN_DIR") or tempfile.gettempdir()
        os.makedirs(token_dir, exist_ok=True)
        self.token_dir = token_dir

        # On a headless server (no Chrome) the browser login can never succeed.
        # Setting STOCKBIT_DISABLE_BROWSER_LOGIN makes the client rely purely on
        # the refresh token and fail with a clear, actionable message instead of
        # trying (and failing) to launch a browser.
        self.disable_browser_login = os.environ.get(
            "STOCKBIT_DISABLE_BROWSER_LOGIN", ""
        ).strip().lower() in ("1", "true", "yes")

        self.token_temp_file_path = os.path.join(token_dir, "stockbit_token.tmp")

        self.refresh_token_temp_file_path = os.path.join(
            token_dir, "stockbit_refresh_token.tmp"
        )

        self.ua_temp_file_path = os.path.join(token_dir, "stockbit_ua.tmp")

        self._initialize_token_file()

    def _request(self, url: str, method: str, payload: dict = None):
        """
        Makes an HTTP request with the specified method and payload, retrying on failure.

        Parameters:
        - method (str): The HTTP method ("GET" or "POST").
        - payload (dict): Optional payload for POST requests.

        Returns:
        - dict: The JSON response from the server, or an empty dictionary on failure.
        """
        retry = 0
        while retry <= 3:
            try:
                if method == "GET":
                    response = requests.get(url, headers=self.headers)
                elif method == "POST":
                    response = requests.post(url, headers=self.headers, json=payload)
                else:
                    raise ValueError("Unsupported HTTP method")

                logger.debug(url)
                logger.debug(response.status_code)
                logger.debug(response.json())

                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(
                        f"Error: Received status code {response.status_code}, "
                        f"text: {response.text}, "
                        f"retry: {retry}"
                    )
                    if response.status_code == 401:
                        self._authenticate_stockbit()
                        retry += 1
                    else:
                        break

            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e} retry: {retry}")
                break

            time.sleep(0.2)

        logger.error(f"Failed to retrieve key statistics retry: {retry}")
        return {}

    def get(self, url: str):
        """
        Performs a GET request using the stored URL and headers.

        Returns:
        - dict: The JSON response from the server, or an empty dictionary on failure.
        """
        return self._request(url, "GET")

    def post(self, url: str, payload: dict):
        """
        Performs a POST request using the stored URL, headers, and provided payload.

        Parameters:
        - payload (dict): The payload for the POST request.

        Returns:
        - dict: The JSON response from the server, or an empty dictionary on failure.
        """
        return self._request(url, "POST", payload)

    def bootstrap_login(self) -> bool:
        """
        Run the interactive browser login and persist the access token, refresh
        token and User-Agent to ``self.token_dir``.

        Intended for the one-time local bootstrap: run this on a machine with
        Chrome, then sync the resulting token files to the (browser-less) server.

        Returns:
            bool: True if a token was obtained, False otherwise.
        """
        self._login()
        return self.is_authorise

    def _authenticate_stockbit(self):
        """
        Authenticates with the Stockbit API and updates the authorization header.

        Prefer the browser-free refresh path whenever a refresh token is
        available (even on a fresh process); only fall back to an interactive
        browser login when no refresh token exists.
        """

        if not self._is_refresh_token_empty():
            self._refresh_token()
        else:
            self._login()

    def _login(self):
        """
        Login to Stockbit API via an interactive browser session.

        Requires a machine with Chrome. On headless servers this is disabled via
        STOCKBIT_DISABLE_BROWSER_LOGIN; there, an expired/invalid refresh token
        is a hard error that requires re-running the local bootstrap.
        """
        if self.disable_browser_login:
            logger.error(
                "Browser login is disabled (STOCKBIT_DISABLE_BROWSER_LOGIN) and no valid "
                "refresh token is available. Re-run the local bootstrap "
                "(`uv run python main.py --stockbit-login`) on a machine with Chrome and "
                f"sync the token files in {self.token_dir} to this host."
            )
            self.is_authorise = False
            return

        self.headers["Authorization"] = None

        token = None
        refresh_token = None
        user_agent = None

        fetcher = None
        try:
            fetcher = StockbitTokenFetcher()
            token, refresh_token, user_agent = fetcher.fetch_tokens()
        except Exception as e:
            logger.error(f"Failed to fetch tokens via StockbitTokenFetcher: {e}")
        finally:
            if fetcher is not None:
                try:
                    fetcher.close()
                except Exception:
                    pass

        if token:
            logger.info("Logged in successfully via StockbitTokenFetcher!")
            self.headers["Authorization"] = f"Bearer {token}"

            if user_agent:
                self.headers["User-Agent"] = user_agent
                logger.info(f"Updated User-Agent to: {user_agent}")

            self._write_token(token, refresh_token or "", user_agent)
            self.is_authorise = True
        else:
            logger.error("Failed to log in via StockbitTokenFetcher.")
            self.is_authorise = False

        time.sleep(1)

    def _refresh_token(self):
        """
        Refreshes new token using refresh token.
        """
        url = "https://exodus.stockbit.com/login/refresh"

        with open(self.refresh_token_temp_file_path, "r") as file:
            self.headers["Authorization"] = f"Bearer {file.read()}"

            try:
                response = requests.post(url, headers=self.headers)

                if response.status_code == 200:
                    logger.info("Token is successfully refreshed!")

                    token = response.json()["data"]["access"]["token"]
                    refresh_token = response.json()["data"]["refresh"]["token"]

                    self.headers["Authorization"] = f"Bearer {token}"

                    self._write_token(token, refresh_token)

                    self.is_authorise = True
                else:
                    logger.error(
                        f"Error: Received status code {response.status_code} - {response.text}"
                    )
                    self._login()

                time.sleep(1)

            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")

    def _write_token(self, token, refresh_token, user_agent=None):
        """
        Write tokens to temporary file.
        :param token:
        :param refresh_token:
        :param user_agent:
        :return:
        """
        with open(self.token_temp_file_path, "w") as file:
            file.write(token)

        with open(self.refresh_token_temp_file_path, "w") as file:
            file.write(refresh_token)

        if user_agent:
            with open(self.ua_temp_file_path, "w") as file:
                file.write(user_agent)

    def _initialize_token_file(self):
        """
        Intialize token files
        :return:
        """
        try:
            with open(self.refresh_token_temp_file_path, "r") as file:
                file.read()
        except FileNotFoundError:
            with open(self.refresh_token_temp_file_path, "w") as file:
                file.write("")

        try:
            with open(self.ua_temp_file_path, "r") as file:
                ua = file.read()
                if ua != "":
                    self.headers["User-Agent"] = ua
        except FileNotFoundError:
            pass

        try:
            with open(self.token_temp_file_path, "r") as file:
                token = file.read()
                logger.debug(f"Token: {token}")
                if token != "":
                    self.headers["Authorization"] = f"Bearer {token}"

                if self.auto_authenticate:
                    self._request_challenge()
        except FileNotFoundError:
            with open(self.token_temp_file_path, "w") as file:
                file.write("")

    def _is_refresh_token_empty(self) -> bool:
        """
        Check if token is empty.
        :return: boolean
        """
        try:
            with open(os.path.join(self.refresh_token_temp_file_path), "r") as file:
                token = file.read()
                return token == ""
        except FileNotFoundError:
            return False

    def _request_challenge(self):
        """
        Check expired token by request to light API
        :return:
        """
        try:
            response = requests.get(
                "https://exodus.stockbit.com/research/indicator/new",
                headers=self.headers,
            )

            if response.status_code != 200:
                logger.error(
                    f"Error: Received status code {response.status_code} - {response.text}"
                )
                self._authenticate_stockbit()
            else:
                logger.info("Logged in successfully with existing token!")

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
