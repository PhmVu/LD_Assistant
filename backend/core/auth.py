from datetime import datetime, timedelta
from typing import List
from fastapi import Request, HTTPException
from functools import wraps
from jose import JWTError, jwt

SECRET_KEY = "SUPER_SECRET_LD_KEY_V2"  # Should be in env for production
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token or token expired")

def require_auth(roles: List[str] = ["user", "admin"]):
    """Decorator to require specific roles. Depends on Request being in kwargs/args or FastAPI dependency."""
    # Since FastAPI uses Dependencies for request context, we can write a dependency function
    pass

# FastAPI Dependency
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = verify_token(token)
    return payload

def require_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = get_current_user(credentials)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Không đủ quyền truy cập (yêu cầu admin)")
    return payload
