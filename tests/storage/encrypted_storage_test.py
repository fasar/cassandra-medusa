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
import os
import tempfile
import pathlib
import base64
import collections
from unittest.mock import MagicMock, patch, ANY
from cryptography.fernet import Fernet

from medusa.storage.abstract_storage import AbstractStorage, ManifestObject
from medusa.config import MedusaConfig, StorageConfig

class MockStorage(AbstractStorage):
    def connect(self):
        pass
    def disconnect(self):
        pass
    async def _list_blobs(self, prefix=None):
        return []
    async def _upload_object(self, data, object_key, headers):
        return MagicMock()
    async def _download_blob(self, src, dest):
        pass
    async def _upload_blob(self, src, dest):
        # Return a dummy ManifestObject for the upload
        return ManifestObject(path=f"{dest}/{pathlib.Path(src).name}", size=100, MD5="enc_hash")
    async def _get_object(self, object_key):
        pass
    async def _read_blob_as_bytes(self, blob):
        pass
    async def _delete_object(self, obj):
        pass
    @staticmethod
    def blob_matches_manifest(blob, object_in_manifest, enable_md5_checks=False):
        pass
    @staticmethod
    def file_matches_storage(src, cached_item, threshold=None, enable_md5_checks=False):
        pass
    @staticmethod
    def compare_with_manifest(actual_size, size_in_manifest, actual_hash=None, hash_in_manifest=None, threshold=None):
        pass

class EncryptedStorageTest(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key().decode('utf-8')

        # Setup config
        config_dict = {
            'storage_provider': 'mock',
            'bucket_name': 'test_bucket',
            'key_file': None,
            'prefix': None,
            'fqdn': 'localhost',
            'concurrent_transfers': '1',
            'key_secret_base64': self.key,
            'storage_class': 'STANDARD',
             'read_timeout': None
        }

        # Create a mock config object using namedtuple as base
        # StorageConfig is defined in medusa.config
        # We can just mock the config object passed to MockStorage
        self.mock_config = MagicMock()
        for k, v in config_dict.items():
            setattr(self.mock_config, k, v)

        self.storage = MockStorage(self.mock_config)

    def test_upload_encrypted_blobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            src_file = os.path.join(temp_dir, "test.txt")
            with open(src_file, "wb") as f:
                f.write(b"plaintext content")

            srcs = [pathlib.Path(src_file)]
            dest = "backup/data"

            # We need to mock _upload_blob to capture what's being uploaded
            # But here we want to test that _upload_encrypted_blobs calls _upload_blobs with ENCRYPTED files
            # And returns manifests with SOURCE metadata.

            # The MockStorage._upload_blob returns a fixed ManifestObject.
            # We want to check the result of upload_blobs (the public method)

            manifests = self.storage.upload_blobs(srcs, dest)

            self.assertEqual(len(manifests), 1)
            mo = manifests[0]

            # Check if source metadata is populated
            self.assertEqual(mo.source_size, 17) # len("plaintext content")
            self.assertIsNotNone(mo.source_MD5)

            # The MockStorage._upload_blob returns size=100, MD5="enc_hash"
            self.assertEqual(mo.size, 100)
            self.assertEqual(mo.MD5, "enc_hash")

            # Verify the path is correct
            self.assertEqual(mo.path, f"{dest}/test.txt")

    @patch("medusa.storage.abstract_storage.AbstractStorage._download_blobs")
    def test_download_encrypted_blobs(self, mock_download_blobs_impl):
        # We need to mock the internal _download_blobs to simulate downloading the ENCRYPTED file
        # to the temporary directory.

        from medusa.storage.encryption import EncryptionManager
        manager = EncryptionManager(self.key)

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create an encrypted file that "download" will simulate
            original_content = b"restored content"

            # We need a side_effect for mock_download_blobs_impl that writes the encrypted file
            # to the temp dir it receives as 2nd argument.
            def side_effect(srcs, dest_dir):
                # srcs is list of strings (paths relative to bucket)
                # dest_dir is the temp dir
                for src in srcs:
                    file_name = pathlib.Path(src).name
                    dest_path = os.path.join(dest_dir, file_name)
                    manager.encrypt_file_content(original_content, dest_path)

            # Helper to encrypt content directly to file (since encrypt_file takes path)
            def encrypt_content_to_file(content, dest_path):
                with tempfile.NamedTemporaryFile() as tmp_src:
                    tmp_src.write(content)
                    tmp_src.flush()
                    manager.encrypt_file(tmp_src.name, dest_path)

            manager.encrypt_file_content = encrypt_content_to_file

            mock_download_blobs_impl.side_effect = side_effect

            # Test parameters
            srcs = ["backup/data/restored.txt"]
            dest = pathlib.Path(temp_dir) / "final_dest"

            self.storage.download_blobs(srcs, dest)

            # Check if file exists in final destination and is decrypted
            final_file = dest / "restored.txt"
            self.assertTrue(final_file.exists())

            with open(final_file, "rb") as f:
                self.assertEqual(f.read(), original_content)

if __name__ == '__main__':
    unittest.main()
