"""在独立 Blender 后台进程检查 .blend / .glb，输出网格与材质指标。"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy


def load_asset(path: Path):
    if not bpy.app.background:
        raise RuntimeError("请在独立 Blender --background --factory-startup 进程执行")
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path))
    elif path.suffix.lower() == ".glb":
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise ValueError("输入格式允许 .blend 或 .glb")


def finite_vector(values):
    return all(math.isfinite(float(value)) for value in values)


def connected_components(mesh):
    adjacency = [[] for _ in mesh.vertices]
    for edge in mesh.edges:
        first, second = edge.vertices
        adjacency[first].append(second)
        adjacency[second].append(first)
    visited = set()
    components = 0
    for start in range(len(mesh.vertices)):
        if start in visited:
            continue
        components += 1
        pending = [start]
        visited.add(start)
        while pending:
            for neighbor in adjacency[pending.pop()]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append(neighbor)
    return components


def inspect_mesh(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        mesh.calc_loop_triangles()
        invalid_vertices = sum(not finite_vector(vertex.co) for vertex in mesh.vertices)
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            topology = {
                "boundary_edges": sum(edge.is_boundary for edge in bm.edges),
                "non_manifold_edges": sum(not edge.is_manifold for edge in bm.edges),
                "wire_edges": sum(edge.is_wire for edge in bm.edges),
                "isolated_vertices": sum(not vertex.link_edges for vertex in bm.verts),
            }
        finally:
            bm.free()
        materials = [slot.material.name if slot.material else None for slot in evaluated.material_slots]
        world_points = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        bounds = None
        if world_points and all(finite_vector(point) for point in world_points):
            bounds = [[round(function(point[axis] for point in world_points), 6) for axis in range(3)] for function in (min, max)]
        smooth = sum(polygon.use_smooth for polygon in mesh.polygons)
        return {
            "name": obj.name,
            "photo_scene_id": obj.get("photo_scene_id"),
            "parent": obj.parent.name if obj.parent else None,
            "source_vertices": len(obj.data.vertices),
            "source_polygons": len(obj.data.polygons),
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
            "triangles": len(mesh.loop_triangles),
            "connected_components": connected_components(mesh),
            "invalid_vertices": invalid_vertices,
            "degenerate_faces": sum(polygon.area <= 1e-12 for polygon in mesh.polygons),
            "zero_length_edges": sum((mesh.vertices[edge.vertices[0]].co - mesh.vertices[edge.vertices[1]].co).length <= 1e-9 for edge in mesh.edges),
            **topology,
            "uv_layers": len(mesh.uv_layers),
            "smooth_polygons": smooth,
            "flat_polygons": len(mesh.polygons) - smooth,
            "material_slots": materials,
            "missing_material_faces": sum(polygon.material_index >= len(materials) or materials[polygon.material_index] is None for polygon in mesh.polygons),
            "modifiers": [{"name": modifier.name, "type": modifier.type} for modifier in obj.modifiers],
            "transform_is_finite": all(finite_vector(row) for row in evaluated.matrix_world),
            "world_bounds": bounds,
        }
    finally:
        evaluated.to_mesh_clear()


def inspect_material(material):
    nodes = list(material.node_tree.nodes) if material.node_tree else []
    textures = [node for node in nodes if node.type == "TEX_IMAGE"]
    return {
        "name": material.name,
        "use_nodes": bool(material.node_tree),
        "node_count": len(nodes),
        "image_texture_nodes": len(textures),
        "images": sorted({node.image.name for node in textures if node.image}),
        "empty_image_texture_nodes": sum(node.image is None for node in textures),
    }


def inspect_scene(input_path: Path):
    load_asset(input_path)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh_objects = sorted((obj for obj in bpy.context.scene.objects if obj.type == "MESH"), key=lambda obj: obj.name)
    meshes = [inspect_mesh(obj, depsgraph) for obj in mesh_objects]
    materials = {slot.material for obj in mesh_objects for slot in obj.material_slots if slot.material}
    count_fields = ("vertices", "edges", "polygons", "triangles", "invalid_vertices", "degenerate_faces", "zero_length_edges", "boundary_edges", "non_manifold_edges", "wire_edges", "isolated_vertices", "missing_material_faces")
    totals = {key: sum(mesh[key] for mesh in meshes) for key in count_fields}
    totals.update(mesh_objects=len(meshes), materials=len(materials))
    issues = []
    if not totals["triangles"]:
        issues.append({"severity": "gate", "code": "empty_mesh"})
    if totals["invalid_vertices"]:
        issues.append({"severity": "gate", "code": "invalid_vertices", "count": totals["invalid_vertices"]})
    if any(not mesh["transform_is_finite"] for mesh in meshes):
        issues.append({"severity": "gate", "code": "invalid_object_transform"})
    for key in ("degenerate_faces", "zero_length_edges", "boundary_edges", "non_manifold_edges", "wire_edges", "isolated_vertices", "missing_material_faces"):
        if totals[key]:
            issues.append({"severity": "warning", "code": key, "count": totals[key]})
    return {
        "schema": "photo_scene_asset.v1",
        "input": str(input_path),
        "blender_version": bpy.app.version_string,
        "frame": bpy.context.scene.frame_current,
        "totals": totals,
        "meshes": meshes,
        "material_details": [inspect_material(material) for material in sorted(materials, key=lambda item: item.name)],
        "issues": issues,
        "hard_gate_pass": not any(issue["severity"] == "gate" for issue in issues),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    if args.output.resolve() == args.input.resolve():
        parser.error("指标输出路径必须与输入资产不同")
    report = inspect_scene(args.input.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"PHOTO_SCENE_METRICS={args.output.resolve()}")


if __name__ == "__main__":
    main()
