# OpenD Sidecar — Kubernetes Deployment Runbook

**Audience:** DevOps / SRE deploying the MooMoo OpenD gateway alongside the RISEDUAL AI backend.
**Scope:** Runbook only. No credentials in this file. No credentials should ever appear in a git-tracked file. Values marked `<REDACTED>` are entered directly into the cluster secret store by the operator.

---

## 1. Why a sidecar

MooMoo's OpenAPI is not pure HTTPS. The Python SDK (`moomoo-api==10.9.6908`) connects over TCP to a locally-running **OpenD** gateway process that owns the encrypted socket to MooMoo servers. RISEDUAL AI treats OpenD as an **independent service** — never a subprocess inside the FastAPI backend — so:

- OpenD failures (crash, restart) don't take down the backend
- OpenD upgrades are decoupled from application deploys
- The unlock password lives only in OpenD's own config file, never in the app container
- The backend reaches OpenD only over `localhost:11111` (via sidecar) or a cluster-internal `Service` — never exposed to the public internet

Two supported topologies:

| Topology | When to use |
|---|---|
| **A. Sidecar container** in the same Pod as the backend | Simplest — traffic never leaves the Pod, `localhost:11111` |
| **B. Dedicated Deployment + ClusterIP Service** | Multiple backend replicas share one OpenD instance (recommended once you scale > 1 replica) |

---

## 2. Vendor artifact

MooMoo does **not** publish an official Docker image. You must build your own from the Linux OpenD binary they ship.

1. Download the **Linux x86_64** OpenD tarball from https://openapi.moomoo.com/moomoo-api-doc/en/intro/download.html (login required, current version series 8.x/9.x — pin to a known-good release; test in staging before rolling forward).
2. Verify the SHA-256 against MooMoo's release page.
3. Extract into `docker/opend/` in your ops repo (NOT the app repo).

Directory layout:

```
docker/opend/
├── Dockerfile
├── OpenD.xml                  # config template only — no secrets
└── OpenD/                     # vendor binary tree from tarball
    ├── OpenD                  # executable
    ├── ...
```

---

## 3. Dockerfile

```dockerfile
# docker/opend/Dockerfile
FROM debian:12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libstdc++6 \
    libgcc-s1 \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/opend
COPY OpenD/ /opt/opend/
COPY OpenD.xml /opt/opend/OpenD.xml.tpl

# OpenD writes its own logs under /opt/opend/log; mount a PVC there if you
# want persistent logs across pod restarts.
RUN chmod +x /opt/opend/OpenD

# Bind the API port only. Ingress traffic is refused; NetworkPolicy locks
# down anything not from the backend Pod / namespace.
EXPOSE 11111

# Small entrypoint that renders OpenD.xml.tpl with values from mounted
# secret env vars, then execs OpenD. Never echoes the login password.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
```

`entrypoint.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${MOOMOO_LOGIN_ACC:?MOOMOO_LOGIN_ACC env not set}"
: "${MOOMOO_LOGIN_PWD_MD5:?MOOMOO_LOGIN_PWD_MD5 env not set}"

# Render config from template; do NOT log the substituted file.
umask 077
envsubst < /opt/opend/OpenD.xml.tpl > /opt/opend/OpenD.xml
chmod 600 /opt/opend/OpenD.xml

# Never print the password. Only log the account handle for support.
echo "[opend] starting for account=${MOOMOO_LOGIN_ACC%%@*}@***"

exec /opt/opend/OpenD -config /opt/opend/OpenD.xml
```

`OpenD.xml.tpl` (kept **without secrets** in git — only placeholders):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<config>
  <api_srv_addr>0.0.0.0</api_srv_addr>
  <api_srv_port>11111</api_srv_port>
  <login_account>${MOOMOO_LOGIN_ACC}</login_account>
  <login_pwd_md5>${MOOMOO_LOGIN_PWD_MD5}</login_pwd_md5>
  <log_level>INFO</log_level>
  <!-- Keep encryption on; the backend adapter uses the default cipher -->
  <encrypt_type>0</encrypt_type>
</config>
```

Build and push:

```bash
docker build -t your-registry/risedual/opend:9.1.4838 docker/opend/
docker push  your-registry/risedual/opend:9.1.4838
```

Tag by OpenD version — the SDK version pinned in the backend (`moomoo-api==10.9.6908`) must match against a supported OpenD major-version range from MooMoo's compatibility table.

---

## 4. Secrets

Never in git. Create in the cluster before deploying:

```bash
kubectl create secret generic moomoo-opend-secret \
  --from-literal=MOOMOO_LOGIN_ACC='<REDACTED>' \
  --from-literal=MOOMOO_LOGIN_PWD_MD5='<REDACTED>' \
  -n risedual
```

- `MOOMOO_LOGIN_ACC` — the MooMoo US account login handle (email)
- `MOOMOO_LOGIN_PWD_MD5` — the **MD5-lowercase-hex** of the LOGIN password (not the trade unlock password). Generate on the operator's machine and paste — never compute on a shared host.

Then a **separate** secret for the backend, which only needs the runtime unlock password:

```bash
kubectl create secret generic moomoo-backend-secret \
  --from-literal=MOOMOO_TRADE_UNLOCK_PASSWORD='<REDACTED>' \
  --from-literal=MOOMOO_ACC_ID='<REDACTED-numeric-account-id>' \
  -n risedual
```

- `MOOMOO_ACC_ID` — the **numeric account id** returned by `get_acc_list()` (not the login handle). The backend caches this.
- `MOOMOO_TRADE_UNLOCK_PASSWORD` — the *trade* unlock password. The backend reads it transiently at submit time and re-locks in `finally`.

Both secrets are mounted only into the containers that need them.

---

## 5. Topology A — Sidecar

Two containers, one Pod. Backend reaches OpenD on `127.0.0.1:11111`.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: risedual-backend
  namespace: risedual
spec:
  replicas: 1                       # sidecar model requires 1 replica
  selector: { matchLabels: { app: risedual-backend } }
  template:
    metadata: { labels: { app: risedual-backend } }
    spec:
      containers:
        - name: backend
          image: your-registry/risedual/backend:<tag>
          env:
            - name: MOOMOO_OPEND_HOST
              value: "127.0.0.1"
            - name: MOOMOO_OPEND_PORT
              value: "11111"
            - name: MOOMOO_LIVE_ENABLED
              value: "0"           # flip to "1" only after verification
            - name: MOOMOO_MAX_NOTIONAL_USD
              value: "50"
            - name: BROKER_DEFAULT
              value: "public"
            - name: RISEDUAL_ALPHA_L2_SOURCE
              value: "none"        # flip to "moomoo" once L2 entitlement confirmed
          envFrom:
            - secretRef: { name: moomoo-backend-secret }
          ports:
            - containerPort: 8001
          readinessProbe:
            httpGet: { path: /api/health, port: 8001 }
            initialDelaySeconds: 10
        - name: opend
          image: your-registry/risedual/opend:9.1.4838
          envFrom:
            - secretRef: { name: moomoo-opend-secret }
          ports:
            - containerPort: 11111
          readinessProbe:
            tcpSocket: { port: 11111 }
            initialDelaySeconds: 15
            periodSeconds: 5
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits:   { cpu: 500m, memory: 512Mi }
          securityContext:
            readOnlyRootFilesystem: false     # OpenD writes to its own log/ dir
            runAsNonRoot: false
            allowPrivilegeEscalation: false
```

Pros: zero network hops, no `Service` needed for OpenD.
Cons: cannot scale backend beyond 1 replica; a backend restart also cycles OpenD.

---

## 6. Topology B — Dedicated OpenD Deployment + Service

Preferred once backend replicas > 1. OpenD lives in its own Pod, backend reaches it by DNS.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: moomoo-opend
  namespace: risedual
spec:
  replicas: 1                       # OpenD is stateful; never scale > 1
  strategy: { type: Recreate }      # not RollingUpdate — avoids double-login
  selector: { matchLabels: { app: moomoo-opend } }
  template:
    metadata: { labels: { app: moomoo-opend } }
    spec:
      containers:
        - name: opend
          image: your-registry/risedual/opend:9.1.4838
          envFrom:
            - secretRef: { name: moomoo-opend-secret }
          ports:
            - containerPort: 11111
          readinessProbe:
            tcpSocket: { port: 11111 }
            initialDelaySeconds: 15
            periodSeconds: 5
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits:   { cpu: 500m, memory: 512Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: moomoo-opend
  namespace: risedual
spec:
  type: ClusterIP                   # never LoadBalancer / NodePort
  selector: { app: moomoo-opend }
  ports:
    - port: 11111
      targetPort: 11111
```

Then in the backend Deployment env:

```yaml
- name: MOOMOO_OPEND_HOST
  value: "moomoo-opend.risedual.svc.cluster.local"
- name: MOOMOO_OPEND_PORT
  value: "11111"
```

The backend's `MoomooMarketDataAdapter` and `MoomooBrokerAdapter` already default to `MOOMOO_OPEND_HOST=moomoo-opend` — matching the Service name — so this is a zero-code switch.

---

## 7. NetworkPolicy (mandatory)

OpenD must accept traffic only from the backend Pod:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: opend-ingress-only-backend
  namespace: risedual
spec:
  podSelector: { matchLabels: { app: moomoo-opend } }
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector: { matchLabels: { app: risedual-backend } }
      ports:
        - port: 11111
          protocol: TCP
```

---

## 8. Verification checklist (post-deploy, before flipping `MOOMOO_LIVE_ENABLED=1`)

Run these against the deployed backend:

```bash
BASE="https://your-backend-url"
TOKEN="<owner JWT>"

# 1. Adapters see OpenD?
curl -s "$BASE/api/admin/moomoo/status" -H "Authorization: Bearer $TOKEN" \
  | jq '.moomoo.market_data.connected, .moomoo.broker.connected'
# Expected: true, true

# 2. Entitlements resolvable?
curl -s "$BASE/api/admin/moomoo/entitlements" -H "Authorization: Bearer $TOKEN"
# Expected: {"entitlements": {"remain": <int>, "total_used": <int>, ...}}

# 3. Quote works?
curl -s "$BASE/api/admin/moomoo/quote/AAPL" -H "Authorization: Bearer $TOKEN"
# Expected: {"available": true, "quote": {"last": ..., "bid": ..., "ask": ...}}

# 4. Account fetch works? (read-only, no unlock needed)
curl -s "$BASE/api/admin/moomoo/account" -H "Authorization: Bearer $TOKEN" \
  | jq '.account | keys'
# Expected: buying power, cash, market_val, etc.
```

Only when all four pass:

```bash
kubectl set env deployment/risedual-backend \
  MOOMOO_LIVE_ENABLED=1 -n risedual
```

The safety gates (`$50 notional cap`, single position, RTH-only) remain in force via env vars.

---

## 9. Rollback

If MooMoo starts behaving oddly:

```bash
# Instant: flip the runtime kill switch (no redeploy)
kubectl set env deployment/risedual-backend \
  MOOMOO_LIVE_ENABLED=0 -n risedual

# Or via the admin API — no infra touch:
curl -s -X POST "$BASE/api/admin/alpha-daytrader/runtime" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"execute_enabled": false}'
```

Broker router falls back to `BROKER_DEFAULT=public` for any bot whose `broker` field is `moomoo`. **No auto-fallback** — existing MooMoo bots simply refuse until the flag is restored. This is intentional.

---

## 10. Notes on secrets hygiene

- `OpenD.xml` on disk is `0600` and lives only inside the OpenD container.
- The backend receives ONLY `MOOMOO_TRADE_UNLOCK_PASSWORD` and `MOOMOO_ACC_ID` — never the login password or its MD5.
- Neither password is ever written to backend logs (`_safe_lock` re-locks in `finally`, `submit_equity` returns error codes only).
- The `/api/admin/moomoo/status` endpoint is regression-tested to never leak either password.
- Rotate `MOOMOO_TRADE_UNLOCK_PASSWORD` inside MooMoo's app and update `moomoo-backend-secret` — no code change required.

---

## 11. Support contacts

- MooMoo OpenAPI docs: https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html
- Compatibility table (SDK ↔ OpenD): https://openapi.moomoo.com/moomoo-api-doc/en/intro/version.html
- Fee & entitlement reference: https://openapi.moomoo.com/moomoo-api-doc/en/intro/fee.html
