"""Pure catalog contract: no service, database, models or user configuration."""
import unittest

from app.content_templates import get_content_template, list_content_templates


class ContentTemplateCatalogTest(unittest.TestCase):
    def test_new_creator_paths_are_selectable_with_evidence_inputs(self):
        for key in ("product_intro", "customer_faq", "customer_case"):
            with self.subTest(key=key):
                item = get_content_template(key)
                self.assertIn(item, list_content_templates())
                self.assertGreaterEqual(len(item.script_structure), 5)
                self.assertTrue(item.scenario)
                self.assertGreaterEqual(len(item.material_requirements), 4)
                self.assertTrue(any("资料" in value or "出处" in value for value in item.material_requirements))
                self.assertIn(item.jianying_style, {"tutorial_demo", "tool_review"})
                self.assertTrue(item.title_rules)
                self.assertTrue(item.publish_copy_rules)

    def test_existing_technical_paths_remain_and_unknown_ids_do_not_substitute(self):
        ids = [item.template_id for item in list_content_templates()]
        self.assertEqual(len(ids), len(set(ids)))
        for key in ("video_diary", "tool_review", "tutorial_demo"):
            self.assertEqual(get_content_template(key).template_id, key)
        with self.assertRaises(KeyError):
            get_content_template("unknown")


if __name__ == "__main__":
    unittest.main()
