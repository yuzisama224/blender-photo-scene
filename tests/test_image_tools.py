"""用合成图验证蒙版提取和对齐指标。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


segment = load_module("seeded_part_masks")
overlay = load_module("render_overlay_validator")


class ImageToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.mask = np.zeros((64, 80), np.uint8)
        self.mask[15:35, 20:40] = 255
        self.reference = self.write_image("reference.png", self.mask)

    def write_image(self, name, image):
        path = self.root / name
        self.assertTrue(cv2.imwrite(str(path), image))
        return path

    def test_identical_mask_and_edges(self):
        for mode in ("mask", "canny"):
            report = overlay.validate(self.reference, self.reference, mode, self.root / f"{mode}.json", self.root / f"{mode}.png")
            self.assertEqual(report["iou"], 1)
            self.assertEqual(report["mse"], 0)
            self.assertEqual(report["centroid_drift_px"], [0, 0])

    def test_shift_has_exact_centroid_error(self):
        shifted = np.zeros_like(self.mask)
        shifted[18:38, 25:45] = 255
        path = self.write_image("shift.png", shifted)
        report = overlay.validate(self.reference, path, "mask", self.root / "shift.json")
        self.assertEqual(report["centroid_drift_px"], [5, 3])
        self.assertAlmostEqual(report["iou"], (15 * 17) / (800 - 15 * 17))
        self.assertEqual(report["render_bbox"], [25, 18, 20, 20])

    def test_empty_mask_and_edges_fail(self):
        path = self.write_image("empty.png", np.zeros_like(self.mask))
        for mode in ("mask", "canny"):
            with self.assertRaisesRegex(ValueError, "为空"):
                overlay.validate(self.reference, path, mode, self.root / "empty.json")
        self.assertFalse((self.root / "empty.json").exists())

    def test_wrong_size_fails_without_resizing(self):
        path = self.write_image("small.png", self.mask[:40, :50])
        with self.assertRaisesRegex(ValueError, "尺寸必须一致"):
            overlay.validate(self.reference, path, "mask", self.root / "wrong.json")
        self.assertFalse((self.root / "wrong.json").exists())

    def test_cli_requires_mode(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "render_overlay_validator.py"), "--reference", str(self.reference), "--render", str(self.reference), "--out", str(self.root / "result.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--mode", result.stderr)

    def test_named_polygon_and_bbox_clip_thresholds(self):
        image = np.full((64, 80, 3), 255, np.uint8)
        image[10:30, 10:50] = (0, 0, 255)
        self.write_image("colors.png", image)
        manifest = {
            "image": "colors.png",
            "parts": [
                {"name": "left", "polygon": [[10, 10], [19, 10], [19, 29], [10, 29]], "mode": "hsv_range", "hsv_min": [0, 200, 200], "hsv_max": [10, 255, 255]},
                {"name": "right", "bbox": [30, 0, 10, 40], "mode": "hsv_range", "hsv_min": [0, 200, 200], "hsv_max": [10, 255, 255]},
            ],
        }
        path = self.root / "parts.json"
        path.write_text(json.dumps(manifest))
        report = segment.extract_parts(path, self.root / "parts")
        self.assertEqual([part["area_px"] for part in report["parts"]], [200, 200])
        self.assertEqual([part["bbox_px"] for part in report["parts"]], [[10, 10, 10, 20], [30, 10, 10, 20]])
        for part in report["parts"]:
            self.assertEqual(cv2.countNonZero(cv2.imread(part["mask"], 0)), 200)

    def test_min_area_failure_does_not_fill_region(self):
        manifest = {"image": "reference.png", "parts": [{"name": "detail", "bbox": [0, 0, 80, 64], "mode": "hsv_range", "hsv_min": [0, 0, 254], "hsv_max": [179, 255, 255], "min_area": 401}]}
        path = self.root / "small_part.json"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "400 px 小于 min_area=401"):
            segment.extract_parts(path, self.root / "failed")
        self.assertFalse((self.root / "failed" / "part_inventory.json").exists())

    def test_empty_extraction_and_missing_mode_fail(self):
        base = segment.region_mask(self.mask.shape, {"name": "part", "bbox": [0, 0, 10, 10]})
        bgr, alpha = segment.read_image(self.reference)
        with self.assertRaisesRegex(ValueError, "显式设置 mode"):
            segment.threshold_inside(bgr, alpha, base, {"name": "part"})
        with self.assertRaisesRegex(ValueError, "为空"):
            segment.bbox_from_mask(np.zeros_like(self.mask))

    def test_offscreen_bbox_does_not_wrap_indices(self):
        for box in ([-30, -30, 10, 10], [100, 100, 10, 10]):
            mask = segment.region_mask(self.mask.shape, {"name": "outside", "bbox": box})
            self.assertEqual(np.count_nonzero(mask), 0)


if __name__ == "__main__":
    unittest.main()
