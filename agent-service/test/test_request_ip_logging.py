import unittest
import json
from types import SimpleNamespace

from unittest.mock import MagicMock, patch

from app.services.instance_resolution import (
    _attach_solidset_instance,
    _request_ip_details,
    _resolve_request_solidset_instance,
    _solidset_header_host,
)
from app.connectors.db_client import get_solidset_instance
from app.api.controllers.notifications import _trace_initial_request, _trace_resolved_instance


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

    @patch("app.services.instance_resolution.get_solidset_instance")
    def test_header_host_looks_up_source_ip(self, lookup):
        lookup.return_value = {"Code": "plant-a"}
        request = SimpleNamespace(
            client=SimpleNamespace(host="10.0.0.8"),
            headers={"x-solidset-instance": "PLANT-A.EXAMPLE:52130"},
        )

        result = _resolve_request_solidset_instance(request)

        self.assertEqual(result["Code"], "plant-a")
        lookup.assert_called_once_with(source_ip="plant-a.example")

    @patch("app.services.instance_resolution.get_solidset_instance")
    def test_missing_header_does_not_identify_instance_by_inbound_ip(self, lookup):
        request = SimpleNamespace(
            client=SimpleNamespace(host="10.0.0.8"),
            headers={"x-forwarded-for": "203.0.113.25"},
            url=SimpleNamespace(hostname="plant-a.example"),
        )

        self.assertIsNone(_resolve_request_solidset_instance(request))
        lookup.assert_not_called()

    @patch("app.services.instance_resolution.get_solidset_instance")
    def test_missing_header_rejects_even_a_single_active_instance(self, lookup):
        request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.8"), headers={})

        self.assertIsNone(_resolve_request_solidset_instance(request))
        lookup.assert_not_called()

    @patch("app.services.instance_resolution.get_solidset_instance")
    def test_unknown_header_does_not_fall_back_to_only_instance(self, lookup):
        lookup.return_value = None
        request = SimpleNamespace(headers={"x-solidset-instance": "unknown.example"})

        self.assertIsNone(_resolve_request_solidset_instance(request))
        lookup.assert_called_once_with(source_ip="unknown.example")

    def test_header_host_rejects_url_credentials_and_path(self):
        self.assertIsNone(_solidset_header_host("https://plant-a.example"))
        self.assertIsNone(_solidset_header_host("user@plant-a.example"))
        self.assertIsNone(_solidset_header_host("plant-a.example/path"))
        self.assertEqual(_solidset_header_host("PLANT-A.EXAMPLE."), "plant-a.example")

    @patch("app.services.instance_resolution.get_solidset_instance")
    def test_loopback_header_variants_use_same_instance_lookup(self, lookup):
        lookup.return_value = {"Code": "local", "SourceIP": "127.0.0.1"}
        for value in ("localhost", "127.0.0.1", "[::1]", "LOCALHOST:52130"):
            with self.subTest(header=value):
                request = SimpleNamespace(headers={"x-solidset-instance": value})
                self.assertEqual(_resolve_request_solidset_instance(request)["Code"], "local")
                lookup.assert_called_with(source_ip="localhost")

    @patch("app.connectors.db_client.ensure_solidset_instance_location_schema")
    @patch("app.connectors.db_client._postgres_connection")
    @patch("builtins.print")
    def test_duplicate_source_ip_does_not_select_an_instance(self, output, connection, _schema):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"Code": "plant-a"}, {"Code": "plant-b"}]
        connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

        result = get_solidset_instance(source_ip="shared.example")

        self.assertIsNone(result)
        self.assertEqual(cursor.execute.call_args.args[1], (True, "shared.example"))
        self.assertIn("varias instancias", output.call_args.args[0])

    @patch("app.connectors.db_client.ensure_solidset_instance_location_schema")
    @patch("app.connectors.db_client._postgres_connection")
    def test_single_source_ip_returns_its_instance(self, connection, _schema):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"ID": "instance-id", "Code": "plant-a"}]
        cursor.fetchone.return_value = {"BaseUrl": "http://data-api"}
        connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

        result = get_solidset_instance(source_ip="plant-a.example")

        self.assertEqual(result["Code"], "plant-a")
        self.assertEqual(result["DataAPI"]["BaseUrl"], "http://data-api")
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("'127.0.0.1', '::1'", cursor.execute.call_args_list[0].args[0])

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

    @patch("builtins.print")
    def test_initial_trace_shows_header_session_and_sender_without_credentials(self, output):
        request = SimpleNamespace(
            client=SimpleNamespace(host="10.0.0.8"),
            headers={
                "x-solidset-instance": "beta-solidset",
                "x-forwarded-for": "203.0.113.25",
                "authorization": "Bearer secret-value",
                "cookie": "session=secret-cookie",
            },
        )
        payload = {
            "sender": {"session": "session-guid", "login": "login-guid", "resource": "resource-guid"},
            "Destiny": {"session": "destiny-guid"},
            "info": {"session_id": "logical-guid"},
            "chat": {"idChat2": 42},
            "RawMessage": "private question",
        }

        _trace_initial_request(request, payload)

        line = output.call_args.args[0]
        data = json.loads(line.removeprefix("📨 INITIAL_REQUEST_IDENTITY "))
        self.assertEqual(data["headers"]["x-solidset-instance"], "beta-solidset")
        self.assertEqual(data["session"]["sender"], "session-guid")
        self.assertEqual(data["sender"]["login"], "login-guid")
        self.assertEqual(data["chat_id"], "42")
        self.assertNotIn("secret-value", line)
        self.assertNotIn("secret-cookie", line)
        self.assertNotIn("private question", line)

    @patch("builtins.print")
    def test_destination_trace_shows_response_target(self, output):
        _trace_resolved_instance({
            "Code": "beta-solidset",
            "SourceIP": "192.0.2.10",
            "BaseUrl": "https://user:secret@solidset.example:52130/private?token=secret",
            "DataAPI": {"BaseUrl": "https://key:secret@data.example:8080/path", "active": True},
        })

        line = output.call_args.args[0]
        data = json.loads(line.removeprefix("📨 INITIAL_REQUEST_DESTINATION "))
        self.assertEqual(data["code"], "beta-solidset")
        self.assertEqual(data["source_ip"], "192.0.2.10")
        self.assertEqual(data["base_url_origin"], "https://solidset.example:52130")
        self.assertEqual(data["data_api_origin"], "https://data.example:8080")
        self.assertTrue(data["data_api_active"])
        self.assertNotIn("secret", line)


if __name__ == "__main__":
    unittest.main()
