#!/opt/homebrew/bin/python3.14
"""
Backend Provider Routing — Debug Script

Starting from an OCTANE defect ID, checks if a Rejected Jira ticket should be
reassigned to a backend provider. Scans comments for backend-related keywords
or explicit field assignments, proposes a provider, and lets the user confirm
or select a different one before updating OCTANE fields.

Usage:
    python3 process_backend_tickets.py 2714587

Auth:
    OCTANE: reads from ~/.netrc (machine octane-prod.bmwgroup.net) or --octane-token
    Jira:   reads from ~/.netrc (machine jira.cc.bmwgroup.net) or --jira-token
"""

import sys
import os
import re
import json
import argparse
import requests
import urllib3
from typing import Any, Dict, List, Optional, Tuple

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Import shared helpers from main script ────────────────────────────────────
from process_navi_tickets import (
    _read_netrc,
    build_jira_session,
    build_octane_session,
    get_issue,
    get_comments,
    extract_comment_text,
    extract_jira_id_from_octane,
    extract_octane_id_from_jira_ticket,
    JIRA_URL,
    OCTANE_URL,
    OCTANE_BASE,
    SHARED_SPACE,
    WORKSPACE,
    JIRA_TOKEN_ENV,
    JIRA_USER_ENV,
    OCTANE_TOKEN_ENV,
    REJECTED_RESOLUTIONS,
    TQR_WELL_CREATED,
)

# ── OCTANE field names ────────────────────────────────────────────────────────
FIELD_OWNER = "owner"
FIELD_ASSIGNED_ECU = "assigned_ecu_udf"
FIELD_PROBLEM_CATEGORY = "problem_category_udf"
FIELD_BLOCKING_REASON = "blocking_reason_udf"
FIELD_SOLUTION_RESPONSIBLE = "solution_responsible_udf"

# ── Phase ID ──────────────────────────────────────────────────────────────────
PHASE_IN_ANALYSIS = {
    "type": "phase",
    "id": "phase.defect.opened",
}

# ── Blocking Reason: Not Responsible ──────────────────────────────────────────
BLOCKING_REASON_NOT_RESPONSIBLE = {
    "type": "list_node",
    "id": "not_responsible_ln",
    "logical_name": "not_responsible_ln",
}

# ── Backend Providers ─────────────────────────────────────────────────────────
# Each provider is a dict with display info and OCTANE field values.
# Fields set to None will not be included in the OCTANE update.

BACKEND_PROVIDERS = [
    {
        "name": "Map Data Issues (HERE)",
        "short": "Map Data",
        "owner_name": "Tobias Naumann",
        "owner": {"type": "workspace_user", "id": "253037"},
        "assigned_ecu": {
            "type": "list_node",
            "id": "dvq836zxwjqywt70py5774emp",
            "logical_name": "dvq836zxwjqywt70py5774emp",
        },
        "problem_category": {
            "type": "list_node",
            "id": "gjq1zne1jm02lt2lqlj0ez867",
            "logical_name": "gjq1zne1jm02lt2lqlj0ez867",
        },
        "problem_category_name": "IDC_mapdata",
        "solution_responsible": None,
    },
    {
        "name": "Japan Backend Map Provider (Zenrin)",
        "short": "Zenrin",
        "owner_name": "Jinglei Huang",
        "owner": {"type": "workspace_user", "id": "272012"},
        "assigned_ecu": {
            "type": "list_node",
            "id": "69g1j2e6v8dw5azl5kex3jmk2",
            "logical_name": "69g1j2e6v8dw5azl5kex3jmk2",
        },
        "problem_category": {
            "type": "list_node",
            "id": "oq62lej4e53k9skgxe0j28mjk",
            "logical_name": "oq62lej4e53k9skgxe0j28mjk",
        },
        "problem_category_name": "Road Map Japan",
        "solution_responsible": None,
    },
    {
        "name": "Point of Interest / Search Content (HERE)",
        "short": "POI / Search Content",
        "owner_name": "Christoph Schoerner",
        "owner": {"type": "workspace_user", "id": "29242"},
        "assigned_ecu": {
            "type": "list_node",
            "id": "dvq836zxwjqywt70py5774emp",
            "logical_name": "dvq836zxwjqywt70py5774emp",
        },
        "problem_category": {
            "type": "list_node",
            "id": "qm5pl7eno07mmc2zzymx1wjge",
            "logical_name": "qm5pl7eno07mmc2zzymx1wjge",
        },
        "problem_category_name": "Online_Content_HERE",
        "solution_responsible": None,
    },
    {
        "name": "LOS Backend",
        "short": "LOS Backend",
        "owner_name": "Stephan Oertelt",
        "owner": {"type": "workspace_user", "id": "515094"},
        "assigned_ecu": {
            "type": "list_node",
            "id": "mdr6nl4vjozxpa60z3116w3e8",
            "logical_name": "mdr6nl4vjozxpa60z3116w3e8",
        },
        "problem_category": {
            "type": "list_node",
            "id": "offboard_los_ln",
            "logical_name": "offboard_los_ln",
        },
        "problem_category_name": "Offboard LOS",
        "solution_responsible": {
            "type": "list_node",
            "id": "39znonz0xdl3cgl5vyzmojvg1",
            "logical_name": "39znonz0xdl3cgl5vyzmojvg1",
        },
    },
    {
        "name": "Traffic Content (HERE)",
        "short": "Traffic Content",
        "owner_name": "Cornelia Schrei",
        "owner": {"type": "workspace_user", "id": "470011"},
        "assigned_ecu": {
            "type": "list_node",
            "id": "dvq836zxwjqywt70py5774emp",
            "logical_name": "dvq836zxwjqywt70py5774emp",
        },
        "problem_category": {
            "type": "list_node",
            "id": "4mxrjn7dnzeqgawzw012zlydq",
            "logical_name": "4mxrjn7dnzeqgawzw012zlydq",
        },
        "problem_category_name": "Traffic_Information",
        "solution_responsible": None,
    },
    {
        "name": "FuDe / Learning",
        "short": "FuDe",
        "owner_name": "Simon Springmann",
        "owner": {"type": "workspace_user", "id": "10023"},
        "assigned_ecu": None,
        "problem_category": {
            "type": "list_node",
            "id": "8z2401zl4wqqmi8pgvoy7keg6",
            "logical_name": "8z2401zl4wqqmi8pgvoy7keg6",
        },
        "problem_category_name": "FuDe_Backend",
        "solution_responsible": None,
    },
    {
        "name": "Perseus",
        "short": "Perseus",
        "owner_name": None,
        "owner": None,
        "assigned_ecu": {
            "type": "list_node",
            "id": "mdr6nl4vjozxpa60z3116w3e8",
            "logical_name": "mdr6nl4vjozxpa60z3116w3e8",
        },
        "problem_category": {
            "type": "list_node",
            "id": "d1598r4v3665pa052nqd3l60k",
            "logical_name": "d1598r4v3665pa052nqd3l60k",
        },
        "problem_category_name": "Offboard PERSEUS",
        "solution_responsible": {
            "type": "list_node",
            "id": "39znonz0xdl3cgl5vyzmojvg1",
            "logical_name": "39znonz0xdl3cgl5vyzmojvg1",
        },
    },
]

# ── Comment detection patterns ────────────────────────────────────────────────

# Generic backend keywords — indicates ticket should go to a backend provider
BACKEND_KEYWORDS_PATTERNS = [
    re.compile(r'\bmust\s+be\s+(?:checked|analyzed|investigated)\s+(?:from|in|by)\s+(?:the\s+)?(?:backend|BE|DB|HERE)\b', re.IGNORECASE),
    re.compile(r'\bplease\s+(?:check|investigate|analyze)\s+(?:in|from|at)\s+(?:the\s+)?(?:backend|BE|DB|HERE)\b', re.IGNORECASE),
    re.compile(r'\bplease\s+assign\s+to\s+(?:HERE|Zenrin|backend|BE)\b', re.IGNORECASE),
    re.compile(r'\bmust\s+be\s+assigned\s+to\s+(?:HERE|Zenrin|backend|BE)\b', re.IGNORECASE),
    re.compile(r'\b(?:DB|BE|backend|HERE)\s+issue\b', re.IGNORECASE),
    re.compile(r'\b(?:data\s*base|database)\s+issue\b', re.IGNORECASE),
    re.compile(r'\bbackend\s+(?:problem|defect|bug)\b', re.IGNORECASE),
    re.compile(r'\bnot\s+(?:a\s+)?(?:navi|navigation|HMI|client)\s+(?:issue|problem|defect)\b', re.IGNORECASE),
]

# Defect category pattern in comments (explicit field assignment)
DEFECT_CATEGORY_PATTERN = re.compile(
    r'(?:defect|problem)\s*(?:/\s*defect)?\s*category\s*[:=]\s*(\S+)',
    re.IGNORECASE,
)

# Assigned ECU pattern in comments
ASSIGNED_ECU_PATTERN = re.compile(
    r'assigned\s+ECU\s*[:=]\s*(\S+)',
    re.IGNORECASE,
)


# ── Detection logic ──────────────────────────────────────────────────────────

def detect_backend_signals(comments: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Scan comments (newest-first) for backend routing signals.
    Returns a dict with detection details, or None if nothing found.

    Detection layers:
    1. Explicit defect category in comment → directly maps to provider
    2. Assigned ECU in comment → narrows candidates
    3. Generic backend keywords → backend routing needed but provider TBD
    """
    for comment in comments:
        text = extract_comment_text(comment)
        author = (comment.get("author") or {}).get("displayName", "Unknown")
        created = (comment.get("created") or "")[:10]

        # Layer 1: explicit defect category
        cat_match = DEFECT_CATEGORY_PATTERN.search(text)
        if cat_match:
            category = cat_match.group(1).strip()
            provider_idx = _find_provider_by_category(category)
            return {
                "trigger": "defect_category",
                "matched_text": cat_match.group(0),
                "category": category,
                "provider_index": provider_idx,
                "author": author,
                "created": created,
                "comment_text": text,
            }

        # Layer 2: assigned ECU
        ecu_match = ASSIGNED_ECU_PATTERN.search(text)
        if ecu_match:
            ecu = ecu_match.group(1).strip()
            provider_idx = _find_provider_by_ecu(ecu, text)
            return {
                "trigger": "assigned_ecu",
                "matched_text": ecu_match.group(0),
                "ecu": ecu,
                "provider_index": provider_idx,
                "author": author,
                "created": created,
                "comment_text": text,
            }

        # Layer 3: generic backend keywords
        for pattern in BACKEND_KEYWORDS_PATTERNS:
            m = pattern.search(text)
            if m:
                # Try to infer provider from context
                provider_idx = _infer_provider_from_context(text)
                return {
                    "trigger": "backend_keyword",
                    "matched_text": m.group(0),
                    "provider_index": provider_idx,
                    "author": author,
                    "created": created,
                    "comment_text": text,
                }

    return None


def _find_provider_by_category(category: str) -> Optional[int]:
    """Find provider index by defect category name (case-insensitive)."""
    cat_lower = category.lower().replace(" ", "_")
    for i, p in enumerate(BACKEND_PROVIDERS):
        if p["problem_category_name"].lower().replace(" ", "_") == cat_lower:
            return i
    return None


def _find_provider_by_ecu(ecu: str, full_text: str = "") -> Optional[int]:
    """Find provider index by ECU name. Uses owner names to disambiguate."""
    ecu_lower = ecu.lower()
    candidates = []
    for i, p in enumerate(BACKEND_PROVIDERS):
        if p.get("assigned_ecu"):
            # Match ECU name — we need to compare against known names
            ecu_names = {
                "dvq836zxwjqywt70py5774emp": "here",
                "69g1j2e6v8dw5azl5kex3jmk2": "zenrin",
                "mdr6nl4vjozxpa60z3116w3e8": "backend_global",
            }
            node_id = p["assigned_ecu"]["id"]
            node_name = ecu_names.get(node_id, "")
            if node_name == ecu_lower:
                candidates.append(i)

    if len(candidates) == 1:
        return candidates[0]
    # Ambiguous — try to disambiguate via owner name in text
    if len(candidates) > 1 and full_text:
        resolved = _disambiguate_by_owner(candidates, full_text)
        if resolved is not None:
            return resolved
    return None


def _disambiguate_by_owner(candidates: List[int], text: str) -> Optional[int]:
    """Given multiple candidate provider indices, check if an owner name appears in text."""
    text_lower = text.lower()
    for i in candidates:
        owner_name = BACKEND_PROVIDERS[i].get("owner_name")
        if owner_name and owner_name.lower() in text_lower:
            return i
    return None


def _infer_provider_from_context(text: str) -> Optional[int]:
    """Try to infer provider from comment text using category/ECU/owner mentions."""
    text_lower = text.lower()

    # Check for category names in text
    for i, p in enumerate(BACKEND_PROVIDERS):
        cat_name = p["problem_category_name"].lower()
        if cat_name in text_lower:
            return i

    # Check for provider-specific keywords
    if "zenrin" in text_lower or "japan" in text_lower:
        return 1  # Zenrin
    if "traffic" in text_lower:
        return 4  # Traffic Content
    if "poi" in text_lower or "search content" in text_lower or "online_content" in text_lower:
        return 2  # POI/Search Content
    if "los" in text_lower:
        return 3  # LOS Backend
    if "perseus" in text_lower:
        return 6  # Perseus
    if "fude" in text_lower or "learning" in text_lower:
        return 5  # FuDe

    # Check for "HERE" with owner name to disambiguate
    if "here" in text_lower:
        here_providers = [i for i, p in enumerate(BACKEND_PROVIDERS)
                         if p.get("assigned_ecu") and p["assigned_ecu"]["id"] == "dvq836zxwjqywt70py5774emp"]
        resolved = _disambiguate_by_owner(here_providers, text)
        if resolved is not None:
            return resolved

    # Last resort: check if any owner name is mentioned
    for i, p in enumerate(BACKEND_PROVIDERS):
        owner_name = p.get("owner_name")
        if owner_name and owner_name.lower() in text_lower:
            return i

    return None


# ── Display helpers ───────────────────────────────────────────────────────────

def display_provider(index: int) -> str:
    """Format a provider's planned OCTANE changes for display."""
    p = BACKEND_PROVIDERS[index]
    lines = [f"  {p['name']}"]
    if p.get("owner_name"):
        lines.append(f"    Owner              : {p['owner_name']}")
    if p.get("assigned_ecu"):
        ecu_names = {
            "dvq836zxwjqywt70py5774emp": "HERE",
            "69g1j2e6v8dw5azl5kex3jmk2": "Zenrin",
            "mdr6nl4vjozxpa60z3116w3e8": "BACKEND_GLOBAL",
        }
        ecu_name = ecu_names.get(p["assigned_ecu"]["id"], "?")
        lines.append(f"    Assigned ECU       : {ecu_name}")
    lines.append(f"    Defect Category    : {p['problem_category_name']}")
    lines.append(f"    Phase              : 03-In Analysis")
    lines.append(f"    Blocking Reason    : Not Responsible")
    if p.get("solution_responsible"):
        lines.append(f"    Solution Responsible: bmw_ATC-Jira")
    return "\n".join(lines)


def display_all_providers() -> str:
    """List all providers with numbers for selection."""
    lines = []
    for i, p in enumerate(BACKEND_PROVIDERS):
        lines.append(f"  {i + 1}) {p['name']}")
    return "\n".join(lines)


# ── OCTANE update ─────────────────────────────────────────────────────────────

def update_octane_backend(session: requests.Session, defect_id: str,
                          provider_index: int) -> bool:
    """Update OCTANE defect with all backend provider fields.
    Returns True on success."""
    p = BACKEND_PROVIDERS[provider_index]

    # Fetch current software_version_udf (required for phase transition to 03-In Analysis)
    try:
        r = session.get(
            f"{OCTANE_BASE}/defects/{defect_id}",
            params={"fields": "id,software_version_udf"},
            timeout=30,
        )
        if r.ok:
            current = r.json()
            sw_version = current.get("software_version_udf")
        else:
            sw_version = None
    except requests.RequestException:
        sw_version = None

    payload: Dict[str, Any] = {
        "phase": PHASE_IN_ANALYSIS,
        FIELD_BLOCKING_REASON: BLOCKING_REASON_NOT_RESPONSIBLE,
        FIELD_PROBLEM_CATEGORY: p["problem_category"],
        "tqr_udf": {"data": [TQR_WELL_CREATED]},
    }

    if sw_version:
        payload["software_version_udf"] = sw_version

    if p.get("owner"):
        payload[FIELD_OWNER] = p["owner"]
    if p.get("assigned_ecu"):
        payload[FIELD_ASSIGNED_ECU] = p["assigned_ecu"]
    if p.get("solution_responsible"):
        payload[FIELD_SOLUTION_RESPONSIBLE] = p["solution_responsible"]

    try:
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        if r.ok:
            return True
        print(f"  API error ({r.status_code}): {r.text[:500]}")
        # Retry with id in payload
        payload["id"] = defect_id
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        if r.ok:
            return True
        print(f"  Retry error ({r.status_code}): {r.text[:500]}")
        return False
    except requests.RequestException as e:
        print(f"  Exception: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Route rejected tickets to backend providers in OCTANE.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "octane_id",
        help="OCTANE defect ID (numeric), e.g. 2714587",
    )
    parser.add_argument(
        "--octane-token",
        default=os.environ.get(OCTANE_TOKEN_ENV),
        metavar="TOKEN",
        help="OCTANE access_token cookie",
    )
    parser.add_argument(
        "--jira-token",
        default=os.environ.get(JIRA_TOKEN_ENV),
        metavar="TOKEN",
        help="Jira API token",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get(JIRA_USER_ENV),
        metavar="EMAIL",
        help="Jira username",
    )
    parser.add_argument(
        "--url",
        default=JIRA_URL,
        metavar="URL",
        help=f"Jira base URL (default: {JIRA_URL})",
    )
    args = parser.parse_args()

    jira_url = args.url.rstrip("/")

    # ── Resolve credentials ───────────────────────────────────────────────────
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

    jira_token = args.jira_token
    jira_user = args.user
    if not jira_token:
        nrc_login, nrc_password = _read_netrc("jira.cc.bmwgroup.net")
        if nrc_password:
            jira_token = nrc_password
            if not jira_user:
                jira_user = nrc_login

    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  Backend Provider Routing  ·  OCTANE #{args.octane_id}")
    print(f"  {OCTANE_URL}  →  {jira_url}")
    print(f"{sep}\n")

    # ── Step 0: Connect to OCTANE, extract Jira ID ────────────────────────────
    print("[0] Connecting to OCTANE, extracting Jira ID …")
    octane_session = build_octane_session(octane_token, octane_user_cookie)

    try:
        r = octane_session.get(f"{OCTANE_BASE}/defects",
                               params={"fields": "id", "limit": 1}, timeout=30)
        if r.status_code == 401:
            print("  ❌ OCTANE token expired or invalid (401).")
            sys.exit(1)
        if not r.ok:
            print(f"  ❌ OCTANE API error {r.status_code}: {r.text[:200]}")
            sys.exit(1)
    except requests.RequestException as e:
        print(f"  ❌ OCTANE connection error: {e}")
        sys.exit(1)
    print("  ✓ Connected to OCTANE")

    issue_key = extract_jira_id_from_octane(octane_session, args.octane_id)
    if not issue_key:
        print(f"  ❌ Could not extract Jira ID from OCTANE #{args.octane_id}")
        sys.exit(1)
    print(f"  Jira ID: {issue_key}")

    # ── Step 1: Fetch Jira issue ──────────────────────────────────────────────
    jira_session = build_jira_session(jira_token, jira_user)
    print(f"\n[1] Fetching Jira issue {issue_key} …")
    issue = get_issue(jira_session, jira_url, issue_key)
    if issue is None:
        sys.exit(1)

    fields = issue.get("fields", {})
    summary = fields.get("summary", "")
    status = (fields.get("status") or {}).get("name", "Unknown")
    resolution = fields.get("resolution")
    res_name = (resolution or {}).get("name", "Unresolved") if resolution else "Unresolved"
    res_date = (fields.get("resolutiondate") or "")[:10]

    print(f"  Summary    : {summary}")
    print(f"  Status     : {status}")
    print(f"  Resolution : {res_name}" + (f"  ({res_date})" if res_date else ""))

    # ── Step 2: Verify rejection ──────────────────────────────────────────────
    print(f"\n[2] Checking resolution …")
    if res_name.lower() not in REJECTED_RESOLUTIONS:
        print(f"  ℹ️  Resolution is '{res_name}' — not Rejected.")
        print(f"      This script only handles Rejected tickets routed to backend.")
        sys.exit(0)
    print(f"  ⚠️  Ticket is REJECTED  ('{res_name}')")

    # ── Step 3: Scan comments for backend signals ─────────────────────────────
    print(f"\n[3] Scanning comments for backend routing signals …")
    comments = get_comments(jira_session, jira_url, issue_key)
    print(f"  {len(comments)} comment(s) found.")

    detection = detect_backend_signals(comments)

    if not detection:
        print(f"\n  ❌ No backend routing signals found in comments.")
        print(f"     This ticket may not be a backend issue.")
        print(f"\n  Would you like to manually assign to a backend provider?")
        print(display_all_providers())
        print(f"  0) Cancel — this is NOT a backend issue\n")

        choice = input("  Select provider [0-7]: ").strip()
        if choice == "0" or not choice:
            print("\n  Cancelled. No changes made.")
            sys.exit(0)
        try:
            provider_idx = int(choice) - 1
            if provider_idx < 0 or provider_idx >= len(BACKEND_PROVIDERS):
                print("  ❌ Invalid selection.")
                sys.exit(1)
        except ValueError:
            print("  ❌ Invalid input.")
            sys.exit(1)
    else:
        # Show the triggering comment
        print(f"\n  ✅ Backend signal detected!")
        print(f"  {'─' * 54}")
        print(f"  Trigger  : {detection['trigger']}")
        print(f"  Matched  : \"{detection['matched_text']}\"")
        print(f"  Author   : {detection['author']}")
        print(f"  Date     : {detection['created']}")
        print(f"  Comment  :")
        for line in detection["comment_text"].splitlines():
            print(f"    {line}")

        # Show proposed provider
        provider_idx = detection.get("provider_index")
        print(f"\n[4] Proposed action:")
        if provider_idx is not None:
            print(f"\n  Must be assigned to:")
            print(display_provider(provider_idx))
        else:
            print(f"\n  ⚠️  Could not auto-detect provider. Please select manually:")

        # Ask for confirmation
        print(f"\n  Available providers:")
        print(display_all_providers())
        print(f"  0) Cancel — this is NOT a backend issue\n")

        if provider_idx is not None:
            default = str(provider_idx + 1)
            prompt = f"  Confirm [{default}] or select different [0-7]: "
        else:
            prompt = f"  Select provider [0-7]: "

        choice = input(prompt).strip()

        if not choice and provider_idx is not None:
            # User pressed Enter → confirm proposed provider
            pass
        elif choice == "0":
            print("\n  Cancelled. No changes made.")
            sys.exit(0)
        else:
            try:
                selected = int(choice) - 1
                if selected < 0 or selected >= len(BACKEND_PROVIDERS):
                    print("  ❌ Invalid selection.")
                    sys.exit(1)
                provider_idx = selected
            except ValueError:
                print("  ❌ Invalid input.")
                sys.exit(1)

    # ── Step 5: Show final plan and confirm ───────────────────────────────────
    p = BACKEND_PROVIDERS[provider_idx]
    print(f"\n{'─' * 62}")
    print(f"  FINAL ACTION — Updating OCTANE #{args.octane_id}:")
    print(display_provider(provider_idx))
    print(f"{'─' * 62}")

    confirm = input("\n  Proceed with OCTANE update? [Y/n]: ").strip().lower()
    if confirm and confirm != "y":
        print("\n  Cancelled. No changes made.")
        sys.exit(0)

    # ── Step 6: Update OCTANE ─────────────────────────────────────────────────
    print(f"\n[5] Updating OCTANE #{args.octane_id} …")
    success = update_octane_backend(octane_session, args.octane_id, provider_idx)

    if success:
        print(f"  ✓ OCTANE #{args.octane_id} updated successfully")
    else:
        print(f"  ❌ Failed to update OCTANE (may lack permissions or field conflict)")

    # ── Result ────────────────────────────────────────────────────────────────
    print(f"\n  ── Result ──")
    print(f"  OCTANE ID : {args.octane_id}")
    print(f"  Jira ID   : {issue_key}")
    print(f"  Provider  : {p['name']}")
    print(f"  OCTANE updated: {'Yes' if success else 'No'}")
    print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={args.octane_id}")
    print()


if __name__ == "__main__":
    main()
