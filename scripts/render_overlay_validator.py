#!/usr/bin/env python3
"""按明确的二值蒙版或 Canny 边缘模式比较同尺寸参考图与渲染图。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def read_mask(path: Path, mode: str):
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"无法读取图片：{path}")
    if mode == "mask":
        result = gray > 127
    elif mode == "canny":
        result = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 40, 120) > 0
    else:
        raise ValueError("mode 必须明确为 mask 或 canny")
    if not result.any():
        raise ValueError(f"{path}：{mode} 结果为空，无法计算有效对齐指标")
    return result


def bbox(mask):
    ys, xs = np.where(mask)
    return [int(xs.min()), int(ys.min()), int(np.ptp(xs) + 1), int(np.ptp(ys) + 1)]


def centroid(mask):
    ys, xs = np.where(mask)
    return [float(xs.mean()), float(ys.mean())]


def compare_masks(reference, rendered, mode: str):
    if reference.shape != rendered.shape:
        raise ValueError(f"图片尺寸必须一致：reference={reference.shape}, render={rendered.shape}；请修正相机输出尺寸")
    if not reference.any() or not rendered.any():
        raise ValueError("参考或渲染蒙版为空")
    intersection = np.logical_and(reference, rendered)
    union = np.logical_or(reference, rendered)
    ref_center, render_center = centroid(reference), centroid(rendered)
    drift = [render_center[axis] - ref_center[axis] for axis in (0, 1)]
    return {
        "mode": mode,
        "size_px": [reference.shape[1], reference.shape[0]],
        "iou": float(intersection.sum() / union.sum()),
        "mse": float(np.logical_xor(reference, rendered).mean()),
        "reference_foreground_ratio": float(reference.mean()),
        "render_foreground_ratio": float(rendered.mean()),
        "reference_bbox": bbox(reference),
        "render_bbox": bbox(rendered),
        "reference_centroid": ref_center,
        "render_centroid": render_center,
        "centroid_drift_px": drift,
        "centroid_distance_px": float(np.linalg.norm(drift)),
    }


def validate(reference_path: Path, render_path: Path, mode: str, output: Path, overlay: Path | None = None):
    reference, rendered = read_mask(reference_path, mode), read_mask(render_path, mode)
    report = compare_masks(reference, rendered, mode)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if overlay:
        image = np.zeros((*reference.shape, 3), np.uint8)
        image[reference & ~rendered] = (255, 255, 0)  # 参考独有区域：青色（BGR）。
        image[rendered & ~reference] = (0, 255, 255)  # 渲染独有区域：黄色。
        image[reference & rendered] = (0, 255, 0)
        overlay.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(overlay), image):
            raise OSError(f"无法写入叠加图：{overlay}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--render", type=Path, required=True)
    parser.add_argument("--mode", choices=("mask", "canny"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--overlay", type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.reference, args.render, args.mode, args.out, args.overlay)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
