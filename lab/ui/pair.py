"""Pair a phone or laptop with the interactive UI (spec v0.3 §2.2): run in a VPS shell by the owner, it prints a
one-time code (20 Crockford base32 characters, 100 bits, valid 5 minutes) and hands only its SHA-256 to the web
server through <data>/control/pairing/; the web server consumes and deletes it. The code is never written to a
file or a log. The device must also be in the actors file (ui-actors.json).

  python3 -I lab/ui/pair.py
"""

import hashlib
import json
import os
import secrets
import time

DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab")
CONTROL = os.environ.get("LAB_CONTROL", os.path.join(DATA, "control"))
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
VALID_SECS = 300


def new_code():
    """(code, its SHA-256): 20 characters from the 32-letter Crockford alphabet."""
    code = "".join(secrets.choice(CROCKFORD) for _ in range(20))
    return code, hashlib.sha256(code.encode()).hexdigest()


def main():
    code, sha = new_code()
    d = os.path.join(CONTROL, "pairing")
    os.makedirs(d, exist_ok=True)
    now = int(time.time())
    path = os.path.join(d, f"{now}-{secrets.token_hex(4)}.json")
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w") as f:
        json.dump({"sha256": sha, "expires_unix": now + VALID_SECS}, f)
    os.replace(path + ".tmp", path)
    shown = "-".join(code[i:i + 4] for i in range(0, 20, 4))
    print(f"Koppelcode (5 minuten geldig, één keer): {shown}")


if __name__ == "__main__":
    main()
