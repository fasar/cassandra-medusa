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
import io
import typing as t
import base64
from unittest.mock import MagicMock, AsyncMock

from medusa.storage.abstract_storage import AbstractStorage, ManifestObject, AbstractBlob
from medusa.storage.encryption import EncryptionManager


class MockStorage(AbstractStorage):
    def connect(self):
        pass

    def disconnect(self):
        pass

    async def _list_blobs(self, prefix=None):
        return []

    async def _upload_object(self, data: io.BytesIO, object_key: str, headers: t.Dict[str, str]) -> AbstractBlob:
        return AbstractBlob(name=object_key, size=100, hash="enc_hash", last_modified=None, storage_class=None)

    async def _upload_object_from_stream(
            self, stream: io.BytesIO, object_key: str,
            headers: t.Dict[str, str]) -> ManifestObject:
        # Read the stream to trigger read() on EncryptedStream
        data = stream.read()
        blob_size = len(data)

        source_size = getattr(stream, 'source_size', None)
        source_md5 = getattr(stream, 'md5_source', None)

        return ManifestObject(
            path=object_key,
            size=blob_size,
            MD5="enc_hash_of_stream",
            source_size=source_size,
            source_MD5=source_md5
        )

    async def _download_blob(self, src: str, dest: str):
        pass

    async def _upload_blob(self, src: str, dest: str) -> ManifestObject:
        src_path = pathlib.Path(src)
        object_key = AbstractStorage.path_maybe_with_parent(dest, src_path)
        return ManifestObject(path=object_key, size=100, MD5="enc_hash")

    async def _get_object(self, object_key):
        pass

    async def _read_blob_as_bytes(self, blob: AbstractBlob) -> bytes:
        pass

    async def _delete_object(self, obj: AbstractBlob):
        pass

    @staticmethod
    def blob_matches_manifest(blob, object_in_manifest, enable_md5_checks=False):
        pass

    @staticmethod
    def file_matches_storage(src, cached_item, threshold=None, enable_md5_checks=False):
        pass

    @staticmethod
    def compare_with_manifest(actual_size, size_in_manifest, actual_hash=None, hash_in_manifest=None,
                              threshold=None):
        pass


class EncryptedStorageTest(unittest.TestCase):
    # Define constant for secondary index suffix used in tests
    TEST_INDEX_SUFFIX = ".test_idx"

    def setUp(self):
        # Generate valid AES-256 key (32 bytes)
        self.raw_key = os.urandom(32)
        self.key = base64.urlsafe_b64encode(self.raw_key).decode('utf-8')

        # Setup config
        config_dict = {
            'storage_provider': 'mock',
            'bucket_name': 'test_bucket',
            'concurrent_transfers': '1',
            'key_secret_base64': self.key,
            'encryption_tmp_dir': None
        }

        self.mock_config = MagicMock()
        for k, v in config_dict.items():
            setattr(self.mock_config, k, v)

        self.storage = MockStorage(self.mock_config)

    def test_upload_encrypted_blobs(self):
        test_msg = b"plaintext content"
        test_msg_size = len(test_msg)
        with tempfile.TemporaryDirectory() as temp_dir:
            src_file = os.path.join(temp_dir, "test.txt")
            with open(src_file, "wb") as f:
                f.write(test_msg)

            srcs = [pathlib.Path(src_file)]
            dest = "backup/data"

            # Execute the loop to run the async upload
            loop = self.storage.get_or_create_event_loop()
            manifests = loop.run_until_complete(self.storage._upload_encrypted_blobs(srcs, dest))

            self.assertEqual(len(manifests), 1)
            mo = manifests[0]

            # Check if source metadata is populated correctly from the stream
            self.assertEqual(mo.source_size, test_msg_size)
            self.assertIsNotNone(mo.source_MD5)

            # Check encrypted metadata
            self.assertTrue(mo.size > 0)
            self.assertEqual(mo.MD5, "enc_hash_of_stream")

            # Verify the path is correct
            self.assertEqual(mo.path, f"{dest}/test.txt")

    def test_upload_encrypted_blobs_with_secondary_index(self):
        test_msg = b"index content"
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a structure like .../table/.index_name/file.db
            index_dir = os.path.join(temp_dir, self.TEST_INDEX_SUFFIX)
            os.mkdir(index_dir)

            src_file = os.path.join(index_dir, "test.db")
            with open(src_file, "wb") as f:
                f.write(test_msg)

            srcs = [pathlib.Path(src_file)]
            dest = "backup/data"

            loop = self.storage.get_or_create_event_loop()
            manifests = loop.run_until_complete(self.storage._upload_encrypted_blobs(srcs, dest))

            self.assertEqual(len(manifests), 1)
            mo = manifests[0]
            self.assertIn(self.TEST_INDEX_SUFFIX, mo.path)

            # Verify it preserved the file name
            self.assertTrue(mo.path.endswith("/test.db"))

    def test_download_encrypted_blobs(self):
        original_content = b"restored content"

        # Test with a specific temp dir configuration
        self.storage.config.encryption_tmp_dir = tempfile.gettempdir()

        # Mock the abstract storage behavior for _download_object_as_stream
        # We need to provide a stream of ENCRYPTED content.

        # 1. Create encrypted content
        src_stream = io.BytesIO(original_content)
        from medusa.storage.encryption import EncryptedStream
        # Using the same key as configured in storage
        enc_stream = EncryptedStream(src_stream, self.key)
        encrypted_content = enc_stream.read()

        # 2. Mock _download_object_as_stream to return this encrypted content as a stream
        # The AbstractStorage._download_encrypted_blob calls _download_object_as_stream
        self.storage._download_object_as_stream = AsyncMock(return_value=io.BytesIO(encrypted_content))

        # 3. We also need to mock os.stat or ensure directories exist if the code relies on them,
        # but _download_encrypted_blob mainly uses _download_object_as_stream + _decrypt_stream_to_file

        with tempfile.TemporaryDirectory() as temp_dir:
            srcs = ["backup/data/restored.txt"]
            dest = pathlib.Path(temp_dir) / "final_dest"

            self.storage.download_blobs(srcs, dest)

            # Check if file exists in final destination and is decrypted
            final_file = dest / "restored.txt"
            self.assertTrue(final_file.exists())

            with open(final_file, "rb") as f:
                self.assertEqual(f.read(), original_content)

    def test_download_encrypted_blobs_skips_plaintext_files(self):
        # Verify that metadata files are NOT decrypted but downloaded via _download_blob directly
        self.storage.config.encryption_tmp_dir = tempfile.gettempdir()
        original_content = b'{"json": "plaintext"}'

        async def side_effect(src, dest):
            # src is key, dest is local folder path
            src_path = pathlib.Path(src)
            file_path = AbstractStorage.path_maybe_with_parent(str(dest), src_path)
            pathlib.Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'wb') as f:
                f.write(original_content)

        self.storage._download_blob = AsyncMock(side_effect=side_effect)
        self.storage._download_object_as_stream = AsyncMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            srcs = [
                "backup/meta/manifest.json",
                "backup/meta/schema.cql"
            ]
            dest = pathlib.Path(temp_dir) / "final_dest"

            self.storage.download_blobs(srcs, dest)

            for src in srcs:
                final_file = dest / pathlib.Path(src).name
                self.assertTrue(final_file.exists(), f"File {final_file} should exist")
                with open(final_file, "rb") as f:
                    self.assertEqual(f.read(), original_content)

            # Ensure we did NOT try to stream/decrypt
            self.storage._download_object_as_stream.assert_not_called()
            # Ensure _download_blob was called for each file
            self.assertEqual(self.storage._download_blob.call_count, len(srcs))


if __name__ == '__main__':
    unittest.main()
