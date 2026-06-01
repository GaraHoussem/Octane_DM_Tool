#!/usr/bin/env python3
"""
Debug script: Extract the master/duplicate OCTANE ID from a Jira ticket's comments.

Called when a Jira ticket has resolution "Duplicate".
Scans comments for duplicate/master references and resolves to an OCTANE ID.

Usage:
    python3 extract_master_duplicate_debug.py IDCEVODEV-1023041
    python3 extract_master_duplicate_debug.py HU22DM-346184
"""

import sys
import os
import argparse
from typing import Optional, List, Dict, Any

import requests

# Import shared helpers from the main pipeline script
from process_navi_tickets import (
    _read_netrc,
    build_jira_session,
    build_octane_session,
    get_comments,
    extract_comment_text,
    extract_octane_id_from_jira_ticket,
    extract_master_duplicate_octane_id,
    set_octane_child_duplicate,
    JIRA_URL,
    OCTANE_URL,
    OCTANE_BASE,
    JIRA_TOKEN_ENV,
    JIRA_USER_ENV,
    OCTANE_TOKEN_ENV,
    DUPLICATE_KEYWORDS,
    DUPLICATE_JIRA_ID_PATTERN,
    OCTANE_ID_PATTERN,
)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract master/duplicate OCTANE ID from a Jira ticket's comments.",
    )
    parser.add_argument(
        "jira_id",
        help="Jira ticket ID, e.g. IDCEVODEV-1023041 or HU22DM-346184",
    )
    parser.add_argument(
        "--jira-token", default=os.environ.get(JIRA_TOKEN_ENV), metavar="TOKEN",
    )
    parser.add_argument(
        "--octane-token", default=os.environ.get(OCTANE_TOKEN_ENV), metavar="TOKEN",
    )
    parser.add_argument(
        "--user", default=os.environ.get(JIRA_USER_ENV), metavar="EMAIL",
    )
    parser.add_argument(
        "--url", default=JIRA_URL, metavar="URL",
    )
    parser.add_argument(
        "--dump", action="store_true",
        help="Dump all raw comment texts (for debugging keyword misses)",
    )
    args = parser.parse_args()

    jira_url = args.url.rstrip("/")

    # ── Resolve Jira credentials ──────────────────────────────────────────────
    jira_token = args.jira_token
    jira_user = args.user
    if not jira_token:
        nrc_login, nrc_password = _read_netrc("jira.cc.bmwgroup.net")
        if nrc_password:
            jira_token = nrc_password
            if not jira_user:
                jira_user = nrc_login

    # ── Resolve OCTANE credentials ────────────────────────────────────────────
    octane_token = args.octane_token
    octane_user_cookie = ""
    if not octane_token:
        nrc_login, nrc_password = _read_netrc("octane-prod.bmwgroup.net")
        if nrc_password:
            octane_token = nrc_password
            octane_user_cookie = nrc_login or ""

    if not octane_token:
        print("❌ No OCTANE token. Use --octane-token, set OCTANE_TOKEN, or add to ~/.netrc.")
        sys.exit(1)

    # ── Build sessions ────────────────────────────────────────────────────────
    jira_session = build_jira_session(jira_token, jira_user)
    octane_session = build_octane_session(octane_token, octane_user_cookie)

    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  Extract Master Duplicate OCTANE ID")
    print(f"  Jira ticket: {args.jira_id}")
    print(f"{sep}\n")

    print(f"[1] Fetching comments for {args.jira_id} …")

    if args.dump:
        comments = get_comments(jira_session, jira_url, args.jira_id)
        print(f"  {len(comments)} comment(s) found.\n")
        for i, comment in enumerate(comments, 1):
            author = (comment.get("author") or {}).get("displayName", "Unknown")
            created = (comment.get("created") or "")[:10]
            text = extract_comment_text(comment)
            print(f"  ── Comment {i} ── {author} ({created}) ──")
            print(f"  {text[:500]}")
            if len(text) > 500:
                print(f"  … ({len(text)} chars total)")
            print()
        sys.exit(0)

    result = extract_master_duplicate_octane_id(
        jira_session, jira_url, octane_session, args.jira_id
    )

    print(f"\n{'─'*62}")
    if result:
        master_octane_id = result
        print(f"  ✅ Master OCTANE ID: {master_octane_id}")
        print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p=1002/2001&entityType=work_item&id={master_octane_id}")

        # ── Extract child OCTANE ID from Jira remote links ────────────────
        print(f"\n[2] Extracting child OCTANE ID for {args.jira_id} …")
        child_octane_id = extract_octane_id_from_jira_ticket(
            jira_session, jira_url, args.jira_id
        )
        if child_octane_id:
            print(f"  Child OCTANE ID: {child_octane_id}")
        else:
            print(f"  ❌ Could not extract OCTANE ID for {args.jira_id} from remote links.")
            print()
            sys.exit(1)

        # ── Update OCTANE child defect ────────────────────────────────────
        print(f"\n[3] Updating OCTANE #{child_octane_id} …")
        print(f"  Blocking reason → 'Child (Duplicate)'")
        print(f"  Parent/Child    → 'Child'")
        print(f"  Relation to     → '{master_octane_id}'")
        updated = set_octane_child_duplicate(octane_session, child_octane_id, master_octane_id)
        if updated:
            print(f"  ✓ OCTANE #{child_octane_id} updated successfully")
        else:
            print(f"  ⚠️  Could not update OCTANE fields (may lack permissions)")

        print(f"\n  ── Result ──")
        print(f"  Jira ID          : {args.jira_id}")
        print(f"  Child OCTANE ID  : {child_octane_id}")
        print(f"  Master OCTANE ID : {master_octane_id}")
        print(f"  OCTANE updated   : {'Yes' if updated else 'No'}")
    else:
        print(f"  ❌ Could not determine master OCTANE ID.")
    print()


if __name__ == "__main__":
    main()
