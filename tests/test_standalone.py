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
        "set_role_permissions",
        "create_custom_field",
        "set_field_property",
        "export_form_customizations",
        "rollback_export",
    }


def test_client_uses_aspire_local_administrator_defaults(monkeypatch) -> None:
    for key in (
        "FRAPPE_URL",
        "FRAPPE_SITE",
        "FRAPPE_API_KEY",
        "FRAPPE_API_SECRET",
        "FRAPPE_USERNAME",
        "FRAPPE_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(standalone, "_session", None)
    calls = {}

    class Response:
        is_error = False

        @staticmethod
        def json():
            return {"message": "Logged In"}

    class Client:
        def __init__(self, **kwargs):
            calls["config"] = kwargs

        def post(self, path, *, data):
            calls["login"] = {"path": path, "data": data}
            return Response()

    monkeypatch.setattr(standalone.httpx, "Client", Client)

    standalone._client()

    assert calls["config"]["base_url"] == "http://127.0.0.1:8000"
    assert calls["config"]["headers"]["Host"] == "cubazone.localhost"
    assert calls["login"] == {
        "path": "/api/method/login",
        "data": {"usr": "Administrator", "pwd": "admin"},
    }

    monkeypatch.setenv("FRAPPE_USERNAME", "Limited User")
    monkeypatch.setenv("FRAPPE_PASSWORD", "test-password")
    monkeypatch.setattr(standalone, "_session", None)
    calls.clear()
    standalone._client()
    assert calls["login"]["data"]["usr"] == "Limited User"
    assert calls["login"]["data"]["pwd"] == "test-password"


def test_remote_client_requires_explicit_password_or_api_token(monkeypatch) -> None:
    monkeypatch.setattr(standalone, "_session", None)
    monkeypatch.setenv("FRAPPE_URL", "https://frappe.example.invalid")
    monkeypatch.delenv("FRAPPE_API_KEY", raising=False)
    monkeypatch.delenv("FRAPPE_API_SECRET", raising=False)
    monkeypatch.delenv("FRAPPE_PASSWORD", raising=False)
    monkeypatch.setattr(
        standalone.httpx,
        "Client",
        lambda **_kwargs: pytest.fail("Client must not send implicit credentials remotely"),
    )

    with pytest.raises(RuntimeError, match="FRAPPE_PASSWORD for remote sites"):
        standalone._client()


def test_read_validation_allows_name_but_write_validation_rejects_it(monkeypatch) -> None:
    monkeypatch.setattr(standalone, "_doctype", lambda _: {"fields": [{"fieldname": "title"}]})

    standalone._assert_fields("Example", {"name": None, "title": None})
    with pytest.raises(ValueError, match="name"):
        standalone._assert_fields("Example", {"name": "x"}, writing=True)


def test_readonly_field_is_rejected_for_write(monkeypatch) -> None:
    monkeypatch.setattr(
        standalone,
        "_doctype",
        lambda _: {
            "fields": [
                {"fieldname": "status", "read_only": 1},
            ]
        },
    )

    with pytest.raises(ValueError, match="Read-only"):
        standalone._assert_fields("Example", {"status": "Open"}, writing=True)


def test_get_document_always_returns_docstatus(monkeypatch) -> None:
    monkeypatch.setattr(standalone, "_doctype", lambda _: {"fields": [{"fieldname": "title"}]})
    monkeypatch.setattr(standalone, "_assert_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        standalone,
        "_request",
        lambda *args, **kwargs: {
            "doctype": "Example",
            "name": "EX-1",
            "title": "Submitted",
            "docstatus": 1,
            "modified": "2026-10-19 12:00:00",
        },
    )

    result = standalone.get_document("Example", "EX-1", fields=["title"])

    assert result["docstatus"] == 1


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


def test_delete_reconciles_a_worker_reload_after_document_is_gone(monkeypatch) -> None:
    def request(method, path, **kwargs):
        if method == "GET" and path.endswith("/Example/EX-1"):
            return {"name": "EX-1", "modified": "current"}
        if method == "DELETE":
            raise standalone.httpx.ConnectError("worker restarted")
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(standalone, "_request", request)
    monkeypatch.setattr(standalone, "_wait_for_document_deleted", lambda *args: True)

    result = standalone.delete_document(
        "Example", "EX-1", expected_modified="current", confirm=True
    )

    assert result["deleted"] is True
    assert result["reconciled_after_server_reload"] is True


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


def test_doctype_slugs_are_filesystem_safe() -> None:
    assert standalone._slug("Purchase Order / Item") == "purchase_order_item"
    assert standalone._slug("áé漢字") == "áé漢字"


def test_export_form_customizations_calls_native_frappe_export(
    tmp_path: Path, monkeypatch
) -> None:
    app_path = tmp_path / "apps" / "erp_app"
    module_path = app_path / "erp_app" / "cubazone"
    module_path.mkdir(parents=True)
    monkeypatch.setattr(standalone, "_assert_app_module", lambda *_: (app_path, module_path))
    monkeypatch.setattr(
        standalone,
        "_doctype",
        lambda _: {"module": "Setup", "fields": []},
    )

    def request(method, path, **kwargs):
        if method == "POST":
            assert path == "/api/method/frappe.modules.utils.export_customizations"
            assert kwargs["data"] == {
                "module": "CubaZone",
                "doctype": "Brand",
                "sync_on_migrate": 1,
                "with_permissions": 0,
            }
            return str(module_path / "custom" / "brand.json")
        if path == "/api/method/frappe.auth.get_logged_user":
            return "Administrator"
        if path == "/api/method/frappe.core.doctype.user.user.get_roles":
            return ["System Manager"]
        assert method == "GET"
        if path.endswith("/Custom%20Field"):
            return [
                {
                    "name": "Brand-disabled",
                    "modified": "2026-10-22 10:00:00",
                }
            ]
        if path.endswith(("/Property%20Setter", "/DocType%20Link")):
            return []
        raise AssertionError(f"Unexpected resource: {path}")

    monkeypatch.setattr(standalone, "_request", request)

    preview = standalone.export_form_customizations(
        app="erp_app", module="CubaZone", doctype="Brand"
    )

    destination = module_path / "custom" / "brand.json"
    assert preview["requires_confirmation"] is True
    assert preview["sync_on_migrate"] is True
    assert preview["files"][0]["path"] == str(destination)
    assert preview["content_diff_available"] is False
    assert not destination.exists()

    result = standalone.export_form_customizations(
        app="erp_app",
        module="CubaZone",
        doctype="Brand",
        confirm=True,
        expected_preview=preview["preview_id"],
    )

    assert result["exported"] is True
    assert result["sync_on_migrate"] is True
    assert result["native_result"] == str(destination)
    assert result["shared_app_tree_detected"] is True


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
        "CubaZone",
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

    payload = standalone._build_doctype_payload("CubaZone", "Example", fields, is_custom=True)

    assert payload["custom"] == 1
    with pytest.raises(ValueError, match="custom Virtual"):
        standalone._build_doctype_payload(
            "CubaZone", "Example", fields, is_custom=True, properties={"is_virtual": 1}
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


def test_doctype_schema_uses_frappe_native_metadata(monkeypatch) -> None:
    schema = {
        "name": "Customer",
        "fields": [
            {"fieldname": "custom_field", "is_custom_field": 1, "idx": 1},
            {"fieldname": "customer_name", "idx": 2},
        ],
    }

    def request(method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/method/frappe.desk.form.load.getdoctype"
        assert kwargs["params"] == {"doctype": "Customer"}
        return {"docs": [schema]}

    monkeypatch.setattr(standalone, "_request", request)

    assert standalone._doctype("Customer") == schema


def test_doctype_schema_rejects_unexpected_native_metadata(monkeypatch) -> None:
    monkeypatch.setattr(standalone, "_request", lambda *_args, **_kwargs: {"docs": []})

    with pytest.raises(RuntimeError, match="unexpected DocType metadata"):
        standalone._doctype("Customer")


def test_run_report_uses_frappe_prepared_report_default(monkeypatch) -> None:
    def request(method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/method/frappe.desk.query_report.run"
        assert kwargs["params"] == {
            "report_name": "Customer Ledger",
            "filters": '{"company": "Example Company"}',
            "ignore_prepared_report": False,
        }
        return {"result": [{"name": f"row-{index}"} for index in range(3)]}

    monkeypatch.setattr(standalone, "_request", request)

    result = standalone.run_report(
        "Customer Ledger", filters={"company": "Example Company"}, limit=2
    )

    assert result["result"] == [{"name": "row-0"}, {"name": "row-1"}]
    assert result["returned_rows"] == 2
    assert result["truncated"] is True


def test_doctype_payload_rejects_duplicate_fields_unknown_properties_and_single_table() -> None:
    duplicate_fields = [
        {"fieldname": "value", "fieldtype": "Data"},
        {"fieldname": "value", "fieldtype": "Int"},
    ]
    with pytest.raises(ValueError, match="unique"):
        standalone._build_doctype_payload("CubaZone", "Example", duplicate_fields)

    with pytest.raises(ValueError, match="Unsupported DocType properties"):
        standalone._build_doctype_payload(
            "CubaZone",
            "Example",
            [{"fieldname": "value", "fieldtype": "Data"}],
            properties={"custom": 1},
        )

    with pytest.raises(ValueError, match="both a child table and a Single"):
        standalone._build_doctype_payload(
            "CubaZone",
            "Example",
            [{"fieldname": "value", "fieldtype": "Data"}],
            is_child_table=True,
            is_single=True,
        )


def test_set_role_permissions_previews_then_calls_native_permission_manager(monkeypatch) -> None:
    requests = []

    monkeypatch.setattr(
        standalone,
        "_doctype_raw",
        lambda _: {"name": "Example", "istable": 0, "is_virtual": 0},
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
