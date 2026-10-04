"""Run the whole pipeline from the command line: fetch, map, clean, combine and save the master CSV.

    python fetch_links.py "https://docs.google.com/forms/d/<ID>/edit" "<another link>"
    python fetch_links.py --file links.txt

Options:
    --dry-run             show what would happen, save nothing
    --save-mappings       remember the proposed field mappings (only for forms that need no review)
    --include-unreviewed  include forms whose fields are uncertain instead of skipping them
    --allow-partial       save the master even though some links failed (their data will be missing)

Everything is saved under data/: raw/, mapped/, cleaned/, rejected/ (with reasons), and master.csv
(the previous master is backed up in data/backups/ first).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portal.auth import AuthSetupError, AuthTokenError, get_forms_service
from portal.config import SettingsStore
from portal.connector import LinkStatus, fetch_all
from portal.mapper import MappingStore
from portal.pipeline import CommitBlocked, commit_run, prepare_run
from portal.storage import CsvStorage, StorageBusyError

DATA = Path(__file__).parent / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch, clean and combine Google Form responses into one master CSV.")
    parser.add_argument("links", nargs="*", help="Form edit links.")
    parser.add_argument("--file", help="A text file with one link per line.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--save-mappings", action="store_true")
    parser.add_argument("--include-unreviewed", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    text = " ".join(args.links)
    if args.file:
        text += "\n" + Path(args.file).read_text(encoding="utf-8")
    if not text.strip():
        sys.exit("Give at least one link, or use --file links.txt")

    settings_store = SettingsStore(DATA)
    settings = settings_store.load()
    if settings_store.last_error:
        print("Warning:", settings_store.last_error)
    store = MappingStore(DATA / "mappings.json")
    storage = CsvStorage.from_settings(DATA, settings)

    try:
        service = get_forms_service()
    except (AuthSetupError, AuthTokenError) as exc:
        sys.exit(f"Sign-in problem: {exc}")

    def show(i, total, result):
        label = result.title or result.url
        detail = f"{result.row_count} responses" if result.status == LinkStatus.READY else result.message
        print(f"[{i}/{total}] {result.status.value:<12} {label[:50]:<50} {detail}")

    results = fetch_all(service, text, progress=show)
    if not results:
        sys.exit("No links were found in what you gave me.")

    prepared = prepare_run(results, settings, mapping_store=store, include_unreviewed=args.include_unreviewed)

    for o in prepared.outcomes:
        if o.mapping is None:
            continue
        print(f"\n  {o.label}  [{o.mapping.status.value}]")
        for f in settings["fields"]:
            m = o.mapping.matches[f["key"]]
            extra = f" + blanks from '{m.fallback}'" if m.fallback else ""
            print(f"    {f['label']:<18} <- {m.column or '(not found)'}{extra}   ({m.method}, {m.score:.2f})")
        for note in o.mapping.notes:
            print(f"    ! {note}")
        if o.cleaning:
            print()
            for line in o.cleaning.report.summary_lines(settings):
                print("    " + line)
        if args.save_mappings and o.status == "Processed" and not o.mapping.needs_attention and not args.dry_run:
            store.save(o.form_id, o.mapping)
            print("    mapping saved")

    print("\n--- Run summary ---")
    for line in prepared.summary_lines():
        print(line)

    if args.dry_run:
        sys.exit("\nDry run: nothing was saved.")
    try:
        done = commit_run(prepared, storage, settings, allow_partial=args.allow_partial)
    except (CommitBlocked, StorageBusyError) as exc:
        sys.exit(f"\nNot saved: {exc}\n(Use --allow-partial to save anyway.)" if isinstance(exc, CommitBlocked) else f"\nNot saved: {exc}")
    print(f"\nSaved {done.master.rows} rows from {done.saved_forms} forms to {done.master.path}")
    if done.master.backup:
        print(f"Previous master backed up to {done.master.backup}")


if __name__ == "__main__":
    main()
