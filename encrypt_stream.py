#!/usr/bin/env python3
import sys
import os
import aws_encryption_sdk
from aws_encryption_sdk.identifiers import CommitmentPolicy, WrappingAlgorithm, EncryptionKeyType, Algorithm
from aws_encryption_sdk.key_providers.raw import RawMasterKeyProvider
from aws_encryption_sdk.key_providers.raw import WrappingKey
from aws_encryption_sdk.materials_managers.caching import CachingCryptoMaterialsManager
from aws_encryption_sdk.caches.local import LocalCryptoMaterialsCache


class _StaticKeyProvider(RawMasterKeyProvider):
    provider_id = "medusa-backup"

    def configure(self, key_name, key_bytes):
        self._key_name = key_name
        self._key_bytes = key_bytes

    def _get_raw_key(self, key_id):
        key_id_str = key_id.decode('utf-8') if isinstance(key_id, bytes) else key_id
        expected_id = self._key_name.decode('utf-8') if isinstance(self._key_name, bytes) else self._key_name
        if key_id_str == expected_id:
            return WrappingKey(
                wrapping_algorithm=WrappingAlgorithm.AES_256_GCM_IV12_TAG16_NO_PADDING,
                wrapping_key=self._key_bytes,
                wrapping_key_type=EncryptionKeyType.SYMMETRIC
            )
        raise ValueError("Invalid key id")


def main():
    # Setup exactly like in medusa.storage.encryption
    client = aws_encryption_sdk.EncryptionSDKClient(
        commitment_policy=CommitmentPolicy.REQUIRE_ENCRYPT_REQUIRE_DECRYPT
    )

    key_bytes = os.urandom(32)
    key_name = "raw-aes-key"

    master_key_provider = _StaticKeyProvider()
    master_key_provider.configure(key_name, key_bytes)
    master_key_provider.add_master_key(key_name)

    cache = LocalCryptoMaterialsCache(capacity=100)
    cmm = CachingCryptoMaterialsManager(
        master_key_provider=master_key_provider,
        cache=cache,
        max_age=3600.0,
        max_messages_encrypted=100000,
        max_bytes_encrypted=100 * 1024 * 1024 * 1024  # 100 GB
    )

    frame_length = 8388608
    algorithm = Algorithm.AES_256_GCM_HKDF_SHA512_COMMIT_KEY

    # Stream stdin to stdout
    with client.stream(
        mode='e',
        source=sys.stdin.buffer,
        materials_manager=cmm,
        frame_length=frame_length,
        algorithm=algorithm
    ) as encryptor:
        for chunk in encryptor:
            sys.stdout.buffer.write(chunk)


if __name__ == '__main__':
    main()
