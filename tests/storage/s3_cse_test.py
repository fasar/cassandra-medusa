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

import base64
import hashlib
import io
import json
import os
import unittest

from medusa.storage import s3_cse
from medusa.storage.s3_cse import (
    HAS_S3EC, HashingReader, LocalAesKeyring, build_encryption_client, check_object_size, decode_key,
    key_fingerprint, make_bandwidth_limiter, CONTEXT_KEY_FINGERPRINT, CONTEXT_KEY_NAME, KEY_NAME, TAG_LENGTH
)
from tests.storage.fake_s3 import FakeS3, make_client

if HAS_S3EC:
    from s3_encryption.exceptions import S3EncryptionClientError, S3EncryptionClientSecurityError
    from s3_encryption.materials import EncryptionMaterials
    from s3_encryption.materials.materials import DecryptionMaterials
    from s3transfer.bandwidth import BandwidthLimitedStream

SKIP_REASON = 'amazon-s3-encryption-client-python is not installed'
BUCKET = 'bucket'


def a_key():
    return os.urandom(32)


class KeyDecodingTest(unittest.TestCase):

    def test_a_valid_key_decodes_to_32_bytes(self):
        key = a_key()
        self.assertEqual(key, decode_key(base64.b64encode(key).decode('utf-8')))

    def test_bytes_input_is_accepted(self):
        key = a_key()
        self.assertEqual(key, decode_key(base64.b64encode(key)))

    def test_an_empty_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not provided'):
            decode_key('')
        with self.assertRaisesRegex(ValueError, 'not provided'):
            decode_key(None)

    def test_invalid_base64_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'base64'):
            decode_key('this is not base64!!')

    def test_a_key_of_the_wrong_length_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '32 bytes.*16 bytes'):
            decode_key(base64.b64encode(os.urandom(16)).decode('utf-8'))

    def test_fingerprint_is_short_hex_and_stable(self):
        key = a_key()
        self.assertEqual(key_fingerprint(key), key_fingerprint(key))
        self.assertEqual(16, len(key_fingerprint(key)))
        int(key_fingerprint(key), 16)
        self.assertNotEqual(key_fingerprint(key), key_fingerprint(a_key()))

    def test_objects_beyond_the_aes_gcm_limit_are_refused_before_upload(self):
        check_object_size('ok.db', s3_cse.MAX_OBJECT_SIZE)
        with self.assertRaisesRegex(ValueError, 'AES-GCM'):
            check_object_size('too-big.db', s3_cse.MAX_OBJECT_SIZE + 1)


class HashingReaderTest(unittest.TestCase):

    def test_md5_and_size_of_what_was_read(self):
        data = os.urandom(100_000)
        reader = HashingReader(io.BytesIO(data))
        chunks = []
        while True:
            chunk = reader.read(7_000)
            if not chunk:
                break
            chunks.append(chunk)
        self.assertEqual(data, b''.join(chunks))
        self.assertEqual(len(data), reader.size)
        self.assertEqual(base64.b64encode(hashlib.md5(data).digest()).decode('utf-8'), reader.md5_base64)

    def test_read_everything_at_once(self):
        data = b'hello'
        reader = HashingReader(io.BytesIO(data))
        self.assertEqual(data, reader.read())
        self.assertEqual(b'', reader.read(-1))
        self.assertEqual(5, reader.size)

    def test_matches_the_manifest_md5_of_the_storage_layer(self):
        # check_already_uploaded() compares source_MD5 to generate_md5_hash(): same encoding, no newline
        from medusa.storage.abstract_storage import AbstractStorage
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(os.urandom(10_000))
        try:
            with open(f.name, 'rb') as src:
                reader = HashingReader(src)
                reader.read()
            self.assertEqual(AbstractStorage.generate_md5_hash(f.name), reader.md5_base64)
        finally:
            os.unlink(f.name)


@unittest.skipIf(not HAS_S3EC, SKIP_REASON)
class LocalAesKeyringTest(unittest.TestCase):

    def setUp(self):
        self.key = a_key()
        self.keyring = LocalAesKeyring(self.key)

    def encrypt(self, keyring=None):
        return (keyring or self.keyring).on_encrypt(EncryptionMaterials(encryption_context={}))

    def decryption_materials(self, enc, context=None):
        # what the client rebuilds from the object metadata; the wrap label is what it writes for
        # every keyring
        edk = enc.encrypted_data_key
        edk.key_provider_info = 'kms+context'
        stored = json.loads(json.dumps(enc.encryption_context)) if context is None else context
        return DecryptionMaterials(encrypted_data_keys=[edk], encryption_context_stored=stored)

    def test_round_trip(self):
        enc = self.encrypt()
        self.assertEqual(32, len(enc.plaintext_data_key))
        self.assertEqual(KEY_NAME, enc.encryption_context[CONTEXT_KEY_NAME])
        self.assertEqual(key_fingerprint(self.key), enc.encryption_context[CONTEXT_KEY_FINGERPRINT])
        dec = self.keyring.on_decrypt(self.decryption_materials(enc))
        self.assertEqual(enc.plaintext_data_key, dec.plaintext_data_key)

    def test_each_object_gets_its_own_data_key_and_wrap(self):
        one, two = self.encrypt(), self.encrypt()
        self.assertNotEqual(one.plaintext_data_key, two.plaintext_data_key)
        self.assertNotEqual(one.encrypted_data_key.encrypted_data_key, two.encrypted_data_key.encrypted_data_key)

    def test_the_honest_wrap_algorithm_label_is_accepted_too(self):
        enc = self.encrypt()
        materials = self.decryption_materials(enc)
        materials.encrypted_data_keys[0].key_provider_info = 'AES/GCM'
        self.assertEqual(enc.plaintext_data_key, self.keyring.on_decrypt(materials).plaintext_data_key)

    def test_another_wrap_algorithm_is_refused(self):
        enc = self.encrypt()
        materials = self.decryption_materials(enc)
        materials.encrypted_data_keys[0].key_provider_info = 'RSA-OAEP-SHA1'
        with self.assertRaisesRegex(S3EncryptionClientError, 'RSA-OAEP-SHA1'):
            self.keyring.on_decrypt(materials)

    def test_the_wrong_key_is_named_by_fingerprint(self):
        enc = self.encrypt()
        other = LocalAesKeyring(a_key())
        with self.assertRaisesRegex(S3EncryptionClientError, 'different key.*{}.*{}'.format(
                key_fingerprint(self.key), other.fingerprint)):
            other.on_decrypt(self.decryption_materials(enc))

    def test_an_object_wrapped_by_another_keyring_is_refused(self):
        enc = self.encrypt()
        context = dict(enc.encryption_context)
        context.pop(CONTEXT_KEY_NAME)
        with self.assertRaisesRegex(S3EncryptionClientError, 'not encrypted with a Medusa local key'):
            self.keyring.on_decrypt(self.decryption_materials(enc, context))

    def test_an_edited_context_breaks_the_wrap(self):
        # the context is authenticated by the wrap: a fingerprint pasted from another object does not
        # get past the fingerprint check
        enc = self.encrypt()
        context = dict(enc.encryption_context)
        context['medusa:extra'] = 'edited'
        with self.assertRaises(S3EncryptionClientSecurityError):
            self.keyring.on_decrypt(self.decryption_materials(enc, context))

    def test_a_truncated_wrapped_key_is_refused(self):
        enc = self.encrypt()
        enc.encrypted_data_key.encrypted_data_key = enc.encrypted_data_key.encrypted_data_key[:10]
        with self.assertRaisesRegex(S3EncryptionClientError, 'too short'):
            self.keyring.on_decrypt(self.decryption_materials(enc))

    def test_the_key_never_shows_in_repr(self):
        self.assertNotIn(self.key.hex(), repr(self.keyring))
        self.assertNotIn(base64.b64encode(self.key).decode('utf-8'), repr(self.keyring))
        self.assertIn(self.keyring.fingerprint, repr(self.keyring))

    def test_a_key_of_the_wrong_length_is_refused(self):
        with self.assertRaises(ValueError):
            LocalAesKeyring(os.urandom(31))


@unittest.skipIf(not HAS_S3EC, SKIP_REASON)
class EncryptionClientRoundTripTest(unittest.TestCase):
    # put_object / upload_fileobj / get_object through the real encryption client and the in-memory S3

    def setUp(self):
        self.key = a_key()
        self.fake = FakeS3()
        self.client = self.new_client(self.key)

    def new_client(self, key, bandwidth_limiter=None):
        boto_client = make_client()
        self.fake.attach(boto_client)
        return build_encryption_client(boto_client, key, bandwidth_limiter)

    def read_all(self, response, block=64 * 1024):
        chunks = []
        while True:
            chunk = response['Body'].read(block)
            if not chunk:
                break
            chunks.append(chunk)
        return b''.join(chunks)

    def test_put_object_stores_ciphertext_and_the_wrapped_key_in_metadata(self):
        data = os.urandom(10_000)
        self.client.put_object(Bucket=BUCKET, Key='k/nb-1-big-Data.db', Body=data)

        stored = self.fake.get(BUCKET, 'k/nb-1-big-Data.db')
        self.assertEqual(len(data) + TAG_LENGTH, len(stored['body']))
        self.assertNotIn(data[:64], stored['body'])
        self.assertIn('x-amz-3', stored['metadata'])
        context = json.loads(stored['metadata']['x-amz-t'])
        self.assertEqual(KEY_NAME, context[CONTEXT_KEY_NAME])
        self.assertEqual(key_fingerprint(self.key), context[CONTEXT_KEY_FINGERPRINT])

        response = self.client.get_object(Bucket=BUCKET, Key='k/nb-1-big-Data.db')
        self.assertEqual(data, self.read_all(response, block=999))

    def test_get_object_streams_without_buffering_the_whole_object(self):
        data = os.urandom(300_000)
        self.client.put_object(Bucket=BUCKET, Key='obj', Body=data)
        body = self.client.get_object(Bucket=BUCKET, Key='obj')['Body']
        first = body.read(1000)
        # delayed authentication: plaintext is released before the whole ciphertext was consumed
        self.assertEqual(data[:len(first)], first)
        self.assertLess(body.tell(), len(data))

    def test_multipart_upload_round_trip_with_the_hashing_reader(self):
        data = os.urandom(12 * 1024 * 1024 + 123)
        reader = HashingReader(io.BytesIO(data))
        self.client.upload_fileobj(reader, BUCKET, 'big', multipart_chunksize=5 * 1024 * 1024)

        self.assertEqual(len(data), reader.size)
        self.assertEqual(base64.b64encode(hashlib.md5(data).digest()).decode('utf-8'), reader.md5_base64)
        stored = self.fake.get(BUCKET, 'big')
        self.assertEqual(len(data) + TAG_LENGTH, len(stored['body']))
        self.assertTrue(stored['etag'].endswith('-3"'), stored['etag'])
        self.assertIn('x-amz-3', stored['metadata'])
        self.assertEqual(data, self.read_all(self.client.get_object(Bucket=BUCKET, Key='big')))

    def test_multipart_forwards_storage_class(self):
        self.client.upload_fileobj(io.BytesIO(os.urandom(100)), BUCKET, 'sc', multipart_chunksize=5 * 1024 * 1024,
                                   StorageClass='STANDARD_IA')
        self.assertEqual('STANDARD_IA', self.fake.get(BUCKET, 'sc')['headers']['x-amz-storage-class'])

    def test_put_object_forwards_storage_class(self):
        self.client.put_object(Bucket=BUCKET, Key='sc', Body=b'x', StorageClass='STANDARD_IA')
        self.assertEqual('STANDARD_IA', self.fake.get(BUCKET, 'sc')['headers']['x-amz-storage-class'])

    def test_a_tampered_object_fails_on_the_last_read(self):
        data = os.urandom(50_000)
        self.client.put_object(Bucket=BUCKET, Key='obj', Body=data)
        stored = self.fake.get(BUCKET, 'obj')
        body = bytearray(stored['body'])
        body[100] ^= 0xff
        stored['body'] = bytes(body)

        response = self.client.get_object(Bucket=BUCKET, Key='obj')
        with self.assertRaises(S3EncryptionClientSecurityError):
            self.read_all(response, block=4096)

    def test_the_wrong_key_is_reported_before_any_plaintext_is_released(self):
        self.client.put_object(Bucket=BUCKET, Key='obj', Body=os.urandom(1000))
        other = self.new_client(a_key())
        with self.assertRaisesRegex(S3EncryptionClientError, 'different key'):
            other.get_object(Bucket=BUCKET, Key='obj')

    def test_a_plaintext_object_is_not_decryptable(self):
        self.fake.store(BUCKET, 'manifest.json', b'{}')
        with self.assertRaises(S3EncryptionClientError):
            self.client.get_object(Bucket=BUCKET, Key='manifest.json')

    def test_a_missing_object_raises_the_client_error(self):
        with self.assertRaises(S3EncryptionClientError):
            self.client.get_object(Bucket=BUCKET, Key='nope')

    def test_only_the_bytes_sent_count_against_the_bandwidth_limit(self):
        # botocore reads the body for the checksum and the signature before sending it; wrapped too
        # early the limiter charges those reads too. 3 MB at 1 MB/s: about three seconds, not six or nine
        import time
        limiter = make_bandwidth_limiter(1024 * 1024)
        client = self.new_client(self.key, bandwidth_limiter=limiter)
        started = time.monotonic()
        client.put_object(Bucket=BUCKET, Key='throttled', Body=os.urandom(3 * 1024 * 1024))
        elapsed = time.monotonic() - started
        self.assertGreater(elapsed, 1.5, 'the limiter did not apply at all')
        self.assertLess(elapsed, 5.0, 'the limiter charged reads that never reached the wire')

    def test_the_bandwidth_limiter_wraps_the_ciphertext_of_every_upload_request(self):
        limiter = make_bandwidth_limiter(100 * 1024 * 1024)
        client = self.new_client(self.key, bandwidth_limiter=limiter)
        client.put_object(Bucket=BUCKET, Key='small', Body=os.urandom(1000))
        client.upload_fileobj(io.BytesIO(os.urandom(6 * 1024 * 1024)), BUCKET, 'big',
                              multipart_chunksize=5 * 1024 * 1024)

        bodies = {(method, key): body_type for method, key, body_type in self.fake.requests}
        self.assertIs(BandwidthLimitedStream, bodies[('PUT', 'small')])
        self.assertIs(BandwidthLimitedStream, bodies[('PUT', 'big')])
        self.assertEqual(os.urandom(0), b'')
        self.assertEqual(1000 + TAG_LENGTH, len(self.fake.get(BUCKET, 'small')['body']))
        self.assertEqual(6 * 1024 * 1024 + TAG_LENGTH, len(self.fake.get(BUCKET, 'big')['body']))


@unittest.skipIf(HAS_S3EC, 'amazon-s3-encryption-client-python is installed')
class MissingDependencyTest(unittest.TestCase):

    def test_the_error_says_how_to_install_it(self):
        with self.assertRaisesRegex(ImportError, r'cassandra-medusa\[encryption\]'):
            build_encryption_client(None, a_key())
        with self.assertRaisesRegex(ImportError, r'cassandra-medusa\[encryption\]'):
            LocalAesKeyring(a_key())
