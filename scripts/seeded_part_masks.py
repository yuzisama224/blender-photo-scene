#!/usr/bin/env python3
"""依据显式区域与颜色条件提取参考图部件蒙版。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


MODES = {"polygon", "bbox", "alpha", "dark_lines", "bright_on_dark", "hsv_range", "non_background"}


def read_image(path: Path):
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"无法读取图片：{path}")
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), None
    return image[:, :, :3], image[:, :, 3] if image.shape[2] == 4 else None


def region_mask(shape, part):
    height, width = shape[:2]
    mask = np.zeros((height, width), np.uint8)
    if "polygon" in part:
        points = np.asarray(part["polygon"], dtype=np.int32)
        if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3:
            raise ValueError(f"{part['name']}：polygon 需要至少三个 [x, y] 点")
        cv2.fillPoly(mask, [points], 255)
    elif "bbox" in part:
        x, y, box_width, box_height = map(int, part["bbox"])
        if box_width <= 0 or box_height <= 0:
            raise ValueError(f"{part['name']}：bbox 宽高必须为正数")
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + box_width), min(height, y + box_height)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = 255
    else:
        raise ValueError(f"{part['name']}：需要 polygon 或 bbox")
    return mask


def threshold_inside(bgr, alpha, base, part):
    mode = part.get("mode")
    if mode not in MODES:
        raise ValueError(f"{part['name']}：显式设置 mode，允许值为 {sorted(MODES)}")
    if mode in {"polygon", "bbox"}:
        if mode not in part:
            raise ValueError(f"{part['name']}：{mode} 模式需要同名区域字段")
        return base.copy()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    if mode == "alpha":
        if alpha is None:
            raise ValueError(f"{part['name']}：alpha 模式需要含透明通道的图片")
        selected = alpha > int(part.get("alpha_min", 8))
    elif mode == "dark_lines":
        selected = (hsv[:, :, 2] < part.get("value_max", 180)) & (hsv[:, :, 1] < part.get("sat_max", 160))
    elif mode == "bright_on_dark":
        selected = (hsv[:, :, 2] > part.get("value_min", 35)) & (hsv[:, :, 1] > part.get("sat_min", 25))
    elif mode == "hsv_range":
        selected = cv2.inRange(hsv, np.array(part["hsv_min"], np.uint8), np.array(part["hsv_max"], np.uint8)) > 0
    else:
        corners = np.array([bgr[0, 0], bgr[0, -1], bgr[-1, 0], bgr[-1, -1]], dtype=np.float32)
        background = np.median(corners, axis=0)
        selected = np.linalg.norm(bgr.astype(np.float32) - background, axis=2) > float(part.get("threshold", 18))
    mask = cv2.bitwise_and(base, selected.astype(np.uint8) * 255)
    kernel = np.ones((3, 3), np.uint8)
    for option, operation in (("close", cv2.MORPH_CLOSE), ("open", cv2.MORPH_OPEN)):
        if part.get(option, False):
            mask = cv2.morphologyEx(mask, operation, kernel)
    # 形态学运算仍应受用户圈定区域约束。
    return cv2.bitwise_and(mask, base)


def bbox_from_mask(mask):
    ys, xs = np.where(mask > 0)
    if not len(xs):
        raise ValueError("蒙版为空")
    return [int(xs.min()), int(ys.min()), int(np.ptp(xs) + 1), int(np.ptp(ys) + 1)]


def extract_parts(manifest_path: Path, output_dir: Path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    image_path = Path(manifest["image"])
    if not image_path.is_absolute():
        image_path = manifest_path.parent / image_path
    image_path = image_path.resolve()
    bgr, alpha = read_image(image_path)
    definitions = manifest.get("parts", [])
    if not definitions:
        raise ValueError("manifest.parts 至少包含一个部件")
    names = [part["name"] for part in definitions]
    if len(set(names)) != len(names) or any(not name or Path(name).name != name or name in {".", ".."} for name in names):
        raise ValueError("部件 name 必须为唯一文件名，不含目录")
    masks = []
    for part in definitions:
        mask = threshold_inside(bgr, alpha, region_mask(bgr.shape, part), part)
        area = int(np.count_nonzero(mask))
        minimum = max(1, int(part.get("min_area", 1)))
        if area < minimum:
            raise ValueError(f"{part['name']}：蒙版面积 {area} px 小于 min_area={minimum}；调整区域或阈值后重试")
        masks.append(mask)
    output_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for part, mask in zip(definitions, masks):
        mask_path = (output_dir / f"{part['name']}_mask.png").resolve()
        if not cv2.imwrite(str(mask_path), mask):
            raise OSError(f"无法写入蒙版：{mask_path}")
        parts.append({"name": part["name"], "mode": part["mode"], "area_px": int(np.count_nonzero(mask)), "bbox_px": bbox_from_mask(mask), "mask": str(mask_path)})
    report = {"schema": "photo_scene_parts.v1", "image": str(image_path), "size_px": [bgr.shape[1], bgr.shape[0]], "parts": parts}
    (output_dir / "part_inventory.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = extract_parts(args.manifest, args.out_dir)
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
