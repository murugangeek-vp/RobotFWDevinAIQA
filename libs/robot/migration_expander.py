"""MIG-07: Robot pre-run modifier — one test per (manifest table x check).

Usage::

    robot --prerunmodifier libs/robot/migration_expander.py:config/migration/<manifest>.yaml \
          tests/migration

Template tests tagged ``per-table`` are cloned for every enabled table (``${TABLE}`` is
replaced with the table name, dependency order), tests tagged ``per-relationship`` for
every manifest relationship (``${RELATIONSHIP}``). The manifest path is published as
suite metadata ``Migration Manifest`` so the suite verifies exactly what it expanded.
Adding a table to the manifest adds its tests; the suite file never changes.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from robot.api import SuiteVisitor

from libs.migration.manifest import load_manifest

_TEMPLATES = {"per-table": "${TABLE}", "per-relationship": "${RELATIONSHIP}"}


def _substitute(body, placeholder: str, value: str) -> None:
    for item in body:
        if getattr(item, "type", None) == "KEYWORD":
            item.args = tuple(str(a).replace(placeholder, value) for a in item.args)
        if hasattr(item, "body"):
            _substitute(item.body, placeholder, value)


class migration_expander(SuiteVisitor):  # class name must equal the module name for Robot
    def __init__(self, manifest_path: str):
        # POSIX separators: Robot treats backslashes in metadata as escapes.
        self.manifest_path = Path(manifest_path).resolve().as_posix()
        self.manifest = load_manifest(self.manifest_path)

    def _instances(self, tag: str) -> list:
        if tag == "per-table":
            return [
                (n, [f"table:{n}", f"tier:{self.manifest.tables[n].tier}"])
                for n in self.manifest.enabled_tables()
            ]
        return [(r["id"], [f"relationship:{r['id']}"]) for r in self.manifest.relationships]

    def start_suite(self, suite):
        if not any(tag in t.tags for t in suite.tests for tag in _TEMPLATES):
            return
        expanded = []
        for test in suite.tests:
            tag = next((t for t in _TEMPLATES if t in test.tags), None)
            if tag is None:
                expanded.append(test)
                continue
            for value, extra_tags in self._instances(tag):
                clone = test.deepcopy()
                clone.name = f"{value} :: {test.name}"
                clone.tags.remove(tag)
                clone.tags.add(extra_tags)
                _substitute(clone.body, _TEMPLATES[tag], value)
                expanded.append(clone)
        suite.tests = expanded
        suite.metadata["Migration Manifest"] = self.manifest_path
        suite.metadata["Migration"] = f"{self.manifest.name} v{self.manifest.version}"

    def visit_test(self, test):
        pass
