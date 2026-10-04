# Forms Data Portal

Pulls Google Form responses (via the Forms API) from pasted edit links, cleans them,
and merges them into one master CSV. Built in Streamlit. See the PRD (v2.0) for the full spec.

## Layout

| Folder | Purpose | Milestone |
| --- | --- | --- |
| `portal/auth` | OAuth secrets and token refresh | 1 |
| `portal/connector` | Link parsing, Forms API fetch | 2 |
| `portal/mapper` | Narrow raw tables to the five fields | 3 |
| `portal/cleaner` | Cleaning rules, Valid / Fixed / Rejected | 4 |
| `portal/dedupe`, `portal/storage` | Merge, dedupe, CSV storage | 5 |
| `portal/ui` | Streamlit screen helpers | 6-8 |
| `portal/config` | Settings file and loader | 0 |
| `data/` | Local working files (git-ignored) | |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
pytest
```

## Secrets

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill it in (Milestone 1).
The real file is git-ignored. Never commit credentials or anything under `data/`.

## One-time Google setup (Milestone 1)

1. In Google Cloud, enable the **Google Forms API** and make sure your OAuth client type is **Desktop app**.
2. On your own computer run `python setup_auth.py` (or `python setup_auth.py --client-secrets client_secret.json`).
   Sign in with the Google account that owns the forms and approve read-only access.
3. Paste the three printed values into `.streamlit/secrets.toml` (local) and the Streamlit Cloud Secrets panel.
4. Check it works: `python check_auth.py "https://docs.google.com/forms/d/<ID>/edit"` should print the form title.

**Consent screen note:** while an External consent screen is in *Testing* status, Google expires the refresh token
after 7 days and only listed test users can approve. Setting the status to *In production* stops the 7-day expiry.

## Fetching forms (Milestone 2)

Try the connector without the UI:

```bash
python fetch_links.py "https://docs.google.com/forms/d/<ID>/edit" "<another link>"
python fetch_links.py --file links.txt
```

Each link gets a status: **Ready**, **Empty**, **No access**, **Invalid link**, **Public link** (a respondent link, so paste the edit link instead),
**Duplicate** (skipped) or **Error** (Google problem after retries). A raw, untouched CSV for each form is saved to `data/raw/<form_id>.csv`.

## Mapping (Milestone 3)

The mapper picks which question feeds each of the five fields. It scores every question title against the field's label and
keywords (including typos and numbered titles such as `2. Contact Number`) and checks that email and phone columns actually
contain emails and phone numbers. Each question is used once. The email used to fill the form (`respondent_email`) is preferred
for the Email field, and a typed email question fills any blanks.

Statuses: **Saved** (confirmed before, still valid), **Auto-detected** (all fields found confidently), **Needs review**
(a field is missing or uncertain), **Changed** (a saved question was renamed or removed, so re-confirm).
Confirmed mappings are remembered in `data/mappings.json`. Thresholds live in the `mapping` section of the settings file.
`fetch_links.py` prints the proposed mapping and writes a mapped preview to `data/mapped/`; add `--save-mappings` to remember them.

## Cleaning (Milestone 4)

Each field's `cleaning` list in the settings names the steps that run, in order. Remove a step from the list to switch it off.

| Status | Meaning |
| --- | --- |
| **Valid** | nothing needed changing |
| **Fixed** | one or more values were standardised |
| **Rejected** | a required value is empty or unusable (the reason is stated) |

Rules: spaces and hidden characters removed; placeholders (n/a, none, -) become empty; names lose digits and symbols and get
capitalised; phones become `+923141837972` style using `cleaning.default_country_code`; city short forms expanded
(`cleaning.city_aliases`); school names capitalised with abbreviations expanded (`cleaning.school_abbreviations`);
emails tidied, lower-cased and checked.

**Flags** keep a row but mark it for a look: very short or random-looking names, phone numbers with an unrecognised pattern or several
numbers, likely email domain typos (`cleaning.email_domain_typos`), and school names that resemble a more common spelling.
List any flag code in `cleaning.reject_flags` (e.g. `name_random`, `email_domain_typo`) to make it reject instead, or set
`cleaning.autofix_email_domain_typos` to true to correct typos automatically.

## Master CSV (Milestone 5)

`prepare_run` maps and cleans every fetched form and builds the combined master in memory; `commit_run` then saves it.
Splitting the two lets the UI show a preview and ask for confirmation before anything is written.

- **Rebuilt every run** from the links given in that run. Running the same links twice does not double the data
  (only the `Fetched At` column changes). The previous master is copied to `data/backups/` first (the newest
  `output.backups_to_keep` are kept), and writes are atomic, so a crash cannot leave a half-written file.
- **Layers** under `data/`: `raw/` (untouched), `mapped/`, `cleaned/`, `rejected/` (with reasons), `duplicates/`, and `master.csv`.
- **Master columns:** the five fields, then Source (the link's label, or the form title), Fetched At and Record Status
  (Valid or Fixed). Switch the last three off with `output.include_system_columns`. Headers use the field labels
  (`output.use_labels_as_headers`).
- **Nothing is silently dropped:** if a link failed, or a form's fields need confirming, the run is *blocked* from saving
  (so the master never quietly loses a form's rows) unless you choose to save anyway (`--allow-partial`).
- **Dedupe is off by default.** Set `dedupe.enabled` to true to remove repeat submissions across all forms. The match key is the email,
  or the WhatsApp number when the email is empty. `dedupe.keep` is `newest`, `first` or `most_complete`. Removed rows are saved to `data/duplicates/`.
- **One writer at a time:** a lock file stops two saves from overlapping.
- **Storage adapter:** the portal only talks to the `StorageAdapter` interface, so a database or Google Sheet backend can replace CSV files later.

## The Streamlit page (Milestone 6)

`streamlit run app.py` opens one page, top to bottom:

1. **Add your forms**: paste edit links (many at once is fine). Each link gets a row with an optional label, a status badge
   and a Remove button. **Check links** quickly confirms the portal can open each form (and shows its title); **Fetch and review**
   downloads the responses. A "Where do I find my form's edit link?" guide sits above the box.
2. **Review**: counts, a plain-language result for every link, and tabs for the master preview, rejected rows (with reasons),
   rows flagged for a look, and what was cleaned.
3. **Save and download**: nothing is written until you press **Confirm and save the master CSV**. If any link failed, saving is
   blocked until you tick **Save anyway**. After saving you can download the master CSV, the cleaned dataset and the rejected rows.
   (On Streamlit Community Cloud the server's disk is temporary, so download what you need.)

Saving also remembers each form's field mapping, so the same form is recognised next time. The page logic lives in
`portal/ui/view.py` (no Streamlit code, fully tested); `app.py` only draws it. `tests/test_app_smoke.py` drives the whole page
with a stand-in for Streamlit; how it *looks* still needs a real `streamlit run app.py`.

## Confirming fields (Milestone 7)

After **Fetch and review**, any form the portal has not seen before gets a short confirmation card ("2. Confirm which question is which").
Each of the five fields has a drop-down listing that form's questions with an example answer, set to the portal's guess and colour-coded:
:green[Looks right], :orange[Please check this one], :red[No matching question was found], or :blue[Your choice] if you changed it.
A field can be set to "this form has no such question" (rows missing a required field are then left out of the master, with the reason shown).
The same question cannot be picked for two fields.

- **Once per form:** pressing **Confirm these fields** saves the choice in `data/mappings.json`; next time the form goes straight to the review.
  **Confirm all that look right** confirms every form marked green in one click.
- **Changing your mind:** in the review, "Which question was used for each field?" has a **Change these** button that takes the form back
  to its confirmation card, with the drop-downs set to what you chose before.
- If a question is renamed or removed later, the form is shown as "Questions changed since last time" and must be confirmed again.
- **Phones and small screens:** the page uses stacked cards, full-width drop-downs and short columns that stack on narrow screens.

## Settings screen (Milestone 8)

The **Settings** tab edits everything that used to need a code change: the fields (name, kind, required, identifying words, cleaning steps;
add and remove fields), cleaning rules (placeholders, default country, city and school lists, email typos, which flags become rejections),
how closely questions must match, duplicate handling, the master's file name and columns, and the workflow (show the preview? ask before saving?).
Nothing changes until **Save settings**; the next review uses the new rules straight away. Adding or removing a field means fetching again.

- **Versions:** every save keeps the version it replaced (`data/settings_history/`). Pick an earlier version, see what differs, and **Restore this version**.
  Restoring is itself a new version, so it can be undone. **Reset to the built-in defaults** works the same way.
- **Backup:** download the current settings as a file, or upload one. On Streamlit Community Cloud the server's disk can reset when the app
  restarts, so download a copy of settings you want to keep (to make them permanent, copy them into `portal/config/default_settings.json`).
- **Password:** add an `[admin]` section with a `password` to the app's Secrets (see `.streamlit/secrets.toml.example`) to lock the tab.
  Without one, a warning is shown, because anyone with the link could change the settings.
- **Safe values:** typed values are checked in plain language (a two-letter country code, a `.csv` file name, sensible thresholds, one question per field...).
  A bad value is refused and nothing is saved.
- **Workflow switches:** with the preview off the detailed tabs are hidden; with confirmation off the master is saved automatically
  when every link worked (if a link failed you are still asked).
- Cleaning steps always run in a fixed safe order, whatever order they are ticked in.
