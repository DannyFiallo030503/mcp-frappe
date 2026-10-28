"""`diff_doctype`, `list_app_drift`, and `promote_custom_doctype`: site and app files in sync."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from frappe_mcp import standalone

APP = "lab_app"
MODULE = "Lab"
DOCTYPE = "Lab Sample"
SLUG = "lab_sample"
OLD = "2026-10-20 09:00:00.000001"
NEW = "2026-10-21 09:00:00.000001"
SAVED = "2026-10-22 09:00:00.000001"
RECORD_PATH = standalone._resource("DocType", DOCTYPE)
CHILD_KEYS = {
    "name", "doctype", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
    "parent", "parenttype", "parentfield",
}


def _field(name: str, fieldname: str, fieldtype: str, idx: int, **values) -> dict:
    return {
        "name": name,
        "doctype": "DocField",
        "owner": "Administrator",
        "parent": DOCTYPE,
        "parenttype": "DocType",
        "parentfield": "fields",
        "idx": idx,
        "docstatus": 0,
        "fieldname": fieldname,
        "fieldtype": fieldtype,
        "label": fieldname.replace("_", " ").title(),
        "reqd": 0,
        "hidden": 0,
        "options": None,
        **values,
    }


def _record(*, modified: str = OLD, custom: int = 0, **values) -> dict:
    """A DocType record the way `/api/resource/DocType/<name>` returns it."""
    return {
        "name": DOCTYPE,
        "doctype": "DocType",
        "owner": "Administrator",
        "creation": OLD,
        "modified": modified,
        "modified_by": "Administrator",
        "docstatus": 0,
        "idx": 0,
        "module": MODULE,
        "custom": custom,
        "istable": 0,
        "issingle": 0,
        "is_tree": 0,
        "track_changes": 0,
        "engine": "InnoDB",
        "migration_hash": None,
        "_user_tags": None,
        "fields": [
            _field("df-1", "title", "Data", 1, reqd=1),
            _field("df-2", "status", "Select", 2, options="Open\nClosed"),
            _field("df-3", "notes", "Small Text", 3),
        ],
        "permissions": [
            {"name": "p-1", "doctype": "DocPerm", "parent": DOCTYPE, "idx": 1,
             "role": "System Manager", "read": 1, "write": 1, "delete": 0},
        ],
        **values,
    }


def _exported(record: dict) -> dict:
    """What Frappe's `export_doc` writes: blanks dropped, child system keys stripped."""

    def clean(row: dict, drop: set[str]) -> dict:
        return {k: v for k, v in row.items() if k not in drop and v not in (None, "", 0, [])}

    doc = clean(record, {"migration_hash", "_user_tags"})
    for table in ("fields", "permissions"):
        doc[table] = [clean(row, CHILD_KEYS) for row in record[table]]
    doc["field_order"] = [row["fieldname"] for row in record["fields"]]
    return doc


@pytest.fixture
def apps(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "apps"
    package = root / APP / APP
    (package / "lab" / "doctype").mkdir(parents=True)
    (package / "hooks.py").write_text('app_publisher = "Example Publisher"\n', encoding="utf-8")
    templates = root / "frappe" / "frappe" / "core" / "doctype" / "doctype" / "boilerplate"
    templates.mkdir(parents=True)
    (templates / "controller._py").write_text("class {classname}({base_class}):\n    pass\n")
    (templates / "test_controller._py").write_text("# test {doctype}\n")
    (templates / "controller.js").write_text("// {doctype}\n")
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(root))
    return root


def _doctype_dir(apps: Path, slug: str = SLUG) -> Path:
    return apps / APP / APP / "lab" / "doctype" / slug


def _write_app_json(apps: Path, doc: dict, slug: str = SLUG) -> str:
    folder = _doctype_dir(apps, slug)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug}.json"
    path.write_text(standalone._frappe_json(doc) + "\n", encoding="utf-8")
    return hashlib.md5(path.read_bytes()).hexdigest()


def _modules(fake_site, *rows: dict) -> None:
    rows = rows or ({"name": MODULE, "app_name": APP, "custom": 0},)
    for row in rows:
        fake_site.add(
            "GET",
            standalone._resource("Module Def"),
            [row],
            params={"filters": standalone._filters({"name": row["name"]})},
        )
    fake_site.add(
        "GET",
        standalone._resource("Module Def"),
        list(rows),
        params=lambda params: "app_name" in params["filters"],
    )


def _customizations(fake_site, fields: list | None = None, setters: list | None = None) -> None:
    fake_site.add("GET", standalone._resource("Custom Field"), fields or [])
    fake_site.add("GET", standalone._resource("Property Setter"), setters or [])


def _site(fake_site, record: dict) -> None:
    fake_site.add("GET", RECORD_PATH, record)
    _modules(fake_site)
    _customizations(fake_site)


# --- normalization ---------------------------------------------------------------------------


def test_normalization_ignores_system_keys_child_names_and_blanks() -> None:
    record = _record()
    exported = _exported(record)
    exported["fields"][0]["name"] = "renamed-row"  # child-row names never matter

    assert standalone._normalize_doctype(record) == standalone._normalize_doctype(exported)


def test_normalization_uses_field_order_and_catches_order_changes() -> None:
    record = _record()
    exported = _exported(record)
    # Frappe keeps the old file order in `fields`; `field_order` holds the real order.
    exported["fields"].reverse()
    assert standalone._normalize_doctype(exported) == standalone._normalize_doctype(record)

    exported["field_order"] = ["status", "title", "notes"]
    site, app = standalone._normalize_doctype(record), standalone._normalize_doctype(exported)
    changes = standalone._compare_doctypes(site, app)
    assert changes["moved"] == [{"fieldname": "status", "insert_after": None}]
    assert not changes["changed"] and not changes["added"] and not changes["removed"]


def test_compare_reports_fields_properties_and_tables_from_site_to_app() -> None:
    record = _record()
    exported = _exported(record)
    exported["fields"].append({"fieldname": "due", "fieldtype": "Date", "label": "Due"})
    exported["field_order"].append("due")
    exported["fields"] = [row for row in exported["fields"] if row["fieldname"] != "notes"]
    exported["field_order"].remove("notes")
    exported["fields"][0]["label"] = "Name"
    exported["track_changes"] = 1
    exported["permissions"][0]["delete"] = 1

    changes = standalone._compare_doctypes(
        standalone._normalize_doctype(record), standalone._normalize_doctype(exported)
    )

    assert changes["added"] == [
        {"fieldname": "due", "fieldtype": "Date", "label": "Due", "insert_after": "status"}
    ]
    assert changes["removed"] == [
        {"fieldname": "notes", "fieldtype": "Small Text", "label": "Notes"}
    ]
    assert changes["changed"] == {"title": {"label": {"from": "Title", "to": "Name"}}}
    assert changes["properties"] == {"track_changes": {"from": None, "to": 1}}
    assert changes["tables"]["permissions"]["only_on_site"] == [
        {"role": "System Manager", "read": 1, "write": 1}
    ]
    assert changes["tables"]["permissions"]["only_in_app"] == [
        {"role": "System Manager", "read": 1, "write": 1, "delete": 1}
    ]


# --- diff_doctype verdicts -------------------------------------------------------------------


def test_diff_in_sync(fake_site, apps) -> None:
    record = _record()
    record["migration_hash"] = _write_app_json(apps, _exported(record))
    _site(fake_site, record)

    result = standalone.diff_doctype(DOCTYPE)

    assert result["verdict"] == "in_sync"
    assert result["doctype_file"]["path"].endswith(f"lab/doctype/{SLUG}/{SLUG}.json")
    assert result["doctype_file"]["migrate_imports_file"] is False
    assert result["customizations"] == []


def test_diff_site_ahead_when_only_the_site_changed(fake_site, apps) -> None:
    file_doc = _exported(_record())
    record = _record(modified=NEW, track_changes=1)
    record["migration_hash"] = _write_app_json(apps, file_doc)
    _site(fake_site, record)

    result = standalone.diff_doctype(DOCTYPE)

    assert result["verdict"] == "site_ahead"
    assert result["doctype_file"]["newer"] == "site"
    assert result["doctype_file"]["changes"]["properties"] == {
        "track_changes": {"from": 1, "to": None}
    }
    assert result["doctype_file"]["migrate_imports_file"] is False


def test_diff_site_ahead_warns_when_migrate_would_still_import(fake_site, apps) -> None:
    _write_app_json(apps, _exported(_record()))
    _site(fake_site, _record(modified=NEW, track_changes=1))  # no migration_hash yet

    result = standalone.diff_doctype(DOCTYPE)

    assert result["verdict"] == "site_ahead"
    assert result["doctype_file"]["migrate_imports_file"] is True
    assert any("bench migrate" in warning for warning in result["warnings"])


def test_diff_app_ahead_after_a_pull(fake_site, apps) -> None:
    record = _record()
    record["migration_hash"] = "hash-of-the-file-before-the-pull"
    file_doc = _exported(_record(modified=NEW))
    file_doc["fields"].append({"fieldname": "due", "fieldtype": "Date"})
    file_doc["field_order"].append("due")
    _write_app_json(apps, file_doc)
    _site(fake_site, record)

    result = standalone.diff_doctype(DOCTYPE)

    assert result["verdict"] == "app_ahead"
    assert result["doctype_file"]["newer"] == "app"
    assert result["doctype_file"]["changes"]["added"][0]["fieldname"] == "due"


def test_diff_diverged_when_both_sides_changed(fake_site, apps) -> None:
    file_doc = _exported(_record())
    file_doc["fields"].append({"fieldname": "due", "fieldtype": "Date"})
    file_doc["field_order"].append("due")
    _write_app_json(apps, file_doc)
    record = _record(modified=NEW, track_changes=1)
    record["migration_hash"] = "hash-of-an-older-file"
    _site(fake_site, record)

    result = standalone.diff_doctype(DOCTYPE)

    assert result["verdict"] == "diverged"
    assert result["doctype_file"]["migrate_imports_file"] is True


def test_diff_missing_in_app(fake_site, apps) -> None:
    _site(fake_site, _record())

    result = standalone.diff_doctype(DOCTYPE)

    assert result["verdict"] == "missing_in_app"
    assert result["doctype_file"]["exists"] is False


def test_diff_site_custom_doctype_points_at_promotion(fake_site, apps) -> None:
    _site(fake_site, _record(custom=1))

    result = standalone.diff_doctype(DOCTYPE)

    assert result["verdict"] == "site_custom"
    assert "promote_custom_doctype" in result["summary"]


def test_diff_refuses_a_doctype_json_symlinked_outside_the_app(fake_site, apps, tmp_path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    folder = _doctype_dir(apps)
    folder.mkdir(parents=True)
    (folder / f"{SLUG}.json").symlink_to(outside)
    _site(fake_site, _record())

    with pytest.raises(ValueError, match="escapes"):
        standalone.diff_doctype(DOCTYPE)


def test_diff_refuses_a_module_folder_symlinked_outside_the_app(fake_site, apps, tmp_path) -> None:
    package = apps / APP / APP
    outside = tmp_path / "outside_module"
    outside.mkdir()
    (package / "lab").rename(tmp_path / "moved")
    (package / "lab").symlink_to(outside)
    _site(fake_site, _record())

    with pytest.raises(ValueError, match="escapes"):
        standalone.diff_doctype(DOCTYPE)


def test_diff_compares_the_module_custom_file(fake_site, apps) -> None:
    record = _record()
    record["migration_hash"] = _write_app_json(apps, _exported(record))
    exported_field = {
        "name": f"{DOCTYPE}-custom_batch", "dt": DOCTYPE, "fieldname": "custom_batch",
        "fieldtype": "Data", "label": "Batch", "module": MODULE, "modified": OLD,
    }
    custom_dir = apps / APP / APP / "lab" / "custom"
    custom_dir.mkdir()
    (custom_dir / f"{SLUG}.json").write_text(
        standalone._frappe_json(
            {"custom_fields": [exported_field], "property_setters": [], "custom_perms": [],
             "links": [], "doctype": DOCTYPE, "sync_on_migrate": 1}
        ),
        encoding="utf-8",
    )
    fake_site.add("GET", RECORD_PATH, record)
    _modules(fake_site)
    _customizations(
        fake_site,
        fields=[
            {**exported_field, "label": "Batch No", "modified": NEW},
            {"name": f"{DOCTYPE}-custom_extra", "dt": DOCTYPE, "fieldname": "custom_extra",
             "fieldtype": "Int", "module": None, "modified": NEW},
            # Another app's customization of the same DocType is not this app's drift.
            {"name": f"{DOCTYPE}-other", "dt": DOCTYPE, "fieldname": "other",
             "fieldtype": "Int", "module": "Other Module", "modified": NEW},
        ],
    )

    result = standalone.diff_doctype(DOCTYPE)

    assert result["verdict"] == "in_sync"
    (custom,) = result["customizations"]
    assert custom["verdict"] == "site_ahead"
    assert custom["custom_fields"]["only_on_site"] == ["custom_extra"]
    assert custom["custom_fields"]["changed"] == {
        "custom_batch": {"label": {"from": "Batch No", "to": "Batch"}}
    }


def test_diff_reports_site_customizations_never_exported(fake_site, apps) -> None:
    record = _record()
    record["migration_hash"] = _write_app_json(apps, _exported(record))
    fake_site.add("GET", RECORD_PATH, record)
    _modules(fake_site)
    _customizations(
        fake_site,
        setters=[{"name": f"{DOCTYPE}-title-bold", "doc_type": DOCTYPE, "field_name": "title",
                  "property": "bold", "value": "1", "module": None}],
    )

    result = standalone.diff_doctype(DOCTYPE)

    (custom,) = result["customizations"]
    assert custom["verdict"] == "missing_in_app"
    assert custom["path"] is None
    assert custom["property_setters"]["only_on_site"] == ["title:bold"]


# --- list_app_drift --------------------------------------------------------------------------


def test_list_app_drift_reports_every_verdict(fake_site, apps) -> None:
    in_sync = _record()
    in_sync["migration_hash"] = _write_app_json(apps, _exported(in_sync))
    changed = _record(modified=NEW, track_changes=1)
    changed["migration_hash"] = _write_app_json(apps, _exported(_record()), "lab_ahead")
    _write_app_json(apps, {"name": "Lab Pulled", "doctype": "DocType", "modified": NEW},
                    "lab_pulled")

    summaries = [
        {"name": DOCTYPE, "module": MODULE, "custom": 0, "modified": OLD,
         "migration_hash": in_sync["migration_hash"]},
        {"name": "Lab Ahead", "module": MODULE, "custom": 0, "modified": NEW,
         "migration_hash": changed["migration_hash"]},
        {"name": "Lab Only On Site", "module": MODULE, "custom": 0, "modified": OLD,
         "migration_hash": None},
        {"name": "Lab Prototype", "module": MODULE, "custom": 1, "modified": OLD,
         "migration_hash": None},
    ]
    _modules(fake_site)
    fake_site.add("GET", standalone._resource("DocType"), summaries)
    fake_site.add("GET", standalone._resource("DocType", "Lab Ahead"),
                  {**changed, "name": "Lab Ahead"})

    result = standalone.list_app_drift(APP)

    assert {item["doctype"]: item["verdict"] for item in result["items"]} == {
        DOCTYPE: "in_sync",
        "Lab Ahead": "site_ahead",
        "Lab Only On Site": "missing_in_app",
        "Lab Prototype": "site_custom",
        "Lab Pulled": "missing_on_site",
    }
    assert result["total"] == 5
    assert result["next_offset"] is None
    # The in-sync DocType matched by hash and timestamp, so its record was never read.
    assert not fake_site.calls_to("GET", RECORD_PATH)


def test_list_app_drift_pages_with_offset(fake_site, apps) -> None:
    for index in range(3):
        _write_app_json(apps, {"name": f"Lab {index}", "modified": OLD}, f"lab_{index}")
    _modules(fake_site)
    fake_site.add("GET", standalone._resource("DocType"), [])

    first = standalone.list_app_drift(APP, limit=2)
    second = standalone.list_app_drift(APP, limit=2, offset=first["next_offset"])

    assert [item["doctype"] for item in first["items"]] == ["Lab 0", "Lab 1"]
    assert first["next_offset"] == 2
    assert [item["doctype"] for item in second["items"]] == ["Lab 2"]
    assert second["next_offset"] is None


def test_list_app_drift_refuses_a_module_of_another_app(fake_site, apps) -> None:
    _modules(fake_site)

    with pytest.raises(ValueError, match="does not belong"):
        standalone.list_app_drift(APP, module="Selling")


# --- promote_custom_doctype ------------------------------------------------------------------


def _promotion_site(fake_site, record: dict | None = None) -> dict:
    record = record or _record(custom=1)
    fake_site.add("GET", RECORD_PATH, record)
    _modules(fake_site)
    fake_site.add("GET", standalone._resource("DocType"), [])
    fake_site.add("GET", "/api/method/frappe.client.get_count", 0)
    return record


def test_promote_preview_lists_the_files_frappe_writes(fake_site, apps) -> None:
    _promotion_site(fake_site)

    preview = standalone.promote_custom_doctype(DOCTYPE, APP, MODULE)

    assert preview["requires_confirmation"] is True
    assert preview["requires_developer_mode"] is True
    assert preview["modified"] == OLD
    folder = _doctype_dir(apps)
    assert preview["files"] == [
        str(folder / f"{SLUG}.json"),
        str(folder / f"{SLUG}.py"),
        str(folder / f"{SLUG}.js"),
        str(folder / f"test_{SLUG}.py"),
    ]
    assert not folder.exists()
    assert not fake_site.calls_to("PUT")


def test_promote_preview_warns_about_custom_link_targets(fake_site, apps) -> None:
    record = _record(custom=1)
    record["fields"].append(_field("df-4", "items", "Table", 4, options="Lab Sample Item"))
    _promotion_site(fake_site, record)
    fake_site.add("GET", standalone._resource("DocType"),
                  [{"name": "Lab Sample Item", "custom": 1}])

    preview = standalone.promote_custom_doctype(DOCTYPE, APP, MODULE)

    assert any("Lab Sample Item" in warning for warning in preview["warnings"])


def test_promote_confirm_saves_custom_0_and_reports_files(fake_site, apps, monkeypatch) -> None:
    _promotion_site(fake_site)
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)

    def put(call):
        # Frappe's on_update exports the JSON; the controllers were staged before the save.
        _write_app_json(apps, {"name": DOCTYPE})
        return {"name": DOCTYPE, "custom": 0, "module": MODULE, "modified": SAVED}

    fake_site.add("PUT", RECORD_PATH, put)
    standalone._meta_cache[DOCTYPE] = (0.0, {"doctype": {}, "children": {}})

    result = standalone.promote_custom_doctype(
        DOCTYPE, APP, MODULE, confirm=True, expected_modified=OLD
    )

    (put_call,) = fake_site.calls_to("PUT", RECORD_PATH)
    assert put_call.json == {"data": {"custom": 0, "module": MODULE, "modified": OLD}}
    assert result["promoted"] is True
    assert result["native_files_complete"] is True
    assert result["reconciled_after_server_reload"] is False
    assert (_doctype_dir(apps) / f"{SLUG}.py").read_text().startswith("class LabSample(Document)")
    assert DOCTYPE not in standalone._meta_cache


@pytest.mark.parametrize("app", ["frappe", "ERPNext"])
def test_promote_refuses_protected_apps(fake_site, apps, app) -> None:
    with pytest.raises(ValueError, match="protected app"):
        standalone.promote_custom_doctype(DOCTYPE, app, MODULE)


def test_promote_refuses_a_module_of_another_app(fake_site, apps) -> None:
    _promotion_site(fake_site)
    _modules(fake_site, {"name": MODULE, "app_name": "other_app", "custom": 0})

    with pytest.raises(ValueError, match="does not belong to app"):
        standalone.promote_custom_doctype(DOCTYPE, APP, MODULE)


def test_promote_refuses_a_custom_module(fake_site, apps) -> None:
    _promotion_site(fake_site)
    _modules(fake_site, {"name": MODULE, "app_name": APP, "custom": 1})

    with pytest.raises(ValueError, match="custom module"):
        standalone.promote_custom_doctype(DOCTYPE, APP, MODULE)


def test_promote_refuses_an_existing_folder(fake_site, apps) -> None:
    _promotion_site(fake_site)
    _doctype_dir(apps).mkdir(parents=True)

    with pytest.raises(ValueError, match="already exists"):
        standalone.promote_custom_doctype(DOCTYPE, APP, MODULE)


def test_promote_refuses_a_standard_doctype(fake_site, apps) -> None:
    _promotion_site(fake_site, _record(custom=0))

    with pytest.raises(ValueError, match="already a standard DocType"):
        standalone.promote_custom_doctype(DOCTYPE, APP, MODULE)


def test_promote_refuses_a_default_print_format(fake_site, apps) -> None:
    _promotion_site(fake_site, _record(custom=1, default_print_format="Lab Print"))

    with pytest.raises(ValueError, match="default print format"):
        standalone.promote_custom_doctype(DOCTYPE, APP, MODULE)


def test_promote_refuses_a_stale_expected_modified(fake_site, apps) -> None:
    _promotion_site(fake_site)

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.promote_custom_doctype(
            DOCTYPE, APP, MODULE, confirm=True, expected_modified="2026-01-01 00:00:00"
        )
    assert not fake_site.calls_to("PUT")
    assert not _doctype_dir(apps).exists()


def test_promote_explains_developer_mode(fake_site, apps, monkeypatch) -> None:
    _promotion_site(fake_site)
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)
    fake_site.add(
        "PUT",
        RECORD_PATH,
        standalone.FrappeError("Not in Developer Mode!", "CannotCreateStandardDoctypeError"),
    )

    with pytest.raises(ValueError, match="developer mode"):
        standalone.promote_custom_doctype(
            DOCTYPE, APP, MODULE, confirm=True, expected_modified=OLD
        )
    # The DocType is still custom, so the staged controllers are removed again.
    assert not _doctype_dir(apps).exists()


def _reload_during_put(fake_site, monkeypatch, saved: dict) -> list[str]:
    record = _promotion_site(fake_site)
    state = {"put": False}
    waited: list[str] = []

    def put(call):
        state["put"] = True
        raise httpx.RemoteProtocolError("Server disconnected")

    fake_site.add("PUT", RECORD_PATH, put)
    fake_site.add(
        "GET", RECORD_PATH, lambda call: copy.deepcopy(saved if state["put"] else record)
    )
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: waited.append("ready"))
    return waited


def test_promote_transport_error_is_reconciled(fake_site, apps, monkeypatch) -> None:
    saved = _record(custom=0, modified=SAVED)
    waited = _reload_during_put(fake_site, monkeypatch, saved)

    result = standalone.promote_custom_doctype(
        DOCTYPE, APP, MODULE, confirm=True, expected_modified=OLD
    )

    assert result["promoted"] is True
    assert result["reconciled_after_server_reload"] is True
    assert result["modified"] == SAVED
    assert len(fake_site.calls_to("PUT", RECORD_PATH)) == 1
    assert waited == ["ready", "ready"]


def test_promote_transport_error_without_the_change_fails_and_cleans_up(
    fake_site, apps, monkeypatch
) -> None:
    _reload_during_put(fake_site, monkeypatch, _record(custom=1))

    with pytest.raises(RuntimeError, match="still a custom DocType"):
        standalone.promote_custom_doctype(
            DOCTYPE, APP, MODULE, confirm=True, expected_modified=OLD
        )
    assert len(fake_site.calls_to("PUT", RECORD_PATH)) == 1
    assert not _doctype_dir(apps).exists()


def test_scrub_matches_frappe_for_doctype_names() -> None:
    # Frappe's scrub replaces each space and hyphen; it does not collapse runs.
    assert standalone._scrub("Lab - Sample") == "lab___sample"
    assert standalone._scrub("Sales Order Item") == "sales_order_item"


def test_app_json_hash_matches_what_migrate_compares(apps) -> None:
    digest = _write_app_json(apps, {"name": DOCTYPE})
    path = _doctype_dir(apps) / f"{SLUG}.json"
    assert digest == hashlib.md5(path.read_bytes()).hexdigest()
    assert json.loads(path.read_text())["name"] == DOCTYPE
