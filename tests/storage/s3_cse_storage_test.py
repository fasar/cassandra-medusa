# -*- coding: utf-8 -*-
# Copyright 2026 DataStax, Inc.
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

"""
S3BaseStorage with client-side encryption, end to end against the in-memory S3.
"""

import base64
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from unittest.mock import patch

from tenacity import wait_fixed

from medusa.storage import Storage
from medusa.storage.abstract_storage import ManifestObject
from medusa.storage.s3_base_storage import S3BaseStorage
from medusa.storage.s3_cse import (
    HAS_S3EC, TAG_LENGTH, NotEncryptedError, PlaintextObjectExistsError, S3EncryptionClientSecurityError,
    WrongKeyError, key_fingerprint
)
from tests.storage.abstract_storage_test import AttributeDict
from tests.storage.fake_s3 import FakeS3

if HAS_S3EC:
    from s3transfer.bandwidth import BandwidthLimitedStream

SKIP_REASON = 'amazon-s3-encryption-client-python is not installed'
BUCKET = 'medusa-it-encrypted'
KEY = base64.b64encode(os.urandom(32)).decode('utf-8')
SMALL = 100 * 1024              # below the 8 MiB multipart threshold: put_object
LARGE = 12 * 1024 * 1024 + 7    # above it: multipart, 3 parts of 5 MiB


def storage_config(tmp_dir, **overrides):
    credentials = os.path.join(tmp_dir, 'credentials')
    with open(credentials, 'w') as f:
        f.write('[default]\naws_access_key_id = fake-access-key\naws_secret_access_key = fake-secret-key\n')
    config = {
        'storage_provider': 's3_compatible',
        'bucket_name': BUCKET,
        'key_file': credentials,
        'api_profile': None,
        'region': 'default',
        'host': '127.0.0.1',
        'port': '1',
        'secure': 'False',
        'ssl_verify': 'False',
        'kms_id': None,
        'sse_c_key': None,
        'storage_class': None,
        'transfer_max_bandwidth': None,
        'multipart_chunksize': '5MB',
        'concurrent_transfers': '2',
        'read_timeout': None,
        's3_addressing_style': 'path',
        'key_secret_base64': KEY,
        # what Storage() itself reads
        'k8s_mode': None,
        'prefix': None,
        'base_path': tmp_dir,
        'fqdn': 'node1',
    }
    config.update(overrides)
    return AttributeDict(config)


def md5_base64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(hashlib.md5(f.read()).digest()).decode('utf-8')


@unittest.skipIf(not HAS_S3EC, SKIP_REASON)
class EncryptedS3StorageTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='medusa-cse-test-')
        self.data_dir = os.path.join(self.tmp_dir, 'data')
        self.restore_dir = os.path.join(self.tmp_dir, 'restore')
        os.makedirs(self.data_dir)
        os.makedirs(self.restore_dir)
        self.fake = FakeS3()
        self.storage = self.connect(storage_config(self.tmp_dir))

    def tearDown(self):
        self.storage.disconnect()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def connect(self, config):
        storage = S3BaseStorage(config)
        storage.connect()
        self.fake.attach(storage.s3_client)
        if storage.s3_cse_client is not None:
            self.fake.attach(storage.s3_cse_client.wrapped_s3_client)
        return storage

    def a_file(self, name, size):
        path = os.path.join(self.data_dir, name)
        with open(path, 'wb') as f:
            f.write(os.urandom(size))
        return path

    def test_sstables_are_encrypted_and_metadata_stays_plaintext(self):
        small = self.a_file('nb-1-big-Data.db', SMALL)
        large = self.a_file('nb-1-big-Index.db', LARGE)
        manifest = os.path.join(self.data_dir, 'manifest.json')
        with open(manifest, 'w') as f:
            json.dump({'hello': 'world'}, f)

        manifest_objects = self.storage.upload_blobs([small, large, manifest], 'node1/backup1/data/ks/t')

        by_name = {os.path.basename(m.path): m for m in manifest_objects}
        self.assertEqual({'nb-1-big-Data.db', 'nb-1-big-Index.db', 'manifest.json'}, set(by_name))

        for name, size in (('nb-1-big-Data.db', SMALL), ('nb-1-big-Index.db', LARGE)):
            stored = self.fake.get(BUCKET, 'node1/backup1/data/ks/t/' + name)
            self.assertEqual(size + TAG_LENGTH, len(stored['body']), name)
            self.assertIn('x-amz-3', stored['metadata'], name)
            self.assertEqual(key_fingerprint(base64.b64decode(KEY)),
                             json.loads(stored['metadata']['x-amz-t'])['medusa:key-fingerprint'])
            mo = by_name[name]
            self.assertEqual(size + TAG_LENGTH, mo.size)
            self.assertEqual(stored['etag'].replace('"', ''), mo.MD5)
            self.assertEqual(size, mo.source_size)
            self.assertEqual(md5_base64(os.path.join(self.data_dir, name)), mo.source_MD5)

        stored = self.fake.get(BUCKET, 'node1/backup1/data/ks/t/manifest.json')
        self.assertEqual(b'{"hello": "world"}', stored['body'])
        self.assertNotIn('x-amz-3', stored['metadata'])
        self.assertIsNone(by_name['manifest.json'].source_size)
        self.assertIsNone(by_name['manifest.json'].source_MD5)

    def test_the_large_file_went_multipart_and_the_small_one_did_not(self):
        self.storage.upload_blobs([self.a_file('nb-1-big-Data.db', SMALL), self.a_file('nb-1-big-Index.db', LARGE)],
                                  'prefix')
        methods = [(method, key) for method, key, _ in self.fake.requests]
        self.assertEqual(1, methods.count(('PUT', 'prefix/nb-1-big-Data.db')))
        self.assertEqual(3, methods.count(('PUT', 'prefix/nb-1-big-Index.db')))
        self.assertIn(('POST', 'prefix/nb-1-big-Index.db'), methods)
        self.assertTrue(self.fake.get(BUCKET, 'prefix/nb-1-big-Index.db')['etag'].endswith('-3"'))

    def test_download_restores_the_plaintext(self):
        small = self.a_file('nb-1-big-Data.db', SMALL)
        large = self.a_file('nb-1-big-Index.db', LARGE)
        manifest = self.a_file('manifest.json', 100)
        self.storage.upload_blobs([small, large, manifest], 'prefix')

        self.storage.download_blobs(['prefix/nb-1-big-Data.db', 'prefix/nb-1-big-Index.db', 'prefix/manifest.json'],
                                    self.restore_dir)

        for name in ('nb-1-big-Data.db', 'nb-1-big-Index.db', 'manifest.json'):
            with open(os.path.join(self.data_dir, name), 'rb') as a, \
                    open(os.path.join(self.restore_dir, name), 'rb') as b:
                self.assertEqual(a.read(), b.read(), name)

    def test_secondary_index_files_keep_their_sub_folder(self):
        index_dir = os.path.join(self.data_dir, '.t_idx')
        os.makedirs(index_dir)
        path = os.path.join(index_dir, 'nb-1-big-Data.db')
        with open(path, 'wb') as f:
            f.write(os.urandom(SMALL))

        manifest_objects = self.storage.upload_blobs([path], 'prefix')
        self.assertEqual('prefix/.t_idx/nb-1-big-Data.db', manifest_objects[0].path)
        self.assertEqual(SMALL, manifest_objects[0].source_size)

        self.storage.download_blobs(['prefix/.t_idx/nb-1-big-Data.db'], self.restore_dir)
        with open(path, 'rb') as a, open(os.path.join(self.restore_dir, '.t_idx', 'nb-1-big-Data.db'), 'rb') as b:
            self.assertEqual(a.read(), b.read())

    def test_a_tampered_object_leaves_no_file_behind(self):
        self.storage.upload_blobs([self.a_file('nb-1-big-Data.db', SMALL)], 'prefix')
        stored = self.fake.get(BUCKET, 'prefix/nb-1-big-Data.db')
        body = bytearray(stored['body'])
        body[SMALL // 2] ^= 0x01
        stored['body'] = bytes(body)

        with self.assertRaises(S3EncryptionClientSecurityError):
            self.storage.download_blobs(['prefix/nb-1-big-Data.db'], self.restore_dir)
        self.assertEqual([], os.listdir(self.restore_dir))

    def test_the_wrong_key_is_reported_and_not_retried(self):
        self.storage.upload_blobs([self.a_file('nb-1-big-Data.db', SMALL)], 'prefix')
        other = self.connect(storage_config(self.tmp_dir, key_secret_base64=base64.b64encode(os.urandom(32)).decode()))
        try:
            requests_before = len(self.fake.requests)
            with self.assertRaisesRegex(WrongKeyError, 'different key'):
                other.download_blobs(['prefix/nb-1-big-Data.db'], self.restore_dir)
            self.assertEqual([], os.listdir(self.restore_dir))
            # two heads and one get: tenacity did not retry a failure that retrying cannot fix
            self.assertEqual(3, len(self.fake.requests) - requests_before)
        finally:
            other.disconnect()

    def test_an_object_uploaded_before_encryption_is_refused_with_the_way_out(self):
        # a backup taken by a Medusa without a key, now restored by one with a key
        plain = self.connect(storage_config(self.tmp_dir, key_secret_base64=None))
        try:
            plain.upload_blobs([self.a_file('nb-1-big-Data.db', SMALL)], 'prefix')
        finally:
            plain.disconnect()
        self.assertNotIn('x-amz-3', self.fake.get(BUCKET, 'prefix/nb-1-big-Data.db')['metadata'])

        requests_before = len(self.fake.requests)
        with self.assertRaisesRegex(NotEncryptedError, 'uploaded before encryption was enabled'):
            self.storage.download_blobs(['prefix/nb-1-big-Data.db'], self.restore_dir)
        self.assertEqual([], os.listdir(self.restore_dir))
        # two heads, no get, no retry
        self.assertEqual([('HEAD', 'prefix/nb-1-big-Data.db', type(None))] * 2, self.fake.requests[requests_before:])

    def test_an_encrypted_upload_never_overwrites_a_plaintext_object(self):
        # the first encrypted differential re-uploads every file under the keys the plaintext chain uses
        path = self.a_file('nb-1-big-Data.db', SMALL)
        plain = self.connect(storage_config(self.tmp_dir, key_secret_base64=None))
        try:
            plain.upload_blobs([path], 'prefix')
        finally:
            plain.disconnect()
        stored_before = self.fake.get(BUCKET, 'prefix/nb-1-big-Data.db')['body']

        with self.assertRaisesRegex(PlaintextObjectExistsError, 'new prefix'):
            self.storage.upload_blobs([path], 'prefix')
        self.assertEqual(stored_before, self.fake.get(BUCKET, 'prefix/nb-1-big-Data.db')['body'])
        self.assertEqual(1, sum(1 for m, k, _ in self.fake.requests if m == 'PUT' and k == 'prefix/nb-1-big-Data.db'))

        # the same file under a new prefix, or over an encrypted object, is fine
        self.storage.upload_blobs([path], 'prefix-cse')
        self.storage.upload_blobs([path], 'prefix-cse')
        self.assertIn('x-amz-3', self.fake.get(BUCKET, 'prefix-cse/nb-1-big-Data.db')['metadata'])

    def test_uploads_are_throttled_when_a_bandwidth_limit_is_set(self):
        throttled = self.connect(storage_config(self.tmp_dir, transfer_max_bandwidth='100MB/s'))
        try:
            throttled.upload_blobs([self.a_file('nb-1-big-Data.db', SMALL), self.a_file('nb-1-big-Index.db', LARGE)],
                                   'prefix')
            bodies = {(m, k): t for m, k, t in self.fake.requests if m == 'PUT'}
            self.assertIs(BandwidthLimitedStream, bodies[('PUT', 'prefix/nb-1-big-Data.db')])
            self.assertIs(BandwidthLimitedStream, bodies[('PUT', 'prefix/nb-1-big-Index.db')])
            throttled.download_blobs(['prefix/nb-1-big-Index.db'], self.restore_dir)
            with open(os.path.join(self.data_dir, 'nb-1-big-Index.db'), 'rb') as a, \
                    open(os.path.join(self.restore_dir, 'nb-1-big-Index.db'), 'rb') as b:
                self.assertEqual(a.read(), b.read())
        finally:
            throttled.disconnect()

    def test_storage_class_and_kms_go_through(self):
        classy = self.connect(storage_config(self.tmp_dir, storage_class='STANDARD_IA', kms_id='my-kms-key'))
        try:
            classy.upload_blobs([self.a_file('nb-1-big-Data.db', SMALL), self.a_file('nb-1-big-Index.db', LARGE)],
                                'prefix')
            for name in ('nb-1-big-Data.db', 'nb-1-big-Index.db'):
                headers = self.fake.get(BUCKET, 'prefix/' + name)['headers']
                self.assertEqual('STANDARD_IA', headers['x-amz-storage-class'], name)
                self.assertEqual('aws:kms', headers['x-amz-server-side-encryption'], name)
                self.assertEqual('my-kms-key', headers['x-amz-server-side-encryption-aws-kms-key-id'], name)
        finally:
            classy.disconnect()

    def test_a_retried_upload_starts_over_from_the_first_byte(self):
        # a retry must reopen and re-hash the file, or the manifest describes a truncated plaintext
        path = self.a_file('nb-1-big-Data.db', SMALL)
        attempts = []
        original = self.storage._S3BaseStorage__upload_encrypted_file

        def flaky(src, object_key, extra_args):
            attempts.append(src)
            if len(attempts) == 1:
                with open(src, 'rb') as f:
                    f.read(10)   # consume a bit, then fail
                raise ConnectionError('simulated network failure')
            return original(src, object_key, extra_args)

        # the coroutine's tenacity decorator sleeps with asyncio.sleep for thousands of seconds
        retrying = S3BaseStorage._upload_blob.retry
        original_wait = retrying.wait
        retrying.wait = wait_fixed(0)
        try:
            with patch.object(self.storage, '_S3BaseStorage__upload_encrypted_file', side_effect=flaky):
                manifest_objects = self.storage.upload_blobs([path], 'prefix')
        finally:
            retrying.wait = original_wait
        self.assertEqual(2, len(attempts))
        self.assertEqual(SMALL, manifest_objects[0].source_size)
        self.assertEqual(md5_base64(path), manifest_objects[0].source_MD5)

    def test_without_a_key_nothing_is_encrypted_and_the_manifest_is_the_old_one(self):
        plain = self.connect(storage_config(self.tmp_dir, key_secret_base64=None))
        try:
            self.assertIsNone(plain.s3_cse_client)
            path = self.a_file('nb-1-big-Data.db', SMALL)
            manifest_objects = plain.upload_blobs([path], 'prefix')
            stored = self.fake.get(BUCKET, 'prefix/nb-1-big-Data.db')
            self.assertEqual(SMALL, len(stored['body']))
            self.assertEqual({}, stored['metadata'])
            self.assertEqual(ManifestObject('prefix/nb-1-big-Data.db', SMALL, stored['etag'].replace('"', '')),
                             manifest_objects[0])
            self.assertIsNone(manifest_objects[0].source_MD5)
            plain.download_blobs(['prefix/nb-1-big-Data.db'], self.restore_dir)
            with open(path, 'rb') as a, open(os.path.join(self.restore_dir, 'nb-1-big-Data.db'), 'rb') as b:
                self.assertEqual(a.read(), b.read())
        finally:
            plain.disconnect()


class EncryptionSettingsTest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='medusa-cse-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_a_key_is_refused_with_a_provider_other_than_s3(self):
        for provider in ('local', 'google_storage', 'azure_blobs'):
            with self.assertRaisesRegex(ValueError, 'only supported with the S3 storage providers'):
                Storage(config=storage_config(self.tmp_dir, storage_provider=provider))

    def test_a_key_is_refused_together_with_sse_c(self):
        with self.assertRaisesRegex(ValueError, 'sse_c_key cannot be combined'):
            Storage(config=storage_config(self.tmp_dir, sse_c_key=base64.b64encode(os.urandom(32)).decode()))

    @unittest.skipIf(not HAS_S3EC, SKIP_REASON)
    def test_the_s3_providers_accept_a_key(self):
        for provider in ('s3_compatible', 'ibm_storage', 's3_rgw', 's3_us_west_oregon'):
            # s3_rgw and ibm_storage need an explicit region, as on master
            storage = Storage(config=storage_config(self.tmp_dir, storage_provider=provider, region='us-east-1'))
            self.assertIsNotNone(storage.storage_driver.encryption_key, provider)

    @unittest.skipIf(not HAS_S3EC, SKIP_REASON)
    def test_a_bad_key_fails_when_the_storage_is_built(self):
        with self.assertRaisesRegex(ValueError, '32 bytes'):
            S3BaseStorage(storage_config(self.tmp_dir, key_secret_base64=base64.b64encode(b'short').decode()))
        with self.assertRaisesRegex(ValueError, 'base64'):
            S3BaseStorage(storage_config(self.tmp_dir, key_secret_base64='not base64 at all!'))

    @unittest.skipIf(HAS_S3EC, 'amazon-s3-encryption-client-python is installed')
    def test_a_key_without_the_library_says_how_to_install_it(self):
        with self.assertRaisesRegex(ImportError, r'cassandra-medusa\[encryption\]'):
            S3BaseStorage(storage_config(self.tmp_dir))
