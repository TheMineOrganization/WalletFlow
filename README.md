# WalletFlow — Bitcoin wallet ledger

WalletFlow is a Flask + SQLAlchemy BTC wallet ledger with Google-only authentication, admin approvals, live support chat, and BTC/USD conversion display.

## Included

- Google OAuth/OIDC authentication using Google-verified email accounts only
- No manual/fake-email registration
- `privateid1100@gmail.com` and `cstones625@gmail.com` are the only admin emails
- BTC balances to 8 decimal places
- One platform receiving address for deposits
- User deposit requests with TXIDs
- Admin approval/rejection of deposits
- User withdrawal requests to a Bitcoin address
- Admin approval/rejection of withdrawals
- Payout TXID recorded when a withdrawal is approved
- Glowing withdrawal-approved confirmation page
- Live customer ↔ admin Socket.IO chat
- Admin customer conversation list
- BTC/USD live conversion estimate on the dashboard
- Existing wallet ledger/statements retained

## Google setup

1. Open Google Cloud Console and create/select a project.
2. Configure the OAuth consent screen.
3. Create an **OAuth client ID → Web application**.
4. Add your deployed site's callback URL:

`https://YOUR-DOMAIN/auth/google/callback`

For local development also add:

`http://127.0.0.1:5000/auth/google/callback`

5. Put the generated credentials in `.env`:

```env
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
```

Do not commit the real client secret to GitHub.

Google authentication checks the OIDC `email_verified` value, so users cannot simply type an email address to create an account.

## Admin accounts

The application recognizes exactly these two verified Google email addresses as admins:

- `privateid1100@gmail.com`
- `cstones625@gmail.com`

The admin role is based on the authenticated Google email, not a password supplied by the user.

## Run

```bash
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

## Chat

Customers open **Chat** to message support. Both admin Google accounts can open **Chat**, select a customer, and reply. Messages are saved in the SQLite database and delivered live through Socket.IO while both sides are connected.

## BTC/USD conversion

The dashboard obtains the current BTC/USD market price from CoinGecko in the browser and calculates the customer's BTC balance in USD. If the market-price request is unavailable, the conversion displays `Unavailable` rather than inventing a price.

## Important Bitcoin behavior

This version **does not automatically move or verify Bitcoin on the blockchain**. A deposit is submitted with a TXID and remains pending until an admin verifies it. For a withdrawal, the admin sends BTC from the platform's Bitcoin wallet and enters the payout TXID before approving the request in the app.

The configured deposit address is:

`bc1qg7v2xm7vrmq4a66fnvjyze2shr80u55csa0q23`

The exact configured address is in `.env` and the application UI. **Send only Bitcoin on Bitcoin mainnet to it.**

## Existing database

The application includes a small SQLite compatibility migration for the new Google-auth field. Back up `wallet_app.db` before deploying over an existing installation.

## Security before real-money production use

This remains a starting wallet/ledger application, not a production custody system. Before holding real BTC, add HTTPS, CSRF protection, strong admin authentication/2FA, audit logging, database transactions/locking, rate limiting, robust Bitcoin address validation, blockchain confirmation checks, hot/cold wallet separation, secure private-key custody, backups, monitoring, and appropriate legal/compliance controls.

## Render PostgreSQL

Set `DATABASE_URL` in the Render web service to the **Internal Database URL** from your Render PostgreSQL database. Do not leave it blank, and do not put the `SECRET_KEY` in this field.

Set `SECRET_KEY` separately (Render can generate it), plus the Google OAuth and BTC address variables.

### Render live chat
The Render start command uses Gunicorn's threaded worker with `simple-websocket`, which allows Flask-SocketIO chat messages to arrive without page refreshes on the deployed single-instance service.
