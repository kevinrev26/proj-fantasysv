#!/usr/bin/env python3
"""
Management command to create an admin user.

Usage:
    python -m scripts.create_admin --username admin --email admin@example.com --password secret123
    python -m scripts.create_admin --username admin --email admin@example.com  # prompts for password

Can also be run directly:
    python backend/scripts/create_admin.py --username admin --email admin@example.com --password secret
"""
import argparse
import getpass
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import User, UserRole
from app.security import get_password_hash
from sqlalchemy.exc import IntegrityError


def create_admin(username: str, email: str, password: str) -> None:
    db = SessionLocal()
    try:
        # Validate inputs
        if not username or len(username.strip()) < 2:
            print("Error: Username must be at least 2 characters.", file=sys.stderr)
            sys.exit(1)

        if "@" not in email:
            print("Error: Invalid email address.", file=sys.stderr)
            sys.exit(1)

        if len(password) < 8:
            print("Error: Password must be at least 8 characters.", file=sys.stderr)
            sys.exit(1)

        username = username.strip()
        email = email.strip().lower()

        # Check for existing user
        existing = db.query(User).filter(
            (User.email == email) | (User.username == username)
        ).first()

        if existing:
            if existing.email == email:
                print(f"Error: A user with email '{email}' already exists.", file=sys.stderr)
            else:
                print(f"Error: A user with username '{username}' already exists.", file=sys.stderr)
            sys.exit(1)

        hashed_password = get_password_hash(password)
        admin = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            role=UserRole.admin,
            is_active=True,           # admin users are immediately active
            onboarding_complete=True,  # admin users skip onboarding
            activation_token=None,
        )

        db.add(admin)
        db.commit()
        db.refresh(admin)

        print(f"✅  Admin user created successfully!")
        print(f"    Username : {admin.username}")
        print(f"    Email    : {admin.email}")
        print(f"    Role     : {admin.role.value}")
        print(f"    ID       : {admin.id}")

    except IntegrityError as e:
        db.rollback()
        print(f"Error: Database integrity error — {e.orig}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        db.rollback()
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Create an admin user for Fantasy SV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/create_admin.py --username admin --email admin@example.com --password Secret123
  python scripts/create_admin.py --username admin --email admin@example.com   # interactive password prompt
        """,
    )
    parser.add_argument(
        "--username",
        required=True,
        metavar="USERNAME",
        help="Admin username (min 2 characters)",
    )
    parser.add_argument(
        "--email",
        required=True,
        metavar="EMAIL",
        help="Admin email address",
    )
    parser.add_argument(
        "--password",
        metavar="PASSWORD",
        default=None,
        help="Admin password (min 8 characters). If omitted, you will be prompted.",
    )

    args = parser.parse_args()

    password = args.password
    if not password:
        # Interactive secure prompt — password is not echoed
        password = getpass.getpass(f"Password for '{args.username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: Passwords do not match.", file=sys.stderr)
            sys.exit(1)

    create_admin(username=args.username, email=args.email, password=password)


if __name__ == "__main__":
    main()
