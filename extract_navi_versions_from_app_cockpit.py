#!/usr/bin/env python3
"""
extract_navi_versions_from_app_cockpit.py
-----------------------------------------
Standalone helper – fetch Navigation App 2.19.x / 2.20.x version names
from the Jira project-versions API and print them as JSON.

Usage:
    python3 extract_navi_versions_from_app_cockpit.py [--json] [--project NAVI]

The script authenticates via ~/.netrc (host: jira.cc.bmwgroup.net).
When --json is given it prints a single JSON line that can be consumed by
api_versions() in octane_dm_tool.py if you ever want to switch back from
the hardcoded list to a live fetch.

Example output:
    {"versions_20x": ["2.20.6", "2.20.4", ...], "versions_19x": ["2.19.5", ...]}
"""

import argparse
import json
import netrc
import re
import sys
from urllib.request import Request, urlopen

JIRA_URL     = "https://jira.cc.bmwgroup.net"
PROJECT_KEY  = "NAVI"          # adjust if the Jira project key differs
VERSION_RE   = re.compile(r"^2\.(19|20)\.\d+$")


def _basic_auth(host: str) -> str:
    """Return a Basic-auth header value using credentials from ~/.netrc."""
    try:
        n = netrc.netrc()
        login, _, password = n.authenticators(host)
        import base64
        token = base64.b64encode(f"{login}:{password}".encode()).decode()
        return f"Basic {token}"
    except Exception as e:
        sys.exit(f"[error] Could not read ~/.netrc for {host}: {e}")


def fetch_versions(project: str, auth_header: str) -> dict:
    url = f"{JIRA_URL}/rest/api/2/project/{project}/versions"
    req = Request(url, headers={"Authorization": auth_header, "Accept": "application/json"})
    with urlopen(req, timeout=30) as r:
        versions = json.loads(r.read())

    v20, v19 = [], []
    for v in versions:
        name = v.get("name", "")
        m = VERSION_RE.match(name)
        if not m:
            continue
        if m.group(1) == "20":
            v20.append(name)
        else:
            v19.append(name)

    # Sort descending (semver-compatible for x.y.z strings)
    def _key(s):
        return tuple(int(p) for p in s.split("."))

    v20.sort(key=_key, reverse=True)
    v19.sort(key=_key, reverse=True)
    return {"versions_20x": v20, "versions_19x": v19}


def main():
    parser = argparse.ArgumentParser(description="Fetch Navi App versions from Jira")
    parser.add_argument("--json", action="store_true", help="Output raw JSON line (for machine consumption)")
    parser.add_argument("--project", default=PROJECT_KEY, help=f"Jira project key (default: {PROJECT_KEY})")
    args = parser.parse_args()

    auth = _basic_auth("jira.cc.bmwgroup.net")
    data = fetch_versions(args.project, auth)

    if args.json:
        print(json.dumps(data))
    else:
        print("Navigation App 2.20.x:")
        for v in data["versions_20x"]:
            print(f"  {v}")
        print("Navigation App 2.19.x:")
        for v in data["versions_19x"]:
            print(f"  {v}")


if __name__ == "__main__":
    main()
