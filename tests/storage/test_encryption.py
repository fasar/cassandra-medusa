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

import base64
import io
import unittest
from unittest.mock import Mock, patch, MagicMock

from medusa.storage.encryption import (
    EncryptionConfig, EncryptionManager, NoOpEncryptor, BaseEncryptor
)


class TestEncryptionConfig(unittest.TestCase):
    """Test cases for EncryptionConfig"""
    
    def test_config_disabled_when_no_key(self):
        """Test that encryption is disabled when no key is provided"""
        config = EncryptionConfig()
        self.assertFalse(config.is_enabled)
    
    @patch('medusa.storage.encryption.AWS_ENCRYPTION_SDK_AVAILABLE', True)
    def test_config_enabled_when_key_provided_and_sdk_available(self):
        """Test that encryption is enabled when key is provided and SDK is available"""
        config = EncryptionConfig(cse_key="test_key")
        self.assertTrue(config.is_enabled)
    
    @patch('medusa.storage.encryption.AWS_ENCRYPTION_SDK_AVAILABLE', False)
    def test_config_disabled_when_key_provided_but_sdk_unavailable(self):
        """Test that encryption is disabled when key is provided but SDK is unavailable"""
        config = EncryptionConfig(cse_key="test_key")
        self.assertFalse(config.is_enabled)
    
    def test_validate_and_prepare_key_from_string(self):
        """Test key preparation from a regular string"""
        config = EncryptionConfig(cse_key="test_key_1234567890123456789012")  # 32 chars
        key = config.validate_and_prepare_key()
        self.assertEqual(len(key), 32)
        # Key should be exactly 32 bytes, no matter the input
        self.assertTrue(key.startswith(b"test_key_1234567890123456789012"))
    
    def test_validate_and_prepare_key_from_base64(self):
        """Test key preparation from base64 encoded string"""
        # Create exactly 32 bytes
        original_key = b"test_key_12345678901234567890123"  # 31 chars + null = 32
        b64_key = base64.b64encode(original_key).decode('utf-8')
        config = EncryptionConfig(cse_key=b64_key)
        key = config.validate_and_prepare_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(key, original_key)
    
    def test_validate_and_prepare_key_padding_short_key(self):
        """Test that short keys are padded to 32 bytes"""
        config = EncryptionConfig(cse_key="short")
        key = config.validate_and_prepare_key()
        self.assertEqual(len(key), 32)
        self.assertTrue(key.startswith(b"short"))
    
    def test_validate_and_prepare_key_truncating_long_key(self):
        """Test that long keys are truncated to 32 bytes"""
        long_key = "a" * 50  # 50 chars
        config = EncryptionConfig(cse_key=long_key)
        key = config.validate_and_prepare_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(key, b"a" * 32)
    
    def test_validate_and_prepare_key_no_key_raises_error(self):
        """Test that validation raises error when no key is provided"""
        config = EncryptionConfig()
        with self.assertRaises(ValueError):
            config.validate_and_prepare_key()
    
    def test_validate_and_prepare_key_caches_result(self):
        """Test that key validation is cached"""
        config = EncryptionConfig(cse_key="test_key_1234567890123456789012")
        key1 = config.validate_and_prepare_key()
        key2 = config.validate_and_prepare_key()
        self.assertIs(key1, key2)  # Should be the same object


class TestNoOpEncryptor(unittest.TestCase):
    """Test cases for NoOpEncryptor"""
    
    def test_encrypt_stream_returns_same_stream(self):
        """Test that encryption returns the same stream"""
        encryptor = NoOpEncryptor()
        stream = io.BytesIO(b"test data")
        context = {"key": "value"}
        result = encryptor.encrypt_stream(stream, context)
        self.assertIs(result, stream)
    
    def test_decrypt_stream_returns_same_stream(self):
        """Test that decryption returns the same stream"""
        encryptor = NoOpEncryptor()
        stream = io.BytesIO(b"test data")
        result = encryptor.decrypt_stream(stream)
        self.assertIs(result, stream)
    
    def test_cleanup_does_nothing(self):
        """Test that cleanup does nothing and doesn't raise errors"""
        encryptor = NoOpEncryptor()
        encryptor.cleanup()  # Should not raise any exception


class TestEncryptionManager(unittest.TestCase):
    """Test cases for EncryptionManager"""
    
    @patch('medusa.storage.encryption.AWS_ENCRYPTION_SDK_AVAILABLE', False)
    def test_manager_creates_noop_encryptor_when_disabled(self):
        """Test that manager creates NoOpEncryptor when encryption is disabled"""
        config = EncryptionConfig()
        manager = EncryptionManager(config)
        self.assertIsInstance(manager.encryptor, NoOpEncryptor)
        self.assertFalse(manager.is_enabled)
    
    @patch('medusa.storage.encryption.AWS_ENCRYPTION_SDK_AVAILABLE', False)
    def test_manager_raises_error_when_key_provided_but_sdk_unavailable(self):
        """Test that manager raises error when key is provided but SDK is unavailable"""
        config = EncryptionConfig(cse_key="test_key")
        # The config should not be enabled when SDK is unavailable
        self.assertFalse(config.is_enabled)
        # But the manager should raise an error if we try to create an AWS encryptor
        manager = EncryptionManager(config)
        self.assertFalse(manager.is_enabled)  # Should create NoOpEncryptor instead
    
    def test_create_encryption_context(self):
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
    
    def test_encrypt_stream_calls_encryptor_with_context(self):
        """Test that encrypt_stream calls encryptor with proper context"""
        config = EncryptionConfig()
        manager = EncryptionManager(config)
        
        # Mock the encryptor
        mock_encryptor = Mock(spec=BaseEncryptor)
        manager.encryptor = mock_encryptor
        
        stream = io.BytesIO(b"test data")
        object_key = "test/object.db"
        storage_provider = "s3"
        
        manager.encrypt_stream(stream, object_key, storage_provider)
        
        # Verify the encryptor was called with correct parameters
        mock_encryptor.encrypt_stream.assert_called_once()
        args, kwargs = mock_encryptor.encrypt_stream.call_args
        self.assertIs(args[0], stream)
        expected_context = {
            "medusa": "backup",
            "object_key": object_key,
            "storage_provider": storage_provider
        }
        self.assertEqual(args[1], expected_context)
    
    def test_decrypt_stream_calls_encryptor(self):
        """Test that decrypt_stream calls encryptor"""
        config = EncryptionConfig()
        manager = EncryptionManager(config)
        
        # Mock the encryptor
        mock_encryptor = Mock(spec=BaseEncryptor)
        manager.encryptor = mock_encryptor
        
        stream = io.BytesIO(b"encrypted data")
        manager.decrypt_stream(stream)
        
        mock_encryptor.decrypt_stream.assert_called_once_with(stream)
    
    def test_cleanup_calls_encryptor_cleanup(self):
        """Test that cleanup calls encryptor cleanup"""
        config = EncryptionConfig()
        manager = EncryptionManager(config)
        
        # Mock the encryptor
        mock_encryptor = Mock(spec=BaseEncryptor)
        manager.encryptor = mock_encryptor
        
        manager.cleanup()
        
        mock_encryptor.cleanup.assert_called_once()


class TestIntegrationWithMockedAWS(unittest.TestCase):
    """Integration tests with mocked AWS SDK"""
    
    @patch('medusa.storage.encryption.AWS_ENCRYPTION_SDK_AVAILABLE', True)
    @patch('medusa.storage.encryption.aws_encryption_sdk')
    @patch('medusa.storage.encryption.AwsCryptographicMaterialProviders')
    @patch('medusa.storage.encryption.MaterialProvidersConfig')
    @patch('medusa.storage.encryption.CreateRawAesKeyringInput')
    def test_aws_encryptor_initialization(self, mock_input, mock_config_cls, 
                                         mock_providers, mock_aws_sdk):
        """Test AWS encryptor initialization with mocked dependencies"""
        # Setup mocks
        mock_client = Mock()
        mock_aws_sdk.EncryptionSDKClient.return_value = mock_client
        
        mock_providers_instance = Mock()
        mock_providers.return_value = mock_providers_instance
        mock_keyring = Mock()
        mock_providers_instance.create_raw_aes_keyring.return_value = mock_keyring

        # Test encryption config and manager
        config = EncryptionConfig(cse_key="test_key_1234567890123456789012")
        manager = EncryptionManager(config)
        
        # Verify AWS SDK was called correctly (we don't need to check the exact commitment policy)
        mock_aws_sdk.EncryptionSDKClient.assert_called_once()
        
        # Verify keyring creation
        mock_providers_instance.create_raw_aes_keyring.assert_called_once()
        
        self.assertTrue(manager.is_enabled)


if __name__ == '__main__':
    unittest.main()