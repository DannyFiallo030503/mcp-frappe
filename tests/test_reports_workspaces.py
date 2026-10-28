"""Report Builder reports and Workspaces."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from frappe_mcp import standalone

MODIFIED = "2026-10-20 10:00:00.000001"
SAMPLE = "/api/method/frappe.desk.reportview.get_list"


def _in_filter(call) -> list[str]:
    return json.loads(call.params["filters"])[0][2]


def _rows(records: dict[str, dict[str, Any]]):
    return lambda call: [records[name] for name in _in_filter(call) if name in records]


@pytest.fixture
def site(fake_site):
    fake_site.add_doctype(
        "Lab Sample",
        [
            {"fieldname": "title", "fieldtype": "Data"},
            {"fieldname": "status", "fieldtype": "Select", "options": "Open\nDone"},
            {"fieldname": "weight", "fieldtype": "Float"},
            {"fieldname": "details", "fieldtype": "Section Break"},
            {"fieldname": "readings", "fieldtype": "Table", "options": "Lab Reading"},
        ],
    )
    fake_site.add_doctype(
        "Lab Reading", [{"fieldname": "value", "fieldtype": "Float"}], istable=1
    )
    fake_site.add_doctype("Lab Note", [{"fieldname": "text", "fieldtype": "Data"}])
    fake_site.add(
        "GET",
        standalone._resource("Module Def"),
        lambda call: [
            {"name": name, "app_name": app, "custom": 0}
            for name, app in (("Lab", "lab_app"), ("Core", "frappe"))
            if json.loads(call.params["filters"])["name"] == name
        ],
    )
    fake_site.reports = {}
    fake_site.workspaces = {}
    fake_site.add(
        "GET",
        standalone._resource("Report"),
        lambda call: [
            {"name": row["name"]}
            for row in fake_site.reports.values()
            if json.loads(call.params["filters"]) == {"name": row["name"]}
        ],
    )
    fake_site.add(
        "GET",
        standalone._resource("Workspace"),
        lambda call: [
            {"name": name}
            for name in fake_site.workspaces
            if json.loads(call.params["filters"]) == {"name": name}
        ],
    )
    fake_site.add("POST", standalone._resource("Report"), {"name": "r", "modified": MODIFIED})
    fake_site.add("POST", standalone._resource("Workspace"), {"name": "w", "modified": MODIFIED})
    fake_site.add("GET", SAMPLE, [{"title": "A"}])
    return fake_site


def _put_report(site, **values) -> dict[str, Any]:
    row = {
        "name": "Open Samples",
        "report_name": "Open Samples",
        "ref_doctype": "Lab Sample",
        "report_type": "Report Builder",
        "is_standard": "No",
        "json": "{}",
        "modified": MODIFIED,
        **values,
    }
    site.reports[row["name"]] = row
    site.add("GET", standalone._resource("Report", row["name"]), row)
    site.add("PUT", standalone._resource("Report", row["name"]), row)
    return row


def _report(**kwargs) -> dict[str, Any]:
    args = {"name": "Open Samples", "ref_doctype": "Lab Sample", "columns": ["name", "title"],
            **kwargs}
    return standalone.upsert_report(**args)


def _saved(site, method: str = "POST") -> dict[str, Any]:
    (call,) = site.calls_to(method)
    return call.json["data"]


# Reports


def test_report_preview_builds_report_builder_json(site) -> None:
    preview = _report(
        columns=["title", "Lab Reading.value"],
        filters={"status": "Open", "weight": [">", 2]},
        order_by="weight desc",
        add_totals_row=True,
    )

    assert preview["preview"] is True and preview["exists"] is False
    assert preview["record"]["report_type"] == "Report Builder"
    assert preview["record"]["is_standard"] == "No"
    assert preview["settings"] == {
        "fields": [["title", "Lab Sample"], ["value", "Lab Reading"]],
        "filters": [
            ["Lab Sample", "status", "=", "Open"],
            ["Lab Sample", "weight", ">", 2],
        ],
        "order_by": "`tabLab Sample`.`weight` desc",
        "add_totals_row": 1,
        "page_length": 20,
        "group_by": None,
    }
    assert site.calls_to("POST") == []


def test_report_confirm_saves_json_and_runs_a_bounded_sample(site) -> None:
    site.add("GET", SAMPLE, [{"title": f"T{i}"} for i in range(50)])

    result = _report(filters=[["status", "=", "Open"]], confirm=True)

    data = _saved(site)
    assert data["doctype"] == "Report" and data["report_name"] == "Open Samples"
    assert data["report_type"] == "Report Builder"
    settings = json.loads(data["json"])
    assert settings["fields"] == [["name", "Lab Sample"], ["title", "Lab Sample"]]
    assert settings["filters"] == [["Lab Sample", "status", "=", "Open"]]
    (sample_call,) = site.calls_to("GET", SAMPLE)
    assert sample_call.params["doctype"] == "Lab Sample"
    assert json.loads(sample_call.params["fields"]) == [
        "`tabLab Sample`.`name`", "`tabLab Sample`.`title`"
    ]
    assert json.loads(sample_call.params["filters"]) == settings["filters"]
    assert sample_call.params["page_length"] <= standalone._REPORT_SAMPLE_ROWS + 1
    assert result["saved"] is True and result["created"] is True
    assert len(result["sample"]["rows"]) == standalone._REPORT_SAMPLE_ROWS
    assert result["sample"]["truncated"] is True


def test_report_sample_failure_does_not_fail_the_save(site) -> None:
    site.add("GET", SAMPLE, standalone.FrappeError("Frappe HTTP 417: Field not permitted"))

    result = _report(confirm=True)

    assert result["saved"] is True
    assert "Field not permitted" in result["sample"]["error"]


@pytest.mark.parametrize(
    ("columns", "message"),
    [
        (["missing"], "Unknown field for Lab Sample: missing"),
        (["details"], "not a column"),
        (["readings"], "not a column"),
        (["Lab Note.text"], "Lab Note is not a child table of Lab Sample"),
        (["Lab Reading.nope"], "Unknown field for Lab Reading: nope"),
        (["title", "title"], "Duplicate column"),
        ([], "columns must be a list of 1-"),
        ("title", "columns must be a list of 1-"),
    ],
)
def test_report_refuses_bad_columns(site, columns, message) -> None:
    with pytest.raises(ValueError, match=message):
        _report(columns=columns)


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        ({"missing": 1}, "Unknown field for Lab Sample: missing"),
        ([["status", "regexp", "x"]], "Unsupported filter operator"),
        ([["Lab Note", "text", "=", "x"]], "Lab Note is not a child table"),
    ],
)
def test_report_filters_reuse_list_validation(site, filters, message) -> None:
    with pytest.raises(ValueError, match=message):
        _report(filters=filters)


def test_report_child_filter_keeps_its_doctype(site) -> None:
    preview = _report(filters=[["Lab Reading", "value", ">", 1]])

    assert preview["settings"]["filters"] == [["Lab Reading", "value", ">", 1]]


@pytest.mark.parametrize(
    ("order_by", "expected"),
    [
        ("title", "`tabLab Sample`.`title` asc"),
        ("modified desc, name", "`tabLab Sample`.`modified` desc, `tabLab Sample`.`name` asc"),
    ],
)
def test_report_order_by_is_rebuilt_from_fieldnames(site, order_by, expected) -> None:
    assert _report(order_by=order_by)["settings"]["order_by"] == expected


@pytest.mark.parametrize(
    ("order_by", "message"),
    [
        ("missing desc", "Unknown field"),
        ("title; drop table", "order_by"),
        ("title sideways", "order_by"),
        ("`tabLab Sample`.`title` desc", "order_by"),
        ("readings", "not a column"),
    ],
)
def test_report_refuses_bad_order_by(site, order_by, message) -> None:
    with pytest.raises(ValueError, match=message):
        _report(order_by=order_by)


def test_report_group_by_saves_frappe_shape(site) -> None:
    preview = _report(
        columns=["status"],
        group_by={"field": "status", "aggregate_function": "sum", "aggregate_on": "weight"},
    )

    settings = preview["settings"]
    assert settings["fields"] == [["status", "Lab Sample"], ["_aggregate_column", "Lab Sample"]]
    assert settings["group_by"] == {
        "group_by": "`tabLab Sample`.`status`",
        "aggregate_function": "sum",
        "aggregate_on": "`tabLab Sample`.`weight`",
    }
    assert settings["order_by"] == "_aggregate_column desc"


def test_report_group_by_sample_passes_aggregate_args(site) -> None:
    _report(columns=["status"], group_by={"field": "status", "aggregate_function": "count"},
            confirm=True)

    (call,) = site.calls_to("GET", SAMPLE)
    assert json.loads(call.params["fields"]) == ["`tabLab Sample`.`status`"]
    assert call.params["group_by"] == "`tabLab Sample`.`status`"
    assert call.params["aggregate_function"] == "count"
    assert call.params["aggregate_on_field"] == "name"
    assert call.params["aggregate_on_doctype"] == "Lab Sample"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"group_by": {"field": "status", "aggregate_function": "max"}}, "aggregate_function"),
        ({"group_by": {"field": "status", "aggregate_function": "sum"}}, "aggregate_on"),
        ({"group_by": {"field": "status", "aggregate_function": "sum",
                       "aggregate_on": "title"}}, "numeric"),
        ({"group_by": {"field": "status", "aggregate_function": "count", "x": 1}}, "Unknown"),
        ({"group_by": {"field": "missing", "aggregate_function": "count"}}, "Unknown field"),
        ({"columns": ["title"], "group_by": {"field": "status", "aggregate_function": "count"}},
         "columns must be exactly"),
        ({"order_by": "title", "group_by": {"field": "status", "aggregate_function": "count"}},
         "order_by"),
    ],
)
def test_report_refuses_bad_group_by(site, kwargs, message) -> None:
    args = {"columns": ["status"], **kwargs}
    with pytest.raises(ValueError, match=message):
        _report(**args)


@pytest.mark.parametrize("doctype", ["Lab Reading"])
def test_report_refuses_child_table_ref_doctype(site, doctype) -> None:
    with pytest.raises(ValueError, match="child table"):
        _report(ref_doctype=doctype, columns=["value"])


@pytest.mark.parametrize("report_type", ["Query Report", "Script Report", "Custom Report"])
def test_report_type_cannot_be_changed(site, report_type) -> None:
    _put_report(site, report_type=report_type)

    with pytest.raises(ValueError, match="Report Builder"):
        _report()


def test_report_update_needs_matching_modified_and_shows_changes(site) -> None:
    _put_report(site, json=json.dumps({"fields": [["name", "Lab Sample"]], "filters": []}))

    preview = _report()
    assert preview["exists"] is True and preview["modified"] == MODIFIED
    assert preview["changes"]["fields"] == {
        "from": [["name", "Lab Sample"]],
        "to": [["name", "Lab Sample"], ["title", "Lab Sample"]],
    }
    assert "filters" not in preview["changes"]

    with pytest.raises(ValueError, match="Document changed since preview"):
        _report(confirm=True)
    with pytest.raises(ValueError, match="Document changed since preview"):
        _report(confirm=True, expected_modified="2020-01-01")

    _report(confirm=True, expected_modified=MODIFIED)
    data = _saved(site, "PUT")
    assert data["modified"] == MODIFIED
    assert "report_name" not in data


def test_report_keeps_unmanaged_settings_on_update(site) -> None:
    _put_report(site, json=json.dumps({"column_widths": {"title": 200}, "chart_args": {"x": 1}}))

    _report(confirm=True, expected_modified=MODIFIED)

    settings = json.loads(_saved(site, "PUT")["json"])
    assert settings["column_widths"] == {"title": 200}
    assert settings["chart_args"] == {"x": 1}


def test_report_refuses_making_a_standard_report_site_only(site) -> None:
    _put_report(site, is_standard="Yes", module="Lab")

    with pytest.raises(ValueError, match="standard"):
        _report()


def test_standard_report_lists_app_files(site, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path))
    (tmp_path / "lab_app" / "lab_app" / "lab").mkdir(parents=True)
    (tmp_path / "lab_app" / "lab_app" / "modules.txt").write_text("Lab\n")

    preview = _report(is_standard=True, module="Lab")

    assert preview["record"]["is_standard"] == "Yes"
    assert preview["record"]["module"] == "Lab"
    files = preview["app_files"]["files"]
    assert any(path.endswith("lab/report/open_samples/open_samples.json") for path in files)
    assert any(path.endswith("lab/report/open_samples/__init__.py") for path in files)
    assert preview["requires_developer_mode"] is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"is_standard": True}, "needs module"),
        ({"is_standard": True, "module": "Core"}, "protected app"),
        ({"module": "Lab"}, "standard reports only"),
        ({"is_standard": True, "module": "Nope"}, "Unknown module"),
    ],
)
def test_report_standard_module_rules(site, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        _report(**kwargs)


def test_report_reconciles_after_reload(site, monkeypatch) -> None:
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda *a, **k: None)
    sent = {}

    def disconnect(call):
        sent.update(call.json["data"])
        _put_report(site, **{key: value for key, value in sent.items() if key != "doctype"})
        raise httpx.RemoteProtocolError("reload")

    site.add("POST", standalone._resource("Report"), disconnect)

    result = _report(confirm=True)

    assert result["saved"] is True and result["reconciled_after_server_reload"] is True
    assert len(site.calls_to("POST")) == 1


# Workspaces


TARGETS = {
    "DocType": {
        "Lab Sample": {"name": "Lab Sample", "istable": 0},
        "Lab Reading": {"name": "Lab Reading", "istable": 1},
    },
    "Report": {
        "Open Samples": {"name": "Open Samples", "report_type": "Report Builder",
                         "ref_doctype": "Lab Sample"},
        "Sample Stats": {"name": "Sample Stats", "report_type": "Script Report",
                         "ref_doctype": "Lab Sample"},
    },
    "Page": {"lab-console": {"name": "lab-console"}},
    "Number Card": {"Open Samples Count": {"name": "Open Samples Count"}},
}


@pytest.fixture
def ws_site(site):
    for doctype, records in TARGETS.items():
        site.add(
            "GET",
            standalone._resource(doctype),
            _rows(records),
            params=lambda params: isinstance(json.loads((params or {}).get("filters", "{}")), list),
        )
    return site


def _put_workspace(site, **values) -> dict[str, Any]:
    row = {
        "name": "Lab",
        "label": "Lab",
        "title": "Lab",
        "module": "Lab",
        "public": 1,
        "for_user": "",
        "content": "[]",
        "shortcuts": [],
        "links": [],
        "number_cards": [],
        "charts": [],
        "quick_lists": [],
        "custom_blocks": [],
        "modified": MODIFIED,
        **values,
    }
    site.workspaces[row["name"]] = row
    site.add("GET", standalone._resource("Workspace", row["name"]), row)
    site.add("PUT", standalone._resource("Workspace", row["name"]), row)
    return row


SHORTCUTS = [
    {"label": "Samples", "type": "DocType", "link_to": "Lab Sample", "doc_view": "List",
     "color": "Blue"},
    {"label": "Open Samples", "type": "Report", "link_to": "Open Samples"},
]
CARDS = [
    {"label": "Masters", "links": [{"label": "Sample", "type": "DocType",
                                    "link_to": "Lab Sample"}]},
    {"label": "Reports", "links": [
        {"label": "Open Samples", "type": "Report", "link_to": "Open Samples"},
        {"label": "Stats", "type": "Report", "link_to": "Sample Stats"},
        {"label": "Console", "type": "Page", "link_to": "lab-console"},
    ]},
]


def _workspace(**kwargs) -> dict[str, Any]:
    args = {"name": "Lab", "module": "Lab", "shortcuts": SHORTCUTS, **kwargs}
    return standalone.upsert_workspace(**args)


def _blocks(content: str) -> list[tuple[str, str | None]]:
    names = {"shortcut": "shortcut_name", "card": "card_name",
             "number_card": "number_card_name"}
    return [
        (block["type"], block["data"].get(names.get(block["type"], "")))
        for block in json.loads(content)
    ]


def test_workspace_content_places_exactly_the_given_items_in_order(ws_site) -> None:
    _workspace(link_cards=CARDS, number_cards=["Open Samples Count"], confirm=True)

    data = _saved(ws_site)
    assert data["doctype"] == "Workspace"
    assert data["label"] == data["title"] == "Lab"
    assert data["public"] == 1 and data["for_user"] == "" and data["type"] == "Workspace"
    assert "custom_blocks" not in data
    blocks = [block for block in _blocks(data["content"]) if block[0] != "spacer"]
    assert blocks == [
        ("number_card", "Open Samples Count"),
        ("shortcut", "Samples"),
        ("shortcut", "Open Samples"),
        ("card", "Masters"),
        ("card", "Reports"),
    ]
    ids = [block["id"] for block in json.loads(data["content"])]
    assert len(set(ids)) == len(ids)
    assert [row["label"] for row in data["shortcuts"]] == ["Samples", "Open Samples"]
    assert data["shortcuts"][0] == {"label": "Samples", "type": "DocType",
                                    "link_to": "Lab Sample", "doc_view": "List",
                                    "color": "Blue"}
    assert [(row["type"], row["label"]) for row in data["links"]] == [
        ("Card Break", "Masters"), ("Link", "Sample"),
        ("Card Break", "Reports"), ("Link", "Open Samples"), ("Link", "Stats"),
        ("Link", "Console"),
    ]
    assert data["links"][0]["link_count"] == 1 and data["links"][2]["link_count"] == 3
    by_label = {row["label"]: row for row in data["links"]}
    assert by_label["Open Samples"]["is_query_report"] == 0
    assert by_label["Stats"]["is_query_report"] == 1
    assert by_label["Console"]["link_type"] == "Page"
    assert data["number_cards"] == [{"number_card_name": "Open Samples Count",
                                     "label": "Open Samples Count"}]


def test_workspace_content_is_deterministic(ws_site) -> None:
    first = _workspace(link_cards=CARDS)["content"]
    second = _workspace(link_cards=CARDS)["content"]

    assert first == second


@pytest.mark.parametrize(
    ("shortcuts", "message"),
    [
        ([{"label": "Web", "type": "URL", "link_to": "https://example.localhost"}], "type"),
        ([{"label": "X", "type": "Dashboard", "link_to": "Lab"}], "type"),
        ([{"label": "X", "type": "DocType", "link_to": "Missing"}], "Unknown DocType"),
        ([{"label": "X", "type": "DocType", "link_to": "Lab Reading"}], "child table"),
        ([{"label": "X", "type": "Report", "link_to": "Missing"}], "Unknown Report"),
        ([{"label": "X", "type": "Page", "link_to": "missing"}], "Unknown Page"),
        ([{"label": "X", "type": "DocType", "link_to": "Lab Sample", "url": "x"}], "Unknown"),
        ([{"label": "X", "type": "DocType", "link_to": "Lab Sample", "doc_view": "Kanban"}],
         "doc_view"),
        ([{"label": "X", "type": "Report", "link_to": "Open Samples", "doc_view": "List"}],
         "doc_view"),
        ([{"label": "X", "type": "DocType", "link_to": "Lab Sample", "color": "red;"}],
         "color"),
        ([{"label": "", "type": "DocType", "link_to": "Lab Sample"}], "label"),
        ([{"label": "X", "type": "DocType", "link_to": "Lab Sample"},
          {"label": "X", "type": "Page", "link_to": "lab-console"}], "Duplicate"),
    ],
)
def test_workspace_refuses_bad_shortcuts(ws_site, shortcuts, message) -> None:
    with pytest.raises(ValueError, match=message):
        _workspace(shortcuts=shortcuts)


@pytest.mark.parametrize(
    ("cards", "message"),
    [
        ([{"label": "C", "links": [{"label": "W", "type": "URL", "link_to": "x"}]}], "type"),
        ([{"label": "C", "links": [{"label": "R", "type": "Report", "link_to": "Missing"}]}],
         "Unknown Report"),
        ([{"label": "C", "links": []}], "links"),
        ([{"label": "C", "links": [{"label": "S", "type": "DocType",
                                    "link_to": "Lab Sample"}], "icon": "x"}], "Unknown"),
        ([{"label": "C", "links": [{"label": "S", "type": "DocType", "link_to": "Lab Sample"}]},
          {"label": "C", "links": [{"label": "S", "type": "DocType",
                                    "link_to": "Lab Sample"}]}], "Duplicate"),
    ],
)
def test_workspace_refuses_bad_link_cards(ws_site, cards, message) -> None:
    with pytest.raises(ValueError, match=message):
        _workspace(link_cards=cards)


def test_workspace_refuses_unknown_number_cards(ws_site) -> None:
    with pytest.raises(ValueError, match="Unknown Number Card"):
        _workspace(number_cards=["Missing"])


@pytest.mark.parametrize(
    ("module", "message"),
    [("Core", "protected app"), ("Nope", "Unknown module")],
)
def test_workspace_refuses_protected_or_unknown_modules(ws_site, module, message) -> None:
    with pytest.raises(ValueError, match=message):
        _workspace(module=module)


def test_workspace_has_no_custom_block_parameter() -> None:
    tool = standalone.server._tool_manager._tools["upsert_workspace"]

    assert "custom_blocks" not in tool.parameters["properties"]


@pytest.mark.parametrize("block", ["custom_block", "chart", "quick_list", "onboarding"])
def test_workspace_refuses_replacing_blocks_it_cannot_rebuild(ws_site, block) -> None:
    _put_workspace(ws_site, content=json.dumps([{"id": "a", "type": block, "data": {}}]))

    with pytest.raises(ValueError, match=block):
        _workspace()


def test_workspace_refuses_private_workspace(ws_site) -> None:
    _put_workspace(ws_site, public=0, for_user="someone@example.localhost")

    with pytest.raises(ValueError, match="private"):
        _workspace()


def test_workspace_refuses_existing_workspace_of_protected_app(ws_site) -> None:
    _put_workspace(ws_site, module="Core")

    with pytest.raises(ValueError, match="protected app"):
        _workspace()


def test_workspace_preview_on_update_shows_added_removed_and_moved(ws_site) -> None:
    content = [
        {"id": "s1", "type": "shortcut", "data": {"shortcut_name": "Open Samples", "col": 3}},
        {"id": "s2", "type": "shortcut", "data": {"shortcut_name": "Samples", "col": 3}},
        {"id": "s3", "type": "shortcut", "data": {"shortcut_name": "Old", "col": 3}},
        {"id": "h1", "type": "header", "data": {"text": "Hello", "col": 12}},
        {"id": "c1", "type": "card", "data": {"card_name": "Masters", "col": 4}},
    ]
    _put_workspace(
        ws_site,
        content=json.dumps(content),
        shortcuts=[
            {"name": "row1", "label": "Open Samples", "type": "Report", "link_to": "Open Samples",
             "stats_filter": "[]"},
            {"name": "row2", "label": "Samples", "type": "DocType", "link_to": "Lab Sample",
             "icon": "flask"},
            {"name": "row3", "label": "Old", "type": "DocType", "link_to": "Lab Sample"},
        ],
        links=[
            {"type": "Card Break", "label": "Masters", "link_count": 1},
            {"type": "Link", "label": "Sample", "link_type": "DocType", "link_to": "Lab Note",
             "description": "Kept"},
        ],
    )

    preview = _workspace(link_cards=CARDS)

    changes = preview["changes"]
    assert changes["shortcuts"] == {"added": [], "removed": ["Old"],
                                    "moved": ["Samples", "Open Samples"]}
    assert changes["link_cards"] == {"added": ["Reports"], "removed": [], "moved": [],
                                     "changed": ["Masters"]}
    assert changes["number_cards"] == {"added": [], "removed": [], "moved": []}
    assert preview["modified"] == MODIFIED
    assert any("header" in warning for warning in preview["warnings"])

    _workspace(link_cards=CARDS, confirm=True, expected_modified=MODIFIED)
    data = _saved(ws_site, "PUT")
    assert data["modified"] == MODIFIED
    samples = next(row for row in data["shortcuts"] if row["label"] == "Samples")
    assert samples["icon"] == "flask" and "name" not in samples
    sample_link = next(row for row in data["links"] if row["label"] == "Sample")
    assert sample_link["description"] == "Kept"
    assert sample_link["link_to"] == "Lab Sample"


def test_workspace_confirm_needs_matching_modified(ws_site) -> None:
    _put_workspace(ws_site)

    with pytest.raises(ValueError, match="Document changed since preview"):
        _workspace(confirm=True)


def test_standard_workspace_preview_lists_app_files(ws_site) -> None:
    preview = _workspace()

    assert preview["app_files"]["files"][-1].endswith("lab/workspace/lab/lab.json")
    assert preview["requires_developer_mode"] is True


def test_get_workspace_reads_layout(ws_site) -> None:
    content = [
        {"id": "n1", "type": "number_card", "data": {"number_card_name": "Open Samples Count"}},
        {"id": "s1", "type": "shortcut", "data": {"shortcut_name": "Samples", "col": 3}},
        {"id": "x1", "type": "custom_block", "data": {"custom_block_name": "Banner"}},
        {"id": "c1", "type": "card", "data": {"card_name": "Masters", "col": 4}},
    ]
    _put_workspace(
        ws_site,
        content=json.dumps(content),
        shortcuts=[{"label": "Samples", "type": "DocType", "link_to": "Lab Sample",
                    "doc_view": "List", "color": "Blue", "stats_filter": "[]"}],
        links=[
            {"type": "Card Break", "label": "Masters", "link_count": 1},
            {"type": "Link", "label": "Sample", "link_type": "DocType", "link_to": "Lab Sample"},
        ],
        number_cards=[{"number_card_name": "Open Samples Count", "label": "Open Samples Count"}],
        custom_blocks=[{"custom_block_name": "Banner", "label": "Banner"}],
    )

    result = standalone.get_workspace("Lab")

    assert result["module"] == "Lab" and result["public"] is True
    assert result["shortcuts"] == [{"label": "Samples", "type": "DocType",
                                    "link_to": "Lab Sample", "doc_view": "List",
                                    "color": "Blue"}]
    assert result["link_cards"] == [{"label": "Masters", "links": [
        {"label": "Sample", "type": "DocType", "link_to": "Lab Sample"}]}]
    assert result["number_cards"] == ["Open Samples Count"]
    assert result["layout"] == [
        {"block": "number_card", "name": "Open Samples Count"},
        {"block": "shortcut", "name": "Samples"},
        {"block": "custom_block", "name": "Banner"},
        {"block": "card", "name": "Masters"},
    ]
    assert result["unmanaged_blocks"] == ["custom_block"]


def test_workspace_route_conflict_is_explained(ws_site) -> None:
    ws_site.add(
        "POST",
        standalone._resource("Workspace"),
        standalone.FrappeError("Frappe HTTP 409: Name already taken", "NameError"),
    )

    with pytest.raises(ValueError, match="same Desk route"):
        _workspace(confirm=True)
