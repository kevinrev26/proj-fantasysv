"""
Legacy seed script — seeds a single admin user from environment variables.
For interactive admin creation with custom credentials, use:

    python scripts/create_admin.py --username admin --email admin@example.com --password secret
"""
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import User, UserRole
from app.security import get_password_hash
from app.config import settings
from sqlalchemy.exc import IntegrityError


def seed_admin():
    db = SessionLocal()
    try:
        admin_email = settings.DEFAULT_ADMIN_EMAIL
        admin_password = settings.DEFAULT_ADMIN_PASSWORD

        existing_admin = db.query(User).filter(User.email == admin_email).first()
        if existing_admin:
            print(f"Admin user {admin_email} already exists.")
            return

        hashed_password = get_password_hash(admin_password)
        new_admin = User(
            username="admin",
            email=admin_email,
            hashed_password=hashed_password,
            role=UserRole.admin,
            is_active=True,
            onboarding_complete=True,
            activation_token=None,
        )

        db.add(new_admin)
        db.commit()
        print(f"Successfully created admin user: {admin_email}")

    except IntegrityError:
        db.rollback()
        print("Error: Could not create admin user due to a database integrity error.")
    except Exception as e:
        db.rollback()
        print(f"An error occurred: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
