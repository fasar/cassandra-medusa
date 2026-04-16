#!/usr/bin/env python3
import sys
import os
import aws_encryption_sdk
from aws_encryption_sdk.key_providers.raw import RawMasterKeyProvider
from aws_encryption_sdk.identifiers import WrappingAlgorithm, EncryptionKeyType, Algorithm
from aws_encryption_sdk.internal.crypto.wrapping_keys import WrappingKey
from aws_encryption_sdk.caches.local import LocalCryptoMaterialsCache
from aws_encryption_sdk.materials_managers.caching import CachingCryptoMaterialsManager


def main():
    # Read from stdin, write to stdout
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer

    # Standard 8MB frame length
    frame_length = 8388608

    # To benchmark pure CPU+SDK performance without KMS, we use a StaticMasterKeyProvider
    # Generate an ephemeral key for the benchmark
    key_bytes = os.urandom(32)
    key_provider_id = "medusa-benchmark-provider"
    key_id = "medusa-benchmark-key"

    # Initialize AWS Encryption SDK client
    client = aws_encryption_sdk.EncryptionSDKClient()

    class _StaticKeyProvider(RawMasterKeyProvider):
        provider_id = key_provider_id

        def _get_raw_key(self, id):
            return WrappingKey(
                wrapping_algorithm=WrappingAlgorithm.AES_256_GCM_IV12_TAG16_NO_PADDING,
                wrapping_key=key_bytes,
                wrapping_key_type=EncryptionKeyType.SYMMETRIC
            )

    master_key_provider = _StaticKeyProvider()
    master_key_provider.add_master_key(key_id)

    # Create cache and materials manager exactly as configured in Medusa
    cache = LocalCryptoMaterialsCache(capacity=100)
    cmm = CachingCryptoMaterialsManager(
        master_key_provider=master_key_provider,
        cache=cache,
        max_age=3600.0,
        max_messages_encrypted=100000,
        max_bytes_encrypted=100 * 1024 * 1024 * 1024
    )

    algorithm = Algorithm.AES_256_GCM_HKDF_SHA512_COMMIT_KEY

    try:
        with client.stream(
            mode='e',
            source=input_stream,
            materials_manager=cmm,
            frame_length=frame_length,
            algorithm=algorithm
        ) as encryptor:
            for chunk in encryptor:
                output_stream.write(chunk)

    except BrokenPipeError:
        # Happens when pv or another tool closes the pipe early
        sys.stderr.close()


if __name__ == "__main__":
    main()
