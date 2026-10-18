import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("instruction_budget", ROOT / "scripts/instruction_budget.py")
budget = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(budget)


class InstructionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.raw = "# Guide\r\n\r\n导语\r\n## Alpha\r\n甲 \t乙　\r\n### Detail\r\n细节\r\n## Beta\r\n末尾\r\n".encode("utf-8")
        (self.root / "rules.md").write_bytes(self.raw)
        self.config = {"schema": 1, "scope": "Explicit fixture; not actual usage or automatic closure.",
                       "nodes": [{"id": "alpha", "path": "rules.md", "section": "Alpha"}],
                       "scenarios": [{"id": "fixture", "task": "Fixture task", "assumptions": "Cold explicit test",
                                      "calls": [{"id": "writer", "role": "Writer", "preload": [], "required": ["alpha"],
                                                 "limits": {"nonspace_chars": 1000, "utf8_bytes": 1000}}]}]}

    @property
    def call(self):
        return self.config["scenarios"][0]["calls"][0]

    def report(self):
        return budget.audit(self.config, self.root)

    def cli(self, *args):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(self.config), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = budget.main(["--root", str(self.root), "--manifest", str(manifest), *map(str, args)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_subtree_preserves_exact_utf8_and_crlf(self):
        source = self.report()["sources"][0]
        selected = self.raw[self.raw.index(b"## Alpha"):self.raw.index(b"## Beta")]
        self.assertEqual(source["utf8_bytes"], len(selected))
        self.assertEqual(source["nonspace_chars"], sum(not char.isspace() for char in selected.decode("utf-8")))
        self.assertEqual(source["file_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(source["slice_sha256"], hashlib.sha256(selected).hexdigest())
        self.assertEqual((source["start_line"], source["end_line"]), (4, 7))
        self.assertEqual(self.raw[source["byte_start"]:source["byte_end"]], selected)

    def test_body_stops_before_child_heading(self):
        self.config["nodes"][0]["scope"] = "body"
        source = self.report()["sources"][0]
        self.assertEqual(source["end_line"], 5)
        self.assertEqual(source["byte_end"], self.raw.index(b"### Detail"))

    def test_heading_like_lines_inside_fences_do_not_select_or_end_section(self):
        raw = b"# Guide\n## Alpha\n```md\n## Alpha\n## Beta\n```\n~~~\n## Alpha\n~~~\nkept\n## Beta\nend\n"
        (self.root / "rules.md").write_bytes(raw)
        source = self.report()["sources"][0]
        self.assertEqual(source["end_line"], 10)
        self.assertEqual(raw[source["byte_end"]:], b"## Beta\nend\n")

    def test_closing_hashes_and_bom_are_supported(self):
        (self.root / "rules.md").write_bytes("\ufeff## Alpha ###\n文本\n".encode("utf-8"))
        source = self.report()["sources"][0]
        self.assertEqual(source["byte_start"], 0)
        self.assertEqual(source["utf8_bytes"], (self.root / "rules.md").stat().st_size)

    def test_ambiguous_titles_are_rejected_even_under_different_parents(self):
        (self.root / "rules.md").write_text("# One\n## Alpha\none\n# Two\n## Alpha\ntwo\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            self.report()

    def test_missing_file_missing_title_invalid_scope_and_non_utf8_fail(self):
        mutations = [("path", "missing.md", "Missing instruction"),
                     ("section", "Missing", "Missing ATX"), ("scope", "unknown", "Invalid section scope")]
        for key, value, message in mutations:
            with self.subTest(key=key):
                config = copy.deepcopy(self.config)
                config["nodes"][0][key] = value
                with self.assertRaisesRegex(ValueError, message):
                    budget.audit(config, self.root)
        (self.root / "rules.md").write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "not UTF-8"):
            self.report()

    def test_paths_must_be_unambiguous_and_inside_source_root(self):
        for name in ("../outside.md", "/outside.md", "C:/outside.md", "rules\\part.md", "./rules.md", "x//rules.md", ""):
            with self.subTest(name=name):
                self.config["nodes"][0]["path"] = name
                with self.assertRaisesRegex(ValueError, "Invalid repository-relative path"):
                    self.report()

    def test_symlink_cannot_escape_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "rules.md"
            outside.write_bytes(self.raw)
            try:
                (self.root / "link.md").symlink_to(outside)
            except OSError:
                self.skipTest("Symlink creation unavailable")
            self.config["nodes"][0]["path"] = "link.md"
            with self.assertRaisesRegex(ValueError, "escapes"):
                self.report()

    def test_dependencies_and_preloads_deduplicate_within_one_call(self):
        self.config["nodes"].extend([{"id": "one", "includes": ["alpha"]},
                                      {"id": "two", "includes": ["one", "alpha"]}])
        self.call["preload"] = ["one"]
        self.call["required"] = ["two", "alpha"]
        report = self.report()
        load = report["loads"][0]
        self.assertEqual(load["source_ids"], ["alpha"])
        self.assertEqual(load["totals"], load["preload_totals"])
        self.assertEqual(load["totals"]["utf8_bytes"], report["sources"][0]["utf8_bytes"])

    def test_different_roles_are_separate_cold_calls(self):
        critic = copy.deepcopy(self.call)
        critic.update(id="critic", role="Independent reviewer", preload=["alpha"])
        self.config["scenarios"][0]["calls"].append(critic)
        loads = self.report()["loads"]
        self.assertEqual(len(loads), 2)
        self.assertEqual(loads[0]["totals"], loads[1]["totals"])
        self.assertEqual(loads[1]["preload_totals"], loads[1]["totals"])

    def test_unknown_reference_and_cycles_fail_even_when_unused(self):
        for node, message in (({"id": "orphan", "includes": ["missing"]}, "Unknown node"),
                              ({"id": "orphan", "includes": ["orphan"]}, "Cyclic")):
            with self.subTest(node=node):
                config = copy.deepcopy(self.config)
                config["nodes"].append(node)
                with self.assertRaisesRegex(ValueError, message):
                    budget.audit(config, self.root)

    def test_duplicate_identifiers_and_references_fail(self):
        for collection in ("nodes", "scenarios"):
            config = copy.deepcopy(self.config)
            config[collection].append(copy.deepcopy(config[collection][0]))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                budget.audit(config, self.root)
        self.call["required"].append("alpha")
        with self.assertRaisesRegex(ValueError, "Duplicate reference"):
            self.report()

    def test_overlapping_sources_rejected_but_body_and_children_partition(self):
        self.config["nodes"].append({"id": "detail", "path": "rules.md", "section": "Detail"})
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            self.report()
        self.config["nodes"][0]["scope"] = "body"
        self.call["required"].append("detail")
        self.assertEqual(len(self.report()["loads"][0]["source_ids"]), 2)

    def test_identical_range_under_different_ids_is_rejected(self):
        self.config["nodes"].append({**self.config["nodes"][0], "id": "duplicate"})
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            self.report()

    def add_branches(self):
        self.config["nodes"].append({"id": "beta", "path": "rules.md", "section": "Beta"})
        self.call["branches"] = [{"id": "ending", "when": "Ending is relevant", "includes": ["beta"]},
                                 {"id": "reuse", "when": "Re-read already loaded rule", "includes": ["alpha"]}]
        self.call["combinations"] = [{"id": "both", "when": "Both apply", "branches": ["ending", "reuse"]}]

    def test_branches_combinations_and_registered_upper_bound(self):
        self.add_branches()
        rows = {row["variant"]: row for row in self.report()["loads"]}
        self.assertEqual(len(rows), 5)
        self.assertGreater(rows["branch:ending"]["totals"]["utf8_bytes"], rows["base"]["totals"]["utf8_bytes"])
        self.assertEqual(rows["base"]["totals"], rows["branch:reuse"]["totals"])
        self.assertEqual(rows["combination:both"]["totals"], rows["registered_branch_union"]["totals"])

    def test_invalid_combinations_and_empty_branch_conditions_fail(self):
        self.add_branches()
        self.call["combinations"][0]["branches"] = ["absent"]
        with self.assertRaisesRegex(ValueError, "Unknown branch"):
            self.report()
        self.call["combinations"] = []
        self.call["branches"][0]["when"] = ""
        with self.assertRaisesRegex(ValueError, "must be nonempty text"):
            self.report()

    def test_base_limit_has_independent_effect(self):
        self.add_branches()
        self.call["base_limits"] = {"nonspace_chars": 1, "utf8_bytes": 1}
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertFalse(report["loads"][0]["passed"])
        self.assertTrue(all(row["passed"] for row in report["loads"][1:]))

    def test_invalid_budget_numbers_fail(self):
        for value in (True, 0, -1, 10.5, "1000"):
            with self.subTest(value=value):
                self.call["limits"]["utf8_bytes"] = value
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    self.report()

    def test_cli_success_budget_failure_and_bad_input_exit_codes(self):
        code, text, error = self.cli()
        self.assertEqual((code, error), (0, ""))
        summary = json.loads(text)
        self.assertTrue(summary["passed"])
        self.assertNotIn("sources", summary)
        self.call["limits"]["utf8_bytes"] = 1
        self.assertEqual(self.cli()[0], 1)
        self.call["required"] = ["unknown"]
        self.assertEqual(self.cli()[0], 2)

    def test_cli_reports_exact_source_identity_and_refuses_existing_output(self):
        destination = self.root / "reports"
        code, text, error = self.cli("--output", destination, "--json")
        self.assertEqual((code, error), (0, ""))
        report = json.loads(text)
        persisted = json.loads((destination / "instruction-loads.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted, report)
        self.assertIn("No normalization, tokenizer, or actual usage measurement", report["method"])
        self.assertTrue(report["not_measured"])
        before = (destination / "instruction-loads.json").read_bytes()
        self.assertEqual(self.cli("--output", destination)[0], 2)
        self.assertEqual((destination / "instruction-loads.json").read_bytes(), before)

    def test_output_symlink_cannot_overwrite_rule_source(self):
        destination = self.root / "reports"
        destination.mkdir()
        try:
            (destination / "instruction-loads.md").symlink_to(self.root / "rules.md")
        except OSError:
            self.skipTest("Symlink creation unavailable")
        self.assertEqual(self.cli("--output", destination)[0], 2)
        self.assertEqual((self.root / "rules.md").read_bytes(), self.raw)
        self.assertFalse((destination / "instruction-loads.json").exists())

    def test_output_directory_symlink_is_rejected(self):
        real = self.root / "reports"
        real.mkdir()
        alias = self.root / "alias"
        try:
            alias.symlink_to(real, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        self.assertEqual(self.cli("--output", alias)[0], 2)
        self.assertEqual(list(real.iterdir()), [])


class RepositoryInstructionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "benchmarks/instruction-loads.json").read_text(encoding="utf-8"))
        cls.report = budget.audit(cls.config, ROOT)
        cls.base = {(item["scenario"], item["call"]): item for item in cls.report["loads"] if item["variant"] == "base"}

    def test_registered_paths_fit_independent_base_and_branch_limits(self):
        self.assertTrue(self.report["passed"])
        self.assertGreaterEqual(len(self.report["loads"]), 50)
        for scenario in self.config["scenarios"]:
            for call in scenario["calls"]:
                for kind, limit_key in (("base", "base_limits"), ("registered_maximum", "limits")):
                    for metric in budget.METRICS:
                        baseline = call["baseline"][kind][metric]
                        self.assertGreaterEqual(call[limit_key][metric], baseline * 1.15)
                        self.assertLess(call[limit_key][metric], baseline * 1.15 + 1001)

    def test_required_loads_cannot_be_dropped_to_make_budgets_pass(self):
        core = {"common", "content_baseline", "content_judgments", "content_outcomes", "content_routing",
                "characters", "support", "viewpoint", "words", "format"}
        for (scenario, call), row in self.base.items():
            if scenario not in ("targeted_punctuation", "short_platform_design") and call != "cover_designer":
                self.assertTrue(core.issubset(row["source_ids"]), (scenario, call))
        long_sources = set(self.base[("long_chapter", "writer")]["source_ids"])
        self.assertTrue({"write_entry", "tools", "chapter_flow", "length", "outline_quality", "project_binding",
                         "content_plan", "naming", "punctuation", "scene", "presence", "hook", "author_note", "note_save"}.issubset(long_sources))
        short_sources = set(self.base[("short_write", "writer")]["source_ids"])
        self.assertTrue({"short_delivery", "completion", "project_author", "project_platform_fields", "fanqie_category_route", "fanqie_short_categories",
                         "qimao_categories", "qidian_categories", "opening", "lens"}.issubset(short_sources))
        self.assertTrue({"author_note", "note_save"}.isdisjoint(short_sources))
        self.assertEqual(set(self.base[("short_write", "cover_designer")]["source_ids"]),
                         {"cover_entry", "common", "project_author", "cover_appeal"})

    def test_fanqie_entry_routes_preserve_three_platforms_and_unknown_fallback(self):
        shared = {"fanqie_category_route", "qimao_categories", "qidian_categories", "project_platform_fields",
                  "project_author", "short_delivery", "completion"}
        short = self.base[("short_write", "writer")]
        novel = self.base[("short_write_novel_entry", "writer")]
        unknown = self.base[("short_write_unknown_entry", "writer")]
        for row in (short, novel, unknown):
            self.assertTrue(shared.issubset(row["source_ids"]))
            self.assertTrue({"author_note", "note_save"}.isdisjoint(row["source_ids"]))
        self.assertIn("fanqie_short_categories", short["source_ids"])
        self.assertNotIn("fanqie_novel_categories", short["source_ids"])
        self.assertIn("fanqie_novel_categories", novel["source_ids"])
        self.assertNotIn("fanqie_short_categories", novel["source_ids"])
        self.assertEqual(set(unknown["source_ids"]), set(short["source_ids"]) | set(novel["source_ids"]))
        sources = {item["id"]: item for item in self.report["sources"]}
        # The fallback must retain every source byte, including both complete
        # option inventories; entry selection only removes the other inventory.
        slices = [sources[key] for key in ("fanqie_category_route", "fanqie_short_categories", "fanqie_novel_categories")]
        raw = (ROOT / slices[0]["path"]).read_bytes()
        self.assertEqual(slices[0]["byte_start"], 0)
        self.assertEqual(slices[0]["byte_end"], slices[1]["byte_start"])
        self.assertEqual(slices[1]["byte_end"], slices[2]["byte_start"])
        self.assertEqual(slices[2]["byte_end"], len(raw))
        for metric in budget.METRICS:
            self.assertEqual(unknown["totals"][metric] - short["totals"][metric],
                             sources["fanqie_novel_categories"][metric])
            self.assertEqual(unknown["totals"][metric] - novel["totals"][metric],
                             sources["fanqie_short_categories"][metric])

    def test_writing_keeps_scene_guidance_and_loads_diagnosis_when_triggered(self):
        for scenario in ("long_chapter", "short_write", "short_write_novel_entry", "short_write_unknown_entry", "draft_fragment"):
            base = self.base[(scenario, "writer")]
            self.assertTrue({"scene", "presence", "expectations", "dialogue"}.issubset(base["source_ids"]), scenario)
            self.assertTrue({"content_expression", "flatness"}.isdisjoint(base["source_ids"]), scenario)
            triggered = next(item for item in self.report["loads"] if item["scenario"] == scenario
                             and item["call"] == "writer" and item["variant"] == "branch:scene_diagnosis")
            self.assertEqual(set(triggered["source_ids"]) - set(base["source_ids"]), {"content_expression", "flatness"}, scenario)

    def test_simple_text_path_avoids_content_and_formal_revision_loads(self):
        row = self.base[("targeted_punctuation", "copyeditor")]
        self.assertEqual(set(row["source_ids"]), {"review_entry", "common", "format", "speaker", "grammar", "punctuation"})
        self.assertLess(row["totals"]["nonspace_chars"], self.base[("content_review", "reviewer")]["totals"]["nonspace_chars"])
        for source in self.report["sources"]:
            if source["path"].endswith("/drama.md"):
                self.assertEqual(source["scope"] in ("body", "subtree"), True)

    def test_shared_style_decision_applies_to_body_roles_only(self):
        for (scenario, call), row in self.base.items():
            applicable = scenario not in ("targeted_punctuation", "short_platform_design") and call != "cover_designer"
            self.assertEqual("style_resolution" in row["source_ids"], applicable, (scenario, call))
        node = next(item for item in self.config["nodes"] if item["id"] == "style_resolution")
        self.assertEqual((node["path"], node["section"]),
                         ("skills/story-skill-write/references/drama.md", "文风统一裁决"))

    def test_genre_choices_count_selected_cards_without_loading_library_in_base(self):
        cards = {"genre_urban_relations", "genre_business", "genre_science_disaster",
                 "genre_folk_fantasy", "genre_mystery", "genre_quiet_daily"}
        index = next(item for item in self.config["nodes"] if item["id"] == "genre_index")
        self.assertEqual((index["section"], index["scope"]), ("题材写法卡", "body"))
        for (scenario, call), base in self.base.items():
            if scenario in ("targeted_punctuation", "short_platform_design") or call == "cover_designer":
                self.assertNotIn("genre_index", base["source_ids"])
                continue
            self.assertTrue(cards.isdisjoint(base["source_ids"]), (scenario, call))
            self.assertEqual("genre_index" in base["source_ids"], call == "writer", (scenario, call))
            rows = {item["variant"]: item for item in self.report["loads"]
                    if (item["scenario"], item["call"]) == (scenario, call)}
            for card in cards:
                selected = set(rows[f"branch:{card}"]["source_ids"])
                self.assertEqual(selected & cards, {card}, (scenario, call, card))
                self.assertIn("genre_index", selected)
                self.assertIn("style_resolution", selected)
            folk = set(rows["combination:folk_mystery_style"]["source_ids"])
            self.assertEqual(folk & cards, {"genre_folk_fantasy", "genre_mystery"})
            cooperation = set(rows["combination:business_relationship_payoff"]["source_ids"])
            self.assertEqual(cooperation & cards, {"genre_business", "genre_urban_relations"})
            self.assertIn("climax", cooperation)
            self.assertTrue(cards.issubset(rows["registered_branch_union"]["source_ids"]))

    def test_writing_brief_is_conditional_on_bound_next_chapter_material(self):
        handoff_sources = {"writing_brief", "writing_handoff", "preference_recall"}
        for (scenario, call), row in self.base.items():
            if scenario == "long_chapter_handoff":
                self.assertTrue(handoff_sources.issubset(row["source_ids"]))
            else:
                self.assertTrue(handoff_sources.isdisjoint(row["source_ids"]), (scenario, call))
        for scenario in ("long_chapter", "short_write", "short_write_novel_entry", "short_write_unknown_entry"):
            with self.subTest(scenario=scenario):
                rows = {item["variant"]: item for item in self.report["loads"]
                        if (item["scenario"], item["call"]) == (scenario, "writer")}
                self.assertEqual(set(rows["branch:writing_brief"]["source_ids"]) -
                                 set(rows["base"]["source_ids"]), handoff_sources)
                self.assertTrue({"writing_brief", "genre_business", "climax", "project_binding", "content_plan"}
                                .issubset(rows["combination:business_brief_payoff"]["source_ids"]))

    def test_handoff_keeps_formal_guards_without_forcing_them_on_fragments(self):
        ordinary = set(self.base[("long_chapter", "writer")]["source_ids"])
        handoff = set(self.base[("long_chapter_handoff", "writer")]["source_ids"])
        self.assertEqual(handoff - ordinary, {"writing_brief", "writing_handoff", "preference_recall"})
        self.assertTrue(ordinary.issubset(handoff))
        upper = {(row["scenario"], row["call"]): row for row in self.report["loads"]
                 if row["variant"] == "registered_branch_union"}
        ordinary_upper = upper[("long_chapter", "writer")]
        handoff_upper = upper[("long_chapter_handoff", "writer")]
        self.assertEqual(handoff_upper["source_ids"], ordinary_upper["source_ids"])
        self.assertEqual(handoff_upper["limits"], ordinary_upper["limits"])
        fragment_rows = [row for row in self.report["loads"] if row["scenario"] == "draft_fragment"]
        for row in fragment_rows:
            self.assertTrue({"chapter_flow", "project_binding", "writing_brief", "writing_handoff",
                             "preference_recall"}.isdisjoint(row["source_ids"]), row["variant"])

    def test_coverage_is_required_for_formal_units_but_fact_examples_are_conditional(self):
        formal = ("long_chapter", "long_chapter_handoff", "short_write", "short_write_novel_entry",
                  "short_write_unknown_entry")
        for scenario in formal:
            base = set(self.base[(scenario, "writer")]["source_ids"])
            self.assertTrue({"continuity_scope", "chapter_coverage"}.issubset(base), scenario)
            self.assertTrue({"new_facts", "continuity_examples"}.isdisjoint(base), scenario)
            rows = {row["variant"]: row for row in self.report["loads"]
                    if (row["scenario"], row["call"]) == (scenario, "writer")}
            self.assertEqual(set(rows["branch:new_facts"]["source_ids"]) - base, {"new_facts"}, scenario)
            self.assertEqual(set(rows["branch:continuity_examples"]["source_ids"]) - base,
                             {"new_facts", "continuity_examples"}, scenario)
        fragment = set(self.base[("draft_fragment", "writer")]["source_ids"])
        self.assertTrue({"continuity_scope", "chapter_coverage", "new_facts", "continuity_examples"}.isdisjoint(fragment))

    def test_short_platform_method_does_not_make_paywalls_or_planning_universal(self):
        design = {"short_platform_scope", "short_platform_target", "short_platform_progress", "short_platform_review"}
        for scenario in ("short_write", "short_write_novel_entry", "short_write_unknown_entry"):
            rows = {row["variant"]: row for row in self.report["loads"]
                    if (row["scenario"], row["call"]) == (scenario, "writer")}
            base = set(rows["base"]["source_ids"])
            self.assertTrue((design | {"short_platform_paywall"}).isdisjoint(base), scenario)
            self.assertEqual(set(rows["branch:short_platform_design"]["source_ids"]) - base, design, scenario)
            self.assertEqual(set(rows["branch:short_paywall"]["source_ids"]) - base,
                             design | {"short_platform_paywall"}, scenario)
        rows = {row["variant"]: row for row in self.report["loads"]
                if (row["scenario"], row["call"]) == ("short_platform_design", "planner")}
        base = set(rows["base"]["source_ids"])
        self.assertEqual(base, design | {"common", "plan_entry"})
        self.assertEqual(set(rows["branch:paywall"]["source_ids"]) - base, {"short_platform_paywall"})
        self.assertTrue({"chapter_flow", "project_binding", "author_note", "note_save", "cover_entry"}.isdisjoint(base))

    def test_every_complete_chapter_branch_keeps_name_and_plan_review(self):
        for row in self.report["loads"]:
            if "complete_chapter" in row["branches"] or "long_chapter" in row["branches"]:
                self.assertTrue({"length", "naming", "content_plan", "hook"}.issubset(row["source_ids"]))

    def test_each_review_role_loads_long_chapter_companion_obligations(self):
        # The independent role starts cold. The rewriter's note/image rules
        # cannot stand in for that reviewer's own applicable reading.
        sources = {item["id"]: item for item in self.report["sources"]}
        companion_sections = {("skills/story-skill-write/references/drama.md", "作者有话说"),
                              ("skills/story-skill-write/references/chapter.md", "作者有话说的保存与核验")}
        for scenario, call in (("content_review", "reviewer"), ("content_rewrite", "rewriter"),
                               ("content_rewrite", "independent_reviewer")):
            rows = [item for item in self.report["loads"] if item["scenario"] == scenario and item["call"] == call]
            long_chapter = next(item for item in rows if item["variant"] == "branch:long_chapter")
            selected = {(sources[item]["path"], sources[item]["section"]) for item in long_chapter["source_ids"]}
            self.assertTrue(companion_sections.issubset(selected), (scenario, call))
            self.assertTrue({"length", "naming", "content_plan", "hook"}.issubset(long_chapter["source_ids"]))
            upper = next(item for item in rows if item["variant"] == "registered_branch_union")
            self.assertTrue({"author_note", "note_save"}.issubset(upper["source_ids"]))
        independent = [item for item in self.report["loads"] if item["scenario"] == "content_rewrite"
                       and item["call"] == "independent_reviewer" and item["variant"] == "combination:commercial_long_chapter"]
        self.assertEqual(len(independent), 1)
        self.assertTrue({"author_note", "note_save", "platform", "commercial_intro"}.issubset(independent[0]["source_ids"]))

    def test_no_claim_of_automatic_closure_actual_usage_or_quality(self):
        self.assertIn("不是 token、实际读取证据或账单", budget.markdown(self.report))
        self.assertIn("not a claim of complete Markdown closure", next(
            item["condition"] for item in self.report["loads"] if item["variant"] == "registered_branch_union"))
        self.assertTrue(any("whether an agent actually read" in item for item in self.report["not_measured"]))


if __name__ == "__main__":
    unittest.main()
