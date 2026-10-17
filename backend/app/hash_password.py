"""`make hash-password`: prompt for a password and print its argon2id hash for `.env`.

The hash is printed with every `$` doubled. The Makefile includes `.env` as make syntax, where a
single `$` would be expanded (and the hash mangled); `$$` reaches the app as one `$`. Values
set some other way (a Kubernetes Secret, a shell export) take the hash as is, with single `$`.
"""

from __future__ import annotations

import getpass
import sys

from app.services.auth import hash_password


def main() -> int:
    password = getpass.getpass("Password: ")
    if not password:
        print("hash-password: empty password", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat: ") != password:
        print("hash-password: the passwords don't match", file=sys.stderr)
        return 1
    print("For .env ($ doubled for make), e.g. ADMIN_PASSWORD_HASH=<this>:", file=sys.stderr)
    print(hash_password(password).replace("$", "$$"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
