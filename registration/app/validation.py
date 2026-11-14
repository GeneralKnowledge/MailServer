"""Username and password rules. mailcow applies its own password policy too."""

from __future__ import annotations

import re

# Conservative local part: letters, digits, dot, hyphen, underscore. Must start
# and end with a letter or digit, no doubled separators. Stricter than RFC 5321
# on purpose: it avoids addresses that some providers or forms reject.
_USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|[._-](?=[a-z0-9]))*$")
MAX_USERNAME_LEN = 32
MAX_PASSWORD_LEN = 128

# A handful of the most common passwords; mailcow's own policy and the length
# rule do most of the work.
_COMMON = {
    "password", "password1", "password123", "123456789012", "qwertyuiop", "letmein", "welcome",
    "iloveyou", "administrator", "changeme", "passw0rd", "qwerty123456", "1q2w3e4r5t6y",
}


def normalise_username(raw: str) -> str:
    return (raw or "").strip().lower()


def username_error(username: str, *, reserved: frozenset[str], min_len: int) -> str | None:
    """Return a user-facing error, or None if the format is acceptable."""
    if not username:
        return "Choose a username."
    if len(username) < min_len:
        return f"Usernames must be at least {min_len} characters."
    if len(username) > MAX_USERNAME_LEN:
        return f"Usernames can be at most {MAX_USERNAME_LEN} characters."
    if not _USERNAME_RE.match(username):
        return ("Use lowercase letters, numbers, and single dots, hyphens or underscores "
                "between them. Start and end with a letter or number.")
    bare = re.sub(r"[._-]", "", username)
    if username in reserved or bare in reserved:
        return "That username is reserved."
    return None


def password_error(password: str, confirm: str, *, username: str, min_len: int) -> str | None:
    if password != confirm:
        return "The passwords don't match."
    if len(password) < min_len:
        return f"Passwords must be at least {min_len} characters."
    if len(password) > MAX_PASSWORD_LEN:
        return f"Passwords can be at most {MAX_PASSWORD_LEN} characters."
    lowered = password.lower()
    if lowered in _COMMON or len(set(password)) < 5:
        return "That password is too easy to guess."
    if username and len(username) >= 3 and username in lowered:
        return "The password must not contain your username."
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    if classes < 3 and len(password) < 20:
        return ("Use at least three of: lowercase, uppercase, numbers, symbols "
                "(or a passphrase of 20+ characters).")
    return None
