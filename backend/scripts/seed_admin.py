import os
import sys
from pathlib import Path

# Add the parent directory to the Python path so we can import 'app'
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
        
        # Check if admin already exists
        existing_admin = db.query(User).filter(User.email == admin_email).first()
        if existing_admin:
            print(f"Admin user {admin_email} already exists.")
            return

        hashed_password = get_password_hash(admin_password)
        new_admin = User(
            username="admin",
            email=admin_email,
            hashed_password=hashed_password,
            role=UserRole.admin
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
