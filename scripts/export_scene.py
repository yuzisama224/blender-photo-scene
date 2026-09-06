#!/usr/bin/env python3
"""后台验证作者场景后，将登记的独立网格导出为含物品 ID 的 GLB。"""

import argparse
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_scene import load_scene, read_manifest, validate_scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    if not bpy.app.background:
        raise ValueError("导出仅允许在 Blender 后台进程运行")
    if Path(args.input).suffix.lower() != ".blend" or Path(args.output).suffix.lower() != ".glb":
        raise ValueError("导出需要 .blend 输入和 .glb 输出")
    output = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    if report_path in {Path(args.input).resolve(), Path(args.manifest).resolve(), output}:
        raise ValueError("验证报告路径必须与输入场景、物品清单和输出 GLB 分别保存")
    manifest = read_manifest(args.manifest)
    load_scene(args.input)
    report = validate_scene(manifest)
    report["input"] = str(Path(args.input).resolve())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                           encoding="utf-8")
    if not report["pass"]:
        raise ValueError(f"作者场景检查未通过，请查看报告：{report_path}")
    # 固定背景允许禁选；后台进程临时解锁集合，确保登记网格全部进入导出选集。
    def unlock_collection(layer):
        layer.collection.hide_select = False
        for child in layer.children:
            unlock_collection(child)

    unlock_collection(bpy.context.view_layer.layer_collection)
    bpy.ops.object.select_all(action="DESELECT")
    for item in manifest["items"]:
        obj = bpy.context.scene.objects[item["object"]]
        obj.hide_set(False)
        obj.hide_select = False
        obj.select_set(True)
    selected_ids = {obj.get("photo_scene_id") for obj in bpy.context.selected_objects}
    if selected_ids != {item["id"] for item in manifest["items"]}:
        raise RuntimeError("导出选集与物品清单不一致")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.export_scene.gltf(
        filepath=str(output), check_existing=False, export_format="GLB",
        use_selection=True, export_extras=True, export_apply=True,
        export_materials="EXPORT", export_cameras=False, export_lights=False,
        export_animations=False, export_skins=False, export_morph=False,
        export_yup=True,
    )
    if "FINISHED" not in result or not output.is_file():
        raise RuntimeError("GLB 导出未完成")
    print(json.dumps({"exported": str(output), "report": str(report_path),
                      "items": len(manifest["items"])}))


if __name__ == "__main__":
    main()
