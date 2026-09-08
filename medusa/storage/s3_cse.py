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
Client-side encryption of S3 objects with the Amazon S3 Encryption Client (S3EC) and a local key.

The S3 Encryption Client does the cryptography: it encrypts each object under a fresh data key,
commits to that key, and stores the wrapped data key in the object's user metadata. What it does
not do is wrap the data key with anything but AWS KMS. Medusa wants the key in a local file, so
this module provides the keyring the client lacks, plus the glue the storage layer needs: key
decoding, a read-through MD5 wrapper, and the bandwidth limiting the client does not offer.
"""

import base64
import hashlib
import io
import json
import os

from s3transfer.bandwidth import BandwidthLimiter, LeakyBucket
from s3transfer.futures import TransferCoordinator

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from s3_encryption import S3EncryptionClient, S3EncryptionClientConfig
    from s3_encryption.exceptions import S3EncryptionClientError, S3EncryptionClientSecurityError
    from s3_encryption.materials import DefaultCryptoMaterialsManager
    from s3_encryption.materials.encrypted_data_key import EncryptedDataKey
    from s3_encryption.materials.keyring import S3Keyring
    HAS_S3EC = True
except ImportError:
    HAS_S3EC = False
    S3Keyring = object

    class S3EncryptionClientError(Exception):
        """Stand-in so that callers can name the client's errors whether or not it is installed."""

    class S3EncryptionClientSecurityError(Exception):
        """Stand-in so that callers can name the client's errors whether or not it is installed."""


class WrongKeyError(S3EncryptionClientError):
    """
    The object was encrypted by a key other than the configured one, or by another keyring.

    Its own type because it is the one decryption error that retrying cannot fix and that the
    operator can act on: configure the key the backup was taken with.
    """


MISSING_DEPENDENCY_MESSAGE = (
    "amazon-s3-encryption-client-python is not installed, but a client-side encryption key is "
    "configured. Install it with 'pip install cassandra-medusa[encryption]'"
)

KEY_LENGTH = 32

# --- On-disk format -------------------------------------------------------------------------
# Everything below is written into, or checked against, the metadata of every encrypted object.
# Changing any of it strands every backup taken before the change.

# Recorded in the encryption context of every object, and required on decrypt: it says which
# keyring wrapped the data key, so that an object written by a KMS keyring - or by a future
# Medusa keyring - fails with a clear message rather than a bad tag.
KEY_NAME = 'medusa-backup/raw-aes-key'
CONTEXT_KEY_NAME = 'medusa:key-name'
# The first bytes of SHA-256 of the key, hex. Not secret, and the only way to tell "wrong key"
# apart from "tampered object" at restore time.
CONTEXT_KEY_FINGERPRINT = 'medusa:key-fingerprint'
FINGERPRINT_LENGTH = 8

KEY_PROVIDER_ID = b'S3Keyring'
WRAP_ALGORITHM = 'AES/GCM'
# S3EC 4.0.0 labels every wrapped data key "kms+context" in the object metadata, whatever
# keyring produced it, and hands that label back on decrypt. Accept it alongside the honest one,
# so that objects written by 4.0.0 stay readable once the client labels them properly.
ACCEPTED_WRAP_ALGORITHMS = (WRAP_ALGORITHM, 'kms+context')
NONCE_LENGTH = 12
TAG_LENGTH = 16
# ---------------------------------------------------------------------------------------------

# AES-GCM authenticates at most 2^39 - 256 bits under one key and nonce, and the client encrypts
# a whole object under a single pair, multipart or not. It does not check the limit itself
# ("planned for a future release"), so Medusa does before uploading.
MAX_OBJECT_SIZE = (2 ** 39 - 256) // 8

# Block size used to copy a decrypted stream to disk. Each read() is one HTTP read plus one
# AES-GCM update, so 1 MiB keeps the Python-level call count low without holding much in memory.
DOWNLOAD_BLOCK_SIZE = 1024 * 1024


def require_s3ec():
    if not HAS_S3EC:
        raise ImportError(MISSING_DEPENDENCY_MESSAGE)


def decode_key(key_secret_base64) -> bytes:
    """
    The configured key: base64, decoding to exactly 32 bytes.

    Validated strictly, and with messages that name the setting, because a key that is off by a
    character would otherwise surface as an unhelpful error from deep inside the client on the
    first upload.
    """
    if not key_secret_base64:
        raise ValueError('Encryption key is not provided')
    try:
        key_bytes = key_secret_base64 if isinstance(key_secret_base64, bytes) else key_secret_base64.encode('utf-8')
        decoded_key = base64.b64decode(key_bytes, validate=True)
    except Exception as e:
        raise ValueError(
            'Encryption key is not properly base64-encoded. '
            'Please ensure the key is base64-encoded. Details: {}'.format(e)
        )
    if len(decoded_key) != KEY_LENGTH:
        raise ValueError(
            'Encryption key has invalid length. Expected {} bytes (256 bits) when base64-decoded, '
            'but got {} bytes.'.format(KEY_LENGTH, len(decoded_key))
        )
    return decoded_key


def key_fingerprint(key_bytes: bytes) -> str:
    return hashlib.sha256(key_bytes).digest()[:FINGERPRINT_LENGTH].hex()


def check_object_size(path, size: int):
    if size > MAX_OBJECT_SIZE:
        raise ValueError(
            '{} is {} bytes, more than the {} bytes AES-GCM can encrypt under a single key. '
            'Client-side encryption cannot back this file up.'.format(path, size, MAX_OBJECT_SIZE)
        )


def _canonical_context(context: dict) -> bytes:
    """
    The encryption context as additional authenticated data for the key wrap.

    The client stores the context as JSON in the object metadata and parses it back on decrypt,
    so the bytes must not depend on dict ordering or on the client's JSON formatting.
    """
    return json.dumps(context, sort_keys=True, separators=(',', ':')).encode('utf-8')


class LocalAesKeyring(S3Keyring):
    """
    Wraps each object's data key with a raw AES-256 key held in memory.

    Immutable once built, and holds nothing but the key and its fingerprint, so one instance can
    serve every upload and download thread of a storage. The nonce for each wrap comes from
    os.urandom, and the wrap is authenticated over the whole encryption context, so the metadata
    that carries the key name and fingerprint cannot be edited without invalidating the wrap.
    """

    def __init__(self, key_bytes: bytes):
        require_s3ec()
        if len(key_bytes) != KEY_LENGTH:
            raise ValueError('The wrapping key must be {} bytes, got {}'.format(KEY_LENGTH, len(key_bytes)))
        self._key = bytes(key_bytes)
        self.fingerprint = key_fingerprint(self._key)

    def __repr__(self):
        # Never let the key into a log line or a traceback
        return 'LocalAesKeyring(fingerprint={})'.format(self.fingerprint)

    def on_encrypt(self, enc_materials):
        enc_materials = super().on_encrypt(enc_materials)

        context = enc_materials.encryption_context
        context[CONTEXT_KEY_NAME] = KEY_NAME
        context[CONTEXT_KEY_FINGERPRINT] = self.fingerprint

        data_key = os.urandom(enc_materials.encryption_algorithm.data_key_length_bytes)
        nonce = os.urandom(NONCE_LENGTH)
        wrapped = AESGCM(self._key).encrypt(nonce, data_key, _canonical_context(context))

        enc_materials.encrypted_data_key = EncryptedDataKey(
            key_provider_id=KEY_PROVIDER_ID,
            key_provider_info=WRAP_ALGORITHM,
            encrypted_data_key=nonce + wrapped,
        )
        enc_materials.plaintext_data_key = data_key
        return enc_materials

    def on_decrypt(self, dec_materials, encrypted_data_keys=None):
        dec_materials = super().on_decrypt(dec_materials, encrypted_data_keys)
        edks = encrypted_data_keys if encrypted_data_keys is not None else dec_materials.encrypted_data_keys
        edk = edks[0]

        if edk.key_provider_info not in ACCEPTED_WRAP_ALGORITHMS:
            raise S3EncryptionClientError(
                'The data key was wrapped with {!r}, which this keyring cannot unwrap'.format(edk.key_provider_info)
            )

        stored = dec_materials.encryption_context_stored
        key_name = stored.get(CONTEXT_KEY_NAME)
        if key_name != KEY_NAME:
            raise WrongKeyError(
                'The object was not encrypted with a Medusa local key: key name {!r}, expected {!r}'.format(
                    key_name, KEY_NAME)
            )
        fingerprint = stored.get(CONTEXT_KEY_FINGERPRINT)
        if fingerprint != self.fingerprint:
            raise WrongKeyError(
                'The object was encrypted with a different key (fingerprint {}) than the one configured '
                '(fingerprint {}). Configure the key the backup was taken with.'.format(fingerprint, self.fingerprint)
            )

        raw = edk.encrypted_data_key
        if len(raw) < NONCE_LENGTH + TAG_LENGTH:
            raise S3EncryptionClientError('The wrapped data key is too short to be valid: {} bytes'.format(len(raw)))
        try:
            data_key = AESGCM(self._key).decrypt(raw[:NONCE_LENGTH], raw[NONCE_LENGTH:], _canonical_context(stored))
        except InvalidTag:
            raise S3EncryptionClientSecurityError(
                'Failed to unwrap the data key: the object metadata has been altered'
            )
        dec_materials.plaintext_data_key = data_key
        return dec_materials


class HashingReader:
    """
    Read-through wrapper that computes the MD5 and size of everything read from a file.

    The S3 Encryption Client reports the size and ETag of the ciphertext only, and the plaintext is
    what the next differential backup compares its local files against. Wrapping the file the
    client reads from yields both in the same pass. The MD5 is a checksum, not a security
    primitive, hence usedforsecurity=False; it is rendered base64 like every other MD5 Medusa
    writes into a manifest.
    """

    def __init__(self, fileobj):
        self._fileobj = fileobj
        self._hash = hashlib.md5(usedforsecurity=False)
        self.size = 0

    def read(self, size=-1):
        data = self._fileobj.read() if size is None or size < 0 else self._fileobj.read(size)
        if data:
            self._hash.update(data)
            self.size += len(data)
        return data

    @property
    def md5_base64(self) -> str:
        return base64.b64encode(self._hash.digest()).decode('utf-8')

    def readable(self):
        return True

    def seekable(self):
        return False

    def close(self):
        self._fileobj.close()


def make_bandwidth_limiter(max_bandwidth) -> BandwidthLimiter:
    """
    The same limiter s3transfer builds for TransferConfig(max_bandwidth=...): one leaky bucket,
    shared by every stream it wraps, so uploads and downloads from every thread share the budget.
    """
    return BandwidthLimiter(LeakyBucket(int(max_bandwidth)))


def limit_bandwidth(limiter: BandwidthLimiter, stream):
    """Wrap a stream so that reads from it consume the limiter's budget."""
    return limiter.get_bandwith_limited_stream(stream, TransferCoordinator())


class _LimitUploadBandwidth:
    """
    A request-created handler that throttles the ciphertext on its way out.

    The client has no bandwidth setting. It replaces the request body with the ciphertext in its
    own before-call handler; this one wraps that ciphertext in the stream s3transfer itself uses to
    enforce max_bandwidth. It runs at request-created, after botocore has read the body for the
    request checksum and for the signature, so that only the bytes going out on the wire count
    against the limit: wrapping earlier made a 50 MB/s limit deliver 15 MB/s.
    """

    def __init__(self, limiter: BandwidthLimiter):
        self._limiter = limiter

    def __call__(self, request, operation_name, **kwargs):
        body = request.data
        if operation_name not in ('PutObject', 'UploadPart'):
            return
        if not body or isinstance(body, dict) or hasattr(body, 'signal_transferring'):
            return
        if isinstance(body, (bytes, bytearray)):
            body = io.BytesIO(body)
        request.data = limit_bandwidth(self._limiter, body)


def build_encryption_client(s3_client, key_bytes: bytes, bandwidth_limiter: BandwidthLimiter = None):
    """
    Wrap a boto3 S3 client so that put_object/upload_fileobj encrypt and get_object decrypts.

    The wrapping registers event handlers on the boto3 client itself, so that client must not be
    used for plaintext objects afterwards: give this function a client of its own.

    Delayed authentication is what makes get_object stream: without it the client reads the whole
    object into memory before releasing a byte. The price is that plaintext reaches the caller
    before the authentication tag is checked; the check happens on the last read and raises, so
    callers must treat the output as unverified until the stream is exhausted.
    """
    require_s3ec()
    keyring = LocalAesKeyring(key_bytes)
    config = S3EncryptionClientConfig(
        keyring=keyring,
        cmm=DefaultCryptoMaterialsManager(keyring),
        enable_delayed_authentication=True,
    )
    client = S3EncryptionClient(s3_client, config)
    if bandwidth_limiter is not None:
        # On the same event as the signer, and after it: botocore runs the handlers of the more
        # specific request-created.s3.PutObject before those of request-created.s3, so a handler
        # registered on the operation's own event would still be ahead of the signature.
        s3_client.meta.events.register_last('request-created.s3', _LimitUploadBandwidth(bandwidth_limiter),
                                            unique_id='medusa-cse-bandwidth')
    return client
