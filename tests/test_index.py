"""profiles/index.json must list every shipped profile with its current SHA-256 (the app's data updater relies on it)."""
import hashlib, json, os, unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles")


class IndexTest(unittest.TestCase):
    def test_index_current(self):
        idx = json.load(open(os.path.join(ROOT, "index.json"), encoding="utf-8"))
        self.assertEqual(idx["format"], 1)
        want = {}
        for kind in ("airports", "aircraft"):
            for n in os.listdir(os.path.join(ROOT, kind)):
                if n.endswith(".json") and not n.startswith("_"):
                    want[f"{kind}/{n}"] = hashlib.sha256(open(os.path.join(ROOT, kind, n), "rb").read()).hexdigest()
        self.assertEqual(idx["files"], want, "run: python tools/make_index.py")


if __name__ == "__main__":
    unittest.main()
