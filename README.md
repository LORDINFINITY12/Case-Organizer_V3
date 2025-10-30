# Case Organizer v3

Case Organizer is a full-stack legal case-management and document-organization platform built with Flask.  
It helps law practices structure, archive, and retrieve their case files, generate invoices, and manage internal communication — all within a private, self-hosted environment.

---

## Overview

Version 3 represents a complete rebuild of the original Case Organizer architecture.  
It introduces secure email-based authentication, internal messaging, integrated invoicing, and Debian-package deployment for seamless installation on Linux servers.

---

## Features

### Core System
- Built on **Flask 3.0** and **Werkzeug 3.0** with hardened routing and isolated session management.
- Fully Debian-packaged (`.deb`) for one-command deployment and auto-systemd integration.
- Secure password storage with **argon2-cffi** and **cryptography** modules.
- Configurable filesystem root (`fs-files`) for all case data.

### Authentication and Accounts
- Email-based login replacing the shared-password model.  
- Password reset via secure SMTP-delivered reset codes.  
- Users can update their username, email, and password independently.  
- Automatic logout after 10 minutes of inactivity for security.

### Administration
- Admins can:
  - Create users with temporary credentials.  
  - Promote or demote roles between *admin* and *standard*.  
  - Edit or delete user accounts.  
  - Update or relocate the root storage path live.  
  - Delete server files directly from the dashboard.

![Admin Account Demo](https://raw.githubusercontent.com/LORDINFINITY12/Case-Organizer_V3/main/static/img/Admin-Account-Demo.png)

### Case Management
- Create, edit, and organize structured case directories:

   ```none
  fs-files/
    YYYY/
      MMM/
        Petitioner v. Respondent/
          Note.json
          Petitions_Applications/
          Orders_Judgments/
          Primary_Documents/
    Case_Law/
      Category/
        Case Type/
          YYYY/
            Petitioner v. Respondent/
    Invoices/
  ```
- **Dual-tab Manage Case** interface:
  - Name lookup pre-fills year/month automatically.
  - Notes stay synchronized with the active case.
- Integrated **Case Law** module:
  - Upload, tag, and search case-law documents.
  - Tabbed search with admin-only delete access.
- Auto-naming of files:
  ```none
  (DDMMYYYY) TYPE DOMAIN Petitioner v. Respondent.ext
  ```
  Reference files keep original names with case suffix.

### Invoicing
- Full PDF invoice generator using **ReportLab**.  
- Accessible both globally and per-case.  
- Dual save: global `Invoices/` archive and per-case folder.  
- Context-aware UI disables irrelevant controls until a case is selected.

![Invoice Demo](https://raw.githubusercontent.com/LORDINFINITY12/Case-Organizer_V3/main/static/img/Invoice-Demo.png)

### Internal Messaging
- Built-in mailbox for users to send, receive, and read messages.  
- Asynchronous SMTP notifications prevent UI blocking.  
- Optional performance logging for slower servers.

![Messaging Demo](https://raw.githubusercontent.com/LORDINFINITY12/Case-Organizer_V3/main/static/img/Messagin-Demo.png)

### Search and Retrieval
- Multi-filter search:
  - Year / Month  
  - Petitioner / Respondent  
  - Domain + Subcategory  
  - Free-text queries  
- Fast indexed search across Notes, Case Law, and Invoices.

![Case Law Search Demo](https://raw.githubusercontent.com/LORDINFINITY12/Case-Organizer_V3/main/static/img/Case-Law-Search-Demo.png)

### UI and UX
- Flattened, consistent styling across all pages.

![Index Demo](https://raw.githubusercontent.com/LORDINFINITY12/Case-Organizer_V3/main/static/img/Index-Demo.png)

- Password-visibility toggle on login form.

![Login Screen Visibility Toggle](https://raw.githubusercontent.com/LORDINFINITY12/Case-Organizer_V3/main/static/img/Login-Screen-Visibility-Toggle.png)
  
- Dark/light theme compatibility.
  
![Dark/Light Mode Comparison](https://raw.githubusercontent.com/LORDINFINITY12/Case-Organizer_V3/main/static/img/Dark-Light-Comparison.png)

- Clear disabled states and keyboard-focus polish.

---

## Requirements

```text
Flask>=3.0
Werkzeug>=3.0
pdfminer.six>=20221105
python-docx>=1.1.0
argon2-cffi>=23.1.0
cryptography>=41.0.0
reportlab>=3.6.12
```

Python 3.10 or newer is required.

---

## Installation

### Option 1 – From Source

```bash
git clone https://github.com/<your-org>/case-organizer-v3.git
cd case-organizer-v3
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Access the app at:

```none
http://localhost:5000
```

---

### Option 2 – Debian Package

```bash
# Download the latest release
wget https://github.com/<your-org>/case-organizer-v3/releases/download/v3.1.2/case-organizer_3.1.2_amd64.deb

# Install the package
sudo dpkg -i case-organizer_3.1.2_amd64.deb

# Enable and start the service
sudo systemctl enable --now case-organizer.service
```

Once active, Case Organizer runs automatically on boot.  
Logs are available via:

```bash
journalctl -u case-organizer.service
```

---

## First-Run Setup

1. **Storage and Users**  
   On first launch you’ll be redirected to `/setup`.  
   Select your storage root (`fs-files`) and define allowed users.

2. **Email Configuration**  
   Provide SMTP details for outgoing mail (password resets and notifications).

3. **Login**  
   Sign in using your registered email and password.

---

## Development Notes

- Configuration stored dynamically in `caseorg_config.py`.  
- Allowed file types: `.pdf`, `.docx`, `.txt`, `.png`, `.jpg`, `.jpeg`, `.json`.  
- Diagnostic routes:  
  - `/ping` – basic health check  
  - `/__routes` – list all Flask routes

---

**License:** MIT / Open Source  
**Current Release:** v3.1.2 (October 2025)
