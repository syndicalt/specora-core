"""Generate auth system from infra/auth contract."""
from __future__ import annotations

from forge.ir.model import DomainIR, InfraIR
from forge.targets.base import GeneratedFile, provenance_header


def generate_auth(ir: DomainIR) -> list[GeneratedFile]:
    """Generate auth files if an infra/auth contract exists."""
    auth_infra = next((i for i in ir.infra if i.category == "auth"), None)
    if not auth_infra:
        return []

    return [
        _generate_interface(auth_infra),
        _generate_jwt_provider(auth_infra),
        _generate_middleware(auth_infra),
    ]


def _generate_interface(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Auth provider interface")
    content = f"""{header}
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel


class AuthUser(BaseModel):
    id: str
    email: str
    role: str


class AuthProvider(ABC):
    @abstractmethod
    async def authenticate(self, token: str) -> Optional[AuthUser]: ...

    @abstractmethod
    async def create_token(self, user_data: dict) -> str: ...

    @abstractmethod
    async def refresh_token(self, token: str) -> Optional[str]: ...
"""
    return GeneratedFile(path="backend/auth/interface.py", content=content, provenance=infra.fqn)


def _generate_jwt_provider(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Built-in JWT auth provider")
    content = f"""{header}
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from backend.auth.interface import AuthProvider, AuthUser
from backend.config import AUTH_SECRET, AUTH_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


class JWTAuthProvider(AuthProvider):

    async def authenticate(self, token: str) -> Optional[AuthUser]:
        try:
            payload = jwt.decode(token, AUTH_SECRET, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            email = payload.get("email", "")
            role = payload.get("role", "")
            if user_id is None:
                return None
            return AuthUser(id=user_id, email=email, role=role)
        except JWTError:
            return None

    async def create_token(self, user_data: dict) -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=AUTH_TOKEN_EXPIRE_MINUTES)
        to_encode = {{
            "sub": user_data.get("id", ""),
            "email": user_data.get("email", ""),
            "role": user_data.get("role", ""),
            "exp": expire,
        }}
        return jwt.encode(to_encode, AUTH_SECRET, algorithm=ALGORITHM)

    async def refresh_token(self, token: str) -> Optional[str]:
        user = await self.authenticate(token)
        if user is None:
            return None
        return await self.create_token(user.model_dump())


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)
"""
    return GeneratedFile(path="backend/auth/jwt_provider.py", content=content, provenance=infra.fqn)


def _generate_middleware(infra: InfraIR) -> GeneratedFile:
    header = provenance_header("python", infra.fqn, "Auth middleware — FastAPI dependencies")
    roles = infra.config.get("roles", [])

    content = f"""{header}
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Header

from backend.auth.interface import AuthProvider, AuthUser
from backend.auth.jwt_provider import JWTAuthProvider
from backend.config import AUTH_ENABLED

_provider: Optional[AuthProvider] = None


def get_auth_provider() -> AuthProvider:
    global _provider
    if _provider is None:
        _provider = JWTAuthProvider()
    return _provider


async def require_auth(
    authorization: str = Header(None),
    provider: AuthProvider = Depends(get_auth_provider),
) -> AuthUser:
    if not AUTH_ENABLED:
        return AuthUser(id="anonymous", email="", role="admin")
    if not authorization:
        raise HTTPException(401, detail={{"error": "missing_token"}})
    token = authorization.replace("Bearer ", "")
    user = await provider.authenticate(token)
    if user is None:
        raise HTTPException(401, detail={{"error": "invalid_token"}})
    return user


def require_role(*roles: str):
    async def check(user: AuthUser = Depends(require_auth)) -> AuthUser:
        if not AUTH_ENABLED:
            return user
        if user.role not in roles:
            raise HTTPException(403, detail={{"error": "forbidden", "required_roles": list(roles)}})
        return user
    return check
"""
    return GeneratedFile(path="backend/auth/middleware.py", content=content, provenance=infra.fqn)
