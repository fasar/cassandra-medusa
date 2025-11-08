# -*- coding: utf-8 -*-
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
Example of how to test the encryption functionality in isolation
"""

import base64
import io
import unittest
from unittest.mock import patch, Mock

from medusa.storage.encryption import EncryptionConfig, EncryptionManager, AWSEncryptor


class TestEncryptionFunctionality(unittest.TestCase):
    """Functional tests for encryption functionality"""
    
    def test_encryption_config_validation(self):
        """Test encryption configuration validation"""
        # Test valid 32-byte key
        key_32_bytes = "a" * 32
        config = EncryptionConfig(cse_key=key_32_bytes)
        validated_key = config.validate_and_prepare_key()
        self.assertEqual(len(validated_key), 32)
        self.assertEqual(validated_key, key_32_bytes.encode('utf-8'))
        
    def test_encryption_config_base64_key(self):
        """Test encryption configuration with base64 encoded key"""
        original_key = b"a" * 32
        b64_key = base64.b64encode(original_key).decode('utf-8')
        config = EncryptionConfig(cse_key=b64_key)
        validated_key = config.validate_and_prepare_key()
        self.assertEqual(validated_key, original_key)
    
    def test_encryption_manager_disabled_by_default(self):
        """Test that encryption manager is disabled by default"""
        config = EncryptionConfig()
        manager = EncryptionManager(config)
        self.assertFalse(manager.is_enabled)
    
    @patch('medusa.storage.encryption.AWS_ENCRYPTION_SDK_AVAILABLE', True)
    @patch('medusa.storage.encryption.aws_encryption_sdk')
    @patch('medusa.storage.encryption.AwsCryptographicMaterialProviders')
    @patch('medusa.storage.encryption.MaterialProvidersConfig')
    @patch('medusa.storage.encryption.CreateRawAesKeyringInput')
    @patch('medusa.storage.encryption.AesWrappingAlg')
    def test_aws_encryptor_setup(self, mock_alg, mock_input, mock_config_cls,
                                mock_providers, mock_aws_sdk):
        """Test AWS encryptor setup with mocked dependencies"""
        # Setup mocks
        mock_client = Mock()
        mock_aws_sdk.EncryptionSDKClient.return_value = mock_client
        mock_aws_sdk.CommitmentPolicy.REQUIRE_ENCRYPT_REQUIRE_DECRYPT = "mock_policy"
        
        mock_providers_instance = Mock()
        mock_providers.return_value = mock_providers_instance
        mock_keyring = Mock()
        mock_providers_instance.create_raw_aes_keyring.return_value = mock_keyring
        
        mock_alg.ALG_AES256_GCM_IV12_TAG16 = "mock_alg"
        
        # Create config and encryptor
        config = EncryptionConfig(cse_key="test_key_1234567890123456789012")
        encryptor = AWSEncryptor(config)
        
        # Verify setup
        self.assertIsNotNone(encryptor.encryption_client)
        self.assertIsNotNone(encryptor.raw_aes_keyring)
        
        # Verify AWS SDK was called correctly
        mock_aws_sdk.EncryptionSDKClient.assert_called_once_with(
            commitment_policy="mock_policy"
        )
    
    def test_encryption_context_creation(self):
        """Test encryption context creation"""
        config = EncryptionConfig()
        manager = EncryptionManager(config)
        
        context = manager.create_encryption_context("test/object.db", "s3")
        expected = {
            "medusa": "backup",
            "object_key": "test/object.db",
            "storage_provider": "s3"
        }
        self.assertEqual(context, expected)
    
    def test_noop_encryptor_passthrough(self):
        """Test that NoOp encryptor passes streams through unchanged"""
        config = EncryptionConfig()  # No key = NoOp encryptor
        manager = EncryptionManager(config)
        
        test_data = b"test data for encryption"
        input_stream = io.BytesIO(test_data)
        
        # Test encryption (should be passthrough)
        encrypted_stream = manager.encrypt_stream(input_stream, "test.db", "s3")
        self.assertIs(encrypted_stream, input_stream)
        
        # Test decryption (should be passthrough)
        decrypted_stream = manager.decrypt_stream(input_stream)
        self.assertIs(decrypted_stream, input_stream)


def run_encryption_example():
    """Example function demonstrating encryption usage"""
    print("=== Medusa Encryption Example ===")
    
    # Example 1: Disabled encryption
    print("\n1. Disabled encryption:")
    config_disabled = EncryptionConfig()
    manager_disabled = EncryptionManager(config_disabled)
    print(f"   Encryption enabled: {manager_disabled.is_enabled}")
    
    # Example 2: Enabled encryption (would work if AWS SDK is available)
    print("\n2. Configured encryption:")
    test_key = "a" * 32  # 32-byte key
    config_enabled = EncryptionConfig(cse_key=test_key)
    print(f"   Key length: {len(config_enabled.validate_and_prepare_key())} bytes")
    print(f"   Available: {config_enabled.is_enabled}")
    
    # Example 3: Encryption context
    print("\n3. Encryption context example:")
    context = manager_disabled.create_encryption_context("backups/table1.db", "s3")
    for key, value in context.items():
        print(f"   {key}: {value}")
    
    print("\n=== End Example ===")


if __name__ == '__main__':
    # Run example
    run_encryption_example()
    
    # Run tests
    print("\nRunning tests...")
    unittest.main(verbosity=2)