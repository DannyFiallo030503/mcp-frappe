import stat
from pathlib import Path

import pytest

from frappe_mcp import standalone


def test_server_registers_expected_tools() -> None:
    assert set(standalone.server._tool_manager._tools) == {
        "list_installed_apps",
        "list_doctypes",
        "get_doctype_schema",
        "list_documents",
        "get_document",
        "create_document",
        "update_document",
        "delete_document",
        "run_report",
        "create_doctype",
        "update_doctype",
        "delete_doctype",
        "diff_doctype",
        "list_app_drift",
        "promote_custom_doctype",
        "get_workflow",
        "upsert_workflow",
        "list_notifications",
        "upsert_notification",
        "set_role_permissions",
        "create_custom_field",
        "set_field_property",
        "export_customizations",
        "rollback_export",
    }


def test_get_document_always_returns_docstatus(fake_site) -> None:
    fake_site.add_doctype("Example", [{"fieldname": "title"}, {"fieldname": "notes"}])
    fake_site.add(
        "GET",
        "/api/resource/Example/EX-1",
        {
            "doctype": "Example",
            "name": "EX-1",
            "title": "Submitted",
            "notes": "not requested",
            "docstatus": 1,
            "modified": "2026-10-19 12:00:00",
        },
    )

    result = standalone.get_document("Example", "EX-1", fields=["title"])

    assert result["docstatus"] == 1
    assert "notes" not in result
    assert len(fake_site.calls_to("GET", "/api/resource/Example/EX-1")) == 1


def test_generated_file_writes_refuse_frappe_core_apps() -> None:
    for app in ("frappe", "ERPNext"):
        with pytest.raises(ValueError, match="protected app"):
            standalone._assert_writable_app(app)

    standalone._assert_writable_app("erp_app")


def test_doctype_creation_reconciles_reload_without_replaying_persisted_write(monkeypatch) -> None:
    attempts = []

    def request(method, path, **kwargs):
        attempts.append((method, path))
        raise standalone.httpx.ConnectError("worker restarted")

    monkeypatch.setattr(standalone, "_request", request)
    monkeypatch.setattr(standalone, "_wait_for_doctype", lambda *args: True)

    reconciled = standalone._create_doctype_request("Example", {"doctype": "DocType"}, [])

    assert reconciled is True
    assert len(attempts) == 1


def test_doctype_creation_does_not_blindly_replay_when_metadata_is_absent(
    monkeypatch, tmp_path: Path
) -> None:
    generated = tmp_path / "example.json"
    generated.write_text("partial", encoding="utf-8")
    attempts = []

    def request(method, path, **kwargs):
        attempts.append((method, path))
        raise standalone.httpx.ConnectError("worker restarted")

    monkeypatch.setattr(standalone, "_request", request)
    monkeypatch.setattr(standalone, "_wait_for_doctype", lambda *args: False)

    with pytest.raises(RuntimeError, match="no DocType record was found"):
        standalone._create_doctype_request("Example", {"doctype": "DocType"}, [generated])

    assert len(attempts) == 1
    assert generated.exists()


def test_delete_reconciles_a_worker_reload_after_document_is_gone(
    fake_site, monkeypatch
) -> None:
    fake_site.add("GET", "/api/resource/Example/EX-1", {"name": "EX-1", "modified": "current"})
    fake_site.add(
        "DELETE", "/api/resource/Example/EX-1", standalone.httpx.ConnectError("worker restarted")
    )
    monkeypatch.setattr(standalone, "_wait_for_document_deleted", lambda *args: True)

    result = standalone.delete_document(
        "Example", "EX-1", expected_modified="current", confirm=True
    )

    assert result["deleted"] is True
    assert result["reconciled_after_server_reload"] is True
    assert len(fake_site.calls_to("DELETE")) == 1


def test_app_module_paths_are_confined(tmp_path: Path, monkeypatch) -> None:
    apps = tmp_path / "apps"
    module = apps / "example_app" / "example_app" / "example_module"
    module.mkdir(parents=True)
    (apps / "example_app" / "example_app").mkdir(exist_ok=True)
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(apps))

    resolved_app, resolved_module = standalone._app_path("example_app", "Example Module")
    assert resolved_app == apps / "example_app"
    assert resolved_module == module

    with pytest.raises(ValueError, match="Invalid app or module"):
        standalone._app_path("../outside", "Example Module")


def test_symlink_escape_is_rejected(tmp_path: Path, monkeypatch) -> None:
    apps = tmp_path / "apps"
    apps.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (apps / "example_app").symlink_to(outside)
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(apps))

    with pytest.raises(ValueError, match="escapes"):
        standalone._app_path("example_app", "module")


def test_doctype_folders_use_frappe_scrub() -> None:
    assert standalone._scrub("Purchase Order Item") == "purchase_order_item"
    assert standalone._scrub("Lab-Sample") == "lab_sample"
    assert standalone._scrub("áé漢字") == "áé漢字"


def test_fixture_plan_preserves_existing_hooks(tmp_path: Path) -> None:
    app_path = tmp_path / "example_app"
    package = app_path / "example_app"
    hooks = package / "hooks.py"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("fixtures = ['existing.json']\n", encoding="utf-8")

    path, content = standalone._fixture_plan(
        app_path, {"Client Script": {"dt": ["Example DocType"], "module": ["Example Module"]}}
    )

    assert path == hooks
    assert "existing.json" in content
    assert "MCP FRAPPE MANAGED FIXTURES" in content
    assert "Client Script" in content
    assert "Custom Field" not in content


def test_atomic_write_preserves_existing_mode_and_sets_public_source_mode(tmp_path: Path) -> None:
    existing = tmp_path / "hooks.py"
    existing.write_text("old\n", encoding="utf-8")
    existing.chmod(0o640)

    standalone._atomic_write(existing, "updated\n")
    created = tmp_path / "new.json"
    standalone._atomic_write(created, "{}\n")

    assert stat.S_IMODE(existing.stat().st_mode) == 0o640
    assert stat.S_IMODE(created.stat().st_mode) == 0o644


def test_doctype_payload_carries_native_fields_settings_and_permissions() -> None:
    fields = [
        {"fieldname": "title", "fieldtype": "Data", "reqd": 1, "in_list_view": 1},
        {"fieldname": "lines", "fieldtype": "Table", "options": "Example Line"},
    ]
    permissions = [{"role": "System Manager", "read": 1, "write": 1, "create": 1}]

    payload = standalone._build_doctype_payload(
        "Example Module",
        "Example Parent",
        fields,
        is_submittable=True,
        is_tree=True,
        properties={
            "autoname": "field:title",
            "track_changes": 1,
            "search_fields": "title",
            "sort_field": "modified",
            "sort_order": "DESC",
        },
        permissions=permissions,
    )

    assert payload["fields"] == fields
    assert payload["permissions"] == permissions
    assert payload["custom"] == 0
    assert payload["is_submittable"] == 1
    assert payload["is_tree"] == 1
    assert payload["autoname"] == "field:title"
    assert payload["track_changes"] == 1
    assert payload["search_fields"] == "title"


def test_doctype_payload_marks_custom_doctypes_and_rejects_custom_virtual() -> None:
    fields = [{"fieldname": "title", "fieldtype": "Data"}]

    payload = standalone._build_doctype_payload("Example Module", "Example", fields, is_custom=True)

    assert payload["custom"] == 1
    with pytest.raises(ValueError, match="custom Virtual"):
        standalone._build_doctype_payload(
            "Example Module", "Example", fields, is_custom=True, properties={"is_virtual": 1}
        )


def test_custom_doctype_creation_does_not_require_local_files(monkeypatch) -> None:
    monkeypatch.delenv("FRAPPE_APPS_PATH", raising=False)
    monkeypatch.setattr(standalone, "_assert_remote_module", lambda *_: None)
    exists_values = iter([False, False, True])
    monkeypatch.setattr(standalone, "_doctype_exists", lambda *_: next(exists_values))
    requests = []

    def create(name, payload, generated_files):
        requests.append((name, payload, generated_files))
        return False

    monkeypatch.setattr(standalone, "_create_doctype_request", create)
    arguments = {
        "app": "example_app",
        "module": "Example Module",
        "name": "Example Custom DocType",
        "fields": [{"fieldname": "title", "fieldtype": "Data"}],
        "is_custom": True,
    }

    preview = standalone.create_doctype(**arguments)
    result = standalone.create_doctype(**arguments, confirm=True)

    assert preview["requires_confirmation"] is True
    assert preview["destination"] is None
    assert preview["expected_native_files"] == []
    assert requests[0][1]["custom"] == 1
    assert requests[0][2] == []
    assert result["created"] is True
    assert result["files"] == {}
    assert result["native_files_complete"] is None


def test_doctype_payload_rejects_duplicate_fields_unknown_properties_and_single_table() -> None:
    duplicate_fields = [
        {"fieldname": "value", "fieldtype": "Data"},
        {"fieldname": "value", "fieldtype": "Int"},
    ]
    with pytest.raises(ValueError, match="unique"):
        standalone._build_doctype_payload("Example Module", "Example", duplicate_fields)

    with pytest.raises(ValueError, match="Unsupported DocType properties"):
        standalone._build_doctype_payload(
            "Example Module",
            "Example",
            [{"fieldname": "value", "fieldtype": "Data"}],
            properties={"custom": 1},
        )

    with pytest.raises(ValueError, match="both a child table and a Single"):
        standalone._build_doctype_payload(
            "Example Module",
            "Example",
            [{"fieldname": "value", "fieldtype": "Data"}],
            is_child_table=True,
            is_single=True,
        )


def test_set_role_permissions_previews_then_calls_native_permission_manager(monkeypatch) -> None:
    requests = []

    monkeypatch.setattr(
        standalone,
        "_meta_bundle",
        lambda _: {"doctype": {"name": "Example", "istable": 0, "is_virtual": 0}, "children": {}},
    )

    def request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        if path == "/api/resource/Role":
            return [{"name": "Example Manager", "disabled": 0}]
        if path == "/api/resource/Custom%20DocPerm":
            return []
        return None

    monkeypatch.setattr(standalone, "_request", request)
    arguments = {
        "doctype": "Example",
        "role": "Example Manager",
        "permissions": {"read": True, "write": True, "create": False},
        "permlevel": 2,
    }

    preview = standalone.set_role_permissions(**arguments)
    assert preview["requires_confirmation"] is True
    assert not any(method == "POST" for method, _, _ in requests)

    result = standalone.set_role_permissions(**arguments, confirm=True)
    assert result["updated"] is True
    writes = [(method, path, kwargs) for method, path, kwargs in requests if method == "POST"]
    assert writes[0][1].endswith("permission_manager.add")
    assert len(writes) == 4
    assert writes[1][2]["json"]["ptype"] == "read"
    assert writes[3][2]["json"]["value"] == 0
