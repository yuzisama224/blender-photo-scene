"""真实 Blender 后台集成测试；仅在临时目录创建场景和报告。"""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BLENDER = os.environ.get("BLENDER_EXECUTABLE") or shutil.which("blender")
if not BLENDER and Path("/Applications/Blender.app/Contents/MacOS/Blender").is_file():
    BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"

FIXTURE = r'''
import bpy
import math
from pathlib import Path
import sys

output = Path(sys.argv[sys.argv.index('--') + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)

def material(name, color):
    value = bpy.data.materials.new(name)
    value.diffuse_color = (*color, 1)
    value.use_nodes = True
    value.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (*color, 1)
    return value

white = material('Ceramic', (0.8, 0.8, 0.7))
red = material('Label', (0.7, 0.1, 0.1))
stone = material('Stone', (0.2, 0.2, 0.2))
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
cup = bpy.context.object
cup.name = 'Cup'
cup['photo_scene_id'] = 'cup_01'
cup.data.materials.append(white)
cup.data.materials.append(red)
cup.data.polygons[0].material_index = 1
cup.rotation_euler.z = 0.21
cup.scale = (0.7, 0.8, 1.1)
second = cup.copy()
second.data = cup.data
second.name = 'Cup2'
second['photo_scene_id'] = 'cup_02'
second.location = (2, 0.3, 0.5)
bpy.context.collection.objects.link(second)
bpy.ops.mesh.primitive_cube_add(size=1, location=(1, 0, -0.2))
table = bpy.context.object
table.name = 'Counter'
table['photo_scene_id'] = 'counter'
table.scale = (5, 2, 0.3)
table.data.materials.append(stone)
bpy.ops.object.camera_add(location=(5, -7, 4))
bpy.context.object.name = 'PhotoCamera'
bpy.context.scene.camera = bpy.context.object
base = output / 'valid.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(base))

for case in ('missing', 'mapping', 'duplicate', 'unregistered', 'empty', 'nonfinite',
             'degenerate', 'zero_faces', 'parent', 'constraint', 'geometry_dependency',
             'hidden', 'unselectable', 'locked', 'hidden_collection', 'unselectable_collection',
             'missing_material', 'bad_material_index', 'fixed_unselectable_collection',
             'material_swapped', 'other_scene_helper'):
    bpy.ops.wm.open_mainfile(filepath=str(base))
    cup = bpy.data.objects['Cup']
    second = bpy.data.objects['Cup2']
    if case == 'missing':
        bpy.data.objects.remove(cup, do_unlink=True)
    elif case == 'mapping':
        cup.name = 'WrongName'
    elif case == 'duplicate':
        second['photo_scene_id'] = 'cup_01'
    elif case == 'unregistered':
        bpy.ops.mesh.primitive_cube_add(location=(10, 0, 0))
    elif case == 'empty':
        bpy.data.objects.remove(cup, do_unlink=True)
        cup = bpy.data.objects.new('Cup', None)
        cup['photo_scene_id'] = 'cup_01'
        bpy.context.collection.objects.link(cup)
    elif case == 'nonfinite':
        cup.data = cup.data.copy()
        cup.data.vertices[0].co.x = float('nan')
    elif case in ('degenerate', 'zero_faces'):
        mesh = bpy.data.meshes.new('Invalid')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [],
                         [(0, 1, 2)] if case == 'degenerate' else [])
        cup.data = mesh
    elif case in ('missing_material', 'bad_material_index'):
        cup.data = cup.data.copy()
        if case == 'missing_material':
            cup.data.materials.clear()
        else:
            cup.data.polygons[0].material_index = 8
    elif case == 'parent':
        second.parent = cup
    elif case == 'constraint':
        constraint = cup.constraints.new('COPY_LOCATION')
        constraint.target = bpy.data.objects['Counter']
    elif case == 'geometry_dependency':
        second.data = second.data.copy()
        deform = second.modifiers.new('Cup controls deformation', 'SIMPLE_DEFORM')
        deform.deform_method = 'BEND'
        deform.deform_axis = 'Z'
        deform.angle = 0.8
        deform.origin = cup
    elif case == 'hidden':
        cup.hide_set(True)
    elif case == 'unselectable':
        cup.hide_select = True
    elif case == 'locked':
        cup.lock_location[0] = True
    elif case in ('hidden_collection', 'unselectable_collection'):
        collection = bpy.data.collections.new('Restricted')
        bpy.context.scene.collection.children.link(collection)
        for previous in list(cup.users_collection):
            previous.objects.unlink(cup)
        collection.objects.link(cup)
        if case == 'hidden_collection':
            collection.hide_render = True
        else:
            collection.hide_select = True
    elif case == 'fixed_unselectable_collection':
        table = bpy.data.objects['Counter']
        collection = bpy.data.collections.new('Fixed structure')
        bpy.context.scene.collection.children.link(collection)
        for previous in list(table.users_collection):
            previous.objects.unlink(table)
        collection.objects.link(table)
        collection.hide_select = True
        table.hide_select = True
    elif case == 'material_swapped':
        cup.data = cup.data.copy()
        cup.data.polygons[0].material_index = 0
        cup.data.polygons[1].material_index = 1
    elif case == 'other_scene_helper':
        construction = bpy.data.scenes.new('Construction')
        helper = cup.copy()
        helper.name = 'Hidden construction mesh'
        del helper['photo_scene_id']
        helper.hide_viewport = True
        helper.hide_render = True
        construction.collection.objects.link(helper)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / (case + '.blend')))
'''


@unittest.skipUnless(BLENDER, "需要 Blender 可执行文件（可设置 BLENDER_EXECUTABLE）")
class SceneValidationTests(unittest.TestCase):
    @classmethod
    def blender(cls, script, *arguments, success=True):
        command = [BLENDER, "--background", "--factory-startup", "--python-exit-code", "1",
                   "--python", str(script), "--", *map(str, arguments)]
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        if success and result.returncode != 0:
            raise AssertionError(f"Blender 执行失败 ({result.returncode}):\n{result.stdout}")
        return result

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="blender-photo-scene-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        generator = cls.directory / "fixture.py"
        generator.write_text(FIXTURE, encoding="utf-8")
        cls.blender(generator, cls.directory)
        cls.manifest_data = {
            "schema_version": 1, "reference_image": "synthetic.png", "photo_camera": "PhotoCamera",
            "items": [dict(id=item_id, name=name, object=name, movable=movable,
                           reference_region=[0, 0, 1, 1], dimensions_basis="合成场景已知尺寸",
                           source={"type": "modeled"})
                      for item_id, name, movable in [("cup_01", "Cup", True),
                                                     ("cup_02", "Cup2", True),
                                                     ("counter", "Counter", False)]],
        }
        cls.manifest = cls.directory / "manifest.json"
        cls.manifest.write_text(json.dumps(cls.manifest_data), encoding="utf-8")

    def verify(self, case="valid", manifest=None, baseline=None):
        input_path = self.directory / (case if case.endswith(".glb") else case + ".blend")
        output = self.directory / (case + ".report.json")
        arguments = ["--input", input_path, "--manifest", manifest or self.manifest, "--output", output]
        if baseline:
            arguments.extend(["--baseline", baseline])
        result = self.blender(SCRIPTS / "verify_scene.py", *arguments, success=False)
        self.assertTrue(output.exists(), result.stdout)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 0 if report["pass"] else 1, result.stdout)
        return report, output

    def test_independent_multimaterial_shared_mesh_and_restoration(self):
        report, _ = self.verify()
        self.assertTrue(report["pass"], report)
        self.assertEqual(len(report["items"]), 3)
        for item in report["items"]:
            if item["movable"]:
                self.assertEqual(item["materials"], ["Ceramic", "Label"])
                self.assertEqual([test["operation"] for test in item["movement_checks"]],
                                 ["translate", "rotate", "restore"])
                self.assertTrue(all(test["pass"] for test in item["movement_checks"]))

    def test_invalid_scenes_have_specific_failures(self):
        cases = {
            "missing": "missing_item", "mapping": "object_mapping_mismatch",
            "duplicate": "duplicate_id", "unregistered": "unregistered_mesh",
            "empty": "invalid_object_type", "nonfinite": "nonfinite_geometry",
            "degenerate": "degenerate_geometry", "zero_faces": "empty_geometry",
            "parent": "movement_affects_others", "constraint": "movement_not_rigid",
            "geometry_dependency": "movement_affects_others", "hidden": "hidden_item",
            "unselectable": "unselectable_item", "locked": "locked_transform",
            "hidden_collection": "hidden_item", "unselectable_collection": "unselectable_item",
            "missing_material": "missing_material_faces", "bad_material_index": "missing_material_faces",
        }
        for case, code in cases.items():
            with self.subTest(case=case):
                report, _ = self.verify(case)
                self.assertFalse(report["pass"], report)
                self.assertIn(code, [entry["code"] for entry in report["issues"]], report)

    def test_manifest_rejects_invalid_field_contract(self):
        changes = [
            lambda data: data.update(schema_version=2),
            lambda data: data.update(items=[]),
            lambda data: data["items"][0].update(movable="true"),
            lambda data: data["items"][0].update(reference_region=[0.8, 0, 0.2, 1]),
            lambda data: data["items"][0].update(source={"type": "public_asset"}),
            lambda data: data["items"][1].update(id="cup_01"),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                data = copy.deepcopy(self.manifest_data)
                change(data)
                path = self.directory / f"invalid-manifest-{index}.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                report, _ = self.verify(manifest=path)
                self.assertFalse(report["pass"], report)
                self.assertEqual(report["issues"][0]["code"], "validation_error")

    def test_glb_export_roundtrip_and_changed_baseline(self):
        glb = self.directory / "scene.glb"
        baseline = self.directory / "scene.authored.json"
        self.blender(SCRIPTS / "export_scene.py", "--input", self.directory / "valid.blend",
                     "--manifest", self.manifest, "--output", glb, "--report", baseline)
        authored = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertTrue(authored["pass"], authored)
        self.assertEqual(authored["mode"], "authored")
        self.assertEqual(authored["input"], str((self.directory / "valid.blend").resolve()))
        for item in authored["items"]:
            if item["movable"]:
                self.assertTrue(item["movement_checks"])
                self.assertTrue(all(check["pass"] for check in item["movement_checks"]))
        report, _ = self.verify("scene.glb", baseline=baseline)
        self.assertTrue(report["pass"], report)
        self.assertEqual({item["id"] for item in report["items"]}, {"cup_01", "cup_02", "counter"})
        changed = copy.deepcopy(authored)
        changed["items"][0]["dimensions"][0] += 1
        changed["items"][0]["matrix_world"][3] += 1
        changed["items"][0]["materials"].append("Missing material")
        changed_path = self.directory / "changed-baseline.json"
        changed_path.write_text(json.dumps(changed), encoding="utf-8")
        failed, _ = self.verify("scene.glb", baseline=changed_path)
        codes = {entry["code"] for entry in failed["issues"]}
        self.assertFalse(failed["pass"])
        self.assertTrue({"roundtrip_dimensions", "roundtrip_matrix_world", "roundtrip_materials"} <= codes)
        missing, _ = self.verify("scene.glb")
        self.assertIn("missing_baseline", {entry["code"] for entry in missing["issues"]})

    def test_export_rejects_invalid_source(self):
        glb = self.directory / "invalid.glb"
        baseline = self.directory / "invalid.authored.json"
        result = self.blender(SCRIPTS / "export_scene.py", "--input", self.directory / "parent.blend",
                              "--manifest", self.manifest, "--output", glb,
                              "--report", baseline, success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(glb.exists())
        report = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertFalse(report["pass"])
        self.assertIn("movement_affects_others", {entry["code"] for entry in report["issues"]})
        self.assertIn(str(baseline.resolve()), result.stdout)

    def test_export_report_cannot_overwrite_inputs_or_output(self):
        source = self.directory / "valid.blend"
        glb = self.directory / "report-collision.glb"
        glb.write_bytes(b"existing export")
        for report_path in (source, self.manifest, glb):
            with self.subTest(report=report_path.name):
                before = hashlib.sha256(report_path.read_bytes()).hexdigest()
                result = self.blender(SCRIPTS / "export_scene.py", "--input", source,
                                      "--manifest", self.manifest, "--output", glb,
                                      "--report", report_path, success=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(hashlib.sha256(report_path.read_bytes()).hexdigest(), before)

    def test_fixed_background_in_unselectable_collection_exports(self):
        glb = self.directory / "fixed-background.glb"
        baseline = self.directory / "fixed-background.authored.json"
        self.blender(SCRIPTS / "export_scene.py", "--input",
                     self.directory / "fixed_unselectable_collection.blend",
                     "--manifest", self.manifest, "--output", glb, "--report", baseline)
        report, _ = self.verify("fixed-background.glb", baseline=baseline)
        self.assertTrue(report["pass"], report)
        self.assertIn("counter", {item["id"] for item in report["items"]})

    def test_glb_rejects_changed_material_faces_with_same_slots(self):
        baseline = self.directory / "material-original.authored.json"
        self.blender(SCRIPTS / "export_scene.py", "--input", self.directory / "valid.blend",
                     "--manifest", self.manifest, "--output", self.directory / "material-original.glb",
                     "--report", baseline)
        glb = self.directory / "material-swapped.glb"
        self.blender(SCRIPTS / "export_scene.py", "--input",
                     self.directory / "material_swapped.blend",
                     "--manifest", self.manifest, "--output", glb,
                     "--report", self.directory / "material-swapped.authored.json")
        report, _ = self.verify("material-swapped.glb", baseline=baseline)
        self.assertFalse(report["pass"], report)
        codes = {entry["code"] for entry in report["issues"]}
        self.assertIn("roundtrip_material_regions", codes)
        self.assertNotIn("roundtrip_materials", codes)

    def test_other_scene_hidden_helper_does_not_affect_delivery(self):
        glb = self.directory / "other-scene-helper.glb"
        baseline = self.directory / "other-scene-helper.authored.json"
        self.blender(SCRIPTS / "export_scene.py", "--input",
                     self.directory / "other_scene_helper.blend",
                     "--manifest", self.manifest, "--output", glb, "--report", baseline)
        authored = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertTrue(authored["pass"], authored)
        self.assertEqual(len(authored["items"]), 3)
        report, _ = self.verify("other-scene-helper.glb", baseline=baseline)
        self.assertTrue(report["pass"], report)
        self.assertEqual(len(report["items"]), 3)

    def test_report_cannot_overwrite_input_or_manifest(self):
        source = self.directory / "valid.blend"
        for output in (source, self.manifest):
            with self.subTest(output=output.name):
                before = hashlib.sha256(output.read_bytes()).hexdigest()
                result = self.blender(SCRIPTS / "verify_scene.py", "--input", source,
                                      "--manifest", self.manifest, "--output", output,
                                      success=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
