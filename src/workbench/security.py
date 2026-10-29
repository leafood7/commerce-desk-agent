"""Revocable bearer sessions: only hashes of credentials are persisted."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from workbench.models import AuthSession, LoginFailure, Organization, User, now


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    )
    return "scrypt$32768$8$1$" + salt + "$" + key.hex()


def password_matches(password: str, encoded: str) -> bool:
    try:
        method, n, r, p, salt, expected = encoded.split("$")
        if (method, n, r, p) != ("scrypt", "32768", "8", "1"):
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = password_hash(secrets.token_urlsafe(32))


def login(
    session: Session,
    organization: str,
    email: str,
    password: str,
    address: str,
    lifetime_minutes: int,
) -> tuple[str, User]:
    account, address_hash = digest(organization + ":" + email), digest(address)
    since = now() - timedelta(minutes=15)
    recent = LoginFailure.created_at >= since
    account_count = session.scalar(
        select(func.count())
        .select_from(LoginFailure)
        .where(recent, LoginFailure.account_hash == account)
    )
    address_count = session.scalar(
        select(func.count())
        .select_from(LoginFailure)
        .where(recent, LoginFailure.address_hash == address_hash)
    )
    if account_count >= 10 or address_count >= 30:
        raise HTTPException(429, "Too many login attempts", headers={"Retry-After": "900"})
    user = session.scalar(
        select(User)
        .join(Organization)
        .where(Organization.slug == organization, User.email == email)
    )
    valid = password_matches(password, user.password_hash if user else _DUMMY_HASH)
    if not user or not user.active or not valid:
        session.add(LoginFailure(account_hash=account, address_hash=address_hash))
        session.commit()
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=digest(token),
            user_id=user.id,
            expires_at=now() + timedelta(minutes=lifetime_minutes),
        )
    )
    session.commit()
    return token, user


def authenticate(session: Session, token: str) -> User:
    if not 32 <= len(token) <= 128:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    user = session.scalar(
        select(User)
        .join(AuthSession)
        .where(
            AuthSession.token_hash == digest(token),
            AuthSession.expires_at > now(),
            User.active.is_(True),
        )
    )
    if user is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    return user
