"""export_customizations writes Frappe's native module customization files, and rolls back."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frappe_mcp import standalone

APP = "example_app"
MODULE = "Example Module"
HOOKS = "app_name = 'example_app'\n"

CUSTOM_FIELD = {
    "name": "Customer-custom_region",
    "owner": "Administrator",
    "creation": "2026-10-19 12:00:00.000001",
    "modified": "2026-10-19 12:00:00.000001",
    "modified_by": "Administrator",
    "docstatus": 0,
    "idx": 0,
    "dt": "Customer",
    "module": MODULE,
    "fieldname": "custom_region",
    "fieldtype": "Data",
    "label": "Región",
    "insert_after": "customer_name",
}
PROPERTY_SETTER = {
    "name": "Customer-customer_name-bold",
    "doc_type": "Customer",
    "module": MODULE,
    "doctype_or_field": "DocField",
    "field_name": "customer_name",
    "property": "bold",
    "property_type": "Check",
    "value": "1",
}
CHILD_FIELD = {
    "name": "Customer Item-custom_note",
    "dt": "Customer Item",
    "module": MODULE,
    "fieldname": "custom_note",
    "fieldtype": "Small Text",
}
LINK = {"name": "abc123", "parent": "Customer", "link_doctype": "Address", "link_fieldname": "x"}
PERM = {"name": "perm1", "parent": "Customer", "role": "Sales User", "read": 1}
CLIENT_SCRIPT = {
    "name": "Customer Form",
    "owner": "Administrator",
    "creation": "2026-10-19 12:00:00.000001",
    "modified": "2026-10-19 12:00:00.000001",
    "modified_by": "Administrator",
    "docstatus": 0,
    "idx": 0,
    "_user_tags": None,
    "dt": "Customer",
    "module": MODULE,
    "view": "Form",
    "enabled": 1,
    "script": "frappe.ui.form.on('Customer', {});",
}


@pytest.fixture
def app_tree(tmp_path: Path, monkeypatch) -> Path:
    apps = tmp_path / "apps"
    package = apps / APP / APP
    ledger = package / "example_module" / "doctype" / "customer_ledger"
    ledger.mkdir(parents=True)
    (ledger / "customer_ledger.json").write_text('{"name": "Customer Ledger"}\n', encoding="utf-8")
    (package / "hooks.py").write_text(HOOKS, encoding="utf-8")
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(apps))
    return apps / APP


def _rows(site, doctype: str, filters: list, rows: list, **params) -> None:
    site.add(
        "GET",
        standalone._resource(doctype),
        rows,
        params={"filters": standalone._filters(filters), **params},
    )


def _customizations(site, doctype, *, fields=(), setters=(), links=(), perms=(), module=None):
    extra = [["module", "=", module]] if module else []
    _rows(site, "Custom Field", [["dt", "=", doctype], *extra], list(fields))
    _rows(site, "Property Setter", [["doc_type", "=", doctype], *extra], list(setters))
    _rows(site, "DocType Link", [["parent", "=", doctype]], list(links), parent="DocType")
    _rows(site, "Custom DocPerm", [["parent", "=", doctype]], list(perms))


@pytest.fixture
def site(fake_site, app_tree):
    fake_site.add(
        "GET",
        standalone._resource("Module Def"),
        [{"name": MODULE, "app_name": APP}],
        params={"filters": standalone._filters({"name": MODULE})},
    )
    fake_site.add_doctype(
        "Customer",
        [
            {"fieldname": "customer_name", "fieldtype": "Data"},
            {"fieldname": "items", "fieldtype": "Table", "options": "Customer Item"},
        ],
        module="Selling",
    )
    fake_site.add_doctype(
        "Customer Item", [{"fieldname": "item", "fieldtype": "Data"}], istable=1, module="Selling"
    )
    _customizations(fake_site, "Customer", fields=[CUSTOM_FIELD], setters=[PROPERTY_SETTER])
    _customizations(fake_site, "Customer Item", fields=[CHILD_FIELD])
    _rows(fake_site, "Client Script", [["dt", "in", ["Customer", "Customer Item"]],
                                       ["module", "=", MODULE]], [])
    return fake_site


def _tree(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def _export(**kwargs) -> dict:
    args = {"app": APP, "module": MODULE, "doctypes": ["Customer"], **kwargs}
    preview = standalone.export_customizations(**args)
    if not preview.get("requires_confirmation"):
        return preview
    return standalone.export_customizations(
        **args, confirm=True, expected_preview=preview["preview_id"]
    )


def _native(app_tree: Path, doctype_slug: str) -> Path:
    return app_tree / APP / "example_module" / "custom" / f"{doctype_slug}.json"


def test_export_writes_native_custom_file_under_custom_folder(site, app_tree) -> None:
    preview = standalone.export_customizations(app=APP, module=MODULE, doctypes=["Customer"])

    target = _native(app_tree, "customer")
    assert preview["requires_confirmation"] is True
    assert str(target) in [item["path"] for item in preview["files"]]
    assert not target.exists()

    result = _export()

    assert str(target) in result["files"]
    data = json.loads(target.read_text(encoding="utf-8"))
    assert set(data) == {
        "custom_fields",
        "property_setters",
        "custom_perms",
        "links",
        "doctype",
        "sync_on_migrate",
    }
    assert data["doctype"] == "Customer"
    assert data["sync_on_migrate"] == 1
    assert data["custom_fields"] == [CUSTOM_FIELD]
    assert data["property_setters"] == [PROPERTY_SETTER]
    assert data["custom_perms"] == []
    assert data["links"] == []


def test_export_serializes_like_frappe_as_json(site, app_tree) -> None:
    _export()

    text = _native(app_tree, "customer").read_text(encoding="utf-8")
    data = json.loads(text)
    expected = json.dumps(data, indent=1, sort_keys=True, separators=(",", ": "))
    assert text == expected
    assert "\\u00f3" in text  # frappe.as_json escapes non-ASCII by default


def test_export_recurses_into_child_tables(site, app_tree) -> None:
    result = _export()

    child = _native(app_tree, "customer_item")
    assert str(child) in result["files"]
    data = json.loads(child.read_text(encoding="utf-8"))
    assert data["doctype"] == "Customer Item"
    assert data["custom_fields"] == [CHILD_FIELD]
    assert result["doctypes"] == ["Customer", "Customer Item"]


def test_export_reads_rows_the_way_frappe_does(site) -> None:
    _export()

    calls = {
        call.path: call.params
        for call in site.calls_to("GET")
        if call.params and call.params.get("order_by") == "name asc"
    }
    link = calls[standalone._resource("DocType Link")]
    assert link["parent"] == "DocType"
    assert json.loads(link["filters"]) == [["parent", "=", "Customer Item"]]
    assert calls[standalone._resource("Custom Field")]["fields"] == '["*"]'


def test_export_skips_empty_doctypes_and_writes_nothing_when_all_are_empty(
    site, app_tree
) -> None:
    _customizations(site, "Customer")
    _customizations(site, "Customer Item", links=[LINK])
    before = _tree(app_tree)

    result = standalone.export_customizations(app=APP, module=MODULE, doctypes=["Customer"])

    assert result["nothing_to_export"] is True
    assert result["requires_confirmation"] is False
    assert result["files"] == []
    assert result["empty"] == ["Customer", "Customer Item"]
    with pytest.raises(ValueError, match="Nothing to export"):
        standalone.export_customizations(
            app=APP, module=MODULE, doctypes=["Customer"], confirm=True, expected_preview="x"
        )
    assert _tree(app_tree) == before


def test_export_skips_only_the_empty_doctype(site, app_tree) -> None:
    _customizations(site, "Customer Item")

    result = _export()

    assert _native(app_tree, "customer").is_file()
    assert not _native(app_tree, "customer_item").exists()
    assert result["empty"] == ["Customer Item"]


def test_export_includes_permissions_only_when_asked(site, app_tree) -> None:
    _customizations(
        site, "Customer", fields=[CUSTOM_FIELD], setters=[PROPERTY_SETTER], perms=[PERM]
    )

    _export()
    data = json.loads(_native(app_tree, "customer").read_text(encoding="utf-8"))
    assert data["custom_perms"] == []
    assert not site.calls_to("GET", standalone._resource("Custom DocPerm"))

    _export(with_permissions=True)
    data = json.loads(_native(app_tree, "customer").read_text(encoding="utf-8"))
    assert data["custom_perms"] == [PERM]


def test_export_applies_module_filter_only_when_asked(site, app_tree) -> None:
    _customizations(site, "Customer", module=MODULE)
    _customizations(site, "Customer Item", module=MODULE)

    _export()
    assert _native(app_tree, "customer").is_file()

    result = standalone.export_customizations(
        app=APP, module=MODULE, doctypes=["Customer"], apply_module_filter=True
    )
    assert result["nothing_to_export"] is True


def test_export_sync_on_migrate_can_be_turned_off(site, app_tree) -> None:
    _export(sync_on_migrate=False)

    data = json.loads(_native(app_tree, "customer").read_text(encoding="utf-8"))
    assert data["sync_on_migrate"] == 0


def test_export_never_writes_doctype_json(site, app_tree) -> None:
    ledger = app_tree / APP / "example_module" / "doctype" / "customer_ledger"
    before = (ledger / "customer_ledger.json").read_bytes()

    result = _export()

    assert not (app_tree / APP / "example_module" / "doctype" / "customer").exists()
    assert (ledger / "customer_ledger.json").read_bytes() == before
    assert all("/doctype/" not in path for path in result["files"])
    assert not (app_tree / APP / "example_module" / "fixtures").exists()


def test_export_without_client_scripts_leaves_hooks_alone(site, app_tree) -> None:
    result = _export()

    assert (app_tree / APP / "hooks.py").read_text(encoding="utf-8") == HOOKS
    assert str(app_tree / APP / "hooks.py") not in result["files"]
    assert not (app_tree / APP / "fixtures").exists()


def test_client_scripts_go_to_app_level_fixtures(site, app_tree) -> None:
    _rows(
        site,
        "Client Script",
        [["dt", "in", ["Customer", "Customer Item"]], ["module", "=", MODULE]],
        [CLIENT_SCRIPT],
    )

    result = _export()

    fixture = app_tree / APP / "fixtures" / "client_script.json"
    assert str(fixture) in result["files"]
    (row,) = json.loads(fixture.read_text(encoding="utf-8"))
    assert row["doctype"] == "Client Script"
    assert row["name"] == "Customer Form"
    assert row["script"] == CLIENT_SCRIPT["script"]
    for dropped in ("owner", "creation", "modified_by", "idx", "_user_tags"):
        assert dropped not in row
    hooks = (app_tree / APP / "hooks.py").read_text(encoding="utf-8")
    assert hooks.startswith(HOOKS.rstrip())
    assert "'dt': 'Client Script'" in hooks
    assert "Custom Field" not in hooks
    assert "Property Setter" not in hooks


def test_client_script_fixture_keeps_rows_outside_the_export(site, app_tree) -> None:
    fixture = app_tree / APP / "fixtures" / "client_script.json"
    fixture.parent.mkdir()
    other = {"doctype": "Client Script", "name": "Supplier Form", "dt": "Supplier"}
    stale = {"doctype": "Client Script", "name": "Old", "dt": "Customer", "module": MODULE}
    fixture.write_text(json.dumps([other, stale]), encoding="utf-8")
    _rows(
        site,
        "Client Script",
        [["dt", "in", ["Customer", "Customer Item"]], ["module", "=", MODULE]],
        [CLIENT_SCRIPT],
    )

    _export()

    names = [row["name"] for row in json.loads(fixture.read_text(encoding="utf-8"))]
    assert names == ["Supplier Form", "Customer Form"]


def test_fixture_plan_keeps_only_client_script_entries(tmp_path: Path) -> None:
    package = tmp_path / APP / APP
    package.mkdir(parents=True)
    old_block = (
        f"\n\n{standalone._HOOK_BEGIN}\n"
        "mcp_frappe_exported_fixtures = [{'dt': 'Custom Field', 'filters': []}, "
        "{'dt': 'Client Script', 'filters': [['dt', 'in', ['Supplier']], "
        "['module', 'in', ['Buying']]]}]\n"
        "fixtures = list(globals().get('fixtures', [])) + mcp_frappe_exported_fixtures\n"
        f"{standalone._HOOK_END}\n"
    )
    (package / "hooks.py").write_text(HOOKS + old_block, encoding="utf-8")

    _, content = standalone._fixture_plan(tmp_path / APP, ["Customer"], MODULE)

    assert "Custom Field" not in content
    assert "['Customer', 'Supplier']" in content
    assert f"['Buying', '{MODULE}']" in content


def test_export_then_rollback_restores_tree_byte_for_byte(site, app_tree) -> None:
    _rows(
        site,
        "Client Script",
        [["dt", "in", ["Customer", "Customer Item"]], ["module", "=", MODULE]],
        [CLIENT_SCRIPT],
    )
    before = _tree(app_tree)

    result = _export()
    assert _tree(app_tree) != before

    preview = standalone.rollback_export(app=APP, module=MODULE, rollback_id=result["rollback_id"])
    rolled = standalone.rollback_export(
        app=APP,
        module=MODULE,
        rollback_id=result["rollback_id"],
        expected_preview=preview["preview_id"],
        confirm=True,
    )

    assert rolled["rolled_back"] is True
    assert _tree(app_tree) == before


def test_rollback_restores_an_export_made_by_the_old_fixture_exporter(site, app_tree) -> None:
    """Old manifests hold app-relative paths under <module>/fixtures/ and no created_dirs."""
    import hashlib

    module_fixtures = app_tree / APP / "example_module" / "fixtures"
    module_fixtures.mkdir()
    exported = '[{"name": "Customer-custom_region"}]\n'
    (module_fixtures / "custom_field.json").write_text(exported, encoding="utf-8")
    hooks_exported = HOOKS + "# exported\n"
    (app_tree / APP / "hooks.py").write_text(hooks_exported, encoding="utf-8")
    backups = app_tree / ".mcp-frappe-backups"
    backups.mkdir()
    (backups / "hooks.bak").write_text(HOOKS, encoding="utf-8")
    manifest = [
        {
            "path": f"{APP}/example_module/fixtures/custom_field.json",
            "backup": None,
            "exported_sha256": hashlib.sha256(exported.encode()).hexdigest(),
        },
        {
            "path": f"{APP}/hooks.py",
            "backup": "hooks.bak",
            "exported_sha256": hashlib.sha256(hooks_exported.encode()).hexdigest(),
        },
    ]
    rollback_id = "export-" + "a" * 32 + ".json"
    (backups / rollback_id).write_text(json.dumps(manifest), encoding="utf-8")

    preview = standalone.rollback_export(app=APP, module=MODULE, rollback_id=rollback_id)
    rolled = standalone.rollback_export(
        app=APP,
        module=MODULE,
        rollback_id=rollback_id,
        expected_preview=preview["preview_id"],
        confirm=True,
    )

    assert rolled["rolled_back"] is True
    assert not (module_fixtures / "custom_field.json").exists()
    assert (app_tree / APP / "hooks.py").read_text(encoding="utf-8") == HOOKS
    assert not backups.exists()


def test_export_refuses_protected_apps(site) -> None:
    with pytest.raises(ValueError, match="protected app"):
        standalone.export_customizations(app="frappe", module=MODULE, doctypes=["Customer"])


def test_export_preview_id_must_match(site, app_tree) -> None:
    with pytest.raises(ValueError, match="stale"):
        standalone.export_customizations(
            app=APP, module=MODULE, doctypes=["Customer"], confirm=True, expected_preview="bad"
        )
    assert not _native(app_tree, "customer").exists()
