# Troubleshooting

## 1) Authentication errors
Run:
```powershell
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

## 2) SSL: CERTIFICATE_VERIFY_FAILED (Zscaler / TLS inspection)
Follow **docs/zscaler-setup.md**:
- set proxy (`HTTP_PROXY`, `HTTPS_PROXY`)
- set trusted CA (`core/custom_ca_certs_file`)

## 3) Reachability / diagnostics failures during gcloud init
Run:
```powershell
gcloud init --skip-diagnostics
```

## 4) Output file locked (WinError 32)
Close the CSV if opened in Excel/Power BI. The script retries automatically.

## 5) Jobs skipped
Keep the machine awake. Scheduler uses a tolerance window (±1 minute) + recent-run guard.
