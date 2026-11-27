import copy, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import validator as v

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def load(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as fh: return json.load(fh)
DS = {"EHLE": {"05", "23"}}
SRC = {"status": "community", "change_note": "Per VAC 2026", "sources": [{"title": "VAC EHLE", "url": "https://example.org/vac", "date": "2026-11-27"}]}

def errs(issues): return [i for i in issues if i.level == "error"]
def warns(issues): return [i for i in issues if i.level == "warn"]

class Airport(unittest.TestCase):
    def setUp(self): self.a = load("profiles/airports/EHLE.json")
    def test_shipped_profile_ok(self): self.assertEqual(errs(v.check_airport(self.a, "EHLE", mode="local", dataset=DS)), [])
    def test_wrong_side_value(self):
        self.a["runway_sides"]["05"] = "up"; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_circuit_too_wide(self):
        self.a["circuit"]["offset_nm"] = 5.0; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_circuit_too_narrow(self):
        self.a["circuit"]["offset_nm"] = 0.05; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_nan_height(self):
        self.a["circuit_agl_ft"] = float("nan"); self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_bool_is_not_number(self):
        self.a["circuit_agl_ft"] = True; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_circuit_height_limits(self):
        for h in (100, 9000): self.a["circuit_agl_ft"] = h; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_unknown_key(self):
        self.a["evil"] = 1; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_markup_in_notes(self):
        for bad in ("<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "javascript:alert(1)", "a&#60;b", "x onload=1"):
            self.a["notes"] = [bad]; self.assertTrue(errs(v.check_airport(self.a, "EHLE")), bad)
    def test_hidden_characters(self):
        for bad in ("hi‮gnp.txt", "zero​width", "bell\x07"):
            self.a["notes"] = [bad]; self.assertTrue(errs(v.check_airport(self.a, "EHLE")), repr(bad))
    def test_long_text(self):
        self.a["notes"] = ["x" * 301]; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
        self.a["notes"] = ["ok"] * 13; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_bad_frequency(self):
        self.a["frequencies"]["Tower"] = "999.999"; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_icao_must_match_file_and_form(self):
        self.assertTrue(errs(v.check_airport(self.a, "EHAM")))
        self.a["icao"] = "../x"; self.assertTrue(errs(v.check_airport(self.a)))
    def test_unknown_runway_for_airport(self):
        self.a["runway_sides"]["09"] = "left"; self.assertTrue(errs(v.check_airport(self.a, "EHLE", dataset=DS)))
    def test_unknown_airport_blocks_submit(self):
        self.assertTrue(errs(v.check_airport(self.a, "EHLE", mode="submit", dataset={"EHAM": {"06"}}, baseline=self.a)))
    def test_bad_call_key_and_markup(self):
        self.a["calls"] = {"hack": "x"}; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
        self.a["calls"] = {"downwind": "<b>x</b>"}; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
    def test_side_flip_needs_source_in_submit(self):
        base = copy.deepcopy(self.a); self.a["runway_sides"]["05"] = "right"
        r = v.check_airport(self.a, "EHLE", baseline=base, mode="submit", dataset=DS)
        self.assertTrue(errs(r)); self.assertTrue(any("SIDE CHANGE" in w.msg for w in warns(r)))
        self.a["meta"] = SRC
        r = v.check_airport(self.a, "EHLE", baseline=base, mode="submit", dataset=DS)
        self.assertEqual(errs(r), []); self.assertTrue(any("SIDE CHANGE" in w.msg for w in warns(r)))   # still flagged for the maintainer
    def test_local_edit_needs_no_source(self):
        base = copy.deepcopy(self.a); self.a["runway_sides"]["05"] = "right"
        self.assertEqual(errs(v.check_airport(self.a, "EHLE", baseline=base, mode="local")), [])
    def test_big_jump_blocked_even_with_source(self):
        base = copy.deepcopy(self.a); self.a["circuit"]["offset_nm"] = 1.4; self.a["meta"] = SRC
        self.assertTrue(errs(v.check_airport(self.a, "EHLE", baseline=base, mode="submit")))
    def test_medium_change_flagged(self):
        base = copy.deepcopy(self.a); self.a["circuit"]["offset_nm"] = 0.53 * 1.4; self.a["meta"] = SRC
        r = v.check_airport(self.a, "EHLE", baseline=base, mode="submit")
        self.assertEqual(errs(r), []); self.assertTrue(warns(r))
    def test_non_https_source(self):
        s = copy.deepcopy(SRC); s["sources"][0]["url"] = "http://example.org"
        self.a["meta"] = s; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))
        for u in ("https://127.0.0.1/x", "https://localhost/x", "javascript:alert(1)"):
            s["sources"][0]["url"] = u; self.a["meta"] = s; self.assertTrue(errs(v.check_airport(self.a, "EHLE")), u)
    def test_cannot_self_declare_verified(self):
        self.a["meta"] = {"status": "verified"}; self.assertTrue(errs(v.check_airport(self.a, "EHLE")))

class Strict(unittest.TestCase):
    def test_duplicate_keys(self): self.assertTrue(v.parse_strict('{"a":1,"a":2}')[1])
    def test_nan(self): self.assertTrue(v.parse_strict('{"a":NaN}')[1])
    def test_big(self): self.assertTrue(v.parse_strict('{"a":"%s"}' % ("x" * 40000))[1])
    def test_deep(self): self.assertTrue(v.parse_strict('{"a":' * 10 + "1" + "}" * 10)[1])
    def test_not_object(self): self.assertTrue(v.parse_strict("[1]")[1])
    def test_bad_utf8(self): self.assertTrue(v.parse_strict(b"\xff\xfe{}")[1])

class Aircraft(unittest.TestCase):
    def setUp(self): self.a = load("profiles/aircraft/c152.json")
    def test_shipped_ok(self):
        import glob
        for f in glob.glob(os.path.join(ROOT, "profiles/aircraft/*.json")):
            o = load(os.path.relpath(f, ROOT)); self.assertEqual(errs(v.check_aircraft(o, o["id"])), [], f)
    def test_speed_band(self):
        self.a["targets"]["FINAL"]["ias"] = [90, 60]; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))
        self.a["targets"]["FINAL"]["ias"] = [5, 60]; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))
    def test_base_faster_than_downwind(self):
        self.a["targets"]["BASE"]["ias"] = [120, 140]; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))
    def test_flap_not_in_detents(self):
        self.a["targets"]["FINAL"]["flaps"] = [45]; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))
    def test_limits_enforced(self):
        self.a["limits"] = {"vs0": 40, "vs1": 48, "vfe": 85, "vno": 110, "vne": 149}
        self.assertEqual(errs(v.check_aircraft(self.a, "c152")), [])
        self.a["targets"]["FINAL"]["ias"] = [40, 66]; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))   # too near the stall
        self.a["targets"]["FINAL"]["ias"] = [60, 95]; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))   # above vfe with flaps
    def test_limits_order(self):
        self.a["limits"] = {"vs0": 60, "vs1": 48}; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))
    def test_bad_simvar(self):
        self.a["carb_simvar"] = "x; rm -rf /"; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))
    def test_rotate(self):
        self.a["rotate_kt"] = 500; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))
        self.a["rotate_kt"] = 90; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))   # above climb band
    def test_unknown_phase_and_key(self):
        self.a["targets"]["EVIL"] = {}; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))
        del self.a["targets"]["EVIL"]; self.a["x"] = 1; self.assertTrue(errs(v.check_aircraft(self.a, "c152")))

class Lang(unittest.TestCase):
    def test_shipped_lang_ok(self):
        import glob
        for f in glob.glob(os.path.join(ROOT, "lang/*.json")):
            with open(f, encoding="utf-8") as fh: self.assertEqual(v.check_lang(json.load(fh)), [], f)
    def test_markup_in_lang(self):
        self.assertTrue(v.check_lang({"cues": {"a": "<script>x</script>"}}))
        self.assertTrue(v.check_lang({"cues": {"a": "<img src=x onerror=alert(1)>"}}))
        self.assertTrue(v.check_lang({"ui": {"a": "hi\u202e"}}))
        self.assertEqual(v.check_lang({"cues": {"a": "Flaps.<fl> Flaps 10.</fl> Go"}}), [])

class Trust(unittest.TestCase):
    def test_hash_ignores_status_only(self):
        a = load("profiles/airports/EHLE.json"); h = v.content_hash(a)
        b = copy.deepcopy(a); b["meta"] = {"status": "local"}; self.assertEqual(v.content_hash(b), v.content_hash(dict(a, meta={"status": "community"})))
        c = copy.deepcopy(a); c["circuit_agl_ft"] = 600; self.assertNotEqual(v.content_hash(c), h)
    def test_verified_only_via_manifest(self):
        a = load("profiles/airports/EHLE.json"); a["meta"] = {"status": "community"}
        self.assertEqual(v.trust_level(a, "airports/EHLE.json", {}), "community")
        self.assertEqual(v.trust_level(a, "airports/EHLE.json", {"airports/EHLE.json": v.content_hash(a)}), "verified")
        a["circuit"]["offset_nm"] = 0.6                      # any edit after verification drops the badge
        self.assertEqual(v.trust_level(a, "airports/EHLE.json", {"airports/EHLE.json": "0" * 64}), "community")
        a["meta"]["status"] = "local"; self.assertEqual(v.trust_level(a, "airports/EHLE.json", {}), "local")

class Sanitize(unittest.TestCase):
    def test_drops_bad_keeps_good(self):
        a = load("profiles/airports/EHLE.json"); a["notes"] = ["fine", "<script>x</script>"]; a["circuit"]["offset_nm"] = 9; a["evil"] = 1
        clean, errors = v.sanitize_airport(a, "EHLE")
        self.assertEqual(clean["notes"], ["fine"]); self.assertNotIn("offset_nm", clean["circuit"]); self.assertNotIn("evil", clean)
        self.assertEqual(clean["runway_sides"], a["runway_sides"]); self.assertTrue(errors)

if __name__ == "__main__":
    unittest.main(verbosity=1)
