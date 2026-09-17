import unittest
from types import SimpleNamespace

from unittest.mock import patch

from app.services.instance_resolution import (
    _attach_solidset_instance,
    _request_ip_details,
    _resolve_request_solidset_instance,
    clear_instance_cache,
)


class RequestIpLoggingTests(unittest.TestCase):
    def setUp(self):
        clear_instance_cache()

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

    @patch("app.services.instance_resolution.get_solidset_instance")
    def test_explicit_instance_header_has_precedence_over_source_ip(self, lookup):
        lookup.return_value = {"Code": "plant-a"}
        request = SimpleNamespace(
            client=SimpleNamespace(host="10.0.0.8"),
            headers={"x-solidset-instance": "plant-a"},
        )

        result = _resolve_request_solidset_instance(request)

        self.assertEqual(result["Code"], "plant-a")
        lookup.assert_called_once_with(code="plant-a", source_ip=None)

    @patch("app.services.instance_resolution.list_active_solidset_instances")
    @patch("app.services.instance_resolution.get_solidset_instance")
    def test_missing_header_does_not_identify_instance_by_inbound_ip(self, lookup, instances):
        instances.return_value = [{"Code": "plant-a"}, {"Code": "plant-b"}]
        request = SimpleNamespace(
            client=SimpleNamespace(host="10.0.0.8"),
            headers={"x-forwarded-for": "203.0.113.25"},
            url=SimpleNamespace(hostname="plant-a.example"),
        )

        self.assertIsNone(_resolve_request_solidset_instance(request))
        lookup.assert_not_called()

    @patch("app.services.instance_resolution.list_active_solidset_instances")
    def test_missing_header_uses_only_active_instance(self, instances):
        instances.return_value = [{"Code": "plant-a"}]
        request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"), headers={})

        self.assertEqual(_resolve_request_solidset_instance(request)["Code"], "plant-a")

    @patch("app.services.instance_resolution.list_active_solidset_instances")
    @patch("app.services.instance_resolution.get_solidset_instance")
    def test_unknown_header_does_not_fall_back_to_only_instance(self, lookup, instances):
        lookup.return_value = None
        request = SimpleNamespace(headers={"x-solidset-instance": "unknown"})

        self.assertIsNone(_resolve_request_solidset_instance(request))
        instances.assert_not_called()

    def test_candidate_is_namespaced_and_receives_response_url(self):
        candidates = [{"fingerprint": "same-message"}]
        instance = {
            "ID": "instance-id",
            "Code": "plant-a",
            "BaseUrl": "http://10.0.0.8:52130/",
            "NotificationUrl": "http://10.0.0.8:52131/",
        }

        _attach_solidset_instance(candidates, instance)

        self.assertEqual(candidates[0]["fingerprint"], "instance-id:same-message")
        self.assertEqual(candidates[0]["solidset_base_url"], "http://10.0.0.8:52130")


if __name__ == "__main__":
    unittest.main()
