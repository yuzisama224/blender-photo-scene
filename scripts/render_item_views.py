"""在独立 Blender 后台进程为指定 photo_scene_id 物件渲染七视图。"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


VIEWS = [
    ("perspective", (1.4, -1.7, 1.2), False),
    ("front", (0, -1, 0), True),
    ("back", (0, 1, 0), True),
    ("left", (-1, 0, 0), True),
    ("right", (1, 0, 0), True),
    ("top", (0, 0, 1), True),
    ("bottom", (0, 0, -1), True),
]


def look_at(obj, target):
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def isolate_item(input_path: Path, item_id: str):
    if not bpy.app.background:
        raise RuntimeError("请在独立 Blender --background --factory-startup 进程执行")
    if input_path.suffix.lower() != ".blend":
        raise ValueError("输入必须为 .blend")
    bpy.ops.wm.open_mainfile(filepath=str(input_path))
    matches = [obj for obj in bpy.context.scene.objects if obj.get("photo_scene_id") == item_id]
    if len(matches) != 1 or matches[0].type != "MESH":
        raise ValueError(f"photo_scene_id={item_id} 必须对应唯一 MESH 物件，当前匹配 {len(matches)} 个")
    source = matches[0]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
    if not mesh.vertices or not mesh.polygons:
        raise ValueError(f"{item_id}：求值后的网格为空")
    # 复制求值形状可保留 modifier / constraint 的最终姿态，同时隔离室内遮挡。
    isolated = bpy.data.objects.new(f"Item_{item_id}", mesh)
    isolated.matrix_world = evaluated.matrix_world.copy()
    for index, slot in enumerate(evaluated.material_slots):
        mesh.materials[index] = slot.material
    scene = bpy.data.scenes.new("PhotoSceneItemViews")
    scene.collection.objects.link(isolated)
    bpy.context.window.scene = scene
    points = [isolated.matrix_world @ vertex.co for vertex in mesh.vertices]
    minimum = Vector([min(point[axis] for point in points) for axis in range(3)])
    maximum = Vector([max(point[axis] for point in points) for axis in range(3)])
    return scene, minimum, maximum, source.name


def configure_scene(scene, center, extent, resolution):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "AgX"
    scene.world = bpy.data.worlds.new("PhotoSceneItemWorld")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.3, 0.3, 0.3, 1)
    background.inputs["Strength"].default_value = 0.6
    # 六个方向均有照明，底部和背面的证据可直接检查。
    for name, direction, _ in VIEWS[1:]:
        data = bpy.data.lights.new(f"ItemLight_{name}", type="AREA")
        data.energy = 50 * extent ** 2
        data.shape = "DISK"
        data.size = extent * 2
        light = bpy.data.objects.new(data.name, data)
        scene.collection.objects.link(light)
        light.location = center + Vector(direction) * extent * 2.5
        look_at(light, center)
    camera_data = bpy.data.cameras.new("PhotoSceneItemCamera")
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    return camera


def render_view(scene, camera, name, direction, center, dimensions, output_dir, orthographic):
    extent = max(dimensions)
    camera.data.type = "ORTHO" if orthographic else "PERSP"
    camera.data.lens = 55
    radius = dimensions.length / 2
    distance = radius / math.sin(camera.data.angle / 2) * 1.25
    camera.location = center + Vector(direction).normalized() * distance
    camera.data.clip_start = extent * 0.001
    camera.data.clip_end = distance + extent * 5
    camera.data.ortho_scale = extent * 1.25
    look_at(camera, center)
    scene.render.filepath = str(output_dir / f"{name}.png")
    bpy.ops.render.render(write_still=True, scene=scene.name)
    return Path(scene.render.filepath)


def create_contact_sheet(paths, output_path, resolution):
    columns, rows = 3, math.ceil(len(paths) / 3)
    sheet = np.zeros((rows * resolution, columns * resolution, 4), dtype=np.float32)
    for index, path in enumerate(paths):
        image = bpy.data.images.load(str(path), check_existing=False)
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        row, column = rows - 1 - index // columns, index % columns
        sheet[row * resolution:(row + 1) * resolution, column * resolution:(column + 1) * resolution] = pixels.reshape((resolution, resolution, 4))
        bpy.data.images.remove(image)
    output = bpy.data.images.new("PhotoSceneItemContactSheet", width=columns * resolution, height=rows * resolution, alpha=True)
    output.pixels.foreach_set(sheet.ravel())
    output.filepath_raw, output.file_format = str(output_path), "PNG"
    output.save()
    bpy.data.images.remove(output)


def render_item(input_path, item_id, output_dir, resolution):
    if resolution < 32:
        raise ValueError("resolution 至少为 32")
    scene, minimum, maximum, source_name = isolate_item(input_path, item_id)
    dimensions = maximum - minimum
    if max(dimensions) <= 0:
        raise ValueError("物件尺寸为空")
    center = (minimum + maximum) / 2
    camera = configure_scene(scene, center, max(dimensions), resolution)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [render_view(scene, camera, name, direction, center, dimensions, output_dir, ortho) for name, direction, ortho in VIEWS]
    contact_sheet = output_dir / "contact_sheet.png"
    create_contact_sheet(paths, contact_sheet, resolution)
    report = {
        "schema": "photo_scene_item_views.v1",
        "input": str(input_path),
        "item_id": item_id,
        "source_object": source_name,
        "blender_version": bpy.app.version_string,
        "resolution": resolution,
        "bounds": {"min": list(minimum), "max": list(maximum), "dimensions": list(dimensions)},
        "view_axes": "world: front=-Y, back=+Y, left=-X, right=+X, top=+Z, bottom=-Z",
        "views": {path.stem: str(path) for path in paths},
        "contact_sheet": str(contact_sheet),
    }
    (output_dir / "evidence.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=384)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    render_item(args.input.resolve(), args.item_id, args.output_dir.resolve(), args.resolution)
    print(f"PHOTO_SCENE_ITEM_VIEWS={args.output_dir.resolve() / 'evidence.json'}")


if __name__ == "__main__":
    main()
