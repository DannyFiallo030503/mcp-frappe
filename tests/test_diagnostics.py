"""`list_pending_patches`, `get_recent_errors`, and `list_app_methods`: developer diagnostics."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from frappe_mcp import standalone

APP = "lab_app"
PATCH_LOG = standalone._resource("Patch Log")
ERROR_LOG = standalone._resource("Error Log")


def _app(tmp_path: Path, monkeypatch) -> Path:
    """Create `apps/<APP>/<APP>/` and point FRAPPE_APPS_PATH at `apps`; return the package."""
    package = tmp_path / "apps" / APP / APP
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path / "apps"))
    return package


def _patch_log(fake_site, rows: list[dict]) -> None:
    """Answer Patch Log reads a page at a time, as `/api/resource` does."""

    def page(call):
        start = int(call.params["limit_start"])
        return rows[start : start + int(call.params["limit_page_length"])]

    fake_site.add("GET", PATCH_LOG, page)


# list_pending_patches

PATCHES = """\
[pre_model_sync]
# a comment
lab_app.patches.v1_0.first
lab_app.patches.v1_0.rerun #2026-01-02

; another comment
execute:frappe.db.set_default("lab", 1)

[post_model_sync]
lab_app.patches.v1_0.after_sync
finally:lab_app.patches.v1_0.last
"""


def test_pending_patches_follow_the_file_order_with_sections_and_lines(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _app(tmp_path, monkeypatch)
    (package / "patches.txt").write_text(PATCHES)
    _patch_log(
        fake_site,
        [
            {"patch": "lab_app.patches.v1_0.first", "skipped": 0},
            {"patch": 'execute:frappe.db.set_default("lab", 1)', "skipped": 0},
            {"patch": "lab_app.patches.v1_0.last", "skipped": 0},
            {"patch": "lab_app.patches.v1_0.after_sync", "skipped": 1},
            {"patch": "lab_app.patches.v1_0.rerun", "skipped": 0},
            {"patch": "lab_app.patches.v0_9.removed", "skipped": 0},
            {"patch": "other_app.patches.v1_0.first", "skipped": 0},
        ],
    )

    result = standalone.list_pending_patches(APP)

    assert result["format"] == "sections"
    assert result["applied_count"] == 3
    assert result["pending"] == [
        {"patch": "lab_app.patches.v1_0.rerun #2026-01-02", "section": "pre_model_sync",
         "line": 4},
        {"patch": "lab_app.patches.v1_0.after_sync", "section": "post_model_sync", "line": 10,
         "skipped": True},
    ]
    assert result["pending_count"] == 2
    # Only rows that name this app's package; the old `rerun` line ran without its `#` marker.
    assert result["unknown_in_log"] == ["lab_app.patches.v0_9.removed",
                                        "lab_app.patches.v1_0.rerun"]
    assert all(call.params["fields"] == '["patch","skipped"]'
               for call in fake_site.calls_to("GET", PATCH_LOG))


def test_pending_patches_read_the_old_plain_format_as_pre_model_sync(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _app(tmp_path, monkeypatch)
    (package / "patches.txt").write_text(
        "lab_app.patches.one\n#lab_app.patches.commented_out\n\nexecute:print(1)\n"
    )
    _patch_log(fake_site, [])

    result = standalone.list_pending_patches(APP)

    assert result["format"] == "plain"
    assert result["pending"] == [
        {"patch": "lab_app.patches.one", "section": "pre_model_sync", "line": 1},
        {"patch": "execute:print(1)", "section": "pre_model_sync", "line": 4},
    ]
    assert result["hint"] == "`bench migrate` runs pending patches"


def test_pending_patches_page_through_the_whole_patch_log(fake_site, tmp_path, monkeypatch) -> None:
    package = _app(tmp_path, monkeypatch)
    (package / "patches.txt").write_text("[pre_model_sync]\nlab_app.patches.last\n")
    monkeypatch.setattr(standalone, "_PATCH_LOG_PAGE", 2)
    rows = [{"patch": f"x.p{index}", "skipped": 0} for index in range(5)]
    _patch_log(fake_site, [*rows, {"patch": "lab_app.patches.last", "skipped": 0}])

    result = standalone.list_pending_patches(APP)

    assert result["pending"] == [] and result["applied_count"] == 1
    assert [call.params["limit_start"] for call in fake_site.calls_to("GET", PATCH_LOG)] == [
        0, 2, 4, 6,
    ]


def test_pending_patches_refuse_a_patch_log_too_large_to_read(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _app(tmp_path, monkeypatch)
    (package / "patches.txt").write_text("[pre_model_sync]\nlab_app.patches.one\n")
    monkeypatch.setattr(standalone, "_PATCH_LOG_PAGE", 2)
    monkeypatch.setattr(standalone, "_PATCH_LOG_MAX", 4)
    _patch_log(fake_site, [{"patch": f"x.p{index}", "skipped": 0} for index in range(9)])

    with pytest.raises(ValueError, match="more than 4"):
        standalone.list_pending_patches(APP)


def test_pending_patches_bound_the_lists_they_return(fake_site, tmp_path, monkeypatch) -> None:
    package = _app(tmp_path, monkeypatch)
    lines = "\n".join(f"lab_app.patches.p{index}" for index in range(7))
    (package / "patches.txt").write_text(f"[pre_model_sync]\n{lines}\n")
    monkeypatch.setattr(standalone, "_PATCH_LIST_MAX", 3)
    _patch_log(fake_site, [{"patch": f"lab_app.gone.g{index}", "skipped": 0} for index in range(5)])

    result = standalone.list_pending_patches(APP)

    assert [row["patch"] for row in result["pending"]] == [
        "lab_app.patches.p0", "lab_app.patches.p1", "lab_app.patches.p2",
    ]
    assert result["pending_count"] == 7
    assert len(result["unknown_in_log"]) == 3 and result["unknown_in_log_count"] == 5


def test_pending_patches_without_a_patches_file_report_it(fake_site, tmp_path, monkeypatch) -> None:
    _app(tmp_path, monkeypatch)
    _patch_log(fake_site, [{"patch": "lab_app.patches.old", "skipped": 0}])

    result = standalone.list_pending_patches(APP)

    assert result["format"] == "missing"
    assert result["pending"] == [] and result["applied_count"] == 0
    assert result["unknown_in_log"] == ["lab_app.patches.old"]
    assert "hint" not in result


def test_pending_patches_report_duplicate_lines(fake_site, tmp_path, monkeypatch) -> None:
    package = _app(tmp_path, monkeypatch)
    (package / "patches.txt").write_text(
        "[pre_model_sync]\nlab_app.patches.one\n[post_model_sync]\nlab_app.patches.one\n"
    )
    _patch_log(fake_site, [])

    result = standalone.list_pending_patches(APP)

    assert any("lab_app.patches.one" in warning for warning in result["warnings"])


def test_pending_patches_need_the_app_checkout(fake_site, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FRAPPE_APPS_PATH", raising=False)
    with pytest.raises(RuntimeError, match="FRAPPE_APPS_PATH"):
        standalone.list_pending_patches(APP)

    (tmp_path / "apps").mkdir()
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path / "apps"))
    with pytest.raises(ValueError, match="does not exist below FRAPPE_APPS_PATH"):
        standalone.list_pending_patches(APP)
    assert fake_site.calls == []


def test_pending_patches_never_run_execute_lines(fake_site, tmp_path, monkeypatch) -> None:
    package = _app(tmp_path, monkeypatch)
    marker = tmp_path / "ran"
    (package / "patches.txt").write_text(
        f"[pre_model_sync]\nexecute:open({str(marker)!r}, 'w').close()\n"
    )
    _patch_log(fake_site, [])

    result = standalone.list_pending_patches(APP)

    assert result["pending"][0]["patch"].startswith("execute:open(")
    assert not marker.exists()


# get_recent_errors

TRACEBACK = "Traceback (most recent call last):\n" + "\n".join(
    f'  File "app.py", line {index}, in step' for index in range(60)
) + "\nfrappe.exceptions.ValidationError: Rate must be positive\n"


def _error(name: str, **values) -> dict:
    return {
        "name": name,
        "creation": "2026-11-20 10:00:00.000001",
        "method": "lab_app.api.price",
        "reference_doctype": None,
        "reference_name": None,
        "error": TRACEBACK,
        **values,
    }


def test_recent_errors_trim_tracebacks_to_their_tail(fake_site) -> None:
    fake_site.add("GET", ERROR_LOG, [_error("e-1")])

    page = standalone.get_recent_errors()

    row = page["rows"][0]
    assert row["exception"] == "frappe.exceptions.ValidationError: Rate must be positive"
    lines = row["traceback"].splitlines()
    assert len(lines) == standalone._ERROR_TRACEBACK_LINES
    assert lines[-1] == row["exception"]
    assert row["traceback_truncated"] is True
    assert "error" not in row
    assert {key: row[key] for key in ("name", "method", "reference_doctype")} == {
        "name": "e-1", "method": "lab_app.api.price", "reference_doctype": None,
    }
    params = fake_site.calls_to("GET", ERROR_LOG)[0].params
    assert params["order_by"] == "creation desc"
    assert standalone.json.loads(params["filters"]) in ([], {})


def test_recent_errors_cap_the_traceback_characters(fake_site) -> None:
    long_line = "x" * 9000
    fake_site.add("GET", ERROR_LOG, [_error("e-1", error=f"{long_line}\nValueError: boom")])

    row = standalone.get_recent_errors()["rows"][0]

    assert len(row["traceback"]) == standalone._ERROR_TRACEBACK_CHARS
    assert row["traceback"].endswith("ValueError: boom")
    assert row["traceback_truncated"] is True


def test_recent_errors_keep_a_short_traceback_whole(fake_site) -> None:
    fake_site.add("GET", ERROR_LOG, [_error("e-1", error="Traceback\nKeyError: 'rate'\n\n")])

    row = standalone.get_recent_errors()["rows"][0]

    assert row["traceback"] == "Traceback\nKeyError: 'rate'"
    assert row["exception"] == "KeyError: 'rate'"
    assert row["traceback_truncated"] is False


def test_recent_errors_filter_by_time_method_and_doctype(fake_site) -> None:
    fake_site.add("GET", ERROR_LOG, [])

    standalone.get_recent_errors(
        since="2026-11-20T09:30", method="api.price", reference_doctype="Sales Order"
    )

    params = fake_site.calls_to("GET", ERROR_LOG)[0].params
    assert standalone.json.loads(params["filters"]) == [
        ["creation", ">=", "2026-11-20 09:30:00"],
        ["method", "like", "%api.price%"],
        ["reference_doctype", "=", "Sales Order"],
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"since": "yesterday"}, "ISO"),
        ({"since": "2026-11-20T09:30:00+02:00"}, "time zone"),
        ({"reference_doctype": "Bad;Name"}, "DocType"),
        ({"method": "x" * 200}, "method"),
        ({"limit": 0}, "limit"),
        ({"limit": 51}, "limit"),
        ({"offset": -1}, "offset"),
    ],
)
def test_recent_errors_reject_bad_arguments(fake_site, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        standalone.get_recent_errors(**kwargs)
    assert fake_site.calls == []


def test_recent_errors_page_with_limit_plus_one(fake_site) -> None:
    fake_site.add("GET", ERROR_LOG, [_error(f"e-{index}") for index in range(3)])

    page = standalone.get_recent_errors(limit=2, offset=4)

    assert [row["name"] for row in page["rows"]] == ["e-0", "e-1"]
    assert page["has_more"] is True and page["offset"] == 4 and page["limit"] == 2
    params = fake_site.calls_to("GET", ERROR_LOG)[0].params
    assert params["limit_start"] == 4 and params["limit_page_length"] == 3


# list_app_methods

API = '''\
import frappe
from frappe import whitelist
from frappe import whitelist as allow


@frappe.whitelist()
def price(item: str, qty: int = 1, *extra, rate=None, **options) -> float:
    """Return the price of an item.

    Second paragraph is not returned.
    """


@frappe.whitelist(allow_guest=True, methods=["GET"])
def ping():
    pass


@whitelist
def bare():
    pass


@allow(methods=["POST"])
def aliased():
    pass


def helper():
    pass


@other.whitelist()
def not_frappe():
    pass
'''

CONTROLLER = '''\
import frappe
from frappe.model.document import Document


class LabSample(Document):
    @frappe.whitelist()
    def recompute(self, force: bool = False):
        """Recompute totals."""

    def validate(self):
        pass
'''


def _methods_app(tmp_path: Path, monkeypatch) -> Path:
    package = _app(tmp_path, monkeypatch)
    (package / "api.py").write_text(API)
    controller = package / "lab" / "doctype" / "lab_sample"
    controller.mkdir(parents=True)
    (controller / "lab_sample.py").write_text(CONTROLLER)
    (controller / "lab_sample.json").write_text('{"name": "Lab Sample", "doctype": "DocType"}')
    return package


def test_app_methods_find_every_decorator_form(fake_site, tmp_path, monkeypatch) -> None:
    _methods_app(tmp_path, monkeypatch)

    page = standalone.list_app_methods(APP)

    rows = {row["method"]: row for row in page["rows"]}
    assert list(rows) == [
        "lab_app.api.price",
        "lab_app.api.ping",
        "lab_app.api.bare",
        "lab_app.api.aliased",
        "lab_app.lab.doctype.lab_sample.lab_sample.LabSample.recompute",
    ]
    assert rows["lab_app.api.price"] == {
        "method": "lab_app.api.price",
        "kind": "module",
        "args": [
            {"name": "item", "annotation": "str"},
            {"name": "qty", "annotation": "int", "default": "1"},
            {"name": "*extra"},
            {"name": "rate", "default": "None"},
            {"name": "**options"},
        ],
        "doc": "Return the price of an item.",
        "allow_guest": False,
        "methods": ["GET", "POST", "PUT", "DELETE"],
        "file": "lab_app/api.py:7",
    }
    assert rows["lab_app.api.ping"]["allow_guest"] is True
    assert rows["lab_app.api.ping"]["methods"] == ["GET"]
    assert rows["lab_app.api.aliased"]["methods"] == ["POST"]
    document = rows["lab_app.lab.doctype.lab_sample.lab_sample.LabSample.recompute"]
    assert document["kind"] == "document" and document["doctype"] == "Lab Sample"
    assert document["args"] == [{"name": "force", "annotation": "bool", "default": "False"}]
    assert page["files_scanned"] == 3
    assert fake_site.calls == []


def test_app_methods_ignore_bare_whitelist_without_the_frappe_import(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _app(tmp_path, monkeypatch)
    (package / "local.py").write_text("def whitelist(f):\n    return f\n\n@whitelist\ndef x():\n"
                                      "    pass\n")

    assert standalone.list_app_methods(APP)["rows"] == []


def test_app_methods_search_and_page(fake_site, tmp_path, monkeypatch) -> None:
    _methods_app(tmp_path, monkeypatch)

    found = standalone.list_app_methods(APP, search="API.P")
    page = standalone.list_app_methods(APP, limit=2, offset=1)

    assert [row["method"] for row in found["rows"]] == ["lab_app.api.price", "lab_app.api.ping"]
    assert [row["method"] for row in page["rows"]] == ["lab_app.api.ping", "lab_app.api.bare"]
    assert page["has_more"] is True


def test_app_methods_skip_tests_hidden_folders_and_node_modules(
    fake_site, tmp_path, monkeypatch
) -> None:
    package = _app(tmp_path, monkeypatch)
    for folder in ("tests", ".hidden", "node_modules", "lab/tests"):
        (package / folder).mkdir(parents=True)
        (package / folder / "t.py").write_text("import frappe\n@frappe.whitelist()\ndef t():\n"
                                               "    pass\n")

    page = standalone.list_app_methods(APP)

    assert page["rows"] == [] and page["files_scanned"] == 1


def test_app_methods_report_a_file_with_a_syntax_error(fake_site, tmp_path, monkeypatch) -> None:
    package = _methods_app(tmp_path, monkeypatch)
    (package / "broken.py").write_text("import frappe\n@frappe.whitelist()\ndef broken(:\n")

    page = standalone.list_app_methods(APP)

    assert len(page["rows"]) == 5
    assert page["skipped"] == [{"file": "lab_app/broken.py", "reason": "syntax error on line 3"}]


def test_app_methods_skip_symlinks_that_leave_the_app(fake_site, tmp_path, monkeypatch) -> None:
    package = _app(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text("import frappe\n@frappe.whitelist()\ndef evil():\n    pass\n")
    os.symlink(outside / "evil.py", package / "evil.py")
    os.symlink(outside, package / "linked")

    page = standalone.list_app_methods(APP)

    assert page["rows"] == []
    assert page["skipped"] == [{"file": "lab_app/evil.py", "reason": "escapes the app"}]
    assert str(tmp_path) not in str(page)


def test_app_methods_cap_files_and_bytes(fake_site, tmp_path, monkeypatch) -> None:
    package = _methods_app(tmp_path, monkeypatch)
    (package / "big.py").write_text("import frappe\n" + "#" * 200 + "\n")
    monkeypatch.setattr(standalone, "_METHOD_FILE_MAX_BYTES", 100)

    page = standalone.list_app_methods(APP)

    assert {"file": "lab_app/big.py", "reason": "larger than 100 bytes"} in page["skipped"]
    assert page["files_truncated"] is False

    monkeypatch.setattr(standalone, "_METHOD_MAX_FILES", 2)
    capped = standalone.list_app_methods(APP)

    assert capped["files_scanned"] == 2 and capped["files_truncated"] is True


def test_app_methods_never_import_the_app(fake_site, tmp_path, monkeypatch) -> None:
    package = _app(tmp_path, monkeypatch)
    marker = tmp_path / "imported"
    (package / "side_effect.py").write_text(
        f"open({str(marker)!r}, 'w').close()\nimport frappe\n@frappe.whitelist()\ndef f():\n"
        "    pass\n"
    )

    page = standalone.list_app_methods(APP)

    assert [row["method"] for row in page["rows"]] == ["lab_app.side_effect.f"]
    assert not marker.exists()


def test_app_methods_validate_arguments(fake_site, tmp_path, monkeypatch) -> None:
    _app(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="limit"):
        standalone.list_app_methods(APP, limit=0)
    with pytest.raises(ValueError, match="offset"):
        standalone.list_app_methods(APP, offset=-1)
    with pytest.raises(ValueError, match="Invalid app"):
        standalone.list_app_methods("../etc")
