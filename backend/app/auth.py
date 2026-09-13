"""JWT authentication with PBKDF2 password hashing (stdlib only, Windows-safe)."""
import datetime as dt
import hashlib
import hmac
import os

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import config
from .database import get_session
from .models import Parent, User

_bearer = HTTPBearer(auto_error=False)

_PBKDF2_ITERATIONS = 100_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_token(user: User) -> str:
    payload = {
        "sub": user.username,
        "role": user.role,
        "usn": user.usn,
        "name": user.display_name,
        "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=config.JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, config.jwt_secret(), algorithm=config.JWT_ALGORITHM)


def get_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, config.jwt_secret(),
                             algorithms=[config.JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.query(User).filter(User.username == payload["sub"]).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if user.role == "librarian":
        from .models import LibrarianAccount
        account = db.get(LibrarianAccount, user.id)
        if account is None or not account.active:
            raise HTTPException(status_code=403, detail="Librarian account is inactive")
    if user.role == "parent":
        parent = db.query(Parent).filter(Parent.user_id == user.id).one_or_none()
        if parent is None or not parent.active:
            raise HTTPException(status_code=403, detail="Parent account is inactive")
    return user


def get_current_user(user: User = Depends(get_authenticated_user)) -> User:
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return user


def require_role(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"Requires role: {roles}")
        return user
    return checker
