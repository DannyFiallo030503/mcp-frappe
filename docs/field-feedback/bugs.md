# Field bug reports

Add an entry when an existing MCP tool fails unexpectedly or produces an incorrect result. Include a minimal, sanitized reproduction and expected versus actual behavior. Check for an existing open report first. Permission denials and documented safety refusals are not bugs.

## Open bugs

### BUG-2026-001 — Field rename reports failure after partially changing the schema

- **Status:** open
- **Reported:** 2026-11-21
- **Task reference:** `mcp-frappe-v040-v15-retest`
- **Tool:** `rename` (`kind=field`)
- **MCP version / commit:** 0.4.0 / `4c0b8a7`
- **Frappe version / apps:** Frappe 15.96.0; ERPNext 15.96.1
- **Preconditions:** disposable promoted standard DocType with one saved document containing a Data field
- **Reproduction:** preview rename `qa_text` to `qa_text_renamed` on `QA MCP V040 20261121 Endpoint Lab`; confirm using returned preview token
- **Expected:** successful schema rename plus data preservation, or a failure that leaves the schema/data unchanged
- **Actual:** confirm twice returned a disconnect/error indicating the saved DocType did not hold the rename and patch files were removed. Subsequent schema read showed `qa_text_renamed`; document read showed the value under the old field absent/null. No rename patch was listed as pending. Old database-column data retention was not inspected and is unverified.
- **Impact:** endpoint returned an error despite a changed schema; data may be inaccessible through the new schema. Do not retry or migrate in the shared site.
- **Workaround:** none verified; keep this operation out of manual parity and preserve the fixture until cleanup is available.
- **Evidence:** [Frappe 15 retest](reports/mcp-frappe-v040-v15-retest-2026-11-21.md)
- **Implementation:** pending


## Template

```markdown
### BUG-YYYY-NNN — Short defect name

- **Status:** open
- **Reported:** YYYY-MM-DD
- **Task reference:** public issue/PR or sanitized slug
- **Tool:** exact MCP tool name
- **MCP version / commit:** verified value or unknown
- **Frappe version / apps:** verified, non-sensitive details or unknown
- **Preconditions:** minimum safe setup; no credentials or private URLs
- **Reproduction:** minimal steps and sanitized arguments
- **Expected:** intended result
- **Actual:** observed error or incorrect behavior; summarize, do not paste sensitive responses
- **Impact:** task-level effect and whether data may have changed
- **Workaround:** safe, authorized workaround or none
- **Evidence:** safe artifact/link or none
- **Implementation:** link and date when fixed
```
