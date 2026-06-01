#!/opt/homebrew/bin/python3.14
"""
OCTANE Ticket Processor GUI
Automatische Bearbeitung von Navigation-Defects in OCTANE

Features:
- Login via OCTANE_User und access_token
- Suche nach Navigation Defects (ECE/Japan/US) in Phase 03
- Automatische Ticket-Bearbeitung mit Progress-Anzeige
"""

import os
import json
import queue as _queue
os.environ['TK_SILENCE_DEPRECATION'] = '1'

import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import threading
import requests
import urllib3
import re
import webbrowser
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple
from PIL import Image as _PILImage, ImageTk as _PILImageTk

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Preferences (persisted across restarts) ───────────────────────────────────
PREFS_FILE = os.path.expanduser("~/.octane_prefs.json")

def _load_prefs() -> dict:
    try:
        with open(PREFS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_prefs(prefs: dict) -> None:
    try:
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════════
# OCTANE API Configuration
# ══════════════════════════════════════════════════════════════════════════════
OCTANE_URL = "https://octane-prod.bmwgroup.net"
SHARED_SPACE = "1002"
WORKSPACE = "2001"
BASE = f"{OCTANE_URL}/api/shared_spaces/{SHARED_SPACE}/workspaces/{WORKSPACE}"

# Defect Categories für Navigation
NAVIGATION_CATEGORIES = [
    "Application Navigation ECE",
    "Application Navigation Japan",
    "Application Navigation US",
]

# Assigned ECU Filter (for OCTANE query)
ASSIGNED_ECUS = ["HU-MGU_02_A", "IDCEVO-25"]

# Assigned ECU dropdown options (display names → must match OCTANE list_node names)
ECU_OPTIONS = ["IDCEVO-25", "HU-MGU_02_A", "HERE", "Zenrin"]

# Phase Filter
SEARCH_PHASE = "03-In Analysis"  # Exact phase name as shown in OCTANE

# Owner Mapping
OWNER_POSITIONING = "Johannes Schoenleben, DE-310"
OWNER_DEFAULT = "Michael Pichlmeier, DE-310"
OWNER_DQ_HERE   = "Tobias Naumann"           # HERE map-data tickets
OWNER_DQ_ZENRIN = "Jinglei Huang"             # Zenrin map-data tickets
OWNER_DQ = "Michael Pichlmeier, DE-310"   # Data-quality (HERE/Zenrin) tickets (legacy fallback)
OWNER_MAX_MERTENS = "Max Mertens"

# Data-Quality (HERE / Zenrin) Keywords
# Derived from 22 real HERE/Zenrin tickets in OCTANE.
# Rule: wrong/incorrect SUPPLIER DATA (not a missing software feature).
# Checked against title only.
DQ_HERE_KEYWORDS = [
    # RTTI / Traffic data (HERE)
    "rtti",
    "traffic jam not shown", "traffic not shown",
    "stillstanding",
    "slow-moving traffic", "slow moving traffic",
    # POI / Address data (HERE)
    "zip code",
    "displayed redundantly",
    "same poi",
    "irrelevant search results",
]
DQ_ZENRIN_KEYWORDS = [
    # Closed / non-existent facilities (Zenrin Japan)
    "no longer existing",
    "non-existed",
    "longer existing facility",
    # Wrong parking entrance geometry
    "wrong parking entrance",
    "parking entrance",   # combined with 'provided'/'wrong'
    # Wrong lane data
    "wrong lane guidance",
    "lane guidance",
    # Japan map routing
    "wrong direction when the selected poi",
    "routing took more turns",
]
# Shared signals: appear in both HERE and Zenrin tickets
DQ_SHARED_KEYWORDS = [
    "incorrect routing point",
    "routing point provided",
    "wrong routing point",
    "wrong arrival point",
    "wrong location for",        # POI location error
    "incorrect phone number",
    "facility was provided",     # stale facility data
    "facility provided",
    "entrance was displayed wrong",
    "wrong entrance",
    "poi is provided",
    "is provided as entrance",
]

# Positioning Keywords – ONLY hard GPS/GNSS/dead-reckoning domain terms.
# Do NOT include generic words like "positioning", "position", "location" which
# also appear in normal navigation (map display, vehicle icon, POI location, etc.)
# Keywords checked against the ticket TITLE ONLY (descriptions are excluded because
# they contain GPS debug logs / coordinates which appear in all navigation tickets).
# Sourced from historical tickets assigned to Johannes Schoenleben (Positioning).
POSITIONING_KEYWORDS = [
    # Direct domain terms
    "positioning",          # vehicle self-localization (safe in title-only mode)
    "localization", "localisation",
    # BMW-internal terms
    "ccp",                  # Current Car Position – BMW term for vehicle position
    "ccp drift",
    "dr mode",              # Dead Reckoning mode
    "tunnel mode",
    "speed pulse",
    # Sensor / signal keywords
    "gps",
    "gnss",
    "gyro",                 # gyroscope sensor
    "compass",
    "odometry",
    "sensor fusion", "sensor_fusion",
    "dead reckoning", "dead_reckoning",
    # Vehicle orientation / heading (multi-word only – bare 'heading' is ambiguous)
    "heading accuracy", "heading error", "heading drift",
    "vehicle heading",      # explicit heading of the vehicle
    "vehicle facing",
    "vehicle position",     # title-only: safe to use here
    "map matching",
    "ego localiz", "self-localiz",
    # Error/accuracy qualifiers
    "position accuracy", "position error", "position uncertainty",
    "localization error", "localisation error",
    # GPS/GNSS signals
    "gps fix", "gps loss", "gps signal",
    "gnss fix", "gnss loss", "gnss signal",
    "latitude", "longitude",
]

# Positioning EXCLUSIONS – if ANY of these phrases appear in the ticket title
# the ticket is NOT a Positioning ticket, regardless of other keywords.
# Used to prevent nav-app crashes and map-display bugs from being misclassified.
POSITIONING_EXCLUSIONS = [
    # App / process crashes
    "crash",              # nav app crash, application crash, crashed, crashing
    "app crash",
    "navigation crash",
    "nav crash",
    "system crash",
    "application crash",
    "freeze",             # app freeze / frozen screen
    "reboot",             # ECU reboot triggered by nav
    "restart",            # app restart / spontaneous restart
    # Map not displayed / black screen
    "map not",            # map not displayed, map not shown, map not loading
    "no map",             # no map displayed
    "map display",        # map display issue / map display failure
    "map render",         # map rendering failed
    "black screen",       # black map screen
    "blank screen",
    "karte nicht",        # German: Karte nicht angezeigt
    "karte wird nicht",
    "nicht angezeigt",    # German: not displayed (generic)
    "map missing",
    "map not loading",
    "map not visible",
    # Generic display / UI failures that are not positioning-domain
    "not displayed",
    "not shown",
    "not visible",
    # Navigation Search / POI / Speech – never Positioning
    "destination search",
    "address search",
    "poi search",
    "voice search",
    "speech search",
    "speech input",
    "voice input",
    "voice command",
    "sprachsuche",          # German: voice/speech search
    "spracheingabe",        # German: speech input
    "navigation search",
    "search result",
    "search function",
    "route search",
    "route calculation",    # routing engine, not positioning
    "route planning",
    "route guidance",
    "guidance",             # turn-by-turn guidance is routing, not GPS domain
    # EV / fuel range topics
    "low range",
    "range warning",
    "range calculation",
    "range display",
    "range navigation",
    "reichweite",           # German: range
]

# First-Use / SOP: computed from current date at runtime (see _next_pu())
# PU cycle: 03/yy, 07/yy, 11/yy
_FIRST_USE_FALLBACK = []  # populated at runtime via _next_pu_options()


def _next_pu(reference_date=None) -> str:
    """Return the next upcoming PU in 'yy-mm' format.
    PU release months are 03, 07, 11."""
    import datetime
    d = reference_date or datetime.date.today()
    yy = d.year % 100
    for month in (3, 7, 11):
        if (d.month, d.day) <= (month, 1):
            return f"{yy:02d}-{month:02d}"
    # Past November → first PU of next year
    return f"{(yy + 1) % 100:02d}-03"


def _next_pu_options() -> list:
    """Return ordered list of upcoming PU values (next 6 PUs) for the dropdown."""
    import datetime
    d = datetime.date.today()
    results = []
    for _ in range(6):
        pu = _next_pu(d)
        if pu not in results:
            results.append(pu)
        # Advance to first day of the next PU month.
        # Use day=1 so _next_pu() detects this date as belonging to that PU.
        yy = int(pu[:2]) + 2000
        mm = int(pu[3:])
        next_mm = {3: 7, 7: 11, 11: 3}[mm]
        next_yy = yy + (1 if mm == 11 else 0)
        d = datetime.date(next_yy, next_mm, 1)
    return results

# I-Steps zu First Use/SOP Mapping (Beispiel - anpassen nach Bedarf)
ISTEP_TO_SOP = {
    "I001": "SOP 07/2024",
    "I002": "SOP 11/2024",
    "I003": "SOP 03/2025",
    "I004": "SOP 07/2025",
    "I005": "SOP 11/2025",
    "NA25": "SOP 11/2025",
    "NA26": "SOP 03/2026",
    "G70": "SOP 2024",
    "G05": "SOP 2024",
    "U06": "SOP 2024",
    "U11": "SOP 2025",
}

# Path to the JPEG/PNG shown as the header icon.
# Set to "" to fall back to the canvas-drawn bee.
ICON_PATH = "~/octane_bee_icon.jpg"

# Display sentinel for "no value" in the Target I-Step dropdown.
# Prevents macOS Aqua from collapsing the button when the variable is empty.
TIS_EMPTY_LABEL = "–"

# Preview table: fixed pixel-widths per column.
# Header labels AND data widgets are placed in identically-sized Frame cells
# so columns align regardless of font / widget type differences.
_TBLCOL_PX = {
    "check":     28,   # ✓ checkbox
    "id":        72,   # Ticket-ID
    "titel":    280,   # Titel
    "aktion":   270,   # Geplante Aktion
    "owner":    200,   # Owner dropdown  (w=30 Hv8 + arrow)
    "sop":       96,   # First Use       (w=6  Hv8 + arrow)
    "ecu":       96,   # ECU             (w=9  Hv8 + arrow)
    "tis":      148,   # Target I-Step   (w=16 Hv8 + arrow)
    "br":       182,   # Blocking Reason (w=22 Hv8 + arrow)
    "kommentar":220,   # Kommentar (editable entry)
    "istep":    148,   # I-Step (read-only label)
    "undo":     115,   # ↩ Reject rückgängig (nur bei REJECT-Zeilen)
}

# Default comment posted on every processed ticket (user-editable in preview)
_DEFAULT_PROCESSED_CMT = "Processed by DE-310 OCTANE Ticket Tool."

# Known blocking-reason values from OCTANE (fallback when API discovery fails)
_BLOCKING_REASON_FALLBACK = [
    "",
    "Additional Information necessary",
    "Child (Duplicate)",
    "CR - conceptional issue",
    "Defect handling in supplier system",
    "Expected behaviour",
    "Function not implemented/testable yet",
    "Further traces necessary",
    "Insufficient Defect Quality",
    "Invalid Testcase",
    "Management decision",
    "Measuring Tool Issue",
    "Not reproducible",
    "Not responsible",
    "PQM Transfer",
    "Retest required",
    "Solution Survey",
    "Solution insufficient",
    "TRI_03-not ok preconditions",
    "TRI_04-not ok platform config",
    "TRI_08-invalid sw version",
    "TRI_09-wrong interpretation of testcase",
    "Tolerated",
    "Tolerated process integrated",
    "Tolerated requested",
    "Under Survey",
    "User Error",
]

# Manual field-name overrides — set these if auto-detection fails.
# Tip: check the "Defect-Felder" log lines after login to see available names.
ISTEP_FIELD_OVERRIDE          = "involved_i_step1_udf"  # confirmed via metadata dump
BLOCKING_REASON_FIELD_OVERRIDE = "blocking_reason_udf"     # confirmed via metadata dump

# Manual list-root overrides for option fetching.
# If "First Use Optionen: keine gefunden" appears, set the logical_name of the
# OCTANE list-root (shown in the post-login log as "Bekannte List-Roots:").
# Examples: "list_node.first_use_udf", "list_node.sop_udf"
FIRST_USE_LIST_OVERRIDE       = ""  # e.g. "list_node.first_use_sop_of_function_udf"
# Same for blocking reason — e.g. "list_node.blocking_reason_udf"
BLOCKING_REASON_LIST_OVERRIDE = ""  # e.g. "list_node.blocking_reason_udf"


# ══════════════════════════════════════════════════════════════════════════════
# OCTANE API Client
# ══════════════════════════════════════════════════════════════════════════════
class OctaneClient:
    """OCTANE REST API Client"""
    
    @staticmethod
    def _sanitize_cookie(value: str) -> str:
        """Strip any character outside the printable ASCII range (0x21-0x7E)
        that is not allowed in HTTP cookie values (RFC 6265).
        Tokens pasted from PDFs or rich-text editors sometimes contain
        non-breaking spaces (U+00A0), curly quotes, or other non-ASCII
        characters that cause urllib3 to raise UnicodeEncodeError."""
        return "".join(c for c in value if 0x20 < ord(c) < 0x7F and c not in '",;\\')

    def __init__(self, access_token: str, user_cookie: str = ""):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "HPECLIENTTYPE": "HPE_MQM_UI"
        })
        clean_token = self._sanitize_cookie(access_token)
        self.session.cookies.set("access_token", clean_token, domain="octane-prod.bmwgroup.net")
        if user_cookie:
            clean_user = self._sanitize_cookie(user_cookie)
            self.session.cookies.set("OCTANE_USER", clean_user, domain="octane-prod.bmwgroup.net")
        self.connected = False
        self.owner_cache: Dict[str, str] = {}
        self.phase_cache: Dict[str, str] = {}
        self._phase_entity_type: str = "list_node"  # set to 'phase' when /phases API works
        self._field_root_cache: Dict[str, Optional[str]] = {}
        self._first_use_list_root: Optional[str] = None  # cached after first successful options fetch
        self._extra_fields: str = ""  # injected after field-probe
        # Batch-save support: accumulate field changes per ticket, commit once
        self._batch_did: Optional[str] = None
        self._batch_updates: Dict[str, Any] = {}
        # Set to True when any API call returns 401 – stops all further field lookups
        self._session_expired: bool = False

    def _log(self, msg: str, level: str = "INFO"):
        """Fallback logger used by OctaneClient methods.
        The GUI app overrides this by injecting a real callback via set_log_callback().
        Falls back to print() so debug output is always visible in the console."""
        if callable(getattr(self, '_log_callback', None)):
            self._log_callback(msg, level)
        else:
            print(f"[{level}] {msg}")

    def set_log_callback(self, cb):
        """Inject a GUI log callback so OctaneClient can emit to the log panel."""
        self._log_callback = cb

    def test_connection(self) -> Tuple[bool, str]:
        """Test der OCTANE-Verbindung"""
        try:
            r = self.session.get(
                f"{BASE}/defects",
                params={"fields": "id", "limit": 1},
                verify=False,
                timeout=30
            )
            if r.status_code == 401:
                return False, "Token abgelaufen oder ungültig"
            if not r.ok:
                return False, f"HTTP {r.status_code}: {r.text[:200]}"
            self.connected = True
            total = r.json().get("total_count", "?")
            return True, f"Verbunden (Total Defects: {total})"
        except requests.RequestException as e:
            return False, f"Verbindungsfehler: {str(e)}"
    
    def _fetch_list_values(self, list_name: str) -> Dict[str, str]:
        """Fetch ALL list values for a given list root and return name->id mapping.
        Tries two query strategies:
        1. logical_name match (standard)
        2. id match (fallback — in some OCTANE instances logical_name == id hash)
        Paginates automatically so lists with >500 entries are fully retrieved.
        Returns {} immediately if the session has expired (401 detected)."""
        if self._session_expired:
            return {}

        def _fetch_with_query(q: str) -> Dict[str, str]:
            out: Dict[str, str] = {}
            offset = 0
            page_size = 500
            try:
                while True:
                    r = self.session.get(
                        f"{BASE}/list_nodes",
                        params={"fields": "id,name", "query": q,
                                "limit": page_size, "offset": offset},
                        verify=False, timeout=30)
                    if r.status_code == 401:
                        self._session_expired = True
                        self._log("Session abgelaufen (401) – bitte neu einloggen.", "ERROR")
                        return {}
                    if not r.ok:
                        break
                    data = r.json().get("data", [])
                    for item in data:
                        out[item.get("name", "")] = item.get("id", "")
                    if len(data) < page_size:
                        break
                    offset += page_size
            except Exception:
                pass
            return out

        # Strategy 1: logical_name
        result = _fetch_with_query(f'"list_root={{logical_name=\'{list_name}\'}}"')
        if result or self._session_expired:
            return result
        # Strategy 2: id (handles OCTANE instances where logical_name is a hash == id)
        result = _fetch_with_query(f'"list_root={{id=\'{list_name}\'}}"')
        return result

    def _all_list_roots(self) -> List[Dict]:
        """Return all list-root nodes from this workspace (cached, paginated).
        Returns [] immediately if the session has expired."""
        if self._session_expired:
            return []
        if not getattr(self, '_list_roots_cache', None):
            collected: List[Dict] = []
            offset = 0
            page_size = 500
            try:
                while True:
                    r = self.session.get(
                        f"{BASE}/list_nodes",
                        params={"fields": "id,name,logical_name",
                                "query": '"list_root={null}"',
                                "limit": page_size, "offset": offset},
                        verify=False, timeout=30)
                    if r.status_code == 401:
                        self._session_expired = True
                        break
                    if not r.ok:
                        break
                    data = r.json().get("data", [])
                    collected.extend(data)
                    if len(data) < page_size:
                        break
                    offset += page_size
            except Exception:
                pass
            self._list_roots_cache = collected
        return self._list_roots_cache

    @staticmethod
    def _is_valid_field_name(name: str) -> bool:
        """Return True if *name* looks like a real OCTANE field name.
        Rejects auto-generated hash/UUID logical names (long all-lowercase-alnum
        strings with no underscores), which can't be used as field names."""
        import re as _re
        if not name or not name[0].isalpha():
            return False
        if len(name) > 14 and '_' not in name and _re.fullmatch(r'[a-z0-9]+', name):
            return False
        return True

    def _find_list_roots_by_keyword(self, *keywords: str) -> List[str]:
        """Return logical_names of all list roots whose name/logical_name
        contains ANY of the given keywords (case-insensitive)."""
        results = []
        for root in self._all_list_roots():
            ln = (root.get("logical_name") or "").lower()
            nm = (root.get("name") or "").lower()
            if any(kw.lower() in ln or kw.lower() in nm for kw in keywords):
                lname = root.get("logical_name") or root.get("name") or ""
                if lname:
                    results.append(lname)
        return results

    def _find_value_in_roots(self, roots: List[str], value: str
                             ) -> Optional[Tuple[str, str]]:
        """Search *value* in each list root and return (field_name, node_id)
        of the first match (exact then substring). field_name is derived from
        the logical_name by stripping the 'list_node.' prefix."""
        for root in roots:
            items = self._fetch_list_values(root)
            if not items:
                continue
            # exact
            for nm, nid in items.items():
                if value.lower() == nm.lower():
                    fname = root.replace("list_node.", "") if root.startswith("list_node.") else root
                    if self._is_valid_field_name(fname):
                        return fname, nid
            # substring
            for nm, nid in items.items():
                if value.lower() in nm.lower():
                    fname = root.replace("list_node.", "") if root.startswith("list_node.") else root
                    if self._is_valid_field_name(fname):
                        return fname, nid
        return None

    def _try_field_candidates_with_node(
            self, defect_id: str, node_id: str,
            field_candidates: List[str]) -> Tuple[bool, str]:
        """When we know the list_node id but not the field name, probe each
        candidate field name by sending a direct PUT (bypassing batch mode).
        Returns (True, field_name) on first success, (False, error_msg) if all fail."""
        for fname in field_candidates:
            # Bypass batch mode: call session.put directly so we get real HTTP status
            try:
                r = self.session.put(
                    f"{BASE}/defects/{defect_id}",
                    json={fname: {"type": "list_node", "id": node_id}},
                    verify=False, timeout=30)
                if r.ok:
                    self._log(f"[Strategy4-probe] ✅ field '{fname}' accepted by OCTANE", "INFO")
                    return True, f"OK via probed field '{fname}'"
                if r.status_code == 400:
                    self._log(f"[Strategy4-probe] field '{fname}' → 400, trying next", "INFO")
                    continue
                # Other error (5xx, network) → give up
                self._log(f"[Strategy4-probe] field '{fname}' → HTTP {r.status_code}: {r.text[:200]}", "WARNING")
                break
            except Exception as e:
                self._log(f"[Strategy4-probe] exception: {e}", "WARNING")
                break
        return False, f"Alle Kandidaten gescheitert: {field_candidates}"

    def _find_defect_field_for_listnode(self, value: str
                                         ) -> Optional[Tuple[str, str]]:
        """Value-first discovery: find a list_node whose name exactly matches
        *value* (case-insensitive), then reverse-look up the defect field that
        uses its list_root.  Returns (field_name, node_id) or None.

        _defect_reference_fields() stores {field_name: list_root_id}.
        Build a reverse map list_root_id → field_name, then match the id
        returned by the list_node search.
        """
        ref_fields = self._defect_reference_fields()
        # Reverse map: list_root_id → field_name
        rev_id: Dict[str, str] = {rid: fn for fn, rid in ref_fields.items()}

        self._log(
            f"[Strategy4] '{value}': ref_fields={len(ref_fields)}, rev_id={len(rev_id)}", "INFO")

        try:
            r = self.session.get(
                f"{BASE}/list_nodes",
                params={"fields": "id,name,list_root",
                        "query": f'"name=\'{value}\'"',
                        "limit": 50},
                verify=False, timeout=20)
            if r.ok:
                nodes = r.json().get("data", [])
                self._log(f"[Strategy4] OCTANE returned {len(nodes)} node(s) for name='{value}'", "INFO")
                for node in nodes:
                    node_name = (node.get("name") or "").strip()
                    # Exact match only (OCTANE '=' can behave as prefix match)
                    if node_name.lower() != value.lower():
                        self._log(f"[Strategy4] skip '{node_name}' (not exact match)", "INFO")
                        continue
                    lr = node.get("list_root")
                    if not isinstance(lr, dict):
                        self._log(f"[Strategy4] node '{node_name}' has no list_root dict", "INFO")
                        continue
                    lr_id = str(lr.get("id", ""))
                    self._log(
                        f"[Strategy4] node '{node_name}' → list_root id={lr_id}",
                        "INFO")
                    # Try metadata reverse map first
                    field_name = rev_id.get(lr_id)
                    if field_name:
                        self._log(f"[Strategy4] ✅ matched via metadata rev_id '{lr_id}' → {field_name}", "INFO")
                        return field_name, node["id"]
                    # Metadata unavailable — verify by fetching list children via id
                    # (In some OCTANE instances logical_name == id, so _fetch_list_values
                    # fallback Strategy 2 now handles this).
                    children = self._fetch_list_values(lr_id)
                    if any(v.lower() == value.lower() for v in children):
                        # Confirmed: this list_root contains the value.
                        # Return a sentinel: field_name = "__lr_id:<lr_id>" so callers
                        # know to try their own candidate field names with this node id.
                        self._log(
                            f"[Strategy4] ✅ confirmed via id-fetch: list_root={lr_id} "
                            f"contains '{value}' ({len(children)} items). "
                            "Returning sentinel for caller to resolve field name.", "INFO")
                        return f"__lr_id:{lr_id}", node["id"]
                    self._log(
                        f"[Strategy4] ❌ list_root id={lr_id} — value not confirmed in children", "WARNING")
            else:
                self._log(f"[Strategy4] list_nodes query failed: {r.status_code}", "WARNING")
        except Exception as e:
            self._log(f"[Strategy4] exception: {e}", "WARNING")
        return None

    def _defect_reference_fields(self) -> Dict[str, str]:
        """Return {field_name: list_root_id} for EVERY defect field that has a
        list_root.  Cached after first use.  Uses the same direct entity_name=
        param format that _probe_extra_fields uses (OCTANE metadata API does NOT
        accept the OCTANE query-language syntax for this endpoint)."""
        if getattr(self, '_defect_ref_fields_cache', None) is not None:
            return self._defect_ref_fields_cache
        if self._session_expired:
            self._defect_ref_fields_cache = {}
            return {}
        result: Dict[str, str] = {}

        def _parse_items(items):
            for fld in items:
                fname = fld.get("name")
                lr    = fld.get("list_root")
                if fname and isinstance(lr, dict):
                    rid = str(lr.get("id") or "")
                    if rid:
                        result[fname] = rid

        # Try both known metadata endpoint variants.
        # Variant A: entity_name embedded in URL (avoids requests encoding issues)
        # Variant B: types-based path used by some OCTANE versions
        # Paginates through all pages.
        page_size = 500
        for base_url in [
            f"{BASE}/metadata/fields?entity_name=defect",
            f"{BASE}/metadata/types/defect/fields",
        ]:
            offset = 0
            page_found = False
            while True:
                try:
                    url = f"{base_url}&limit={page_size}&offset={offset}"
                    r = self.session.get(url, verify=False, timeout=20)
                    if r.status_code == 401:
                        self._session_expired = True
                        self._log(
                            f"[defect_ref_fields] HTTP 401 – Session abgelaufen! "
                            "Bitte Programm neu starten und neu einloggen.", "ERROR")
                        break
                    if not r.ok:
                        self._log(
                            f"[defect_ref_fields] {base_url.split('?')[0].split('/')[-2:]} "
                            f"HTTP {r.status_code}", "WARNING")
                        break
                    body  = r.json()
                    items = body.get("data", body.get("fields", []))
                    if not items:
                        break
                    page_found = True
                    _parse_items(items)
                    if len(items) < page_size:
                        break
                    offset += page_size
                except Exception as e:
                    self._log(f"[defect_ref_fields] exception: {e}", "WARNING")
                    break
            if page_found and result:
                break  # no need to try second variant

        self._defect_ref_fields_cache = result
        self._log(
            f"[defect_ref_fields] {len(result)} fields with list_root. "
            f"Sample: {list(result.items())[:5]}", "INFO")
        return result

    def _get_field_list_root(self, field_name: str,
                              entity_name: str = "defect") -> Optional[str]:
        """Return the list_root id for a given entity field.
        For defect fields, uses the cached _defect_reference_fields() to avoid
        extra API calls.  Falls back to a direct metadata query for other entities."""
        cache_key = f"{entity_name}.{field_name}"
        if cache_key in self._field_root_cache:
            return self._field_root_cache[cache_key]
        # Fast path: defect fields are already fully cached
        if entity_name == "defect":
            rid = self._defect_reference_fields().get(field_name)
            self._field_root_cache[cache_key] = rid
            return rid
        try:
            r = self.session.get(
                f"{BASE}/metadata/fields?entity_name={entity_name}&limit=500",
                verify=False, timeout=15)
            if r.ok:
                body = r.json()
                items = body.get("data", body.get("fields", []))
                for fld in items:
                    if fld.get("name") != field_name:
                        continue
                    lr = fld.get("list_root")
                    if isinstance(lr, dict):
                        rid = str(lr.get("id") or "")
                        if rid:
                            self._field_root_cache[cache_key] = rid
                            return rid
        except Exception:
            pass
        self._field_root_cache[cache_key] = None
        return None

    def _get_phase_id(self, phase_name: str,
                       defect_id: Optional[str] = None) -> Optional[str]:
        """Get phase ID by name.
        First tries the /phases API (OCTANE v12+) which returns first-class
        phase entities.  Falls back to list_root-based discovery for older
        instances.  Fast-fails immediately if the session has expired."""
        if self._session_expired:
            return None
        if not self.phase_cache:
            self._log(f"[phase] Cache leer – Discovery für '{phase_name}'", "INFO")
            import re as _re
            _ppat = _re.compile(r'^\d{2}[-\s]')

            # ── Strategy P: /phases API (OCTANE v12+ first-class phase entities) ──
            # IDs returned may be deferred refs like 'phase.defect.deferred' but
            # OCTANE accepts them in PUT when type='phase' is specified.
            try:
                rph = self.session.get(
                    f"{BASE}/phases",
                    params={"fields": "id,name", "limit": 200},
                    verify=False, timeout=15)
                if rph.status_code == 401:
                    self._session_expired = True
                    self._log("[phase] StrategyP: 401 – Session abgelaufen", "ERROR")
                    return None
                if rph.ok:
                    ph_data = rph.json().get("data", [])
                    if ph_data and any(_ppat.match(p.get("name", "")) for p in ph_data):
                        self._phase_entity_type = "phase"
                        for p in ph_data:
                            nm  = (p.get("name") or "").strip()
                            nid = (p.get("id")   or "").strip()
                            if nm and nid:
                                self.phase_cache[nm] = nid
                        self._log(
                            f"[phase] /phases API: {len(ph_data)} Phases geladen, "
                            f"type='phase': {list(self.phase_cache.keys())}", "INFO")
            except Exception as e:
                self._log(f"[phase] StrategyP Fehler: {e}", "WARNING")
            if self._session_expired:
                return None

            def _load_from_lr_id(lr_id: str) -> bool:
                """Fetch all list_nodes for a given list_root id.
                Uses list_root={id='...'} DIRECTLY — bypasses _fetch_list_values
                which would first try list_root={logical_name='...'} and could
                return wrong nodes if another list's logical_name equals this id."""
                out: Dict[str, str] = {}
                offset = 0
                page_size = 200
                try:
                    while True:
                        r = self.session.get(
                            f"{BASE}/list_nodes",
                            params={"fields": "id,name",
                                    "query": f'"list_root={{id=\'{lr_id}\'}}"',
                                    "limit": page_size, "offset": offset},
                            verify=False, timeout=20)
                        if r.status_code == 401:
                            self._session_expired = True
                            return False
                        if not r.ok:
                            break
                        data = r.json().get("data", [])
                        for item in data:
                            out[item.get("name", "")] = item.get("id", "")
                        if len(data) < page_size:
                            break
                        offset += page_size
                except Exception:
                    pass
                if out and any(_ppat.match(n) for n in out):
                    self.phase_cache.update(out)
                    self._log(
                        f"[phase] {len(out)} Phases via list_root id={lr_id}: "
                        f"{list(out.keys())}", "INFO")
                    return True
                return False

            # ── Strategy 0: Defect's own phase node → exact list_root id ─────────
            # Reads defect/{id} to get phase node id, then reads that node to get
            # list_root.id, then fetches all siblings.  No logical_name needed.
            try:
                if defect_id:
                    r0 = self.session.get(
                        f"{BASE}/defects/{defect_id}",
                        params={"fields": "id,phase{id,name}"},
                        verify=False, timeout=15)
                else:
                    r0 = self.session.get(
                        f"{BASE}/defects",
                        params={"fields": "id,phase{id,name}", "limit": 1},
                        verify=False, timeout=15)
                if r0.status_code == 401:
                    self._session_expired = True
                    self._log("[phase] Strategy0: 401 – Session abgelaufen", "ERROR")
                    return None
                if r0.ok:
                    entity = (r0.json() if defect_id
                              else (r0.json().get("data") or [{}])[0])
                    phase_node_ref = entity.get("phase") if isinstance(entity, dict) else None
                    if isinstance(phase_node_ref, dict):
                        pnid = phase_node_ref.get("id")
                        if pnid:
                            r0b = self.session.get(
                                f"{BASE}/list_nodes/{pnid}",
                                params={"fields": "id,name,list_root"},
                                verify=False, timeout=15)
                            if r0b.status_code == 401:
                                self._session_expired = True
                                self._log("[phase] Strategy0b: 401 – Session abgelaufen", "ERROR")
                                return None
                            if r0b.ok:
                                lr0 = r0b.json().get("list_root")
                                if isinstance(lr0, dict):
                                    lr_id = lr0.get("id")
                                    if lr_id:
                                        _load_from_lr_id(lr_id)
            except Exception as e:
                self._log(f"[phase] Strategy0 Fehler: {e}", "WARNING")
            if self._session_expired:
                return None

            # ── Strategy 1: Pivot on a known phase name → list_root id ───────────
            # Queries a phase name that must exist on every defect (e.g. the current
            # search phase "03-In Analysis").  Gets its list_root.id, then fetches
            # all siblings.  Avoids the 55-node ambiguity of a wildcard search.
            if not self.phase_cache:
                pivot_names = [SEARCH_PHASE, "01-New", "03-In Analysis",
                               "04-In Progress", "05-In Testing"]
                for pivot in pivot_names:
                    if self._session_expired:
                        return None
                    try:
                        escaped = pivot.replace("'", "\\'")
                        rp = self.session.get(
                            f"{BASE}/list_nodes",
                            params={"fields": "id,name,list_root",
                                    "query": f'"name=\'{escaped}\'"',
                                    "limit": 10},
                            verify=False, timeout=15)
                        if rp.status_code == 401:
                            self._session_expired = True
                            self._log("[phase] Strategy1: 401 – Session abgelaufen", "ERROR")
                            return None
                        if rp.ok:
                            for node in rp.json().get("data", []):
                                if (node.get("name", "") or "").lower() != pivot.lower():
                                    continue
                                lr = node.get("list_root")
                                if isinstance(lr, dict) and lr.get("id"):
                                    if _load_from_lr_id(lr["id"]):
                                        break
                        if self.phase_cache:
                            break
                    except Exception as e:
                        self._log(f"[phase] Strategy1 pivot='{pivot}' Fehler: {e}", "WARNING")

            # ── Strategy 2: Metadata list_root ───────────────────────────────────
            if not self.phase_cache and not self._session_expired:
                self._log("[phase] Strategy2: Metadata-Lookup...", "INFO")
                meta_root = self._get_field_list_root("phase", "defect")
                if meta_root:
                    if not _load_from_lr_id(meta_root):
                        # meta_root might be a logical_name, try direct fetch too
                        vals = self._fetch_list_values(meta_root)
                        if vals and any(_ppat.match(n) for n in vals):
                            self.phase_cache.update(vals)
                            self._log(f"[phase] Strategy2: {len(vals)} Phases.", "INFO")

            if not self.phase_cache:
                self._log(f"[phase] ❌ Discovery für '{phase_name}' fehlgeschlagen.", "ERROR")

        # ── Lookup in populated cache ─────────────────────────────────────────
        for name, id_ in self.phase_cache.items():
            if phase_name in name:
                return id_
        self._log(
            f"[phase] '{phase_name}' nicht im Cache: {list(self.phase_cache.keys())[:8]}", "WARNING")
        return None

    
    def _get_user_id(self, user_name: str) -> Optional[str]:
        """Get user ID by name (searches workspace_users).
        Accepts "Full Name" or "Full Name, extra" formats — only the part
        before the first comma is used for the OCTANE query.
        """
        if user_name in self.owner_cache:
            return self.owner_cache[user_name]
        if self._session_expired:
            return None
        # Strip department suffix like ", DE-310" before querying OCTANE
        search_name = user_name.split(",")[0].strip()
        parts = search_name.lower().split()  # e.g. ["michael", "pichlmeier"]

        def _match(user: dict) -> bool:
            full = (user.get("full_name", "") or user.get("name", "") or "").lower()
            return all(p in full for p in parts)

        try:
            # 1. Wildcard query on full_name
            r = self.session.get(
                f"{BASE}/workspace_users",
                params={
                    "fields": "id,full_name,name,email",
                    "query": f'"full_name=\'{search_name}*\'"',
                    "limit": 10
                },
                verify=False,
                timeout=30
            )
            if r.status_code == 401:
                self._session_expired = True
                return None
            if r.ok:
                for user in r.json().get("data", []):
                    if _match(user):
                        self.owner_cache[user_name] = user["id"]
                        return user["id"]

            # 2. Wildcard query on name field
            r2 = self.session.get(
                f"{BASE}/workspace_users",
                params={
                    "fields": "id,full_name,name,email",
                    "query": f'"name=\'{search_name}*\'"',
                    "limit": 10
                },
                verify=False,
                timeout=30
            )
            if r2.status_code == 401:
                self._session_expired = True
                return None
            if r2.ok:
                for user in r2.json().get("data", []):
                    if _match(user):
                        self.owner_cache[user_name] = user["id"]
                        return user["id"]

            # 3. Broad fetch — match by ALL name parts
            r3 = self.session.get(
                f"{BASE}/workspace_users",
                params={"fields": "id,full_name,name,email", "limit": 500},
                verify=False, timeout=30
            )
            if r3.status_code == 401:
                self._session_expired = True
                return None
            if r3.ok:
                all_users = r3.json().get("data", [])
                for user in all_users:
                    if _match(user):
                        self.owner_cache[user_name] = user["id"]
                        return user["id"]
                # 4. Last resort: surname-only match
                if parts:
                    surname = parts[-1]
                    candidates = [u for u in all_users
                                  if surname in (u.get("full_name", "") or
                                                 u.get("name", "") or "").lower()]
                    if len(candidates) == 1:
                        # Only one user with that surname → safe to use
                        self.owner_cache[user_name] = candidates[0]["id"]
                        return candidates[0]["id"]
                    if candidates:
                        names = [u.get("full_name") or u.get("name") for u in candidates[:8]]
                        self._log(
                            f"[_get_user_id] '{search_name}' nicht eindeutig gefunden. "
                            f"Kandidaten mit '{surname}': {names}", "WARNING")
                    else:
                        # Log some users with same first letter to aid debugging
                        first_letter = parts[0][0] if parts else ""
                        sample = [u.get("full_name") or u.get("name")
                                  for u in all_users
                                  if (u.get("full_name") or u.get("name") or "")
                                     .lower().startswith(first_letter)][:10]
                        self._log(
                            f"[_get_user_id] '{search_name}' nicht gefunden "
                            f"(total {len(all_users)} Users). "
                            f"Namen mit '{first_letter}': {sample}", "WARNING")
        except Exception as e:
            self._log(f"[_get_user_id] Fehler: {e}", "WARNING")
        return None
    
    def _build_query(self) -> str:
        """Build OCTANE query matching Phase + Nav categories (OR) + ECUs (OR)."""
        cats = " || ".join(
            f"problem_category_udf={{name='{c}'}}"
            for c in NAVIGATION_CATEGORIES
        )
        ecus = " || ".join(
            f"assigned_ecu_udf={{name='{e}'}}"
            for e in ASSIGNED_ECUS
        )
        return f'"phase={{name=\'{SEARCH_PHASE}\'}} ; ({cats}) ; ({ecus})"'

    def count_defects(self) -> int:
        """Count all matching defects via a single API query."""
        query = self._build_query()
        seen_ids: set = set()
        offset = 0
        while True:
            try:
                r = self.session.get(
                    f"{BASE}/defects",
                    params={"fields": "id", "query": query,
                            "limit": 200, "offset": offset},
                    verify=False, timeout=30
                )
                if not r.ok:
                    break
                data = r.json().get("data", [])
                for d in data:
                    seen_ids.add(d["id"])
                if len(data) < 200:
                    break
                offset += 200
            except Exception:
                break
        return len(seen_ids)

    def count_unprocessed_defects(self) -> Tuple[int, int]:
        """Count all matching defects and those not yet processed.
        A Positioning ticket already owned by OWNER_POSITIONING is 'done'.
        Returns (total, unprocessed_count)."""
        query = self._build_query()
        seen_ids: set = set()
        unprocessed = 0
        offset = 0
        while True:
            try:
                r = self.session.get(
                    f"{BASE}/defects",
                    params={"fields": "id,name,owner",
                            "query": query, "limit": 200, "offset": offset},
                    verify=False, timeout=45)
                if not r.ok:
                    self._log(f"[count] HTTP {r.status_code}: {r.text[:200]}", "WARNING")
                    break
                data = r.json().get("data", [])
                for d in data:
                    did = d.get("id")
                    if did is None:
                        continue
                    if did not in seen_ids:
                        seen_ids.add(did)
                        if not self.is_already_processed(d):
                            unprocessed += 1
                if len(data) < 200:
                    break
                offset += 200
            except Exception as e:
                self._log(f"[count] Ausnahme: {e} — gebe Teilergebnis zurück", "WARNING")
                break  # return partial result rather than (0, 0)
        return len(seen_ids), unprocessed

    def search_defects(self, offset: int = 0, limit: int = 5,
                        callback=None, progress_cb=None) -> Tuple[List[Dict[str, Any]], int]:
        """
        Suche nach Navigation-Defects (neueste zuerst), paginiert.
        Lädt alle passenden Tickets, sortiert nach Erstellungsdatum (neueste zuerst),
        und gibt die gewünschte Seite (offset:offset+limit) zurück.
        progress_cb(fraction 0.0–1.0) is called after each page load.
        Returns: (defects_for_this_page, total_count)
        """
        collected: List[Dict[str, Any]] = []
        seen_ids: set = set()
        query = self._build_query()
        sub_offset = 0
        api_total: int = 0
        while True:
            try:
                r = self.session.get(
                    f"{BASE}/defects",
                    params={
                        "fields": ("id,name,owner,phase,attachments,"
                                   "problem_category_udf,assigned_ecu_udf,"
                                   "solution_responsible_udf,detected_by,creation_time"
                                   + (f",{self._extra_fields}" if self._extra_fields else "")),
                        "query": query,
                        "order_by": "-creation_time",
                        "limit": 200,
                        "offset": sub_offset
                    },
                    verify=False, timeout=90
                )
                if not r.ok:
                    if callback:
                        callback(f"[WARN] HTTP {r.status_code}: {r.text[:200]}")
                    break
                body = r.json()
                if api_total == 0:
                    api_total = body.get("total_count", 0)
                data = body.get("data", [])
                for d in data:
                    if d["id"] not in seen_ids:
                        seen_ids.add(d["id"])
                        collected.append(d)
                if progress_cb and api_total:
                    progress_cb(min(1.0, len(collected) / api_total))
                if len(data) < 200:
                    break
                sub_offset += 200
            except requests.RequestException as e:
                if callback:
                    callback(f"[ERROR] {e}")
                break

        # Sort all collected by creation_time descending (newest first)
        collected.sort(key=lambda d: d.get("creation_time") or "", reverse=True)

        total = len(collected)
        page  = collected[offset: offset + limit]
        batch_num = offset // limit + 1
        if callback:
            end = offset + len(page)
            callback(f"  Batch {batch_num}: Tickets {offset+1}–{end} von {total} gesamt")
        return page, total
    
    def get_attachments(self, defect_id: str) -> List[Dict[str, Any]]:
        """Get attachments for a defect"""
        try:
            r = self.session.get(
                f"{BASE}/defects/{defect_id}",
                params={"fields": "attachments"},
                verify=False,
                timeout=30
            )
            if r.ok:
                attachments = r.json().get("attachments", {})
                if isinstance(attachments, dict):
                    return attachments.get("data", [])
            return []
        except Exception:
            return []
    
    def has_screenshots_or_traces(self, defect: Dict[str, Any]) -> bool:
        """Check if defect has screenshots or DLT traces attached"""
        attachments = defect.get("attachments", {})
        if isinstance(attachments, dict):
            attachment_list = attachments.get("data", [])
        else:
            attachment_list = self.get_attachments(defect.get("id", ""))
        
        if not attachment_list:
            return False
        
        # Check for screenshots or traces
        for att in attachment_list:
            name = (att.get("name", "") or "").lower()
            if any(ext in name for ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".dlt", ".trace", ".log"]):
                return True
        
        return len(attachment_list) > 0  # Any attachment counts

    _IMAGE_VIDEO_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff",
                          ".tif", ".webp", ".mp4", ".mov", ".avi", ".mkv",
                          ".wmv", ".m4v", ".mpg", ".mpeg", ".3gp"}
    _ZIP_EXT = {".zip", ".gz", ".tar", ".tgz", ".7z", ".rar", ".bz2"}

    def _check_attachment_types(self, defect: Dict[str, Any]):
        """Return (has_media, has_zip) booleans for the defect's attachments."""
        attachments = defect.get("attachments", {})
        attachment_list = (attachments.get("data", []) if isinstance(attachments, dict)
                           else self.get_attachments(defect.get("id", "")))
        has_media = False
        has_zip   = False
        for att in attachment_list:
            name = (att.get("name", "") or "").lower()
            ext  = "." + name.rsplit(".", 1)[-1] if "." in name else ""
            if ext in self._IMAGE_VIDEO_EXT:
                has_media = True
            if ext in self._ZIP_EXT:
                has_zip = True
            if has_media and has_zip:
                break
        return has_media, has_zip

    def has_dlt_traces(self, defect: Dict[str, Any]) -> bool:
        """Return True when there is at least one picture/movie AND at least one
        zip file in the attachments.  Either alone is not sufficient."""
        has_media, has_zip = self._check_attachment_types(defect)
        return has_media and has_zip

    def post_comment(self, defect_id: str, text: str) -> Tuple[bool, str]:
        """Post a plain-text comment on a defect.
        OCTANE REST API requires the entity wrapped in {"data": [...]}.
        """
        try:
            r = self.session.post(
                f"{BASE}/comments",
                json={"data": [{"text": text,
                                "owner_work_item": {"type": "defect",
                                                    "id": str(defect_id)}}]},
                verify=False, timeout=15)
            if r.ok:
                return True, "OK"
            return False, f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            return False, f"Fehler: {e}"

    def fetch_blocking_reason_options(self,
                                       field_name: Optional[str] = None) -> List[str]:
        """Fetch Blocking Reason dropdown options from OCTANE list nodes.
        Uses four strategies in order:
        1. Manual BLOCKING_REASON_LIST_OVERRIDE (if set).
        2. Known list names derived from the discovered field name.
        3. A set of known candidate list names.
        4. Scan all list-root nodes for names matching 'blocking'/'reject'.
        5. Read distinct values directly from actual defects (last resort).
        """
        candidates_tried: set = set()
        _override_active = bool(BLOCKING_REASON_LIST_OVERRIDE)

        # Known blocking-reason keywords – at least one must appear in a valid list.
        # Extended with German/abbreviated variants used in this OCTANE instance.
        _BR_SIGNALS = {"traces", "information", "duplicate", "behaviour",
                       "conceptional", "not a defect", "solved", "supplier",
                       "tolerat", "survey", "reproducible", "responsible",
                       "insufficient", "invalid", "testcase", "tri_",
                       "management", "retest", "transfer", "user error",
                       "pqm", "measuring", "function not", "child"}
        # Values that indicate a WRONG list (e.g. cancellation/closure reasons)
        _BR_ANTI_SIGNALS = {"cancelled", "canceled"}

        def _looks_like_blocking_reasons(opts: List[str], force: bool = False) -> bool:
            """Return True when the option list resembles a blocking-reason list.
            When *force* is True (manual override), accept any non-empty list."""
            if force or _override_active:
                return bool(opts)
            if not opts:
                return False
            # Reject lists dominated by cancellation/closure reasons
            anti_count = sum(
                1 for o in opts
                if any(s in o.lower() for s in _BR_ANTI_SIGNALS)
            )
            if anti_count > len(opts) * 0.4:
                return False
            joined = " ".join(o.lower() for o in opts)
            return any(sig in joined for sig in _BR_SIGNALS)

        def _try(list_name: str, force: bool = False) -> List[str]:
            if list_name in candidates_tried:
                return []
            candidates_tried.add(list_name)
            result = self._fetch_list_values(list_name)
            opts = sorted(result.keys()) if result else []
            if opts and not _looks_like_blocking_reasons(opts, force=force):
                self._log(f"[blocking_reason] {list_name}: {len(opts)} Werte, aber kein BR-Signal → übersprungen", "INFO")
                return []   # wrong field (e.g. cancellation/closure reason)
            return opts

        # Strategy 0: manual override
        if BLOCKING_REASON_LIST_OVERRIDE:
            opts = _try(BLOCKING_REASON_LIST_OVERRIDE, force=True)
            if opts:
                self._log(f"[blocking_reason] Override '{BLOCKING_REASON_LIST_OVERRIDE}': {len(opts)} Optionen", "SUCCESS")
                return opts
            self._log(f"[blocking_reason] Override '{BLOCKING_REASON_LIST_OVERRIDE}' lieferte keine Werte", "WARNING")

        # Strategy 1: derive names from the discovered field name
        if field_name:
            for ln in [f"list_node.{field_name}",
                       f"list_node.{field_name.replace('_udf', '')}",
                       field_name]:
                opts = _try(ln)
                if opts:
                    return opts

        # Strategy 2: known candidate list names
        for ln in ["list_node.blocking_reason_udf",
                   "list_node.blocking_reason",
                   "list_node.closed_reason_udf",
                   "list_node.closed_reason",
                   "list_node.rejection_reason_udf",
                   "list_node.bl_reason_udf",
                   "list_node.reject_reason_udf",
                   "list_node.cwa_reason_udf",
                   "list_node.cwa_reason"]:
            opts = _try(ln)
            if opts:
                return opts

        # Strategy 3: scan all list-root nodes for names matching blocking/reject/cwa
        try:
            r = self.session.get(
                f"{BASE}/list_nodes",
                params={"fields": "id,name,logical_name",
                        "query": '"list_root={null}"',
                        "limit": 500},
                verify=False, timeout=15)
            if r.ok:
                for root in r.json().get("data", []):
                    ln  = (root.get("logical_name") or "").lower()
                    nm  = (root.get("name") or "").lower()
                    if any(k in ln or k in nm for k in
                           ["blocking_reason", "blocking", "reject_reason",
                            "bl_reason", "cwa_reason"]):
                        lname = root.get("logical_name") or root.get("name") or ""
                        opts = _try(lname)
                        if opts:
                            return opts
        except Exception:
            pass

        # Strategy 4: read distinct values from actual defects
        if field_name:
            try:
                r = self.session.get(
                    f"{BASE}/defects",
                    params={"fields": f"id,{field_name}",
                            "query": self._build_query(),
                            "limit": 50},
                    verify=False, timeout=20)
                if r.ok:
                    seen: set = set()
                    for rec in r.json().get("data", []):
                        val = rec.get(field_name)
                        if isinstance(val, dict) and val.get("name"):
                            seen.add(val["name"])
                        elif isinstance(val, list):
                            for item in val:
                                if isinstance(item, dict) and item.get("name"):
                                    seen.add(item["name"])
                        elif isinstance(val, str) and val.strip():
                            seen.add(val.strip())
                    if seen:
                        opts_s4 = sorted(seen)
                        if _looks_like_blocking_reasons(opts_s4):
                            return opts_s4
                        # Values from defects don't look like blocking reasons
                        # (e.g. "Cancelled …" from closed/cancelled tickets) – skip.
                        self._log(
                            f"[blocking_reason] Strategy4: {len(seen)} Werte aus Defects "
                            f"übersprungen (kein BR-Signal): {opts_s4[:5]}",
                            "INFO"
                        )
            except Exception:
                pass

        # Diagnostic: dump all list roots so the user can find the correct name
        try:
            roots = self._all_list_roots()
            if roots:
                root_names = sorted(
                    (r.get("logical_name") or r.get("name") or "") for r in roots
                    if r.get("logical_name") or r.get("name")
                )
                self._log(
                    f"[blocking_reason] Alle {len(root_names)} bekannten List-Roots: "
                    f"{', '.join(root_names)}",
                    "INFO"
                )
                self._log(
                    "[blocking_reason] Tipp: Setze BLOCKING_REASON_LIST_OVERRIDE auf den "
                    "passenden Wert aus der Liste oben.",
                    "WARNING"
                )
        except Exception:
            pass

        # Strategy 5: try metadata API to find which list root this field references
        try:
            for meta_url in [
                f"{BASE}/metadata/fields?entity_name=defect",
                f"{BASE}/metadata/types/defect/fields",
            ]:
                r = self.session.get(meta_url, verify=False, timeout=10)
                if not r.ok:
                    continue
                items = r.json().get("data", r.json().get("fields", []))
                for fdef in items:
                    fname = (fdef.get("name") or "").lower()
                    label = (fdef.get("label") or "").lower()
                    if not any(k in fname or k in label
                               for k in ["blocking_reason", "bl_reason",
                                         "reject_reason", "cwa_reason"]):
                        continue
                    # Check for a field_type_data.list_root reference
                    ftd = fdef.get("field_type_data") or {}
                    list_root = (ftd.get("logical_name") or ftd.get("list_root_logical_name")
                                 or ftd.get("list_root", {}).get("logical_name") or "")
                    if list_root:
                        opts = _try(list_root)
                        if opts:
                            return opts
                    # If the actual field name is known, try it directly
                    actual = fdef.get("name") or ""
                    if actual:
                        for ln in [f"list_node.{actual}",
                                   f"list_node.{actual.replace('_udf', '')}"]:
                            opts = _try(ln)
                            if opts:
                                return opts
        except Exception:
            pass

        return []

    def set_blocking_reason(self, defect_id: str, field_name: str,
                             value: str) -> Tuple[bool, str]:
        """Set the Blocking Reason field to a list_node value by name."""
        if not value:
            return True, "skipped (empty)"
        if self._session_expired:
            return False, "Session abgelaufen"
        try:
            # Try several list_root logical_name variants (field may have
            # 'list_node.' prefix or use the bare field name)
            data: List[Dict] = []
            for ln in [field_name,
                       f"list_node.{field_name}",
                       f"list_node.{field_name.replace('_udf', '')}"]:
                r = self.session.get(
                    f"{BASE}/list_nodes",
                    params={"fields": "id,name",
                            "query": f'"list_root={{logical_name=\'{ln}\'}}"',
                            "limit": 200},
                    verify=False, timeout=15)
                if r.ok:
                    data = r.json().get("data", [])
                    if data:
                        break
            if data:
                for item in data:
                    if value.lower() == (item.get("name", "") or "").lower():
                        return self.update_defect(defect_id, {
                            field_name: {"type": "list_node", "id": item["id"]}
                        })
                # Case-insensitive substring fallback
                for item in data:
                    if value.lower() in (item.get("name", "") or "").lower():
                        return self.update_defect(defect_id, {
                            field_name: {"type": "list_node", "id": item["id"]}
                        })
            # Global name-search fallback: scan all list_nodes by name regardless
            # of list_root. OCTANE rejects nodes from the wrong list on PUT, so
            # this is safe — it only succeeds with the correct node.
            ok_ns, msg_ns = self._set_by_name_search(defect_id, field_name, value)
            if ok_ns:
                return ok_ns, msg_ns
            return False, f"Blocking Reason '{value}' nicht in OCTANE-Liste gefunden ({msg_ns})"
        except Exception as e:
            return False, f"Fehler: {e}"

    def set_problem_category(self, defect_id: str, category_name: str) -> Tuple[bool, str]:
        """Set problem_category_udf to a list_node value by name."""
        field = "problem_category_udf"
        try:
            r = self.session.get(
                f"{BASE}/list_nodes",
                params={"fields": "id,name",
                        "query": f'"list_root={{logical_name=\'{field}\'}}"',
                        "limit": 300},
                verify=False, timeout=20)
            if r.ok:
                items = r.json().get("data", [])
                for item in items:
                    if category_name.lower() == (item.get("name", "") or "").lower():
                        return self.update_defect(defect_id, {
                            field: {"type": "list_node", "id": item["id"]}
                        })
                for item in items:
                    if category_name.lower() in (item.get("name", "") or "").lower():
                        return self.update_defect(defect_id, {
                            field: {"type": "list_node", "id": item["id"]}
                        })
            return False, f"Defect Category '{category_name}' nicht gefunden"
        except Exception as e:
            return False, f"Fehler: {e}"

    def set_latlon(self, defect_id: str, lat: float, lon: float) -> Tuple[bool, str]:
        """Set the Latitude | Longitude field (plain text, format 'xxx.xxxxxx | xxxx.xxxxxx')."""
        latlon_str = f"{lat:.6f} | {lon:.6f}"
        # Discover the field name by scanning defect metadata for a field
        # whose name or label contains 'lat' and 'lon'.
        found_field: Optional[str] = None
        try:
            r = self.session.get(
                f"{BASE}/metadata/fields",
                params={"fields": "name,label,field_type",
                        "query": '"entity_name=\'defect\'"',
                        "limit": 500},
                verify=False, timeout=20)
            if r.ok:
                for fld in r.json().get("data", []):
                    fn = (fld.get("name") or "").lower()
                    lb = (fld.get("label") or "").lower()
                    if ("lat" in fn and "lon" in fn) or ("lat" in lb and "lon" in lb):
                        found_field = fld.get("name")
                        break
                    if ("latitude" in fn or "latitude" in lb):
                        found_field = fld.get("name")
                        break
        except Exception:
            pass
        if not found_field:
            # Common names to try
            for cand in ["lat_lon_udf", "latitude_longitude_udf", "coordinates_udf",
                         "geo_coordinates_udf", "latlon_udf", "lat_lon"]:
                found_field = cand
                break
        if found_field:
            return self.update_defect(defect_id, {found_field: latlon_str})
        return False, "Lat/Lon Feld nicht gefunden"

    def set_first_use(self, defect_id: str, field_name: str,
                      value: str) -> Tuple[bool, str]:
        """Set the First Use / SOP field to a list_node value by name."""
        if not value or value.lower() in ("unbekannt", "unknown", ""):
            return True, "skipped (empty/unknown)"
        if self._session_expired:
            return False, "Session abgelaufen"

        def _try_root(logical_name: str) -> Optional[Tuple[bool, str]]:
            try:
                r = self.session.get(
                    f"{BASE}/list_nodes",
                    params={"fields": "id,name",
                            "query": f"\"list_root={{logical_name='{logical_name}'}}\"",
                            "limit": 200},
                    verify=False, timeout=15)
                if not r.ok:
                    return None
                items = r.json().get("data", [])
                if not items:
                    return None
                for item in items:
                    if value.lower() == (item.get("name", "") or "").lower():
                        return self.update_defect(defect_id, {
                            field_name: {"type": "list_node", "id": item["id"]}
                        })
                for item in items:
                    if value.lower() in (item.get("name", "") or "").lower():
                        return self.update_defect(defect_id, {
                            field_name: {"type": "list_node", "id": item["id"]}
                        })
                available = [i.get("name", "") for i in items[:6]]
                return False, f"First Use '{value}' nicht in Liste (root: {logical_name}). Verfügbar: {available}"
            except Exception as e:
                return False, f"Fehler: {e}"

        # ── Primary: direct name search (no list_root required) ──────────────
        ok0, msg0 = self._set_by_name_search(defect_id, field_name, value)
        if ok0:
            return ok0, msg0

        # ── Fallback: list_root-based lookup ──────────────────────────────────
        meta_root = self._get_field_list_root(field_name)
        candidates = []
        if self._first_use_list_root:
            candidates.append(self._first_use_list_root)
        if meta_root:
            candidates.append(meta_root)
        candidates += [
            f"list_node.{field_name}",
            f"list_node.{field_name.replace('_udf', '')}",
            field_name,
        ]
        seen: set = set()
        for logical_name in candidates:
            if logical_name in seen:
                continue
            seen.add(logical_name)
            result = _try_root(logical_name)
            if result is not None:
                return result
        return False, f"First Use Feld '{field_name}': '{value}' nicht in OCTANE gefunden ({msg0})"

    def is_data_quality_ticket(self, defect: Dict[str, Any]) -> Optional[str]:
        """Check if ticket is a map data quality issue for HERE or Zenrin.
        Returns 'here', 'zenrin', or None.
        Zenrin is ONLY assigned when the category is 'Application Navigation Japan'
        (Zenrin is the Japan-exclusive map data provider).
        Checked against title only to avoid false positives from descriptions."""
        name = (defect.get("name", "") or "").lower()
        cat  = defect.get("problem_category_udf", {})
        cat_name = (cat.get("name", "") if isinstance(cat, dict) else "").lower()
        is_japan = "japan" in cat_name

        # Zenrin keywords are only valid for Japan tickets
        if any(kw in name for kw in DQ_ZENRIN_KEYWORDS):
            return "zenrin" if is_japan else "here"
        if any(kw in name for kw in DQ_HERE_KEYWORDS):
            return "here"
        # Shared keywords: Japan → Zenrin, ECE/US → HERE
        if any(kw in name for kw in DQ_SHARED_KEYWORDS):
            return "zenrin" if is_japan else "here"
        return None

    def is_positioning_ticket(self, defect: Dict[str, Any]) -> bool:
        """Check if ticket is a GPS/GNSS positioning ticket.
        A ticket is classified as Positioning only when:
          1. Its title contains a POSITIONING_KEYWORDS match, AND
          2. Its title does NOT contain any POSITIONING_EXCLUSIONS phrase
             (crashes and map-display bugs are never Positioning tickets).
        Description is NOT checked to avoid false positives from GPS coordinates
        embedded in debug logs."""
        name = (defect.get("name", "") or "").lower()
        # Exclusion check wins: crashes / map-not-displayed → never Positioning
        if any(exc in name for exc in POSITIONING_EXCLUSIONS):
            return False
        return any(keyword in name for keyword in POSITIONING_KEYWORDS)

    def is_already_processed(self, defect: Dict[str, Any]) -> bool:
        """True for Positioning tickets already owned by OWNER_POSITIONING.
        These are in Phase 03 with the correct owner – no further action needed.
        Matches on the first token of OWNER_POSITIONING (before the first comma)
        to be robust against OCTANE returning the name without the ', DE-310' suffix."""
        owner = defect.get("owner", {})
        owner_name = (owner.get("full_name", "") or owner.get("name", "")
                      if isinstance(owner, dict) else str(owner or ""))
        # Use only the surname/firstname part (before first comma) for matching
        owner_key = OWNER_POSITIONING.split(",")[0].strip().lower()
        if owner_key not in owner_name.lower():
            return False
        return self.is_positioning_ticket(defect)

    def fetch_first_use_options(self, first_use_field: Optional[str] = None) -> List[str]:
        """Try to fetch First Use options from OCTANE list nodes.
        Tries multiple strategies in order:
        1. Derive list logical name from the probed field name (most reliable).
        2. A set of known candidate list names.
        3. Scan all list_node roots for names matching 'first_use'/'sop'.
        4. Read distinct values from actual navigation defects as a last resort.
        Only values in SOP format YY-MM within range 23-03 to 26-07 are kept.
        """
        def _filter_sop(opts: List[str]) -> List[str]:
            """Keep only valid SOP values (YY-MM, 23-03 .. <current year+4>-11) + 'unbekannt'."""
            import datetime as _dt
            _max_ym = ((_dt.date.today().year % 100) + 4) * 100 + 11
            out = []
            for v in opts:
                if v.lower() in ("unbekannt", "unknown", ""):
                    out.append(v)
                    continue
                m = re.match(r'^(\d{2})-(\d{2})$', v)
                if m:
                    ym = int(m.group(1)) * 100 + int(m.group(2))
                    if 2303 <= ym <= _max_ym:
                        out.append(v)
            return out

        tried: set = set()

        def _try(list_name: str, force: bool = False) -> List[str]:
            if list_name in tried:
                return []
            tried.add(list_name)
            result = self._fetch_list_values(list_name)
            opts = sorted(result.keys()) if result else []
            if force:
                # Override: accept without SOP filtering so any non-empty list wins
                if opts:
                    self._first_use_list_root = list_name
                return opts
            filtered = _filter_sop(opts) if opts else []
            if filtered:
                self._first_use_list_root = list_name  # cache for set_first_use
            return filtered

        # Strategy 0: manual override
        if FIRST_USE_LIST_OVERRIDE:
            opts = _try(FIRST_USE_LIST_OVERRIDE, force=True)
            # Apply SOP filter but fall back to unfiltered if filter removes everything
            filtered = _filter_sop(opts) if opts else []
            result = filtered or opts
            if result:
                self._log(f"[first_use] Override '{FIRST_USE_LIST_OVERRIDE}': {len(result)} Optionen", "SUCCESS")
                return result
            self._log(f"[first_use] Override '{FIRST_USE_LIST_OVERRIDE}' lieferte keine Werte", "WARNING")

        # Strategy 1: derive list name from probed field (e.g. 'first_use_udf' → 'list_node.first_use_udf')
        if first_use_field:
            candidates = [
                f"list_node.{first_use_field}",
                f"list_node.{first_use_field.replace('_udf', '')}",
                first_use_field,
            ]
            for c in candidates:
                opts = _try(c)
                if opts:
                    return opts

        # Strategy 2: known candidate list names
        for list_name in [
            "list_node.first_use_udf",
            "list_node.first_use_sop_udf",
            "list_node.first_use",
            "list_node.sop_udf",
            "list_node.pu_udf",
            "list_node.first_introduction_udf",
        ]:
            opts = _try(list_name)
            if opts:
                return opts

        # Strategy 3: scan list roots, look for names matching 'first_use' or 'sop'
        try:
            r = self.session.get(
                f"{BASE}/list_nodes",
                params={"fields": "id,name,logical_name",
                        "query": '"list_root={null}"',
                        "limit": 200},
                verify=False, timeout=15)
            if r.ok:
                for root in r.json().get("data", []):
                    ln = (root.get("logical_name") or "").lower()
                    nm = (root.get("name") or "").lower()
                    if any(k in ln or k in nm for k in
                           ["first_use", "firstuse", "sop", "pu_udf",
                            "first_introduction", "first use"]):
                        lname = root.get("logical_name") or root.get("name") or ""
                        opts = _try(lname)
                        if opts:
                            return opts
        except Exception:
            pass

        # Strategy 4: read distinct values already present on navigation defects
        if first_use_field:
            try:
                r = self.session.get(
                    f"{BASE}/defects",
                    params={"fields": f"id,{first_use_field}",
                            "query":  self._build_query(),
                            "limit":  50},
                    verify=False, timeout=20)
                if r.ok:
                    seen: set = set()
                    node_ids: dict = {}  # name → id (first seen)
                    for rec in r.json().get("data", []):
                        val = rec.get(first_use_field)
                        if val is None:
                            continue
                        if isinstance(val, dict) and "name" in val:
                            n = val["name"]
                            seen.add(n)
                            node_ids.setdefault(n, val.get("id"))
                        elif isinstance(val, dict):
                            for item in val.get("data", []):
                                if isinstance(item, dict) and "name" in item:
                                    n = item["name"]
                                    seen.add(n)
                                    node_ids.setdefault(n, item.get("id"))
                        elif isinstance(val, str) and val.strip():
                            seen.add(val.strip())
                    if seen:
                        self._log(
                            f"[fetch_first_use] Strategy4: Werte aus OCTANE-Defects: "
                            f"{sorted(seen)}", "INFO")
                        # Bootstrap list_root from first found node
                        for nm, nid in node_ids.items():
                            if nid:
                                try:
                                    rn = self.session.get(
                                        f"{BASE}/list_nodes/{nid}",
                                        params={"fields": "id,name,list_root"},
                                        verify=False, timeout=10)
                                    if rn.ok:
                                        lr = rn.json().get("list_root")
                                        if isinstance(lr, dict) and lr.get("id"):
                                            self._first_use_list_root = lr["id"]
                                            self._log(
                                                f"[fetch_first_use] Bootstrap list_root "
                                                f"id={lr['id']} via node '{nm}'", "INFO")
                                except Exception:
                                    pass
                                break
                        # Apply SOP filter; if it strips everything, keep raw values
                        filtered = _filter_sop(sorted(seen))
                        return filtered if filtered else sorted(seen)
            except Exception:
                pass

        # Diagnostic: dump all list roots so the user can find the correct name
        try:
            roots = self._all_list_roots()
            if roots:
                root_names = sorted(
                    (r.get("logical_name") or r.get("name") or "") for r in roots
                    if r.get("logical_name") or r.get("name")
                )
                self._log(
                    f"[first_use] Alle {len(root_names)} bekannten List-Roots: "
                    f"{', '.join(root_names)}",
                    "INFO"
                )
                self._log(
                    "[first_use] Tipp: Setze FIRST_USE_LIST_OVERRIDE auf den "
                    "passenden Wert aus der Liste oben.",
                    "WARNING"
                )
        except Exception:
            pass

        return []
    
    def _discover_all_defect_keys(self) -> List[str]:
        """Fetch one defect with NO field restriction so OCTANE returns every field.
        Falls back to a wide candidate list if the unrestricted fetch returns nothing useful."""
        all_keys: List[str] = []
        # Strategy A: unrestricted – OCTANE may return all fields
        try:
            r = self.session.get(f"{BASE}/defects",
                                 params={"query": self._build_query(), "limit": 1},
                                 verify=False, timeout=15)
            if r.ok:
                data = r.json().get("data", [])
                if data:
                    all_keys = sorted(data[0].keys())
        except Exception:
            pass

        # Strategy B: explicit wide candidate list if we got very few keys back
        if len(all_keys) < 10:
            cands = ("id,name,first_use_sop_of_function_udf,first_use_udf,first_use_sop_udf,"
                     "first_use,sop_udf,pu_udf,"
                     "involved_i_step1_udf,involved_i_step_udf,involved_i_step,"
                     "involved_i_steps,involved_i_steps_udf,"
                     "involved_isteps,istep_udf,i_step_udf,i_steps_udf,i_steps,"
                     "affected_i_step_udf,affected_i_steps_udf,relevant_i_step_udf,"
                     "target_i_step_udf,target_i_steps_udf,"
                     "solution_cluster_udf,solution_responsible_udf,blocking_reason_udf,"
                     "creation_time,owner,phase,assigned_ecu_udf")
            try:
                r = self.session.get(f"{BASE}/defects",
                                     params={"fields": cands,
                                             "query": self._build_query(), "limit": 1},
                                     verify=False, timeout=15)
                if r.ok:
                    data = r.json().get("data", [])
                    if data:
                        cand_keys = sorted(data[0].keys())
                        # Merge unique keys from both strategies
                        for k in cand_keys:
                            if k not in all_keys:
                                all_keys.append(k)
                        all_keys.sort()
            except Exception:
                pass
        return all_keys

    def _probe_extra_fields(self) -> Dict[str, Optional[str]]:
        """Discover First Use and Involved I-Step field names.

        Strategy 1: OCTANE metadata/fields API (tries two known URL variants).
        Strategy 2: Fetch our actual navigation defects (proven to return data) with
                    ALL candidate field names in a single request — check which fields
                    actually come back populated.  This is the most reliable approach
                    because OCTANE omits unknown/null fields from responses entirely.
        """
        result: Dict[str, Optional[str]] = {"first_use": None, "istep": None,
                                             "target_istep": None, "blocking_reason": None}

        first_use_cands     = ["first_use_sop_of_function_udf", "first_use_udf",
                                "first_use_sop_udf", "first_use",
                                "sop_udf", "pu_udf", "first_introduction_udf"]
        target_istep_cands  = ["target_i_step_udf", "target_i_step", "target_istep",
                                "target_istep_udf", "target_i_steps", "target_i_steps_udf"]
        istep_cands         = ["involved_i_step1_udf",
                                "involved_i_step_udf",
                                "involved_i_step",
                                "involved_i_steps", "involved_i_steps_udf", "involved_isteps",
                                "istep_udf", "i_step_udf",
                                "involved_versions_udf", "involved_version_udf",
                                "affected_i_step_udf", "affected_i_steps_udf"]
        blocking_cands      = ["blocking_reason_udf", "blocking_reason",
                                "bl_reason_udf", "reject_reason_udf"]

        # ── Strategy 1: metadata API (two known endpoint variants) ──────────
        for meta_url in [
            f"{BASE}/metadata/fields?entity_name=defect",
            f"{BASE}/metadata/types/defect/fields",
        ]:
            try:
                r = self.session.get(meta_url, verify=False, timeout=10)
                if r.ok:
                    body  = r.json()
                    items = body.get("data", body.get("fields", []))
                    for fdef in items:
                        fname = fdef.get("name", "") or ""
                        label = (fdef.get("label", "") or "").lower()
                        fl    = fname.lower()
                        if result["first_use"] is None and (
                                any(k in fl for k in ["first_use", "sop_udf", "pu_udf"]) or
                                any(k in label for k in
                                    ["first use", "sop", "first introduction"])):
                            result["first_use"] = fname
                        if result["istep"] is None and "target" not in fl and (
                                any(k in fl for k in
                                    ["involved_i_step", "involved_istep"]) or
                                ("involved" in label and
                                 any(k in label for k in ["i-step", "i step", "istep"]))):
                            result["istep"] = fname
                        if result["target_istep"] is None and (
                                any(k in fl for k in
                                    ["target_i_step", "target_istep"]) or
                                any(k in label for k in
                                    ["target i-step", "target i step", "target istep"])):
                            result["target_istep"] = fname
                        if result["blocking_reason"] is None and (
                                any(k in fl for k in
                                    ["blocking_reason", "bl_reason", "reject_reason"]) or
                                any(k in label for k in
                                    ["blocking reason", "reject reason"])):
                            result["blocking_reason"] = fname
                    if all(result[k] for k in result):
                        return result
            except Exception:
                pass

        # ── Strategy 2: fetch one navigation defect without field restriction ─
        # OCTANE returns ALL fields when no `fields` param is given.  We then
        # pattern-match the actual key names – completely immune to name guessing.
        if result["first_use"] is None or result["istep"] is None or result["target_istep"] is None:
            try:
                r = self.session.get(
                    f"{BASE}/defects",
                    params={"query": self._build_query(), "limit": 5},
                    verify=False, timeout=20)
                if r.ok:
                    all_keys: List[str] = []
                    for rec in r.json().get("data", []):
                        for k in rec.keys():
                            if k not in all_keys:
                                all_keys.append(k)

                    def _key_matches(k: str, patterns: List[str]) -> bool:
                        kl = k.lower()
                        return any(p in kl for p in patterns)

                    # Prefer involved_i_step1_udf over other i_step variants
                    if "involved_i_step1_udf" in all_keys:
                        result["istep"] = "involved_i_step1_udf"
                    for k in all_keys:
                        if result["first_use"] is None and _key_matches(
                                k, ["first_use_sop", "first_use", "sop_udf", "pu_udf",
                                    "first_introduction"]):
                            result["first_use"] = k
                        if result["istep"] is None and "target" not in k.lower() and _key_matches(
                                k, ["involved_i_step", "involved_istep"]):
                            result["istep"] = k
                        if result["target_istep"] is None and _key_matches(
                                k, ["target_i_step", "target_istep"]):
                            result["target_istep"] = k
                        if result["blocking_reason"] is None and _key_matches(
                                k, ["blocking_reason", "bl_reason", "reject_reason"]):
                            result["blocking_reason"] = k
            except Exception:
                pass

        # ── Strategy 3: fetch actual navigation defects with all candidates ─
        # Fallback: request candidates explicitly – stops when OCTANE accepts them.
        if result["first_use"] is None or result["istep"] is None or \
                result["target_istep"] is None or result["blocking_reason"] is None:
            all_cands = first_use_cands + istep_cands + target_istep_cands + blocking_cands
            # Try in subsets so one unknown name doesn't kill the whole request
            for chunk in [all_cands[:8], all_cands[8:16], all_cands[16:]]:
                try:
                    r = self.session.get(
                        f"{BASE}/defects",
                        params={"fields": "id," + ",".join(chunk),
                                "query":  self._build_query(),
                                "limit":  10},
                        verify=False, timeout=20)
                    if not r.ok:
                        continue

                    def _has_data(val: Any) -> bool:
                        if val is None:
                            return False
                        if isinstance(val, str):
                            return bool(val.strip())
                        if isinstance(val, dict):
                            return bool(val.get("name") or
                                        (isinstance(val.get("data"), list)
                                         and len(val["data"]) > 0))
                        if isinstance(val, list):
                            return len(val) > 0
                        return bool(val)

                    for rec in r.json().get("data", []):
                        for fname in first_use_cands:
                            if result["first_use"] is None and _has_data(rec.get(fname)):
                                result["first_use"] = fname
                        for fname in istep_cands:
                            if result["istep"] is None and _has_data(rec.get(fname)):
                                result["istep"] = fname
                        for fname in target_istep_cands:
                            if result["target_istep"] is None and _has_data(rec.get(fname)):
                                result["target_istep"] = fname
                        for fname in blocking_cands:
                            if result["blocking_reason"] is None and _has_data(rec.get(fname)):
                                result["blocking_reason"] = fname
                        if all(result[k] for k in result):
                            break
                except Exception:
                    pass

        return result

    @staticmethod
    def _parse_pu_from_istep(istep_name: str) -> str:
        """Extract PU string from I-Step name, e.g. 'G045-24-07-480' → '24-07'."""
        if not istep_name:
            return ""
        parts = istep_name.strip().split("-")
        # Expect format: {derivative}-{YY}-{MM}-{seq}  e.g. G045-24-07-480
        if len(parts) >= 3:
            return f"{parts[1]}-{parts[2]}"
        return istep_name

    def get_sop_from_defect(self, defect: Dict[str, Any],
                            first_use_field: Optional[str],
                            istep_field: Optional[str]) -> str:
        """Read First Use / SOP from defect data.
        Handles all OCTANE field wrapper formats:
          - plain string
          - {"type": "list_node", "name": "..."}   (single reference)
          - {"type": "list_nodes", "data": [...]}   (multi-value reference)
          - plain list
        """

        def _extract_name(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, str):
                return val.strip()
            if isinstance(val, dict):
                # Single reference: {"type": "list_node", "id": "...", "name": "..."}
                if "name" in val:
                    return str(val["name"]).strip()
                # Multi-value wrapper: {"type": "list_nodes", "data": [...]}
                inner = val.get("data", [])
                if isinstance(inner, list) and inner:
                    first = inner[0]
                    return str(first.get("name", "") if isinstance(first, dict)
                               else first).strip()
            if isinstance(val, list) and val:
                first = val[0]
                return str(first.get("name", "") if isinstance(first, dict)
                           else first).strip()
            return ""

        # 1. Try the dedicated First Use field
        if first_use_field:
            name = _extract_name(defect.get(first_use_field))
            if name:
                return name

        # 2. Parse SOP from Involved I-Step field
        if istep_field:
            name = _extract_name(defect.get(istep_field))
            if name:
                pu = self._parse_pu_from_istep(name)
                if pu:
                    return pu

        return ""
    
    def update_defect(self, defect_id: str, updates: Dict[str, Any]) -> Tuple[bool, str]:
        """Send a single PUT to update one or more fields of a defect.
        Per the REST API spec: only the fields to be changed go in the body.
        Each call is its own HTTP request so a bad field name in one setter
        cannot silently prevent other fields from being written."""
        try:
            r = self.session.put(
                f"{BASE}/defects/{defect_id}",
                json=updates,
                verify=False,
                timeout=30
            )
            if r.status_code == 401:
                self._session_expired = True
                self._log("Session abgelaufen (401) – bitte neu einloggen.", "ERROR")
                return False, "Session abgelaufen (401)"
            if r.ok:
                return True, f"OK (HTTP {r.status_code})"
            else:
                return False, f"HTTP {r.status_code}: {r.text[:300]}"
        except requests.RequestException as e:
            return False, f"Request-Fehler: {e}"

    def _set_by_name_search(self, defect_id: str, field_name: str,
                             value: str) -> Tuple[bool, str]:
        """Set a list_node field WITHOUT knowing the list_root.
        Searches list_nodes by name directly, then tries each match with PUT.
        OCTANE will reject a node that belongs to the wrong list, so we try all
        candidates until one PUT succeeds.  Caches successful node IDs."""
        cache_key = f"_nsearch:{field_name}:{value.lower()}"
        cached_id = getattr(self, '_nsearch_cache', {}).get(cache_key)
        if cached_id:
            ok, msg = self.update_defect(defect_id,
                {field_name: {"type": "list_node", "id": cached_id}})
            if ok:
                return ok, msg
            # Cached ID no longer valid — fall through to fresh search

        if self._session_expired:
            return False, "Session abgelaufen"
        try:
            escaped = value.replace("'", "\\'")
            r = self.session.get(
                f"{BASE}/list_nodes",
                params={"fields": "id,name",
                        "query": f'"name=\'{escaped}\'"',
                        "limit": 50},
                verify=False, timeout=20)
            if r.status_code == 401:
                self._session_expired = True
                return False, "Session abgelaufen (401)"
            if not r.ok:
                return False, f"list_nodes Suche HTTP {r.status_code}: {r.text[:200]}"

            nodes = r.json().get("data", [])
            # Exact matches first, then case-insensitive substring
            exact   = [n for n in nodes if (n.get("name") or "").lower() == value.lower()]
            partial = [n for n in nodes if n not in exact and
                       value.lower() in (n.get("name") or "").lower()]
            to_try = exact + partial

            if not to_try:
                return False, f"Kein list_node mit Name '{value}' in OCTANE gefunden"

            last_err = ""
            for node in to_try:
                ok, msg = self.update_defect(defect_id,
                    {field_name: {"type": "list_node", "id": node["id"]}})
                if ok:
                    if not hasattr(self, '_nsearch_cache'):
                        self._nsearch_cache: Dict[str, str] = {}
                    self._nsearch_cache[cache_key] = node["id"]
                    return True, f"OK ('{node['name']}', id={node['id']})"
                last_err = msg

            names = [n.get("name", "") for n in to_try[:5]]
            return False, (f"'{value}' gefunden ({len(to_try)} Kandidaten: {names}) "
                           f"aber PUT fehlgeschlagen: {last_err}")
        except Exception as e:
            return False, f"Fehler: {e}"

    def begin_batch(self, defect_id: str) -> None:
        """No-op: each update_defect call sends its own PUT immediately."""
        pass

    def commit_batch(self) -> Tuple[bool, str]:
        """No-op: all field updates were already written by their individual PUT calls."""
        return True, "ok"

    def cancel_batch(self) -> None:
        """No-op."""
        pass
    
    def _set_phase_by_name_search(self, defect_id: str,
                                   phase_prefix: str) -> Tuple[bool, str]:
        """Set defect phase when the cached phase_id is wrong or missing.

        Strategy A (primary): Query existing defects that are already in the
        target phase.  OCTANE returns the phase node ID embedded in the defect
        response — this is guaranteed to be the correct ID for the defect entity
        type in this workspace, with no list_root ambiguity at all.

        Strategy B (fallback): Probe list_nodes by name prefix; try each until
        OCTANE accepts one."""
        if self._session_expired:
            return False, "Session abgelaufen"

        import re as _re
        _ppat = _re.compile(r'^\d{2}[-\s]')
        escaped = phase_prefix.replace("'", "\\'")

        # ── Strategy P: OCTANE /phases API with type='phase' ─────────────────
        # In OCTANE (v12+) phases are first-class entities at /phases, NOT
        # list_nodes grounded in a list_root.  The PUT body must use
        # {"type": "phase", "id": "..."} — using "list_node" always fails.
        try:
            rp = self.session.get(
                f"{BASE}/phases",
                params={"fields": "id,name",
                        "query": f'"name=\'{escaped}*\'"',
                        "limit": 50},
                verify=False, timeout=15)
            if rp.status_code == 401:
                self._session_expired = True
                return False, "Session abgelaufen (401)"
            if rp.ok:
                phases_data = rp.json().get("data", [])
                self._log(
                    f"[phase] Strategy-P /phases API: {len(phases_data)} Treffer "
                    f"für '{phase_prefix}*': "
                    f"{[p.get('name') for p in phases_data]}", "INFO")
                for p in phases_data:
                    nm  = (p.get("name") or "").strip()
                    nid = (p.get("id")   or "").strip()
                    if not nid or not nm.startswith(phase_prefix):
                        continue
                    self._log(
                        f"[phase] Strategy-P: versuche '{nm}' id={nid}", "INFO")
                    ok, msg = self.update_defect(defect_id,
                        {"phase": {"type": "phase", "id": nid}})
                    if ok:
                        self.phase_cache[nm] = nid
                        self._log(
                            f"[phase] ✅ Strategy-P: '{nm}' gesetzt", "INFO")
                        return True, f"OK ('{nm}')"
                    if "401" in msg:
                        self._session_expired = True
                        return False, "Session abgelaufen (401)"
                    self._log(
                        f"[phase] Strategy-P: PUT fehlgeschlagen '{nm}': {msg}",
                        "WARNING")
                # Also try without query filter (full list, filter client-side)
                if not phases_data:
                    rp_all = self.session.get(
                        f"{BASE}/phases",
                        params={"fields": "id,name", "limit": 200},
                        verify=False, timeout=15)
                    if rp_all.ok:
                        all_phases = rp_all.json().get("data", [])
                        self._log(
                            f"[phase] Strategy-P (alle): {len(all_phases)} Phases: "
                            f"{[p.get('name') for p in all_phases[:20]]}", "INFO")
                        for p in all_phases:
                            nm  = (p.get("name") or "").strip()
                            nid = (p.get("id")   or "").strip()
                            if not nid or not nm.startswith(phase_prefix):
                                continue
                            ok, msg = self.update_defect(defect_id,
                                {"phase": {"type": "phase", "id": nid}})
                            if ok:
                                self.phase_cache[nm] = nid
                                return True, f"OK ('{nm}')"
                            if "401" in msg:
                                self._session_expired = True
                                return False, "Session abgelaufen (401)"
        except Exception as e:
            self._log(f"[phase] Strategy-P Fehler: {e}", "WARNING")
        if self._session_expired:
            return False, "Session abgelaufen"

        def _try_phase_node(source_label: str, nid: str) -> Tuple[bool, str]:
            """Resolve node name (if needed), verify prefix, PUT, cache on success.
            A deferred reference like 'phase.defect.deferred' is a placeholder
            OCTANE returns when the phase field is not expanded — it is never
            a valid node id.  Skip it immediately."""
            if not nid:
                return False, "keine id"
            # Deferred refs contain dots and are never usable node ids
            if "." in nid:
                self._log(
                    f"[phase] {source_label}: deferred ref '{nid}' ignoriert",
                    "WARNING")
                return False, f"deferred ref: {nid}"
            nm = ""
            try:
                rn = self.session.get(f"{BASE}/list_nodes/{nid}",
                                      params={"fields": "id,name"},
                                      verify=False, timeout=10)
                if rn.status_code == 401:
                    self._session_expired = True
                    return False, "Session abgelaufen (401)"
                if rn.ok:
                    nm = (rn.json().get("name") or "").strip()
            except Exception:
                pass
            # If we could not fetch the name, still try the id — the query already
            # filtered by prefix so the id should be correct.
            if nm and phase_prefix and not nm.startswith(phase_prefix):
                self._log(
                    f"[phase] {source_label}: '{nm}' passt nicht zu prefix '{phase_prefix}'",
                    "WARNING")
                return False, f"falscher prefix: '{nm}'"
            self._log(
                f"[phase] {source_label}: '{nm or nid}' id={nid}", "INFO")
            ok, msg = self.update_defect(defect_id,
                {"phase": {"type": "list_node", "id": nid}})
            if ok:
                if nm:
                    self.phase_cache[nm] = nid
                return True, f"OK ('{nm or nid}')"
            if "401" in msg:
                self._session_expired = True
                return False, "Session abgelaufen (401)"
            self._log(
                f"[phase] {source_label}: PUT fehlgeschlagen id={nid}: {msg}", "WARNING")
            return False, msg

        # ── Strategy Z+A: two-step real-ID lookup ────────────────────────────
        # Problem: OCTANE's COLLECTION endpoint (GET /defects?fields=id,phase&...)
        # always returns 'phase.defect.deferred' instead of the real node ID.
        # The SINGULAR endpoint (GET /defects/{id}?fields=id,phase) DOES return
        # the real phase node ID (as Strategy 0 in _get_phase_id proves).
        #
        # Two-step workaround:
        #   Step 1 – query defects in target phase requesting only 'id' (no phase
        #            field → no deferred ref); collect candidate defect IDs.
        #            Also include known seed IDs for specific phase prefixes.
        #   Step 2 – fetch each candidate via the singular endpoint with
        #            fields=id,phase; extract the real phase node ID and PUT it.
        _PHASE_SEEDS: Dict[str, str] = {"04": "2717293"}
        candidate_ids: List[str] = []
        if phase_prefix in _PHASE_SEEDS:
            candidate_ids.append(_PHASE_SEEDS[phase_prefix])

        # Step 1: find more candidate defect IDs (fields=id only, no deferred-ref)
        try:
            r_find = self.session.get(
                f"{BASE}/defects",
                params={"fields": "id",
                        "query": f'"phase={{name=\'{escaped}*\'}}"',
                        "limit": 5},
                verify=False, timeout=15)
            if r_find.status_code == 401:
                self._session_expired = True
                return False, "Session abgelaufen (401)"
            if r_find.ok:
                for d in r_find.json().get("data", []):
                    did = str(d.get("id", "")).strip()
                    if did and did not in candidate_ids:
                        candidate_ids.append(did)
        except Exception as e:
            self._log(f"[phase] Step1-Query Fehler: {e}", "WARNING")
        if self._session_expired:
            return False, "Session abgelaufen"

        self._log(
            f"[phase] Step2: {len(candidate_ids)} Kandidaten-Defects "
            f"für Phase '{phase_prefix}*': {candidate_ids[:6]}", "INFO")

        # Step 2: fetch each candidate singularly → real phase.id
        for cid in candidate_ids:
            if self._session_expired:
                return False, "Session abgelaufen"
            try:
                r2 = self.session.get(f"{BASE}/defects/{cid}",
                                      params={"fields": "id,phase"},
                                      verify=False, timeout=10)
                if r2.status_code == 401:
                    self._session_expired = True
                    return False, "Session abgelaufen (401)"
                if not r2.ok:
                    self._log(
                        f"[phase] Step2: defect {cid} → HTTP {r2.status_code}",
                        "WARNING")
                    continue
                ph = r2.json().get("phase")
                if not isinstance(ph, dict):
                    self._log(
                        f"[phase] Step2: defect {cid} → kein phase dict ({ph!r})",
                        "WARNING")
                    continue
                nid = (ph.get("id") or "").strip()
                if not nid or "." in nid:
                    self._log(
                        f"[phase] Step2: defect {cid} → deferred ({nid!r}), "
                        f"versuche fields-losen Abruf...", "WARNING")
                    # fields=id,phase yields deferred refs on this OCTANE instance.
                    # Retry WITHOUT any fields parameter — full entity response
                    # returns all fields eagerly resolved, including the real phase id.
                    try:
                        r2b = self.session.get(f"{BASE}/defects/{cid}",
                                               verify=False, timeout=15)
                        if r2b.status_code == 401:
                            self._session_expired = True
                            return False, "Session abgelaufen (401)"
                        if r2b.ok:
                            ph2 = r2b.json().get("phase")
                            if isinstance(ph2, dict):
                                nid2 = (ph2.get("id") or "").strip()
                                nm2  = (ph2.get("name") or "").strip()
                                self._log(
                                    f"[phase] Step2-full: defect {cid} → "
                                    f"phase id={nid2!r} name={nm2!r}", "INFO")
                                if nid2 and "." not in nid2:
                                    ok, msg = _try_phase_node(
                                        f"Step2-full (defect {cid})", nid2)
                                    if ok:
                                        return True, msg
                    except Exception as e2:
                        self._log(
                            f"[phase] Step2-full Fehler defect {cid}: {e2}", "WARNING")
                    continue
                ok, msg = _try_phase_node(f"Step2 (defect {cid})", nid)
                if ok:
                    return True, msg
            except Exception as e:
                self._log(f"[phase] Step2 Fehler defect {cid}: {e}", "WARNING")

        if not candidate_ids:
            self._log(
                f"[phase] Phase '{phase_prefix}': keine Kandidaten-Defects gefunden",
                "WARNING")
        if self._session_expired:
            return False, "Session abgelaufen"

        # ── Strategy N: name-based PUT (no node-id lookup needed) ────────────
        # Try sending the phase by name rather than ID.  Some OCTANE versions
        # accept {"type": "list_node", "name": "..."} directly.
        for name_candidate in [f"{phase_prefix}-In Progress",
                                f"{phase_prefix} - In Progress",
                                phase_prefix]:
            if self._session_expired:
                break
            try:
                self._log(
                    f"[phase] Strategy-N: name-PUT '{name_candidate}'", "INFO")
                ok, msg = self.update_defect(defect_id,
                    {"phase": {"type": "list_node", "name": name_candidate}})
                if ok:
                    return True, f"OK (name='{name_candidate}')"
                if "401" in msg:
                    self._session_expired = True
                    return False, "Session abgelaufen (401)"
                self._log(
                    f"[phase] Strategy-N: '{name_candidate}' fehlgeschlagen: {msg}",
                    "WARNING")
            except Exception as e:
                self._log(f"[phase] Strategy-N Fehler: {e}", "WARNING")
        if self._session_expired:
            return False, "Session abgelaufen"

        # ── Strategy B: probe list_nodes by name prefix ───────────────────────
        try:
            r = self.session.get(
                f"{BASE}/list_nodes",
                params={"fields": "id,name",
                        "query": f'"name=\'{escaped}*\'"',
                        "limit": 100},
                verify=False, timeout=20)
            if r.status_code == 401:
                self._session_expired = True
                return False, "Session abgelaufen (401)"
            if not r.ok:
                return False, f"list_nodes Suche HTTP {r.status_code}: {r.text[:200]}"
            nodes = r.json().get("data", [])
            candidates = [n for n in nodes
                          if _ppat.match(n.get("name", ""))
                          and n.get("name", "").startswith(phase_prefix)]
            self._log(
                f"[phase] Strategy-B name-search '{phase_prefix}*': "
                f"{len(candidates)} Kandidaten: "
                f"{[n.get('name') for n in candidates]}", "INFO")
            if not candidates:
                return False, f"Kein Phase-Node mit Prefix '{phase_prefix}' gefunden"
            last_err = ""
            for node in candidates:
                ok, msg = self.update_defect(defect_id,
                    {"phase": {"type": "list_node", "id": node["id"]}})
                if ok:
                    self.phase_cache[node["name"]] = node["id"]
                    self._log(
                        f"[phase] ✅ '{node['name']}' via Strategy-B "
                        f"(id={node['id']})", "INFO")
                    return True, f"OK ('{node['name']}')"
                if "401" in msg:
                    self._session_expired = True
                    return False, "Session abgelaufen (401)"
                last_err = msg
            names = [n.get("name", "") for n in candidates[:5]]
            return False, (f"Phase '{phase_prefix}*' — Strategy A+B fehlgeschlagen. "
                           f"Kandidaten: {names}. Letzter Fehler: {last_err}")
        except Exception as e:
            return False, f"Fehler: {e}"

    def set_phase(self, defect_id: str, phase_name: str) -> Tuple[bool, str]:
        """Set defect phase.
        Primary: lookup cached phase id → PUT.
        Fallback: if OCTANE returns phase_does_not_exist, the cached id belongs
        to the wrong entity type's list — use name-prefix probe search instead,
        which lets OCTANE validate and accept only the correct node."""
        if self._session_expired:
            return False, "Session abgelaufen"
        phase_id = self._get_phase_id(phase_name, defect_id=defect_id)
        if phase_id:
            # Use the entity type determined during cache discovery
            ph_type = self._phase_entity_type
            ok, msg = self.update_defect(defect_id, {
                "phase": {"type": ph_type, "id": phase_id}
            })
            if ok:
                return ok, msg
            # If that type failed, try the other type before giving up
            alt_type = "list_node" if ph_type == "phase" else "phase"
            ok2, msg2 = self.update_defect(defect_id, {
                "phase": {"type": alt_type, "id": phase_id}
            })
            if ok2:
                self._phase_entity_type = alt_type
                return ok2, msg2
            self._log(
                f"[phase] type='{ph_type}' fehlgeschlagen: {msg[:80]}", "WARNING")
            self._log(
                f"[phase] type='{alt_type}' fehlgeschlagen: {msg2[:80]}", "WARNING")
            if "phase_does_not_exist" not in msg and "phase_does_not_exist" not in msg2:
                return False, msg
            # Cached id is wrong — clear cache so next ticket re-discovers
            self._log(
                f"[phase] phase_does_not_exist (id={phase_id}) → "
                "Name-Search Fallback", "WARNING")
            self.phase_cache = {}
            prev_root = self._field_root_cache.pop("defect.phase", None)
            if prev_root:
                self._field_root_cache[f"_bad.{prev_root}"] = None
        else:
            self._log(f"[phase] phase_id nicht im Cache – direkt Name-Search", "INFO")
        # Fallback: probe by name prefix (reliable regardless of list_root ambiguity)
        return self._set_phase_by_name_search(defect_id, phase_name)
    
    def set_owner(self, defect_id: str, owner_name: str) -> Tuple[bool, str]:
        """Set defect owner"""
        if self._session_expired:
            return False, "Session abgelaufen"
        user_id = self._get_user_id(owner_name)
        if not user_id:
            return False, f"User '{owner_name}' nicht in OCTANE gefunden (Suche: '{owner_name.split(',')[0].strip()}')"
        
        return self.update_defect(defect_id, {
            "owner": {"type": "workspace_user", "id": user_id}
        })
    
    def set_target_istep(self, defect_id: str, target_istep_field: str,
                          value: str) -> Tuple[bool, str]:
        """Set the Target I-Step field to a list_node value by name."""
        return self._set_by_name_search(defect_id, target_istep_field, value)

    def set_solution_responsible(self, defect_id: str, value: str) -> Tuple[bool, str]:
        """Set solution responsible field.
        Primary: direct name search (no list_root required).
        Fallback: known logical-name candidates → brute-force scan."""
        if self._session_expired:
            return False, "Session abgelaufen"
        # ── Primary: search list_node by value name, try each with PUT ────────
        for fn in ["solution_responsible_udf", "solution_responsible",
                   "sol_responsible_udf", "responsible_udf"]:
            ok, msg = self._set_by_name_search(defect_id, fn, value)
            if ok:
                return ok, msg
        # ── Fallback: legacy list_root-based candidates ───────────────────────
        candidates: List[Tuple[str, str]] = [
            ("list_node.solution_responsible_udf", "solution_responsible_udf"),
            ("list_node.solution_responsible",     "solution_responsible"),
            ("list_node.sol_responsible_udf",      "sol_responsible_udf"),
            ("list_node.responsible_udf",          "responsible_udf"),
        ]
        seen: set = set()
        best_avail: Optional[Tuple[str, list]] = None
        for logical_name, fname in candidates:
            if logical_name in seen:
                continue
            seen.add(logical_name)
            items = self._fetch_list_values(logical_name)
            if not items:
                continue
            # List found — check for value (exact then substring)
            for nm, nid in items.items():
                if value.lower() == nm.lower():
                    return self.update_defect(defect_id,
                        {fname: {"type": "list_node", "id": nid}})
            for nm, nid in items.items():
                if value.lower() in nm.lower():
                    return self.update_defect(defect_id,
                        {fname: {"type": "list_node", "id": nid}})
            # Value not in this list — remember for error message, keep looking
            if best_avail is None:
                best_avail = (logical_name, list(items.keys()))
        # ── 3. Brute-force: scan ALL workspace roots by keyword ──────────────
        import re as _re
        _qnr_pat = _re.compile(r'^q[0-9a-z]{4,}$', _re.I)

        def _is_qnr_list(root_name: str) -> bool:
            """Return True if the list consists entirely of QNR user-style codes
            (e.g. q505071) — these are workspace_user references, not text choices."""
            vals = self._fetch_list_values(root_name)
            if not vals:
                return False
            return all(_qnr_pat.match(k) for k in list(vals.keys())[:10])

        scan_roots = [r for r in self._find_list_roots_by_keyword(
                          "responsible", "solution_responsible", "sol_resp")
                      if r not in seen and not _is_qnr_list(r)]
        result = self._find_value_in_roots(scan_roots, value)
        if result:
            fname, nid = result
            return self.update_defect(defect_id,
                {fname: {"type": "list_node", "id": nid}})
        # Found roots but value wasn't there — show what is available
        for root in scan_roots:
            avail = self._fetch_list_values(root)
            if avail:
                best_avail = best_avail or (root, list(avail.keys()))
        tried = list(seen) + scan_roots

        # ── 4. Value-first discovery: find list_node then trace to field ──
        found = self._find_defect_field_for_listnode(value)
        if found:
            field_name, nid = found
            if field_name.startswith("__lr_id:"):
                # Metadata unavailable — probe candidate field names directly
                ok, msg = self._try_field_candidates_with_node(defect_id, nid, [
                    "solution_responsible_udf", "solution_responsible",
                    "sol_responsible_udf", "responsible_udf",
                ])
                if ok:
                    return True, msg
            else:
                return self.update_defect(defect_id,
                    {field_name: {"type": "list_node", "id": nid}})
        if best_avail:
            return False, (f"Solution Responsible '{value}' nicht gefunden. "
                           f"Verfügbar ({best_avail[0]}): {best_avail[1][:8]}")
        return False, (f"Solution Responsible Feld nicht gefunden "
                       f"(Wert: '{value}', gesucht in: {tried[:6]})")

    def set_solution_cluster(self, defect_id: str, value: str) -> Tuple[bool, str]:
        """Set solution cluster field.
        Primary: direct name search (no list_root required).
        Fallback: known logical-name candidates → brute-force scan."""
        if self._session_expired:
            return False, "Session abgelaufen"
        # ── Primary: search list_node by value name, try each with PUT ────────
        for fn in ["solution_cluster_udf", "solution_cluster",
                   "cluster_udf", "sol_cluster_udf"]:
            ok, msg = self._set_by_name_search(defect_id, fn, value)
            if ok:
                return ok, msg
        # ── Fallback: legacy list_root-based candidates ───────────────────────
        candidates: List[Tuple[str, str]] = [
            ("list_node.solution_cluster_udf", "solution_cluster_udf"),
            ("list_node.solution_cluster",     "solution_cluster"),
            ("list_node.cluster_udf",          "cluster_udf"),
            ("list_node.sol_cluster_udf",      "sol_cluster_udf"),
        ]
        seen: set = set()
        best_avail: Optional[Tuple[str, list]] = None
        for logical_name, fname in candidates:
            if logical_name in seen:
                continue
            seen.add(logical_name)
            items = self._fetch_list_values(logical_name)
            if not items:
                continue
            # List found — check for value (exact then substring)
            for nm, nid in items.items():
                if value.lower() == nm.lower():
                    return self.update_defect(defect_id,
                        {fname: {"type": "list_node", "id": nid}})
            for nm, nid in items.items():
                if value.lower() in nm.lower():
                    return self.update_defect(defect_id,
                        {fname: {"type": "list_node", "id": nid}})
            # Value not in this list — remember for error message, keep looking
            if best_avail is None:
                best_avail = (logical_name, list(items.keys()))
        # ── 3. Brute-force: scan ALL workspace roots by keyword ──────────────
        scan_roots = [r for r in self._find_list_roots_by_keyword(
                          "cluster", "solution_cluster", "sol_cluster")
                      if r not in seen]
        result = self._find_value_in_roots(scan_roots, value)
        if result:
            fname, nid = result
            return self.update_defect(defect_id,
                {fname: {"type": "list_node", "id": nid}})
        # Found roots but value wasn't there — show what is available
        for root in scan_roots:
            avail = self._fetch_list_values(root)
            if avail:
                best_avail = best_avail or (root, list(avail.keys()))
        tried = list(seen) + scan_roots

        # ── 4. Value-first discovery: find list_node then trace to field ──
        found = self._find_defect_field_for_listnode(value)
        if found:
            field_name, nid = found
            if field_name.startswith("__lr_id:"):
                ok, msg = self._try_field_candidates_with_node(defect_id, nid, [
                    "solution_cluster_udf", "solution_cluster",
                    "cluster_udf", "sol_cluster_udf",
                ])
                if ok:
                    return True, msg
            else:
                return self.update_defect(defect_id,
                    {field_name: {"type": "list_node", "id": nid}})
        if best_avail:
            return False, (f"Solution Cluster '{value}' nicht gefunden. "
                           f"Verfügbar ({best_avail[0]}): {best_avail[1][:8]}")
        return False, (f"Solution Cluster Feld nicht gefunden "
                       f"(Wert: '{value}', gesucht in: {tried[:6]})")

    def set_assigned_ecu(self, defect_id: str, ecu_name: str) -> Tuple[bool, str]:
        """Set the assigned ECU field.
        Primary: direct name search (no list_root required).
        Fallback: known logical-name candidates (tiered matching) → brute-force scan."""
        if self._session_expired:
            return False, "Session abgelaufen"
        # ── Primary: search list_node by value name, try each with PUT ────────
        for fn in ["assigned_ecu_udf", "assigned_ecu", "ecu_udf",
                   "env_ecu_def_udf"]:
            ok, msg = self._set_by_name_search(defect_id, fn, ecu_name)
            if ok:
                return ok, msg
        # ── Fallback: legacy list_root-based candidates ───────────────────────
        candidates: List[Tuple[str, str]] = [
            ("list_node.assigned_ecu_udf", "assigned_ecu_udf"),
            ("list_node.assigned_ecu",     "assigned_ecu"),
            ("list_node.ecu_udf",          "ecu_udf"),
        ]
        ecu_lower = ecu_name.lower()
        seen: set = set()
        best_avail: Optional[Tuple[str, list]] = None
        for logical_name, fname in candidates:
            if logical_name in seen:
                continue
            seen.add(logical_name)
            items = self._fetch_list_values(logical_name)
            if not items:
                continue
            # Tiered matching: exact → prefix → substring
            for nm, nid in items.items():
                if ecu_lower == nm.lower():
                    return self.update_defect(defect_id,
                        {fname: {"type": "list_node", "id": nid}})
            for nm, nid in items.items():
                nm_l = nm.lower()
                if nm_l.startswith(ecu_lower) or ecu_lower.startswith(
                        nm_l.split()[0] if nm_l.split() else nm_l):
                    return self.update_defect(defect_id,
                        {fname: {"type": "list_node", "id": nid}})
            for nm, nid in items.items():
                if ecu_lower in nm.lower():
                    return self.update_defect(defect_id,
                        {fname: {"type": "list_node", "id": nid}})
            # Value not in this list — remember for error message, keep looking
            if best_avail is None:
                best_avail = (logical_name, list(items.keys()))
        # ── 3. Brute-force: scan ALL workspace roots by keyword ──────────────
        _ecu_hints = {"idcevo", "mgu", "here", "zenrin"}

        def _has_ecu_values(root_name: str) -> bool:
            """Return True only if the list contains at least one ECU-like entry."""
            vals = self._fetch_list_values(root_name)
            return any(
                any(h in v.lower() for h in _ecu_hints)
                for v in (vals or {}).keys()
            )

        scan_roots = [r for r in self._find_list_roots_by_keyword(
                          "ecu", "assigned_ecu")
                      if r not in seen and _has_ecu_values(r)]
        result = self._find_value_in_roots(scan_roots, ecu_name)
        if result:
            fname, nid = result
            return self.update_defect(defect_id,
                {fname: {"type": "list_node", "id": nid}})
        # Found roots but value wasn't there — show what is available
        for root in scan_roots:
            avail = self._fetch_list_values(root)
            if avail:
                best_avail = best_avail or (root, list(avail.keys()))
        tried = list(seen) + scan_roots

        # ── 4. Value-first discovery: find list_node then trace to field ──
        found = self._find_defect_field_for_listnode(ecu_name)
        if found:
            field_name, nid = found
            if field_name.startswith("__lr_id:"):
                ok, msg = self._try_field_candidates_with_node(defect_id, nid, [
                    "assigned_ecu_udf", "assigned_ecu", "ecu_udf",
                ])
                if ok:
                    return True, msg
            else:
                return self.update_defect(defect_id,
                    {field_name: {"type": "list_node", "id": nid}})
        if best_avail:
            return False, (f"Assigned ECU '{ecu_name}' nicht gefunden. "
                           f"Verfügbar ({best_avail[0]}): {best_avail[1][:8]}")
        return False, (f"Assigned ECU Feld nicht gefunden "
                       f"(Wert: '{ecu_name}', gesucht in: {tried[:6]})")


# ══════════════════════════════════════════════════════════════════════════════
# GUI Application (using native tk widgets for macOS compatibility)
# ══════════════════════════════════════════════════════════════════════════════
class OctaneTicketProcessorGUI:
    """Main GUI Application"""
    
    # ── BMW Neue Klasse Design Language ──────────────────────────────────────
    # Background tiers
    BG_COLOR     = "#0f0f12"       # near-black main background
    FRAME_BG     = "#1a1a22"       # card background
    CARD_BORDER  = "#2a2a38"       # subtle card border
    # Accent colours (BMW Neue Klasse: electric blue + warm amber)
    ACCENT       = "#1e9de8"       # main blue
    ACCENT2      = "#6ec6f5"       # lighter blue highlight
    AMBER        = "#f0a500"       # amber accent
    # Header
    HEADER_BG    = "#07070a"
    HEADER_FG    = "#ffffff"
    # Text
    FG_PRIMARY   = "#ffffff"
    FG_SECONDARY = "#ffffff"
    # Buttons
    BUTTON_BG          = "#1e9de8"
    BUTTON_FG          = "#ffffff"
    BUTTON_DISABLED_BG = "#2a2a40"
    BUTTON_OK_BG       = "#14a874"
    BUTTON_CANCEL_BG   = "#cc3333"
    # Status
    SUCCESS_COLOR  = "#14a874"
    ERROR_COLOR    = "#e84040"
    WARNING_COLOR  = "#f0a500"

    # Action types
    ACTION_REJECT   = "REJECT"
    ACTION_PHASE04  = "PHASE_04"
    ACTION_DQ       = "DATA_QUALITY"  # HERE / Zenrin map data issue

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OCTANE · Navigation Defects  |  BMW Group")
        self.root.geometry("1120x860")
        self.root.minsize(960, 720)
        self.root.configure(bg=self.BG_COLOR)

        self.client: Optional[OctaneClient] = None
        self.processing = False
        self.planned_actions: List[Dict[str, Any]] = []
        self._batch_offset = 0
        self._total_available = 0
        self._processed_count = 0   # actually successfully written to OCTANE
        self._first_use_options: List[str] = _FIRST_USE_FALLBACK
        self._first_use_field: Optional[str] = None
        self._istep_field:     Optional[str] = None
        self._target_istep_field: Optional[str] = None
        self._blocking_reason_field:   Optional[str] = None
        self._blocking_reason_options: List[str] = []

        # Thread-safe GUI update queue: background threads put callables here;
        # the main thread drains it every 50 ms.  Use this instead of
        # root.after(0, cb) from background threads, which is NOT thread-safe
        # on macOS Tcl/Tk and causes hangs after the first call.
        self._gui_queue: _queue.Queue = _queue.Queue()

        self._create_widgets()
        # Start the queue-drain loop AFTER widgets exist
        self._gui_queue_poll()

    def _gui_queue_poll(self):
        """Drain the thread-safe GUI queue in the main thread every 50 ms.
        Callbacks are executed one-by-one; any exception in a single callback
        is caught and printed to stderr so that the poll loop always reschedules
        itself (prevents the button from staying grey forever)."""
        try:
            while True:
                cb = self._gui_queue.get_nowait()
                try:
                    cb()
                except Exception as _cb_err:
                    import sys
                    print(f"[_gui_queue_poll] callback error: {_cb_err}", file=sys.stderr)
        except _queue.Empty:
            pass
        self.root.after(50, self._gui_queue_poll)

    def _gui_do(self, cb):
        """Schedule a callable to run on the main thread (safe from any thread)."""
        self._gui_queue.put(cb)

    # ── Widget factories ──────────────────────────────────────────────────────

    def _create_label_frame(self, parent, text, **kwargs):
        kw = dict(bg=self.FRAME_BG, fg=self.ACCENT2,
                  font=('Helvetica', 10, 'bold'),
                  relief=tk.FLAT, bd=0,
                  highlightthickness=1,
                  highlightbackground=self.CARD_BORDER,
                  padx=12, pady=4)
        kw.update(kwargs)
        return tk.LabelFrame(parent, text=f"  {text}  ", **kw)

    def _btn(self, parent, text, command, bg=None, state=tk.NORMAL, **kwargs):
        if bg is None:
            bg = self.BUTTON_BG
        disabled_bg = self.BUTTON_DISABLED_BG
        hl_bg = self.CARD_BORDER if state == tk.DISABLED else bg
        fg = kwargs.pop("fg", self.BUTTON_FG if state == tk.NORMAL else self.FG_SECONDARY)
        return tk.Button(parent, text=text, command=command,
                         bg=bg if state == tk.NORMAL else disabled_bg,
                         fg=fg,
                         activebackground=self.ACCENT2,
                         activeforeground="#ffffff",
                         font=('Helvetica', 10, 'bold'),
                         relief=tk.FLAT, bd=0,
                         highlightthickness=1,
                         highlightbackground=hl_bg,
                         highlightcolor=self.ACCENT2,
                         padx=18, pady=4,
                         cursor="hand2" if state == tk.NORMAL else "arrow",
                         state=state, **kwargs)

    # ── BMW Neue Klasse icon (canvas-drawn) ───────────────────────────────────

    def _draw_nk_icon(self, canvas: tk.Canvas, x: int, y: int, w: int = 340):
        """BMW Neue Klasse 'Iconic Lights' headlight signature.
        Matches the official design: wide wing + two portrait kidney pills + diagonal slashes."""
        H  = int(w * 0.205)          # total icon height
        fg = "#ffffff"
        lw = 2

        # Background fill
        canvas.create_rectangle(x, y, x + w, y + H + 12,
                                 fill=self.HEADER_BG, outline="", tags="icon")

        cy = y + int(H * 0.48)       # vertical center (slight room below for reflection)
        wh = int(H * 0.44)           # wing half-height at widest point

        # ── Center kidney (pill) dimensions ───────────────────────────────────
        cx   = x + w // 2
        kg   = int(w * 0.010)        # gap between the two pills
        kw   = int(w * 0.063)        # width of each pill
        kh   = int(H * 0.92)         # height (portrait)
        kr   = int(kw  * 0.46)       # corner radius
        kt   = cy - kh // 2          # top y
        kb   = cy + kh // 2          # bottom y
        # Left kidney x-span:  [lk1, lk2]
        # Right kidney x-span: [rk1, rk2]
        lk1, lk2 = cx - kg - kw, cx - kg
        rk1, rk2 = cx + kg,      cx + kg + kw

        # ── Wing polygons (outline only, smooth) ─────────────────────────────
        # Left wing: tip at far left →  shoulder → inner edge (butts kidney)
        def wing(ax1, ax2, tip_x):
            pts = [
                tip_x,                   cy,
                ax1 + int((ax2-ax1)*0.72), cy - wh,
                ax2,                     cy - int(wh * 0.52),
                ax2,                     cy + int(wh * 0.52),
                ax1 + int((ax2-ax1)*0.72), cy + wh,
            ]
            canvas.create_polygon(pts, fill="", outline=fg,
                                   width=lw, smooth=True, tags="icon")

        wing(x,   lk1, x)       # left  (tip at x)
        wing(rk2, x+w, x+w)     # right (tip at x+w)

        # ── Inner panel rectangle in each wing ───────────────────────────────
        ph = int(wh * 0.62)          # panel half-height
        for px1, px2 in [
            (x + int(w*0.075), lk1 - int(w*0.008)),
            (rk2 + int(w*0.008), x + w - int(w*0.075)),
        ]:
            canvas.create_rectangle(px1, cy - ph, px2, cy + ph,
                                     fill="", outline=fg, width=1, tags="icon")

        # ── Diagonal slash marks (2 per side, outer quarter of each wing) ────
        sh = int(wh * 1.35)          # slash half-height
        dx = int(w * 0.014)          # horizontal spread of each slash
        for n in range(2):
            # Left side: "\" shape (top-left → bottom-right)
            sx = x + int(w * (0.155 + n * 0.051))
            canvas.create_line(sx - dx, cy - sh, sx + dx, cy + sh,
                                fill=fg, width=lw + 1, tags="icon")
            # Right side: "/" shape (mirror)
            sx = x + w - int(w * (0.155 + n * 0.051))
            canvas.create_line(sx + dx, cy - sh, sx - dx, cy + sh,
                                fill=fg, width=lw + 1, tags="icon")

        # ── Rounded-rectangle helper (arcs + lines) ───────────────────────────
        def rr(px1, py1, px2, py2, r):
            a = dict(fill="", outline=fg, width=lw, style=tk.ARC, tags="icon")
            canvas.create_arc(px1,       py1,       px1+2*r, py1+2*r, start=90,  extent=90, **a)
            canvas.create_arc(px2-2*r,   py1,       px2,     py1+2*r, start=0,   extent=90, **a)
            canvas.create_arc(px1,       py2-2*r,   px1+2*r, py2,     start=180, extent=90, **a)
            canvas.create_arc(px2-2*r,   py2-2*r,   px2,     py2,     start=270, extent=90, **a)
            ln = dict(fill=fg, width=lw, tags="icon")
            canvas.create_line(px1+r, py1, px2-r, py1, **ln)
            canvas.create_line(px1+r, py2, px2-r, py2, **ln)
            canvas.create_line(px1, py1+r, px1, py2-r, **ln)
            canvas.create_line(px2, py1+r, px2, py2-r, **ln)

        # ── Center kidney / pill shapes ───────────────────────────────────────
        rr(lk1, kt, lk2, kb, kr)
        rr(rk1, kt, rk2, kb, kr)

        # ── Subtle reflection (horizontal gradient lines below kidneys) ───────
        for i, c in enumerate(["#606078", "#404058", "#28283a"]):
            ry = kb + 3 + i * 3
            canvas.create_line(lk1 + kr, ry, rk2 - kr, ry,
                                fill=c, width=1, tags="icon")

    def _render_header_icon(self, canvas: tk.Canvas,
                             x: int, y: int, w: int, h: int):
        """Display the JPG icon in the header canvas, falling back to the drawn bee."""
        import os
        path = os.path.expanduser(ICON_PATH) if ICON_PATH else ""
        if path and os.path.exists(path):
            try:
                from PIL import Image, ImageTk
                img = Image.open(path)
                # Scale to header height, preserve aspect ratio
                new_h = h
                new_w = int(img.width * new_h / img.height)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                canvas.config(width=new_w, height=new_h)
                canvas.create_image(0, 0, anchor=tk.NW, image=photo)
                canvas._bee_photo = photo  # keep reference to prevent GC
                return
            except Exception:
                pass
        self._draw_bee_logo(canvas)

    def _draw_bee_logo(self, canvas: tk.Canvas):
        """Cinematic honeybee carrying heavy pollen loads — header logo.
        Canvas is 360 × 116 px on HEADER_BG (#07070a)."""
        import math
        import random

        s  = 1.4     # scale factor
        cx = 178     # bee body center x
        cy = 55      # bee body center y
        T  = "bee_logo"

        def S(v): return int(v * s)

        canvas.delete(T)

        # ── Cinematic amber backlight glow ─────────────────────────────────
        for (rx, ry, col) in [
            (S(75), S(44), "#1a1000"),
            (S(60), S(35), "#221500"),
            (S(46), S(27), "#2c1a00"),
            (S(32), S(19), "#382100"),
        ]:
            canvas.create_oval(cx - rx, cy - ry, cx + rx, cy + ry,
                               fill=col, outline="", tags=T)

        # ── Wings (drawn first — lay behind body) ──────────────────────────
        # Upper wing — large, iridescent
        canvas.create_oval(cx - S(18), cy - S(35),
                           cx + S(16), cy - S(11),
                           fill="#b8d8f4", outline="#6090b8",
                           width=1, tags=T)
        canvas.create_line(cx - S(8), cy - S(30),
                           cx + S(12), cy - S(16),
                           fill="#90b8d8", width=1, tags=T)   # vein
        # Lower wing
        canvas.create_oval(cx - S(8), cy - S(27),
                           cx + S(13), cy - S(9),
                           fill="#a4cce8", outline="#6090b8",
                           width=1, tags=T)

        # ── Thorax ─────────────────────────────────────────────────────────
        canvas.create_oval(cx + S(10), cy - S(12),
                           cx + S(24), cy + S(12),
                           fill="#e0a400", outline="#a07200",
                           width=1, tags=T)

        # ── Abdomen ────────────────────────────────────────────────────────
        canvas.create_oval(cx - S(28), cy - S(14),
                           cx + S(22), cy + S(14),
                           fill="#f5c000", outline="#b08000",
                           width=S(1), tags=T)
        for sx in (-12, 0, 12):          # 3 black stripes
            canvas.create_oval(cx + S(sx) - S(5), cy - S(14),
                               cx + S(sx) + S(5), cy + S(14),
                               fill="#1c1000", outline="", tags=T)
        canvas.create_arc(cx - S(24), cy - S(13),  # top highlight arc
                          cx + S(18), cy + S(2),
                          start=15, extent=150, style=tk.ARC,
                          outline="#ffd840", width=1, tags=T)
        canvas.create_arc(cx - S(27), cy,           # belly shadow arc
                          cx + S(21), cy + S(13),
                          start=180, extent=180, style=tk.ARC,
                          outline="#906000", width=1, tags=T)
        canvas.create_oval(cx - S(28), cy - S(14),  # outline on top
                           cx + S(22), cy + S(14),
                           fill="", outline="#b08000",
                           width=S(1), tags=T)

        # ── Stinger ────────────────────────────────────────────────────────
        canvas.create_polygon(
            [cx - S(28), cy - S(4),
             cx - S(28), cy + S(4),
             cx - S(41), cy],
            fill="#b89020", outline="#907010", width=1, tags=T)

        # ── Head ───────────────────────────────────────────────────────────
        canvas.create_oval(cx + S(16), cy - S(11),
                           cx + S(38), cy + S(11),
                           fill="#f5c000", outline="#b08000",
                           width=S(1), tags=T)
        # Compound eye — large, dramatic red-amber
        canvas.create_oval(cx + S(25), cy - S(8),
                           cx + S(37), cy + S(2),
                           fill="#c83000", outline="", tags=T)
        canvas.create_oval(cx + S(27), cy - S(6),
                           cx + S(36), cy + S(1),
                           fill="#200000", outline="", tags=T)
        canvas.create_oval(cx + S(28), cy - S(5),  # glint
                           cx + S(30), cy - S(3),
                           fill="#ff6060", outline="", tags=T)
        # Head highlight
        canvas.create_arc(cx + S(18), cy - S(10),
                          cx + S(36), cy,
                          start=20, extent=140, style=tk.ARC,
                          outline="#ffd860", width=1, tags=T)
        # Determined smile
        canvas.create_arc(cx + S(17), cy,
                          cx + S(35), cy + S(10),
                          start=200, extent=140, style=tk.ARC,
                          outline="#443300", width=S(1), tags=T)

        # ── Antennae ───────────────────────────────────────────────────────
        canvas.create_line(cx + S(26), cy - S(10),
                           cx + S(20), cy - S(27),
                           fill="#553300", width=S(1), tags=T)
        canvas.create_oval(cx + S(17), cy - S(31),
                           cx + S(23), cy - S(25),
                           fill="#221100", outline="", tags=T)
        canvas.create_line(cx + S(31), cy - S(9),
                           cx + S(37), cy - S(24),
                           fill="#553300", width=S(1), tags=T)
        canvas.create_oval(cx + S(34), cy - S(28),
                           cx + S(40), cy - S(22),
                           fill="#221100", outline="", tags=T)

        # ── Heavy pollen loads on hind legs ────────────────────────────────
        h_y = cy + S(14)   # bottom of abdomen / leg attachment y

        # Left hind leg + enormous blob
        hx1   = cx - S(18)
        lx1   = hx1 - 12
        ly1   = h_y + 8
        canvas.create_line(hx1, h_y, lx1, ly1,
                           fill="#885500", width=2, tags=T)
        PR1 = 16
        canvas.create_oval(lx1 - PR1, ly1 - 2,
                           lx1 + PR1, ly1 + PR1 * 2,
                           fill="#e89800", outline="#c07000",
                           width=1, tags=T)
        canvas.create_oval(lx1 - PR1 + 4, ly1 + 3,   # inner glow
                           lx1 + PR1 - 6, ly1 + PR1,
                           fill="#ffc840", outline="", tags=T)
        canvas.create_oval(lx1 - 5, ly1 + 2,          # bright highlight
                           lx1 + 1, ly1 + 7,
                           fill="#fff080", outline="", tags=T)

        # Right hind leg + large blob
        hx2   = cx - S(8)
        lx2   = hx2 + 8
        ly2   = h_y + 6
        canvas.create_line(hx2, h_y, lx2, ly2,
                           fill="#885500", width=2, tags=T)
        PR2 = 13
        canvas.create_oval(lx2 - PR2, ly2 - 2,
                           lx2 + PR2, ly2 + PR2 * 2,
                           fill="#e89800", outline="#c07000",
                           width=1, tags=T)
        canvas.create_oval(lx2 - PR2 + 3, ly2 + 2,
                           lx2 + PR2 - 5, ly2 + PR2 - 1,
                           fill="#ffc840", outline="", tags=T)
        canvas.create_oval(lx2 - 4, ly2 + 1,
                           lx2 + 1, ly2 + 5,
                           fill="#fff080", outline="", tags=T)

        # Middle legs
        for ml_x in (cx - S(6), cx + S(4)):
            canvas.create_line(ml_x, h_y, ml_x - 3, h_y + 10,
                               fill="#885500", width=1, tags=T)

        # Front legs (reaching forward under strain)
        canvas.create_line(cx + S(14), cy + 2,
                           cx + S(14) + 10, cy + 12,
                           fill="#885500", width=1, tags=T)
        canvas.create_line(cx + S(17), cy - 2,
                           cx + S(17) + 8, cy + 10,
                           fill="#885500", width=1, tags=T)

        # ── Pollen dust shower (fixed-seed so it's stable on redraw) ───────
        random.seed(42)
        dust_bx, dust_by = lx1, ly1 + PR1 + 4
        for _ in range(10):
            px  = dust_bx + random.randint(-22, 22)
            py  = dust_by + random.randint(-4, 14)
            pr3 = random.randint(1, 3)
            col = random.choice(["#f5c000", "#e8a800", "#ffd040",
                                  "#d49000", "#f0b000"])
            canvas.create_oval(px - pr3, py - pr3, px + pr3, py + pr3,
                               fill=col, outline="", tags=T)

        # ── Effort / motion lines (streak leftward from stinger tip) ───────
        stx = cx - S(41) - 4
        for (lx, ly, length) in [
            (stx,     cy - 12, 22),
            (stx,     cy,      30),
            (stx,     cy + 12, 22),
            (stx - 2, cy - 20, 14),
            (stx - 2, cy + 20, 14),
        ]:
            canvas.create_line(lx, ly, lx - length, ly,
                               fill="#342000", width=1, tags=T)

        # ── Subtle rim light on top of bee (cinematic key light) ───────────
        canvas.create_arc(cx - S(27), cy - S(15),
                          cx + S(37), cy,
                          start=5, extent=170, style=tk.ARC,
                          outline="#604010", width=1, tags=T)

    def _create_widgets(self):
        # ── Outer scroll container ────────────────────────────────────────────
        main_frame = tk.Frame(self.root, bg=self.BG_COLOR, padx=16, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── HEADER BAND ──────────────────────────────────────────────────────
        hdr_outer = tk.Frame(main_frame, bg=self.HEADER_BG,
                             highlightthickness=0)
        hdr_outer.pack(fill=tk.X, pady=(0, 14))

        # Left: icon canvas (shows real image if ICON_PATH exists, else drawn fallback)
        icon_canvas = tk.Canvas(hdr_outer, bg=self.HEADER_BG,
                                highlightthickness=0,
                                width=360, height=116)
        icon_canvas.pack(side=tk.LEFT, padx=(8, 0), pady=2)
        icon_canvas.update_idletasks()
        self._render_header_icon(icon_canvas, 0, 0, 360, 116)

        # Center: title block
        title_block = tk.Frame(hdr_outer, bg=self.HEADER_BG)
        title_block.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=16)

        tk.Label(title_block,
                 text="OCTANE  ·  Navigation Defects",
                 bg=self.HEADER_BG, fg="#ffffff",
                 font=('Helvetica', 15, 'bold')).pack(anchor=tk.W)
        tk.Label(title_block,
                 text="Automated Phase-03 → Phase-04 Processor  |  BMW Group Navigation",
                 bg=self.HEADER_BG, fg=self.ACCENT2,
                 font=('Helvetica', 9)).pack(anchor=tk.W)

        # Right: accent bar (thin coloured strip)
        accent_bar = tk.Frame(hdr_outer, bg=self.ACCENT, width=5)
        accent_bar.pack(side=tk.RIGHT, fill=tk.Y)

        # ── LOGIN CARD ────────────────────────────────────────────────────────
        login_frame = self._create_label_frame(main_frame, "OCTANE Login")
        login_frame.pack(fill=tk.X, pady=(0, 8))
        login_frame.configure(bg=self.FRAME_BG)

        def _entry(parent, **kw):
            return tk.Entry(parent, font=('SF Mono', 10) if True else ('Menlo', 10),
                            bg="#111118", fg=self.FG_PRIMARY,
                            insertbackground=self.ACCENT,
                            relief=tk.FLAT,
                            highlightthickness=1,
                            highlightbackground=self.CARD_BORDER,
                            highlightcolor=self.ACCENT, **kw)

        r1 = tk.Frame(login_frame, bg=self.FRAME_BG)
        r1.pack(fill=tk.X, pady=2)
        tk.Label(r1, text="OCTANE User:", bg=self.FRAME_BG, fg=self.FG_SECONDARY,
                 font=('Helvetica', 10), width=14, anchor=tk.W).pack(side=tk.LEFT)
        self.user_entry = _entry(r1, width=52)
        self.user_entry.pack(side=tk.LEFT, ipady=5, padx=(6, 0))

        r2 = tk.Frame(login_frame, bg=self.FRAME_BG)
        r2.pack(fill=tk.X, pady=2)
        tk.Label(r2, text="Access Token:", bg=self.FRAME_BG, fg=self.FG_SECONDARY,
                 font=('Helvetica', 10), width=14, anchor=tk.W).pack(side=tk.LEFT)
        self.token_entry = _entry(r2, width=52, show="●")
        self.token_entry.pack(side=tk.LEFT, ipady=5, padx=(6, 0))
        self.show_token = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="anzeigen", variable=self.show_token,
                       command=self._toggle_token_visibility,
                       font=('Helvetica', 9),
                       bg=self.FRAME_BG, fg=self.FG_SECONDARY,
                       selectcolor=self.BG_COLOR,
                       activebackground=self.FRAME_BG).pack(side=tk.LEFT, padx=10)
        self.login_btn = self._btn(r2, "  Login  ", self._do_login, fg="#000000")
        self.login_btn.pack(side=tk.LEFT, padx=(14, 0))

        # Pre-fill saved OCTANE User
        _saved = _load_prefs().get("octane_user", "")
        if _saved:
            self.user_entry.insert(0, _saved)

        self.login_status = tk.Label(login_frame, text="Nicht verbunden",
                                     font=('Helvetica', 10), anchor=tk.W,
                                     bg=self.FRAME_BG, fg=self.FG_SECONDARY)
        self.login_status.pack(fill=tk.X, pady=(2, 0))

        # ── AKTIONEN CARD ─────────────────────────────────────────────────────
        action_frame = self._create_label_frame(main_frame, "Aktionen")
        action_frame.pack(fill=tk.X, pady=(0, 8))
        action_frame.configure(bg=self.FRAME_BG)

        self.process_btn = self._btn(
            action_frame,
            "🔍  Nächste 10 Tickets analysieren (Batch 1)",
            self._start_analysis, state=tk.DISABLED, fg="#000000")
        self.process_btn.pack(fill=tk.X, pady=(2, 4))

        # Stats strip
        stats_strip = tk.Frame(action_frame, bg="#0f0f18",
                               highlightthickness=1,
                               highlightbackground=self.CARD_BORDER)
        stats_strip.pack(fill=tk.X, pady=(0, 4))
        self.total_count_label = tk.Label(stats_strip, text="",
                                          font=('Helvetica', 10, 'bold'), anchor=tk.W,
                                          bg="#0f0f18", fg=self.ACCENT2,
                                          padx=10, pady=5)
        self.total_count_label.pack(side=tk.LEFT)
        self.batch_info_label = tk.Label(stats_strip, text="",
                                         font=('Helvetica', 9), anchor=tk.E,
                                         bg="#0f0f18", fg=self.FG_SECONDARY,
                                         padx=10)
        self.batch_info_label.pack(side=tk.RIGHT)

        tk.Label(action_frame,
                 text="Cat: Navigation ECE / Japan / US   ·   ECU: HU-MGU_02_A, IDCEVO-25   ·   Phase 03   ·   Sol.Resp.: BMW",
                 font=('Helvetica', 9), anchor=tk.W, justify=tk.LEFT,
                 bg=self.FRAME_BG, fg=self.FG_SECONDARY,
                 ).pack(anchor=tk.W, pady=(4, 0))

        # ── FORTSCHRITT CARD ──────────────────────────────────────────────────
        progress_frame = self._create_label_frame(main_frame, "Fortschritt")
        progress_frame.pack(fill=tk.X, pady=(0, 8))
        progress_frame.configure(bg=self.FRAME_BG)

        self.progress_label = tk.Label(progress_frame, text="Bereit",
                                       font=('Helvetica', 10), anchor=tk.W,
                                       bg=self.FRAME_BG, fg=self.FG_SECONDARY)
        self.progress_label.pack(anchor=tk.W)
        self.progress_canvas = tk.Canvas(progress_frame, height=20,
                                         bg="#111118",
                                         highlightthickness=1,
                                         highlightbackground=self.CARD_BORDER)
        self.progress_canvas.pack(fill=tk.X, pady=(4, 2))
        self.progress_canvas.bind("<Configure>", lambda e: self._draw_progress_bar())
        self.progress_value = 0
        self._draw_progress_bar()
        self.stats_label = tk.Label(progress_frame, text="",
                                    font=('Helvetica', 9), anchor=tk.W,
                                    bg=self.FRAME_BG, fg=self.FG_SECONDARY)
        self.stats_label.pack(anchor=tk.W)

        # ── CONTENT NOTEBOOK (Aktionsvorschau + Log) ──────────────────────────
        self._content_nb = ttk.Notebook(main_frame)
        self._content_nb.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        _preview_tab = tk.Frame(self._content_nb, bg=self.FRAME_BG)
        self._content_nb.add(_preview_tab, text="  Aktionsvorschau  ")
        _log_tab = tk.Frame(self._content_nb, bg=self.FRAME_BG)
        self._content_nb.add(_log_tab, text="  Log  ")

        # ── AKTIONSVORSCHAU (tab 1) ───────────────────────────────────────────
        self.preview_outer = self._create_label_frame(_preview_tab, "Aktionsvorschau")
        self.preview_outer.configure(bg=self.FRAME_BG)
        self.preview_outer.pack(fill=tk.BOTH, expand=True)

        # Outer container that holds header + scrollable rows + x-scrollbar
        preview_inner = tk.Frame(self.preview_outer, bg=self.FRAME_BG)
        preview_inner.pack(fill=tk.BOTH, expand=True)

        # Horizontal scrollbar (shared between header canvas and rows canvas)
        hsb = tk.Scrollbar(preview_inner, orient=tk.HORIZONTAL,
                           bg=self.FRAME_BG, troughcolor=self.BG_COLOR)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)

        # Table header rendered inside a canvas so it scrolls horizontally
        _total_tbl_w = sum(_TBLCOL_PX.values())
        self._hdr_canvas = tk.Canvas(preview_inner, height=28, highlightthickness=0,
                               bg="#0a0a14")
        self._hdr_canvas.pack(fill=tk.X, side=tk.TOP)
        hdr = tk.Frame(self._hdr_canvas, bg="#0a0a14",
                       width=_total_tbl_w, height=28)
        hdr.pack_propagate(False)
        self._hdr_canvas.create_window((0, 0), window=hdr, anchor="nw")
        for col_text, col_key in [
                ("✓", "check"), ("Ticket-ID", "id"), ("Titel", "titel"),
                ("Geplante Aktion", "aktion"), ("Owner", "owner"),
                ("First Use", "sop"), ("ECU", "ecu"),
                ("Target I-Step", "tis"), ("Blocking Reason", "br"),
                ("Kommentar", "kommentar"), ("I-Step", "istep"),
                ("↩", "undo")]:
            f = self._cell(hdr, "#0a0a14", col_key)
            tk.Label(f, text=col_text, bg="#0a0a14", fg=self.ACCENT2,
                     font=('Menlo', 9, 'bold'), anchor=tk.CENTER
                     ).pack(fill=tk.BOTH, expand=True)
        # Set scrollregion immediately from known column widths (no update needed)
        self._hdr_canvas.configure(scrollregion=(0, 0, _total_tbl_w, 28))

        # Scrollable rows
        tbl = tk.Frame(preview_inner, bg=self.FRAME_BG)
        tbl.pack(fill=tk.BOTH, expand=True)
        vsb = tk.Scrollbar(tbl, orient=tk.VERTICAL,
                           bg=self.FRAME_BG, troughcolor=self.BG_COLOR)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_canvas = tk.Canvas(tbl, yscrollcommand=vsb.set,
                                        highlightthickness=0,
                                        bg=self.FRAME_BG)
        self.preview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.config(command=self.preview_canvas.yview)
        self.preview_rows_frame = tk.Frame(self.preview_canvas, bg=self.FRAME_BG)
        self.preview_canvas.create_window((0, 0), window=self.preview_rows_frame,
                                          anchor="nw")

        def _on_rows_configure(e):
            self.preview_canvas.configure(
                scrollregion=self.preview_canvas.bbox("all"))
            # Keep header canvas scroll region in sync (same total width)
            self._hdr_canvas.configure(scrollregion=(
                0, 0,
                self.preview_rows_frame.winfo_reqwidth(),
                28))

        self.preview_rows_frame.bind("<Configure>", _on_rows_configure)

        # Wire horizontal scrollbar to both canvases
        def _xscroll(*args):
            self.preview_canvas.xview(*args)
            self._hdr_canvas.xview(*args)

        hsb.config(command=_xscroll)
        # Sync header x-position whenever rows canvas scrolls horizontally
        self.preview_canvas.configure(
            xscrollcommand=lambda *a: (hsb.set(*a), self._hdr_canvas.xview_moveto(a[0])))

        # Confirm / Cancel row
        brow = tk.Frame(self.preview_outer, bg=self.FRAME_BG)
        brow.pack(fill=tk.X, pady=(4, 0))
        self.confirm_btn = self._btn(brow, "✅  Ausführen",
                                     self._confirm_execute, bg=self.BUTTON_OK_BG, fg="#000000")
        self.confirm_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.cancel_btn = self._btn(brow, "✕  Abbrechen",
                                    self._cancel_preview, bg=self.BUTTON_CANCEL_BG, fg="#000000")
        self.cancel_btn.pack(side=tk.LEFT)
        self.select_all_btn = self._btn(brow, "☑ Alle  ☐ Keine",
                                        self._toggle_all_selection, bg="#2a2a40", fg="#000000",
                                        width=16)
        self.select_all_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.preview_count_label = tk.Label(brow, text="",
                                            font=('Helvetica', 10),
                                            bg=self.FRAME_BG, fg=self.FG_SECONDARY)
        self.preview_count_label.pack(side=tk.LEFT, padx=20)

        # ── LOG CARD (tab 2) ──────────────────────────────────────────────────
        log_frame = self._create_label_frame(_log_tab, "Log")
        log_frame.pack(fill=tk.BOTH, expand=True)
        log_frame.configure(bg=self.FRAME_BG)

        tbl2 = tk.Frame(log_frame, bg=self.FRAME_BG)
        tbl2.pack(fill=tk.BOTH, expand=True)
        vsb2 = tk.Scrollbar(tbl2, bg=self.FRAME_BG, troughcolor=self.BG_COLOR)
        vsb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = tk.Text(tbl2, height=9, font=('Menlo', 10),
                                state=tk.DISABLED, wrap=tk.WORD,
                                yscrollcommand=vsb2.set,
                                bg="#07070e", fg="#ffffff",
                                insertbackground=self.ACCENT,
                                relief=tk.FLAT, bd=0,
                                padx=8, pady=6)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb2.config(command=self.log_text.yview)
        self.log_text.tag_config("INFO",    foreground="#ffffff")
        self.log_text.tag_config("SUCCESS", foreground="#14d4a0")
        self.log_text.tag_config("WARNING", foreground=self.AMBER)
        self.log_text.tag_config("ERROR",   foreground="#ff5555")
        self.log_text.tag_config("HEADER",  foreground=self.ACCENT,
                                 font=('Menlo', 10, 'bold'))

        clear_btn = self._btn(log_frame, "Log löschen", self._clear_log, fg="#000000")
        clear_btn.pack(anchor=tk.E, pady=(6, 0))

        self.root.update()
    
    # ── Helper widgets ───────────────────────────────────────────────────────────
    
    def _draw_progress_bar(self):
        self.progress_canvas.delete("progress")
        width = self.progress_canvas.winfo_width() or 900
        fill_width = int(width * self.progress_value / 100)
        # Background track
        self.progress_canvas.create_rectangle(0, 0, width, 20,
                                              fill="#111118", outline="",
                                              tags="progress")
        # Filled segment — blue gradient via two overlapping rects
        if fill_width > 0:
            self.progress_canvas.create_rectangle(0, 0, fill_width, 20,
                                                  fill=self.ACCENT, outline="",
                                                  tags="progress")
            # top highlight strip
            self.progress_canvas.create_rectangle(0, 0, fill_width, 5,
                                                  fill=self.ACCENT2, outline="",
                                                  tags="progress")
        # Percentage text
        pct = self.progress_value
        self.progress_canvas.create_text(
            width // 2, 10,
            text=f"{pct:.0f}%",
            font=('Helvetica', 8, 'bold'),
            fill="#ffffff" if pct > 15 else self.FG_SECONDARY,
            tags="progress"
        )
    
    def _toggle_token_visibility(self):
        self.token_entry.config(show="" if self.show_token.get() else "●")
    
    def _log(self, message: str, tag: str = "INFO"):
        # Safe to call from any thread: captures timestamp immediately and
        # puts a closure into the thread-safe GUI queue.
        ts = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{ts}] {message}\n"
        def _append(fmt=formatted, t=tag):
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, fmt, t)
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self._gui_do(_append)
    
    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
    
    def _update_progress_label(self, text: str):
        self._gui_do(lambda: self.progress_label.config(text=text))

    def _update_progress_bar(self, value: float):
        def _do():
            self.progress_value = value
            self._draw_progress_bar()
        self._gui_do(_do)

    def _update_stats(self, text: str):
        self._gui_do(lambda: self.stats_label.config(text=text))

    def _set_processing(self, active: bool):
        self.processing = active
        state = tk.DISABLED if active else tk.NORMAL
        bg = self.BUTTON_DISABLED_BG if active else self.BUTTON_BG
        cursor = "arrow" if active else "hand2"
        s, b, c = state, bg, cursor
        self._gui_do(lambda: self.process_btn.config(state=s, bg=b, cursor=c))

    # ── Login ────────────────────────────────────────────────────────────────────
    
    def _do_login(self):
        token = self.token_entry.get().strip()
        user  = self.user_entry.get().strip()
        if not token:
            messagebox.showerror("Fehler", "Bitte Access Token eingeben")
            return
        self.login_status.config(text="🔄 Verbinde...", fg=self.ACCENT)
        self._log("Verbinde zu OCTANE...", "INFO")
        self.root.update_idletasks()
        self.client = OctaneClient(token, user)
        self.client.set_log_callback(self._log)
        success, message = self.client.test_connection()
        if success:
            self.login_status.config(text=f"✅ {message}",
                                     fg=self.SUCCESS_COLOR, bg=self.FRAME_BG)
            self._log(f"Login erfolgreich: {message}", "SUCCESS")
            # Persist OCTANE User for next startup
            if user:
                prefs = _load_prefs()
                prefs["octane_user"] = user
                _save_prefs(prefs)
            self.process_btn.config(state=tk.NORMAL, bg=self.BUTTON_BG, cursor="hand2")
            self.total_count_label.config(text="🔄 Zähle offene Tickets...",
                                          bg="#0f0f18", fg=self.ACCENT2)
            threading.Thread(target=self._fetch_total_count, daemon=True).start()
        else:
            self.login_status.config(text=f"❌ {message}",
                                     fg=self.ERROR_COLOR, bg=self.FRAME_BG)
            self._log(f"Login fehlgeschlagen: {message}", "ERROR")
            self.process_btn.config(state=tk.DISABLED, bg=self.BUTTON_DISABLED_BG)
            self.client = None
    
    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1: Analysis (compute planned actions, no writes to OCTANE)
    # ══════════════════════════════════════════════════════════════════════════
    
    def _fetch_total_count(self):
        """Background thread: count defects, probe optional fields, fetch First Use options.
        Count is fetched FIRST so the label updates immediately after login; the slower
        field-probe and cache pre-warming happen afterwards."""
        try:
            # 1. Count matching defects first → label updates right after login
            total, unprocessed = self.client.count_unprocessed_defects()
            self._total_available = total
            self._unprocessed_count = unprocessed
            self._gui_do(lambda t=total, u=unprocessed: self.total_count_label.config(
                text=f"📋 Phase 03 Nav. ECE/JP/US: {u} noch offen ({t} gesamt)"))
            self._log(f"Tickets gesamt: {total}, davon offen (noch nicht bearbeitet): {unprocessed}", "INFO")
            self._update_process_btn_label()

            # 2. Probe/discover extra field names
            extra = self.client._probe_extra_fields()
            self._first_use_field      = extra["first_use"]
            self._istep_field          = extra["istep"]
            self._target_istep_field   = extra["target_istep"]
            self._blocking_reason_field = extra["blocking_reason"]
            # Apply manual overrides from config constants
            if ISTEP_FIELD_OVERRIDE:
                self._istep_field = ISTEP_FIELD_OVERRIDE
            if BLOCKING_REASON_FIELD_OVERRIDE:
                self._blocking_reason_field = BLOCKING_REASON_FIELD_OVERRIDE
            self._log(f"Field probe: first_use={self._first_use_field}, "
                      f"istep={self._istep_field}, "
                      f"target_istep={self._target_istep_field}, "
                      f"blocking_reason={self._blocking_reason_field}", "INFO")

            # If any field still missing, dump all available keys for debugging
            if self._first_use_field is None or self._istep_field is None:
                all_keys = self.client._discover_all_defect_keys()
                if all_keys:
                    interesting = [k for k in all_keys
                                   if any(x in k.lower() for x in
                                          ["first", "sop", "pu_", "_pu",
                                           "step", "use", "version", "istep"])]
                    self._log(f"Mögliche SOP/IStep-Felder: "
                              f"{interesting or '(keine gefunden)'}", "WARNING")
                    self._log(f"Alle defect-Felder ({len(all_keys)}): "
                              f"{', '.join(all_keys)}", "INFO")
                    # Auto-assign from discovered keys if still None
                    if self._istep_field is None:
                        for k in all_keys:
                            kl = k.lower()
                            is_step = ("i_step" in kl or "istep" in kl or
                                       "i_steps" in kl or "step_udf" in kl)
                            is_not_target = "target" not in kl
                            if is_step and is_not_target:
                                self._istep_field = k
                                self._log(f"I-Step Feld auto-erkannt: {k}", "SUCCESS")
                                break

            # 3. Inject discovered field names into search_defects requests.
            # ONLY include fields confirmed to exist by the probe — speculative
            # candidates cause OCTANE to return HTTP 400 for the whole request.
            extra_parts = [f for f in [self._first_use_field, self._istep_field,
                                        self._target_istep_field,
                                        self._blocking_reason_field] if f]
            self.client._extra_fields = ",".join(extra_parts)

            # 3b. Pre-warm all lookup caches while the session is fresh.
            # _defect_reference_fields, _all_list_roots and phase_cache are all
            # called lazily during ticket execution (minutes later), by which time
            # the OCTANE session cookie may have expired.  Fetching them now
            # guarantees the data is cached so execution never needs to repeat this.
            self._log("Lade Metadaten-Cache (Felder + Listen)...", "INFO")
            ref_fields = self.client._defect_reference_fields()
            self._log(f"Metadaten: {len(ref_fields)} Referenzfelder gecacht.", "INFO")
            list_roots = self.client._all_list_roots()
            self._log(f"Metadaten: {len(list_roots)} Listen-Roots gecacht.", "INFO")
            # Pre-warm phase cache so set_phase() during execution is just a lookup
            phase_id_03 = self.client._get_phase_id("03-In Analysis")
            phase_count = len(self.client.phase_cache)
            if phase_count:
                self._log(f"Metadaten: {phase_count} Phases gecacht (03-ID={phase_id_03}).", "INFO")
            else:
                self._log("Metadaten: Phase-Cache leer – Lookup wird bei Ausführung wiederholt.", "WARNING")

            # 4. Fetch First Use list options from OCTANE list nodes
            options = self.client.fetch_first_use_options(self._first_use_field)
            if options:
                self._first_use_options = options
                self._log(f"First Use Optionen geladen ({len(options)}): "
                          f"{', '.join(options[:8])}{'...' if len(options) > 8 else ''}",
                          "SUCCESS")
            else:
                self._log("First Use Optionen: keine gefunden – verwende Fallback-Liste", "WARNING")

            # 5. Fetch Blocking Reason options from OCTANE
            br_opts = self.client.fetch_blocking_reason_options(self._blocking_reason_field)
            if br_opts:
                self._blocking_reason_options = br_opts
                self._log(f"Blocking Reason Optionen ({len(br_opts)}): "
                          f"{', '.join(br_opts[:6])}{'...' if len(br_opts) > 6 else ''}",
                          "SUCCESS")
            else:
                self._log(
                    f"Blocking Reason Optionen: keine gefunden "
                    f"(Feldname={self._blocking_reason_field!r}). "
                    "Trage den richtigen Feldnamen in BLOCKING_REASON_FIELD_OVERRIDE ein.",
                    "WARNING")
        except Exception as e:
            self._gui_do(lambda: self.total_count_label.config(
                text="⚠️ Anzahl konnte nicht abgerufen werden"))
            self._log(f"Fehler beim Zählen: {e}", "WARNING")

    def _update_process_btn_label(self):
        """Refresh button label to reflect current batch number and open count."""
        batch_num = self._batch_offset // 10 + 1
        unprocessed = getattr(self, '_unprocessed_count', None)
        label = f"🔍 Nächste 10 Tickets analysieren (Batch {batch_num}"
        if unprocessed is not None:
            remaining_open = max(0, unprocessed - self._processed_count)
            label += f", {remaining_open} noch offen"
        elif self._total_available:
            remaining_open = max(0, self._total_available - self._processed_count)
            label += f", {remaining_open} gesamt"
        label += ")"
        self._gui_do(lambda: self.process_btn.config(text=label))
    
    def _start_analysis(self):
        if self.processing:
            return
        if not self.client or not self.client.connected:
            messagebox.showerror("Fehler", "Bitte zuerst einloggen")
            return
        # If there are still unprocessed tickets from a previous batch, skip past them.
        # If there are NO planned actions it's a fresh start → reset both counters.
        if self.planned_actions:
            self._batch_offset += getattr(self, '_batch_raw_examined',
                                          len(self.planned_actions))
        else:
            # Fresh start or user pressed Analyse after Ausführen finished
            self._batch_offset    = 0
            self._processed_count = 0
        self._hide_preview()
        self._set_processing(True)
        self.progress_value = 0
        self._draw_progress_bar()
        threading.Thread(target=self._analyse_tickets, daemon=True).start()
    
    def _analyse_tickets(self):
        """Fetch next defects and compute planned actions – NO writes.
        Fetches a window of 30 raw tickets and skips already-processed ones
        (Positioning tickets already owned by Schoenleben), collecting up to
        10 actionable tickets per call."""
        try:
            batch_num = self._batch_offset // 10 + 1
            self._log("=" * 60, "HEADER")
            self._log(f"ANALYSE  –  Batch {batch_num}  (ab Ticket {self._batch_offset + 1})",
                      "HEADER")
            self._log("=" * 60, "HEADER")
            self._update_progress_label(f"Lade Batch {batch_num} von OCTANE...")
            self._update_progress_bar(5)

            def _load_progress(frac: float):
                self._update_progress_bar(10 + frac * 35)

            # Fetch a generous window so skipped tickets don’t reduce the visible count
            FETCH_WINDOW = 20
            defects, total = self.client.search_defects(
                offset=self._batch_offset,
                limit=FETCH_WINDOW,
                callback=lambda m: self._log(m, "INFO"),
                progress_cb=_load_progress)
            self._total_available = total
            self._update_progress_bar(50)
            # Refresh count label with data from search_defects.
            # If _unprocessed_count is 0 but total > 0, the background count
            # silently failed — use total as the best available estimate.
            uc = getattr(self, '_unprocessed_count', None)
            if uc:  # truthy: count worked and returned > 0
                remaining_now = max(0, uc - self._processed_count)
            elif total:
                # Background count returned 0 (failed or race) — seed from search total
                remaining_now = max(0, total - self._processed_count)
                self._unprocessed_count = total  # update so button label is correct
            else:
                remaining_now = 0
            self._gui_do(lambda r=remaining_now, t=total: self.total_count_label.config(
                text=f"📋 Phase 03 Nav. ECE/JP/US: {r} noch offen ({t} gesamt)"))

            if not defects and self._batch_offset > 0:
                self._log(
                    f"Ende der Liste erreicht (Offset {self._batch_offset} ≥ {total}). "
                    "Starte wieder von vorne mit den verbleibenden Tickets.",
                    "WARNING")
                self._batch_offset = 0
                defects, total = self.client.search_defects(
                    offset=0, limit=FETCH_WINDOW,
                    callback=lambda m: self._log(m, "INFO"),
                    progress_cb=_load_progress)
                self._total_available = total

            if not defects:
                self._log("Keine weiteren Defects gefunden – alle Tickets bearbeitet.", "WARNING")
                self._set_processing(False)
                self._update_progress_label("Alle Tickets bearbeitet")
                self._update_progress_bar(100)
                self._gui_do(lambda: self.batch_info_label.config(
                    text=f"✅ Alle {total} Tickets wurden bearbeitet."))
                return

            planned = []
            examined = 0   # raw tickets examined in this fetch
            skipped  = 0
            n = len(defects)

            for defect in defects:
                examined += 1
                self._update_progress_bar(50 + examined / n * 50)
                self._update_progress_label(
                    f"Analysiere Ticket {examined}/{n}: {defect.get('id', '?')}...")
                action = self._compute_action(defect)
                if action is None:
                    skipped += 1
                    self._log(
                        f"  [{defect.get('id','?')}] ⏭  Übersprungen (Positioning bereits bearbeitet)",
                        "INFO")
                    continue
                planned.append(action)
                tag = ("WARNING" if action["action"] == self.ACTION_REJECT
                       else "SUCCESS" if action["action"] == self.ACTION_DQ
                       else "INFO")
                self._log(f"  [{action['defect_id']}] {action['action_label']}", tag)
                if len(planned) >= 10:
                    break

            # Remember how many raw tickets we consumed so _start_analysis can
            # advance the offset correctly (past skipped AND shown tickets).
            self._batch_raw_examined = examined

            skip_note = f" ({skipped} bereits bearbeitet übersprungen)" if skipped else ""
            self._log(
                f"\nAnalyse abgeschlossen: {len(planned)} Aktionen geplant{skip_note}.",
                "SUCCESS")
            self.planned_actions = planned
            self._gui_do(lambda p=planned: self._show_preview(p))

        except Exception as e:
            self._log(f"FEHLER bei Analyse: {e}", "ERROR")
        finally:
            self._set_processing(False)
            self._update_progress_bar(100)
            self._update_progress_label("Analyse fertig – bitte Aktionen prüfen")
    
    def _compute_action(self, defect: Dict[str, Any],
                         skip_attachment_check: bool = False) -> Dict[str, Any]:
        """Determine what action would be taken for a defect (dry-run).
        Pass skip_attachment_check=True to bypass the media/zip gate (undo-reject)."""
        defect_id   = defect.get("id", "?")
        defect_name = defect.get("name", "Unnamed")

        # Skip Positioning tickets already assigned to OWNER_POSITIONING
        if self.client.is_already_processed(defect):
            return None

        # ── Extract Involved I-Step early so all action types carry it ────────
        def _extract_istep_val(iv: Any) -> str:
            if isinstance(iv, dict):
                inner = iv.get("data", [])
                return (inner[0].get("name", "") if isinstance(inner, list) and inner
                        else iv.get("name", ""))
            if isinstance(iv, list) and iv:
                first = iv[0]
                return first.get("name", "") if isinstance(first, dict) else str(first)
            if isinstance(iv, str):
                return iv
            return ""

        istep_raw = ""
        if self._istep_field:
            istep_raw = _extract_istep_val(defect.get(self._istep_field))
        # Always scan all fetched keys as fallback — catches alternative field names
        # (e.g. involved_i_step vs involved_i_step_udf) that the probe might have missed
        if not istep_raw:
            for k, v in defect.items():
                kl = k.lower()
                if ("i_step" in kl or "istep" in kl) and "target" not in kl:
                    candidate = _extract_istep_val(v)
                    if candidate:
                        # Cache the winning field name for future tickets
                        self._istep_field = k
                        istep_raw = candidate
                        break
        # On first ticket, log all defect keys so user can see what OCTANE returned
        if not hasattr(self, '_defect_keys_logged'):
            self._defect_keys_logged = True
            self._log(f"Defect-Felder im ersten Ticket: {sorted(defect.keys())}", "INFO")

        # Step 1: Attachment check – picture/movie + zip required
        has_media, has_zip = self.client._check_attachment_types(defect)
        if not skip_attachment_check and (not has_media or not has_zip):
            if not has_media:
                # Missing picture/movie → ask reporter to attach one
                label      = "Reject → Phase 01-New (kein Bild/Video)"
                br_default = "Additional Information necessary"
                comment    = "Please attach picture or movie of perceived issue."
            else:
                # Picture exists but no trace zip → traces needed
                label      = "Reject → Phase 01-New (kein Trace-Zip)"
                br_default = "Further Traces Necessary"
                comment    = ""
            return {
                "defect_id":               defect_id,
                "defect_name":             defect_name,
                "action":                  self.ACTION_REJECT,
                "action_label":            label,
                "new_owner":               None,
                "sop":                     None,
                "istep_raw":               istep_raw,
                "blocking_reason_default": br_default,
                "comment":                 comment,
                "defect":                  defect,
            }

        # Step 2: owner logic
        current_owner = defect.get("owner", {})
        current_owner_name = ""
        if isinstance(current_owner, dict):
            current_owner_name = (current_owner.get("full_name", "")
                                  or current_owner.get("name", ""))

        is_pos = self.client.is_positioning_ticket(defect)
        dq_supplier = self.client.is_data_quality_ticket(defect)  # 'here'/'zenrin'/None

        if dq_supplier:
            # Data quality ticket → per-supplier owner, Phase 03, not responsible
            supplier_label = "HERE" if dq_supplier == "here" else "Zenrin"
            dq_owner = OWNER_DQ_HERE if dq_supplier == "here" else OWNER_DQ_ZENRIN
            category  = "IDC_mapdata" if dq_supplier == "here" else "Road Map Japan"
            # Read current ECU from ticket, override with detected supplier
            ecu_raw = defect.get("assigned_ecu_udf", {})
            ecu_current = (ecu_raw.get("name", "") if isinstance(ecu_raw, dict) else str(ecu_raw or ""))
            suggested_ecu = "HERE" if dq_supplier == "here" else "Zenrin"
            return {
                "defect_id":             defect_id,
                "defect_name":           defect_name,
                "action":                self.ACTION_DQ,
                "action_label":          f"→ Phase 03 | {supplier_label} (DQ) | Owner: {dq_owner.split()[0]} | Cat: {category}",
                "new_owner":             dq_owner,
                "sop":                   _next_pu(),
                "dq_supplier":           dq_supplier,
                "istep_raw":             istep_raw,
                "target_istep":          "No I-step Relevance",
                "assigned_ecu":          suggested_ecu,
                "ecu_current":           ecu_current,
                "blocking_reason_default": "not responsible",
                "defect":                defect,
            }

        if OWNER_MAX_MERTENS.lower() in current_owner_name.lower():
            new_owner = OWNER_DEFAULT
        elif is_pos:
            new_owner = OWNER_POSITIONING
        else:
            new_owner = OWNER_DEFAULT

        # Positioning tickets only need an owner change — skip if already correct
        pos_owner_search = OWNER_POSITIONING.split(",")[0].strip().lower()
        if is_pos and pos_owner_search in current_owner_name.lower():
            return None  # nothing to do
        
        sop = _next_pu()   # always suggest next upcoming PU
        # Target I-Step: "No I-step Relevance" for Navigation, blank for Positioning
        target_istep = "" if is_pos else "No I-step Relevance"
        pos_label = "Positioning" if is_pos else "Navigation"
        action_label = (
            f"→ Phase 03 | Positioning | Owner + First Use"
            if is_pos else
            f"→ Phase 04 | Navigation | Sol.Resp: cc_jira"
        )

        # Read current assigned ECU from ticket
        ecu_raw = defect.get("assigned_ecu_udf", {})
        ecu_current = (ecu_raw.get("name", "") if isinstance(ecu_raw, dict) else str(ecu_raw or ""))
        # Suggest closest matching ECU_OPTIONS entry
        suggested_ecu = ecu_current
        for opt in ECU_OPTIONS:
            if opt.lower() in ecu_current.lower() or ecu_current.lower() in opt.lower():
                suggested_ecu = opt
                break

        return {
            "defect_id":               defect_id,
            "defect_name":             defect_name,
            "action":                  self.ACTION_PHASE04,
            "action_label":            action_label,
            "is_positioning":          is_pos,
            "new_owner":               new_owner,
            "sop":                     sop,
            "istep_raw":               istep_raw,
            "target_istep":            target_istep,
            "dq_supplier":             None,
            "assigned_ecu":            suggested_ecu,
            "ecu_current":             ecu_current,
            "blocking_reason_default": "",
            "defect":                  defect,
        }
    
    # ── Preview table helper ─────────────────────────────────────────────

    @staticmethod
    def _cell(parent: tk.Widget, bg: str, col: str) -> tk.Frame:
        """Fixed-pixel-width frame cell for the preview table.
        Using pack_propagate(False) ensures the frame keeps its width
        regardless of the widget placed inside it."""
        f = tk.Frame(parent, bg=bg, width=_TBLCOL_PX[col], height=24)
        f.pack(side=tk.LEFT)
        f.pack_propagate(False)
        return f

    # ══════════════════════════════════════════════════════════════════════════
    # Preview panel
    # ══════════════════════════════════════════════════════════════════════════

    def _show_preview(self, planned: List[Dict[str, Any]]):
        """Render the action-preview table and show it (main thread)."""
        # Clear old rows
        for w in self.preview_rows_frame.winfo_children():
            w.destroy()
        
        reject_count  = sum(1 for a in planned if a["action"] == self.ACTION_REJECT)
        phase04_count = len(planned) - reject_count
        
        owner_options = [OWNER_POSITIONING, OWNER_DEFAULT]
        fu_options    = _next_pu_options()

        for i, action in enumerate(planned):
            is_reject = action["action"] == self.ACTION_REJECT
            exec_failed = action.get("exec_failed", False)
            if exec_failed:
                row_bg  = "#1f0d00"   # dark orange – execution failed
                row_brd = "#c04000"
            elif is_reject:
                row_bg  = "#180e0e"
                row_brd = "#5a1a1a"
            else:
                row_bg  = "#0b150f"
                row_brd = "#1a4a2a"
            row = tk.Frame(self.preview_rows_frame, bg=row_bg,
                           highlightthickness=1, highlightbackground=row_brd)
            row.pack(fill=tk.X, pady=1)
            action["_preview_row"] = row   # used by execution to remove row in-place

            # ── Checkbox ───────────────────────────────────────────────────
            check_var = tk.BooleanVar(value=True)
            action["check_var"] = check_var
            c_cell = self._cell(row, row_bg, "check")
            cb = tk.Checkbutton(c_cell, variable=check_var,
                                bg=row_bg, fg="#ffffff",
                                selectcolor=self.BG_COLOR,
                                activebackground=row_bg,
                                command=self._update_confirm_label,
                                padx=0)
            cb.pack(anchor=tk.CENTER, expand=True)

            # ── Ticket ID – clickable link ──────────────────────────────────
            did_val = action["defect_id"]
            id_cell = self._cell(row, row_bg, "id")
            id_lbl = tk.Label(id_cell, text=did_val,
                              bg=row_bg, fg=self.ACCENT2,
                              font=('Menlo', 9, 'underline'), anchor=tk.W,
                              padx=4, cursor="hand2")
            id_lbl.pack(fill=tk.BOTH, expand=True)
            octane_url = (f"{OCTANE_URL}/ui/?p={SHARED_SPACE}/{WORKSPACE}"
                          f"#/entity-navigation?entityType=work_item&id={did_val}")
            id_lbl.bind("<Button-1>", lambda e, url=octane_url: webbrowser.open(url))

            # ── Ticket name ──────────────────────────────────────────────────
            name_short = action["defect_name"][:44] + "…" \
                if len(action["defect_name"]) > 44 else action["defect_name"]
            titel_cell = self._cell(row, row_bg, "titel")
            tk.Label(titel_cell, text=name_short, bg=row_bg,
                     fg=self.FG_PRIMARY, font=('Menlo', 9),
                     anchor=tk.W, padx=4).pack(fill=tk.BOTH, expand=True)

            # ── Action label ─────────────────────────────────────────────────
            action_color = self.ERROR_COLOR if is_reject else self.SUCCESS_COLOR
            aktion_cell = self._cell(row, row_bg, "aktion")
            action_label_var = tk.StringVar(value=action["action_label"])
            action["action_label_var"] = action_label_var
            tk.Label(aktion_cell, textvariable=action_label_var,
                     bg=row_bg, fg=action_color,
                     font=('Menlo', 9, 'bold'),
                     anchor=tk.W, padx=4).pack(fill=tk.BOTH, expand=True)

            if not is_reject:
                # ── Owner dropdown ────────────────────────────────────────────
                is_dq_action = action["action"] == self.ACTION_DQ
                # DQ tickets offer HERE / Zenrin owners; nav tickets offer Nav / Positioning.
                row_owner_options = ([OWNER_DQ_HERE, OWNER_DQ_ZENRIN]
                                     if is_dq_action else
                                     [OWNER_POSITIONING, OWNER_DEFAULT])
                owner_default_val = action.get("new_owner") or (
                    OWNER_DQ_HERE if is_dq_action else OWNER_DEFAULT)
                owner_var = tk.StringVar(value=owner_default_val)
                action["owner_var"] = owner_var

                # Mutable cell: will hold tis_var once it is created below.
                _tis_var_cell: list = []

                def _on_owner_change(name, idx, op, a=action, v=owner_var,
                                     tc=_tis_var_cell, dq=is_dq_action):
                    chosen = v.get()
                    if dq:
                        # Update dq_supplier so execution uses the right category / ECU fallback.
                        a["dq_supplier"] = ("here"
                                            if OWNER_DQ_HERE.lower() in chosen.lower()
                                            else "zenrin")
                        return
                    is_pos = OWNER_POSITIONING.lower() in chosen.lower()
                    a["is_positioning"] = is_pos
                    new_lbl = ("→ Phase 03 | Positioning | Owner + First Use"
                               if is_pos else
                               "→ Phase 04 | Navigation | Sol.Resp: cc_jira")
                    a["action_label"] = new_lbl
                    a["action_label_var"].set(new_lbl)
                    # Sync Target I-Step: Positioning → not set; Navigation → No I-step Relevance.
                    if tc:
                        tc[0].set(TIS_EMPTY_LABEL if is_pos else "No I-step Relevance")

                owner_var.trace_add("write", _on_owner_change)

                om = tk.OptionMenu(self._cell(row, row_bg, "owner"), owner_var, *row_owner_options)
                om.config(font=('Helvetica', 8), width=30, relief=tk.FLAT,
                          bg=self.ACCENT, fg="#000000",
                          activebackground=self.ACCENT2, highlightthickness=0)
                om["menu"].config(font=('Helvetica', 9), bg=self.FRAME_BG, fg="#ffffff",
                                  activebackground=self.ACCENT, activeforeground="#ffffff")
                om.pack()

                # ── First Use dropdown ────────────────────────────────────────
                next_pu = _next_pu()
                sop_var = tk.StringVar(value=next_pu)
                action["sop_var"] = sop_var
                fu_options_ordered = [next_pu] + [o for o in fu_options if o != next_pu]
                sm = tk.OptionMenu(self._cell(row, row_bg, "sop"), sop_var, *fu_options_ordered)
                sm.config(font=('Helvetica', 8), width=6, relief=tk.FLAT,
                          bg=self.ACCENT, fg="#000000",
                          activebackground=self.ACCENT2, highlightthickness=0)
                sm["menu"].config(font=('Helvetica', 9), bg=self.FRAME_BG, fg="#ffffff",
                                  activebackground=self.ACCENT, activeforeground="#ffffff")
                sm.pack()

                # ── ECU dropdown ──────────────────────────────────────────────
                ecu_default = action.get("assigned_ecu") or ECU_OPTIONS[0]
                ecu_opts = [ecu_default] + [o for o in ECU_OPTIONS if o != ecu_default]
                ecu_var = tk.StringVar(value=ecu_default)
                action["ecu_var"] = ecu_var
                em = tk.OptionMenu(self._cell(row, row_bg, "ecu"), ecu_var, *ecu_opts)
                em.config(font=('Helvetica', 8), width=9, relief=tk.FLAT,
                          bg=self.ACCENT, fg="#000000",
                          activebackground=self.ACCENT2, highlightthickness=0)
                em["menu"].config(font=('Helvetica', 9), bg=self.FRAME_BG, fg="#ffffff",
                                  activebackground=self.ACCENT, activeforeground="#ffffff")
                em.pack()

                # ── Target I-Step dropdown ────────────────────────────────────
                tis_options = ["No I-step Relevance", TIS_EMPTY_LABEL]
                tis_default_raw = action.get("target_istep") or ""
                tis_default = tis_default_raw if tis_default_raw else TIS_EMPTY_LABEL
                tis_var = tk.StringVar(value=tis_default)
                action["target_istep_var"] = tis_var
                _tis_var_cell.append(tis_var)  # make accessible to _on_owner_change
                tis_opts_ordered = [tis_default] + [o for o in tis_options if o != tis_default]
                tm = tk.OptionMenu(self._cell(row, row_bg, "tis"), tis_var, *tis_opts_ordered)
                tm.config(font=('Helvetica', 8), width=16, relief=tk.FLAT,
                          bg=self.ACCENT, fg="#000000",
                          activebackground=self.ACCENT2, highlightthickness=0)
                tm["menu"].config(font=('Helvetica', 9), bg=self.FRAME_BG, fg="#ffffff",
                                  activebackground=self.ACCENT, activeforeground="#ffffff")
                tm.pack()

                # ── Blocking Reason dropdown ──────────────────────────────────
                br_opts = self._blocking_reason_options or _BLOCKING_REASON_FALLBACK
                br_default = action.get("blocking_reason_default") or ""
                br_var = tk.StringVar(value=br_default)
                action["blocking_reason_var"] = br_var
                br_opts_full = ([""] if "" not in br_opts else []) + [o for o in br_opts if o]
                br_opts_ordered = ([br_default] if br_default and br_default not in br_opts_full
                                   else []) + [""] + [o for o in br_opts_full if o != br_default]
                if not br_opts_ordered:
                    br_opts_ordered = [""]
                bm = tk.OptionMenu(self._cell(row, row_bg, "br"), br_var, *br_opts_ordered)
                bm.config(font=('Helvetica', 8), width=22, relief=tk.FLAT,
                          bg=self.ACCENT if not br_default else "#d47800",
                          fg="#000000",
                          activebackground=self.ACCENT2, highlightthickness=0)
                bm["menu"].config(font=('Helvetica', 9), bg=self.FRAME_BG, fg="#ffffff",
                                  activebackground=self.ACCENT, activeforeground="#ffffff")
                def _on_br_change(name, idx, op, w=bm, v=br_var):
                    w.config(bg="#d47800" if v.get() else self.ACCENT)
                br_var.trace_add("write", _on_br_change)
                bm.pack()

                # ── Kommentar (editable) ─────────────────────────────────────
                cmt_default = action.get("comment") or _DEFAULT_PROCESSED_CMT
                cmt_var = tk.StringVar(value=cmt_default)
                action["comment_var"] = cmt_var
                cmt_entry = tk.Entry(self._cell(row, row_bg, "kommentar"),
                                     textvariable=cmt_var,
                                     font=("Menlo", 8),
                                     bg=self.ACCENT, fg="#dddddd",
                                     insertbackground="#ffffff",
                                     relief=tk.FLAT, bd=0, width=26)
                cmt_entry.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

                # ── I-Step (read-only) ────────────────────────────────────────
                istep_display = action.get("istep_raw") or "\u2014"
                istep_cell = self._cell(row, row_bg, "istep")
                tk.Label(istep_cell, text=istep_display,
                         bg=row_bg, fg=self.FG_SECONDARY,
                         font=('Menlo', 8), anchor=tk.W,
                         padx=4).pack(fill=tk.BOTH, expand=True)

                # ── Undo placeholder (non-reject rows have no undo button) ───
                self._cell(row, row_bg, "undo")
            else:
                action["owner_var"] = None
                action["sop_var"]   = None
                action["ecu_var"]   = None
                action["target_istep_var"] = None
                # Pixel-width spacer cells for Owner / First Use / ECU / Target I-Step
                for col_key in ("owner", "sop", "ecu", "tis"):
                    self._cell(row, row_bg, col_key)   # empty fixed-width frame
                # Blocking Reason dropdown (same as non-reject rows)
                br_opts = self._blocking_reason_options or _BLOCKING_REASON_FALLBACK
                br_default = action.get("blocking_reason_default") or ""
                br_var = tk.StringVar(value=br_default)
                action["blocking_reason_var"] = br_var
                br_opts_full = ([""] if "" not in br_opts else []) + [o for o in br_opts if o]
                br_opts_ordered = ([br_default] if br_default and br_default not in br_opts_full
                                   else []) + [""] + [o for o in br_opts_full if o != br_default]
                if not br_opts_ordered:
                    br_opts_ordered = [""]
                bm = tk.OptionMenu(self._cell(row, row_bg, "br"), br_var, *br_opts_ordered)
                bm.config(font=('Helvetica', 8), width=22, relief=tk.FLAT,
                          bg=self.ACCENT if not br_default else "#d47800",
                          fg="#000000",
                          activebackground=self.ACCENT2, highlightthickness=0)
                bm["menu"].config(font=('Helvetica', 9), bg=self.FRAME_BG, fg="#ffffff",
                                  activebackground=self.ACCENT, activeforeground="#ffffff")
                def _on_br_change_rej(name, idx, op, w=bm, v=br_var):
                    w.config(bg="#d47800" if v.get() else self.ACCENT)
                br_var.trace_add("write", _on_br_change_rej)
                bm.pack()
                # Kommentar cell (reject rows: editable, pre-filled with auto-comment or default)
                cmt_default = action.get("comment") or _DEFAULT_PROCESSED_CMT
                cmt_var = tk.StringVar(value=cmt_default)
                action["comment_var"] = cmt_var
                cmt_entry = tk.Entry(self._cell(row, row_bg, "kommentar"),
                                     textvariable=cmt_var,
                                     font=("Menlo", 8),
                                     bg="#1a2050" if action.get("comment") else self.ACCENT,
                                     fg="#88aaff" if action.get("comment") else "#dddddd",
                                     insertbackground="#ffffff",
                                     relief=tk.FLAT, bd=0, width=26)
                cmt_entry.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
                # I-Step (read-only) for reject rows
                istep_display = action.get("istep_raw") or "—"
                istep_cell = self._cell(row, row_bg, "istep")
                tk.Label(istep_cell, text=istep_display,
                         bg=row_bg, fg=self.FG_SECONDARY,
                         font=('Menlo', 8), anchor=tk.W,
                         padx=4).pack(fill=tk.BOTH, expand=True)
                # ↩ Undo-Reject button
                undo_cell = self._cell(row, row_bg, "undo")
                tk.Button(undo_cell, text="↩ Revoke Reject",
                          font=('Helvetica', 8), bg="#1a2a3a", fg="#88ccff",
                          relief=tk.FLAT, cursor="hand2", bd=0, padx=2,
                          activebackground="#2a3a4a", activeforeground="#aaddff",
                          command=lambda a=action: self._undo_reject(a)
                          ).pack(anchor=tk.CENTER, expand=True)
        self._update_confirm_label()
        
        # Switch to the Aktionsvorschau tab so the user sees the results
        self._gui_do(lambda: self._content_nb.select(0))
        # Re-sync header canvas scrollregion after widgets have settled.
        # IMPORTANT: do NOT call update_idletasks() here – this method is
        # often invoked from inside a _gui_do callback (e.g. the execution
        # finally block).  update_idletasks() re-enters the Tk event loop
        # from within an after-driven callback, which hangs the poll loop on
        # macOS and prevents all subsequent queued callbacks (e.g. the
        # process_btn re-enable) from ever running.
        # after_idle defers the sync to the next idle cycle instead.
        def _sync_hdr():
            total_w = self.preview_rows_frame.winfo_reqwidth() or sum(_TBLCOL_PX.values())
            self._hdr_canvas.configure(scrollregion=(0, 0, total_w, 28))
            self._hdr_canvas.xview_moveto(0)
        self.root.after_idle(_sync_hdr)
    
    def _hide_preview(self):
        """Hide the preview panel (clears rows; preview_outer stays in its tab)."""
        for w in self.preview_rows_frame.winfo_children():
            w.destroy()
        self.planned_actions = []
        # Defensive: re-enable the process button whenever the preview closes
        # and execution is no longer running.  Guards against root.after(0,...)
        # callbacks not being flushed on macOS when no user events arrive.
        if not self.processing:
            try:
                self.process_btn.config(
                    state=tk.NORMAL, bg=self.BUTTON_BG, cursor="hand2")
            except Exception:
                pass
    
    def _undo_reject(self, action: Dict[str, Any]):
        """User pressed ↩ Revoke Reject on a REJECT row.
        Re-compute the action for the ticket without the attachment check
        (i.e. treat it as if attachments are fine) and refresh the preview."""
        defect = action.get("defect")
        if not defect:
            return
        did = action["defect_id"]
        new_action = self._compute_action(defect, skip_attachment_check=True)
        idx = next((i for i, a in enumerate(self.planned_actions)
                    if a["defect_id"] == did), None)
        if idx is None:
            return
        if new_action is None:
            # Would be skipped (already processed) → remove from list
            self.planned_actions.pop(idx)
            self._log(f"[{did}] ↩ Reject rückgängig – Ticket als bereits bearbeitet erkannt und entfernt.", "INFO")
        else:
            self.planned_actions[idx] = new_action
            self._log(f"[{did}] ↩ Reject rückgängig – neue Aktion: {new_action['action_label']}", "INFO")
        # Re-render the entire preview with the updated list
        self._gui_do(lambda: self._show_preview(self.planned_actions))

    def _cancel_preview(self):
        """User pressed Abbrechen – discard suggestions, do NOT advance offset."""
        self._hide_preview()
        self._log("Vorschau verworfen – keine Änderungen in OCTANE.", "WARNING")
        self._update_progress_label("Bereit")
        self.progress_value = 0
        self._draw_progress_bar()
        self.stats_label.config(text="")

    def _update_confirm_label(self):
        """Refresh the Ausführen button text to reflect currently checked tickets."""
        selected = [a for a in self.planned_actions
                    if not a.get("check_var") or a["check_var"].get()]
        phase04 = sum(1 for a in selected if a["action"] == self.ACTION_PHASE04)
        reject  = sum(1 for a in selected if a["action"] == self.ACTION_REJECT)
        dq      = sum(1 for a in selected if a["action"] == self.ACTION_DQ)
        total_sel = len(selected)
        total_all = len(self.planned_actions)
        dq_part = f",  {dq}× DQ" if dq else ""
        self.confirm_btn.config(
            text=f"✅ Ausführen  [{total_sel}/{total_all}]  ({phase04}× Phase 04,  {reject}× Reject{dq_part})")
        self.preview_count_label.config(
            text=f"{total_sel} von {total_all} ausgewählt")

    def _toggle_all_selection(self):
        """Toggle all checkboxes: if all are checked → uncheck all, else → check all."""
        if not self.planned_actions:
            return
        all_checked = all(
            a.get("check_var") and a["check_var"].get()
            for a in self.planned_actions
        )
        for action in self.planned_actions:
            if action.get("check_var"):
                action["check_var"].set(not all_checked)
        self.select_all_btn.config(
            text="☐ Alle  ☑ Keine" if all_checked else "☑ Alle  ☐ Keine")
        self._update_confirm_label()


    # ── Bee + confetti animation ─────────────────────────────────────────────

    def _show_bee_animation(self):
        """Overlay a cartoon honeybee flying across the window with confetti."""
        import math, random

        W = self.root.winfo_width()  or 1120
        H = self.root.winfo_height() or 860
        RX = self.root.winfo_rootx()
        RY = self.root.winfo_rooty()

        # ── Overlay toplevel (semi-transparent dark overlay) ───────────────
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.geometry(f"{W}x{H}+{RX}+{RY}")
        ov.lift()
        ov.attributes("-topmost", True)
        try:
            ov.attributes("-alpha", 0.88)      # semi-transparent on macOS/Win
        except Exception:
            pass

        cv = tk.Canvas(ov, width=W, height=H, bg=self.BG_COLOR,
                       highlightthickness=0)
        cv.pack(fill=tk.BOTH, expand=True)

        # ── Confetti pieces ───────────────────────────────────────────────
        CONFETTI_COLORS = ["#f5c518", "#e84040", "#1e9de8", "#14a874",
                           "#f0a500", "#cc44cc", "#ffffff", "#6ec6f5"]
        N_CONF = 60
        confetti = []
        for _ in range(N_CONF):
            x  = random.randint(0, W)
            y  = random.randint(-H, 0)        # start above screen
            w  = random.randint(6, 14)
            h  = random.randint(4, 9)
            col = random.choice(CONFETTI_COLORS)
            vy = random.uniform(3.5, 7.0)     # fall speed
            vx = random.uniform(-1.5, 1.5)
            angle = random.uniform(0, 360)
            spin  = random.uniform(-8, 8)
            cid = cv.create_rectangle(x, y, x+w, y+h, fill=col, outline="")
            confetti.append({"id": cid, "x": x, "y": y, "w": w, "h": h,
                              "vx": vx, "vy": vy,
                              "angle": angle, "spin": spin, "col": col})

        # ── Bee parts (drawn as canvas items, grouped by tag) ─────────────
        # Body = golden ellipse; stripes = black arcs; head = circle
        # Wings = two semi-transparent ovals; stinger = triangle
        # Eyes = small white+black dot; antennae = lines+dots
        BEE_TAG = "bee"

        def _draw_bee(cx, cy, scale=1.0):
            cv.delete(BEE_TAG)
            s = scale
            # Abdomen (main body)
            cv.create_oval(cx-28*s, cy-14*s, cx+22*s, cy+14*s,
                           fill="#f5c000", outline="#222", width=max(1,int(1.5*s)),
                           tags=BEE_TAG)
            # Black stripes on abdomen
            for sx in (-10, 2, 14):
                cv.create_oval(cx+(sx-5)*s, cy-14*s, cx+(sx+5)*s, cy+14*s,
                               fill="#111", outline="", tags=BEE_TAG)
            # Re-draw abdomen outline on top so stripes look clipped
            cv.create_oval(cx-28*s, cy-14*s, cx+22*s, cy+14*s,
                           fill="", outline="#222", width=max(1,int(1.5*s)),
                           tags=BEE_TAG)
            # Head
            cv.create_oval(cx+16*s, cy-11*s, cx+38*s, cy+11*s,
                           fill="#f5c000", outline="#222", width=max(1,int(1.5*s)),
                           tags=BEE_TAG)
            # Eye
            cv.create_oval(cx+27*s, cy-6*s, cx+35*s, cy+2*s,
                           fill="white", outline="", tags=BEE_TAG)
            cv.create_oval(cx+29*s, cy-4*s, cx+34*s, cy+1*s,
                           fill="#111", outline="", tags=BEE_TAG)
            # Smile
            cv.create_arc(cx+19*s, cy+1*s, cx+35*s, cy+12*s,
                          start=200, extent=140, style=tk.ARC,
                          outline="#333", width=max(1,int(1.5*s)), tags=BEE_TAG)
            # Antennae
            cv.create_line(cx+26*s, cy-10*s, cx+22*s, cy-26*s,
                           fill="#333", width=max(1,int(1.5*s)), tags=BEE_TAG)
            cv.create_oval(cx+19*s, cy-30*s, cx+25*s, cy-24*s,
                           fill="#111", outline="", tags=BEE_TAG)
            cv.create_line(cx+31*s, cy-10*s, cx+36*s, cy-25*s,
                           fill="#333", width=max(1,int(1.5*s)), tags=BEE_TAG)
            cv.create_oval(cx+33*s, cy-29*s, cx+39*s, cy-23*s,
                           fill="#111", outline="", tags=BEE_TAG)
            # Stinger (left tip)
            pts = [cx-28*s, cy-4*s,  cx-28*s, cy+4*s,  cx-38*s, cy]
            cv.create_polygon(pts, fill="#b8860b", outline="", tags=BEE_TAG)
            # Upper wing
            cv.create_oval(cx-18*s, cy-34*s, cx+16*s, cy-10*s,
                           fill="#d0eeff", outline="#88bbdd",
                           width=max(1,int(s)), tags=BEE_TAG)
            # Lower wing
            cv.create_oval(cx-10*s, cy-28*s, cx+14*s, cy-8*s,
                           fill="#c8e8ff", outline="#88bbdd",
                           width=max(1,int(s)), tags=BEE_TAG)

        # ── Animation state ───────────────────────────────────────────────
        DURATION_MS  = 3200   # total animation time
        FRAME_MS     = 33     # ~30 fps
        bee_x        = -60.0
        bee_y        = H * 0.38
        bee_vx       = W / (DURATION_MS / FRAME_MS) * 1.1  # cross screen
        frame        = [0]
        total_frames = DURATION_MS // FRAME_MS

        def _tick():
            nonlocal bee_x, bee_y
            f = frame[0]
            if f >= total_frames or not ov.winfo_exists():
                ov.destroy()
                return

            # Bee bobbing motion
            bob = math.sin(f * 0.28) * 12
            bee_x += bee_vx
            cur_y = bee_y + bob
            wing_flap_scale = 1.0 + 0.08 * math.sin(f * 0.9)  # subtle size pulse
            _draw_bee(bee_x, cur_y, scale=wing_flap_scale)

            # Move confetti
            for c in confetti:
                c["x"] += c["vx"]
                c["y"] += c["vy"]
                c["angle"] += c["spin"]
                # Rotate rectangle by redrawing as polygon
                cx2, cy2 = c["x"] + c["w"]/2, c["y"] + c["h"]/2
                hw, hh = c["w"]/2, c["h"]/2
                a = math.radians(c["angle"])
                cos_a, sin_a = math.cos(a), math.sin(a)
                corners = [(-hw,-hh),(hw,-hh),(hw,hh),(-hw,hh)]
                pts = []
                for dx, dy in corners:
                    pts += [cx2 + dx*cos_a - dy*sin_a,
                            cy2 + dx*sin_a + dy*cos_a]
                cv.coords(c["id"], *pts)
                # Wrap vertically (recycle from top when off screen)
                if c["y"] > H + 20:
                    c["y"] = random.randint(-30, -10)
                    c["x"] = random.randint(0, W)

            frame[0] += 1
            ov.after(FRAME_MS, _tick)

        _tick()

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2: Execution (write confirmed actions to OCTANE)
    # ══════════════════════════════════════════════════════════════════════════
    
    def _confirm_execute(self):
        if not self.planned_actions:
            return
        # Only execute tickets whose checkbox is checked
        actions = [a for a in self.planned_actions
                   if not a.get("check_var") or a["check_var"].get()]
        if not actions:
            messagebox.showinfo("Hinweis", "Keine Tickets ausgewählt.")
            return
        total_in_batch = len(self.planned_actions)  # capture before _hide_preview clears it
        if not messagebox.askyesno(
            "Bestätigen",
            f"Sollen {len(actions)} von {total_in_batch} Tickets jetzt in OCTANE bearbeitet werden?"
        ):
            return

        # ── Resolve all tkinter StringVar values HERE in the main thread ──────
        # Background threads must NOT call .get() on tkinter variables — it is
        # not thread-safe on macOS and silently returns empty/wrong strings.
        for a in actions:
            if a.get("owner_var"):
                a["_owner_resolved"]   = a["owner_var"].get()
            if a.get("sop_var"):
                a["_sop_resolved"]     = a["sop_var"].get()
            if a.get("ecu_var"):
                a["_ecu_resolved"]     = a["ecu_var"].get()
            if a.get("target_istep_var"):
                a["_tis_resolved"]     = a["target_istep_var"].get()
            if a.get("blocking_reason_var"):
                a["_br_resolved"]      = a["blocking_reason_var"].get()
            if a.get("comment_var"):
                a["_comment_resolved"] = a["comment_var"].get()

        # Offset is advanced in _execute_actions finally block (by succeeded count only)
        self._set_processing(True)
        self.progress_value = 0
        self._draw_progress_bar()
        # Switch to the Log tab so the user can follow execution progress
        try:
            self._content_nb.select(1)
        except Exception:
            pass
        threading.Thread(target=self._execute_actions, args=(actions,), daemon=True).start()
    
    def _execute_actions(self, actions: List[Dict[str, Any]]):
        """Execute all confirmed actions against OCTANE (with logging)."""
        total = len(actions)
        stats = {"ok": 0, "rejected": 0, "errors": 0}
        failed_ids: set = set()   # defined here so the finally block can always reference it
        
        try:
            self._log("=" * 60, "HEADER")
            self._log(f"AUSFÜHRUNG: {total} Tickets werden bearbeitet", "HEADER")
            self._log("=" * 60, "HEADER")

            for i, action in enumerate(actions):
                did = action["defect_id"]
                self._update_progress_label(f"Schreibe {i+1}/{total}: {did}")
                self._update_progress_bar((i + 1) / total * 100)
                self._log(f"\n[{did}] #{did} – {action['defect_name'][:60]}", "HEADER")
                # Shorthand: every sub-log line gets the ticket ID as prefix
                def log(msg, tag, _did=did): self._log(f"  [{_did}] {msg}", tag)

                try:
                    self.client.begin_batch(did)
                    if action["action"] == self.ACTION_REJECT:
                        # Step 1: Set Blocking Reason FIRST (OCTANE requires it before
                        # allowing the phase transition to 01-New)
                        br_val = action.get("_br_resolved") or action.get("blocking_reason_default") or ""
                        if br_val and self._blocking_reason_field:
                            ok2, msg2 = self.client.set_blocking_reason(
                                did, self._blocking_reason_field, br_val)
                            if ok2:
                                log(f"✅ Blocking Reason → {br_val}", "SUCCESS")
                            else:
                                log(f"⚠️  Blocking Reason-Fehler: {msg2}", "WARNING")
                        elif br_val and not self._blocking_reason_field:
                            log(f"ℹ️  Blocking Reason: {br_val} (Feld nicht gefunden)", "INFO")
                        # Step 2: Now change phase (blocking reason already written)
                        _reject_phase_ok = False
                        _reject_phase_msg = ""
                        for _rphase in ("01", "01-New", "New"):
                            ok, msg = self.client.set_phase(did, _rphase)
                            if ok:
                                log(f"✅ Phase → {_rphase} (Reject)", "SUCCESS")
                                stats["rejected"] += 1
                                _reject_phase_ok = True
                                break
                            _reject_phase_msg = msg
                        if not _reject_phase_ok:
                            log(f"❌ Phase-Fehler (01 / 01-New / New): {_reject_phase_msg}", "ERROR")
                            stats["errors"] += 1
                            failed_ids.add(did)
                        # Save ticket
                        ok_s, msg_s = self.client.commit_batch()
                        if ok_s:
                            log("💾 Ticket gespeichert", "SUCCESS")
                            cmt_text = action.get("_comment_resolved") or _DEFAULT_PROCESSED_CMT
                            ok_c, msg_c = self.client.post_comment(did, cmt_text)
                            if ok_c:
                                log(f"✅ Kommentar gepostet: {cmt_text}", "SUCCESS")
                            else:
                                log(f"❌ Kommentar-Fehler: {msg_c}", "ERROR")
                        else:
                            log(f"❌ Speichern fehlgeschlagen: {msg_s}", "ERROR")
                            stats["errors"] += 1
                            failed_ids.add(did)

                    elif action["action"] == self.ACTION_DQ:
                        errors_here = []
                        supplier = action.get("dq_supplier", "here")
                        is_here   = supplier == "here"
                        supplier_label = "HERE" if is_here else "Zenrin"

                        # Per-supplier owner
                        owner_name = (action.get("_owner_resolved")
                                      or (OWNER_DQ_HERE if is_here else OWNER_DQ_ZENRIN))
                        ok, msg = self.client.set_owner(did, owner_name)
                        if ok:
                            log(f"✅ Owner → {owner_name}", "SUCCESS")
                        else:
                            log(f"⚠️  Owner-Fehler: {msg}", "WARNING")
                            errors_here.append(msg)

                        # Assigned ECU (HERE or Zenrin)
                        ecu_val = action.get("_ecu_resolved") or action.get("assigned_ecu") or supplier_label
                        ok, msg = self.client.set_assigned_ecu(did, ecu_val)
                        if ok:
                            log(f"✅ Assigned ECU → {ecu_val}", "SUCCESS")
                        else:
                            log(f"⚠️  Assigned ECU-Fehler: {msg}", "WARNING")

                        # Defect Category
                        category = "IDC_mapdata" if is_here else "Road Map Japan"
                        ok, msg = self.client.set_problem_category(did, category)
                        if ok:
                            log(f"✅ Defect Category → {category}", "SUCCESS")
                        else:
                            log(f"⚠️  Category-Fehler: {msg}", "WARNING")

                        # Blocking Reason: "not responsible"
                        br_val = "not responsible"
                        if self._blocking_reason_field:
                            ok, msg = self.client.set_blocking_reason(
                                did, self._blocking_reason_field, br_val)
                            if ok:
                                log(f"✅ Blocking Reason → {br_val}", "SUCCESS")
                            else:
                                log(f"⚠️  Blocking Reason-Fehler: {msg}", "WARNING")

                        # Lat | Lon (HERE only) – extract from ticket description
                        if is_here:
                            defect_data = action.get("defect", {})
                            desc_raw = (defect_data.get("description") or "")
                            # description is not fetched during search to save bandwidth —
                            # fetch it now on demand.
                            if not desc_raw:
                                try:
                                    rd = self.client.session.get(
                                        f"{BASE}/defects/{did}",
                                        params={"fields": "description"},
                                        verify=False, timeout=20)
                                    if rd.ok:
                                        desc_raw = rd.json().get("description") or ""
                                except Exception:
                                    pass
                            import re as _re
                            desc_plain = _re.sub(r'<[^>]+>', ' ', desc_raw)
                            coords = _re.findall(
                                r'(-?\d{1,3}\.\d{4,})[,\s|/]+(-?\d{1,3}\.\d{4,})',
                                desc_plain)
                            if coords:
                                try:
                                    lat_f = float(coords[0][0])
                                    lon_f = float(coords[0][1])
                                    ok, msg = self.client.set_latlon(did, lat_f, lon_f)
                                    if ok:
                                        log(f"✅ Lat|Lon → {lat_f:.6f} | {lon_f:.6f}", "SUCCESS")
                                    else:
                                        log(f"⚠️  Lat/Lon-Fehler: {msg}", "WARNING")
                                except ValueError:
                                    log("⚠️  Lat/Lon: Koordinaten konnten nicht geparst werden", "WARNING")
                            else:
                                log("ℹ️  Lat/Lon: keine Koordinaten in Beschreibung gefunden", "INFO")

                        # Phase 03-In Analysis – set last so all fields (owner,
                        # ECU, category, blocking reason) are already present.
                        ok, msg = self.client.set_phase(did, "03")
                        if ok:
                            log("✅ Phase → 03-In Analysis", "SUCCESS")
                        else:
                            log(f"❌ Phase-Fehler: {msg}", "ERROR")
                            errors_here.append(msg)

                        # Save ticket
                        ok_s, msg_s = self.client.commit_batch()
                        if ok_s:
                            log("💾 Ticket gespeichert", "SUCCESS")
                            cmt_text = action.get("_comment_resolved") or _DEFAULT_PROCESSED_CMT
                            ok_c, msg_c = self.client.post_comment(did, cmt_text)
                            if ok_c:
                                log(f"✅ Kommentar gepostet: {cmt_text}", "SUCCESS")
                            else:
                                log(f"❌ Kommentar-Fehler: {msg_c}", "ERROR")
                        else:
                            log(f"❌ Speichern fehlgeschlagen: {msg_s}", "ERROR")
                            errors_here.append(msg_s)

                        if errors_here:
                            stats["errors"] += 1
                            failed_ids.add(did)
                        else:
                            stats["ok"] += 1

                    else:  # ACTION_PHASE04  (Navigation or Positioning)
                        errors_here = []
                        is_pos_action = action.get("is_positioning", False)

                        # Owner
                        owner_name = action.get("_owner_resolved") or action.get("new_owner") or ""
                        if owner_name:
                            ok, msg = self.client.set_owner(did, owner_name)
                            if ok:
                                log(f"✅ Owner → {owner_name}", "SUCCESS")
                            else:
                                log(f"⚠️  Owner-Fehler: {msg}", "WARNING")
                                errors_here.append(msg)

                        # First Use / SOP
                        sop_val = action.get("_sop_resolved") or action.get("sop") or ""
                        if sop_val and sop_val not in ("unbekannt", ""):
                            if self._first_use_field:
                                ok, msg = self.client.set_first_use(
                                    did, self._first_use_field, sop_val)
                                if ok:
                                    log(f"✅ First Use → {sop_val}", "SUCCESS")
                                else:
                                    log(f"⚠️  First Use-Fehler: {msg}", "WARNING")
                            else:
                                log(f"ℹ️  First Use: {sop_val} (Feld nicht gefunden)", "INFO")

                        if is_pos_action:
                            log("ℹ️  Positioning → Phase 03 bleibt, kein Phase/ECU/Sol.Resp-Update", "INFO")
                            # Speichere Owner + First Use für Positioning
                            ok_s, msg_s = self.client.commit_batch()
                            if ok_s:
                                log("💾 Ticket gespeichert", "SUCCESS")
                                cmt_text = action.get("_comment_resolved") or _DEFAULT_PROCESSED_CMT
                                ok_c, msg_c = self.client.post_comment(did, cmt_text)
                                if ok_c:
                                    log(f"✅ Kommentar gepostet: {cmt_text}", "SUCCESS")
                                else:
                                    log(f"❌ Kommentar-Fehler: {msg_c}", "ERROR")
                            else:
                                log(f"❌ Speichern fehlgeschlagen: {msg_s}", "ERROR")
                                errors_here.append(msg_s)
                        else:
                            # Solution Cluster
                            ok, msg = self.client.set_solution_cluster(did, "Navigation")
                            if ok:
                                log("✅ Solution Cluster → Navigation", "SUCCESS")
                            else:
                                log(f"⚠️  Solution Cluster-Fehler: {msg}", "WARNING")

                            # Phase 04
                            ok, msg = self.client.set_phase(did, "04")
                            if ok:
                                log("✅ Phase → 04", "SUCCESS")
                            else:
                                log(f"❌ Phase-Fehler: {msg}", "ERROR")
                                errors_here.append(msg)

                            # Speichere Owner + First Use + Solution Cluster direkt nach Phase-04-Setzung
                            ok_s, msg_s = self.client.commit_batch()
                            if ok_s:
                                log("💾 Vorfelder gespeichert", "SUCCESS")
                                cmt_text = action.get("_comment_resolved") or _DEFAULT_PROCESSED_CMT
                                ok_c, msg_c = self.client.post_comment(did, cmt_text)
                                if ok_c:
                                    log(f"✅ Kommentar gepostet: {cmt_text}", "SUCCESS")
                                else:
                                    log(f"❌ Kommentar-Fehler: {msg_c}", "ERROR")
                            else:
                                log(f"❌ Speichern (vor Phase) fehlgeschlagen: {msg_s}", "ERROR")
                                errors_here.append(msg_s)

                            # Neue Batch-Session für Felder nach Phase-04-Setzung
                            self.client.begin_batch(did)

                            # Target I-Step
                            tis_val = action.get("_tis_resolved") or action.get("target_istep") or ""
                            if tis_val in (TIS_EMPTY_LABEL, "No I-step Relevance", "–", "keine"):
                                tis_val = ""
                            if tis_val and self._target_istep_field:
                                ok, msg = self.client.set_target_istep(
                                    did, self._target_istep_field, tis_val)
                                if ok:
                                    log(f"✅ Target I-Step → {tis_val}", "SUCCESS")
                                else:
                                    log(f"⚠️  Target I-Step-Fehler: {msg}", "WARNING")
                            elif tis_val and not self._target_istep_field:
                                log(f"ℹ️  Target I-Step: {tis_val} (Feld nicht gefunden)", "INFO")

                            # Solution Responsible
                            ok, msg = self.client.set_solution_responsible(did, "cc_jira")
                            if ok:
                                log("✅ Solution Responsible → cc_jira", "SUCCESS")
                            else:
                                log(f"⚠️  Sol.Resp.-Fehler: {msg}", "WARNING")

                            # Assigned ECU
                            ecu_val = action.get("_ecu_resolved") or action.get("assigned_ecu") or ""
                            if ecu_val:
                                ok, msg = self.client.set_assigned_ecu(did, ecu_val)
                                if ok:
                                    log(f"✅ Assigned ECU → {ecu_val}", "SUCCESS")
                                else:
                                    log(f"⚠️  Assigned ECU-Fehler: {msg}", "WARNING")

                            # Blocking Reason
                            br_val = action.get("_br_resolved") or action.get("blocking_reason_default") or ""
                            if br_val and self._blocking_reason_field:
                                ok, msg = self.client.set_blocking_reason(
                                    did, self._blocking_reason_field, br_val)
                                if ok:
                                    log(f"✅ Blocking Reason → {br_val}", "SUCCESS")
                                else:
                                    log(f"⚠️  Blocking Reason-Fehler: {msg}", "WARNING")
                            elif br_val and not self._blocking_reason_field:
                                log(f"ℹ️  Blocking Reason: {br_val} (Feld nicht gefunden)", "INFO")

                            # Speichere Felder nach Phase-04-Setzung
                            ok_s, msg_s = self.client.commit_batch()
                            if ok_s:
                                log("💾 Ticket gespeichert", "SUCCESS")
                            else:
                                log(f"❌ Speichern fehlgeschlagen: {msg_s}", "ERROR")
                                errors_here.append(msg_s)

                        if errors_here:
                            stats["errors"] += 1
                            failed_ids.add(did)
                        else:
                            stats["ok"] += 1

                except Exception as e:
                    self.client.cancel_batch()
                    log(f"❌ Unerwarteter Fehler: {e}", "ERROR")
                    stats["errors"] += 1
                    failed_ids.add(did)
                
                self._update_stats(
                    f"Abgeschlossen: {stats['ok']}  |  "
                    f"Reject: {stats['rejected']}  |  "
                    f"Fehler: {stats['errors']}  |  "
                    f"({i+1}/{total})"
                )
                # Live: remove successfully processed row from the preview table
                if did not in failed_ids:
                    # Remove from planned_actions so the final re-render doesn't show it
                    self.planned_actions = [a for a in self.planned_actions
                                            if a["defect_id"] != did]
                    # Destroy only the one row widget in-place — no tab switch, no full re-render
                    _row_ref = action.get("_preview_row")
                    if _row_ref:
                        self._gui_do(lambda w=_row_ref: w.winfo_exists() and w.destroy())
                    self._gui_do(self._update_confirm_label)
                    ub = getattr(self, '_unprocessed_count', self._total_available) or self._total_available
                    rt_live = max(0, ub - (self._processed_count + stats['ok'] + stats['rejected']))
                    self._gui_do(lambda rt=rt_live, t=self._total_available:
                        self.total_count_label.config(
                            text=f"📋 Phase 03 Nav. ECE/JP/US: {rt} noch offen ({t} gesamt)"))
            
            self._log("\n" + "=" * 60, "HEADER")
            self._log("AUSFÜHRUNG ABGESCHLOSSEN", "HEADER")
            self._log(f"  Verarbeitet: {stats['ok']}", "SUCCESS")
            self._log(f"  Rejected:    {stats['rejected']}", "WARNING")
            self._log(f"  Fehler:      {stats['errors']}",
                      "ERROR" if stats["errors"] else "INFO")
            self._log("=" * 60, "HEADER")
        
        except Exception as e:
            self._log(f"KRITISCHER FEHLER: {e}", "ERROR")
        finally:
            # Determine which tickets actually succeeded
            executed_ids  = {a["defect_id"] for a in actions}
            succeeded_ids = executed_ids - failed_ids
            # Mark failed actions so the re-rendered row can be highlighted
            for a in actions:
                if a["defect_id"] in failed_ids:
                    a["exec_failed"] = True
            remaining = [a for a in self.planned_actions
                         if a["defect_id"] not in succeeded_ids]
            self.planned_actions = remaining
            # Update processed counter with actual successes only
            succeeded_count = len(succeeded_ids)
            self._processed_count += succeeded_count
            self._set_processing(False)
            unprocessed_base = getattr(self, '_unprocessed_count', self._total_available)
            remaining_total = max(0, unprocessed_base - self._processed_count)
            done_label = (f"Batch abgeschlossen. Noch {remaining_total} Ticket(s) ausstehend."
                          if remaining_total else "Alle Tickets bearbeitet ✅")
            self._update_progress_label(done_label)
            self._gui_do(lambda rt=remaining_total, t=self._total_available: self.total_count_label.config(
                text=f"📋 Phase 03 Nav. ECE/JP/US: {rt} noch offen ({t} gesamt)"))
            self._gui_do(lambda: self.batch_info_label.config(
                text=f"Bearbeitet: {self._processed_count} | Ausstehend: {remaining_total} | "
                     f"Gesamt: {self._total_available}"))
            self._update_process_btn_label()
            # Re-render preview with leftover tickets (unselected or failed ones)
            if remaining:
                self._gui_do(lambda r=remaining: self._show_preview(r))
            else:
                self._gui_do(self._hide_preview)
            # Belt-and-suspenders: unconditionally re-enable the Analyse button as
            # the very last queued item, AFTER _show_preview/_hide_preview complete.
            # Ensures the button is never left grey regardless of which render path ran.
            _btn_bg = self.BUTTON_BG
            self._gui_do(lambda: self.process_btn.config(
                state=tk.NORMAL, bg=_btn_bg, cursor="hand2"))


# ══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    app = OctaneTicketProcessorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
