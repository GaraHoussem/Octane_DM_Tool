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
from typing import Any, Dict, List, Optional, Tuple

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
    "won't do",
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


# ── Backend Provider Routing ──────────────────────────────────────────────────

# OCTANE field names for backend routing
FIELD_OWNER = "owner"
FIELD_ASSIGNED_ECU = "assigned_ecu_udf"
FIELD_PROBLEM_CATEGORY = "problem_category_udf"
FIELD_SOLUTION_RESPONSIBLE = "solution_responsible_udf"

# Phase for backend routing (03-In Analysis)
PHASE_IN_ANALYSIS = {
    "type": "phase",
    "id": "phase.defect.opened",
}

# Blocking Reason: Not Responsible
BLOCKING_REASON_NOT_RESPONSIBLE = {
    "type": "list_node",
    "id": "not_responsible_ln",
    "logical_name": "not_responsible_ln",
}

# Backend Providers — each with display info and OCTANE field values.
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

# Provider keywords used in flexible "assign ... <provider>" pattern
_PROVIDER_KEYWORDS = r'HERE|Zenrin|backend|BE|Perseus|LOS|FuDe|Traffic|POI|Map\s*Data'

# Generic backend keywords — indicates ticket should go to a backend provider
BACKEND_KEYWORDS_PATTERNS = [
    re.compile(r'\bmust\s+be\s+(?:checked|analyzed|investigated)\s+(?:from|in|by)\s+(?:the\s+)?(?:backend|BE|DB|HERE)\b', re.IGNORECASE),
    re.compile(r'\bplease\s+(?:check|investigate|analyze)\s+(?:in|from|at)\s+(?:the\s+)?(?:backend|BE|DB|HERE)\b', re.IGNORECASE),
    # Flexible "assign ... <provider>" — matches any words between assign and the provider keyword
    re.compile(r'\bassign\w*\s+(?:\S+\s+){0,6}(?:' + _PROVIDER_KEYWORDS + r')\b', re.IGNORECASE),
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

# Assigned ECU pattern in comments (matches both "assigned ECU:" and "assignedECU:")
ASSIGNED_ECU_PATTERN = re.compile(
    r'assigned\s*ECU\s*[:=]\s*(\S+)',
    re.IGNORECASE,
)


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


def classify_rejection(comments: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Single-pass newest-first scan of comments for ALL rejection sub-types.
    The newest comment that matches any pattern wins.

    Returns a dict with 'verdict' key:
      - "expected_behavior" + keyword, author, created, excerpt
      - "backend" + trigger, matched_text, provider_index, author, created, comment_text
      - "missing_traces" + keyword, author, created, excerpt
    Or None if no pattern matched in any comment.
    """
    for comment in comments:
        text = extract_comment_text(comment)
        text_lower = text.lower()
        author = (comment.get("author") or {}).get("displayName", "Unknown")
        created = (comment.get("created") or "")[:10]

        # ── Check expected behavior keywords ──────────────────────────────
        for keyword in EXPECTED_BEHAVIOR_KEYWORDS:
            if keyword in text_lower:
                excerpt = _find_excerpt(text, keyword)
                return {
                    "verdict": "expected_behavior",
                    "keyword": keyword,
                    "author": author,
                    "created": created,
                    "excerpt": excerpt,
                }

        # ── Check backend signals (3 layers) ─────────────────────────────
        # Layer 1: explicit defect category
        cat_match = DEFECT_CATEGORY_PATTERN.search(text)
        if cat_match:
            category = cat_match.group(1).strip()
            provider_idx = _find_provider_by_category(category)
            return {
                "verdict": "backend",
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
                "verdict": "backend",
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
                provider_idx = _infer_provider_from_context(text)
                return {
                    "verdict": "backend",
                    "trigger": "backend_keyword",
                    "matched_text": m.group(0),
                    "provider_index": provider_idx,
                    "author": author,
                    "created": created,
                    "comment_text": text,
                }

        # ── Check missing traces patterns ────────────────────────────────
        for pattern in MISSING_TRACES_PATTERNS:
            m = pattern.search(text)
            if m:
                matched_text = m.group(0)
                excerpt = _find_excerpt(text, matched_text)
                return {
                    "verdict": "missing_traces",
                    "keyword": matched_text,
                    "author": author,
                    "created": created,
                    "excerpt": excerpt,
                }

    return None


# ── Backend detection logic ───────────────────────────────────────────────────

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
    ecu_lower = ecu.strip(",.;:)]}").lower()
    candidates = []
    for i, p in enumerate(BACKEND_PROVIDERS):
        if p.get("assigned_ecu"):
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


# ── Backend display helpers ───────────────────────────────────────────────────

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


# ── Backend OCTANE update ─────────────────────────────────────────────────────

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
        BLOCKING_REASON_FIELD: BLOCKING_REASON_NOT_RESPONSIBLE,
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


def prompt_backend_provider(detection: Dict[str, Any],
                            octane_id: str, octane_session: requests.Session
                            ) -> Optional[bool]:
    """Interactive backend provider routing.
    Takes a pre-computed backend detection result, prompts user for provider
    selection, and updates OCTANE if confirmed.
    Returns True if updated, False if update failed, None if cancelled."""

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
        pass  # User pressed Enter → confirm proposed provider
    elif choice == "0":
        return None
    else:
        try:
            selected = int(choice) - 1
            if selected < 0 or selected >= len(BACKEND_PROVIDERS):
                print("  ❌ Invalid selection.")
                return None
            provider_idx = selected
        except ValueError:
            print("  ❌ Invalid input.")
            return None

    # Show final plan and confirm
    print(f"\n{'─' * 62}")
    print(f"  FINAL ACTION — Updating OCTANE #{octane_id}:")
    print(display_provider(provider_idx))
    print(f"{'─' * 62}")

    confirm = input("\n  Proceed with OCTANE update? [Y/n]: ").strip().lower()
    if confirm and confirm != "y":
        return None

    print(f"\n  Updating OCTANE #{octane_id} …")
    success = update_octane_backend(octane_session, octane_id, provider_idx)

    if success:
        print(f"  ✓ OCTANE #{octane_id} updated successfully")
        print(f"\n  ── Result ──")
        print(f"  Provider  : {BACKEND_PROVIDERS[provider_idx]['name']}")
        print(f"  OCTANE updated: Yes")
    else:
        print(f"  ❌ Failed to update OCTANE (may lack permissions or field conflict)")
        print(f"\n  ── Result ──")
        print(f"  Provider  : {BACKEND_PROVIDERS[provider_idx]['name']}")
        print(f"  OCTANE updated: No")

    return success


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
        print(f"  ℹ️  Resolution is '{res_name}' — not Rejected/Won't Do, Duplicate, or Cannot Reproduce.")
        print()
        sys.exit(0)

    # ══════════════════════════════════════════════════════════════════════════
    # PATH A: Rejected / Won't Do → single-pass newest-first classification
    # ══════════════════════════════════════════════════════════════════════════
    if is_rejected:
        print(f"  ⚠️  Ticket is REJECTED  ('{res_name}')")

        print(f"\n[3] Fetching comments …")
        comments = get_comments(session, jira_url, issue_key)
        print(f"  {len(comments)} comment(s) found.")

        print(f"\n[4] Classifying rejection (newest comment wins) …")
        classification = classify_rejection(comments)

        if classification is None:
            print(f"  ❌  No pattern matched in any comment.")
            print(f"      Not: expected behavior, backend issue, or missing traces.")
            print()
            sys.exit(2)

        verdict = classification["verdict"]
        print(f"  Verdict: {verdict}  (from {classification['author']}, {classification['created']})")

        # ── EXPECTED BEHAVIOR ─────────────────────────────────────────────
        if verdict == "expected_behavior":
            print(f"\n  ✅  EXPECTED BEHAVIOR — match found in comments")
            print(f"  {'─'*54}")
            print(f"  Keyword  : \"{classification['keyword']}\"")
            print(f"  Author   : {classification['author']}")
            print(f"  Date     : {classification['created']}")
            print(f"  Excerpt  :\n")
            for line in classification["excerpt"].splitlines():
                print(f"    {line}")

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

        # ── BACKEND ISSUE (interactive) ───────────────────────────────────
        if verdict == "backend":
            print(f"\n  ✅  BACKEND signal detected in newest matching comment")
            backend_result = prompt_backend_provider(classification, args.octane_id, octane_session)

            if backend_result is not None:
                print(f"  OCTANE ID : {args.octane_id}")
                print(f"  Jira ID   : {issue_key}")
                print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={args.octane_id}")
                print()
                sys.exit(0 if backend_result else 1)

            # User cancelled → exit (they explicitly said "not a backend issue")
            print(f"\n  ❌  Backend routing cancelled by user.")
            print()
            sys.exit(2)

        # ── MISSING TRACES ────────────────────────────────────────────────
        if verdict == "missing_traces":
            print(f"\n  ✅  MISSING TRACES — match found in comments")
            print(f"  {'─'*54}")
            print(f"  Matched  : \"{classification['keyword']}\"")
            print(f"  Author   : {classification['author']}")
            print(f"  Date     : {classification['created']}")
            print(f"  Excerpt  :\n")
            for line in classification["excerpt"].splitlines():
                print(f"    {line}")

            print(f"\n[5] Setting OCTANE 'Blocking reason' → '{BLOCKING_REASON_ADDITIONAL_INFO_NAME}' …")
            updated = set_octane_additional_info_needed(octane_session, args.octane_id)
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
            print(f"  Verdict   : Rejected — Missing Traces")
            print(f"  OCTANE updated: {'Yes' if updated else 'No'}")
            print(f"  Phase → {TARGET_PHASE}: {'Yes' if phase_ok else 'No'}")
            print(f"  URL: {OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={args.octane_id}")
            print()
            sys.exit(0)

    # ══════════════════════════════════════════════════════════════════════════
    # PATH B: Cannot Reproduce
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

    # ══════════════════════════════════════════════════════════════════════════
    # PATH C: Duplicate → find master OCTANE ID, update child
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


if __name__ == "__main__":
    main()
