from cryptography.fernet import Fernet, InvalidToken
import base64
import hashlib
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ORDER_SECRET_KEY = os.getenv("ORDER_SECRET_HASHKEY", "Transpek_Industry_Limited")  # Default key for quick encryption/decryption

class EncryptionManager:
    
    def __init__(self, key: str):
        
        # Convert variable-length key to a fixed 32-byte Fernet key
        self.original_key = key
        self.fernet_key = self._generate_fernet_key(key)
        self.cipher = Fernet(self.fernet_key)
    
    def _generate_fernet_key(self, key: str) -> bytes:
        # Hash the key multiple times to get 32 bytes
        hash_obj = hashlib.sha256(key.encode('utf-8'))
        hash_bytes = hash_obj.digest()  # 32 bytes
        
        # Encode to base64 (Fernet requires base64-encoded 32-byte key)
        fernet_key = base64.urlsafe_b64encode(hash_bytes)
        
        return fernet_key
    
    def encrypt(self, plaintext: str) -> str:
        try:
            # Convert plaintext to bytes
            plaintext_bytes = plaintext.encode('utf-8')
            
            # Encrypt using Fernet
            ciphertext = self.cipher.encrypt(plaintext_bytes)
            
            # Return as string (already base64 encoded by Fernet)
            return ciphertext.decode('utf-8')
            
        except Exception as e:
            logger.error(f"Encryption error: {str(e)}")
            raise
    
    def decrypt(self, ciphertext: str) -> str:
        try:
            # Convert ciphertext back to bytes
            ciphertext_bytes = ciphertext.encode('utf-8')
            
            # Decrypt using Fernet
            plaintext_bytes = self.cipher.decrypt(ciphertext_bytes)
            
            # Convert bytes back to string
            return plaintext_bytes.decode('utf-8')
            
        except InvalidToken:
            logger.error("Decryption failed: Invalid token or wrong key")
            raise InvalidToken("Decryption failed: Invalid token or wrong key")
        except Exception as e:
            logger.error(f"Decryption error: {str(e)}")
            raise


def encrypt(plaintext: str) -> str:
    """Quick encryption using BASE_KEY"""
    manager = EncryptionManager(str(ORDER_SECRET_KEY))
    return manager.encrypt(plaintext)


def decrypt(ciphertext: str) -> str:
    """Quick decryption using BASE_KEY"""
    manager = EncryptionManager(str(ORDER_SECRET_KEY))
    return manager.decrypt(ciphertext)