from cryptography.fernet import Fernet
import os

key = os.getenv("ENV_KEY")
cipher = Fernet(key.encode())

with open(".env", "rb") as f:
    encrypted = cipher.encrypt(f.read())

with open(".env.enc", "wb") as f:
    f.write(encrypted)