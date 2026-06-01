#!/opt/homebrew/bin/python3.14
"""
Process Navigation Tickets
Starting from an OCTANE defect ID, extracts the Jira ticket ID,
then checks the Jira resolution:
  - Rejected  → scans comments for "expected behavior" → sets Blocking reason
  - Duplicate → extracts master OCTANE ID from comments → sets Child (Duplicate)

Usage:
    python3 process_navi_tickets.py 2713179
    python3 process_navi_tickets.py 2713179 --jira-token YOUR_JIRA_TOKEN
    python3 process_navi_tickets.py 2713179 --octane-token YOUR_OCTANE_TOKEN

Auth:
    OCTANE: reads from ~/.netrc (machine octane-prod.bmwgroup.net) or --octane-token
    Jira:   reads from ~/.netrc (machine jira.cc.bmwgroup.net) or --jira-token
"""

import sys
import os
import re
import json
import netrc
import argparse
import requests
import urllib3
from typing import Any, Dict, List, Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Configuration ─────────────────────────────────────────────────────────────
JIRA_URL = "https://jira.cc.bmwgroup.net"

# OCTANE API
OCTANE_URL = "https://octane-prod.bmwgroup.net"
SHARED_SPACE = "1002"
WORKSPACE = "2001"
OCTANE_BASE = f"{OCTANE_URL}/api/shared_spaces/{SHARED_SPACE}/workspaces/{WORKSPACE}"

# Environment variable names for credentials
JIRA_TOKEN_ENV = "JIRA_TOKEN"
JIRA_USER_ENV  = "JIRA_USER"
OCTANE_TOKEN_ENV = "OCTANE_TOKEN"

# Resolution names treated as "Rejected"
REJECTED_RESOLUTIONS = {
    "rejected",
}

# Resolution names treated as "Duplicate"
DUPLICATE_RESOLUTIONS = {
    "duplicate",
}

# Resolution names treated as "Cannot Reproduce"
CANNOT_REPRODUCE_RESOLUTIONS = {
    "cannot reproduce",
}

# OCTANE URL pattern found in Jira remote links
# e.g. https://octane-prod.bmwgroup.net/ui/entity-navigation?p=1002/2001&entityType=work_item&id=2640673
OCTANE_URL_PATTERN = re.compile(
    r'octane[^"\'>\s]*?(?:/(?:defects?|work[_-]?items?|entity)[/=]|[?&]id=)(\d+)', re.IGNORECASE)

# Jira project prefixes we care about (for duplicate comment scanning)
DUPLICATE_JIRA_ID_PATTERN = re.compile(r'(?:IDCEVODEV|HU22DM)-\d+')

# Bare OCTANE ID: 6-7 digit number (not part of a longer number or Jira ID)
OCTANE_ID_PATTERN = re.compile(r'(?<!\d)(\d{6,7})(?!\d)')

# Keywords indicating duplicate/master reference (case-insensitive)
DUPLICATE_KEYWORDS = [
    "duplicate to",
    "duplicates to",
    "duplicated to",
    "duplicate of",
    "duplicates",
    "dup to",
    "dup of",
    "master is",
    "master ticket",
    "master:",
    "master defect",
    "master ",
]

# Keywords that indicate "rejected as expected behavior"
# Checked case-insensitively against the full comment body.
EXPECTED_BEHAVIOR_KEYWORDS = [
    "works as specified",
    "works as expected",
    "works as designed",
    "expected behavior",
    "expected behaviour",
    "this is expected",
    "as designed",
    "by design",
    "per specification",
    "per spec",
]

# Regex patterns that indicate "rejected due to missing traces".
# Uses word-boundary anchors to match variations like "missing DLT traces",
# "missing idcevo DLT traces", "please add dlt traces", etc.
MISSING_TRACES_PATTERNS = [
    re.compile(r'\bmissing\b[\w\s]{0,40}\btraces\b', re.IGNORECASE),
    re.compile(r'\bplease\s+(?:attach|add)\b[\w\s]{0,40}\btraces\b', re.IGNORECASE),
]


# ── Netrc helpers ─────────────────────────────────────────────────────────────

def _read_netrc(machine: str) -> tuple:
    """Read login/password from ~/.netrc for the given machine.
    Uses a simple manual parser to handle malformed entries that
    Python's netrc module rejects.
    Returns (login, password) or (None, None) if not found."""
    netrc_path = os.path.expanduser("~/.netrc")
    if not os.path.exists(netrc_path):
        return None, None
    try:
        with open(netrc_path, "r") as f:
            content = f.read()
    except OSError:
        return None, None

    # Split into tokens (respecting quotes would be ideal but not needed here)
    tokens = content.split()
    i = 0
    while i < len(tokens):
        if tokens[i] == "machine" and i + 1 < len(tokens):
            if tokens[i + 1] == machine:
                # Found our machine — scan for login/password
                login = None
                password = None
                j = i + 2
                while j < len(tokens) and tokens[j] != "machine":
                    if tokens[j] == "login" and j + 1 < len(tokens):
                        login = tokens[j + 1]
                        j += 2
                    elif tokens[j] == "password" and j + 1 < len(tokens):
                        password = tokens[j + 1]
                        j += 2
                    else:
                        j += 1
                return login, password
        i += 1
    return None, None


# ── Session setup ─────────────────────────────────────────────────────────────

def build_jira_session(token: Optional[str], user: Optional[str]) -> requests.Session:
    """Build a Jira API session with Bearer or Basic auth."""
    session = requests.Session()
    session.verify = False
    session.headers["Accept"] = "application/json"

    if token and user:
        session.auth = (user, token)
    elif token:
        session.headers["Authorization"] = f"Bearer {token}"

    return session


def build_octane_session(access_token: str, user_cookie: str = "") -> requests.Session:
    """Build an OCTANE API session using cookie-based auth."""
    session = requests.Session()
    session.verify = False
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "HPECLIENTTYPE": "HPE_MQM_UI",
    })
    # Sanitize token (strip non-ASCII and cookie-unsafe chars)
    _bad = set('",;\\')
    clean = "".join(c for c in access_token if 0x20 < ord(c) < 0x7F and c not in _bad)
    session.cookies.set("access_token", clean, domain="octane-prod.bmwgroup.net")
    # Note: OCTANE_USER cookie is intentionally NOT set — it restricts UDF field visibility
    return session


# ── OCTANE: extract Jira ID ───────────────────────────────────────────────────

# Known candidate field names for "Ticket no. supplier"
_SUPPLIER_TICKET_CANDIDATES = [
    "ticketno_supplier_udf",
    "ticket_no_supplier_udf",
    "ticket_no__supplier_udf",
    "ticket_number_supplier_udf",
    "supplier_ticket_udf",
    "supplier_ticket_no_udf",
]

JIRA_ID_PATTERN = re.compile(r'[A-Z][A-Z0-9]+-\d+')


def _discover_supplier_ticket_field(session: requests.Session,
                                    defect_id: str) -> Optional[str]:
    """Discover the API field name for 'Ticket no. supplier'."""
    # Strategy 1: unrestricted fetch — scan keys
    try:
        r = session.get(f"{OCTANE_BASE}/defects/{defect_id}", timeout=30)
        if r.ok:
            for key in r.json().keys():
                kl = key.lower()
                if "ticket" in kl and "supplier" in kl:
                    return key
    except Exception:
        pass

    # Strategy 2: try each candidate individually (OCTANE rejects requests
    # that include any unknown field name, so we cannot batch them)
    for cand in _SUPPLIER_TICKET_CANDIDATES:
        try:
            r = session.get(f"{OCTANE_BASE}/defects/{defect_id}",
                            params={"fields": f"id,{cand}"}, timeout=30)
            if r.ok:
                data = r.json()
                if cand in data and data[cand] is not None:
                    return cand
        except Exception:
            continue

    return None


def extract_jira_id_from_octane(session: requests.Session,
                                defect_id: str) -> Optional[str]:
    """Extract the Jira ticket ID from an OCTANE defect's 'Ticket no. supplier' field."""
    field_name = _discover_supplier_ticket_field(session, defect_id)
    if not field_name:
        return None

    try:
        r = session.get(f"{OCTANE_BASE}/defects/{defect_id}",
                        params={"fields": f"id,name,{field_name}"},
                        timeout=30)
        if not r.ok:
            return None
    except requests.RequestException:
        return None

    raw_value = r.json().get(field_name)

    # Handle different OCTANE field formats
    text = ""
    if isinstance(raw_value, str):
        text = raw_value
    elif isinstance(raw_value, dict):
        text = raw_value.get("name", "") or raw_value.get("value", "") or ""
        if not text and "data" in raw_value:
            items = raw_value["data"]
            if isinstance(items, list) and items:
                text = items[0].get("name", "") or str(items[0])
    elif isinstance(raw_value, list) and raw_value:
        first = raw_value[0]
        text = first.get("name", "") if isinstance(first, dict) else str(first)

    if not text:
        return None

    m = JIRA_ID_PATTERN.search(text)
    if m:
        return m.group(0)

    stripped = text.strip()
    if JIRA_ID_PATTERN.fullmatch(stripped):
        return stripped

    # Return raw text if short (might be a custom format)
    if len(stripped) < 50:
        return stripped

    return None


# ── OCTANE: update Blocking reason ────────────────────────────────────────────

# The "Blocking reason" field in OCTANE is a list_node reference.
# The value for "Expected behaviour" is:
BLOCKING_REASON_FIELD = "blocking_reason_udf"
BLOCKING_REASON_EXPECTED_BEHAVIOUR = {
    "type": "list_node",
    "id": "expected_behaviour_ln",
    "logical_name": "expected_behaviour_ln",
}


def set_octane_blocking_reason(session: requests.Session,
                               defect_id: str) -> bool:
    """Set the 'Blocking reason' field to 'Expected behaviour' on an OCTANE defect.
    Returns True on success, False otherwise."""
    payload = {
        BLOCKING_REASON_FIELD: BLOCKING_REASON_EXPECTED_BEHAVIOUR,
    }
    try:
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        if r.ok:
            return True
        # Some OCTANE versions require id in the payload
        payload["id"] = defect_id
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        return r.ok
    except requests.RequestException:
        return False


# ── OCTANE: update Duplicate child fields ─────────────────────────────────────

BLOCKING_REASON_CHILD_DUPLICATE = {
    "type": "list_node",
    "id": "duplicate_ln",
    "logical_name": "duplicate_ln",
}

PARENT_CHILD_CHILD = {
    "type": "list_node",
    "id": "child_ln",
    "logical_name": "child_ln",
}


def set_octane_child_duplicate(session: requests.Session,
                               defect_id: str,
                               master_octane_id: str) -> bool:
    """Set duplicate child fields on an OCTANE defect:
    - Blocking reason → 'Child (Duplicate)'
    - Parent/Child → 'Child'
    - Relation to → master OCTANE ID
    Returns True on success."""
    payload = {
        BLOCKING_REASON_FIELD: BLOCKING_REASON_CHILD_DUPLICATE,
        "parent_child_udf": PARENT_CHILD_CHILD,
        "relation_to_udf": master_octane_id,
    }
    try:
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        return r.ok
    except requests.RequestException:
        return False


# ── OCTANE: update Blocking reason → Not reproducible ─────────────────────────

BLOCKING_REASON_NOT_REPRODUCIBLE = {
    "type": "list_node",
    "id": "e925j0ov2vlw8i9e6qn4xjqkz",
    "logical_name": "e925j0ov2vlw8i9e6qn4xjqkz",
}


def set_octane_not_reproducible(session: requests.Session,
                               defect_id: str) -> bool:
    """Set the 'Blocking reason' field to 'Not reproducible' on an OCTANE defect.
    Returns True on success, False otherwise."""
    payload = {
        BLOCKING_REASON_FIELD: BLOCKING_REASON_NOT_REPRODUCIBLE,
    }
    try:
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        return r.ok
    except requests.RequestException:
        return False


# ── OCTANE: update Blocking reason → Additional Information necessary ─────────

BLOCKING_REASON_ADDITIONAL_INFO_NAME = "Additional Information necessary"
BLOCKING_REASON_ADDITIONAL_INFO = {
    "type": "list_node",
    "id": "20vl1n7zw5vd2ikdkmnyyxn9k",
    "logical_name": "20vl1n7zw5vd2ikdkmnyyxn9k",
}
_BLOCKING_REASON_ROOT = "q0pk3rm202r22hwjk06xyd27m"


def _discover_list_node(session: requests.Session, root_id: str,
                        name: str) -> Optional[Dict[str, str]]:
    """Find a list_node by display name under a given root.
    Returns a dict suitable for OCTANE field update, or None."""
    try:
        r = session.get(
            f"{OCTANE_BASE}/list_nodes",
            params={
                "fields": "id,name,logical_name",
                "query": f'"list_root EQ {{id={root_id}}}; name EQ ^{name}^"',
                "limit": 100,
            },
            timeout=30,
        )
        if not r.ok:
            return None
    except requests.RequestException:
        return None

    for node in r.json().get("data", []):
        if node.get("name", "").lower() == name.lower():
            return {
                "type": "list_node",
                "id": node["id"],
                "logical_name": node.get("logical_name", node["id"]),
            }
    return None


def set_octane_additional_info_needed(session: requests.Session,
                                      defect_id: str) -> bool:
    """Set 'Blocking reason' to 'Additional Information necessary'.
    Uses the known list_node ID, with runtime discovery as fallback.
    Returns True on success, False otherwise."""
    node = BLOCKING_REASON_ADDITIONAL_INFO

    payload = {BLOCKING_REASON_FIELD: node}
    try:
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        return r.ok
    except requests.RequestException:
        return False


# ── OCTANE: change phase ──────────────────────────────────────────────────────

TARGET_PHASE = "01-New"

# Required field for phase transition to "01-New":
# "Defect quality rating" (tqr_udf) — multi-reference list_node field.
# Value "01-well created defect" is the default for properly filed tickets.
TQR_WELL_CREATED = {
    "type": "list_node",
    "id": "ndg3jor26gmplhekep6gyjk5p",
    "logical_name": "ndg3jor26gmplhekep6gyjk5p",
}


def set_octane_phase(session: requests.Session, defect_id: str,
                    phase_name: str = TARGET_PHASE,
                    extra_fields: Optional[Dict[str, Any]] = None) -> bool:
    """Move an OCTANE defect to the given phase.
    Discovers the phase ID by listing all defect phases, then PUTs it.
    Also sets the required 'Defect quality rating' field.
    extra_fields: optional dict of additional fields to include in the PUT
    (some phase transitions require other fields like blocking_reason_udf).
    Returns True on success."""
    # Step 1: fetch all phases and find the target by name + entity
    try:
        r = session.get(
            f"{OCTANE_BASE}/phases",
            params={"fields": "id,name,entity", "limit": 200},
            timeout=30,
        )
        if not r.ok:
            return False
    except requests.RequestException:
        return False

    phases = r.json().get("data", [])
    phase_id = None
    for p in phases:
        if p.get("entity") == "defect" and p.get("name") == phase_name:
            phase_id = p["id"]
            break
    if not phase_id:
        return False

    # Step 2: PUT the phase change (include required tqr_udf field)
    payload = {
        "phase": {"type": "phase", "id": phase_id},
        "tqr_udf": {"data": [TQR_WELL_CREATED]},
    }
    if extra_fields:
        payload.update(extra_fields)
    try:
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(
            f"{OCTANE_BASE}/defects/{defect_id}",
            json=payload,
            timeout=30,
        )
        return r.ok
    except requests.RequestException:
        return False


# ── API helpers ───────────────────────────────────────────────────────────────

def get_issue(session: requests.Session, jira_url: str,
              issue_key: str) -> Optional[Dict[str, Any]]:
    """Fetch issue metadata (summary, status, resolution)."""
    url = f"{jira_url}/rest/api/2/issue/{issue_key}"
    params = {
        "fields": "summary,status,resolution,resolutiondate,assignee,priority,comment"
    }
    try:
        r = session.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"  ❌ Connection error: {e}")
        return None

    if r.status_code == 401:
        print("  ❌ Authentication failed (401). Check --token / --user or env vars.")
        return None
    if r.status_code == 403:
        print("  ❌ Forbidden (403). You may not have permission to view this issue.")
        return None
    if r.status_code == 404:
        print(f"  ❌ Issue '{issue_key}' not found (404).")
        return None
    if not r.ok:
        print(f"  ❌ API error {r.status_code}: {r.text[:300]}")
        return None

    return r.json()


def extract_octane_id_from_jira_ticket(session: requests.Session, jira_url: str,
                                       issue_key: str) -> Optional[str]:
    """Extract OCTANE ticket ID from a Jira ticket's remote links."""
    url = f"{jira_url}/rest/api/2/issue/{issue_key}/remotelink"
    try:
        r = session.get(url, timeout=30)
    except requests.RequestException:
        return None
    if not r.ok:
        return None
    links = r.json() if isinstance(r.json(), list) else []
    for rl in links:
        obj = rl.get("object", {})
        link_url = obj.get("url", "")
        m = OCTANE_URL_PATTERN.search(link_url)
        if m:
            return m.group(1)
        title = obj.get("title", "")
        m = OCTANE_URL_PATTERN.search(title)
        if m:
            return m.group(1)
    return None


def get_comments(session: requests.Session, jira_url: str,
                 issue_key: str) -> List[Dict[str, Any]]:
    """Fetch all comments for an issue, ordered newest first."""
    url = f"{jira_url}/rest/api/2/issue/{issue_key}/comment"
    all_comments: List[Dict[str, Any]] = []
    start_at = 0
    page_size = 100

    while True:
        params = {
            "orderBy": "-created",
            "startAt": start_at,
            "maxResults": page_size,
        }
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            print(f"  ⚠️  Error fetching comments: {e}")
            break

        if not r.ok:
            print(f"  ⚠️  Comments API returned {r.status_code}")
            break

        body = r.json()
        batch: List[Dict[str, Any]] = body.get("comments", [])
        all_comments.extend(batch)

        total = body.get("total", 0)
        start_at += len(batch)
        if start_at >= total or not batch:
            break

    return all_comments


# ── Text extraction ───────────────────────────────────────────────────────────

def _adf_to_text(node: Any) -> str:
    """Recursively extract plain text from Atlassian Document Format (Jira Cloud)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return " ".join(_adf_to_text(c) for c in node.get("content", []))
    if isinstance(node, list):
        return " ".join(_adf_to_text(n) for n in node)
    return ""


def extract_comment_text(comment: Dict[str, Any]) -> str:
    """Return the plain-text body of a comment regardless of format."""
    body = comment.get("body", "")
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        # Atlassian Document Format (Jira Cloud / newer Data Center)
        return _adf_to_text(body)
    return ""


# ── Core logic ────────────────────────────────────────────────────────────────

def find_expected_behavior_comment(
        comments: List[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """
    Scan comments newest-first for expected-behavior keywords.
    Returns a dict with match details, or None if no match found.
    """
    for comment in comments:   # already sorted newest-first
        text = extract_comment_text(comment)
        text_lower = text.lower()

        for keyword in EXPECTED_BEHAVIOR_KEYWORDS:
            if keyword in text_lower:
                author  = (comment.get("author") or {}).get("displayName", "Unknown")
                created = (comment.get("created") or "")[:10]
                # Highlight the matching line for context
                excerpt = _find_excerpt(text, keyword)
                return {
                    "keyword": keyword,
                    "author":  author,
                    "created": created,
                    "excerpt": excerpt,
                }
    return None


def _find_excerpt(text: str, keyword: str, radius: int = 120) -> str:
    """Return a short excerpt around the matching keyword."""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return text[:200]
    start = max(0, idx - radius)
    end   = min(len(text), idx + len(keyword) + radius)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def find_missing_traces_comment(
        comments: List[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """
    Scan comments newest-first for missing-traces regex patterns.
    Returns a dict with match details, or None if no match found.
    """
    for comment in comments:
        text = extract_comment_text(comment)

        for pattern in MISSING_TRACES_PATTERNS:
            m = pattern.search(text)
            if m:
                matched_text = m.group(0)
                author = (comment.get("author") or {}).get("displayName", "Unknown")
                created = (comment.get("created") or "")[:10]
                excerpt = _find_excerpt(text, matched_text)
                return {
                    "keyword": matched_text,
                    "author": author,
                    "created": created,
                    "excerpt": excerpt,
                }
    return None


def extract_master_duplicate_octane_id(
    jira_session, jira_url: str, octane_session, issue_key: str
) -> Optional[str]:
    """
    Extract the OCTANE ID of the master/duplicate ticket from Jira comments.

    Strategy:
    1. Scan comments for duplicate keywords
    2. From matching comment, extract Jira IDs and/or bare OCTANE IDs
    3. Priority: Jira ID → resolve via remote links (more reliable) > bare OCTANE ID
    4. If ambiguous (multiple candidates), return None and flag uncertainty

    Returns the master OCTANE ID as a string, or None if not found/ambiguous.
    """
    comments = get_comments(jira_session, jira_url, issue_key)
    if not comments:
        print("  No comments found.")
        return None

    print(f"  {len(comments)} comment(s) to scan.\n")

    for comment in comments:
        text = extract_comment_text(comment)
        text_lower = text.lower()

        matched_keyword = None
        for keyword in DUPLICATE_KEYWORDS:
            if keyword in text_lower:
                matched_keyword = keyword
                break

        if not matched_keyword:
            continue

        author = (comment.get("author") or {}).get("displayName", "Unknown")
        created = (comment.get("created") or "")[:10]

        print(f"  Found keyword: \"{matched_keyword}\"")
        print(f"  Author: {author}  |  Date: {created}")
        print(f"  Comment text (first 300 chars):")
        print(f"    {text[:300]}")
        print()

        # Extract all Jira IDs from comment (excluding the current ticket), deduplicated
        jira_ids = list(dict.fromkeys(m for m in DUPLICATE_JIRA_ID_PATTERN.findall(text) if m != issue_key))

        # Extract all bare OCTANE IDs from comment, deduplicated
        octane_ids = list(dict.fromkeys(OCTANE_ID_PATTERN.findall(text)))

        print(f"  Jira IDs found (excl. self): {jira_ids}")
        print(f"  Bare OCTANE IDs found: {octane_ids}")
        print()

        # Case: Multiple Jira IDs — ambiguous
        if len(jira_ids) > 1:
            print(f"  ⚠️  AMBIGUOUS: Multiple Jira IDs found in comment: {jira_ids}")
            print(f"     Cannot determine which is the master. Please check manually.")
            return None

        # Case: Exactly one Jira ID — resolve to OCTANE via remote links
        if len(jira_ids) == 1:
            master_jira_id = jira_ids[0]
            print(f"  → Resolving Jira ID '{master_jira_id}' to OCTANE ID via remote links …")
            master_octane_id = extract_octane_id_from_jira_ticket(
                jira_session, jira_url, master_jira_id
            )
            if master_octane_id:
                print(f"  ✓ Master OCTANE ID: {master_octane_id}  (via Jira {master_jira_id})")
                return master_octane_id
            else:
                print(f"  ⚠️  Could not resolve Jira '{master_jira_id}' to OCTANE ID via remote links.")
                if len(octane_ids) == 1:
                    print(f"  → Falling back to bare OCTANE ID from comment: {octane_ids[0]}")
                    return octane_ids[0]
                elif len(octane_ids) > 1:
                    print(f"  ⚠️  AMBIGUOUS: Multiple bare OCTANE IDs: {octane_ids}")
                    print(f"     Please check manually.")
                    return None
                else:
                    print(f"  ❌ No OCTANE ID found for master ticket.")
                    return None

        # Case: No Jira ID but bare OCTANE ID(s)
        if len(octane_ids) == 1:
            print(f"  → Using bare OCTANE ID from comment: {octane_ids[0]}")
            return octane_ids[0]
        elif len(octane_ids) > 1:
            print(f"  ⚠️  AMBIGUOUS: Multiple bare OCTANE IDs found: {octane_ids}")
            print(f"     Cannot determine which is the master. Please check manually.")
            return None

        # Case: Keyword found but no IDs extracted
        print(f"  ⚠️  Keyword matched but no ticket IDs found near it.")
        print(f"     Full comment may need manual review.")
        continue

    print("  ❌ No duplicate/master reference found in any comment.")
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check if a Jira ticket is Rejected as 'expected behavior'.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "octane_id",
        help="OCTANE defect ID (numeric), e.g. 2713179",
    )
    parser.add_argument(
        "--octane-token",
        default=os.environ.get(OCTANE_TOKEN_ENV),
        metavar="TOKEN",
        help=f"OCTANE access_token cookie (or set ${OCTANE_TOKEN_ENV}, or ~/.netrc)",
    )
    parser.add_argument(
        "--jira-token",
        default=os.environ.get(JIRA_TOKEN_ENV),
        metavar="TOKEN",
        help=f"Jira API / personal access token  (or set ${JIRA_TOKEN_ENV}, or ~/.netrc)",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get(JIRA_USER_ENV),
        metavar="EMAIL",
        help=f"Jira username or e-mail for basic auth  (or set ${JIRA_USER_ENV})",
    )
    parser.add_argument(
        "--url",
        default=JIRA_URL,
        metavar="URL",
        help=f"Jira base URL  (default: {JIRA_URL})",
    )
    args = parser.parse_args()

    jira_url = args.url.rstrip("/")

    # ── Resolve OCTANE credentials (CLI > env > netrc) ────────────────────────
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

    # ── Resolve Jira credentials (CLI > env > netrc) ──────────────────────────
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
    print(f"  Process Navigation Ticket  ·  OCTANE #{args.octane_id}")
    print(f"  {OCTANE_URL}  →  {jira_url}")
    print(f"{sep}\n")

    # ── Step 0: Extract Jira ID from OCTANE ───────────────────────────────────
    print(f"[0] Connecting to OCTANE, extracting Jira ID …")
    octane_session = build_octane_session(octane_token, octane_user_cookie)

    # Test OCTANE connection
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
    print(f"  ✓ Connected to OCTANE")

    issue_key = extract_jira_id_from_octane(octane_session, args.octane_id)
    if not issue_key:
        print(f"  ❌ Could not extract Jira ID from OCTANE #{args.octane_id}")
        print(f"     (field 'Ticket no. supplier' is empty or not found)")
        sys.exit(1)

    print(f"  Jira ID: {issue_key}")

    # ── Build Jira session ────────────────────────────────────────────────────
    session = build_jira_session(jira_token, jira_user)

    # ── Step 1: fetch issue ───────────────────────────────────────────────────
    print(f"\n[1] Fetching Jira issue {issue_key} …")
    issue = get_issue(session, jira_url, issue_key)
    if issue is None:
        sys.exit(1)

    fields     = issue.get("fields", {})
    summary    = fields.get("summary", "")
    status     = (fields.get("status")     or {}).get("name", "Unknown")
    resolution = fields.get("resolution")
    res_name   = (resolution or {}).get("name", "Unresolved") if resolution else "Unresolved"
    res_date   = (fields.get("resolutiondate") or "")[:10]
    priority   = (fields.get("priority")   or {}).get("name", "")
    assignee   = (fields.get("assignee")   or {}).get("displayName", "Unassigned")

    print(f"  Summary    : {summary}")
    print(f"  Status     : {status}")
    print(f"  Resolution : {res_name}" + (f"  ({res_date})" if res_date else ""))
    print(f"  Priority   : {priority}")
    print(f"  Assignee   : {assignee}")

    # ── Step 2: resolution routing ────────────────────────────────────────────
    res_lower = res_name.lower()
    is_rejected  = res_lower in REJECTED_RESOLUTIONS
    is_duplicate = res_lower in DUPLICATE_RESOLUTIONS
    is_cannot_reproduce = res_lower in CANNOT_REPRODUCE_RESOLUTIONS

    print(f"\n[2] Checking resolution …")

    if not is_rejected and not is_duplicate and not is_cannot_reproduce:
        print(f"  ℹ️  Resolution is '{res_name}' — not Rejected, Duplicate, or Cannot Reproduce.")
        print()
        sys.exit(0)

    # ══════════════════════════════════════════════════════════════════════════
    # PATH A: Rejected → scan for "expected behavior"
    # ══════════════════════════════════════════════════════════════════════════
    if is_rejected:
        print(f"  ⚠️  Ticket is REJECTED  ('{res_name}')")

        print(f"\n[3] Scanning comments for expected-behavior keywords (newest first) …")
        comments = get_comments(session, jira_url, issue_key)
        print(f"  {len(comments)} comment(s) found.")
        print(f"  Keywords : {EXPECTED_BEHAVIOR_KEYWORDS}\n")

        match = find_expected_behavior_comment(comments)

        if match:
            print(f"  ✅  EXPECTED BEHAVIOR — match found in comments")
            print(f"  {'─'*54}")
            print(f"  Keyword  : \"{match['keyword']}\"")
            print(f"  Author   : {match['author']}")
            print(f"  Date     : {match['created']}")
            print(f"  Excerpt  :\n")
            for line in match["excerpt"].splitlines():
                print(f"    {line}")

            print(f"\n[4] Verifying OCTANE ID via Jira remote links …")
            octane_id_from_jira = extract_octane_id_from_jira_ticket(session, jira_url, issue_key)
            if octane_id_from_jira:
                print(f"  OCTANE ID (from Jira): {octane_id_from_jira}")
                if octane_id_from_jira == args.octane_id:
                    print(f"  ✓ Matches input OCTANE ID")
                else:
                    print(f"  ⚠️  Differs from input OCTANE #{args.octane_id}")
            else:
                print(f"  (No OCTANE link in Jira remote links — using input #{args.octane_id})")

            print(f"\n[5] Setting OCTANE 'Blocking reason' → 'Expected behaviour' …")
            updated = set_octane_blocking_reason(octane_session, args.octane_id)
            if updated:
                print(f"  ✓ OCTANE #{args.octane_id} updated successfully")
            else:
                print(f"  ⚠️  Could not update OCTANE field (may already be set or lack permissions)")

            print(f"\n[6] Moving OCTANE #{args.octane_id} to phase '{TARGET_PHASE}' …")
            phase_ok = set_octane_phase(octane_session, args.octane_id)
            if phase_ok:
                print(f"  ✓ Phase changed to '{TARGET_PHASE}'")
            else:
                print(f"  ⚠️  Could not change phase (may not be an allowed transition)")

            print(f"\n  ── Result ──")
            print(f"  OCTANE ID : {args.octane_id}")
            print(f"  Jira ID   : {issue_key}")
            print(f"  Verdict   : Rejected as Expected Behaviour")
            print(f"  OCTANE updated: {'Yes' if updated else 'No'}")
            print(f"  Phase → {TARGET_PHASE}: {'Yes' if phase_ok else 'No'}")
            print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={args.octane_id}")
            print()
            sys.exit(0)
        else:
            print(f"  ❌  No expected-behavior keyword found in any comment.")
            print(f"      Continuing to check for 'missing traces' …\n")

            # ── PATH A2: Missing traces ──────────────────────────────────
            print(f"[4] Scanning comments for missing-traces patterns …")
            print(f"  Patterns: {[p.pattern for p in MISSING_TRACES_PATTERNS]}\n")

            traces_match = find_missing_traces_comment(comments)

            if traces_match:
                print(f"  ✅  MISSING TRACES — match found in comments")
                print(f"  {'─'*54}")
                print(f"  Matched  : \"{traces_match['keyword']}\"")
                print(f"  Author   : {traces_match['author']}")
                print(f"  Date     : {traces_match['created']}")
                print(f"  Excerpt  :\n")
                for line in traces_match["excerpt"].splitlines():
                    print(f"    {line}")

                print(f"\n[5] Verifying OCTANE ID via Jira remote links …")
                octane_id_from_jira = extract_octane_id_from_jira_ticket(session, jira_url, issue_key)
                if octane_id_from_jira:
                    print(f"  OCTANE ID (from Jira): {octane_id_from_jira}")
                    if octane_id_from_jira == args.octane_id:
                        print(f"  ✓ Matches input OCTANE ID")
                    else:
                        print(f"  ⚠️  Differs from input OCTANE #{args.octane_id}")
                else:
                    print(f"  (No OCTANE link in Jira remote links — using input #{args.octane_id})")

                print(f"\n[6] Setting OCTANE 'Blocking reason' → '{BLOCKING_REASON_ADDITIONAL_INFO_NAME}' …")
                updated = set_octane_additional_info_needed(octane_session, args.octane_id)
                if updated:
                    print(f"  ✓ OCTANE #{args.octane_id} updated successfully")
                else:
                    print(f"  ⚠️  Could not update OCTANE field (may already be set or lack permissions)")

                print(f"\n[7] Moving OCTANE #{args.octane_id} to phase '{TARGET_PHASE}' …")
                phase_ok = set_octane_phase(octane_session, args.octane_id)
                if phase_ok:
                    print(f"  ✓ Phase changed to '{TARGET_PHASE}'")
                else:
                    print(f"  ⚠️  Could not change phase (may not be an allowed transition)")

                print(f"\n  ── Result ──")
                print(f"  OCTANE ID : {args.octane_id}")
                print(f"  Jira ID   : {issue_key}")
                print(f"  Verdict   : Rejected — Missing Traces")
                print(f"  OCTANE updated: {'Yes' if updated else 'No'}")
                print(f"  Phase → {TARGET_PHASE}: {'Yes' if phase_ok else 'No'}")
                print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={args.octane_id}")
                print()
                sys.exit(0)
            else:
                print(f"  ❌  No missing-traces pattern found in any comment.")
                print(f"      Rejection reason is NOT documented as 'expected behavior' or 'missing traces'.")
                print()
                sys.exit(2)

    # ══════════════════════════════════════════════════════════════════════════
    # PATH B: Duplicate → find master OCTANE ID, update child
    # ══════════════════════════════════════════════════════════════════════════
    if is_duplicate:
        print(f"  ⚠️  Ticket is DUPLICATE  ('{res_name}')")

        print(f"\n[3] Scanning comments for master/duplicate reference …")
        master_octane_id = extract_master_duplicate_octane_id(
            session, jira_url, octane_session, issue_key
        )

        if not master_octane_id:
            print(f"\n  ❌ Could not determine master OCTANE ID from comments.")
            print()
            sys.exit(3)

        print(f"\n  ✅ Master OCTANE ID: {master_octane_id}")
        print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={master_octane_id}")

        print(f"\n[4] Setting OCTANE #{args.octane_id} as duplicate child …")
        print(f"  Blocking reason → 'Child (Duplicate)'")
        print(f"  Parent/Child    → 'Child'")
        print(f"  Relation to     → '{master_octane_id}'")
        updated = set_octane_child_duplicate(octane_session, args.octane_id, master_octane_id)
        if updated:
            print(f"  ✓ OCTANE #{args.octane_id} updated successfully")
        else:
            print(f"  ⚠️  Could not update OCTANE fields (may lack permissions)")

        print(f"\n[5] Moving OCTANE #{args.octane_id} to phase '{TARGET_PHASE}' …")
        phase_ok = set_octane_phase(octane_session, args.octane_id)
        if phase_ok:
            print(f"  ✓ Phase changed to '{TARGET_PHASE}'")
        else:
            print(f"  ⚠️  Could not change phase (may not be an allowed transition)")

        print(f"\n  ── Result ──")
        print(f"  OCTANE ID        : {args.octane_id}")
        print(f"  Jira ID          : {issue_key}")
        print(f"  Verdict          : Duplicate")
        print(f"  Master OCTANE ID : {master_octane_id}")
        print(f"  OCTANE updated   : {'Yes' if updated else 'No'}")
        print(f"  Phase → {TARGET_PHASE}: {'Yes' if phase_ok else 'No'}")
        print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={args.octane_id}")
        print()
        sys.exit(0)

    # ══════════════════════════════════════════════════════════════════════════
    # PATH C: Cannot Reproduce → phase change first, then blocking reason
    # ══════════════════════════════════════════════════════════════════════════
    if is_cannot_reproduce:
        print(f"  ⚠️  Ticket is CANNOT REPRODUCE  ('{res_name}')")

        print(f"\n[3] Moving OCTANE #{args.octane_id} to phase '{TARGET_PHASE}' + setting Blocking reason → 'Not reproducible' …")
        phase_ok = set_octane_phase(
            octane_session, args.octane_id,
            extra_fields={BLOCKING_REASON_FIELD: BLOCKING_REASON_NOT_REPRODUCIBLE},
        )
        if phase_ok:
            print(f"  ✓ Phase changed to '{TARGET_PHASE}'")
            print(f"  ✓ Blocking reason set to 'Not reproducible'")
        else:
            print(f"  ⚠️  Could not update OCTANE (phase change or blocking reason failed)")

        print(f"\n  ── Result ──")
        print(f"  OCTANE ID : {args.octane_id}")
        print(f"  Jira ID   : {issue_key}")
        print(f"  Verdict   : Cannot Reproduce")
        print(f"  Phase → {TARGET_PHASE}: {'Yes' if phase_ok else 'No'}")
        print(f"  Blocking reason → Not reproducible: {'Yes' if phase_ok else 'No'}")
        print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={args.octane_id}")
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
