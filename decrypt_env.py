from cryptography.fernet import Fernet
import os

# Get key from environment
key = os.getenv("ENV_KEY")

if not key:
    raise ValueError("ENV_KEY not set in environment")

# Initialize cipher
cipher = Fernet(key.encode())

# Read encrypted file
with open(".env.enc", "rb") as f:
    encrypted_data = f.read()

# Decrypt
try:
    decrypted_data = cipher.decrypt(encrypted_data)
except Exception as e:
    raise ValueError("Decryption failed. Check if ENV_KEY is correct.") from e

# Write back to .env
with open(".env", "wb") as f:
    f.write(decrypted_data)

print("✅ .env file restored successfully")