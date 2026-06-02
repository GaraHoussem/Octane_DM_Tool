#!/usr/bin/env python3
"""Debug script: inspect OCTANE defect fields and list_nodes for target_i_step_udf.

Usage: python3 debug_octane_fields.py <TOKEN>
"""

import json, os, sys, requests, urllib3
urllib3.disable_warnings()

OCTANE_URL = "https://octane-prod.bmwgroup.net"
SHARED_SPACE = "1002"
WORKSPACE = "2001"
BASE = f"{OCTANE_URL}/api/shared_spaces/{SHARED_SPACE}/workspaces/{WORKSPACE}"
DEFECT_ID = "2711195"

token = sys.argv[1] if len(sys.argv) > 1 else None
if not token:
    token = os.environ.get("OCTANE_TOKEN")
if not token:
    # Parse .netrc manually (stdlib netrc chokes on some entries)
    netrc_path = os.path.expanduser("~/.netrc")
    if os.path.exists(netrc_path):
        with open(netrc_path) as f:
            lines = f.read().splitlines()
        found_machine = False
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            if "machine" in line and "octane-prod.bmwgroup.net" in line:
                found_machine = True
                # Check if password is on the same line
                if "password" in parts:
                    idx = parts.index("password")
                    if idx + 1 < len(parts):
                        token = parts[idx + 1]
                        break
                continue
            if found_machine and "password" in parts:
                idx = parts.index("password")
                if idx + 1 < len(parts):
                    token = parts[idx + 1]
                break
if not token:
    token = input("Enter OCTANE access_token: ").strip()

s = requests.Session()
s.verify = False
s.headers.update({"Accept": "application/json", "Content-Type": "application/json", "HPECLIENTTYPE": "HPE_MQM_UI"})
bad = set('",;\\')
clean = "".join(c for c in token if 0x20 < ord(c) < 0x7F and c not in bad)
s.cookies.set("access_token", clean, domain="octane-prod.bmwgroup.net")

print("=" * 60)
print(f"1. Fetching defect {DEFECT_ID} (all fields)")
print("=" * 60)
r = s.get(f"{BASE}/defects/{DEFECT_ID}", timeout=30)
print(f"   Status: {r.status_code}")
if r.ok:
    data = r.json()
    # Find all fields with "target" or "istep" or "week" in the name
    for key, val in sorted(data.items()):
        kl = key.lower()
        if any(kw in kl for kw in ("target", "istep", "i_step", "week", "kw")):
            print(f"   {key} = {json.dumps(val, indent=4)}")
else:
    print(f"   ERROR: {r.text[:500]}")
    exit(1)

print()
print("=" * 60)
print("2. Metadata: fields matching 'target' or 'i_step' or 'week'")
print("=" * 60)
r = s.get(f"{BASE}/metadata/fields", params={"entity_name": "defect", "limit": 500}, timeout=30)
print(f"   Status: {r.status_code}")
if r.ok:
    body = r.json()
    fields = body.get("data", body.get("fields", []))
    print(f"   Total fields returned: {len(fields)}")
    for fld in fields:
        name = fld.get("name", "")
        nl = name.lower()
        if any(kw in nl for kw in ("target", "istep", "i_step", "week", "kw")):
            print(f"\n   Field: {name}")
            print(f"     label: {fld.get('label')}")
            print(f"     field_type: {fld.get('field_type')}")
            print(f"     list_root: {fld.get('list_root')}")
            lr = fld.get("list_root")
            if isinstance(lr, dict) and lr.get("id"):
                root_id = lr["id"]
                print(f"     → Fetching list_nodes under root {root_id}...")
                r2 = s.get(f"{BASE}/list_nodes", params={
                    "fields": "id,name,logical_name",
                    "query": f'"list_root EQ {{id={root_id}}}"',
                    "limit": 200,
                }, timeout=30)
                if r2.ok:
                    nodes = r2.json().get("data", [])
                    print(f"     → {len(nodes)} list_node(s):")
                    for n in nodes:
                        print(f"        {n.get('name')!r}  id={n.get('id')}  logical={n.get('logical_name')}")
                else:
                    print(f"     → list_nodes query failed: {r2.status_code}")
else:
    print(f"   ERROR: {r.text[:500]}")

print()
print("=" * 60)
print("3. Direct field query: specific candidates")
print("=" * 60)
candidates = [
    "target_i_step_udf", "target_i_steps_udf", "target_i_step",
    "target_istep_udf", "target_istep",
    "target_week_udf", "target_week",
]
fields_str = ",".join(["id"] + candidates)
r = s.get(f"{BASE}/defects/{DEFECT_ID}", params={"fields": fields_str}, timeout=30)
print(f"   Status: {r.status_code}")
if r.ok:
    data = r.json()
    for c in candidates:
        if c in data:
            print(f"   {c} = {json.dumps(data[c], indent=4)}")
        else:
            print(f"   {c} → NOT PRESENT in response")
else:
    print(f"   ERROR: {r.text[:500]}")
