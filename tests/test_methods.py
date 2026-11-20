"""Allowlisted method calls: the FRAPPE_MCP_METHODS file and the three method tools."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from frappe_mcp import standalone

ROOT = Path(__file__).resolve().parents[1]
APP = "lab_app"
MAPPER = "lab_app.lab.doctype.lab_order.lab_order.make_lab_invoice"
READER = "lab_app.api.get_price"
WRITER = "lab_app.api.close_period"
METHOD = "/api/method/"


@pytest.fixture
def methods(monkeypatch):
    """Isolate the method tools' registration and the loaded allowlist from other tests."""
    monkeypatch.setattr(
        standalone.server._tool_manager, "_tools", dict(standalone.server._tool_manager._tools)
    )
    monkeypatch.setattr(standalone, "_TOOLSETS", copy.deepcopy(standalone._TOOLSETS))
    for name in ("FRAPPE_MCP_METHODS", "FRAPPE_APPS_PATH", "FRAPPE_MCP_TOOLSETS",
                 "FRAPPE_MCP_READ_ONLY", "FRAPPE_URL"):
        monkeypatch.delenv(name, raising=False)
    standalone._method_config.clear()
    yield monkeypatch
    standalone._method_config.clear()


def _entries() -> list[dict]:
    return [
        {
            "method": MAPPER,
            "kind": "mapper",
            "source_doctype": "Lab Order",
            "target_doctype": "Lab Invoice",
            "description": "Draft a Lab Invoice from a Lab Order",
        },
        {
            "method": READER,
            "kind": "read",
            "description": "Price of an item",
            "params": {
                "item": {"type": "string", "required": True},
                "qty": {"type": "integer"},
                "mode": {"enum": ["net", "gross"]},
                "options": {"type": "object"},
            },
        },
        {"method": WRITER, "kind": "write", "description": "Close an accounting period"},
    ]


def _config(tmp_path: Path, entries: list | None = None, text: str | None = None) -> Path:
    path = tmp_path / "methods.json"
    path.write_text(text if text is not None else json.dumps(entries or _entries(), indent=2))
    return path


def _load(methods, tmp_path: Path, entries: list | None = None, text: str | None = None):
    methods.setenv("FRAPPE_MCP_METHODS", str(_config(tmp_path, entries, text)))
    return standalone._method_settings()


def _fails(methods, tmp_path, entries=None, text=None) -> str:
    with pytest.raises(RuntimeError) as error:
        _load(methods, tmp_path, entries, text)
    return str(error.value)


# Loading the file


def test_no_file_registers_no_method_tool(methods) -> None:
    assert standalone._method_settings() == []

    assert not set(standalone._METHOD_TOOLS) & set(standalone.server._tool_manager._tools)
    assert not set(standalone._METHOD_TOOLS) & standalone._TOOLSETS["data"]


def test_a_file_registers_the_method_tools_in_the_data_toolset(methods, tmp_path) -> None:
    warnings = _load(methods, tmp_path)

    tools = standalone.server._tool_manager._tools
    assert set(standalone._METHOD_TOOLS) <= set(tools)
    assert set(standalone._METHOD_TOOLS) <= standalone._TOOLSETS["data"]
    assert tools["list_allowed_methods"].annotations.readOnlyHint is True
    assert tools["make_mapped_document"].annotations.readOnlyHint is True
    assert tools["call_allowed_method"].annotations.readOnlyHint is False
    assert warnings == [
        "methods.json: 3 lab_app entries not checked against the app source "
        "(FRAPPE_APPS_PATH is not set)"
    ]


def test_list_allowed_methods_returns_entries_without_internals(methods, tmp_path) -> None:
    _load(methods, tmp_path)

    listed = standalone.list_allowed_methods()

    assert listed["count"] == 3
    rows = {row["method"]: row for row in listed["methods"]}
    assert rows[READER]["params"]["item"] == {"type": "string", "required": True}
    assert rows[MAPPER]["target_doctype"] == "Lab Invoice"
    assert all(not key.startswith("_") for row in rows.values() for key in row)
    assert rows[WRITER]["checked_against_source"] is False


def test_the_example_config_loads(methods) -> None:
    methods.setenv("FRAPPE_MCP_METHODS", str(ROOT / "examples" / "erpnext-methods.json"))

    standalone._method_settings()

    kinds = {entry["kind"] for entry in standalone._method_config.values()}
    assert len(standalone._method_config) == 7 and kinds == {"mapper", "read"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"method": "lab_app.api.*"}, "line 10: lab_app.api.*: wildcards are not allowed"),
        ({"method": "lab_app"}, "line 10: 'lab_app' is not a dotted Python path"),
        ({"method": "lab_app.api.get price"}, "is not a dotted Python path"),
        ({"method": "lab_app.api.x;import os"}, "is not a dotted Python path"),
        ({"kind": "call"}, "line 10: lab_app.api.get_price: kind must be one of"),
        ({"extra": 1}, "unknown keys: extra"),
        ({"description": "x" * 301}, "description must be text up to 300"),
        ({"source_doctype": "Lab Order"}, "source_doctype does not apply to a read entry"),
        ({"target_doctype": "../x"}, "target_doctype must be a DocType name"),
        ({"params": {"ignore_permissions": {}}}, "params may not include ignore_permissions"),
        ({"params": {"cmd": {}}}, "params may not include cmd"),
        ({"params": {"qty": {"type": "int"}}}, "params.qty.type must be one of"),
        ({"params": {"qty": {"required": "yes"}}}, "params.qty.required must be true or false"),
        ({"params": {"qty": {"enum": []}}}, "params.qty.enum must be a list"),
        ({"params": {"qty": {"min": 1}}}, "params.qty has unknown keys: min"),
        ({"params": {"1qty": {}}}, "is not a Python identifier"),
        ({"params": []}, "params must be an object"),
    ],
)
def test_each_entry_rule_fails_at_startup_with_the_line(methods, tmp_path, change, message):
    entries = _entries()
    entries[1].update(change)

    assert message in _fails(methods, tmp_path, entries)


def test_a_write_entry_takes_no_doctypes_and_a_mapper_no_params(methods, tmp_path) -> None:
    entries = _entries()
    entries[2]["target_doctype"] = "Lab Invoice"
    entries[0]["params"] = {"source_name": {}}

    message = _fails(methods, tmp_path, entries)

    assert "target_doctype does not apply to a write entry" in message
    assert "a mapper takes only source_name" in message


@pytest.mark.parametrize(
    "method",
    [
        "frappe.client.get_list",
        "frappe.client.insert",
        "frappe.model.mapper.make_mapped_doc",
        "frappe.handler.run_doc_method",
    ],
)
def test_dispatching_methods_cannot_be_allowlisted(methods, tmp_path, method) -> None:
    message = _fails(methods, tmp_path, [{"method": method, "kind": "read"}])

    assert "can't be allowlisted" in message


def test_a_method_listed_twice_names_the_second_line(methods, tmp_path) -> None:
    entries = _entries()
    entries.append(dict(entries[2]))

    message = _fails(methods, tmp_path, entries)

    assert "line 38" in message and "is listed twice" in message


def test_file_level_problems_fail_at_startup(methods, tmp_path) -> None:
    missing_comma = '[\n{"method": "a.b"\n"kind": 1}]'
    assert "line 3: invalid JSON" in _fails(methods, tmp_path, text=missing_comma)
    assert "JSON list of 1 to 200" in _fails(methods, tmp_path, text="{}")
    assert "JSON list of 1 to 200" in _fails(methods, tmp_path, text="[]")
    assert "entry 1: needs a method" in _fails(methods, tmp_path, text='[{"kind": "read"}]')
    methods.setenv("FRAPPE_MCP_METHODS", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="cannot read missing.json"):
        standalone._method_settings()


def test_every_problem_is_reported_at_once(methods, tmp_path) -> None:
    entries = _entries()
    entries[0]["kind"] = "map"
    entries[2]["method"] = "lab_app.*"

    message = _fails(methods, tmp_path, entries)

    assert "line 3" in message and "line 33" in message


def test_main_exits_on_a_bad_file(methods, tmp_path) -> None:
    methods.setenv("FRAPPE_MCP_METHODS", str(_config(tmp_path, [{"method": "a.*"}])))
    ran = []
    methods.setattr(standalone.server, "run", lambda **kwargs: ran.append(kwargs))

    with pytest.raises(SystemExit, match="wildcards are not allowed"):
        standalone.main([])

    assert ran == []


def test_read_only_mode_keeps_the_method_tools_off(methods, tmp_path) -> None:
    methods.setenv("FRAPPE_MCP_READ_ONLY", "YES")
    methods.setenv("FRAPPE_MCP_METHODS", str(_config(tmp_path)))
    listed = []
    methods.setattr(
        standalone.server,
        "run",
        lambda **kwargs: listed.append(set(standalone.server._tool_manager._tools)),
    )

    standalone.main([])

    assert not set(standalone._METHOD_TOOLS) & listed[0]


# Checking entries against the app source

API = '''\
import frappe


@frappe.whitelist()
def get_price(item: str, qty: int = 1, mode=None, options: dict | None = None):
    """Return the price of an item."""


@frappe.whitelist(methods=["POST"])
def close_period(period: str):
    pass


@frappe.whitelist()
def flexible(**kwargs):
    pass


def helper():
    pass
'''

CONTROLLER = '''\
import frappe


@frappe.whitelist()
def make_lab_invoice(source_name, target_doc=None):
    pass
'''


def _source_app(tmp_path: Path, methods) -> Path:
    package = tmp_path / "apps" / APP / APP
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "api.py").write_text(API)
    folder = package / "lab" / "doctype" / "lab_order"
    folder.mkdir(parents=True)
    (folder / "lab_order.py").write_text(CONTROLLER)
    (folder / "lab_order.json").write_text('{"name": "Lab Order", "doctype": "DocType"}')
    methods.setenv("FRAPPE_APPS_PATH", str(tmp_path / "apps"))
    return package


def test_entries_are_checked_against_the_checkout(methods, tmp_path) -> None:
    _source_app(tmp_path, methods)

    warnings = _load(methods, tmp_path)

    assert warnings == []
    reader = standalone._method_config[READER]
    assert reader["_source"] == "lab_app/api.py:5"
    assert reader["_args"] == {"item", "qty", "mode", "options"}
    assert standalone.list_allowed_methods()["methods"][0]["checked_against_source"] is True


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"method": "lab_app.api.missing", "kind": "read"}, "is not a whitelisted module"),
        ({"method": "lab_app.api.helper", "kind": "read"}, "is not a whitelisted module"),
        ({"method": "lab_app.nowhere.fn", "kind": "read"}, "is not a whitelisted module"),
        ({"method": WRITER, "kind": "read"}, "whitelists it for POST only; a read entry"),
        (
            {"method": READER, "kind": "read", "params": {"price": {}}},
            "lab_app/api.py:5 has no argument price",
        ),
    ],
)
def test_a_mismatch_with_the_source_fails_at_startup(methods, tmp_path, entry, message) -> None:
    _source_app(tmp_path, methods)

    error = _fails(methods, tmp_path, [entry])

    assert "methods.json line 3" in error and message in error


def test_a_kwargs_function_accepts_any_listed_param(methods, tmp_path) -> None:
    _source_app(tmp_path, methods)

    _load(methods, tmp_path, [{"method": "lab_app.api.flexible", "kind": "write",
                               "params": {"anything": {}}}])

    assert standalone._method_config["lab_app.api.flexible"]["_args"] is None


def test_another_app_without_a_checkout_only_warns(methods, tmp_path) -> None:
    _source_app(tmp_path, methods)

    warnings = _load(methods, tmp_path, [{"method": "other_app.api.ping", "kind": "read"}])

    assert warnings == [
        "methods.json: 1 other_app entry not checked against the app source "
        "(no other_app checkout under FRAPPE_APPS_PATH)"
    ]


def test_app_methods_suggest_config_entries(methods, tmp_path) -> None:
    _source_app(tmp_path, methods)

    page = standalone.list_app_methods(APP, as_config=True)

    rows = {row["method"]: row for row in page["rows"]}
    assert rows[READER] == {
        "method": READER,
        "kind": "read",
        "description": "Return the price of an item.",
        "params": {
            "item": {"type": "string", "required": True},
            "qty": {"type": "integer"},
            "mode": {},
            "options": {"type": "object"},
        },
    }
    assert rows[WRITER]["kind"] == "write"
    assert rows[MAPPER] == {"method": MAPPER, "kind": "mapper", "source_doctype": "Lab Order"}
    assert "never writes it" in page["note"]
    # Every suggestion that names its kind and params is a valid entry once pasted.
    assert _load(methods, tmp_path, list(rows.values())) == []


# Refusals: nothing outside the file is ever called


@pytest.mark.parametrize(
    "method",
    ["frappe.client.get_list", "run_doc_method", "frappe.utils.now", "lab_app.api.other", None],
)
def test_unlisted_methods_are_refused_by_both_tools(methods, tmp_path, fake_site, method) -> None:
    _load(methods, tmp_path)

    with pytest.raises(ValueError, match="not in the FRAPPE_MCP_METHODS allowlist"):
        standalone.call_allowed_method(method)
    with pytest.raises(ValueError, match="not in the FRAPPE_MCP_METHODS allowlist"):
        standalone.make_mapped_document(method, "LO-1")

    assert fake_site.calls == []


def test_kind_mismatches_are_refused(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)

    with pytest.raises(ValueError, match="mapper entry; call it with make_mapped_document"):
        standalone.call_allowed_method(MAPPER, {"source_name": "LO-1"})
    with pytest.raises(ValueError, match="read entry; call it with call_allowed_method"):
        standalone.make_mapped_document(READER, "LO-1")
    with pytest.raises(ValueError, match="write entry; call it with call_allowed_method"):
        standalone.make_mapped_document(WRITER, "LO-1")

    assert fake_site.calls == []


# call_allowed_method


def test_a_read_entry_runs_with_get(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)
    fake_site.add("GET", METHOD + READER, 12.5)

    result = standalone.call_allowed_method(
        READER, {"item": "ITEM-1", "qty": 3, "options": {"a": 1}, "mode": None}
    )

    assert result == {"method": READER, "kind": "read", "result": 12.5, "truncated": False}
    (call,) = fake_site.calls
    assert call.params == {"item": "ITEM-1", "qty": 3, "options": '{"a": 1}'}


def test_a_write_entry_previews_then_posts_on_confirm(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)
    fake_site.add("POST", METHOD + WRITER, {"closed": True})

    preview = standalone.call_allowed_method(WRITER, {"period": "2026-10"})

    assert preview == {
        "preview": True,
        "action": "call_allowed_method",
        "method": WRITER,
        "kind": "write",
        "description": "Close an accounting period",
        "args": {"period": "2026-10"},
        "requires_confirmation": True,
    }
    assert fake_site.calls == []

    result = standalone.call_allowed_method(WRITER, {"period": "2026-10"}, confirm=True)

    assert result["result"] == {"closed": True}
    (call,) = fake_site.calls
    assert call.method == "POST" and call.json == {"period": "2026-10"}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"qty": 1}, "needs item"),
        ({"item": "X", "price": 1}, "takes no argument price; allowed: item, qty, mode, options"),
        ({"item": 5}, "item must be of type string"),
        ({"item": "X", "qty": "3"}, "qty must be of type integer"),
        ({"item": "X", "qty": True}, "qty must be of type integer"),
        ({"item": "X", "mode": "list"}, "mode must be one of"),
        ({"item": "X", "mode": ["net"]}, "mode must be a scalar value"),
        ({"item": "X", "options": []}, "options must be of type object"),
        ({"item": "X", "ignore_permissions": True}, "ignore_permissions is never sent"),
        ({"item": "X", "cmd": "frappe.client.delete"}, "cmd is never sent"),
        ({"item": "x" * 10_001}, "longer than 10000 characters"),
        ({"bad name": 1}, "Invalid argument name"),
        (["item"], "args must be an object"),
    ],
)
def test_arguments_must_fit_the_params(methods, tmp_path, fake_site, args, message) -> None:
    _load(methods, tmp_path)

    with pytest.raises(ValueError, match=message):
        standalone.call_allowed_method(READER, args)

    assert fake_site.calls == []


def test_without_params_only_scalars_and_known_names_pass(methods, tmp_path, fake_site) -> None:
    _source_app(tmp_path, methods)
    _load(methods, tmp_path)

    with pytest.raises(ValueError, match="must be a scalar value; add params"):
        standalone.call_allowed_method(WRITER, {"period": {"from": "a"}})
    with pytest.raises(ValueError, match="takes no argument company"):
        standalone.call_allowed_method(WRITER, {"company": "X"})
    preview = standalone.call_allowed_method(WRITER, {"period": "2026-10"})

    assert preview["preview"] is True and fake_site.calls == []


def test_a_list_result_is_paged_and_long_values_cut(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)
    rows = [{"n": index, "note": "x" * 3000 if index == 2 else ""} for index in range(10)]
    fake_site.add("GET", METHOD + READER, rows)

    result = standalone.call_allowed_method(READER, {"item": "X"}, limit=3, offset=1)

    assert [row["n"] for row in result["result"]] == [1, 2, 3]
    assert len(result["result"][1]["note"]) == standalone._METHOD_RESULT_TEXT + 3
    assert result["total_rows"] == 10 and result["has_more"] is True
    assert result["truncated"] is True


def test_a_huge_result_comes_back_as_cut_text(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)
    fake_site.add("GET", METHOD + READER, {f"k{index}": "x" * 1900 for index in range(100)})

    result = standalone.call_allowed_method(READER, {"item": "X"})

    assert result["result"] is None
    assert len(result["result_text"]) == standalone._METHOD_RESULT_MAX_CHARS
    assert result["truncated"] is True


def test_limit_and_offset_are_checked(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)

    with pytest.raises(ValueError, match="limit must be between 1 and 500"):
        standalone.call_allowed_method(READER, {"item": "X"}, limit=0)
    with pytest.raises(ValueError, match="offset"):
        standalone.call_allowed_method(READER, {"item": "X"}, offset=-1)


# make_mapped_document

INVOICE_FIELDS = [
    {"fieldname": "customer", "fieldtype": "Link", "options": "Customer"},
    {"fieldname": "customer_name", "fieldtype": "Data", "read_only": 1},
    {"fieldname": "grand_total", "fieldtype": "Currency", "read_only": 1},
    {"fieldname": "remarks", "fieldtype": "Small Text"},
    {"fieldname": "items", "fieldtype": "Table", "options": "Lab Invoice Item"},
]
ITEM_FIELDS = [
    {"fieldname": "item_code", "fieldtype": "Link", "options": "Item"},
    {"fieldname": "qty", "fieldtype": "Float"},
    {"fieldname": "lab_order", "fieldtype": "Link", "options": "Lab Order", "read_only": 1},
    {"fieldname": "lab_order_item", "fieldtype": "Data", "hidden": 1, "read_only": 1},
]


def _mapped(rows: int = 2) -> dict:
    return {
        "doctype": "Lab Invoice",
        "name": "new-lab-invoice-abc",
        "__islocal": 1,
        "__unsaved": 1,
        "docstatus": 0,
        "owner": "Administrator",
        "customer": "CUST-1",
        "customer_name": "Example Customer",
        "grand_total": 30.0,
        "remarks": "",
        "items": [
            {
                "doctype": "Lab Invoice Item",
                "name": f"new-lab-invoice-item-{index}",
                "__islocal": 1,
                "parent": "new-lab-invoice-abc",
                "parenttype": "Lab Invoice",
                "parentfield": "items",
                "idx": index + 1,
                "docstatus": 0,
                "item_code": "ITEM-1",
                "qty": 3.0,
                "lab_order": "LO-1",
                "lab_order_item": f"row-{index}",
            }
            for index in range(rows)
        ],
    }


def _invoice_site(fake_site) -> None:
    fake_site.add_doctype("Lab Invoice", INVOICE_FIELDS)
    fake_site.add_doctype("Lab Invoice Item", ITEM_FIELDS, istable=1)


def test_a_mapper_returns_a_clean_draft_and_saves_nothing(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)
    _invoice_site(fake_site)
    fake_site.add("GET", standalone._MAKE_MAPPED_DOC, _mapped())

    draft = standalone.make_mapped_document(MAPPER, "LO-1")

    assert draft["doctype"] == "Lab Invoice"
    assert draft["source_doctype"] == "Lab Order" and draft["source_name"] == "LO-1"
    assert draft["values"] == {
        "customer": "CUST-1",
        "items": [
            {"item_code": "ITEM-1", "qty": 3.0, "lab_order": "LO-1", "lab_order_item": "row-0"},
            {"item_code": "ITEM-1", "qty": 3.0, "lab_order": "LO-1", "lab_order_item": "row-1"},
        ],
    }
    assert draft["read_only"] == {"customer_name": "Example Customer", "grand_total": 30.0}
    (call,) = fake_site.calls_to("GET", standalone._MAKE_MAPPED_DOC)
    assert call.params == {"method": MAPPER, "source_name": "LO-1"}
    assert not [call for call in fake_site.calls if call.method != "GET"]


def test_a_mapped_draft_passes_create_document(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)
    _invoice_site(fake_site)
    fake_site.add("GET", standalone._MAKE_MAPPED_DOC, _mapped())
    draft = standalone.make_mapped_document(MAPPER, "LO-1")

    preview = standalone.create_document(draft["doctype"], draft["values"])

    assert preview["preview"] is True and preview["fields"] == ["customer", "items"]


def test_a_mapper_cuts_long_child_tables_and_says_so(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)
    _invoice_site(fake_site)
    fake_site.add("GET", standalone._MAKE_MAPPED_DOC, _mapped(rows=205))

    draft = standalone.make_mapped_document(MAPPER, "LO-1")

    assert len(draft["values"]["items"]) == standalone._MAPPED_CHILD_ROWS
    assert draft["_truncated_child_tables"] == ["items"]
    assert "can't be created as it is" in draft["note"]


def test_a_mapper_returning_another_doctype_is_an_error(methods, tmp_path, fake_site) -> None:
    _load(methods, tmp_path)
    fake_site.add("GET", standalone._MAKE_MAPPED_DOC, {"doctype": "Lab Receipt"})

    with pytest.raises(RuntimeError, match="returned a Lab Receipt, not the listed Lab Invoice"):
        standalone.make_mapped_document(MAPPER, "LO-1")


@pytest.mark.parametrize("source_name", ["", "  ", "x" * 141, None])
def test_a_mapper_needs_a_source_name(methods, tmp_path, fake_site, source_name) -> None:
    _load(methods, tmp_path)

    with pytest.raises(ValueError, match="source_name"):
        standalone.make_mapped_document(MAPPER, source_name)


def test_a_read_entry_with_a_target_returns_a_draft(methods, tmp_path, fake_site) -> None:
    entries = _entries()
    entries[1]["target_doctype"] = "Lab Invoice"
    _load(methods, tmp_path, entries)
    _invoice_site(fake_site)
    fake_site.add("GET", METHOD + READER, _mapped(rows=1))

    result = standalone.call_allowed_method(READER, {"item": "X"})

    assert result["doctype"] == "Lab Invoice"
    assert result["values"]["customer"] == "CUST-1"
    assert "__islocal" not in result["values"]["items"][0]
