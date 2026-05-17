# 📊 BigQuery Reporting Automation (Enterprise‑Safe)

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Enterprise-Safe](https://img.shields.io/badge/Enterprise-Safe-brightgreen)](#-why-this-is-enterprise-safe)
[![Zscaler-Ready](https://img.shields.io/badge/Zscaler-Ready-blueviolet)](#%EF%B8%8F-zscaler--corporate-proxy-setup)
[![gcloud Portable ZIP](https://img.shields.io/badge/gcloud-Portable%20ZIP-orange)](#-portable-cloud-sdk--enterprise-guide)
[![Dependencies: stdlib only](https://img.shields.io/badge/Dependencies-Stdlib%20Only-9cf)](#-why-this-is-enterprise-safe)

> ✅ **Enterprise-safe BigQuery reporting automation**  
> Runs scheduled SQL reports using **bq/gsutil CLI** (not Python SDK), works behind **Zscaler / corporate proxy / TLS inspection**, supports **portable gcloud ZIP**, and requires **no pandas / no DLLs**.

## ✅ Why this is Enterprise‑Safe

This project is designed for **locked‑down corporate laptops** where typical tooling fails.

### What makes it enterprise‑safe?
- ✅ **Stdlib‑only**: No `pandas`, no extra Python packages, no native DLL installs.
- ✅ **CLI‑first**: Uses **`bq` + `gsutil`** instead of Python BigQuery SDK (avoids cryptography/DLL/AppLocker issues).
- ✅ **Zscaler‑ready**: Works in environments with **proxy + TLS inspection** using proxy config + trusted corporate CA.
- ✅ **Portable Google Cloud CLI**: If you **can’t install gcloud**, use the **official ZIP/versioned archive** and point the script to `bq.cmd`.
- ✅ **Enterprise certificate handling**: Supports `custom_ca_certs_file` for trusted corporate CA exported from Edge/Windows store.

👉 Quick start for enterprise users: **docs/zscaler-setup.md**

## ⚡ Enterprise Quickstart (Zscaler / Proxy / TLS Inspection)

If `gcloud` fails with `SSL: CERTIFICATE_VERIFY_FAILED`, do this:

1) Set proxy (example: localhost listener used by many Zscaler setups)
```bat
setx HTTP_PROXY  "http://127.0.0.1:9000"
setx HTTPS_PROXY "http://127.0.0.1:9000"
```

2) Export your **Corporate Root CA / Internal Root CA** from Edge:
```
edge://certificate-manager/localcerts/platformcerts
```

3) Trust it in gcloud:
```bat
gcloud config set core/custom_ca_certs_file "C:\path\to\trusted_certs.cer"
```

4) Avoid diagnostics false failures:
```bat
gcloud init --skip-diagnostics
```

➡ Full guide: **docs/zscaler-setup.md**

## ✨ Features
- ⏰ Schedule jobs via `jobs.json`
- 🧵 Parallel execution + retry handling
- ☁️ BigQuery extract to **GCS wildcard shards**
- 🧩 Download + merge shards into a single CSV
- 🧹 Idle-time cleanup of local temp files, BigQuery temp tables, and GCS temp files
- 📄 Daily output folders + summary CSV logs
- 🖥️ Live dashboard (console UI)

## 📦 Project Structure
```
bigquery-reporting-automation/
├── bq_automation.py
├── jobs.json
├── queries/
├── logs/                # auto-generated
├── docs/
│   ├── zscaler-setup.md
│   ├── enterprise-setup.md
│   ├── faq.md
│   └── troubleshooting.md
├── requirements.txt
├── .gitignore
├── LICENSE
└── CONTRIBUTING.md
```

## 🚀 Quick Start

1) Put your SQL files in `queries/`

2) Configure `jobs.json` (template included)

3) Update placeholders in `bq_automation.py`
- `PROJECT`, `DATASET`, `LOCATION`, `BUCKET`
- `SAFE_TEMP_DIR`, `FINAL_OUTPUT_BASE`
- `BQ` path (portable ZIP Cloud SDK is supported)

4) Authenticate
```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

5) Run
```bash
python bq_automation.py
```

## 🛡️ Zscaler / Corporate Proxy Setup
Start here: **docs/zscaler-setup.md**

## 🏢 Portable Cloud SDK + Enterprise Guide
See: **docs/enterprise-setup.md**

## ❓ FAQ
See: **docs/faq.md**

## 🧰 Troubleshooting
See: **docs/troubleshooting.md**

## 🤝 Contributing
PRs welcome — see **CONTRIBUTING.md**.

## 📜 License
MIT — see **LICENSE**.

## 🔎 Keywords
enterprise bigquery reporting automation zscaler proxy tls inspection custom_ca_certs_file portable gcloud zip bq gsutil
