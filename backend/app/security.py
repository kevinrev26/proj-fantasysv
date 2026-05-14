from datetime import datetime, timedelta
from typing import Dict, Optional
import structlog
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Request, HTTPException
from . import models
from .config import settings

logger = structlog.get_logger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(plain: str) -> str:
    """Hash a plain password using bcrypt."""
    try:
        hashed = pwd_context.hash(plain)
        logger.info("Password hashed successfully")
        return hashed
    except Exception as e:
        logger.error("Failed to hash password", error=str(e))
        raise

def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against a hashed password."""
    try:
        result = pwd_context.verify(plain, hashed)
        if not result:
            logger.warning("Password verification failed - incorrect password")
        else:
            logger.info("Password verification successful")
        return result
    except Exception as e:
        logger.error("Error during password verification", error=str(e))
        return False

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create a JWT access token."""
    try:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
        logger.info("Access token created successfully")
        return encoded_jwt
    except Exception as e:
        logger.error("Failed to create access token", error=str(e))
        raise

def create_refresh_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create a JWT refresh token."""
    try:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
        logger.info("Refresh token created successfully")
        return encoded_jwt
    except Exception as e:
        logger.error("Failed to create refresh token", error=str(e))
        raise

def decode_token(token: str) -> Optional[Dict]:
    """Decode a JWT token and return the payload."""
    try:
        if not token:
            return None
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        logger.info("Token decoded successfully")
        return payload
    except JWTError as e:
        logger.warning("JWT decoding failed - invalid token or expired", error=str(e))
        return None
    except Exception as e:
        logger.error("Unexpected error during token decoding", error=str(e))
        return None

def get_current_user_id_from_token(request: Request) -> int:
    """Extract user ID from authorization token in request."""
    try:
        # Get the Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            logger.warning("No Authorization header found")
            raise HTTPException(status_code=401, detail="Authorization header missing")
        
        # Check if it's a Bearer token
        if not auth_header.startswith("Bearer "):
            logger.warning("Invalid Authorization header format")
            raise HTTPException(status_code=401, detail="Invalid authorization header format")
        
        # Extract token
        token = auth_header.split(" ")[1]
        
        # Decode token
        payload = decode_token(token)
        if not payload:
            logger.warning("Invalid or expired token")
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        
        # Extract user ID from payload
        user_id = payload.get("sub")
        if not user_id:
            logger.warning("No user ID found in token payload")
            raise HTTPException(status_code=401, detail="Invalid token payload")
        
        # Convert to int
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            logger.error("User ID in token is not a valid integer")
            raise HTTPException(status_code=401, detail="Invalid user ID in token")
        
        logger.info("User ID extracted from token successfully", user_id=user_id)
        return user_id
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error("Error extracting user ID from token", error=str(e))
        raise HTTPException(status_code=401, detail="Could not validate credentials")


def create_access_token_for_user(user: models.User) -> str:
    """Create a JWT access token for a user."""
    try:
        token_data = {"sub": str(user.id), "username": user.username, "role": user.role.value}
        return create_access_token(token_data)
    except Exception as e:
        logger.error("Failed to create access token for user", error=str(e))
        raise

def create_refresh_token_for_user(user: models.User) -> str:
    """Create a JWT refresh token for a user."""
    try:
        token_data = {"sub": str(user.id), "username": user.username, "role": user.role.value}
        return create_refresh_token(token_data)
    except Exception as e:
        logger.error("Failed to create refresh token for user", error=str(e))
        raise
