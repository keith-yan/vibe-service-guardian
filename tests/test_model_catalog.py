import unittest

from vsg.model_catalog import CatalogError, catalog_summary, load_catalog, validate_catalog


class ModelCatalogTests(unittest.TestCase):
    def test_bundled_catalog_is_valid_and_offline(self):
        catalog = load_catalog()
        summary = catalog_summary(catalog)
        self.assertGreaterEqual(summary["model_count"], 10)
        self.assertTrue(summary["offline"])
        self.assertTrue(any(item["architecture"] == "moe" for item in catalog["models"]))
        self.assertTrue(any(item["architecture"] == "dense" for item in catalog["models"]))

    def test_active_parameters_cannot_exceed_total(self):
        raw = load_catalog()
        raw["models"][0]["active_params_b"] = raw["models"][0]["total_params_b"] + 1
        with self.assertRaises(CatalogError):
            validate_catalog(raw)

    def test_reference_architectures_match_official_model_cards(self):
        models = {item["id"]: item for item in load_catalog()["models"]}
        self.assertEqual(models["qwen3.5-35b-a3b"]["architecture"], "moe")
        self.assertEqual(models["gemma-4-e2b"]["architecture"], "dense")
        self.assertEqual(models["gemma-4-e4b"]["architecture"], "dense")
        self.assertEqual(models["gemma-4-26b-a4b"]["architecture"], "moe")
        self.assertEqual(models["gpt-oss-120b"]["active_params_b"], 5.1)


if __name__ == "__main__":
    unittest.main()
