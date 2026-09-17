"""Initialize the BitBuy SQLAlchemy schema before Gunicorn starts."""
from app import app, db

with app.app_context():
    db.create_all()
    print("BitBuy database schema initialized successfully.")
