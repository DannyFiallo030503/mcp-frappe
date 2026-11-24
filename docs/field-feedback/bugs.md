# Field bug reports

Add an entry when an existing MCP tool fails unexpectedly or produces an incorrect result. Include a minimal, sanitized reproduction and expected versus actual behavior. Check for an existing open report first. Permission denials and documented safety refusals are not bugs.

## Open bugs

### BUG-2026-001 — DocType rename leaves native files after development reload

- **Status:** open; native rename validated through same-site `--noreload` endpoint on 2026-11-24
- **Reported:** 2026-11-23
- **Task reference:** `mcp-frappe-v16-doctype-rename`
- **Tool:** `rename(kind="doctype", doctype="DocType")`
- **MCP version / commit:** 0.4.0 working tree
- **Frappe version / apps:** Frappe 16.34.0
- **Preconditions:** Standard app DocType, developer mode, Bench web server watching app Python files
- **Reproduction:** Create a standard virtual DocType in an app checkout, preview its DocType rename, then confirm it. The native request moves the app folder and triggers the web worker reload during the request.
- **Expected:** Rename either completes, or the MCP leaves the original DocType and its app files intact after reporting failure.
- **Actual:** On the default development server, Frappe can reload after moving/renaming app files but before the virtual DocType metadata transaction commits. The MCP reports failure. A same-site `bench serve --noreload` run passed create, rename, readback, and delete. Recovery for the default watcher remains under validation.
- **Impact:** Rename can be interrupted in the default development-server mode, leaving metadata and app files out of sync.
- **Workaround:** Use the same Bench/site through `bench serve --noreload` for standard virtual DocType renames; inspect metadata and app files after a failed request.
- **Evidence:** `e2e/evidence/2026-11-24-v16-isolated-doctype-rename.json`; private details retained in the consuming repository.
- **Implementation:** Recovery guards added to the working tree; default-watcher recovery still needs a final live reproduction.

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
