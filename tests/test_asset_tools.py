"""BLENDER_EXECUTABLE 指向 Blender 时执行独立后台集成测试。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import cv2


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
BLENDER = os.environ.get("BLENDER_EXECUTABLE")


@unittest.skipUnless(BLENDER, "设置 BLENDER_EXECUTABLE 后执行 Blender 集成测试")
class AssetToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="photo-scene-assets-")
        cls.root = Path(cls.directory.name)
        fixture = cls.root / "create_fixture.py"
        fixture.write_text('''import bpy
from pathlib import Path
root = Path(__file__).parent
bpy.ops.wm.read_factory_settings(use_empty=True)
material = bpy.data.materials.new("Red")
material.use_nodes = True
material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (0.8, 0.02, 0.01, 1)
bpy.ops.mesh.primitive_cube_add(size=2, location=(3, 4, 5))
item = bpy.context.object
item.name = "TestItem"
item["photo_scene_id"] = "item01"
item.scale = (0.8, 0.5, 1.2)
item.data.materials.append(material)
bevel = item.modifiers.new("Edge", "BEVEL")
bevel.width = 0.1
bevel.segments = 3
bpy.ops.mesh.primitive_cube_add(size=20, location=(3, 4, 5))
room = bpy.context.object
room.name = "EnclosingRoom"
room["photo_scene_id"] = "room"
room.data.materials.append(material)
bpy.ops.object.light_add(type="AREA", location=(0, 0, 5))
bpy.ops.object.camera_add(location=(0, 0, 20))
bpy.context.scene.camera = bpy.context.object
bpy.ops.wm.save_as_mainfile(filepath=str(root / "fixture.blend"))
bpy.ops.export_scene.gltf(filepath=str(root / "fixture.glb"), export_extras=True)
''', encoding="utf-8")
        cls.run_blender(fixture)
        cls.source_hash = hashlib.sha256((cls.root / "fixture.blend").read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    @classmethod
    def run_blender(cls, script, *args):
        command = [BLENDER, "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(script)]
        if args:
            command.extend(["--", *map(str, args)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=240)
        if result.returncode:
            raise AssertionError(f"Blender exited {result.returncode}:\n{result.stdout[-6000:]}\n{result.stderr[-2000:]}")
        return result

    def test_blend_and_glb_geometry_inspection(self):
        for suffix in ("blend", "glb"):
            output = self.root / f"{suffix}_metrics.json"
            self.run_blender(SCRIPTS / "inspect_asset.py", "--input", self.root / f"fixture.{suffix}", "--output", output)
            report = json.loads(output.read_text())
            self.assertTrue(report["hard_gate_pass"])
            self.assertEqual(report["totals"]["mesh_objects"], 2)
            self.assertEqual(report["totals"]["missing_material_faces"], 0)
            item = next(mesh for mesh in report["meshes"] if mesh["photo_scene_id"] == "item01")
            self.assertGreater(item["vertices"], 8)
            self.assertGreater(len(report["material_details"]), 0)
            if suffix == "blend":
                self.assertEqual(item["source_vertices"], 8)

    def test_inspection_cannot_overwrite_input(self):
        source = self.root / "fixture.blend"
        with self.assertRaisesRegex(AssertionError, "指标输出路径必须与输入资产不同"):
            self.run_blender(SCRIPTS / "inspect_asset.py", "--input", source, "--output", source)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), self.source_hash)

    def test_seven_views_isolate_item_and_preserve_source(self):
        output_dir = self.root / "views"
        self.run_blender(SCRIPTS / "render_item_views.py", "--input", self.root / "fixture.blend", "--item-id", "item01", "--output-dir", output_dir, "--resolution", "64")
        report = json.loads((output_dir / "evidence.json").read_text())
        self.assertEqual(set(report["views"]), {"perspective", "front", "back", "left", "right", "top", "bottom"})
        self.assertLess(max(report["bounds"]["dimensions"]), 3)
        for path in report["views"].values():
            image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
            self.assertEqual(image.shape, (64, 64, 4))
            foreground = cv2.countNonZero(image[:, :, 3])
            self.assertGreater(foreground, 100)
            self.assertLess(foreground, 64 * 64)
            opaque = image[:, :, 3] > 127
            self.assertGreater(image[:, :, 2][opaque].mean(), image[:, :, 0][opaque].mean() * 1.5)
        sheet = cv2.imread(report["contact_sheet"], cv2.IMREAD_UNCHANGED)
        self.assertEqual(sheet.shape, (192, 192, 4))
        self.assertEqual(hashlib.sha256((self.root / "fixture.blend").read_bytes()).hexdigest(), self.source_hash)


if __name__ == "__main__":
    unittest.main()
