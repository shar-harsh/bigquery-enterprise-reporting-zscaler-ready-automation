# Zscaler Setup (Recommended Default)

Most enterprise environments route traffic through **Zscaler** (often with TLS inspection). In this setup:
- Browsers work ✅ because Windows/Edge trusts the enterprise root certificate
- CLI tools may fail ❌ with `SSL: CERTIFICATE_VERIFY_FAILED` unless we configure proxy + trusted CA

This guide fixes it using a layered approach.

---

## Layer 1 — Force proxy usage

### 1A) Set environment variables (recommended)
> Use `http://` scheme even for HTTPS proxy URLs.

**PowerShell**
```powershell
setx HTTP_PROXY  "http://127.0.0.1:9000"
setx HTTPS_PROXY "http://127.0.0.1:9000"
```

Close and reopen the terminal after `setx`.

### 1B) Configure gcloud proxy properties (backup)
```powershell
gcloud config set proxy/type http
gcloud config set proxy/address 127.0.0.1
gcloud config set proxy/port 9000
```

> If your organization uses a PAC file, it often resolves to a local listener like `127.0.0.1:9000`. Confirm your actual port.

---

## Layer 2 — Fix TLS inspection (trusted CA)

### ✅ Important: the certificate may NOT be named “Zscaler”
In many companies, the intercepting certificate chain is branded as:
- **Corporate Root CA**
- **Internal Root CA**
- **Enterprise Root CA**

Even if it doesn’t say “Zscaler”, it can still be the correct certificate that signs the intercepted HTTPS traffic.

### 2A) Export the trusted corporate root certificate from Edge (Windows store)
Open this in Edge:

```
edge://certificate-manager/localcerts/platformcerts
```

Then:
1. Go to **Local certificates** → **Trusted Certificates** (Imported from Windows)
2. Locate your organization’s **Corporate Root CA** / **Internal Root CA** (names vary)
3. Click **Export** and save as `.cer`

### 2B) Tell gcloud to trust it
```powershell
gcloud config set core/custom_ca_certs_file "C:\path\to\trusted_certs.cer"
```

### 2C) (Optional) Also set SSL_CERT_FILE for Python-based tools
```powershell
setx SSL_CERT_FILE "C:\path\to\trusted_certs.cer"
```

### 2D) Last resort only — disable certificate checks
⚠️ Not recommended unless approved by your security team.
```powershell
gcloud config set core/check_certificates False
```

---

## Layer 3 — Authentication without false failures

Some environments fail `gcloud init` diagnostics behind Zscaler even when commands work.
Use:
```powershell
gcloud init --skip-diagnostics
```

Then:
```powershell
gcloud auth login
gcloud projects list
```

---

## Verify your gcloud configuration (sanitized example)
Run:
```powershell
gcloud config list
```

Expected shape:
```text
[core]
account = <YOUR_ACCOUNT_EMAIL>
project = <YOUR_PROJECT_ID>
custom_ca_certs_file = C:\path\to\trusted_certs.cer

[proxy]
address = 127.0.0.1
port = 9000
type = http

Your active configuration is: [default]
```

---

## Why this project uses CLI instead of Python SDK
In locked-down enterprise machines, Python native libraries (e.g., `cryptography`) can be blocked by AppLocker.
This repo uses `bq` and `gsutil` CLI to remain compatible.

---

## OneDrive / Sync folders note
If your output path is inside a sync folder (OneDrive), large CSVs can be locked mid-write.
Best practice:
- download to `SAFE_TEMP_DIR`
- then move to final output directory

(This is already how the automation is designed.)
