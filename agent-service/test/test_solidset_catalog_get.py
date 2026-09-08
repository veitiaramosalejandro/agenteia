import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.controllers import synchronization as controller
from app.connectors import solidset_data_api as connector


class CatalogGetTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(controller.router)
        self.client = TestClient(app)

    def test_routes_select_instance_and_forward_pagination(self):
        configuration = {'active': True, 'BaseUrl': 'http://data-api'}
        page = dict(rows=[], rowCount=0, offset=20, limit=10, hasMore=False, nextOffset=None)
        for dataset in ('resources', 'workrooms'):
            with self.subTest(dataset=dataset), patch.object(controller, 'get_solidset_instance',
                    return_value={'Code': 'example', 'DataAPI': configuration}) as lookup, \
                    patch.object(controller, 'read_catalog_page', return_value=page) as read:
                response = self.client.get(f'/api/v1/agent/solidset/{dataset}',
                    params={'instanceCode': 'example', 'offset': 20, 'limit': 10})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), dict(instanceCode='example', **page))
                lookup.assert_called_once_with(code='example', source_ip=None)
                read.assert_called_once_with(configuration, dataset, offset=20, limit=10)

    def test_bad_pagination_and_missing_instance_are_rejected_before_lookup(self):
        for params in ({}, {'instanceCode': ' '}, {'instanceCode': 'x', 'offset': -1},
                       {'instanceCode': 'x', 'limit': 0}, {'instanceCode': 'x', 'limit': 1001}):
            with self.subTest(params=params), patch.object(controller, 'get_solidset_instance') as lookup:
                self.assertEqual(self.client.get('/api/v1/agent/solidset/resources', params=params).status_code, 422)
                lookup.assert_not_called()

    def test_missing_instance_and_inactive_gateway(self):
        for instance, expected in ((None, 404), ({'Code': 'x', 'DataAPI': {'active': False}}, 503)):
            with patch.object(controller, 'get_solidset_instance', return_value=instance), \
                    patch.object(controller, 'read_catalog_page') as read:
                response = self.client.get('/api/v1/agent/solidset/workrooms', params={'instanceCode': 'x'})
                self.assertEqual(response.status_code, expected)
                read.assert_not_called()

    def test_gateway_error_is_sanitized(self):
        with patch.object(controller, 'get_solidset_instance', return_value={
                'Code': 'x', 'DataAPI': {'active': True, 'BaseUrl': 'http://data'}}), \
                patch.object(controller, 'read_catalog_page', side_effect=connector.SolidSETDataAPIError('private key')):
            response = self.client.get('/api/v1/agent/solidset/resources', params={'instanceCode': 'x'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private key', response.text)

    def test_connector_reads_only_one_page_and_excludes_extra_fields(self):
        connection = Mock(max_rows=50)
        connection.client.get.return_value.json.return_value = {
            'rows': [{'ResourceId': 'id', 'DisplayName': 'Name', 'Password': 'secret'}],
            'limit': 1, 'hasMore': True, 'nextOffset': 6,
        }
        with patch.object(connector, 'DataAPIConnection') as factory:
            factory.return_value.__enter__.return_value = connection
            result = connector.read_catalog_page({}, 'resources', offset=5, limit=1)
        connection.client.get.assert_called_once_with('/api/v1/datasets/resources', params={'offset': 5, 'limit': 1})
        self.assertEqual(result['nextOffset'], 6)
        self.assertNotIn('Password', result['rows'][0])

    def test_connector_rejects_malformed_page(self):
        connection = Mock(max_rows=50)
        connection.client.get.return_value.json.return_value = {'rows': [], 'hasMore': True}
        with patch.object(connector, 'DataAPIConnection') as factory:
            factory.return_value.__enter__.return_value = connection
            with self.assertRaises(connector.SolidSETDataAPIError):
                connector.read_catalog_page({}, 'workrooms', offset=0, limit=10)


if __name__ == '__main__':
    unittest.main()
