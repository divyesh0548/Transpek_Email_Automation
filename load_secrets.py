from cryptography.fernet import Fernet
import os

def load_secrets():
    key = os.getenv("ENV_KEY")
    cipher = Fernet(key.encode())

    with open(".env.enc", "rb") as f:
        decrypted = cipher.decrypt(f.read()).decode()

    # Parse decrypted content as dotenv-style `KEY=value` pairs.
    # - Skip empty lines and comments
    # - Skip lines without '=' (prevents unpack errors)
    # - Trim whitespace around keys/values
    # - Strip one pair of surrounding quotes if present
    for line in decrypted.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        os.environ[k] = v