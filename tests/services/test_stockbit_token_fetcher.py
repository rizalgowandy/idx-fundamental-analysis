import unittest

from services.stockbit_token_fetcher import StockbitTokenFetcher


class TestStockbitTokenFetcherReal(unittest.TestCase):
    def test_fetch_tokens_interactive(self):
        """Integration-style test that opens a real browser and requires manual login."""

        fetcher = StockbitTokenFetcher()
        access_token = None

        try:
            access_token, refresh_token, user_agent = fetcher.fetch_tokens()
        finally:
            fetcher.close()

        self.assertIsInstance(access_token, str)
        self.assertNotEqual(access_token, "")
        self.assertIsInstance(user_agent, str)
        self.assertNotEqual(user_agent, "")
        # refresh_token may be None if Stockbit changes where it stores the token;
        # when present it must be a non-empty string.
        if refresh_token is not None:
            self.assertIsInstance(refresh_token, str)
            self.assertNotEqual(refresh_token, "")


if __name__ == "__main__":
    unittest.main()
