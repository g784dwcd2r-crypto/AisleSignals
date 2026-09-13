#!/usr/bin/env python3
"""Operator-only offline pilot recovery. Passwords never enter process arguments."""

import argparse
import getpass
import json
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.api.evidence_backup import create_backup, restore_backup


def main(argv=None):
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            self.print_usage(sys.stderr)
            self.exit(2, "Invalid command arguments; use --help. Passphrases may be entered only at the hidden prompt.\n")

    parser = SafeParser(description="Offline encrypted AisleSignals recovery; stop the pilot first.")
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create", help="Snapshot a stopped pilot and encrypt DB, evidence and recovery key.")
    create.add_argument("--db", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    restore = commands.add_parser("restore", help="Restore to a new directory and revoke all saved sessions.")
    restore.add_argument("--archive", required=True, type=Path)
    restore.add_argument("--target-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("Run from an interactive terminal; backup passphrases are accepted only by hidden prompt.")
    try:
        passphrase = getpass.getpass("Backup passphrase (14–256 characters): ")
        if args.action == "create":
            if passphrase != getpass.getpass("Confirm backup passphrase: "):
                raise ValueError("Backup passphrases did not match.")
            result = create_backup(args.db, args.output, passphrase)
        else:
            result = restore_backup(args.archive, args.target_dir, passphrase)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt):
        # Never print paths, archive contents, account details, keys or parser
        # errors from a damaged database. Operator can retry with a known backup.
        print("Recovery failed. Check the passphrase, stop the pilot, use a new destination, and verify the archive and available disk space. Existing data was not overwritten.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
