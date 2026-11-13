# Field feedback

This folder tracks real task outcomes from MCP users and turns capability gaps and defects into follow-up work.

- [Usage log](usage-log.md): one sanitized entry per task that invoked Frappe MCP, including read-only calls and failures.
- [Feature requests](feature-requests.md): Frappe operations needed by a task that the current tools do not provide.
- [Bugs](bugs.md): reproducible cases where an existing tool fails or returns an incorrect result.
- [Sanitized CubaZone task reports](cubazone/): task-level MCP activity and outcomes imported from local project records.

## Recording rules

- Record observations, not assumptions. A successful tool call does not prove the task succeeded; verify and report both outcomes separately.
- Link a safe, reviewed evidence artifact when one exists. Do not store raw tool responses, credentials, private site URLs, customer data, business records, or screenshots containing them here.
- Use a public issue, pull request, or sanitized task slug as the task reference. Omit private tracker IDs and internal names unless they are already public and approved for publication.
- Include failed attempts and permission denials in the usage log. A permission denial or intentional safety refusal is not a bug.
- Before adding feedback, search these files for an existing open item. Update that item when it describes the same gap or defect.
- Keep request and bug IDs stable (`FR-YYYY-NNN`, `BUG-YYYY-NNN`). Set status to `open`, `in progress`, `resolved`, or `closed` and link the implementation or fix when resolved.

## Entry templates

Copy a row in [usage-log.md](usage-log.md). For a gap or defect, copy the corresponding template from [feature-requests.md](feature-requests.md) or [bugs.md](bugs.md). Keep examples generic and sanitized.
