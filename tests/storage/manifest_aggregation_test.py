# -*- coding: utf-8 -*-
# Copyright 2024 DataStax, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
import json
from unittest.mock import MagicMock, patch
import pathlib

from medusa.storage.abstract_storage import AbstractStorage, ManifestObject
from medusa.config import MedusaConfig

class MockStorage(AbstractStorage):
    def connect(self): pass
    def disconnect(self): pass
    async def _list_blobs(self, prefix=None): return []
    async def _upload_object(self, data, object_key, headers): pass
    async def _download_blob(self, src, dest): pass
    async def _upload_blob(self, src, dest): pass
    async def _get_object(self, object_key): pass
    async def _read_blob_as_bytes(self, blob): pass
    async def _delete_object(self, obj): pass
    @staticmethod
    def blob_matches_manifest(blob, object_in_manifest, enable_md5_checks=False): pass
    @staticmethod
    def file_matches_storage(src, cached_item, threshold=None, enable_md5_checks=False): pass
    @staticmethod
    def compare_with_manifest(actual_size, size_in_manifest, actual_hash=None, hash_in_manifest=None, threshold=None): pass

    # Must implement list_node_backups as it's not abstract but depends on other methods
    # But for this test, we will mock it or the methods it calls.
    # Actually, list_node_backups is defined in Storage class (medusa/storage/__init__.py),
    # not AbstractStorage.
    # The method get_files_from_all_manifests IS IN Storage class.

from medusa.storage import Storage

class GetAllManifestsTest(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.fqdn = "test-fqdn"
        self.config.prefix = "prefix"
        self.config.storage_provider = "local" # Just to pass initialization check
        self.config.k8s_mode = None

        # We need to mock _load_storage to return our MockStorage (driver)
        # But Storage class wraps the driver.

        with patch("medusa.storage.LocalStorage"):
             self.storage = Storage(config=self.config)

    def test_get_files_from_all_manifests(self):
        # Create dummy backups
        backup1 = MagicMock()
        backup1.name = "backup1"
        backup1.manifest = json.dumps([
            {
                "keyspace": "ks1",
                "columnfamily": "cf1",
                "objects": [
                    {
                        "path": "prefix/test-fqdn/backup1/data/ks1/cf1/f1.db",
                        "size": 100,
                        "MD5": "md5_1",
                        "source_size": 90,
                        "source_MD5": "src_md5_1"
                    }
                ]
            }
        ])

        backup2 = MagicMock()
        backup2.name = "backup2"
        backup2.manifest = json.dumps([
             {
                "keyspace": "ks1",
                "columnfamily": "cf1",
                "objects": [
                    {
                        # Newer version of f1.db (different source MD5)
                        "path": "prefix/test-fqdn/backup2/data/ks1/cf1/f1.db",
                        "size": 110,
                        "MD5": "md5_2",
                        "source_size": 95,
                        "source_MD5": "src_md5_2"
                    },
                     {
                        "path": "prefix/test-fqdn/backup2/data/ks1/cf1/f2.db",
                        "size": 200,
                        "MD5": "md5_3",
                        "source_size": 190,
                        "source_MD5": "src_md5_3"
                    }
                ]
            }
        ])

        # Mock list_node_backups to return these
        self.storage.list_node_backups = MagicMock(return_value=[backup1, backup2])

        # Call the method
        files = self.storage.get_files_from_all_manifests()

        # Check structure: files[ks][table][filename]
        self.assertIn("ks1", files)
        self.assertIn("cf1", files["ks1"])

        cf_files = files["ks1"]["cf1"]
        self.assertIn("f1.db", cf_files)
        self.assertIn("f2.db", cf_files)

        # Check that f1.db is from backup2 (since it was processed last in the loop)
        # Wait, the loop order depends on list_node_backups order.
        # AbstractStorage.list_node_backups sorts by start time.
        # Here we just iterate. If backup2 comes after backup1 in the list, it overwrites.
        # This simulates "latest version available in storage".

        f1 = cf_files["f1.db"]
        self.assertEqual(f1.source_MD5, "src_md5_2")
        self.assertEqual(f1.path, "prefix/test-fqdn/backup2/data/ks1/cf1/f1.db")

        f2 = cf_files["f2.db"]
        self.assertEqual(f2.source_MD5, "src_md5_3")

if __name__ == '__main__':
    unittest.main()
