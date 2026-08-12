# Emergent Auth (Google) — Testing Playbook

## Step 1: Create Test User & Session (Mongo)
```
mongosh --eval "
use('test_database');
var userId = 'test-user-' + Date.now();
var sessionToken = 'test_session_' + Date.now();
db.users.insertOne({
  user_id: userId,
  email: 'test.user.' + Date.now() + '@example.com',
  name: 'Test User',
  picture: 'https://via.placeholder.com/150',
  created_at: new Date()
});
db.user_sessions.insertOne({
  user_id: userId,
  session_token: sessionToken,
  expires_at: new Date(Date.now() + 7*24*60*60*1000),
  created_at: new Date()
});
print('Session token: ' + sessionToken);
"
```

## Step 2: Backend API
```
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
curl -X POST "$API_URL/api/auth/google/session" -H "X-Session-ID: <session_id>"
curl -X GET "$API_URL/api/auth/me" -H "Authorization: Bearer <session_token>"
```

## Step 3: Browser cookie testing
```javascript
await page.context.add_cookies([{
  "name": "session_token", "value": "<TOKEN>",
  "domain": "risedual-trading.preview.emergentagent.com",
  "path": "/", "httpOnly": true, "secure": true, "sameSite": "None"
}]);
```

## Checklist
- User document has user_id (UUID) + email
- Session doc user_id matches user.user_id
- All Mongo queries include `{"_id": 0}` projection
- Backend accepts both cookie session_token AND Authorization Bearer JWT
- Frontend detects `#session_id=` via `useLocation().hash` (reactive)
- AuthCallback uses `useRef` for one-shot processed flag

## Success indicators
- `POST /api/auth/google/session` returns `{token, user}` matching legacy login shape
- `GET /api/auth/me` returns user data with either Bearer or cookie token
- Landing page redirects logged-in users to dashboard
