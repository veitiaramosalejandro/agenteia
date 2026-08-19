import unittest
from types import SimpleNamespace

from app.main import _request_ip_details


class RequestIpLoggingTests(unittest.TestCase):
    def test_keeps_direct_and_forwarded_ips_separate(self):
        request = SimpleNamespace(
            client=SimpleNamespace(host="10.0.0.8"),
            headers={"x-forwarded-for": "203.0.113.25, 10.0.0.2"},
        )

        direct, forwarded = _request_ip_details(request)

        self.assertEqual(direct, "10.0.0.8")
        self.assertEqual(forwarded, "203.0.113.25")

    def test_supports_request_without_proxy_headers(self):
        request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={},
        )

        direct, forwarded = _request_ip_details(request)

        self.assertEqual(direct, "127.0.0.1")
        self.assertEqual(forwarded, "-")


if __name__ == "__main__":
    unittest.main()
