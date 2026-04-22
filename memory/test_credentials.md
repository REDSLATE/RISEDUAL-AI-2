# Test Credentials

## Admin / Owner (RISEDUAL — unified account)
- Email: admin@risedual.ai
- Password: RiseDual2026!
- Role: owner
- Subscription: pro
- Can activate/deactivate users and grant/revoke Pro

> *Owner and admin refer to the same unified account since
> Feb 2026 (`seed_admin()` cleanup). Both `OWNER_EMAIL` and
> `ADMIN_EMAIL` aliases in `conftest_creds.py` resolve here.*

## Auth Method
- httpOnly secure cookies (primary)
- POST /api/auth/login → sets access_token + refresh_token cookies
- CORS: credentials: 'include' required on all fetch calls

## Login Brute-Force Lockout
- 5 failed attempts per `(IP, email)` pair → locked for 15 minutes.
- Stored in `login_attempts` collection. Clear manually to unlock:
  ```
  await db.login_attempts.delete_many({})
  ```
- Successful logins during pytest ERROR noise (not failures) are
  almost always this lockout from earlier iterations.

## Password Reset (if test_credentials.md drifts from DB)
If you see `{"detail":"Invalid email or password"}` on a known-good
password, the DB hash was rotated by some other test run. Reset:
```python
import bcrypt
new_hash = bcrypt.hashpw(b'RiseDual2026!', bcrypt.gensalt(rounds=12))
await db.users.update_one(
    {'email': 'admin@risedual.ai'},
    {'$set': {'password_hash': new_hash.decode()}},
)
```
