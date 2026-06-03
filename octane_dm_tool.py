#!/usr/bin/env python3
"""
OCTANE DM Tool — Unified CLI + Web GUI
Processes navigation defect tickets from OCTANE via Jira analysis.

Usage:
    python3 octane_dm_tool.py                    # Launch web GUI on port 5050
    python3 octane_dm_tool.py 2713179            # CLI mode for single ticket
    python3 octane_dm_tool.py 2713179 --octane-token TOKEN

Auth:
    OCTANE: reads from ~/.netrc (machine octane-prod.bmwgroup.net) or --octane-token
    Jira:   reads from ~/.netrc (machine jira.cc.bmwgroup.net) or --jira-token
"""

import sys
import os
import re
import json
import argparse
import webbrowser
from datetime import datetime, timezone
from threading import Timer
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

JIRA_URL = "https://jira.cc.bmwgroup.net"

OCTANE_URL = "https://octane-prod.bmwgroup.net"
SHARED_SPACE = "1002"
WORKSPACE = "2001"
OCTANE_BASE = f"{OCTANE_URL}/api/shared_spaces/{SHARED_SPACE}/workspaces/{WORKSPACE}"

JIRA_TOKEN_ENV = "JIRA_TOKEN"
JIRA_USER_ENV  = "JIRA_USER"
OCTANE_TOKEN_ENV = "OCTANE_TOKEN"

REJECTED_RESOLUTIONS = {"rejected", "won't do"}
DUPLICATE_RESOLUTIONS = {"duplicate"}
CANNOT_REPRODUCE_RESOLUTIONS = {"cannot reproduce"}
DONE_RESOLUTIONS = {"done"}

OCTANE_URL_PATTERN = re.compile(
    r'octane[^"\'>\s]*?(?:/(?:defects?|work[_-]?items?|entity)[/=]|[?&]id=)(\d+)', re.IGNORECASE)

DUPLICATE_JIRA_ID_PATTERN = re.compile(r'(?:IDCEVODEV|HU22DM)-\d+')
OCTANE_ID_PATTERN = re.compile(r'(?<!\d)(\d{6,7})(?!\d)')
JIRA_COLOR_TAG_PATTERN = re.compile(r'\{color(?::[^}]*)?\}')
JIRA_CODE_BLOCK_PATTERN = re.compile(r'\{code(?::[^}]*)?\}.*?\{code\}', re.DOTALL)

DUPLICATE_KEYWORDS = [
    "duplicate to", "duplicates to", "duplicated to", "duplicate of",
    "duplicates", "dup to", "dup of",
    "master is", "master ticket", "master:", "master defect",
]

# Authors to ignore for duplicate keyword scanning (automated bots / pre-analysis tools)
DUPLICATE_IGNORE_AUTHORS = {"techuser apinext ci cd"}

EXPECTED_BEHAVIOR_KEYWORDS = [
    "works as specified", "works as expected", "works as designed",
    "expected behavior", "expected behaviour", "this is expected",
    "as designed", "by design", "per specification", "per spec",
]

MISSING_TRACES_PATTERNS = [
    re.compile(r'\bmissing\b[\w\s]{0,40}\btraces\b', re.IGNORECASE),
    re.compile(r'\bplease\s+(?:attach|add)\b[\w\s]{0,40}\btraces\b', re.IGNORECASE),
]


# ── Backend Provider Routing ──────────────────────────────────────────────────

FIELD_OWNER = "owner"
FIELD_ASSIGNED_ECU = "assigned_ecu_udf"
FIELD_PROBLEM_CATEGORY = "problem_category_udf"
FIELD_SOLUTION_RESPONSIBLE = "solution_responsible_udf"

PHASE_IN_ANALYSIS = {"type": "phase", "id": "phase.defect.opened"}

BLOCKING_REASON_NOT_RESPONSIBLE = {
    "type": "list_node", "id": "not_responsible_ln", "logical_name": "not_responsible_ln",
}

BACKEND_PROVIDERS = [
    {
        "name": "Map Data Issues (HERE)",
        "short": "Map Data",
        "owner_name": "Tobias Naumann",
        "owner": {"type": "workspace_user", "id": "253037"},
        "assigned_ecu": {"type": "list_node", "id": "dvq836zxwjqywt70py5774emp", "logical_name": "dvq836zxwjqywt70py5774emp"},
        "problem_category": {"type": "list_node", "id": "gjq1zne1jm02lt2lqlj0ez867", "logical_name": "gjq1zne1jm02lt2lqlj0ez867"},
        "problem_category_name": "IDC_mapdata",
        "solution_responsible": None,
    },
    {
        "name": "Japan Backend Map Provider (Zenrin)",
        "short": "Zenrin",
        "owner_name": "Jinglei Huang",
        "owner": {"type": "workspace_user", "id": "272012"},
        "assigned_ecu": {"type": "list_node", "id": "69g1j2e6v8dw5azl5kex3jmk2", "logical_name": "69g1j2e6v8dw5azl5kex3jmk2"},
        "problem_category": {"type": "list_node", "id": "oq62lej4e53k9skgxe0j28mjk", "logical_name": "oq62lej4e53k9skgxe0j28mjk"},
        "problem_category_name": "Road Map Japan",
        "solution_responsible": None,
    },
    {
        "name": "Point of Interest / Search Content (HERE)",
        "short": "POI / Search Content",
        "owner_name": "Christoph Schoerner",
        "owner": {"type": "workspace_user", "id": "29242"},
        "assigned_ecu": {"type": "list_node", "id": "dvq836zxwjqywt70py5774emp", "logical_name": "dvq836zxwjqywt70py5774emp"},
        "problem_category": {"type": "list_node", "id": "qm5pl7eno07mmc2zzymx1wjge", "logical_name": "qm5pl7eno07mmc2zzymx1wjge"},
        "problem_category_name": "Online_Content_HERE",
        "solution_responsible": None,
    },
    {
        "name": "LOS Backend",
        "short": "LOS Backend",
        "owner_name": "Stephan Oertelt",
        "owner": {"type": "workspace_user", "id": "515094"},
        "assigned_ecu": {"type": "list_node", "id": "mdr6nl4vjozxpa60z3116w3e8", "logical_name": "mdr6nl4vjozxpa60z3116w3e8"},
        "problem_category": {"type": "list_node", "id": "offboard_los_ln", "logical_name": "offboard_los_ln"},
        "problem_category_name": "Offboard LOS",
        "solution_responsible": {"type": "list_node", "id": "39znonz0xdl3cgl5vyzmojvg1", "logical_name": "39znonz0xdl3cgl5vyzmojvg1"},
    },
    {
        "name": "Traffic Content (HERE)",
        "short": "Traffic Content",
        "owner_name": "Cornelia Schrei",
        "owner": {"type": "workspace_user", "id": "470011"},
        "assigned_ecu": {"type": "list_node", "id": "dvq836zxwjqywt70py5774emp", "logical_name": "dvq836zxwjqywt70py5774emp"},
        "problem_category": {"type": "list_node", "id": "4mxrjn7dnzeqgawzw012zlydq", "logical_name": "4mxrjn7dnzeqgawzw012zlydq"},
        "problem_category_name": "Traffic_Information",
        "solution_responsible": None,
    },
    {
        "name": "FuDe / Learning",
        "short": "FuDe",
        "owner_name": "Simon Springmann",
        "owner": {"type": "workspace_user", "id": "10023"},
        "assigned_ecu": None,
        "problem_category": {"type": "list_node", "id": "8z2401zl4wqqmi8pgvoy7keg6", "logical_name": "8z2401zl4wqqmi8pgvoy7keg6"},
        "problem_category_name": "FuDe_Backend",
        "solution_responsible": None,
    },
    {
        "name": "Perseus",
        "short": "Perseus",
        "owner_name": None,
        "owner": None,
        "assigned_ecu": {"type": "list_node", "id": "mdr6nl4vjozxpa60z3116w3e8", "logical_name": "mdr6nl4vjozxpa60z3116w3e8"},
        "problem_category": {"type": "list_node", "id": "d1598r4v3665pa052nqd3l60k", "logical_name": "d1598r4v3665pa052nqd3l60k"},
        "problem_category_name": "Offboard PERSEUS",
        "solution_responsible": {"type": "list_node", "id": "39znonz0xdl3cgl5vyzmojvg1", "logical_name": "39znonz0xdl3cgl5vyzmojvg1"},
    },
]

_PROVIDER_KEYWORDS = r'HERE|Zenrin|backend|BE|Perseus|LOS|FuDe|Traffic|POI|Map\s*Data'

BACKEND_KEYWORDS_PATTERNS = [
    re.compile(r'\bmust\s+be\s+(?:checked|analyzed|investigated)\s+(?:from|in|by)\s+(?:the\s+)?(?:backend|BE|DB|HERE)\b', re.IGNORECASE),
    re.compile(r'\bplease\s+(?:check|investigate|analyze)\s+(?:in|from|at)\s+(?:the\s+)?(?:backend|BE|DB|HERE)\b', re.IGNORECASE),
    re.compile(r'\bassign\w*\s+(?:\S+\s+){0,6}(?:' + _PROVIDER_KEYWORDS + r')\b', re.IGNORECASE),
    re.compile(r'\b(?:DB|BE|backend|HERE)\s+issue\b', re.IGNORECASE),
    re.compile(r'\b(?:data\s*base|database)\s+issue\b', re.IGNORECASE),
    re.compile(r'\bbackend\s+(?:problem|defect|bug)\b', re.IGNORECASE),
    re.compile(r'\bnot\s+(?:a\s+)?(?:navi|navigation|HMI|client)\s+(?:issue|problem|defect)\b', re.IGNORECASE),
]

DEFECT_CATEGORY_PATTERN = re.compile(
    r'(?:defect|problem)\s*(?:/\s*defect)?\s*category\s*[:=]\s*(\S+)', re.IGNORECASE)

ASSIGNED_ECU_PATTERN = re.compile(r'assigned\s*ECU\s*[:=]\s*(\S+)', re.IGNORECASE)


# ── OCTANE field constants ────────────────────────────────────────────────────

BLOCKING_REASON_FIELD = "blocking_reason_udf"
BLOCKING_REASON_EXPECTED_BEHAVIOUR = {
    "type": "list_node", "id": "expected_behaviour_ln", "logical_name": "expected_behaviour_ln",
}
BLOCKING_REASON_CHILD_DUPLICATE = {
    "type": "list_node", "id": "duplicate_ln", "logical_name": "duplicate_ln",
}
PARENT_CHILD_CHILD = {
    "type": "list_node", "id": "child_ln", "logical_name": "child_ln",
}
BLOCKING_REASON_NOT_REPRODUCIBLE = {
    "type": "list_node", "id": "e925j0ov2vlw8i9e6qn4xjqkz", "logical_name": "e925j0ov2vlw8i9e6qn4xjqkz",
}
BLOCKING_REASON_ADDITIONAL_INFO_NAME = "Additional Information necessary"
BLOCKING_REASON_ADDITIONAL_INFO = {
    "type": "list_node", "id": "20vl1n7zw5vd2ikdkmnyyxn9k", "logical_name": "20vl1n7zw5vd2ikdkmnyyxn9k",
}
_BLOCKING_REASON_ROOT = "q0pk3rm202r22hwjk06xyd27m"

TARGET_PHASE = "01-New"
TQR_WELL_CREATED = {
    "type": "list_node", "id": "ndg3jor26gmplhekep6gyjk5p", "logical_name": "ndg3jor26gmplhekep6gyjk5p",
}


# ══════════════════════════════════════════════════════════════════════════════
# Core Functions
# ══════════════════════════════════════════════════════════════════════════════

def _read_netrc(machine: str) -> tuple:
    """Read login/password from ~/.netrc for the given machine."""
    netrc_path = os.path.expanduser("~/.netrc")
    if not os.path.exists(netrc_path):
        return None, None
    try:
        with open(netrc_path, "r") as f:
            content = f.read()
    except OSError:
        return None, None

    tokens = content.split()
    i = 0
    while i < len(tokens):
        if tokens[i] == "machine" and i + 1 < len(tokens):
            if tokens[i + 1] == machine:
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
    _bad = set('",;\\')
    clean = "".join(c for c in access_token if 0x20 < ord(c) < 0x7F and c not in _bad)
    session.cookies.set("access_token", clean, domain="octane-prod.bmwgroup.net")
    return session


# ── OCTANE: extract Jira ID ───────────────────────────────────────────────────

_SUPPLIER_TICKET_CANDIDATES = [
    "ticketno_supplier_udf", "ticket_no_supplier_udf", "ticket_no__supplier_udf",
    "ticket_number_supplier_udf", "supplier_ticket_udf", "supplier_ticket_no_udf",
]
JIRA_ID_PATTERN = re.compile(r'[A-Z][A-Z0-9]+-\d+')


def _discover_supplier_ticket_field(session: requests.Session, defect_id: str) -> Optional[str]:
    """Discover the API field name for 'Ticket no. supplier'."""
    try:
        r = session.get(f"{OCTANE_BASE}/defects/{defect_id}", timeout=30)
        if r.ok:
            for key in r.json().keys():
                kl = key.lower()
                if "ticket" in kl and "supplier" in kl:
                    return key
    except Exception:
        pass

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


def extract_jira_id_from_octane(session: requests.Session, defect_id: str) -> Optional[str]:
    """Extract the Jira ticket ID from an OCTANE defect's 'Ticket no. supplier' field."""
    field_name = _discover_supplier_ticket_field(session, defect_id)
    if not field_name:
        return None

    try:
        r = session.get(f"{OCTANE_BASE}/defects/{defect_id}",
                        params={"fields": f"id,name,{field_name}"}, timeout=30)
        if not r.ok:
            return None
    except requests.RequestException:
        return None

    raw_value = r.json().get(field_name)
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
    if len(stripped) < 50:
        return stripped
    return None


# ── OCTANE: update functions ──────────────────────────────────────────────────

def set_octane_blocking_reason(session: requests.Session, defect_id: str) -> bool:
    """Set 'Blocking reason' to 'Expected behaviour'."""
    payload = {BLOCKING_REASON_FIELD: BLOCKING_REASON_EXPECTED_BEHAVIOUR}
    try:
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        return r.ok
    except requests.RequestException:
        return False


def set_octane_child_duplicate(session: requests.Session, defect_id: str, master_octane_id: str) -> bool:
    """Set duplicate child fields on an OCTANE defect."""
    payload = {
        BLOCKING_REASON_FIELD: BLOCKING_REASON_CHILD_DUPLICATE,
        "parent_child_udf": PARENT_CHILD_CHILD,
        "relation_to_udf": master_octane_id,
    }
    try:
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        return r.ok
    except requests.RequestException:
        return False


def set_octane_not_reproducible(session: requests.Session, defect_id: str) -> bool:
    """Set 'Blocking reason' to 'Not reproducible'."""
    payload = {BLOCKING_REASON_FIELD: BLOCKING_REASON_NOT_REPRODUCIBLE}
    try:
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        return r.ok
    except requests.RequestException:
        return False


# Known list_root IDs for target fields (discovered via API testing).
_FIELD_LIST_ROOTS = {
    "target_i_step_udf": "ypz1k378y1jyzu9l24z80nx3m",   # "PbM I-Step"
    "target_week_udf":   "75ezlndn4nm87cjo1woj3r3j9",   # "Pbm Target Week List"
}


def _resolve_list_node_field(session: requests.Session, field_name: str,
                             value: str, defect_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Resolve a user-entered text value to a list_node object for the given field."""
    root_id = _FIELD_LIST_ROOTS.get(field_name)
    print(f"  [resolve] field={field_name}, value='{value}', root_id={root_id}")
    if root_id:
        node = _discover_list_node(session, root_id, value)
        print(f"  [resolve] result: {node}")
        if node:
            return node
    return None


def _discover_list_node(session: requests.Session, root_id: str, name: str) -> Optional[Dict[str, str]]:
    """Find a list_node by display name under a given root.

    Searches globally by name then filters by list_root ID in Python,
    because OCTANE's query parser does not accept non-numeric IDs in
    the ``{id=...}`` syntax.
    """
    try:
        r = session.get(f"{OCTANE_BASE}/list_nodes", params={
            "fields": "id,name,logical_name,list_root",
            "query": f'"name EQ ^{name}^"',
            "limit": 100,
        }, timeout=30)
        print(f"  [discover] search name='{name}' → {r.status_code}, "
              f"count={len(r.json().get('data', [])) if r.ok else '?'}")
        if not r.ok:
            print(f"  [discover] error: {r.text[:200]}")
            return None
    except requests.RequestException as e:
        print(f"  [discover] exception: {e}")
        return None
    for node in r.json().get("data", []):
        lr = node.get("list_root", {})
        lr_id = lr.get("id", "") if isinstance(lr, dict) else ""
        if node.get("name", "").lower() == name.lower() and lr_id == root_id:
            print(f"  [discover] matched: id={node['id']}, root={lr_id}")
            return {"type": "list_node", "id": node["id"], "logical_name": node.get("logical_name", node["id"])}
    print(f"  [discover] no match for root={root_id}")
    return None


def set_octane_additional_info_needed(session: requests.Session, defect_id: str) -> bool:
    """Set 'Blocking reason' to 'Additional Information necessary'."""
    payload = {BLOCKING_REASON_FIELD: BLOCKING_REASON_ADDITIONAL_INFO}
    try:
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        return r.ok
    except requests.RequestException:
        return False


def set_octane_phase(session: requests.Session, defect_id: str,
                    phase_name: str = TARGET_PHASE,
                    extra_fields: Optional[Dict[str, Any]] = None) -> bool:
    """Move an OCTANE defect to the given phase."""
    try:
        r = session.get(f"{OCTANE_BASE}/phases",
                        params={"fields": "id,name,entity", "limit": 200}, timeout=30)
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

    payload = {
        "phase": {"type": "phase", "id": phase_id},
        "tqr_udf": {"data": [TQR_WELL_CREATED]},
    }
    if extra_fields:
        payload.update(extra_fields)
    try:
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        if r.ok:
            return True
        payload["id"] = defect_id
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        return r.ok
    except requests.RequestException:
        return False


# ── Jira API helpers ──────────────────────────────────────────────────────────

def get_previous_resolution(session: requests.Session, jira_url: str, issue_key: str) -> Optional[str]:
    """Check Jira changelog for the resolution value immediately before it was set to 'Rejected'.
    Returns the previous resolution (fromString) of the last change that set resolution to Rejected."""
    url = f"{jira_url}/rest/api/2/issue/{issue_key}"
    params = {"expand": "changelog", "fields": "summary"}
    try:
        r = session.get(url, params=params, timeout=30)
    except requests.RequestException:
        return None
    if not r.ok:
        return None
    data = r.json()
    changelog = data.get("changelog", {})
    last_from = None
    for history in changelog.get("histories", []):
        for item in history.get("items", []):
            if item.get("field", "").lower() == "resolution":
                to_val = (item.get("toString") or "").lower()
                if to_val == "rejected":
                    last_from = (item.get("fromString") or "").lower()
    return last_from


def get_issue(session: requests.Session, jira_url: str, issue_key: str) -> Optional[Dict[str, Any]]:
    """Fetch issue metadata."""
    url = f"{jira_url}/rest/api/2/issue/{issue_key}"
    params = {"fields": "summary,status,resolution,resolutiondate,assignee,priority,comment"}
    try:
        r = session.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        print(f"  Connection error: {e}")
        return None
    if r.status_code == 401:
        print("  Authentication failed (401).")
        return None
    if r.status_code == 403:
        print("  Forbidden (403).")
        return None
    if r.status_code == 404:
        print(f"  Issue '{issue_key}' not found (404).")
        return None
    if not r.ok:
        print(f"  API error {r.status_code}: {r.text[:300]}")
        return None
    return r.json()


def extract_octane_id_from_jira_ticket(session: requests.Session, jira_url: str, issue_key: str) -> Optional[str]:
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


def get_comments(session: requests.Session, jira_url: str, issue_key: str) -> List[Dict[str, Any]]:
    """Fetch all comments for an issue, ordered newest first."""
    url = f"{jira_url}/rest/api/2/issue/{issue_key}/comment"
    all_comments: List[Dict[str, Any]] = []
    start_at = 0
    page_size = 100
    while True:
        params = {"orderBy": "-created", "startAt": start_at, "maxResults": page_size}
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.RequestException:
            break
        if not r.ok:
            break
        body = r.json()
        batch = body.get("comments", [])
        all_comments.extend(batch)
        total = body.get("total", 0)
        start_at += len(batch)
        if start_at >= total or not batch:
            break
    return all_comments


# ── Text extraction ───────────────────────────────────────────────────────────

def _adf_to_text(node: Any) -> str:
    """Recursively extract plain text from Atlassian Document Format."""
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
    """Return the plain-text body of a comment."""
    body = comment.get("body", "")
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        return _adf_to_text(body)
    return ""


# ── Classification logic ──────────────────────────────────────────────────────

def _find_excerpt(text: str, keyword: str, radius: int = 120) -> str:
    """Return a short excerpt around the matching keyword."""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return text[:200]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(keyword) + radius)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _relative_time(iso_timestamp: str) -> str:
    """Convert an ISO timestamp to relative time."""
    if not iso_timestamp:
        return "unknown"
    try:
        ts = iso_timestamp.replace("+0000", "+00:00").replace("+0100", "+01:00").replace("+0200", "+02:00")
        if "." in ts:
            ts = ts.split(".")[0] + ts[ts.rfind("+"):] if "+" in ts.split(".")[1] else ts.split(".")[0] + ts[ts.rfind("-", 10):] if "-" in ts[11:] else ts.split(".")[0]
        dt = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            mins = seconds // 60
            return f"{mins} minute{'s' if mins != 1 else ''} ago"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = seconds // 86400
        if days == 1:
            return "yesterday"
        if days < 30:
            return f"{days} days ago"
        return iso_timestamp[:10]
    except (ValueError, IndexError):
        return iso_timestamp[:10] if len(iso_timestamp) >= 10 else iso_timestamp


def classify_rejection(comments: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Single-pass newest-first scan of comments for ALL rejection sub-types."""
    for comment in comments:
        text = extract_comment_text(comment)
        text_lower = text.lower()
        author = (comment.get("author") or {}).get("displayName", "Unknown")
        created_raw = comment.get("created") or ""
        created = created_raw[:10]

        for keyword in EXPECTED_BEHAVIOR_KEYWORDS:
            if keyword in text_lower:
                return {
                    "verdict": "expected_behavior", "keyword": keyword,
                    "author": author, "created": created, "created_raw": created_raw,
                    "excerpt": _find_excerpt(text, keyword),
                }

        cat_match = DEFECT_CATEGORY_PATTERN.search(text)
        if cat_match:
            category = cat_match.group(1).strip()
            return {
                "verdict": "backend", "trigger": "defect_category",
                "matched_text": cat_match.group(0), "category": category,
                "provider_index": _find_provider_by_category(category),
                "author": author, "created": created, "created_raw": created_raw,
                "comment_text": text,
            }

        ecu_match = ASSIGNED_ECU_PATTERN.search(text)
        if ecu_match:
            ecu = ecu_match.group(1).strip()
            return {
                "verdict": "backend", "trigger": "assigned_ecu",
                "matched_text": ecu_match.group(0), "ecu": ecu,
                "provider_index": _find_provider_by_ecu(ecu, text),
                "author": author, "created": created, "created_raw": created_raw,
                "comment_text": text,
            }

        for pattern in BACKEND_KEYWORDS_PATTERNS:
            m = pattern.search(text)
            if m:
                return {
                    "verdict": "backend", "trigger": "backend_keyword",
                    "matched_text": m.group(0),
                    "provider_index": _infer_provider_from_context(text),
                    "author": author, "created": created, "created_raw": created_raw,
                    "comment_text": text,
                }

        for pattern in MISSING_TRACES_PATTERNS:
            m = pattern.search(text)
            if m:
                matched_text = m.group(0)
                return {
                    "verdict": "missing_traces", "keyword": matched_text,
                    "author": author, "created": created, "created_raw": created_raw,
                    "excerpt": _find_excerpt(text, matched_text),
                }

    return None


# ── Backend detection helpers ─────────────────────────────────────────────────

def _find_provider_by_category(category: str) -> Optional[int]:
    cat_lower = category.lower().replace(" ", "_")
    for i, p in enumerate(BACKEND_PROVIDERS):
        if p["problem_category_name"].lower().replace(" ", "_") == cat_lower:
            return i
    return None


def _find_provider_by_ecu(ecu: str, full_text: str = "") -> Optional[int]:
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
    text_lower = text.lower()
    for i in candidates:
        owner_name = BACKEND_PROVIDERS[i].get("owner_name")
        if owner_name and owner_name.lower() in text_lower:
            return i
    return None


def _infer_provider_from_context(text: str) -> Optional[int]:
    text_lower = text.lower()
    for i, p in enumerate(BACKEND_PROVIDERS):
        cat_name = p["problem_category_name"].lower()
        if cat_name in text_lower:
            return i
    if "zenrin" in text_lower or "japan" in text_lower:
        return 1
    if "traffic" in text_lower:
        return 4
    if "poi" in text_lower or "search content" in text_lower or "online_content" in text_lower:
        return 2
    if "los" in text_lower:
        return 3
    if "perseus" in text_lower:
        return 6
    if "fude" in text_lower or "learning" in text_lower:
        return 5
    if "here" in text_lower:
        here_providers = [i for i, p in enumerate(BACKEND_PROVIDERS)
                         if p.get("assigned_ecu") and p["assigned_ecu"]["id"] == "dvq836zxwjqywt70py5774emp"]
        resolved = _disambiguate_by_owner(here_providers, text)
        if resolved is not None:
            return resolved
    for i, p in enumerate(BACKEND_PROVIDERS):
        owner_name = p.get("owner_name")
        if owner_name and owner_name.lower() in text_lower:
            return i
    return None


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


def update_octane_backend(session: requests.Session, defect_id: str, provider_index: int) -> Dict[str, Any]:
    """Update OCTANE defect with all backend provider fields.
    Returns dict with keys: success (bool), phase_set (bool), phase_warning (str or None).
    """
    p = BACKEND_PROVIDERS[provider_index]
    try:
        r = session.get(f"{OCTANE_BASE}/defects/{defect_id}",
                        params={"fields": "id,software_version_udf"}, timeout=30)
        sw_version = r.json().get("software_version_udf") if r.ok else None
    except requests.RequestException:
        sw_version = None

    # Build the base fields (without phase)
    base_fields: Dict[str, Any] = {
        BLOCKING_REASON_FIELD: BLOCKING_REASON_NOT_RESPONSIBLE,
        FIELD_PROBLEM_CATEGORY: p["problem_category"],
        "tqr_udf": {"data": [TQR_WELL_CREATED]},
    }
    if sw_version:
        base_fields["software_version_udf"] = sw_version
    if p.get("owner"):
        base_fields[FIELD_OWNER] = p["owner"]
    if p.get("assigned_ecu"):
        base_fields[FIELD_ASSIGNED_ECU] = p["assigned_ecu"]
    if p.get("solution_responsible"):
        base_fields[FIELD_SOLUTION_RESPONSIBLE] = p["solution_responsible"]

    # Try phase transitions in order: 03-In Analysis, then 02-In Pre-Analysis
    PHASE_PRE_ANALYSIS = {"type": "phase", "id": "phase.defect.fixed"}
    phase_attempts = [
        (PHASE_IN_ANALYSIS, "03-In Analysis"),
        (PHASE_PRE_ANALYSIS, "02-In Pre-Analysis"),
    ]

    for phase_obj, phase_name in phase_attempts:
        payload = {"phase": phase_obj, **base_fields}
        try:
            r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
            if r.ok:
                return {"success": True, "phase_set": True, "phase_warning": None}
            print(f"  Phase {phase_name} error ({r.status_code}): {r.text[:300]}")
            # Retry with id
            payload["id"] = defect_id
            r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
            if r.ok:
                return {"success": True, "phase_set": True, "phase_warning": None}
            print(f"  Phase {phase_name} retry error ({r.status_code}): {r.text[:300]}")
        except requests.RequestException as e:
            print(f"  Phase {phase_name} exception: {e}")

    # All phase transitions failed — update fields without phase change
    print(f"  All phase transitions failed, updating fields without phase change")
    payload = {**base_fields}
    try:
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        if r.ok:
            return {"success": True, "phase_set": False, "phase_warning": "Phase could not be changed (transition blocked). Other fields were updated successfully."}
        print(f"  Fields-only error ({r.status_code}): {r.text[:300]}")
        payload["id"] = defect_id
        r = session.put(f"{OCTANE_BASE}/defects/{defect_id}", json=payload, timeout=30)
        if r.ok:
            return {"success": True, "phase_set": False, "phase_warning": "Phase could not be changed (transition blocked). Other fields were updated successfully."}
        print(f"  Fields-only retry error ({r.status_code}): {r.text[:300]}")
        return {"success": False, "phase_set": False, "phase_warning": None}
    except requests.RequestException as e:
        print(f"  Exception: {e}")
        return {"success": False, "phase_set": False, "phase_warning": None}


def extract_master_duplicate_octane_id(jira_session, jira_url: str, octane_session, issue_key: str) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
    """Extract the OCTANE ID of the master/duplicate ticket from Jira comments.
    Returns (master_octane_id, matched_comment_info) where matched_comment_info
    is {"author": ..., "created": ..., "text": ...} or None."""
    comments = get_comments(jira_session, jira_url, issue_key)
    if not comments:
        print("  No comments found.")
        return None, None

    print(f"  {len(comments)} comment(s) to scan.\n")

    for comment in comments:
        # Skip automated bot comments
        comment_author_lower = (comment.get("author") or {}).get("displayName", "").lower()
        if comment_author_lower in DUPLICATE_IGNORE_AUTHORS:
            continue
        text = extract_comment_text(comment)
        # Strip code blocks for keyword matching (avoid false matches in log traces)
        text_for_keywords = JIRA_CODE_BLOCK_PATTERN.sub('', text).lower()

        matched_keyword = None
        for keyword in DUPLICATE_KEYWORDS:
            if keyword in text_for_keywords:
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

        jira_ids = list(dict.fromkeys(m for m in DUPLICATE_JIRA_ID_PATTERN.findall(text) if m != issue_key))
        # Strip code blocks and color markup before extracting bare OCTANE IDs
        text_clean = JIRA_CODE_BLOCK_PATTERN.sub('', text)
        text_clean = JIRA_COLOR_TAG_PATTERN.sub('', text_clean)
        # Extract bare OCTANE IDs but exclude numbers that are part of Jira IDs (e.g. "899011" from "IDCEVODEV-899011")
        jira_id_numbers = set()
        for jid in jira_ids:
            parts = jid.split("-")
            if len(parts) == 2:
                jira_id_numbers.add(parts[1])
        octane_ids = list(dict.fromkeys(oid for oid in OCTANE_ID_PATTERN.findall(text_clean) if oid not in jira_id_numbers))
        print(f"  Jira IDs found (excl. self): {jira_ids}")
        print(f"  Bare OCTANE IDs found: {octane_ids}")
        print()

        comment_info = {"author": author, "created": created, "text": text[:600]}

        if len(jira_ids) > 1:
            print(f"  AMBIGUOUS: Multiple Jira IDs found: {jira_ids}")
            return None, comment_info

        if len(jira_ids) == 1:
            master_jira_id = jira_ids[0]
            print(f"  Resolving Jira ID '{master_jira_id}' to OCTANE ID via remote links …")
            master_octane_id = extract_octane_id_from_jira_ticket(jira_session, jira_url, master_jira_id)
            if master_octane_id:
                print(f"  ✓ Master OCTANE ID: {master_octane_id}  (via Jira {master_jira_id})")
                return master_octane_id, comment_info
            else:
                print(f"  ⚠ Could not resolve Jira '{master_jira_id}' to OCTANE ID — master ticket has no OCTANE ID.")
                # Only fall back to bare OCTANE IDs if they are genuinely separate IDs (not from Jira suffix)
                if len(octane_ids) == 1:
                    print(f"  Falling back to bare OCTANE ID: {octane_ids[0]}")
                    return octane_ids[0], comment_info
                elif len(octane_ids) > 1:
                    print(f"  AMBIGUOUS: Multiple bare OCTANE IDs: {octane_ids}")
                    return None, comment_info
                else:
                    # No valid OCTANE ID available — report clearly
                    comment_info["no_octane_warning"] = f"Master Jira ticket {master_jira_id} has no OCTANE ID."
                    return None, comment_info

        if len(octane_ids) == 1:
            print(f"  Using bare OCTANE ID: {octane_ids[0]}")
            return octane_ids[0], comment_info
        elif len(octane_ids) > 1:
            print(f"  AMBIGUOUS: Multiple bare OCTANE IDs: {octane_ids}")
            return None, comment_info

        print(f"  Keyword matched but no ticket IDs found.")
        continue

    print("  No duplicate/master reference found in any comment.")
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
# Web GUI (Flask)
# ══════════════════════════════════════════════════════════════════════════════

def _get_sessions(user_octane_token=None):
    """Build Jira and OCTANE sessions from environment/netrc/user input."""
    jira_token = os.environ.get("JIRA_TOKEN")
    jira_user = os.environ.get("JIRA_USER")
    if not jira_token:
        login, password = _read_netrc("jira.cc.bmwgroup.net")
        if password:
            jira_token = password
            if not jira_user:
                jira_user = login

    octane_token = user_octane_token or os.environ.get("OCTANE_TOKEN")
    if not octane_token:
        login, password = _read_netrc("octane-prod.bmwgroup.net")
        if password:
            octane_token = password

    if not jira_token:
        return None, None, "Missing Jira credentials"
    if not octane_token:
        return None, None, "Missing OCTANE credentials. Please paste your token above."

    jira_session = build_jira_session(jira_token, jira_user)
    octane_session = build_octane_session(octane_token)
    return jira_session, octane_session, None


def create_web_app():
    """Create and configure the Flask web application."""
    from flask import Flask, request as flask_request, jsonify as flask_jsonify, render_template_string

    app = Flask(__name__)

    @app.route("/api/process", methods=["POST"])
    def api_process():
        data = flask_request.get_json()
        raw_jira_id = data.get("jira_id", "").strip()
        octane_id = data.get("octane_id", "").strip()
        VALID_JIRA_PREFIXES = ("HU22DM-", "IDCEVODEV-")

        # ── Jira-first mode: user entered a Jira ID directly in the Process field ──
        if raw_jira_id and raw_jira_id.upper().startswith(VALID_JIRA_PREFIXES) and not octane_id:
            user_octane_token = data.get("octane_token", "").strip() or None
            jira_session, octane_session, err = _get_sessions(user_octane_token)
            if err:
                return flask_jsonify({"error": err}), 401
            issue_key = raw_jira_id
            issue = get_issue(jira_session, JIRA_URL, issue_key)
            if not issue:
                return flask_jsonify({"error": f"Could not fetch Jira issue {issue_key}"}), 404
            fields = issue.get("fields", {})
            summary = fields.get("summary", "")
            status = (fields.get("status") or {}).get("name", "Unknown")
            resolution = fields.get("resolution")
            res_name = (resolution or {}).get("name", "Unresolved") if resolution else "Unresolved"
            res_date = (fields.get("resolutiondate") or "")[:10]
            priority = (fields.get("priority") or {}).get("name", "")
            assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
            result = {
                "octane_id": "", "issue_key": issue_key, "summary": summary,
                "status": status, "resolution": res_name, "resolution_date": res_date,
                "priority": priority, "assignee": assignee,
                "jira_url": f"{JIRA_URL}/browse/{issue_key}",
                "input_mode": "jira",
            }
            res_lower = res_name.lower()
            is_done = res_lower in DONE_RESOLUTIONS
            if not is_done:
                result["path"] = "none"
                result["message"] = f"Resolution is '{res_name}' — no action needed."
                return flask_jsonify(result)
            result["path"] = "done"
            # Fetch info fields
            try:
                info_r = jira_session.get(
                    f"{JIRA_URL}/rest/api/2/issue/{issue_key}",
                    params={"fields": "*all", "expand": "names"}, timeout=30)
                if info_r.ok:
                    info_body = info_r.json()
                    field_names = info_body.get("names", {})
                    info_fields = info_body.get("fields", {})
                    rev = {v.lower(): k for k, v in field_names.items()}
                    def _find_field_j(keywords):
                        for label, fid in rev.items():
                            if all(kw in label for kw in keywords):
                                return fid
                        return None
                    sop_fid = _find_field_j(["first", "use"]) or _find_field_j(["sop"])
                    shift_fid = _find_field_j(["shift", "set"])
                    def _str_val_j(v):
                        if v is None: return ""
                        if isinstance(v, dict): return v.get("value") or v.get("name") or ""
                        if isinstance(v, list): return ", ".join(_str_val_j(x) for x in v if x)
                        return str(v)
                    if sop_fid:
                        result["first_use_sop"] = _str_val_j(info_fields.get(sop_fid, ""))
                    if shift_fid:
                        result["shift_to_set"] = _str_val_j(info_fields.get(shift_fid, ""))
            except Exception:
                pass
            return flask_jsonify(result)

        # ── Normal OCTANE-first mode ──
        if not octane_id or not octane_id.isdigit():
            return flask_jsonify({"error": "Invalid OCTANE ID"}), 400

        user_octane_token = data.get("octane_token", "").strip() or None
        jira_session, octane_session, err = _get_sessions(user_octane_token)
        if err:
            return flask_jsonify({"error": err}), 401

        try:
            r = octane_session.get(f"{OCTANE_BASE}/defects", params={"fields": "id", "limit": 1}, timeout=30)
            if r.status_code == 401:
                return flask_jsonify({"error": "OCTANE token expired or invalid (401). Please refresh your token."}), 401
            if not r.ok:
                return flask_jsonify({"error": f"OCTANE API error {r.status_code}: {r.text[:200]}"}), 502
        except Exception as e:
            return flask_jsonify({"error": f"OCTANE connection error: {e}"}), 502

        # Use manually provided Jira ID if given (from missing_jira_id flow), otherwise extract from OCTANE
        manual_jira_id = raw_jira_id  # may be set by reprocessWithJiraId
        if manual_jira_id:
            issue_key = manual_jira_id
        else:
            issue_key = extract_jira_id_from_octane(octane_session, octane_id)
            if not issue_key:
                return flask_jsonify({
                    "octane_id": octane_id,
                    "octane_url": f"{OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={octane_id}",
                    "path": "missing_jira_id",
                    "error": f"'Ticket no. supplier' field is empty in OCTANE #{octane_id}. Please enter the Jira ID manually.",
                }), 200

        # Validate Jira ID prefix
        VALID_JIRA_PREFIXES = ("HU22DM-", "IDCEVODEV-")
        if not issue_key.startswith(VALID_JIRA_PREFIXES):
            return flask_jsonify({
                "octane_id": octane_id,
                "octane_url": f"{OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={octane_id}",
                "path": "invalid_jira_id",
                "invalid_jira_id": issue_key,
                "error": f"'{issue_key}' is not a valid Navi Jira ID (expected HU22DM-* or IDCEVODEV-*). You can enter the master OCTANE ID manually if this is a duplicate.",
            }), 200

        issue = get_issue(jira_session, JIRA_URL, issue_key)
        if not issue:
            return flask_jsonify({"error": f"Could not fetch Jira issue {issue_key}"}), 404

        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        status = (fields.get("status") or {}).get("name", "Unknown")
        resolution = fields.get("resolution")
        res_name = (resolution or {}).get("name", "Unresolved") if resolution else "Unresolved"
        res_date = (fields.get("resolutiondate") or "")[:10]
        priority = (fields.get("priority") or {}).get("name", "")
        assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")

        result = {
            "octane_id": octane_id, "issue_key": issue_key, "summary": summary,
            "status": status, "resolution": res_name, "resolution_date": res_date,
            "priority": priority, "assignee": assignee,
            "octane_url": f"{OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={octane_id}",
            "jira_url": f"{JIRA_URL}/browse/{issue_key}",
            "input_mode": "octane",
        }

        res_lower = res_name.lower()
        is_rejected = res_lower in REJECTED_RESOLUTIONS
        is_duplicate = res_lower in DUPLICATE_RESOLUTIONS
        is_cannot_reproduce = res_lower in CANNOT_REPRODUCE_RESOLUTIONS
        is_done = res_lower in DONE_RESOLUTIONS

        if not is_rejected and not is_duplicate and not is_cannot_reproduce and not is_done:
            result["path"] = "none"
            result["message"] = f"Resolution is '{res_name}' — no action needed."
            return flask_jsonify(result)

        # PATH D: Done — need version selection; also fetch read-only info fields
        if is_done:
            result["path"] = "done"
            try:
                info_r = jira_session.get(
                    f"{JIRA_URL}/rest/api/2/issue/{issue_key}",
                    params={"fields": "*all", "expand": "names"},
                    timeout=30)
                if info_r.ok:
                    info_body = info_r.json()
                    field_names = info_body.get("names", {})  # customfieldXXX → display name
                    info_fields = info_body.get("fields", {})
                    # Build reverse map: display name (lower) → field id
                    rev = {v.lower(): k for k, v in field_names.items()}
                    def _find_field(keywords):
                        for label, fid in rev.items():
                            if all(kw in label for kw in keywords):
                                return fid
                        return None
                    sop_fid = _find_field(["first", "use"]) or _find_field(["sop"])
                    shift_fid = _find_field(["shift", "set"])
                    def _str_val(v):
                        if v is None: return ""
                        if isinstance(v, dict): return v.get("value") or v.get("name") or ""
                        if isinstance(v, list): return ", ".join(_str_val(x) for x in v if x)
                        return str(v)
                    if sop_fid:
                        result["first_use_sop"] = _str_val(info_fields.get(sop_fid, ""))
                    if shift_fid:
                        result["shift_to_set"] = _str_val(info_fields.get(shift_fid, ""))
            except Exception:
                pass  # info fields are optional, don't fail the whole request
            return flask_jsonify(result)

        # PATH A: Rejected
        if is_rejected:
            comments = get_comments(jira_session, JIRA_URL, issue_key)
            result["comment_count"] = len(comments)

            # Step 1: Check Jira changelog — what was the resolution before it became "Rejected"?
            prev_resolution = get_previous_resolution(jira_session, JIRA_URL, issue_key)
            if prev_resolution == "cannot reproduce":
                result["path"] = "cannot_reproduce"
                return flask_jsonify(result)
            elif prev_resolution in DUPLICATE_RESOLUTIONS:
                master_octane_id, matched_comment = extract_master_duplicate_octane_id(jira_session, JIRA_URL, octane_session, issue_key)
                if matched_comment:
                    result["duplicate_comment"] = matched_comment
                result["path"] = "duplicate"
                if master_octane_id:
                    result["master_octane_id"] = master_octane_id
                    result["master_url"] = f"{OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={master_octane_id}"
                else:
                    warning = (matched_comment or {}).get("no_octane_warning", "")
                    result["error"] = warning or "Could not determine master OCTANE ID from comments."
                return flask_jsonify(result)

            # Step 2: Comment-based classification (expected behavior, backend, missing traces)
            # Also scan for duplicate keywords in comments
            dup_comment_index = None
            for idx, comment in enumerate(comments):
                # Skip automated bot comments
                comment_author = (comment.get("author") or {}).get("displayName", "").lower()
                if comment_author in DUPLICATE_IGNORE_AUTHORS:
                    continue
                text = extract_comment_text(comment)
                # Strip code blocks for keyword matching
                text_for_keywords = JIRA_CODE_BLOCK_PATTERN.sub('', text).lower()
                has_dup_keyword = any(kw in text_for_keywords for kw in DUPLICATE_KEYWORDS)
                if not has_dup_keyword:
                    continue
                # Strip code blocks and color tags before looking for ticket references
                text_stripped = JIRA_CODE_BLOCK_PATTERN.sub('', text)
                text_stripped = JIRA_COLOR_TAG_PATTERN.sub('', text_stripped)
                jira_ids = [m for m in DUPLICATE_JIRA_ID_PATTERN.findall(text_stripped) if m != issue_key]
                octane_ids = OCTANE_ID_PATTERN.findall(text_stripped)
                if jira_ids or octane_ids:
                    dup_comment_index = idx
                    break

            classification = classify_rejection(comments)

            # Determine which signal is newer (lower index = newer comment)
            classification_index = None
            if classification:
                cls_created = classification.get("created_raw", "")
                for idx, comment in enumerate(comments):
                    if (comment.get("created") or "").startswith(classification.get("created", "~~")):
                        author = (comment.get("author") or {}).get("displayName", "")
                        if author == classification.get("author", "~~"):
                            classification_index = idx
                            break

            # If duplicate keyword (with ticket ID) is in a newer comment, prefer duplicate path
            if dup_comment_index is not None and (classification_index is None or dup_comment_index < classification_index):
                master_octane_id, matched_comment = extract_master_duplicate_octane_id(jira_session, JIRA_URL, octane_session, issue_key)
                if matched_comment:
                    result["duplicate_comment"] = matched_comment
                result["path"] = "duplicate"
                if master_octane_id:
                    result["master_octane_id"] = master_octane_id
                    result["master_url"] = f"{OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={master_octane_id}"
                else:
                    warning = (matched_comment or {}).get("no_octane_warning", "")
                    result["error"] = warning or "Could not determine master OCTANE ID from comments."
                return flask_jsonify(result)

            if classification is None:
                # Fallback: duplicate keyword without ticket ID
                if dup_comment_index is not None:
                    master_octane_id, matched_comment = extract_master_duplicate_octane_id(jira_session, JIRA_URL, octane_session, issue_key)
                    if matched_comment:
                        result["duplicate_comment"] = matched_comment
                    result["path"] = "duplicate"
                    if master_octane_id:
                        result["master_octane_id"] = master_octane_id
                        result["master_url"] = f"{OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={master_octane_id}"
                    else:
                        warning = (matched_comment or {}).get("no_octane_warning", "")
                        result["error"] = warning or "Could not determine master OCTANE ID from comments."
                    return flask_jsonify(result)
                result["path"] = "rejected_unknown"
                result["message"] = "No pattern matched in any comment (not expected behavior, backend, or missing traces)."
                return flask_jsonify(result)

            verdict = classification["verdict"]
            rel_time = _relative_time(classification.get("created_raw", ""))
            result["path"] = f"rejected_{verdict}"
            result["verdict"] = verdict
            result["classification"] = {
                "author": classification.get("author", ""),
                "created": classification.get("created", ""),
                "created_raw": classification.get("created_raw", ""),
                "relative_time": rel_time,
            }

            if verdict == "expected_behavior":
                result["classification"]["keyword"] = classification.get("keyword", "")
                result["classification"]["excerpt"] = classification.get("excerpt", "")
            elif verdict == "backend":
                result["classification"]["trigger"] = classification.get("trigger", "")
                result["classification"]["matched_text"] = classification.get("matched_text", "")
                result["classification"]["comment_text"] = classification.get("comment_text", "")
                result["classification"]["provider_index"] = classification.get("provider_index")
                result["providers"] = [
                    {"index": i, "name": p["name"], "owner": p.get("owner_name", ""),
                     "category": p.get("problem_category_name", "")}
                    for i, p in enumerate(BACKEND_PROVIDERS)
                ]
            elif verdict == "missing_traces":
                result["classification"]["keyword"] = classification.get("keyword", "")
                result["classification"]["excerpt"] = classification.get("excerpt", "")

            return flask_jsonify(result)

        # PATH B: Cannot Reproduce
        if is_cannot_reproduce:
            result["path"] = "cannot_reproduce"
            return flask_jsonify(result)

        # PATH C: Duplicate
        if is_duplicate:
            master_octane_id, matched_comment = extract_master_duplicate_octane_id(jira_session, JIRA_URL, octane_session, issue_key)
            if matched_comment:
                result["duplicate_comment"] = matched_comment
            result["path"] = "duplicate"
            if master_octane_id:
                result["master_octane_id"] = master_octane_id
                result["master_url"] = f"{OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={master_octane_id}"
            else:
                warning = (matched_comment or {}).get("no_octane_warning", "")
                result["error"] = warning or "Could not determine master OCTANE ID from comments."
            return flask_jsonify(result)

        return flask_jsonify(result)

    @app.route("/api/execute", methods=["POST"])
    def api_execute():
        data = flask_request.get_json()
        octane_id = data.get("octane_id", "").strip()
        action = data.get("action", "")
        if not octane_id or not octane_id.isdigit():
            return flask_jsonify({"error": "Invalid OCTANE ID"}), 400

        user_octane_token = data.get("octane_token", "").strip() or None
        _, octane_session, err = _get_sessions(user_octane_token)
        if err:
            return flask_jsonify({"error": err}), 401
        jira_session, _, _ = _get_sessions(user_octane_token)

        result = {"octane_id": octane_id, "action": action}

        if action == "expected_behavior":
            updated = set_octane_blocking_reason(octane_session, octane_id)
            phase_ok = set_octane_phase(octane_session, octane_id)
            result["blocking_reason_set"] = updated
            result["phase_set"] = phase_ok
            result["success"] = updated or phase_ok
        elif action == "backend":
            provider_index = data.get("provider_index")
            if provider_index is None or not (0 <= provider_index < len(BACKEND_PROVIDERS)):
                return flask_jsonify({"error": "Invalid provider index"}), 400
            backend_result = update_octane_backend(octane_session, octane_id, provider_index)
            result["success"] = backend_result["success"]
            result["provider"] = BACKEND_PROVIDERS[provider_index]["name"]
            result["phase_set"] = backend_result["phase_set"]
            if backend_result.get("phase_warning"):
                result["phase_warning"] = backend_result["phase_warning"]
        elif action == "missing_traces":
            updated = set_octane_additional_info_needed(octane_session, octane_id)
            phase_ok = set_octane_phase(octane_session, octane_id)
            result["blocking_reason_set"] = updated
            result["phase_set"] = phase_ok
            result["success"] = updated or phase_ok
        elif action == "cannot_reproduce":
            phase_ok = set_octane_phase(octane_session, octane_id,
                                        extra_fields={BLOCKING_REASON_FIELD: BLOCKING_REASON_NOT_REPRODUCIBLE})
            result["success"] = phase_ok
            result["phase_set"] = phase_ok
        elif action == "duplicate":
            master_octane_id = data.get("master_octane_id", "")
            if not master_octane_id:
                return flask_jsonify({"error": "Missing master OCTANE ID"}), 400
            updated = set_octane_child_duplicate(octane_session, octane_id, master_octane_id)
            phase_ok = set_octane_phase(octane_session, octane_id)
            result["duplicate_set"] = updated
            result["phase_set"] = phase_ok
            result["success"] = updated or phase_ok
        elif action == "done":
            versions = data.get("versions", [])
            issue_key = data.get("issue_key", "")
            target_i_step = data.get("target_i_step", "").strip()
            target_week = data.get("target_week", "").strip()
            move_to_pre_verification = data.get("move_to_pre_verification", False)
            close_jira_ticket = data.get("close_jira_ticket", False)
            change_solution_responsible = data.get("change_solution_responsible", False)
            octane_closed_in_version = data.get("octane_closed_in_version", "").strip()
            done_mode = data.get("done_mode", "jira")
            if not versions and not target_i_step and not target_week and not move_to_pre_verification \
                    and not close_jira_ticket and not change_solution_responsible and not octane_closed_in_version:
                return flask_jsonify({"error": "No fields to update"}), 400

            # ── Jira: set Integrated in Version(s) ──
            if versions and issue_key:
                formatted = [f"navigation-app/{v}" for v in versions]
                url = f"{JIRA_URL}/rest/api/2/issue/{issue_key}"
                payload = {"fields": {"customfield_10812": formatted}}
                try:
                    r = jira_session.put(url, json=payload, timeout=30)
                    result["jira_success"] = r.ok
                    if not r.ok:
                        result["jira_error"] = r.text[:300]
                except requests.RequestException as e:
                    result["jira_success"] = False
                    result["jira_error"] = str(e)
                result["versions_set"] = formatted
            else:
                result["jira_success"] = True  # nothing to do

            # ── Jira: close ticket ──
            if close_jira_ticket and issue_key:
                try:
                    trans_r = jira_session.get(f"{JIRA_URL}/rest/api/2/issue/{issue_key}/transitions", timeout=30)
                    transition_id = None
                    if trans_r.ok:
                        for t in trans_r.json().get("transitions", []):
                            if t.get("to", {}).get("name", "").lower() == "closed":
                                transition_id = t["id"]
                                break
                    if transition_id:
                        close_r = jira_session.post(
                            f"{JIRA_URL}/rest/api/2/issue/{issue_key}/transitions",
                            json={"transition": {"id": transition_id}}, timeout=30)
                        result["jira_closed"] = close_r.ok
                        if not close_r.ok:
                            result["jira_close_error"] = close_r.text[:200]
                    else:
                        result["jira_closed"] = False
                        result["jira_close_error"] = "Could not find 'Closed' transition in Jira"
                except requests.RequestException as e:
                    result["jira_closed"] = False
                    result["jira_close_error"] = str(e)

            # ── Jira: change solution responsible to BMW_Octane ──
            if change_solution_responsible and issue_key:
                try:
                    # Discover the Solution Responsible field ID from issue metadata
                    meta_r = jira_session.get(
                        f"{JIRA_URL}/rest/api/2/issue/{issue_key}/editmeta", timeout=30)
                    sol_resp_field = None
                    if meta_r.ok:
                        for fid, fdef in meta_r.json().get("fields", {}).items():
                            label = (fdef.get("name") or "").lower()
                            if "solution" in label and "responsible" in label:
                                sol_resp_field = fid
                                break
                    if sol_resp_field:
                        upd_r = jira_session.put(
                            f"{JIRA_URL}/rest/api/2/issue/{issue_key}",
                            json={"fields": {sol_resp_field: "BMW_Octane"}}, timeout=30)
                        result["solution_responsible_set"] = upd_r.ok
                        if not upd_r.ok:
                            result["solution_responsible_error"] = upd_r.text[:200]
                    else:
                        result["solution_responsible_set"] = False
                        result["solution_responsible_error"] = "Could not find 'Solution Responsible' field in Jira"
                except requests.RequestException as e:
                    result["solution_responsible_set"] = False
                    result["solution_responsible_error"] = str(e)

            # ── OCTANE: set closed_in_ver_udf directly (without phase transition) ──
            if octane_closed_in_version:
                civ_val = f"navigation-app/{octane_closed_in_version}"
                try:
                    r = octane_session.put(f"{OCTANE_BASE}/defects/{octane_id}",
                                           json={"closed_in_ver_udf": civ_val}, timeout=30)
                    if r.ok:
                        result["octane_closed_in_version_set"] = civ_val
                    else:
                        result["octane_closed_in_version_error"] = r.text[:200]
                except requests.RequestException as e:
                    result["octane_closed_in_version_error"] = str(e)

            # ── OCTANE: set Target I-Step and/or Target Week ──
            octane_payload = {}
            octane_fields_display = {}
            if target_i_step:
                node = _resolve_list_node_field(octane_session, "target_i_step_udf", target_i_step, octane_id)
                if node:
                    octane_payload["target_i_step_udf"] = node
                    octane_fields_display["target_i_step_udf"] = target_i_step
                else:
                    result["octane_success"] = False
                    result["octane_error"] = f"Could not resolve Target I-Step value '{target_i_step}' in OCTANE list nodes"
            if target_week:
                node = _resolve_list_node_field(octane_session, "target_week_udf", target_week, octane_id)
                if node:
                    octane_payload["target_week_udf"] = node
                    octane_fields_display["target_week_udf"] = target_week
                else:
                    octane_payload["target_week_udf"] = target_week
                    octane_fields_display["target_week_udf"] = target_week
            if octane_payload and not result.get("octane_error"):
                import pprint
                print(f"  [done] OCTANE PUT payload: {pprint.pformat(octane_payload)}")
                try:
                    r = octane_session.put(f"{OCTANE_BASE}/defects/{octane_id}", json=octane_payload, timeout=30)
                    print(f"  [done] OCTANE PUT → {r.status_code}")
                    if not r.ok:
                        print(f"  [done] OCTANE PUT error: {r.text[:300]}")
                        octane_payload["id"] = octane_id
                        r = octane_session.put(f"{OCTANE_BASE}/defects/{octane_id}", json=octane_payload, timeout=30)
                        print(f"  [done] OCTANE PUT retry → {r.status_code}")
                    result["octane_success"] = r.ok
                    if not r.ok:
                        result["octane_error"] = r.text[:300]
                except requests.RequestException as e:
                    result["octane_success"] = False
                    result["octane_error"] = str(e)
                result["octane_fields_set"] = octane_fields_display
            elif not result.get("octane_error"):
                result["octane_success"] = True

            # ── OCTANE: move phase 04 → 05 → 07 if requested ──
            if move_to_pre_verification:
                closed_in_version = data.get("closed_in_version", "").strip()
                phase_ok = set_octane_phase(octane_session, octane_id, "05-In Testing")
                if phase_ok:
                    phase7_extra = {}
                    if closed_in_version:
                        phase7_extra["closed_in_ver_udf"] = f"navigation-app/{closed_in_version}"
                    phase_ok = set_octane_phase(octane_session, octane_id, "07-In Pre-Verification", extra_fields=phase7_extra if phase7_extra else None)
                result["phase_set"] = phase_ok
                if phase_ok and closed_in_version:
                    result["closed_in_version_set"] = f"navigation-app/{closed_in_version}"
                if not phase_ok:
                    result["phase_error"] = "Failed to transition to 07-In Pre-Verification"

            result["success"] = (result.get("jira_success", True)
                                 and result.get("octane_success", True)
                                 and result.get("phase_set", True)
                                 and result.get("jira_closed", True) if close_jira_ticket else True
                                 and result.get("solution_responsible_set", True) if change_solution_responsible else True)
        else:
            return flask_jsonify({"error": f"Unknown action: {action}"}), 400

        result["url"] = f"{OCTANE_URL}/ui/entity-navigation?p={SHARED_SPACE}/{WORKSPACE}&entityType=work_item&id={octane_id}"
        return flask_jsonify(result)

    # Hardcoded Navigation App versions (App Cockpit extraction reserved for future use)
    _cached_versions = {
        "versions_20x": ["2.20.6", "2.20.4", "2.20.3", "2.20.2", "2.20.0"],
        "versions_19x": ["2.19.5", "2.19.4", "2.19.3", "2.19.2", "2.19.1"],
    }

    @app.route("/api/versions", methods=["GET"])
    def api_versions():
        """Return Navigation App versions (hardcoded; swap body for App Cockpit extraction when needed)."""
        return flask_jsonify(_cached_versions)

    # Cache for discovered OCTANE UDF field names: {label_lower: api_field_name}
    _octane_udf_cache: Dict[str, str] = {}

    @app.route("/api/done_octane_fields", methods=["GET"])
    def api_done_octane_fields():
        """Fetch current values of Done-relevant fields for an OCTANE defect."""
        octane_id = flask_request.args.get("octane_id", "").strip()
        octane_token = flask_request.args.get("octane_token", "").strip() or None
        if not octane_id or not octane_id.isdigit():
            return flask_jsonify({"error": "Invalid OCTANE ID"}), 400
        _, octane_session, err = _get_sessions(octane_token)
        if err:
            return flask_jsonify({"error": err}), 401

        def _extract(val):
            if val is None: return ""
            if isinstance(val, bool): return ""
            if isinstance(val, (int, float)): return ""  # numeric fields are not display values
            if isinstance(val, dict): return val.get("name", "") or val.get("label", "") or ""
            if isinstance(val, list): return ", ".join(_extract(x) for x in val if x)
            return str(val)

        # Step 1: Fetch the known-safe core fields only
        try:
            r = octane_session.get(
                f"{OCTANE_BASE}/defects/{octane_id}",
                params={"fields": "id,closed_in_ver_udf,target_i_step_udf,target_week_udf"},
                timeout=30)
            if not r.ok:
                return flask_jsonify({"error": f"OCTANE error {r.status_code}: {r.text[:200]}"}), 502
        except Exception as e:
            return flask_jsonify({"error": str(e)}), 502
        d = r.json()

        # Step 2: Discover info UDF field names via metadata (once, cached)
        if not _octane_udf_cache:
            try:
                meta_r = octane_session.get(
                    f"{OCTANE_BASE}/metadata/fields",
                    params={"entity_name": "defect"},
                    timeout=30)
                if meta_r.ok:
                    for fdef in meta_r.json().get("data", []):
                        label = (fdef.get("label") or "").lower()
                        name = fdef.get("name", "")
                        if name:
                            _octane_udf_cache[label] = name
            except Exception:
                pass

        # Find fields by label — use phrase matching to avoid wrong field hits
        sop_field = ""
        shift_field = ""
        for label, name in _octane_udf_cache.items():
            if not sop_field and ("first use" in label or "sop of function" in label or "first use/sop" in label):
                sop_field = name
            if not shift_field and "shift to set" in label:
                shift_field = name

        # Step 3: Fetch info fields in a separate call — failure is non-fatal
        first_use_sop = ""
        shift_to_set = ""
        info_fields_to_fetch = [f for f in [sop_field, shift_field] if f]
        if info_fields_to_fetch:
            try:
                r2 = octane_session.get(
                    f"{OCTANE_BASE}/defects/{octane_id}",
                    params={"fields": "id," + ",".join(info_fields_to_fetch)},
                    timeout=30)
                if r2.ok:
                    d2 = r2.json()
                    if sop_field:
                        first_use_sop = _extract(d2.get(sop_field))
                    if shift_field:
                        shift_to_set = _extract(d2.get(shift_field))
            except Exception:
                pass  # info fields are optional

        return flask_jsonify({
            "closed_in_ver_udf": _extract(d.get("closed_in_ver_udf")),
            "target_i_step_udf": _extract(d.get("target_i_step_udf")),
            "target_week_udf": _extract(d.get("target_week_udf")),
            "first_use_sop": first_use_sop,
            "shift_to_set": shift_to_set,
        })

    HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OCTANE DM Tool</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #ffffff; color: #1e293b; min-height: 100vh; padding: 16px; font-size: 13px;
        }
        .container { max-width: 720px; margin: 0 auto; }
        .header-bar {
            background: linear-gradient(135deg, #0ea5e9, #06b6d4, #14b8a6);
            border-radius: 14px; padding: 16px; margin-bottom: 18px; text-align: center;
        }
        h1 { color: #fff; font-size: 1.2rem; font-weight: 700; letter-spacing: -0.3px; }
        .input-section { display: flex; gap: 8px; margin-bottom: 12px; }
        input[type="text"] {
            flex: 1; padding: 9px 12px; border: 1.5px solid #e2e8f0; border-radius: 8px;
            background: #f8fafc; color: #1e293b; font-size: 0.82rem; outline: none;
            transition: border-color 0.2s, background 0.2s;
        }
        input[type="text"]:focus { border-color: #0ea5e9; background: #fff; }
        input[type="text"]::placeholder { color: #94a3b8; }
        button {
            padding: 9px 18px; border: none; border-radius: 8px; background: #0ea5e9;
            color: #fff; font-size: 0.82rem; font-weight: 600; cursor: pointer; transition: all 0.2s;
        }
        button:hover { background: #0284c7; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(14,165,233,0.25); }
        button:active { transform: translateY(0); }
        button:disabled { background: #cbd5e1; cursor: not-allowed; transform: none; box-shadow: none; }
        .card {
            background: #fff; border-radius: 10px; padding: 16px; margin-bottom: 12px;
            border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.03), 0 2px 8px rgba(0,0,0,0.02);
        }
        .card h2 { color: #0f172a; margin-bottom: 10px; font-size: 0.92rem; font-weight: 700; }
        .info-grid { display: grid; grid-template-columns: 90px 1fr; gap: 5px 10px; }
        .info-label { color: #64748b; font-size: 0.78rem; font-weight: 600; }
        .info-value { color: #1e293b; font-size: 0.82rem; font-weight: 500; }
        .info-value a { color: #0ea5e9; text-decoration: none; font-weight: 600; }
        .info-value a:hover { color: #0284c7; text-decoration: underline; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 0.65rem; font-weight: 700; }
        .badge-backend { background: #cffafe; color: #0891b2; }
        .badge-expected { background: #d1fae5; color: #059669; }
        .badge-missing { background: #ffedd5; color: #ea580c; }
        .badge-cannot-reproduce { background: #f3e8ff; color: #9333ea; }
        .badge-duplicate { background: #fef3c7; color: #d97706; }
        .badge-done { background: #d1fae5; color: #047857; }
        .comment-box {
            background: #f1f5f9; border-left: 3px solid #0ea5e9;
            padding: 10px 12px; margin: 10px 0; border-radius: 0 8px 8px 0;
            white-space: pre-wrap; font-family: 'SF Mono', 'JetBrains Mono', monospace;
            font-size: 0.75rem; line-height: 1.6; color: #334155;
        }
        .comment-meta { color: #94a3b8; font-size: 0.7rem; margin-bottom: 4px; }
        .provider-list { list-style: none; margin: 8px 0; }
        .provider-item {
            display: flex; align-items: center; padding: 9px 12px; margin: 5px 0;
            background: #f8fafc; border-radius: 8px; cursor: pointer;
            border: 1.5px solid #e2e8f0; transition: all 0.15s;
        }
        .provider-item:hover { border-color: #0ea5e9; background: #f0f9ff; }
        .provider-item.selected { border-color: #0ea5e9; background: #ecfeff; box-shadow: 0 0 0 3px rgba(14,165,233,0.08); }
        .provider-item .number {
            width: 26px; height: 26px; display: flex; align-items: center; justify-content: center;
            background: linear-gradient(135deg, #0ea5e9, #06b6d4);
            color: #fff; border-radius: 6px; font-weight: 700; font-size: 0.72rem;
            margin-right: 10px; flex-shrink: 0;
        }
        .provider-item.selected .number { background: linear-gradient(135deg, #059669, #10b981); }
        .provider-info { flex: 1; }
        .provider-name { font-weight: 600; color: #1e293b; font-size: 0.8rem; }
        .provider-detail { font-size: 0.7rem; color: #64748b; margin-top: 1px; }
        .provider-item .recommended {
            font-size: 0.6rem; background: linear-gradient(135deg, #059669, #10b981); color: #fff;
            padding: 2px 7px; border-radius: 5px; font-weight: 700; margin-left: 6px;
        }
        .action-bar { display: flex; gap: 8px; margin-top: 14px; padding-top: 14px; border-top: 1px solid #e2e8f0; }
        .btn-confirm {
            background: linear-gradient(135deg, #059669, #10b981);
            color: #fff; padding: 8px 22px; font-size: 0.82rem;
        }
        .btn-confirm:hover { background: linear-gradient(135deg, #047857, #059669); box-shadow: 0 4px 12px rgba(5,150,105,0.25); }
        .btn-cancel {
            background: #fff; border: 1.5px solid #f43f5e;
            color: #f43f5e; padding: 8px 22px; font-size: 0.82rem; font-weight: 600;
        }
        .btn-cancel:hover { background: #f43f5e; color: #fff; }
        .result-card { border: 1.5px solid #10b981; }
        .result-card.error { border: 1.5px solid #f43f5e; }
        .spinner {
            display: inline-block; width: 14px; height: 14px;
            border: 2px solid #0ea5e9; border-top-color: transparent;
            border-radius: 50%; animation: spin 0.7s linear infinite;
            margin-right: 8px; vertical-align: middle;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .loading-text { color: #0ea5e9; font-size: 0.82rem; font-weight: 600; }
        .hidden { display: none; }
        .excerpt-box {
            background: #f0fdfa; border-left: 3px solid #14b8a6;
            padding: 8px 12px; margin: 8px 0; border-radius: 0 8px 8px 0;
            white-space: pre-wrap; font-size: 0.78rem; color: #334155;
        }
        .will-set {
            background: #ecfdf5; border: 1px solid #a7f3d0;
            border-radius: 8px; padding: 10px 14px; margin-top: 10px;
        }
        .will-set h4 { color: #059669; margin-bottom: 4px; font-size: 0.75rem; font-weight: 700; }
        .will-set p { font-size: 0.78rem; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header-bar"><h1>OCTANE DM Tool</h1></div>
        <div class="input-section">
            <input type="text" id="octaneToken" placeholder="OCTANE Token (optional)">
        </div>
        <div class="input-section">
            <input type="text" id="octaneId" placeholder="OCTANE ID (e.g. 2706229) or Jira ID (e.g. IDCEVODEV-123456)"
                   onkeypress="if(event.key==='Enter') processTicket()">
            <button onclick="processTicket()" id="processBtn">Process</button>
        </div>
        <div id="loading" class="hidden" style="padding:12px 0">
            <span class="spinner"></span><span class="loading-text">Connecting...</span>
        </div>
        <div id="results"></div>
    </div>
    <script>
        let currentData = null, selectedProvider = null, octaneClosedInVerFetched = '';
        async function processTicket() {
            const inputVal = document.getElementById('octaneId').value.trim();
            if (!inputVal) return;
            document.getElementById('loading').classList.remove('hidden');
            document.getElementById('results').innerHTML = '';
            document.getElementById('processBtn').disabled = true;
            const isJiraInput = /^(IDCEVODEV|HU22DM)-\d+/i.test(inputVal);
            try {
                const octaneToken = document.getElementById('octaneToken').value.trim();
                const body = isJiraInput
                    ? {jira_id: inputVal, octane_token: octaneToken}
                    : {octane_id: inputVal, octane_token: octaneToken};
                const resp = await fetch('/api/process', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
                const data = await resp.json();
                if (!resp.ok) { showError(data.error||'Unknown error'); return; }
                currentData = data; renderResult(data);
            } catch(e) { showError('Connection failed: '+e.message); }
            finally { document.getElementById('loading').classList.add('hidden'); document.getElementById('processBtn').disabled = false; }
        }
        function showError(msg) { document.getElementById('results').innerHTML = `<div class="card result-card error"><h2>Error</h2><p>${escHtml(msg)}</p></div>`; }
        function renderResult(data) {
            let html = renderTicketInfo(data);
            if (data.path==='none') html+=`<div class="card"><h2>No Action Needed</h2><p>${escHtml(data.message)}</p></div>`;
            else if (data.path==='missing_jira_id') html+=renderMissingJiraId(data);
            else if (data.path==='invalid_jira_id') html+=renderInvalidJiraId(data);
            else if (data.path==='rejected_unknown') html+=`<div class="card"><h2>Unclassified Rejection</h2><p>${escHtml(data.message)}</p></div>`;
            else if (data.path==='rejected_expected_behavior') html+=renderExpectedBehavior(data);
            else if (data.path==='rejected_backend') html+=renderBackend(data);
            else if (data.path==='rejected_missing_traces') html+=renderMissingTraces(data);
            else if (data.path==='cannot_reproduce') html+=renderCannotReproduce(data);
            else if (data.path==='duplicate') html+=renderDuplicate(data);
            else if (data.path==='done') html+=renderDone(data);
            document.getElementById('results').innerHTML = html;
            if (data.path==='rejected_backend' && data.classification.provider_index!==null) selectProvider(data.classification.provider_index);
            if (data.path==='done') loadVersions();
        }
        function renderMissingJiraId(data) {
            return `<div class="card"><h2>Octane Ticket ${escHtml(data.octane_id)} has no Jira ID set.</h2>
                <p style="margin-bottom:10px">'Ticket no. supplier' field is empty in OCTANE #${escHtml(data.octane_id)}.</p>
                <p style="font-size:0.78rem;color:#64748b">Enter the Jira ID to continue processing:</p>
                <div style="margin-top:8px"><input type="text" id="manualJiraId" placeholder="e.g. IDCEVODEV-1034622 or HU22DM-392311" style="width:280px"></div>
                <div class="action-bar"><button class="btn-confirm" onclick="reprocessWithJiraId()">Process</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div></div>`;
        }
        async function reprocessWithJiraId() {
            const jiraId = (document.getElementById('manualJiraId')||{}).value;
            if (!jiraId || !jiraId.trim()) { alert('Please enter a Jira ID.'); return; }
            document.getElementById('loading').classList.remove('hidden');
            document.getElementById('results').innerHTML = '';
            try {
                const octaneToken = document.getElementById('octaneToken').value.trim();
                const resp = await fetch('/api/process', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({octane_id:currentData.octane_id,octane_token:octaneToken,jira_id:jiraId.trim()})});
                const data = await resp.json();
                if (!resp.ok) { showError(data.error||'Unknown error'); return; }
                currentData = data; renderResult(data);
            } catch(e) { showError('Connection failed: '+e.message); }
            finally { document.getElementById('loading').classList.add('hidden'); }
        }
        function renderInvalidJiraId(data) {
            return `<div class="card"><h2>Octane Ticket ${escHtml(data.octane_id)} has Invalid Jira ID</h2>
                <p style="margin-bottom:10px"><strong>${escHtml(data.invalid_jira_id)}</strong> is not a valid Navi Jira ID (expected HU22DM-* or IDCEVODEV-*).</p>
                <p style="font-size:0.78rem;color:#64748b">If this is a duplicate, enter the master OCTANE ID below:</p>
                <div style="margin-top:8px"><input type="text" id="manualMasterId" placeholder="e.g. 2707578" style="width:200px"></div>
                <div class="action-bar"><button class="btn-confirm" onclick="executeDuplicate()">Set as Duplicate</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div></div>`;
        }
        function renderTicketInfo(data) {
            let badge='';
            if (data.path&&data.path.startsWith('rejected_backend')) badge='<span class="badge badge-backend">Backend</span>';
            else if (data.path&&data.path.startsWith('rejected_expected')) badge='<span class="badge badge-expected">Expected Behavior</span>';
            else if (data.path&&data.path.startsWith('rejected_missing')) badge='<span class="badge badge-missing">Missing Traces</span>';
            else if (data.path==='cannot_reproduce') badge='<span class="badge badge-cannot-reproduce">Cannot Reproduce</span>';
            else if (data.path==='duplicate') badge='<span class="badge badge-duplicate">Duplicate</span>';
            else if (data.path==='done') badge='<span class="badge badge-done">Done</span>';
            return `<div class="card"><h2>Ticket Info ${badge}</h2><div class="info-grid">
                <span class="info-label">OCTANE</span><span class="info-value"><a href="${escHtml(data.octane_url)}" target="_blank">#${escHtml(data.octane_id)}</a></span>
                <span class="info-label">Jira</span><span class="info-value"><a href="${escHtml(data.jira_url)}" target="_blank">${escHtml(data.issue_key)}</a></span>
            </div></div>`;
        }
        function renderExpectedBehavior(data) {
            const c=data.classification;
            return `<div class="card"><h2>Planned Action: Reject as Expected Behaviour</h2>
                <div class="comment-box"><div class="comment-meta">${escHtml(c.author)} &middot; ${escHtml(c.created)}</div>${escHtml(c.excerpt)}</div>
                <div class="will-set"><h4>Will set in OCTANE:</h4><p>Blocking reason &rarr; Expected behaviour<br>Phase &rarr; ${escHtml('""" + TARGET_PHASE + """')}</p></div>
                <div class="action-bar"><button class="btn-confirm" onclick="executeAction('expected_behavior')">Confirm</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div></div>`;
        }
        function renderBackend(data) {
            const c=data.classification;
            let ph='';
            for (const p of data.providers) {
                const isRec=p.index===c.provider_index;
                ph+=`<div class="provider-item ${isRec?'selected':''}" id="provider-${p.index}" onclick="selectProvider(${p.index})">
                    <div class="number">${p.index+1}</div><div class="provider-info"><div class="provider-name">${escHtml(p.name)}</div>
                    <div class="provider-detail">${p.owner?escHtml(p.owner):''} ${p.category?'&middot; '+escHtml(p.category):''}</div></div>
                    ${isRec?'<span class="recommended">Recommended</span>':''}</div>`;
            }
            const recName=c.provider_index!==null?data.providers[c.provider_index].name:'Unknown';
            return `<div class="card"><h2>Planned Action: Reject to Backend Provider &ldquo;${escHtml(recName)}&rdquo;</h2>
                <div class="comment-box"><div class="comment-meta">${escHtml(c.author)} &middot; ${escHtml(c.created)}</div>${escHtml(c.comment_text)}</div>
                <h3 style="color:#0891b2;margin:10px 0 6px;font-weight:700;font-size:0.82rem">Select Provider:</h3>
                <ul class="provider-list">${ph}</ul>
                <div class="action-bar"><button class="btn-confirm" onclick="executeBackend()">Confirm Provider</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div></div>`;
        }
        function selectProvider(index) {
            selectedProvider=index;
            document.querySelectorAll('.provider-item').forEach(el=>el.classList.remove('selected'));
            document.getElementById('provider-'+index).classList.add('selected');
        }
        function renderMissingTraces(data) {
            const c=data.classification;
            return `<div class="card"><h2>Planned Action: Reject — Missing Traces</h2>
                <div class="comment-box"><div class="comment-meta">${escHtml(c.author)} &middot; ${escHtml(c.created)}</div>${escHtml(c.excerpt)}</div>
                <div class="will-set"><h4>Will set in OCTANE:</h4><p>Blocking reason &rarr; Additional Information necessary<br>Phase &rarr; ${escHtml('""" + TARGET_PHASE + """')}</p></div>
                <div class="action-bar"><button class="btn-confirm" onclick="executeAction('missing_traces')">Confirm</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div></div>`;
        }
        function renderCannotReproduce(data) {
            return `<div class="card"><h2>Cannot Reproduce</h2>
                <div class="will-set"><h4>Will set in OCTANE:</h4><p>Blocking reason &rarr; Not reproducible<br>Phase &rarr; ${escHtml('""" + TARGET_PHASE + """')}</p></div>
                <div class="action-bar"><button class="btn-confirm" onclick="executeAction('cannot_reproduce')">Confirm</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div></div>`;
        }
        function renderDuplicate(data) {
            let commentHtml = '';
            if (data.duplicate_comment) {
                const c = data.duplicate_comment;
                commentHtml = `<div class="comment-box"><div class="comment-meta">${escHtml(c.author)} &middot; ${escHtml(c.created)}</div>${escHtml(c.text)}</div>`;
            }
            if (data.error) {
                return `<div class="card"><h2>Duplicate — Master Octane ID Not Found</h2>
                    <p style="margin-bottom:10px">${escHtml(data.error)}</p>
                    ${commentHtml}
                    <div style="margin-top:12px"><label style="font-size:0.78rem;font-weight:600;color:#64748b">Enter Master OCTANE ID manually:</label>
                    <input type="text" id="manualMasterId" placeholder="e.g. 2707578" style="margin-top:4px;width:200px"></div>
                    <div class="action-bar"><button class="btn-confirm" onclick="executeDuplicate()">Confirm</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div></div>`;
            }
            return `<div class="card"><h2>Duplicate</h2><div class="info-grid">
                <span class="info-label">Master ID</span><span class="info-value"><a href="${escHtml(data.master_url)}" target="_blank">#${escHtml(data.master_octane_id)}</a></span></div>
                ${commentHtml}
                <div class="will-set"><h4>Will set in OCTANE:</h4><p>Blocking reason &rarr; Child (Duplicate)<br>Relation to &rarr; ${escHtml(data.master_octane_id)}<br>Phase &rarr; ${escHtml('""" + TARGET_PHASE + """')}</p></div>
                <div class="action-bar"><button class="btn-confirm" onclick="executeDuplicate()">Confirm</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div></div>`;
        }
        function renderDone(data) {
            const isJira = currentData.input_mode === 'jira';
            let jiraInfoHtml = '';
            if (currentData.first_use_sop) jiraInfoHtml += `<div style="display:flex;gap:6px;align-items:baseline"><span style="font-size:0.72rem;font-weight:700;color:#64748b;white-space:nowrap">First use/SoP of function:</span><span style="font-size:0.8rem;color:#0f172a">${escHtml(currentData.first_use_sop)}</span></div>`;
            if (currentData.shift_to_set) jiraInfoHtml += `<div style="display:flex;gap:6px;align-items:baseline;margin-top:4px"><span style="font-size:0.72rem;font-weight:700;color:#64748b;white-space:nowrap">Shift to SET:</span><span style="font-size:0.8rem;color:#0f172a">${escHtml(currentData.shift_to_set)}</span></div>`;
            const jiraInfoBlock = jiraInfoHtml ? `<div style="margin-bottom:14px;padding:10px;border:1.5px solid #e2e8f0;border-radius:8px;background:#f0f9ff">${jiraInfoHtml}</div>` : '';
            return `<div class="card" id="done-card">
                <h2>Resolution: Done</h2>
                <p style="font-size:0.78rem;color:#64748b;margin-bottom:14px">OCTANE #${escHtml(currentData.octane_id)}${isJira?' &mdash; Jira: <strong>'+escHtml(currentData.issue_key)+'</strong>':''}</p>
                <div id="versions-loading" class="hidden"></div>
                <!-- JIRA MODE -->
                <div id="done-jira-section" class="${isJira?'':'hidden'}">
                    ${jiraInfoBlock}
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
                        <div>
                            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
                                <h3 style="color:#0891b2;font-size:0.82rem;font-weight:700">Navigation App 2.20.x</h3>
                                <button onclick="addNaviVersion('20')" style="padding:3px 9px;font-size:0.72rem;background:#e0f2fe;color:#0369a1;border:1px solid #7dd3fc;border-radius:6px;cursor:pointer">+ Add version</button>
                            </div>
                            <select id="version20" style="width:100%;padding:8px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.8rem;background:#f8fafc">
                                <option value="">\u2014 none \u2014</option>
                            </select>
                        </div>
                        <div>
                            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
                                <h3 style="color:#0891b2;font-size:0.82rem;font-weight:700">Navigation App 2.19.x</h3>
                                <button onclick="addNaviVersion('19')" style="padding:3px 9px;font-size:0.72rem;background:#e0f2fe;color:#0369a1;border:1px solid #7dd3fc;border-radius:6px;cursor:pointer">+ Add version</button>
                            </div>
                            <select id="version19" style="width:100%;padding:8px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.8rem;background:#f8fafc">
                                <option value="">\u2014 none \u2014</option>
                            </select>
                        </div>
                    </div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">
                        <div><label style="font-size:0.78rem;font-weight:600;color:#64748b;display:block;margin-bottom:4px">Target I-Step</label>
                            <input type="text" id="targetIStepJira" placeholder="e.g. NA25" style="width:100%;padding:8px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.8rem;background:#f8fafc">
                        </div>
                        <div><label style="font-size:0.78rem;font-weight:600;color:#64748b;display:block;margin-bottom:4px">Target Week</label>
                            <input type="text" id="targetWeekJira" placeholder="e.g. KW26" style="width:100%;padding:8px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.8rem;background:#f8fafc">
                        </div>
                    </div>
                    <div style="margin-top:14px;display:flex;flex-direction:column;gap:10px">
                        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.82rem;font-weight:600;color:#0f172a">
                            <input type="checkbox" id="closeJiraTicket" style="width:16px;height:16px;accent-color:#0891b2">
                            Close Jira Ticket (move status to Closed)
                        </label>
                        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.82rem;font-weight:600;color:#0f172a">
                            <input type="checkbox" id="changeSolutionResponsible" style="width:16px;height:16px;accent-color:#0891b2">
                            Change solution responsible to Octane (set &ldquo;BMW_Octane&rdquo;)
                        </label>
                    </div>
                    <div class="will-set" style="margin-top:12px"><h4>Will set:</h4>
                        <p>Jira: Integrated in Version(s) &rarr; <span id="versions-preview">\u2014</span><br>OCTANE: Target I-Step &amp; Target Week</p>
                    </div>
                    <div class="action-bar"><button class="btn-confirm" onclick="executeDoneJira()">Confirm</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div>
                </div>
                <!-- OCTANE MODE -->
                <div id="done-octane-section" class="${isJira?'hidden':''}">
                    <div id="octane-fields-loading" class="hidden"><span class="spinner"></span><span class="loading-text">Loading OCTANE fields\u2026</span></div>
                    <div id="octane-fields-content" class="hidden">
                        <div id="octane-closed-row" class="hidden" style="margin-bottom:12px;padding:10px;border:1.5px solid #fde68a;border-radius:8px;background:#fffbeb">
                            <p style="margin:0 0 6px;font-size:0.78rem;font-weight:600;color:#92400e">\u26a0 Closed in version is empty</p>
                            <label style="font-size:0.78rem;font-weight:600;color:#64748b;display:block;margin-bottom:4px">Set Closed in version</label>
                            <select id="octaneClosedInVersion" style="width:100%;max-width:300px;padding:8px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.8rem;background:#f8fafc">
                                <option value="">\u2014 select version \u2014</option>
                            </select>
                        </div>
                        <div id="octane-istep-row" class="hidden" style="margin-bottom:12px;padding:10px;border:1.5px solid #fde68a;border-radius:8px;background:#fffbeb">
                            <p style="margin:0 0 6px;font-size:0.78rem;font-weight:600;color:#92400e">\u26a0 Target I-Step is empty</p>
                            <label style="font-size:0.78rem;font-weight:600;color:#64748b;display:block;margin-bottom:4px">Set Target I-Step</label>
                            <input type="text" id="targetIStep" placeholder="e.g. NA25" style="width:200px;padding:8px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.8rem;background:#f8fafc">
                        </div>
                        <div id="octane-week-row" class="hidden" style="margin-bottom:12px;padding:10px;border:1.5px solid #fde68a;border-radius:8px;background:#fffbeb">
                            <p style="margin:0 0 6px;font-size:0.78rem;font-weight:600;color:#92400e">\u26a0 Target Week is empty</p>
                            <label style="font-size:0.78rem;font-weight:600;color:#64748b;display:block;margin-bottom:4px">Set Target Week</label>
                            <input type="text" id="targetWeek" placeholder="e.g. KW26" style="width:200px;padding:8px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.8rem;background:#f8fafc">
                        </div>
                        <div style="margin-top:8px;padding:10px;border:1.5px solid #e2e8f0;border-radius:8px;background:#f0fdf4">
                            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.82rem;font-weight:600;color:#166534">
                                <input type="checkbox" id="moveToPreVerification" style="width:16px;height:16px;accent-color:#16a34a" onchange="toggleClosedInVersion()">
                                Move OCTANE ticket to 07-In Pre-Verification
                            </label>
                            <p style="margin:4px 0 0 24px;font-size:0.72rem;color:#64748b">Will transition: 04-In Progress \u2192 05-In Testing \u2192 07-In Pre-Verification</p>
                            <div id="closedInVersionRow" class="hidden" style="margin-top:10px;padding-left:24px">
                                <label style="font-size:0.78rem;font-weight:600;color:#64748b;display:block;margin-bottom:4px">Closed in version (required for phase 7)</label>
                                <select id="closedInVersion" style="width:100%;max-width:300px;padding:8px;border:1.5px solid #e2e8f0;border-radius:8px;font-size:0.8rem;background:#f8fafc">
                                    <option value="">\u2014 select version \u2014</option>
                                </select>
                            </div>
                        </div>
                        <div class="action-bar"><button class="btn-confirm" onclick="executeDoneOctane()">Confirm</button><button class="btn-cancel" onclick="cancel()">Cancel</button></div>
                    </div>
                </div>
            </div>`;
        }
        function loadVersions() {
            const VERS_20 = ['2.20.6','2.20.4','2.20.3','2.20.2','2.20.0'];
            const VERS_19 = ['2.19.5','2.19.4','2.19.3','2.19.2','2.19.1'];
            const fill = (selId, vers) => {
                const s = document.getElementById(selId);
                if (!s) return;
                vers.forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); });
            };
            fill('version20', VERS_20);
            fill('version19', VERS_19);
            const allVers = [...VERS_20, ...VERS_19];
            fill('closedInVersion', allVers);
            fill('octaneClosedInVersion', allVers);
            document.getElementById('version20').addEventListener('change', updateVersionPreview);
            document.getElementById('version19').addEventListener('change', updateVersionPreview);
            initDoneMode();
        }
        function initDoneMode() {
            // Jira section is pre-shown/hidden by renderDone; for OCTANE mode auto-load fields
            const octaneSection = document.getElementById('done-octane-section');
            if (octaneSection && !octaneSection.classList.contains('hidden')) {
                loadOctaneFields();
            }
        }
        async function loadOctaneFields() {
            const octaneId = currentData.octane_id;
            const token = document.getElementById('octaneToken').value.trim();
            const loading = document.getElementById('octane-fields-loading');
            const content = document.getElementById('octane-fields-content');
            loading.classList.remove('hidden');
            content.classList.add('hidden');
            try {
                const resp = await fetch(`/api/done_octane_fields?octane_id=${encodeURIComponent(octaneId)}&octane_token=${encodeURIComponent(token)}`);
                const d = await resp.json();
                if (!resp.ok) {
                    loading.innerHTML=`<p style="color:#f43f5e">${escHtml(d.error||'Failed to load OCTANE fields')}</p>`;
                    loading.classList.remove('hidden');
                    return;
                }
                loading.classList.add('hidden');
                octaneClosedInVerFetched = d.closed_in_ver_udf || '';
                if (!d.closed_in_ver_udf) document.getElementById('octane-closed-row').classList.remove('hidden');
                if (!d.target_i_step_udf) document.getElementById('octane-istep-row').classList.remove('hidden');
                if (!d.target_week_udf) document.getElementById('octane-week-row').classList.remove('hidden');
                // Show OCTANE info fields (read-only)
                let octaneInfoHtml = '';
                if (d.first_use_sop) octaneInfoHtml += `<div style="display:flex;gap:6px;align-items:baseline"><span style="font-size:0.72rem;font-weight:700;color:#64748b;white-space:nowrap">First use/SoP of function:</span><span style="font-size:0.8rem;color:#0f172a">${escHtml(d.first_use_sop)}</span></div>`;
                if (d.shift_to_set) octaneInfoHtml += `<div style="display:flex;gap:6px;align-items:baseline;margin-top:4px"><span style="font-size:0.72rem;font-weight:700;color:#64748b;white-space:nowrap">Shift to SET:</span><span style="font-size:0.8rem;color:#0f172a">${escHtml(d.shift_to_set)}</span></div>`;
                if (octaneInfoHtml) {
                    const infoDiv = document.createElement('div');
                    infoDiv.style.cssText = 'margin-bottom:14px;padding:10px;border:1.5px solid #e2e8f0;border-radius:8px;background:#f0f9ff';
                    infoDiv.innerHTML = octaneInfoHtml;
                    content.insertBefore(infoDiv, content.firstChild);
                }
                content.classList.remove('hidden');
            } catch(e) {
                loading.innerHTML=`<p style="color:#f43f5e">Failed: ${escHtml(e.message)}</p>`;
                loading.classList.remove('hidden');
            }
        }
        function toggleClosedInVersion() {
            const row = document.getElementById('closedInVersionRow');
            // Only show if checkbox is checked AND closed_in_ver is not already filled
            if (document.getElementById('moveToPreVerification').checked && !octaneClosedInVerFetched) {
                row.classList.remove('hidden');
            } else {
                row.classList.add('hidden');
            }
        }
        function updateVersionPreview() {
            const v20 = document.getElementById('version20').value;
            const v19 = document.getElementById('version19').value;
            const parts = [];
            if (v20) parts.push('navigation-app/' + v20);
            if (v19) parts.push('navigation-app/' + v19);
            document.getElementById('versions-preview').textContent = parts.length ? parts.join(', ') : '\u2014';
        }
        function addNaviVersion(series) {
            const prefix = series === '20' ? '2.20.' : '2.19.';
            const selId  = series === '20' ? 'version20' : 'version19';
            const input  = prompt('Enter new version (e.g. ' + prefix + (series === '20' ? '7' : '6') + '):');
            if (!input || !input.trim()) return;
            const ver = input.trim();
            if (!ver.startsWith(prefix)) { alert('Version must start with ' + prefix); return; }
            // Insert into target select + shared dropdowns, avoiding duplicates
            for (const cid of [selId, 'closedInVersion', 'octaneClosedInVersion']) {
                const sel = document.getElementById(cid);
                if (!sel) continue;
                let dup = false;
                for (const o of sel.options) { if (o.value === ver) { dup = true; break; } }
                if (dup) { if (cid === selId) sel.value = ver; continue; }
                const opt = document.createElement('option');
                opt.value = ver; opt.textContent = ver;
                // Insert in descending version order (after the blank "— none —" option at index 0)
                let inserted = false;
                for (let i = 1; i < sel.options.length; i++) {
                    if (sel.options[i].value < ver) { sel.insertBefore(opt, sel.options[i]); inserted = true; break; }
                }
                if (!inserted) sel.appendChild(opt);
                if (cid === selId) sel.value = ver;
            }
            updateVersionPreview();
        }
        async function executeDoneJira() {
            const issueKey = currentData.issue_key || '';
            const v20 = document.getElementById('version20').value;
            const v19 = document.getElementById('version19').value;
            const versions = []; if (v20) versions.push(v20); if (v19) versions.push(v19);
            const targetIStep = document.getElementById('targetIStepJira').value.trim();
            const targetWeek = document.getElementById('targetWeekJira').value.trim();
            const closeJira = document.getElementById('closeJiraTicket').checked;
            const changeSolResp = document.getElementById('changeSolutionResponsible').checked;
            if (!versions.length && !targetIStep && !targetWeek && !closeJira && !changeSolResp) { alert('Please fill at least one field or select an action.'); return; }
            await doExecute({octane_id:currentData.octane_id, action:'done', issue_key:issueKey, versions:versions, target_i_step:targetIStep, target_week:targetWeek, close_jira_ticket:closeJira, change_solution_responsible:changeSolResp, done_mode:'jira'});
        }
        async function executeDoneOctane() {
            const octaneId = currentData.octane_id || '';
            const closedInVerDirect = (document.getElementById('octaneClosedInVersion')||{value:''}).value;
            const targetIStep = (document.getElementById('targetIStep')||{value:''}).value.trim();
            const targetWeek = (document.getElementById('targetWeek')||{value:''}).value.trim();
            const moveToPreVerification = document.getElementById('moveToPreVerification').checked;
            // Use dropdown value if shown, otherwise fall back to already-fetched value
            const closedInVersion = document.getElementById('closedInVersion').value || octaneClosedInVerFetched;
            if (moveToPreVerification && !closedInVersion) { alert('Please select a Closed in version for phase 7 transition.'); return; }
            if (!closedInVerDirect && !targetIStep && !targetWeek && !moveToPreVerification) { alert('Please fill at least one field or select phase transition.'); return; }
            await doExecute({octane_id:octaneId, action:'done', issue_key:currentData.issue_key, versions:[], target_i_step:targetIStep, target_week:targetWeek, move_to_pre_verification:moveToPreVerification, closed_in_version:closedInVersion, octane_closed_in_version:closedInVerDirect, done_mode:'octane'});
        }
        async function executeAction(action){await doExecute({octane_id:currentData.octane_id,action:action});}
        async function executeBackend(){
            if(selectedProvider===null){alert('Please select a provider.');return;}
            await doExecute({octane_id:currentData.octane_id,action:'backend',provider_index:selectedProvider});
        }
        async function executeDuplicate(){
            let masterId = currentData.master_octane_id;
            const manualInput = document.getElementById('manualMasterId');
            if (manualInput) { masterId = manualInput.value.trim(); }
            if (!masterId || !masterId.match(/^\d+$/)) { alert('Please enter a valid numeric Master OCTANE ID.'); return; }
            await doExecute({octane_id:currentData.octane_id,action:'duplicate',master_octane_id:masterId});
        }
        async function doExecute(payload){
            document.querySelectorAll('.btn-confirm,.btn-cancel').forEach(b=>b.disabled=true);
            payload.octane_token=document.getElementById('octaneToken').value.trim();
            try{const resp=await fetch('/api/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                const result=await resp.json();renderExecutionResult(result);}catch(e){showError('Execution failed: '+e.message);}
        }
        function renderExecutionResult(result){
            const success=result.success;let d='';
            if(result.blocking_reason_set!==undefined) d+=`<br>Blocking reason: ${result.blocking_reason_set?'Set':'Failed'}`;
            if(result.phase_set!==undefined) d+=`<br>Phase: ${result.phase_set?'Set':'Failed'}`;
            if(result.phase_warning) d+=`<br><span style="color:#d97706;font-weight:600">⚠ ${escHtml(result.phase_warning)}</span>`;
            if(result.provider) d+=`<br>Provider: ${escHtml(result.provider)}`;
            if(result.duplicate_set!==undefined) d+=`<br>Duplicate: ${result.duplicate_set?'Set':'Failed'}`;
            if(result.versions_set) d+=`<br>Jira — Integrated in Version(s): ${escHtml(result.versions_set.join(', '))}`;
            if(result.octane_fields_set) {
                const fields = result.octane_fields_set;
                if(fields.target_i_step_udf) d+=`<br>OCTANE — Target I-Step: ${escHtml(fields.target_i_step_udf)}`;
                if(fields.target_week_udf) d+=`<br>OCTANE — Target Week: ${escHtml(fields.target_week_udf)}`;
            }
            if(result.octane_closed_in_version_set) d+=`<br>OCTANE — Closed in version: ${escHtml(result.octane_closed_in_version_set)}`;
            if(result.closed_in_version_set) d+=`<br>OCTANE — Closed in version (phase 7): ${escHtml(result.closed_in_version_set)}`;
            if(result.jira_closed!==undefined) d+=`<br>Jira — Close: ${result.jira_closed?'<span style="color:#16a34a">Success</span>':'<span style="color:#f43f5e">Failed</span>'}`;
            if(result.jira_close_error) d+=`<br><span style="color:#f43f5e">Jira close: ${escHtml(result.jira_close_error)}</span>`;
            if(result.solution_responsible_set!==undefined) d+=`<br>Jira — Solution Responsible: ${result.solution_responsible_set?'<span style="color:#16a34a">Set to BMW_Octane</span>':'<span style="color:#f43f5e">Failed</span>'}`;
            if(result.solution_responsible_error) d+=`<br><span style="color:#f43f5e">Solution Responsible: ${escHtml(result.solution_responsible_error)}</span>`;
            if(result.jira_error) d+=`<br><span style="color:#f43f5e">Jira: ${escHtml(result.jira_error)}</span>`;
            if(result.octane_error) d+=`<br><span style="color:#f43f5e">OCTANE: ${escHtml(result.octane_error)}</span>`;
            if(result.octane_closed_in_version_error) d+=`<br><span style="color:#f43f5e">OCTANE closed-in-version: ${escHtml(result.octane_closed_in_version_error)}</span>`;
            if(result.error_detail) d+=`<br><span style="color:#f43f5e">${escHtml(result.error_detail)}</span>`;
            let links = '';
            if(result.action==='done') {
                if(currentData.jira_url) links+=`<a href="${escHtml(currentData.jira_url)}" target="_blank">Open in Jira &rarr;</a> `;
                if(result.url) links+=`<a href="${escHtml(result.url)}" target="_blank">Open in OCTANE &rarr;</a>`;
            } else if(result.url) {
                links=`<a href="${escHtml(result.url)}" target="_blank">Open in OCTANE &rarr;</a>`;
            }
            document.getElementById('results').innerHTML+=`<div class="card result-card ${success?'':'error'}">
                <h2>${success?'Success':'Failed'}</h2><p>OCTANE #${escHtml(result.octane_id)}${d}</p>
                ${links?`<p style="margin-top:8px">${links}</p>`:''}</div>`;
        }
        function cancel(){document.getElementById('results').innerHTML+=`<div class="card"><h2>Cancelled</h2><p>No changes were made.</p></div>`;}
        function escHtml(str){if(!str)return'';return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
    </script>
</body>
</html>
"""

    @app.route("/")
    def index():
        return render_template_string(HTML_PAGE)

    return app


# ══════════════════════════════════════════════════════════════════════════════
# CLI Mode
# ══════════════════════════════════════════════════════════════════════════════

def run_cli():
    """Run the interactive CLI mode for a single ticket."""
    parser = argparse.ArgumentParser(
        description="OCTANE DM Tool — Process navigation defect tickets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("octane_id", help="OCTANE defect ID (numeric), e.g. 2713179")
    parser.add_argument("--octane-token", default=os.environ.get(OCTANE_TOKEN_ENV), metavar="TOKEN",
                        help=f"OCTANE access_token cookie (or set ${OCTANE_TOKEN_ENV}, or ~/.netrc)")
    parser.add_argument("--jira-token", default=os.environ.get(JIRA_TOKEN_ENV), metavar="TOKEN",
                        help=f"Jira API token (or set ${JIRA_TOKEN_ENV}, or ~/.netrc)")
    parser.add_argument("--user", default=os.environ.get(JIRA_USER_ENV), metavar="EMAIL",
                        help=f"Jira username for basic auth (or set ${JIRA_USER_ENV})")
    parser.add_argument("--url", default=JIRA_URL, metavar="URL", help=f"Jira base URL (default: {JIRA_URL})")
    args = parser.parse_args()

    jira_url = args.url.rstrip("/")

    # Resolve OCTANE credentials
    octane_token = args.octane_token
    octane_user_cookie = ""
    if not octane_token:
        nrc_login, nrc_password = _read_netrc("octane-prod.bmwgroup.net")
        if nrc_password:
            octane_token = nrc_password
            octane_user_cookie = nrc_login or ""
    if not octane_token:
        print("No OCTANE token. Use --octane-token, set OCTANE_TOKEN, or add to ~/.netrc.")
        sys.exit(1)

    # Resolve Jira credentials
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

    # Step 0: Extract Jira ID from OCTANE
    print(f"[0] Connecting to OCTANE, extracting Jira ID …")
    octane_session = build_octane_session(octane_token, octane_user_cookie)

    try:
        r = octane_session.get(f"{OCTANE_BASE}/defects", params={"fields": "id", "limit": 1}, timeout=30)
        if r.status_code == 401:
            print("  OCTANE token expired or invalid (401).")
            sys.exit(1)
        if not r.ok:
            print(f"  OCTANE API error {r.status_code}: {r.text[:200]}")
            sys.exit(1)
    except requests.RequestException as e:
        print(f"  OCTANE connection error: {e}")
        sys.exit(1)
    print(f"  ✓ Connected to OCTANE")

    issue_key = extract_jira_id_from_octane(octane_session, args.octane_id)
    if not issue_key:
        print(f"  Could not extract Jira ID from OCTANE #{args.octane_id}")
        sys.exit(1)
    print(f"  Jira ID: {issue_key}")

    # Build Jira session
    session = build_jira_session(jira_token, jira_user)

    # Step 1: fetch issue
    print(f"\n[1] Fetching Jira issue {issue_key} …")
    issue = get_issue(session, jira_url, issue_key)
    if issue is None:
        sys.exit(1)

    fields = issue.get("fields", {})
    summary = fields.get("summary", "")
    status = (fields.get("status") or {}).get("name", "Unknown")
    resolution = fields.get("resolution")
    res_name = (resolution or {}).get("name", "Unresolved") if resolution else "Unresolved"
    res_date = (fields.get("resolutiondate") or "")[:10]
    priority = (fields.get("priority") or {}).get("name", "")
    assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")

    print(f"  Summary    : {summary}")
    print(f"  Status     : {status}")
    print(f"  Resolution : {res_name}" + (f"  ({res_date})" if res_date else ""))
    print(f"  Priority   : {priority}")
    print(f"  Assignee   : {assignee}")

    # Step 2: resolution routing
    res_lower = res_name.lower()
    is_rejected = res_lower in REJECTED_RESOLUTIONS
    is_duplicate = res_lower in DUPLICATE_RESOLUTIONS
    is_cannot_reproduce = res_lower in CANNOT_REPRODUCE_RESOLUTIONS

    print(f"\n[2] Checking resolution …")

    if not is_rejected and not is_duplicate and not is_cannot_reproduce:
        print(f"  Resolution is '{res_name}' — no action needed.")
        sys.exit(0)

    # PATH A: Rejected
    if is_rejected:
        print(f"  Ticket is REJECTED ('{res_name}')")
        print(f"\n[3] Fetching comments …")
        comments = get_comments(session, jira_url, issue_key)
        print(f"  {len(comments)} comment(s) found.")

        print(f"\n[4] Classifying rejection …")
        classification = classify_rejection(comments)

        if classification is None:
            print(f"  No pattern matched in any comment.")
            sys.exit(2)

        verdict = classification["verdict"]
        rel_time = _relative_time(classification.get("created_raw", ""))
        print(f"  Verdict: {verdict}  (from {classification['author']}, {rel_time})")

        if verdict == "expected_behavior":
            print(f"\n  PLANNED ACTION: Reject as Expected Behaviour")
            print(f"  Keyword: \"{classification['keyword']}\"")
            print(f"  Will set: Blocking reason → Expected behaviour, Phase → {TARGET_PHASE}")
            confirm = input("\n  Proceed? [Y/n]: ").strip().lower()
            if confirm and confirm != "y":
                print(f"  Cancelled.")
                sys.exit(2)
            updated = set_octane_blocking_reason(octane_session, args.octane_id)
            phase_ok = set_octane_phase(octane_session, args.octane_id)
            print(f"  Blocking reason: {'Set' if updated else 'Failed'}")
            print(f"  Phase: {'Set' if phase_ok else 'Failed'}")
            sys.exit(0)

        if verdict == "backend":
            provider_idx = classification.get("provider_index")
            print(f"\n  PLANNED ACTION: Route to Backend Provider")
            print(f"  Trigger: {classification['trigger']}")
            if provider_idx is not None:
                print(f"  Proposed: {BACKEND_PROVIDERS[provider_idx]['name']}")
            for i, p in enumerate(BACKEND_PROVIDERS):
                marker = " ◀" if i == provider_idx else ""
                print(f"  {i + 1}) {p['name']}{marker}")
            print(f"  0) Cancel")
            choice = input(f"\n  Select [{provider_idx + 1 if provider_idx is not None else '0-7'}]: ").strip()
            if not choice and provider_idx is not None:
                pass
            elif choice == "0":
                print(f"  Cancelled.")
                sys.exit(2)
            else:
                try:
                    selected = int(choice) - 1
                    if 0 <= selected < len(BACKEND_PROVIDERS):
                        provider_idx = selected
                    else:
                        print("  Invalid selection.")
                        sys.exit(2)
                except ValueError:
                    print("  Invalid input.")
                    sys.exit(2)
            confirm = input(f"\n  Confirm {BACKEND_PROVIDERS[provider_idx]['name']}? [Y/n]: ").strip().lower()
            if confirm and confirm != "y":
                print(f"  Cancelled.")
                sys.exit(2)
            backend_result = update_octane_backend(octane_session, args.octane_id, provider_idx)
            print(f"  OCTANE updated: {'Yes' if backend_result['success'] else 'No'}")
            if backend_result.get("phase_warning"):
                print(f"  ⚠ {backend_result['phase_warning']}")
            sys.exit(0 if backend_result["success"] else 1)

        if verdict == "missing_traces":
            print(f"\n  PLANNED ACTION: Reject — Missing Traces")
            print(f"  Will set: Blocking reason → {BLOCKING_REASON_ADDITIONAL_INFO_NAME}, Phase → {TARGET_PHASE}")
            confirm = input("\n  Proceed? [Y/n]: ").strip().lower()
            if confirm and confirm != "y":
                print(f"  Cancelled.")
                sys.exit(2)
            updated = set_octane_additional_info_needed(octane_session, args.octane_id)
            phase_ok = set_octane_phase(octane_session, args.octane_id)
            print(f"  Blocking reason: {'Set' if updated else 'Failed'}")
            print(f"  Phase: {'Set' if phase_ok else 'Failed'}")
            sys.exit(0)

    # PATH B: Cannot Reproduce
    if is_cannot_reproduce:
        print(f"  Ticket is CANNOT REPRODUCE ('{res_name}')")
        phase_ok = set_octane_phase(octane_session, args.octane_id,
                                    extra_fields={BLOCKING_REASON_FIELD: BLOCKING_REASON_NOT_REPRODUCIBLE})
        print(f"  Phase + Blocking reason: {'Set' if phase_ok else 'Failed'}")
        sys.exit(0)

    # PATH C: Duplicate
    if is_duplicate:
        print(f"  Ticket is DUPLICATE ('{res_name}')")
        print(f"\n[3] Scanning comments for master reference …")
        master_octane_id, _ = extract_master_duplicate_octane_id(session, jira_url, octane_session, issue_key)
        if not master_octane_id:
            print(f"  Could not determine master OCTANE ID.")
            sys.exit(3)
        print(f"\n  Master OCTANE ID: {master_octane_id}")
        updated = set_octane_child_duplicate(octane_session, args.octane_id, master_octane_id)
        phase_ok = set_octane_phase(octane_session, args.octane_id)
        print(f"  Duplicate fields: {'Set' if updated else 'Failed'}")
        print(f"  Phase: {'Set' if phase_ok else 'Failed'}")
        sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def run_gui():
    """Launch the web GUI."""
    app = create_web_app()
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  OCTANE DM Tool — Web GUI")
    print(f"  http://localhost:{port}")
    print(f"  Press Ctrl+C to stop\n")
    Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    # If a positional argument is given (numeric ID), run CLI mode
    # Otherwise, launch the web GUI
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        run_cli()
    elif len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print("OCTANE DM Tool — Unified CLI + Web GUI")
        print()
        print("Usage:")
        print("  python3 octane_dm_tool.py                 Launch web GUI (port 5050)")
        print("  python3 octane_dm_tool.py 2713179         CLI mode for ticket 2713179")
        print("  python3 octane_dm_tool.py 2713179 --octane-token TOKEN")
        print()
        print("For CLI help: python3 octane_dm_tool.py 0 --help")
    else:
        run_gui()
