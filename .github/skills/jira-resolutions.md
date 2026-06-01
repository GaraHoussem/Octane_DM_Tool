---
name: jira-resolutions
description: "Reference for available Jira resolution values in the BMW IDCEVODEV project (jira.cc.bmwgroup.net). Use when working with Jira ticket resolution logic, filtering, or classification."
---

# Jira Resolutions — BMW IDCEVODEV

## Available Resolution Values

The following resolutions are configured in the BMW Jira instance (`jira.cc.bmwgroup.net`) for the IDCEVODEV project:

| Resolution | Description |
|---|---|
| **Rejected** | Ticket was evaluated and intentionally rejected (e.g. expected behavior, not a valid defect) |
| **Duplicate** | Issue is a duplicate of another ticket |
| **Cannot Reproduce** | The reported issue could not be reproduced |
| **Deficient** | Ticket lacks sufficient information or quality |
| **Won't Do** | Issue acknowledged but will not be addressed |
| **Done** | Work completed successfully |

## Notes

- Only **Rejected** indicates the ticket was actively dismissed as invalid/not-a-bug.
- **Won't Do** means the issue is valid but deprioritized — it is NOT the same as Rejected.
- **Deficient** means the ticket itself is low quality (missing traces, unclear description), not that the reported issue is invalid.
- When checking for "expected behavior" rejections, scan comments (newest first) for keywords like:
  - "works as specified"
  - "works as expected"
  - "works as designed"
  - "expected behavior" / "expected behaviour"
  - "this is expected"
  - "as designed" / "by design"
  - "per specification" / "per spec"

## Jira Instance

- **URL:** `https://jira.cc.bmwgroup.net`
- **Auth:** Bearer token (Personal Access Token)
- **API:** REST API v2 (`/rest/api/2/`)
