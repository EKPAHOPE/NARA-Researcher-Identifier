# NARA UK Researcher Finder

A Streamlit app for finding researchers affiliated with UK higher-education institutions from **PubMed** or **OpenAlex**.

## Features

- Choose PubMed or OpenAlex.
- Free-text/Boolean search query.
- Configurable date range.
- Use the journal set from the original NARA notebook, all journals, or custom journals.
- Filter by UK country, region and HEI.
- One-row-per-researcher member table.
- Publication-level audit table.
- ORCID from source metadata when available.
- Publication counts, journal counts and OpenAlex citation totals.
- CSV downloads.
- Regional, HEI and journal coverage summaries.
- One-hour Streamlit caching for repeated identical searches.

## Restricted NARA member login

The application now requires a single NARA team account to sign in **before** displaying any researcher searches, filters, data tables or downloads. It uses the **same account and initial password as the NARA Funding Opportunity Finder**:

- **Email:** `NARA-CAMHR@hull.ac.uk`
- **Password:** Use the existing NARA funding finder password provided for this project.

The login page displays this exact notice:

> This funding finder is restricted to authorised NARA member access

The initial password is included only as a salted **PBKDF2-SHA256 hash** in `auth.py`, not as plaintext. Authentication lives in the Streamlit session; a **Log out** button clears the session. Repeated invalid attempts result in a temporary per-session lockout.

For public or shared deployment, **rotate the initial password**, serve over HTTPS, and consider university-managed authentication (SSO / reverse proxy) for higher-assurance membership control. This single-account login is not equivalent to institution-verified multi-user access.

To set a new password, run this once from the project directory after installing dependencies:

```bash
python3 - <<'PY_HASH'
from auth import make_password_hash
from getpass import getpass
print(make_password_hash(getpass("New password: ")))
PY_HASH
```

Copy the output into `.streamlit/secrets.toml` or the Streamlit deployment Secrets area:

```toml
APP_PASSWORD_HASH = "pbkdf2_sha256$...paste-generated-hash..."
```

You may also provide `APP_PASSWORD_HASH` as an environment variable. **Never publish `.streamlit/secrets.toml`.**

## Important identity and affiliation behaviour

The app does **not** use a name-only ORCID search. It reads ORCID directly from PubMed/OpenAlex metadata when it is available.

For the one-row-per-member table, the HEI/region is taken from the **latest matching publication in the current search result**. This makes the HEI and region single filterable values.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
python3 -m streamlit run streamlit_app.py
```

Edit `.streamlit/secrets.toml` with your optional database API keys and, if rotating the password, `APP_PASSWORD_HASH`.

## Secrets

```toml
NCBI_API_KEY = "..."
NCBI_EMAIL = "..."
OPENALEX_API_KEY = "..."
APP_PASSWORD_HASH = "pbkdf2_sha256$...optional-new-hash..."
```

Do not commit `.streamlit/secrets.toml` to GitHub. It is already included in `.gitignore`.

The app also supports entering API keys in the sidebar for the current session if a deployment secret has not been configured.

## Deploy on Streamlit Community Cloud

1. Push this project to a GitHub repository.
2. In Streamlit Community Cloud, create a new app from the repository.
3. Set `streamlit_app.py` as the entrypoint.
4. Add your API keys in **Advanced settings / Secrets**.
5. Deploy.

## Search notes

### PubMed
The search combines:
- the selected journal filter, if any;
- the free-text PubMed query, if any;
- the selected publication-date range.

PubMed searches are capped at the app's selected maximum and at 10,000 PMIDs per run.

### OpenAlex
The app:
- resolves selected journal names to OpenAlex source IDs;
- searches works by date and free-text query;
- restricts works to those with a GB institutional authorship;
- maps author institutions to the UK HEI/region table from the notebook.

If an OpenAlex journal cannot be resolved, the app reports it in the result screen.

## Project files

- `streamlit_app.py` — user interface and authenticated application entrypoint
- `auth.py` — login, salted password verification, logout and session controls
- `services.py` — PubMed/OpenAlex retrieval, HEI matching and author aggregation
- `config.py` — journal list, keywords and UK HEI aliases from the notebook
- `requirements.txt` — deployment dependencies
- `.streamlit/secrets.toml.example` — secrets template
