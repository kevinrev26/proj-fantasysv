import pytest
from datetime import timedelta
from unittest.mock import patch
from jose import JWTError
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user_id_from_token,
    create_access_token_for_user,
    create_refresh_token_for_user
)
from app.config import settings
from fastapi import Request
from unittest.mock import Mock
from app import models


def test_verify_password():
    """Test that password verification works correctly."""
    password = "test_password"
    hashed = hash_password(password)
    
    # Test correct password
    assert verify_password(password, hashed) is True
    
    # Test incorrect password
    assert verify_password("wrong_password", hashed) is False
    
    # Test with empty strings
    assert verify_password("", hash_password("")) is True

def test_create_access_token():
    """Test that access token creation works correctly."""
    data = {"user_id": 1, "username": "testuser"}
    token = create_access_token(data)
    
    assert isinstance(token, str)
    assert len(token) > 0
    
    # Test with custom expiration
    token_with_expiry = create_access_token(data, expires_delta=timedelta(minutes=60))
    assert isinstance(token_with_expiry, str)
    assert len(token_with_expiry) > 0

def test_create_refresh_token():
    """Test that refresh token creation works correctly."""
    data = {"user_id": 1, "username": "testuser"}
    token = create_refresh_token(data)
    
    assert isinstance(token, str)
    assert len(token) > 0
    
    # Test with custom expiration
    token_with_expiry = create_refresh_token(data, expires_delta=timedelta(days=7))
    assert isinstance(token_with_expiry, str)
    assert len(token_with_expiry) > 0

def test_decode_token_valid():
    """Test that valid token decoding works correctly."""
    data = {"user_id": 1, "username": "testuser"}
    token = create_access_token(data)
    
    decoded = decode_token(token)
    assert decoded is not None
    assert decoded["user_id"] == 1
    assert decoded["username"] == "testuser"

def test_decode_token_invalid():
    """Test that invalid token decoding returns None."""
    # Test with invalid token
    decoded = decode_token("invalid.token.here")
    assert decoded is None
    
    # Test with expired token
    expired_data = {"user_id": 1, "username": "testuser"}
    expired_token = create_access_token(expired_data, expires_delta=timedelta(minutes=-1))
    decoded = decode_token(expired_token)
    assert decoded is None

def test_decode_token_empty():
    """Test that empty token decoding returns None."""
    decoded = decode_token("")
    assert decoded is None

def test_decode_token_none():
    """Test that None token decoding returns None."""
    decoded = decode_token(None)
    assert decoded is None

def test_password_security():
    """Test that passwords are properly secured."""
    password = "complex_password_123!@#"
    hashed = hash_password(password)
    
    # Hash should be different each time due to salt (bcrypt behavior)
    # But verify should work consistently
    assert verify_password(password, hashed) is True
    assert verify_password("wrong_password", hashed) is False
    
    # Test that the hash is actually long and complex
    assert len(hashed) > 50
    assert hashed.startswith("$2b$")

def test_edge_cases():
    """Test edge cases for all functions."""
    # Test with special characters
    special_password = "p@ssw0rd!#$%^&*()"
    hashed = hash_password(special_password)
    assert verify_password(special_password, hashed) is True
    
    # Test with unicode
    unicode_password = "p@ssw0rdñáéíóú"
    hashed = hash_password(unicode_password)
    assert verify_password(unicode_password, hashed) is True
    
    # Test with very long password
    long_password = "a" * 1000
    hashed = hash_password(long_password)
    assert verify_password(long_password, hashed) is True

def test_get_current_user_id_from_token_valid():
    """Test that get_current_user_id_from_token works with valid token."""
    # Create a valid token
    data = {"sub": "123", "username": "testuser"}
    token = create_access_token(data)
    
    # Mock request
    mock_request = Mock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}
    
    # Test extraction
    user_id = get_current_user_id_from_token(mock_request)
    assert user_id == 123

def test_get_current_user_id_from_token_invalid_header():
    """Test that get_current_user_id_from_token handles invalid headers."""
    mock_request = Mock()
    mock_request.headers = {"Authorization": "Invalid Token"}
    
    with pytest.raises(Exception):
        get_current_user_id_from_token(mock_request)

def test_get_current_user_id_from_token_missing_header():
    """Test that get_current_user_id_from_token handles missing headers."""
    mock_request = Mock()
    mock_request.headers = {}
    
    with pytest.raises(Exception):
        get_current_user_id_from_token(mock_request)

def test_get_current_user_id_from_token_expired_token():
    """Test that get_current_user_id_from_token handles expired tokens."""
    # Create an expired token
    data = {"sub": "123", "username": "testuser"}
    expired_token = create_access_token(data, expires_delta=timedelta(minutes=-1))
    
    mock_request = Mock()
    mock_request.headers = {"Authorization": f"Bearer {expired_token}"}
    
    with pytest.raises(Exception):
        get_current_user_id_from_token(mock_request)

def test_get_current_user_id_from_token_invalid_user_id():
    """Test that get_current_user_id_from_token handles invalid user ID."""
    # Create a token with non-integer user ID
    data = {"sub": "not_a_number", "username": "testuser"}
    token = create_access_token(data)
    
    mock_request = Mock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}
    
    with pytest.raises(Exception):
        get_current_user_id_from_token(mock_request)

def test_create_access_token_for_user():
    """Test that creating access token for user works correctly."""
    # Create a mock user
    mock_user = Mock()
    mock_user.id = 123
    mock_user.username = "testuser"
    mock_user.role.value = "user"
    
    token = create_access_token_for_user(mock_user)
    
    assert isinstance(token, str)
    assert len(token) > 0

def test_create_refresh_token_for_user():
    """Test that creating refresh token for user works correctly."""
    # Create a mock user
    mock_user = Mock()
    mock_user.id = 123
    mock_user.username = "testuser"
    mock_user.role.value = "user"
    
    token = create_refresh_token_for_user(mock_user)
    
    assert isinstance(token, str)
    assert len(token) > 0
