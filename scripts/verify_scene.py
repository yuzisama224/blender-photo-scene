#!/usr/bin/env python3
"""在独立 Blender 后台进程中检查清单、几何、逐件移动及 GLB 回读。"""

import argparse
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector


def issue(issues, code, message, item_id=None):
    issues.append({"code": code, "item_id": item_id, "message": message})


def read_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("schema_version 必须为 1")
    for field in ("reference_image", "photo_camera"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"{field} 必须为非空字符串")
    if not isinstance(manifest.get("items"), list) or not manifest["items"]:
        raise ValueError("items 必须为非空数组")
    ids, names = set(), set()
    for item in manifest["items"]:
        for field in ("id", "name", "object", "dimensions_basis"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"物品 {field} 必须为非空字符串")
        if item["id"] in ids or item["object"] in names:
            raise ValueError("清单中的 id 和 object 必须唯一")
        ids.add(item["id"])
        names.add(item["object"])
        if type(item.get("movable")) is not bool:
            raise ValueError(f"{item['id']} 的 movable 必须为布尔值")
        region = item.get("reference_region")
        if (not isinstance(region, list) or len(region) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       or not 0 <= v <= 1 for v in region)
                or region[0] >= region[2] or region[1] >= region[3]):
            raise ValueError(f"{item['id']} 的 reference_region 必须为归一化非空矩形")
        source = item.get("source", {})
        if source.get("type") not in ("modeled", "public_asset"):
            raise ValueError(f"{item['id']} 的素材来源类型无效")
        if source["type"] == "public_asset":
            for field in ("url", "license", "attribution"):
                if not isinstance(source.get(field), str) or not source[field].strip():
                    raise ValueError(f"{item['id']} 的公开素材必须记录 {field}")
    return manifest


def load_scene(path):
    path = Path(path).resolve()
    if path.suffix.lower() == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path))
        return "authored"
    if path.suffix.lower() == ".glb":
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(path))
        return "roundtrip"
    raise ValueError("input 必须为 .blend 或 .glb")


def matrix_values(matrix):
    return [float(v) for row in matrix for v in row]


def snapshot(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    state = {"matrix": evaluated.matrix_world.copy(), "vertices": []}
    if evaluated.type == "MESH":
        mesh = evaluated.to_mesh()
        try:
            state["vertices"] = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        finally:
            evaluated.to_mesh_clear()
    return state


def states_close(left, right, tolerance, transform=None):
    transform = transform if transform is not None else Matrix.Identity(4)
    expected_matrix = transform @ left["matrix"]
    if any(abs(a - b) > tolerance for a, b in zip(
            matrix_values(expected_matrix), matrix_values(right["matrix"]))):
        return False
    if len(left["vertices"]) != len(right["vertices"]):
        return False
    return all(((transform @ a) - b).length <= tolerance
               for a, b in zip(left["vertices"], right["vertices"]))


def geometry_evidence(obj, item, issues):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        coordinates = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        finite = all(math.isfinite(v) for point in coordinates for v in point)
        finite = finite and all(math.isfinite(v) for v in matrix_values(evaluated.matrix_world))
        if not finite:
            issue(issues, "nonfinite_geometry", "几何或变换包含非有限数值", item["id"])
            return None
        if not mesh.polygons or not coordinates:
            issue(issues, "empty_geometry", "物品必须具有顶点和面", item["id"])
            return None
        lower = Vector([min(point[i] for point in coordinates) for i in range(3)])
        upper = Vector([max(point[i] for point in coordinates) for i in range(3)])
        mesh.calc_loop_triangles()
        threshold = max((upper - lower).length_squared * 1e-12, 1e-18)
        materials = [material.name if material else None for material in mesh.materials]
        areas = [0.0] * len(mesh.polygons)
        material_areas = [0.0] * len(materials)
        material_centers = [Vector((0, 0, 0)) for _ in materials]
        for triangle in mesh.loop_triangles:
            a, b, c = [coordinates[index] for index in triangle.vertices]
            area = (b - a).cross(c - a).length / 2
            areas[triangle.polygon_index] += area
            slot = mesh.polygons[triangle.polygon_index].material_index
            if slot < len(materials):
                material_areas[slot] += area
                material_centers[slot] += (a + b + c) * (area / 3)
        if any(area <= threshold for area in areas):
            issue(issues, "degenerate_geometry", "物品包含零面积或退化面", item["id"])
        if any(material is None for material in materials):
            issue(issues, "empty_material_slot", "材质槽需要有效材质", item["id"])
        if any(polygon.material_index >= len(materials)
               or materials[polygon.material_index] is None for polygon in mesh.polygons):
            issue(issues, "missing_material_faces", "每个面需要分配有效材质", item["id"])
        used = {polygon.material_index for polygon in mesh.polygons}
        if any(index not in used for index in range(len(materials))):
            issue(issues, "unused_material_slot", "导出前需要清理未使用材质槽", item["id"])
        material_regions = [
            {"material": material, "area": area,
             "centroid": list(center / area) if area > 0 else None}
            for material, area, center in zip(materials, material_areas, material_centers)
        ]
        return {
            "id": item["id"], "object": obj.name, "movable": item["movable"],
            "dimensions": list(upper - lower), "center": list((upper + lower) / 2),
            "matrix_world": matrix_values(evaluated.matrix_world),
            "materials": materials, "material_regions": material_regions,
            "vertex_count": len(mesh.vertices),
            "polygon_count": len(mesh.polygons), "movement_checks": [],
        }
    finally:
        evaluated.to_mesh_clear()


def check_movement(obj, item, evidence, issues):
    others = [other for other in bpy.context.scene.objects if other != obj]
    original_basis = obj.matrix_basis.copy()
    original_parent_inverse = obj.matrix_parent_inverse.copy()
    before = snapshot(obj)
    other_states = {other.name: snapshot(other) for other in others}
    extent = max(max(evidence["dimensions"]), 0.01)
    tolerance = max(extent * 1e-5, 1e-6)
    origin = before["matrix"].translation
    deltas = [
        ("translate", Matrix.Translation(Vector((0.37, -0.29, 0.23)) * extent)),
        ("rotate", Matrix.Translation(origin)
         @ Matrix.Rotation(math.radians(23), 4, Vector((1, 2, 3)).normalized())
         @ Matrix.Translation(-origin)),
    ]
    try:
        for operation, delta in deltas:
            obj.matrix_world = delta @ before["matrix"]
            bpy.context.view_layer.update()
            moves_correctly = states_close(before, snapshot(obj), tolerance, delta)
            changed = [other.name for other in others
                       if not states_close(other_states[other.name], snapshot(other), tolerance)]
            if not moves_correctly:
                issue(issues, "movement_not_rigid", f"{operation} 时物品未按预期整体移动", item["id"])
            if changed:
                issue(issues, "movement_affects_others",
                      f"{operation} 牵连对象：{', '.join(changed)}", item["id"])
            evidence["movement_checks"].append({
                "operation": operation, "pass": moves_correctly and not changed,
                "affected_objects": changed,
            })
            obj.matrix_parent_inverse = original_parent_inverse
            obj.matrix_basis = original_basis
            bpy.context.view_layer.update()
    finally:
        obj.matrix_parent_inverse = original_parent_inverse
        obj.matrix_basis = original_basis
        bpy.context.view_layer.update()
    restored = states_close(before, snapshot(obj), tolerance) and all(
        states_close(other_states[other.name], snapshot(other), tolerance) for other in others)
    evidence["movement_checks"].append({"operation": "restore", "pass": restored})
    if not restored:
        issue(issues, "restore_failed", "测试后场景未恢复原始状态", item["id"])


def compare_baseline(report, baseline):
    issues = report["issues"]
    if baseline.get("mode") != "authored" or baseline.get("pass") is not True:
        issue(issues, "invalid_baseline", "回读对照需要通过检查的作者场景报告")
        return
    expected = {item["id"]: item for item in baseline["items"]}
    actual = {item["id"]: item for item in report["items"]}
    if set(expected) != set(actual):
        issue(issues, "roundtrip_item_set", "GLB 物品 ID 集合与作者场景不一致")
    for item_id in expected.keys() & actual.keys():
        before, after = expected[item_id], actual[item_id]
        tolerance = max(max(before["dimensions"]) * 1e-4, 1e-5)
        for field in ("dimensions", "center", "matrix_world"):
            if len(before[field]) != len(after[field]) or any(
                    abs(a - b) > tolerance for a, b in zip(before[field], after[field])):
                issue(issues, "roundtrip_" + field, f"GLB 回读的 {field} 与作者场景不一致", item_id)
        if before["materials"] != after["materials"]:
            issue(issues, "roundtrip_materials", "GLB 材质名称、槽数量或顺序不一致", item_id)
        before_regions, after_regions = before["material_regions"], after["material_regions"]
        regions_match = len(before_regions) == len(after_regions)
        for source, imported in zip(before_regions, after_regions):
            area_tolerance = max(source["area"] * 1e-4,
                                 max(before["dimensions"]) ** 2 * 1e-8, 1e-10)
            if (source["material"] != imported["material"]
                    or abs(source["area"] - imported["area"]) > area_tolerance
                    or source["centroid"] is None or imported["centroid"] is None
                    or any(abs(a - b) > tolerance
                           for a, b in zip(source["centroid"], imported["centroid"]))):
                regions_match = False
        if not regions_match:
            issue(issues, "roundtrip_material_regions", "GLB 材质分区的表面积或表面质心不一致", item_id)


def collection_access():
    access = {}

    def visit(layer, selectable=True, renderable=True):
        collection = layer.collection
        selectable = (selectable and not layer.exclude and not layer.hide_viewport
                      and not collection.hide_viewport and not collection.hide_select)
        renderable = renderable and not layer.exclude and not collection.hide_render
        for obj in collection.objects:
            previous = access.get(obj.name, (False, False))
            access[obj.name] = (previous[0] or selectable, previous[1] or renderable)
        for child in layer.children:
            visit(child, selectable, renderable)

    visit(bpy.context.view_layer.layer_collection)
    return access


def validate_scene(manifest, mode="authored", baseline=None):
    report = {"schema_version": 1, "mode": mode, "blender_version": bpy.app.version_string,
              "pass": False, "issues": [], "items": []}
    issues = report["issues"]
    if mode == "authored":
        camera = bpy.context.scene.objects.get(manifest["photo_camera"])
        if camera is None or camera.type != "CAMERA":
            issue(issues, "missing_photo_camera", "清单中的照片匹配相机不存在")
    tagged = {}
    for obj in bpy.context.scene.objects:
        item_id = obj.get("photo_scene_id")
        if item_id is not None:
            tagged.setdefault(str(item_id), []).append(obj)
    expected_ids = {item["id"] for item in manifest["items"]}
    for item_id, objects in tagged.items():
        if len(objects) != 1:
            issue(issues, "duplicate_id", "多个对象使用同一个物品 ID", item_id)
        if item_id not in expected_ids:
            issue(issues, "unregistered_id", "对象的物品 ID 未登记", item_id)
    assigned = set()
    valid = []
    access = collection_access()
    for item in manifest["items"]:
        objects = tagged.get(item["id"], [])
        if not objects:
            issue(issues, "missing_item", "找不到物品 ID 对应的对象", item["id"])
            continue
        if len(objects) != 1:
            continue
        obj = objects[0]
        assigned.add(obj.name)
        if mode == "authored" and obj.name != item["object"]:
            issue(issues, "object_mapping_mismatch", "对象名称与清单不一致", item["id"])
        if obj.type != "MESH":
            issue(issues, "invalid_object_type", "每件物品必须对应一个 MESH 对象", item["id"])
            continue
        if mode == "authored":
            selectable, renderable = access.get(obj.name, (False, False))
            if not obj.visible_get() or obj.hide_render or not renderable:
                issue(issues, "hidden_item", "登记物品需要在视口和渲染中可见", item["id"])
            if item["movable"] and (obj.hide_select or not selectable):
                issue(issues, "unselectable_item", "可移动物品需要允许直接选中", item["id"])
            if item["movable"] and (any(obj.lock_location) or any(obj.lock_rotation)):
                issue(issues, "locked_transform", "可移动物品的平移和旋转轴需要解锁", item["id"])
        evidence = geometry_evidence(obj, item, issues)
        if evidence is not None:
            report["items"].append(evidence)
            valid.append((obj, item, evidence))
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and obj.name not in assigned:
            issue(issues, "unregistered_mesh", f"网格 {obj.name} 未唯一登记到物品清单")
    # 几何有效后才运行变换，避免无效数值污染依赖对象和测试证据。
    invalid_ids = {entry["item_id"] for entry in issues}
    for obj, item, evidence in valid:
        if item["movable"] and item["id"] not in invalid_ids:
            check_movement(obj, item, evidence, issues)
    if mode == "roundtrip":
        if baseline is None:
            issue(issues, "missing_baseline", "GLB 检查需要 --baseline 作者场景报告")
        else:
            compare_baseline(report, baseline)
    report["pass"] = not issues
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    output = Path(args.output)
    sources = [args.input, args.manifest] + ([args.baseline] if args.baseline else [])
    if any(output.resolve() == Path(source).resolve() for source in sources):
        print("验证报告路径必须与输入场景、物品清单和基线报告分别保存", file=sys.stderr)
        sys.exit(1)
    try:
        if not bpy.app.background:
            raise ValueError("验证仅允许在 Blender 后台进程运行")
        manifest = read_manifest(args.manifest)
        mode = load_scene(args.input)
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
        report = validate_scene(manifest, mode, baseline)
    except Exception as error:
        report = {"schema_version": 1, "pass": False, "items": [],
                  "issues": [{"code": "validation_error", "item_id": None, "message": str(error)}]}
    report["input"] = str(Path(args.input).resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "report": str(output), "issues": len(report["issues"])}))
    if not report["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
