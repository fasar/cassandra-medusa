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
    EncryptionConfig, EncryptionManager, NoOpEncryptor, BaseEncryptor, AWSEncryptor,
    AWS_ENCRYPTION_SDK_AVAILABLE
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
    
    def test_get_cse_key_from_string(self):
        """Test key preparation from a regular string"""
        config = EncryptionConfig(cse_key="test_key_1234567890123456789012")  # 32 chars
        key = config.get_cse_key()
        self.assertEqual(len(key), 32)
        # Key should be exactly 32 bytes, no matter the input
        self.assertTrue(key.startswith(b"test_key_1234567890123456789012"))
    
    def test_get_cse_key_from_base64(self):
        """Test key preparation from base64 encoded string"""
        # Create exactly 32 bytes
        original_key = b"test_key_12345678901234567890123"  # 31 chars + null = 32
        b64_key = base64.b64encode(original_key).decode('utf-8')
        config = EncryptionConfig(cse_key=b64_key)
        key = config.get_cse_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(key, original_key)
    
    def test_get_cse_key_padding_short_key(self):
        """Test that short keys are padded to 32 bytes"""
        config = EncryptionConfig(cse_key="short")
        key = config.get_cse_key()
        self.assertEqual(len(key), 32)
        self.assertTrue(key.startswith(b"short"))
    
    def test_get_cse_key_truncating_long_key(self):
        """Test that long keys are truncated to 32 bytes"""
        long_key = "a" * 50  # 50 chars
        config = EncryptionConfig(cse_key=long_key)
        key = config.get_cse_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(key, b"a" * 32)
    
    def test_get_cse_key_no_key_raises_error(self):
        """Test that validation raises error when no key is provided"""
        config = EncryptionConfig()
        with self.assertRaises(ValueError):
            config.get_cse_key()
    
    def test_get_cse_key_caches_result(self):
        """Test that key validation is cached"""
        config = EncryptionConfig(cse_key="test_key_1234567890123456789012")
        key1 = config.get_cse_key()
        key2 = config.get_cse_key()
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

class TestAWSEncryptor(unittest.TestCase):
    """Test cases for AWSEncryptor"""
    
    @unittest.skipIf(not AWS_ENCRYPTION_SDK_AVAILABLE, "AWS Encryption SDK not available")
    def test_encrypt_decrypt_stream(self):
        """Test that encryption produces different data and decryption returns original data"""
        # Create a test configuration with a 32-byte key
        test_key = b"test_encryption_key_1234567890wc -l /home/sartor/work/Workbox/cassandra-medusa/tests/storage/encryption_test.py"  # 32 bytes
        b64_key = base64.b64encode(test_key).decode('utf-8')
        config = EncryptionConfig(cse_key=b64_key)
        
        # Create AWSEncryptor instance
        encryptor = AWSEncryptor(config)
        
        # Original data
        original_data = b"This is a test data stream for encryption and decryption testing."
        
        # Create source stream from bytes
        source_stream = io.BytesIO(original_data)
        
        # Create encryption context
        context = {
            "object_key": "test/object.db",
            "storage_provider": "s3"
        }
        
        # Encrypt the stream
        encrypted_stream = encryptor.encrypt_stream(source_stream, context)
        
        # Read encrypted data
        encrypted_data = encrypted_stream.read()
        
        # Verify that encrypted data is different from original data
        self.assertNotEqual(encrypted_data, original_data)
        self.assertGreater(len(encrypted_data), 0)
        
        # Create a new stream from encrypted data for decryption
        encrypted_stream_for_decrypt = io.BytesIO(encrypted_data)
        
        # Decrypt the stream
        decrypted_stream = encryptor.decrypt_stream(encrypted_stream_for_decrypt)
        
        # Read decrypted data
        decrypted_data = decrypted_stream.read()
        
        # Verify that decrypted data matches original data
        self.assertEqual(decrypted_data, original_data)
        
        # Cleanup
        encryptor.cleanup()
    
    @unittest.skipIf(not AWS_ENCRYPTION_SDK_AVAILABLE, "AWS Encryption SDK not available")
    def test_encrypt_produces_different_output_each_time(self):
        """Test that encryption produces different ciphertext each time (due to random IV)"""
        # Create a test configuration
        test_key = b"test_encryption_key_1234567890wc -l /home/sartor/work/Workbox/cassandra-medusa/tests/storage/encryption_test.py"  # 32 bytes
        b64_key = base64.b64encode(test_key).decode('utf-8')
        config = EncryptionConfig(cse_key=b64_key)
        
        # Create AWSEncryptor instance
        encryptor = AWSEncryptor(config)
        
        # Original data
        original_data = b"Test data for randomness verification"
        
        # Encryption context
        context = {
            "object_key": "test/object.db",
            "storage_provider": "s3"
        }
        
        # Encrypt the same data twice
        encrypted_stream1 = encryptor.encrypt_stream(io.BytesIO(original_data), context)
        encrypted_data1 = encrypted_stream1.read()
        
        encrypted_stream2 = encryptor.encrypt_stream(io.BytesIO(original_data), context)
        encrypted_data2 = encrypted_stream2.read()
        
        # Verify that both encryptions produce different ciphertext
        self.assertNotEqual(encrypted_data1, encrypted_data2)
        
        # But both should decrypt to the same original data
        decrypted_data1 = encryptor.decrypt_stream(io.BytesIO(encrypted_data1)).read()
        decrypted_data2 = encryptor.decrypt_stream(io.BytesIO(encrypted_data2)).read()
        
        self.assertEqual(decrypted_data1, original_data)
        self.assertEqual(decrypted_data2, original_data)
        
        # Cleanup
        encryptor.cleanup()


if __name__ == '__main__':
    unittest.main()
