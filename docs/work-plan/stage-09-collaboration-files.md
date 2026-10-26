# Stage 09: Collaboration and files

**Depends on:** 08 · **Size:** medium · **Fixes:** structural gap 4 (collaboration) in [assessment.md](assessment.md)

## Why

"What happened to this invoice?", "leave a note for accounting", "assign this to Ana", and "attach the signed PDF" are everyday requests. They use dedicated Frappe endpoints with their own permission rules, so plain record CRUD isn't the right way to do them.

## Read first

- Frappe:
  - `frappe/desk/form/load.py` (`get_docinfo`);
  - `frappe/desk/form/utils.py` (`add_comment`);
  - `frappe/desk/form/assign_to.py` (`add`, `remove`);
  - `frappe/handler.py` (`upload_file`);
  - the `File` DocType (`attached_to_doctype`, `attached_to_name`, `is_private`).
- `docs/security.md` (data handling)

## Tasks

- [ ] **`get_document_activity(doctype, name, limit=20)`.** Read-only. Call `frappe.desk.form.load.get_docinfo` and return bounded lists of comments, versions (field changes), assignments, attachment metadata, shares, and communications. Each list gets its own cap and a `truncated` flag. Strip HTML from comment content, and cap each item's text.
- [ ] **`add_comment(doctype, name, content, confirm=False)`.** Call `frappe.desk.form.utils.add_comment`. `comment_email` and `comment_by` come from the connected user (`get_site_info` from stage 06); never let the caller set them. Bound `content` to 5,000 characters.
- [ ] **`assign_document(doctype, name, users, description=None, date=None, confirm=False)`.** Call `assign_to.add`. Check that each user exists by searching `User`. Add `unassign_document` using `assign_to.remove`.
- [ ] **`list_attachments(doctype, name)`.** Return `File` records with name, file name, size, privacy flag, and URL.
- [ ] **`read_attachment(file_name, max_bytes=262144)`.** Return text content for text-like MIME types, and metadata only for binary files. Fetch through the authenticated session, so private files keep their permission checks.
- [ ] **`attach_file(doctype, name, filename, content_base64 | path, is_private=True, confirm=False)`.** Upload with `POST /api/method/upload_file` (multipart: `file`, `doctype`, `docname`, `is_private`).
  - **Security:** a local `path` would let the model upload any file on the machine to the site. Accept `path` only below a configured `FRAPPE_UPLOAD_ROOT`, resolved strictly with symlink checks like `_app_path`. Otherwise accept only `content_base64`.
  - Cap uploads at 10 MB.
  - Default to private.
  - The preview shows the file name, size, SHA-256, and target.
- [ ] **Toolsets and annotations.** Read tools join `read`; the others join `data`. `unassign_document` gets `destructiveHint=True`.
- [ ] **Tests:**
  - activity lists are bounded and HTML is stripped;
  - comment identity can't be overridden;
  - an upload path outside the root is rejected (including through a symlink);
  - the size cap holds;
  - the multipart request has the right fields;
  - binary attachments return metadata only.

## Acceptance criteria

- The new tools are registered, annotated, and covered by the contract test.
- `docs/security.md` documents `FRAPPE_UPLOAD_ROOT`, private-by-default uploads, and that attachment content passes through the MCP host's logs. `docs/tool-reference.md` and `docs/ai-agent-guide.md` are updated, including a "what happened to this document" recipe.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Print, PDF, and email sending (backlog). Shares management.

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Notes for the next stage:
