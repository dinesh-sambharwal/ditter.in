#!/usr/bin/env python3
"""
Script to create an admin user in the Ditter application.
Run this script when no admin user exists in the system.
"""

import sys
import os

# Add the current directory to the path so we can import the app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import User
from werkzeug.security import generate_password_hash

def create_admin():
    with app.app_context():
        # Check if any admin already exists
        existing_admin = User.query.filter_by(is_admin=True).first()
        if existing_admin:
            print(f"Admin user already exists: {existing_admin.username}")
            return

        # Get admin details from user input
        print("Creating admin user...")
        username = input("Enter admin username: ").strip()
        email = input("Enter admin email: ").strip()
        password = input("Enter admin password: ")

        # Check if user already exists
        existing_user = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()

        if existing_user:
            print(f"User with username '{username}' or email '{email}' already exists.")
            # Make existing user admin
            existing_user.is_admin = True
            db.session.commit()
            print(f"Made existing user '{existing_user.username}' an admin.")
        else:
            # Create new admin user
            admin_user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                is_admin=True
            )
            db.session.add(admin_user)
            db.session.commit()
            print(f"Created new admin user: {username}")

if __name__ == "__main__":
    create_admin()