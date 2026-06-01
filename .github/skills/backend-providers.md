---
name: backend-providers
description: "Reference for backend provider routing when a Rejected Jira ticket needs to be reassigned to a backend team in OCTANE. Use when a rejection comment indicates the issue must be checked from the backend side (HERE, Zenrin, LOS, etc.)."
---

# Backend Provider Routing — Rejected Tickets

When a Jira ticket is Rejected and the rejection comment indicates the issue must
be checked from the backend side, the OCTANE defect must be reassigned to the
appropriate backend provider. Each provider has a specific set of OCTANE fields.

## Providers

### 1. Map Data Issues (HERE)

| Field | Value |
|---|---|
| Assigned ECU | HERE |
| Owner | Tobias Naumann |
| Defect Category | IDC_mapdata |
| Phase | In Analysis (phase 3) |
| Blocking Reason | Not Responsible |

### 2. Japan Backend Map Provider (Zenrin)

| Field | Value |
|---|---|
| Assigned ECU | Zenrin |
| Owner | Jinglei Huang |
| Defect Category | Road Map Japan |
| Phase | In Analysis (phase 3) |
| Blocking Reason | Not Responsible |

### 3. Point of Interest / Search Content (HERE)

| Field | Value |
|---|---|
| Assigned ECU | HERE |
| Owner | Christoph Schoerner |
| Defect Category | Online_Content_HERE |
| Phase | In Analysis (phase 3) |
| Blocking Reason | Not Responsible |

### 4. LOS Backend

| Field | Value |
|---|---|
| Owner | Oertelt Stephan, DE-701 |
| Solution Responsible | bmw_ATC-Jira |
| Assigned ECU | BACKEND_GLOBAL |
| Defect Category | Offboard LOS |
| Phase | In Analysis (phase 3) |
| Blocking Reason | Not Responsible |

### 5. Traffic Content (HERE)

| Field | Value |
|---|---|
| Owner | Cornelia Schrei |
| Assigned ECU | HERE |
| Defect Category | Traffic Information |
| Phase | In Analysis (phase 3) |
| Blocking Reason | Not Responsible |

### 6. FuDe / Learning

| Field | Value |
|---|---|
| Owner | Simon Springmann |
| Defect Category | FuDe_Backend |
| Phase | In Analysis (phase 3) |

### 7. Perseus

| Field | Value |
|---|---|
| Assigned ECU | BACKEND_GLOBAL |
| Defect Category | Offboard PERSEUS |
| Phase | In Analysis (phase 3) |
| Solution Responsible | bmw_ATC-Jira |

## Comment Detection

### Generic Backend Keywords

Comments that indicate a ticket needs backend routing (case-insensitive):

- "must be checked from backend"
- "please check in HERE backend"
- "please assign to HERE"
- "must be assigned to HERE"
- "must be checked from DB"
- "DB issue"
- "BE issue"

Abbreviations:
- **DB** = database
- **BE** = backend

These may appear in various combinations and phrasings.

### Provider-Specific Indicators

Comments may also contain explicit field assignments that identify the provider:

```
Assigned ECU: HERE
Defect Category: Online_Content_HERE
```

The script should match **Defect Category** and **Assigned ECU** values from comments
to determine which provider the ticket belongs to.

### Provider Identification Priority

1. **Explicit Defect Category in comment** — most reliable, maps directly to a provider
2. **Assigned ECU in comment** — narrows to a subset of providers
3. **Generic backend keywords** — indicates backend routing but provider may be ambiguous

### Defect Category → Provider Mapping

| Defect Category | Provider |
|---|---|
| IDC_mapdata | Map Data Issues (HERE) |
| Road Map Japan | Japan Backend (Zenrin) |
| Online_Content_HERE | POI / Search Content (HERE) |
| Offboard LOS | LOS Backend |
| Traffic Information | Traffic Content (HERE) |
| FuDe_Backend | FuDe / Learning |
| Offboard PERSEUS | Perseus |

### Assigned ECU → Provider Mapping (when no Defect Category)

| Assigned ECU | Possible Providers |
|---|---|
| HERE | Map Data, POI/Search Content, Traffic Content (ambiguous — needs further context) |
| Zenrin | Japan Backend |
| BACKEND_GLOBAL | LOS Backend or Perseus (ambiguous — needs Defect Category) |
