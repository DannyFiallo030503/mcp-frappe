# Field bug reports

Add an entry when an existing MCP tool fails unexpectedly or produces an incorrect result. Include a minimal, sanitized reproduction and expected versus actual behavior. Check for an existing open report first. Permission denials and documented safety refusals are not bugs.

## Open bugs

### BUG-2026-001 — DocType rename leaves native files after development reload

- **Status:** open; default-reloader recovery validated on 2026-11-24; successful rename still requires a stable worker
- **Reported:** 2026-11-23
- **Task reference:** `mcp-frappe-v16-doctype-rename`
- **Tool:** `rename(kind="doctype", doctype="DocType")`
- **MCP version / commit:** 0.4.0 working tree
- **Frappe version / apps:** Frappe 16.34.0
- **Preconditions:** Standard app DocType, developer mode, Bench web server watching app Python files
- **Reproduction:** Create a standard virtual DocType in an app checkout, preview its DocType rename, then confirm it. The native request moves the app folder and triggers the web worker reload during the request.
- **Expected:** Rename either completes, or the MCP leaves the original DocType and its app files intact after reporting failure.
- **Actual:** On the default development server, Frappe reloaded after moving app files and before the virtual DocType metadata transaction committed. The MCP reported failure, preserved the original metadata and folder, quarantined the moved folder, and retained a recovery snapshot. Cleanup through MCP on a stable worker on the same Bench/site passed. A separate same-site `bench serve --noreload` run passed create, rename, readback, and delete.
- **Impact:** Rename does not complete on the default development-server worker, but the validated recovery leaves the old metadata and files intact. The operation requires a stable worker to complete.
- **Workaround:** Use the same Bench/site through `bench serve --noreload` for standard virtual DocType renames; inspect metadata and app files after a failed request.
- **Evidence:** `e2e/evidence/2026-11-24-v16-isolated-doctype-rename.json`; `e2e/evidence/2026-11-24-v16-doctype-rename-reloader-recovery.json`; private details retained in the consuming repository.
- **Implementation:** Recovery guards merged in PR #40; default-watcher safe recovery validated with live evidence on 2026-11-24.

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
