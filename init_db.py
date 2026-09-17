"""Initialize the WalletFlow SQLAlchemy schema before Gunicorn starts."""
from app import app, db

with app.app_context():
    db.create_all()
    print("WalletFlow database schema initialized successfully.")
