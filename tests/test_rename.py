"""`rename`: documents, Custom Fields, app DocType fields (with a patch), and DocTypes."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from frappe_mcp import standalone

COUNT_PATH = "/api/method/frappe.client.get_count"
DOCFIELD = standalone._resource("DocField")
CUSTOM_FIELD = standalone._resource("Custom Field")
SETTERS = standalone._resource("Property Setter")
MODIFIED = "2026-11-03 08:00:00.000001"


def _count(table: dict):
    """Answer get_count from `{(doctype, filters_json): count}`; anything else counts 0."""

    def answer(call):
        return table.get((call.params["doctype"], call.params["filters"]), 0)

    return answer


def _links(fake_site, standard: list[dict], custom: list[dict] | None = None) -> None:
    fake_site.add("GET", DOCFIELD, standard)
    fake_site.add(
        "GET",
        CUSTOM_FIELD,
        custom or [],
        params=lambda params: '"options"' in params.get("filters", ""),
    )


# kind="document"


def _document_site(fake_site, *, allow_rename: int = 1) -> None:
    fake_site.add_doctype("Lab Client", [{"fieldname": "title"}], allow_rename=allow_rename)
    fake_site.add(
        "GET",
        standalone._resource("Lab Client", "ACME"),
        {"name": "ACME", "modified": MODIFIED},
    )
    _links(
        fake_site,
        [
            {"parent": "Lab Order", "fieldname": "client", "fieldtype": "Link"},
            {"parent": "Lab Invoice", "fieldname": "items", "fieldtype": "Table"},
        ],
        [{"dt": "Lab Visit", "fieldname": "custom_client", "fieldtype": "Link"}],
    )
    fake_site.add(
        "GET",
        COUNT_PATH,
        _count(
            {
                ("Lab Order", '[["client","=","ACME"]]'): 4,
                ("Lab Visit", '[["custom_client","=","ACME"]]'): 1,
            }
        ),
    )


def test_document_preview_counts_the_records_that_link_to_it(fake_site) -> None:
    _document_site(fake_site)

    preview = standalone.rename("document", "Lab Client", "ACME", "Acme Ltd")

    assert preview["preview"] is True
    assert preview["modified"] == MODIFIED
    assert preview["links"] == [
        {"doctype": "Lab Order", "fieldname": "client", "fieldtype": "Link", "records": 4},
        {
            "doctype": "Lab Visit",
            "fieldname": "custom_client",
            "fieldtype": "Link",
            "custom_field": True,
            "records": 1,
        },
    ]
    assert preview["linked_records"] == 5
    assert not fake_site.calls_to("POST")


def test_document_confirm_calls_frappes_rename(fake_site) -> None:
    _document_site(fake_site)
    fake_site.add("POST", standalone._RENAME_DOCUMENT, "Acme Ltd")
    preview = standalone.rename("document", "Lab Client", "ACME", "Acme Ltd")

    result = standalone.rename(
        "document",
        "Lab Client",
        "ACME",
        "Acme Ltd",
        expected_preview=preview["preview_id"],
        confirm=True,
    )

    (post,) = fake_site.calls_to("POST", standalone._RENAME_DOCUMENT)
    assert post.json == {
        "doctype": "Lab Client",
        "docname": "ACME",
        "name": "Acme Ltd",
        "merge": False,
    }
    assert result == {
        "renamed": True,
        "kind": "document",
        "doctype": "Lab Client",
        "old": "ACME",
        "new": "Acme Ltd",
        "merge": False,
        "name": "Acme Ltd",
    }


def test_document_confirm_refuses_a_stale_preview(fake_site) -> None:
    _document_site(fake_site)
    preview = standalone.rename("document", "Lab Client", "ACME", "Acme Ltd")
    fake_site.add(
        "GET", standalone._resource("Lab Client", "ACME"), {"name": "ACME", "modified": "later"}
    )

    with pytest.raises(ValueError, match="stale"):
        standalone.rename(
            "document",
            "Lab Client",
            "ACME",
            "Acme Ltd",
            expected_preview=preview["preview_id"],
            confirm=True,
        )
    assert not fake_site.calls_to("POST")


def test_document_rename_refusals(fake_site) -> None:
    _document_site(fake_site, allow_rename=0)
    with pytest.raises(ValueError, match="does not allow renaming"):
        standalone.rename("document", "Lab Client", "ACME", "Acme Ltd")
    with pytest.raises(ValueError, match="kind='doctype'"):
        standalone.rename("document", "DocType", "Lab Client", "Lab Customer")
    with pytest.raises(ValueError, match="does not rename Server Script"):
        standalone.rename("document", "Server Script", "a", "b")
    with pytest.raises(ValueError, match="same"):
        standalone.rename("document", "Lab Client", "ACME", "ACME")


def test_document_merge_needs_the_target_and_a_rename_needs_a_free_name(fake_site) -> None:
    _document_site(fake_site)

    with pytest.raises(ValueError, match="merge needs an existing"):
        standalone.rename("document", "Lab Client", "ACME", "Acme Ltd", merge=True)
    fake_site.add("GET", COUNT_PATH, 1)
    with pytest.raises(ValueError, match="already exists"):
        standalone.rename("document", "Lab Client", "ACME", "Acme Ltd")


# kind="custom_field"


def _custom_field_site(fake_site, *, system_generated: int = 0) -> None:
    fake_site.add_doctype(
        "Customer",
        [
            {"fieldname": "customer_name", "fieldtype": "Data"},
            {
                "fieldname": "custom_batch",
                "fieldtype": "Data",
                "is_custom_field": 1,
                "depends_on": "eval:doc.custom_batch",
            },
            {"fieldname": "custom_note", "fieldtype": "Data", "depends_on": "doc.custom_batch"},
        ],
        title_field="custom_batch",
    )
    fake_site.add(
        "GET",
        CUSTOM_FIELD,
        [
            {
                "name": "Customer-custom_batch",
                "fieldname": "custom_batch",
                "fieldtype": "Data",
                "is_system_generated": system_generated,
                "modified": MODIFIED,
            }
        ],
        params=lambda params: '"fieldname":"custom_batch"' in params.get("filters", ""),
    )
    fake_site.add(
        "GET",
        SETTERS,
        [
            {"name": "ps-1", "field_name": "custom_batch", "property": "bold", "value": "1"},
            {
                "name": "ps-2",
                "field_name": None,
                "property": "field_order",
                "value": json.dumps(["customer_name", "custom_batch", "custom_note"]),
            },
        ],
    )
    fake_site.add("GET", COUNT_PATH, 7)


def test_custom_field_preview_lists_what_frappe_leaves_behind(fake_site) -> None:
    _custom_field_site(fake_site)

    preview = standalone.rename("custom_field", "Customer", "custom_batch", "custom_lot")

    assert preview["custom_field"] == "Customer-custom_batch"
    assert preview["records_with_value"] == 7
    (warning,) = preview["warnings"]
    assert "Property Setter bold on custom_batch" in warning
    assert "field_order Property Setter of Customer" in warning
    assert "DocType setting title_field" in warning
    assert "custom_note.depends_on" in warning


def test_custom_field_confirm_calls_rename_fieldname(fake_site) -> None:
    _custom_field_site(fake_site)
    fake_site.add("POST", standalone._RENAME_CUSTOM_FIELD, None)
    preview = standalone.rename("custom_field", "Customer", "custom_batch", "custom_lot")

    result = standalone.rename(
        "custom_field",
        "Customer",
        "custom_batch",
        "custom_lot",
        expected_preview=preview["preview_id"],
        confirm=True,
    )

    (post,) = fake_site.calls_to("POST")
    assert post.json == {"custom_field": "Customer-custom_batch", "fieldname": "custom_lot"}
    assert result["renamed"] is True


def test_custom_field_refusals(fake_site) -> None:
    _custom_field_site(fake_site, system_generated=1)
    fake_site.add(
        "GET",
        CUSTOM_FIELD,
        [],
        params=lambda params: '"fieldname":"customer_name"' in params.get("filters", ""),
    )

    with pytest.raises(ValueError, match="system-generated"):
        standalone.rename("custom_field", "Customer", "custom_batch", "custom_lot")
    with pytest.raises(ValueError, match="standard field"):
        standalone.rename("custom_field", "Customer", "customer_name", "client_name")
    with pytest.raises(ValueError, match="merge applies"):
        standalone.rename("custom_field", "Customer", "custom_batch", "x", merge=True)


# kind="field": a field of the user's app DocType, plus a template patch

APP = "lab_app"
DOCTYPE = "Lab Sample"
RECORD_PATH = standalone._resource("DocType", DOCTYPE)
PATCH_LOG = standalone._resource("Patch Log")
PATCHES = """\
[pre_model_sync]
lab_app.patches.first

[post_model_sync]
lab_app.patches.second

"""
EXPECTED_PATCH = """\
# Generated by mcp-frappe: renames the Lab Sample field batch_no to lot_no.
import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
\tfrappe.reload_doc('lab', "doctype", 'lab_sample')
\trename_field('Lab Sample', 'batch_no', 'lot_no')
"""


def _field_site(fake_site, tmp_path: Path, monkeypatch, *, custom: int = 0, app: str = APP):
    package = tmp_path / "apps" / APP / APP
    folder = package / "lab" / "doctype" / "lab_sample"
    folder.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "patches.txt").write_text(PATCHES)
    (folder / "lab_sample.json").write_text('{"fields": [{"fieldname": "batch_no"}]}')
    (folder / "lab_sample.py").write_text("def validate(doc):\n    return doc.batch_no\n")
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path / "apps"))
    fields = [
        {"name": "df-1", "fieldname": "title", "fieldtype": "Data", "idx": 1},
        {"name": "df-2", "fieldname": "batch_no", "fieldtype": "Data", "idx": 2},
        {
            "name": "df-3",
            "fieldname": "lot_hint",
            "fieldtype": "Data",
            "idx": 3,
            "depends_on": "eval:doc.batch_no",
        },
    ]
    record = {
        "name": DOCTYPE,
        "module": "Lab",
        "custom": custom,
        "modified": MODIFIED,
        "title_field": "batch_no",
        "search_fields": "title, batch_no",
        "fields": fields,
    }
    fake_site.add("GET", RECORD_PATH, record)
    fake_site.add("GET", standalone._resource("Module Def"), [{"name": "Lab", "app_name": app}])
    fake_site.add_doctype(DOCTYPE, [dict(row) for row in fields], module="Lab")
    fake_site.add("GET", COUNT_PATH, 12)
    fake_site.add("GET", PATCH_LOG, [])
    return package


def test_field_preview_shows_the_exact_patch_and_patches_txt(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _field_site(fake_site, tmp_path, monkeypatch)

    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")

    assert preview["patch"] == "lab_app.patches.rename_lab_sample_batch_no_to_lot_no"
    assert preview["records_with_value"] == 12
    assert preview["doctype_settings"] == {"title_field": "lot_no", "search_fields": "title,lot_no"}
    files = {Path(item["path"]).relative_to(package).as_posix(): item for item in preview["files"]}
    assert set(files) == {
        "patches/rename_lab_sample_batch_no_to_lot_no.py",
        "patches/__init__.py",
        "patches.txt",
    }
    assert (
        "+\trename_field('Lab Sample', 'batch_no', 'lot_no')"
        in files["patches/rename_lab_sample_batch_no_to_lot_no.py"]["diff"]
    )
    assert (
        "lab_app.patches.second\n+lab_app.patches.rename_lab_sample" in files["patches.txt"]["diff"]
    )
    assert preview["code_mentions"] == [
        {"file": "lab/doctype/lab_sample/lab_sample.py", "lines": [2]}
    ]
    assert any("lot_hint.depends_on" in warning for warning in preview["warnings"])
    assert not (package / "patches").exists()
    assert not fake_site.calls_to("PUT")


def test_field_confirm_writes_the_patch_then_saves_the_doctype(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _field_site(fake_site, tmp_path, monkeypatch)
    fake_site.add("PUT", RECORD_PATH, {"name": DOCTYPE, "modified": "later"})
    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")

    result = standalone.rename(
        "field", DOCTYPE, "batch_no", "lot_no", expected_preview=preview["preview_id"], confirm=True
    )

    patch = package / "patches" / "rename_lab_sample_batch_no_to_lot_no.py"
    assert patch.read_text() == EXPECTED_PATCH
    assert (package / "patches" / "__init__.py").read_text() == ""
    assert (package / "patches.txt").read_text() == (
        "[pre_model_sync]\nlab_app.patches.first\n\n[post_model_sync]\nlab_app.patches.second\n"
        "lab_app.patches.rename_lab_sample_batch_no_to_lot_no\n\n"
    )
    (put,) = fake_site.calls_to("PUT", RECORD_PATH)
    data = put.json["data"]
    assert [row["fieldname"] for row in data["fields"]] == ["title", "lot_no", "lot_hint"]
    assert data["fields"][1]["name"] == "df-2"
    assert data["modified"] == MODIFIED
    assert data["title_field"] == "lot_no"
    assert result["renamed"] is True
    assert result["next_step"] == "`bench migrate` runs pending patches"
    assert result["rollback_id"].startswith("export-")


def test_field_rename_files_roll_back_with_rollback_export(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _field_site(fake_site, tmp_path, monkeypatch)
    fake_site.add("PUT", RECORD_PATH, {"name": DOCTYPE})
    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")
    result = standalone.rename(
        "field", DOCTYPE, "batch_no", "lot_no", expected_preview=preview["preview_id"], confirm=True
    )

    undo = standalone.rollback_export(APP, "Lab", result["rollback_id"])
    standalone.rollback_export(
        APP, "Lab", result["rollback_id"], expected_preview=undo["preview_id"], confirm=True
    )

    assert (package / "patches.txt").read_text() == PATCHES
    assert not (package / "patches").exists()


def test_a_failed_doctype_save_removes_the_patch_files(fake_site, tmp_path, monkeypatch) -> None:
    package = _field_site(fake_site, tmp_path, monkeypatch)
    fake_site.add(
        "PUT",
        RECORD_PATH,
        standalone.FrappeError(
            "Frappe HTTP 417: not in developer mode", "CannotCreateStandardDoctypeError"
        ),
    )
    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")

    with pytest.raises(standalone.FrappeError):
        standalone.rename(
            "field",
            DOCTYPE,
            "batch_no",
            "lot_no",
            expected_preview=preview["preview_id"],
            confirm=True,
        )

    assert (package / "patches.txt").read_text() == PATCHES
    assert not (package / "patches").exists()
    assert not (tmp_path / "apps" / APP / ".mcp-frappe-backups").exists()


def test_a_reload_during_the_save_is_reconciled(fake_site, tmp_path, monkeypatch) -> None:
    _field_site(fake_site, tmp_path, monkeypatch)
    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")
    record = standalone._request("GET", RECORD_PATH)
    record["fields"][1]["fieldname"] = "lot_no"
    record["title_field"] = "lot_no"
    record["search_fields"] = "title,lot_no"
    record["modified"] = "after reload"

    def put(call):
        fake_site.add("GET", RECORD_PATH, record)
        raise httpx.ReadError("worker reloaded")

    fake_site.add("PUT", RECORD_PATH, put)
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)

    result = standalone.rename(
        "field", DOCTYPE, "batch_no", "lot_no", expected_preview=preview["preview_id"], confirm=True
    )

    assert result["reconciled_after_server_reload"] is True


def test_transport_disconnect_accepts_remote_normalization_and_keeps_patch(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _field_site(fake_site, tmp_path, monkeypatch)
    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")
    record = standalone._request("GET", RECORD_PATH)
    record["fields"][1]["fieldname"] = "lot_no"
    record["modified"] = "after reload"

    def put(call):
        fake_site.add("GET", RECORD_PATH, record)
        raise httpx.ReadError("worker reloaded")

    fake_site.add("PUT", RECORD_PATH, put)
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)
    result = standalone.rename(
        "field", DOCTYPE, "batch_no", "lot_no", expected_preview=preview["preview_id"], confirm=True
    )

    assert result["renamed"] is True
    assert any("differs from the preview" in warning for warning in result["warnings"])
    assert (package / "patches.txt").exists()


def test_transport_disconnect_with_unconfirmed_readback_preserves_patch(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _field_site(fake_site, tmp_path, monkeypatch)
    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")

    def put(call):
        fake_site.add("GET", RECORD_PATH, httpx.ReadError("readback unavailable"))
        raise httpx.ReadError("worker reloaded")

    fake_site.add("PUT", RECORD_PATH, put)
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)
    rollback_id = f"export-{preview['preview_id'][:32]}.json"
    with pytest.raises(
        RuntimeError,
        match=rf"rollback_id={rollback_id}.*rollback_export.*customize.*FRAPPE_MCP_TOOLSETS",
    ):
        standalone.rename(
            "field",
            DOCTYPE,
            "batch_no",
            "lot_no",
            expected_preview=preview["preview_id"],
            confirm=True,
        )

    assert (package / "patches.txt").exists()
    assert (package / "patches" / "rename_lab_sample_batch_no_to_lot_no.py").exists()
    assert len(fake_site.calls_to("PUT", RECORD_PATH)) == 1


def test_transport_disconnect_unchanged_schema_returns_rollback_path(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _field_site(fake_site, tmp_path, monkeypatch)
    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")
    existing = standalone._request("GET", RECORD_PATH)

    def put(call):
        fake_site.add("GET", RECORD_PATH, existing)
        raise httpx.ReadError("worker reloaded")

    fake_site.add("PUT", RECORD_PATH, put)
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)
    rollback_id = f"export-{preview['preview_id'][:32]}.json"

    with pytest.raises(
        RuntimeError,
        match=rf"rollback_id={rollback_id}.*rollback_export.*customize.*FRAPPE_MCP_TOOLSETS",
    ):
        standalone.rename(
            "field",
            DOCTYPE,
            "batch_no",
            "lot_no",
            expected_preview=preview["preview_id"],
            confirm=True,
        )

    assert (package / "patches.txt").exists()
    assert len(fake_site.calls_to("PUT", RECORD_PATH)) == 1


def test_field_rename_refusals(fake_site, tmp_path, monkeypatch) -> None:
    _field_site(fake_site, tmp_path, monkeypatch, custom=1)
    with pytest.raises(ValueError, match="site-custom"):
        standalone.rename("field", DOCTYPE, "batch_no", "lot_no")


def test_field_rename_refuses_frappe_doctypes_and_taken_names(
    fake_site, tmp_path, monkeypatch
) -> None:
    _field_site(fake_site, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="already has a field title"):
        standalone.rename("field", DOCTYPE, "batch_no", "title")
    with pytest.raises(ValueError, match="no field named"):
        standalone.rename("field", DOCTYPE, "missing", "lot_no")
    fake_site.add(
        "GET", standalone._resource("Module Def"), [{"name": "Lab", "app_name": "erpnext"}]
    )
    with pytest.raises(ValueError, match="standard DocType of erpnext"):
        standalone.rename("field", DOCTYPE, "batch_no", "lot_no")


def test_field_rename_refuses_a_patch_already_listed(fake_site, tmp_path, monkeypatch) -> None:
    package = _field_site(fake_site, tmp_path, monkeypatch)
    (package / "patches.txt").write_text(
        PATCHES + "lab_app.patches.rename_lab_sample_batch_no_to_lot_no\n"
    )
    with pytest.raises(ValueError, match="already lists"):
        standalone.rename("field", DOCTYPE, "batch_no", "lot_no")


def test_field_rename_skips_patch_names_the_site_already_ran(
    fake_site, tmp_path, monkeypatch
) -> None:
    # A rename undone and done again: Patch Log keeps the first run, so `bench migrate` would
    # skip a patch of the same name and the values would never reach the new column.
    package = _field_site(fake_site, tmp_path, monkeypatch)
    base = "lab_app.patches.rename_lab_sample_batch_no_to_lot_no"
    fake_site.add(
        "GET",
        PATCH_LOG,
        [
            {"patch": base},
            {"patch": f"{base}_2"},
            {"patch": "lab_app.patches.rename_lab_sampleXbatch"},
        ],
    )
    fake_site.add("PUT", RECORD_PATH, {"name": DOCTYPE})

    preview = standalone.rename("field", DOCTYPE, "batch_no", "lot_no")
    standalone.rename(
        "field", DOCTYPE, "batch_no", "lot_no", expected_preview=preview["preview_id"], confirm=True
    )

    assert preview["patch"] == f"{base}_3"
    assert (package / "patches" / "rename_lab_sample_batch_no_to_lot_no_3.py").read_text() == (
        EXPECTED_PATCH
    )
    assert (package / "patches.txt").read_text().endswith(f"{base}_3\n\n")
    (log_read,) = {call.params["filters"] for call in fake_site.calls_to("GET", PATCH_LOG)}
    assert json.loads(log_read) == [["patch", "like", f"{base}%"]]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "[pre_model_sync]\n\n[post_model_sync]\nnew.patch\n"),
        ("app.patches.a\n", "app.patches.a\nnew.patch\n"),
        (
            "[pre_model_sync]\napp.a\n[post_model_sync]\napp.b\n\n# end\n",
            "[pre_model_sync]\napp.a\n[post_model_sync]\napp.b\nnew.patch\n\n# end\n",
        ),
        (
            "[post_model_sync]\napp.b\n[pre_model_sync]\napp.a\n",
            "[post_model_sync]\napp.b\nnew.patch\n[pre_model_sync]\napp.a\n",
        ),
        ("[pre_model_sync]\napp.a\n", "[pre_model_sync]\napp.a\n\n[post_model_sync]\nnew.patch\n"),
    ],
)
def test_append_patch_adds_to_the_post_model_sync_section(text, expected) -> None:
    assert standalone._append_patch(text, "new.patch") == expected


# kind="doctype"


def _doctype_site(fake_site, tmp_path, monkeypatch, *, custom: int = 0) -> Path:
    package = tmp_path / "apps" / APP / APP
    (package / "lab" / "doctype" / "lab_sample").mkdir(parents=True)
    (package / "lab" / "doctype" / "lab_order").mkdir(parents=True)
    (package / "lab" / "doctype" / "lab_order" / "lab_order.json").write_text(
        '{"options": "Lab Sample"}'
    )
    (package / "hooks.py").write_text('doc_events = {"Lab Sample": {}}\n')
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path / "apps"))
    fake_site.add(
        "GET",
        RECORD_PATH,
        {"name": DOCTYPE, "module": "Lab", "custom": custom, "modified": MODIFIED, "fields": []},
    )
    fake_site.add("GET", standalone._resource("Module Def"), [{"name": "Lab", "app_name": APP}])
    fake_site.add("GET", standalone._resource("DocType"), [])
    _links(fake_site, [{"parent": "Lab Order", "fieldname": "sample", "fieldtype": "Link"}])
    fake_site.add("GET", COUNT_PATH, 2)
    return package


def test_doctype_preview_lists_links_files_and_app_mentions(
    fake_site, tmp_path, monkeypatch
) -> None:
    _doctype_site(fake_site, tmp_path, monkeypatch)

    preview = standalone.rename("doctype", "DocType", DOCTYPE, "Lab Specimen")

    assert preview["records"] == 2
    assert preview["links"] == [
        {"doctype": "Lab Order", "fieldname": "sample", "fieldtype": "Link"}
    ]
    assert preview["app_files"]["to"].endswith("lab/doctype/lab_specimen")
    assert preview["requires_administrator"] is True
    assert [item["file"] for item in preview["code_mentions"]] == [
        "hooks.py",
        "lab/doctype/lab_order/lab_order.json",
    ]


def test_doctype_rename_refusals(fake_site, tmp_path, monkeypatch) -> None:
    _doctype_site(fake_site, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="doctype='DocType'"):
        standalone.rename("doctype", "Lab Order", DOCTYPE, "Lab Specimen")
    with pytest.raises(ValueError, match="merge applies"):
        standalone.rename("doctype", "DocType", DOCTYPE, "Lab Specimen", merge=True)
    fake_site.add("GET", standalone._resource("DocType"), [{"name": "Lab Specimen"}])
    with pytest.raises(ValueError, match="already exists"):
        standalone.rename("doctype", "DocType", DOCTYPE, "Lab Specimen")


def test_doctype_confirm_reconciles_a_reload(fake_site, tmp_path, monkeypatch) -> None:
    _doctype_site(fake_site, tmp_path, monkeypatch, custom=1)
    preview = standalone.rename("doctype", "DocType", DOCTYPE, "Lab Specimen")
    assert "app_files" not in preview

    def post(call):
        fake_site.add(
            "GET",
            standalone._resource("DocType"),
            lambda call: (
                [{"name": "Lab Specimen"}] if "Lab Specimen" in call.params["filters"] else []
            ),
        )
        raise httpx.ReadError("worker reloaded")

    fake_site.add("POST", standalone._RENAME_DOCUMENT, post)
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)

    result = standalone.rename(
        "doctype",
        "DocType",
        DOCTYPE,
        "Lab Specimen",
        expected_preview=preview["preview_id"],
        confirm=True,
    )

    (call,) = fake_site.calls_to("POST")
    assert call.json == {
        "doctype": "DocType",
        "docname": DOCTYPE,
        "name": "Lab Specimen",
        "merge": False,
    }
    assert result["reconciled_after_server_reload"] is True


def test_failed_doctype_rename_quarantines_moved_files_and_restores_original(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "lab" / "doctype" / "lab_sample"
    destination = tmp_path / "lab" / "doctype" / "lab_specimen"
    source.mkdir(parents=True)
    original = {
        "lab_sample.json": '{"doctype": "DocType", "name": "Lab Sample"}',
        "lab_sample.py": "class LabSample(Document):\n    pass\n",
        "lab_sample.js": "frappe.ui.form.on('Lab Sample', {});\n",
        "test_lab_sample.py": "class TestLabSample: pass\n",
    }
    for name, content in original.items():
        (source / name).write_text(content)
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "lab_sample.cpython-314.pyc").write_bytes(b"compiled")
    backup = tmp_path / "backup"
    import shutil

    shutil.copytree(source, backup)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    source.rename(destination)
    for path in list(destination.iterdir()):
        renamed = path.with_name(path.name.replace("lab_sample", "lab_specimen"))
        path.rename(renamed)
        if renamed.suffix == ".json":
            renamed.write_text('{"doctype": "DocType", "name": "Lab Specimen"}')
        elif renamed.suffix == ".py":
            renamed.write_text(
                renamed.read_text().replace("LabSample", "LabSpecimen").replace(
                    "TestLabSample", "TestLabSpecimen"
                )
            )
        elif renamed.suffix == ".js":
            renamed.write_text(renamed.read_text().replace("Lab Sample", "Lab Specimen"))

    recovery = standalone._restore_doctype_files_after_failed_rename(
        source, destination, backup, "Lab Sample", "Lab Specimen"
    )

    assert recovery is not None
    assert not destination.exists()
    quarantine = recovery["moved_files"]
    assert (quarantine / "lab_specimen.json").is_file()
    assert (recovery["snapshot"] / "original" / "lab_sample.json").is_file()
    assert {path.name: path.read_text() for path in source.iterdir() if path.is_file()} == original
    assert (source / "__pycache__" / "lab_sample.cpython-314.pyc").read_bytes() == b"compiled"


def test_failed_doctype_rename_quarantines_unexpected_destination(tmp_path, monkeypatch) -> None:
    source = tmp_path / "lab" / "doctype" / "lab_sample"
    destination = tmp_path / "lab" / "doctype" / "lab_specimen"
    backup = tmp_path / "backup"
    backup.mkdir(parents=True)
    (backup / "lab_sample.json").write_text('{"name": "Lab Sample"}')
    destination.mkdir(parents=True)
    (destination / "operator-file.txt").write_text("preserve me")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    recovery = standalone._restore_doctype_files_after_failed_rename(
        source, destination, backup, "Lab Sample", "Lab Specimen"
    )

    assert recovery is not None
    quarantined = recovery["moved_files"]
    assert (quarantined / "operator-file.txt").read_text() == "preserve me"
    assert (source / "lab_sample.json").read_text() == '{"name": "Lab Sample"}'


def test_doctype_rename_wait_failure_retains_snapshot(tmp_path, monkeypatch) -> None:
    source = tmp_path / "lab" / "doctype" / "lab_sample"
    source.mkdir(parents=True)
    (source / "lab_sample.json").write_text('{"name": "Lab Sample"}')
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        standalone,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadError("worker stopped")),
    )
    monkeypatch.setattr(
        standalone,
        "_wait_for_server_ready",
        lambda: (_ for _ in ()).throw(RuntimeError("still offline")),
    )

    with pytest.raises(RuntimeError, match="Snapshot retained at") as error:
        standalone._apply_doctype_rename(
            "Lab Sample",
            "Lab Specimen",
            {},
            {
                "app_files": {
                    "from": str(source),
                    "to": str(source.parent / "lab_specimen"),
                    "local_checkout": True,
                }
            },
        )

    snapshot = Path(str(error.value).split("Snapshot retained at ", 1)[1].rstrip("."))
    assert (snapshot / "original" / "lab_sample.json").read_text() == '{"name": "Lab Sample"}'


def test_doctype_rename_ambiguous_remote_state_retains_snapshot(tmp_path, monkeypatch) -> None:
    source = tmp_path / "lab" / "doctype" / "lab_sample"
    source.mkdir(parents=True)
    (source / "lab_sample.json").write_text('{"name": "Lab Sample"}')
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        standalone,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadError("worker stopped")),
    )
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)
    monkeypatch.setattr(standalone, "_doctype_exists", lambda _name: True)

    with pytest.raises(RuntimeError, match="Snapshot retained at") as error:
        standalone._apply_doctype_rename(
            "Lab Sample",
            "Lab Specimen",
            {},
            {
                "app_files": {
                    "from": str(source),
                    "to": str(source.parent / "lab_specimen"),
                    "local_checkout": True,
                }
            },
        )

    snapshot = Path(str(error.value).split("Snapshot retained at ", 1)[1].rstrip("."))
    assert (snapshot / "original" / "lab_sample.json").read_text() == '{"name": "Lab Sample"}'


def test_doctype_rename_readback_failure_retains_snapshot(tmp_path, monkeypatch) -> None:
    source = tmp_path / "lab" / "doctype" / "lab_sample"
    source.mkdir(parents=True)
    (source / "lab_sample.json").write_text('{"name": "Lab Sample"}')
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        standalone,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadError("worker stopped")),
    )
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)
    monkeypatch.setattr(
        standalone,
        "_doctype_exists",
        lambda _name: (_ for _ in ()).throw(httpx.ReadError("worker restarted again")),
    )

    with pytest.raises(RuntimeError, match="Snapshot retained at") as error:
        standalone._apply_doctype_rename(
            "Lab Sample",
            "Lab Specimen",
            {},
            {
                "app_files": {
                    "from": str(source),
                    "to": str(source.parent / "lab_specimen"),
                    "local_checkout": True,
                }
            },
        )

    snapshot = Path(str(error.value).split("Snapshot retained at ", 1)[1].rstrip("."))
    assert (snapshot / "original" / "lab_sample.json").read_text() == '{"name": "Lab Sample"}'


def test_failed_doctype_delete_restores_removed_app_folder(tmp_path) -> None:
    folder = tmp_path / "lab" / "doctype" / "lab_sample"
    folder.mkdir(parents=True)
    (folder / "lab_sample.json").write_text('{"name": "Lab Sample"}')
    backup = tmp_path / "backup"
    import shutil

    shutil.copytree(folder, backup)
    shutil.rmtree(folder)

    restored = standalone._restore_doctype_files_after_failed_delete(folder, backup)

    assert restored is True
    assert (folder / "lab_sample.json").read_text() == '{"name": "Lab Sample"}'


def test_doctype_recovery_snapshot_is_retained_outside_app_tree(tmp_path, monkeypatch) -> None:
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "controller.py").write_text("class Example: pass\n")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    retained = standalone._retain_doctype_snapshot(backup)

    assert retained is not None
    assert retained != backup
    assert (retained / "original" / "controller.py").read_text() == "class Example: pass\n"
    assert retained.parent.stat().st_mode & 0o777 == 0o700


def test_failed_doctype_delete_preserves_partially_changed_folder(tmp_path) -> None:
    folder = tmp_path / "lab" / "doctype" / "lab_sample"
    folder.mkdir(parents=True)
    (folder / "lab_sample.py").write_text("operator edit\n")
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "lab_sample.py").write_text("original\n")

    with pytest.raises(RuntimeError, match="partially changed"):
        standalone._restore_doctype_files_after_failed_delete(folder, backup)

    assert (folder / "lab_sample.py").read_text() == "operator edit\n"


def test_app_mentions_skips_symlinks_and_hidden_folders(tmp_path) -> None:
    base = tmp_path / ".bench" / "lab_app"
    (base / "lab").mkdir(parents=True)
    (base / "lab" / "a.py").write_text("x = 'Lab Sample'\n")
    (base / ".git").mkdir()
    (base / ".git" / "b.py").write_text("'Lab Sample'\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "c.py").write_text("'Lab Sample'\n")
    (base / "linked").symlink_to(outside, target_is_directory=True)
    (base / "d.py").symlink_to(outside / "c.py")

    found = standalone._app_mentions(base, standalone.re.compile("Lab Sample"))

    assert found == [{"file": "lab/a.py", "lines": [1]}]
