"""
Legacy seed script — seeds a single admin user from environment variables.
For interactive admin creation with custom credentials, use:

    python scripts/create_admin.py --username admin --email admin@example.com --password secret
"""
import os
import sys
from pathlib import Path
import structlog

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import User, UserRole
from app.security import get_password_hash
from app.config import settings
from sqlalchemy.exc import IntegrityError

logger = structlog.get_logger()

def seed_admin():
    logger.info("Starting admin seeding process")
    db = SessionLocal()
    try:
        admin_email = settings.DEFAULT_ADMIN_EMAIL
        admin_password = settings.DEFAULT_ADMIN_PASSWORD

        existing_admin = db.query(User).filter(User.email == admin_email).first()
        if existing_admin:
            logger.info("Admin user already exists", email=admin_email)
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
        logger.info("Successfully created admin user", email=admin_email)
        print(f"Successfully created admin user: {admin_email}")

    except IntegrityError:
        db.rollback()
        logger.error("Database integrity error during admin seeding")
        print("Error: Could not create admin user due to a database integrity error.")
    except Exception as e:
        db.rollback()
        logger.error("Error during admin seeding", error=str(e))
        print(f"An error occurred: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
