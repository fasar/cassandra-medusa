# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import MagicMock, patch

from medusa.config import StorageConfig, _namedtuple_from_dict
from medusa.storage import Storage


class ProxyConfigTest(unittest.TestCase):
    def setUp(self):
        self.original_http_proxy = os.environ.get('HTTP_PROXY')
        self.original_https_proxy = os.environ.get('HTTPS_PROXY')
        if 'HTTP_PROXY' in os.environ:
            del os.environ['HTTP_PROXY']
        if 'HTTPS_PROXY' in os.environ:
            del os.environ['HTTPS_PROXY']

    def tearDown(self):
        if self.original_http_proxy:
            os.environ['HTTP_PROXY'] = self.original_http_proxy
        elif 'HTTP_PROXY' in os.environ:
            del os.environ['HTTP_PROXY']

        if self.original_https_proxy:
            os.environ['HTTPS_PROXY'] = self.original_https_proxy
        elif 'HTTPS_PROXY' in os.environ:
            del os.environ['HTTPS_PROXY']

    def _get_storage_config(self, proxy_url=None):
        storage_config_dict = {
            'storage_provider': 'local',
            'bucket_name': 'test-bucket',
            'key_file': '/tmp/key_file',
            'prefix': 'test-prefix',
            'fqdn': 'localhost',
            'base_path': '/tmp/backups',
            'concurrent_transfers': '1',
            'multi_part_upload_threshold': '20971520',
            'proxy_url': proxy_url,
            'k8s_mode': 'False'
        }

        return _namedtuple_from_dict(StorageConfig, storage_config_dict)

    @patch('medusa.storage.Storage._load_storage')
    def test_proxy_env_vars_set(self, mock_load_storage):
        proxy_url = "http://proxy.example.com:8080"
        config = self._get_storage_config(proxy_url=proxy_url)

        # Initialize Storage, which should set env vars
        Storage(config=config)

        self.assertEqual(os.environ.get('HTTP_PROXY'), proxy_url)
        self.assertEqual(os.environ.get('HTTPS_PROXY'), proxy_url)

    @patch('medusa.storage.Storage._load_storage')
    def test_proxy_env_vars_not_set_when_none(self, mock_load_storage):
        config = self._get_storage_config(proxy_url=None)

        Storage(config=config)

        self.assertIsNone(os.environ.get('HTTP_PROXY'))
        self.assertIsNone(os.environ.get('HTTPS_PROXY'))
