import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from medusa.storage.custom_script_storage import CustomScriptStorage
from medusa.storage.abstract_storage import ManifestObject, AbstractBlob

class MockConfig:
    def __init__(self, upload_script, download_script):
        self.upload_script = upload_script
        self.download_script = download_script
        self.concurrent_transfers = 1
        self.kms_id = None
        self.sse_c_key = None
        self.k8s_mode = False
        self.bucket_name = "test-bucket"
        self.api_profile = None
        self.key_file = None
        self.host = None
        self.region = "us-east-1"
        self.secure = True
        self.ssl_verify = True
        self.s3_addressing_style = "auto"
        self.multipart_chunksize = "50MB"
        self.transfer_max_bandwidth = "10MB"
        self.multi_part_upload_threshold = "50MB"
        self.aws_cli_path = "aws"
        self.backup_grace_period_in_days = 10
        self.use_sudo_for_restore = False
        self.prefix = "prefix"
        self.fqdn = "fqdn"
        self.host_file_separator = "_"
        self.storage_provider = "custom_script"
        self.storage_class = None
        self.base_path = "/tmp"
        self.max_backup_age = 0
        self.max_backup_count = 0
        self.port = None
        self.read_timeout = 60
        self.key_secret_base64 = None

class MockBlob:
    def __init__(self, name, size, hash):
        self.name = name
        self.size = size
        self.hash = hash

class TestCustomScriptStorage(unittest.TestCase):

    def setUp(self):
        # Create temp upload script
        self.upload_fd, self.upload_path = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(self.upload_fd, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write("echo '{\"remote_md5\": \"q82rzavNq82rzavNq82rzQ==\", \"remote_size\": 999}'\n")
        os.chmod(self.upload_path, 0o755)

        # Create temp download script
        self.download_fd, self.download_path = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(self.download_fd, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write("echo 'Downloaded'\n")
        os.chmod(self.download_path, 0o755)

        self.config = MockConfig(self.upload_path, self.download_path)
        # Mock S3BaseStorage connection
        CustomScriptStorage.connect = lambda self: None
        self.storage = CustomScriptStorage(self.config)

    def tearDown(self):
        os.remove(self.upload_path)
        os.remove(self.download_path)

    def test_upload_blob(self):
        # Create a temp file to upload
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w') as f:
            f.write("test data")

        dest = "s3://test/dest.txt"

        loop = CustomScriptStorage.get_or_create_event_loop()
        manifest_obj = loop.run_until_complete(self.storage._upload_blob(path, dest))

        self.assertEqual(manifest_obj.path, "dest.txt")
        self.assertEqual(manifest_obj.size, 9)
        self.assertIsNotNone(manifest_obj.MD5)
        self.assertEqual(manifest_obj.remote_md5, "q82rzavNq82rzavNq82rzQ==")
        self.assertEqual(manifest_obj.remote_size, 999)

        os.remove(path)

    def test_blob_matches_manifest(self):
        blob = MockBlob("dest.txt", 999, "abcdabcdabcdabcdabcdabcdabcdabcd")
        manifest_dict = {
            "path": "dest.txt",
            "size": 9,
            "MD5": "EjQSNBI0EjQSNBI0EjQSNA==",
            "remote_size": 999,
            "remote_md5": "q82rzavNq82rzavNq82rzQ=="
        }

        matches = CustomScriptStorage.blob_matches_manifest(blob, manifest_dict, enable_md5_checks=True)
        self.assertTrue(matches)

    def test_blob_matches_manifest_fallback(self):
        blob = MockBlob("dest.txt", 9, "12341234123412341234123412341234")
        manifest_dict = {
            "path": "dest.txt",
            "size": 9,
            "MD5": "EjQSNBI0EjQSNBI0EjQSNA=="
        }

        matches = CustomScriptStorage.blob_matches_manifest(blob, manifest_dict, enable_md5_checks=True)
        self.assertTrue(matches)

if __name__ == '__main__':
    unittest.main()
