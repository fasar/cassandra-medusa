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
import logging
import typing as t
from abc import ABC, abstractmethod

try:
    import aws_encryption_sdk
    from aws_encryption_sdk import CommitmentPolicy
    from aws_cryptographic_material_providers.mpl import AwsCryptographicMaterialProviders
    from aws_cryptographic_material_providers.mpl.config import MaterialProvidersConfig
    from aws_cryptographic_material_providers.mpl.models import AesWrappingAlg, CreateRawAesKeyringInput
    AWS_ENCRYPTION_SDK_AVAILABLE = True
except ImportError:
    AWS_ENCRYPTION_SDK_AVAILABLE = False


class EncryptionConfig:
    """Configuration for client-side encryption"""
    
    def __init__(self, cse_key: t.Optional[str] = None, key_namespace: str = "medusa-cse-keys", 
                 key_name: str = "medusa-aes-wrapping-key"):
        self.cse_key = cse_key
        self.key_namespace = key_namespace
        self.key_name = key_name
        if(self.is_enabled):
            self._validated_cse_key = self._prepare_cse_key()
        else:
            self._validated_cse_key = None
        
    @property
    def is_enabled(self) -> bool:
        """Check if encryption is enabled and available"""
        return self.cse_key is not None and AWS_ENCRYPTION_SDK_AVAILABLE
    
    def get_cse_key(self) -> bytes:
        """get CSE key for use"""
        if self._validated_cse_key == None:
            raise ValueError("CSE key is required for encryption")
        
        return self._validated_cse_key
        
    def _prepare_cse_key(self) -> bytes:
        """Validate and prepare the CSE key for use"""            
        if not self.cse_key:
            raise ValueError("CSE key is required for encryption")
            
        # Convert the CSE key to bytes if it's a string
        if isinstance(self.cse_key, str):
            # Assume it's base64 encoded if it's a string
            try:
                cse_key_bytes = base64.b64decode(self.cse_key)
            except Exception:
                # If base64 decode fails, encode the string directly
                cse_key_bytes = self.cse_key.encode('utf-8')
        else:
            cse_key_bytes = self.cse_key
                
        # Ensure the key is exactly 32 bytes for AES-256
        if len(cse_key_bytes) != 32:
            logging.warning("CSE key length is not 32 bytes, padding/truncating to 32 bytes")
            if len(cse_key_bytes) < 32:
                cse_key_bytes = cse_key_bytes.ljust(32, b'\x00')
            else:
                cse_key_bytes = cse_key_bytes[:32]
                
        self._validated_cse_key = cse_key_bytes
        return self._validated_cse_key


class BaseEncryptor(ABC):
    """Abstract base class for encryption operations"""
    
    @abstractmethod
    def encrypt_stream(self, source_stream: io.IOBase, context: t.Dict[str, str]) -> io.IOBase:
        """Encrypt a stream with the given context"""
        pass
    
    @abstractmethod
    def decrypt_stream(self, encrypted_stream: io.IOBase) -> io.IOBase:
        """Decrypt a stream"""
        pass

class NoOpEncryptor(BaseEncryptor):
    """No-operation encryptor that does nothing (for when encryption is disabled)"""
    
    def encrypt_stream(self, source_stream: io.IOBase, context: t.Dict[str, str]) -> io.IOBase:
        return source_stream
    
    def decrypt_stream(self, encrypted_stream: io.IOBase) -> io.IOBase:
        return encrypted_stream


class AWSEncryptor(BaseEncryptor):
    """AWS Encryption SDK based encryptor"""
    
    def __init__(self, config: EncryptionConfig):
        if not AWS_ENCRYPTION_SDK_AVAILABLE:
            raise RuntimeError("AWS Encryption SDK is not available")
            
        self.config = config
        self.encryption_client = None
        self.raw_aes_keyring = None
        self._setup_encryption()
    
    def _setup_encryption(self):
        """Setup AWS Encryption SDK components"""
        try:
            # Initialize the encryption SDK client with strict commitment policy
            self.encryption_client = aws_encryption_sdk.EncryptionSDKClient(
                commitment_policy=CommitmentPolicy.REQUIRE_ENCRYPT_REQUIRE_DECRYPT
            )

            # Get validated key
            cse_key_bytes = self.config.get_cse_key()

            # Create a Raw AES keyring for encryption/decryption
            mat_prov = AwsCryptographicMaterialProviders(config=MaterialProvidersConfig())
            keyring_input = CreateRawAesKeyringInput(
                key_namespace=self.config.key_namespace,
                key_name=self.config.key_name,
                wrapping_key=cse_key_bytes,
                wrapping_alg=AesWrappingAlg.ALG_AES256_GCM_IV12_TAG16
            )
            self.raw_aes_keyring = mat_prov.create_raw_aes_keyring(input=keyring_input)
            
            logging.info("AWS client-side encryption initialized successfully")
        except Exception as e:
            logging.error(f"Failed to setup AWS client-side encryption: {e}")
            raise
    
    def encrypt_stream(self, source_stream: io.IOBase, context: t.Dict[str, str]) -> io.IOBase:
        """Encrypt a stream using AWS Encryption SDK"""
        return self.encryption_client.stream(
            mode='e',
            source=source_stream,
            keyring=self.raw_aes_keyring,
            encryption_context=context
        )
    
    def decrypt_stream(self, encrypted_stream: io.IOBase) -> io.IOBase:
        """Decrypt a stream using AWS Encryption SDK"""
        return self.encryption_client.stream(
            mode='d',
            source=encrypted_stream,
            keyring=self.raw_aes_keyring
        )



class EncryptionManager:
    """Manages encryption operations for storage"""
    
    def __init__(self, config: EncryptionConfig):
        self.config = config
        self.encryptor = self._create_encryptor()
    
    def _create_encryptor(self) -> BaseEncryptor:
        """Create appropriate encryptor based on configuration"""
        if not self.config.is_enabled:
            logging.info("Client-side encryption disabled")
            return NoOpEncryptor()
        
        if not AWS_ENCRYPTION_SDK_AVAILABLE:
            raise RuntimeError(
                "CSE key provided but AWS Encryption SDK is not available. "
                "Please install: pip install aws-encryption-sdk[raw] aws-cryptographic-material-providers"
            )
        
        return AWSEncryptor(self.config)
    
    @property
    def is_enabled(self) -> bool:
        """Check if encryption is enabled"""
        return self.config.is_enabled
    
    def create_encryption_context(self, object_key: str, storage_provider: str) -> t.Dict[str, str]:
        """
        Create encryption context for the given object.
        The context includes metadata about the object being encrypted.
        Context is not encrypted.
        """
        return {
            "object_key": object_key,
            "storage_provider": storage_provider
        }
    
    def encrypt_stream(self, source_stream: io.IOBase, object_key: str, storage_provider: str) -> io.IOBase:
        """Encrypt a stream using the configured encryptor"""
        context = self.create_encryption_context(object_key, storage_provider)
        return self.encryptor.encrypt_stream(source_stream, context)
    
    def decrypt_stream(self, encrypted_stream: io.IOBase) -> io.IOBase:
        """Decrypt a stream using the configured encryptor"""
        return self.encryptor.decrypt_stream(encrypted_stream)
