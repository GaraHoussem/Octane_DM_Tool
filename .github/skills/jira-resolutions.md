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

## Custom Field IDs

| Field ID | Field Name | Type | Description |
|---|---|---|---|
| `customfield_10812` | Integrated in Version(s) | option | The software version(s) the fix was integrated into |
| `customfield_11202` | Pre-Integrated in Version(s) | array | Branch/version where the fix is pre-integrated (e.g. `apinext/navigation-app/release/2.20.6`) |
| `customfield_10811` | Resolved in Version(s) | option | The version in which the issue was resolved |
| `customfield_10809` | Found in SW Version(s) | option | The software version where the defect was found |
| `customfield_10810` | Found in HW Version(s) | option | The hardware version where the defect was found |
| `customfield_10300` | Domain | option | Domain category (e.g. "Navigation") |
| `customfield_10804` | Defect Category | option | Application category (e.g. "Application Navigation ECE") |
| `customfield_10115` | Team | any | Responsible team (e.g. "APINEXT: Honey Bee") |
| `customfield_10813` | — | option | Platform identifier (e.g. "NA5") |
| `customfield_11200` | Integration Due Date | date | Deadline for integration |
| `customfield_11201` | Integration Requests | — | Integration request tracking |

## Jira Instance

- **URL:** `https://jira.cc.bmwgroup.net`
- **Auth:** Bearer token (Personal Access Token)
- **API:** REST API v2 (`/rest/api/2/`)
