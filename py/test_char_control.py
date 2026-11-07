"""char_control 的最小回归：提示词 -> spec -> 校验。"""
import unittest

from char_control import compile_prompt, validate_spec, SCHEMA_VERSION


EDITORIAL = (
    "9:16 vertical - editorial portrait, single subject. Soft black mist filter, "
    "subtle haze, gentle highlight bloom, muted tones. Minimal indoor space, "
    "clean background, slight texture. Young Korean woman, minimal makeup, "
    "natural skin texture. Pose: sitting on floor with one leg bent and the other "
    "relaxed, body slightly leaning, shoulders not aligned, head tilted. "
    "Expression: calm, slightly distant, natural lips. Lighting: soft side light."
)


class CharControlTest(unittest.TestCase):
    def test_editorial_prompt_compiles_to_sit(self):
        spec = compile_prompt(EDITORIAL)
        self.assertEqual(spec["version"], SCHEMA_VERSION)
        self.assertEqual(spec["action"], "sit")
        self.assertEqual(spec["camera"]["framing"], "9:16")
        self.assertAlmostEqual(spec["pelvis"]["height"], 0.15)
        self.assertEqual(spec["face"]["expression"], "calm")
        self.assertEqual(spec["head"]["roll"], 8.0)
        self.assertEqual(validate_spec(spec), [])

    def test_run_prompt(self):
        spec = compile_prompt("a minecraft character running through a village")
        self.assertEqual(spec["action"], "run")
        self.assertEqual(validate_spec(spec), [])

    def test_wave_prompt(self):
        spec = compile_prompt("standing and waving at the camera")
        self.assertEqual(spec["action"], "wave")
        self.assertEqual(validate_spec(spec), [])

    def test_invalid_spec_is_rejected(self):
        spec = compile_prompt("sit")
        spec["pelvis"]["height"] = 9
        spec["camera"]["framing"] = "3:2"
        errors = validate_spec(spec)
        self.assertTrue(any("pelvis.height" in e for e in errors))
        self.assertTrue(any("camera.framing" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
