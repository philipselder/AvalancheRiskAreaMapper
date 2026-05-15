# AvalancheRiskAreaMapper
A streamlit app designed to allow mountain experts to draw avalanche release area boundaries on their regions of expertise

## Run locally

1. Create and activate a Python environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
streamlit run app.py
```

## App features

- Title: **Avalanche Risk Area Mapping Tool**
- Interactive map centered on the South Island of New Zealand
- Single **Area of Expertise** polygon
- Multiple **Potential Avalanche Release Area** polygons (inside the expertise area)
- **Clear All** to remove release area polygons
- **Send Results** dialog to email the generated ZIP attachment

## Streamlit deployment email setup

To enable the Send Results workflow on Streamlit Community Cloud, set these secrets in the app settings:

```toml
[smtp]
host = "smtp.gmail.com"
port = 587
username = "your-smtp-username"
password = "your-smtp-password-or-app-password"
from_email = "your-from-address@gmail.com"
use_starttls = true
```

Notes:

- Use an app password for Gmail rather than your account password.
- The app sends the results ZIP to philip.s.elder@gmail.com.
- Submitter name and email are read from the logged-in user's `username` and `email` fields in `passwords.json`.

### Local testing without Streamlit secrets

In `app.py`, set `USE_LOCAL_SMTP_TESTING = True` and populate `LOCAL_SMTP_CONFIG` with valid SMTP values.
When `USE_LOCAL_SMTP_TESTING` is `False`, the app uses `[smtp]` values from Streamlit secrets.
