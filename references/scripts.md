# 工具入口

下列路径均相对 Skill 根目录。把 `SKILL_DIR` 设为实际安装位置，`BLENDER_EXECUTABLE` 设为环境检查确认的可执行路径，`TASK_DIR` 设为当前任务产物目录。命令中的场景名和物品 ID 使用实际任务值。

## 环境

优先使用已有的技能 Python 环境，否则用机器现有 Python 执行 `scripts/preflight.py`；已有 Blender 路径时传入 `--blender`，其余情况由预检发现。环境检查只读取运行时，MCP 连接仍需 Agent 实际调用只读场景查询确认。

```sh
python3 "$SKILL_DIR/scripts/preflight.py"
```

读取 `issues` 区分 Blender 缺失、启动失败和 Python 包缺失。缺少当前步骤必需项时，按 [安装确认](modules/execution.md#依赖与安装确认) 提示并询问用户；可选图像工具的依赖在启用对应工具时处理。连接或启动失败先诊断已有安装。

图像工具和包验证的 Python 依赖见 [requirements.txt](../requirements.txt)。获得用户对这些依赖安装的明确同意后，可在技能独立环境中安装；以下命令使用已有的 `uv`：

```sh
uv venv "$SKILL_DIR/.venv"
uv pip install --python "$SKILL_DIR/.venv/bin/python" -r "$SKILL_DIR/requirements.txt"
"$SKILL_DIR/.venv/bin/python" "$SKILL_DIR/scripts/preflight.py" --blender "$BLENDER_EXECUTABLE"
```

以下 `python` 表示已确认可用的环境解释器；Blender 脚本使用 Blender 内置 Python。`preflight.py` 的 `pass` 表示列出的本地依赖全部可用，各步骤按自身所需依赖及 MCP 状态判断能否继续。安装后只复查受影响依赖与连接。

## 默认执行范围

按 [验收](modules/qa.md) 选择检查范围。默认使用阶段预览、一次导出内作者验证和一次 GLB 回读；网格详查、遮罩叠图及独立七视图按具体疑点启用。完整日志和报告保存到证据目录，返回上下文的内容仅含退出状态、结论、异常摘要与路径。

## 局部遮罩与照片比较

需要定量定位已发现的轮廓或位置差异时使用。

`seeded_part_masks.py --manifest seeds.json --out-dir masks` 接收区域种子 JSON。`image` 相对种子文件，`parts` 中的 `name` 对应物品 ID。像素坐标由 Agent 对照原照片确认，必要时由物品清单归一化区域换算。

```json
{
  "image": "参考照片.jpg",
  "parts": [
    {"name": "item-001", "mode": "bbox", "bbox": [100, 150, 80, 120]},
    {"name": "item-002", "mode": "polygon", "polygon": [[30, 40], [70, 40], [60, 80]]}
  ]
}
```

区域遮罩只是人工或 Agent 指定的像素区域；需实际打开结果确认它确实覆盖目标物品，再用于对照。更细的轮廓遮罩可以由检查后的多边形指定。

```sh
python "$SKILL_DIR/scripts/seeded_part_masks.py" --manifest "$TASK_DIR/seeds.json" --out-dir "$TASK_DIR/evidence/masks"
python "$SKILL_DIR/scripts/render_overlay_validator.py" --reference "$TASK_DIR/reference-mask.png" --render "$TASK_DIR/render-mask.png" --mode mask --out "$TASK_DIR/evidence/overlay.json" --overlay "$TASK_DIR/evidence/overlay.png"
```

比较输入必须同尺寸、同视角和同一物品。`mask` 用于已确认的黑底白色前景遮罩，`canny` 用于两张图的边缘比较。输出 IoU、前景区域和质心位移，解释这些局部度量时同时查看源图、渲染和叠图；空结果或尺寸不一致会失败。

## 网格指标与独立移动

辅助脚本在新的后台 Blender 进程运行，使用 `--python-exit-code 1` 将脚本异常传回调用者。以下独立命令用于网格、UV 或移动问题诊断；默认作者验证由下文的导出器执行并保存报告。

```sh
"$BLENDER_EXECUTABLE" --background --factory-startup --python-exit-code 1 --python "$SKILL_DIR/scripts/inspect_asset.py" -- --input "$TASK_DIR/场景.blend" --output "$TASK_DIR/evidence/mesh-metrics.json"
"$BLENDER_EXECUTABLE" --background --factory-startup --python-exit-code 1 --python "$SKILL_DIR/scripts/verify_scene.py" -- --input "$TASK_DIR/场景.blend" --manifest "$TASK_DIR/scene-manifest.json" --output "$TASK_DIR/evidence/authored.json"
```

`inspect_asset.py` 按需输出通用网格、UV 和材质指标。边界边、连通网格岛等指标结合物品用途解释。

`verify_scene.py` 的 `pass`、`issues` 和逐项 `items` 是独立移动及结构结果。它检查清单对应、网格有效性、可选状态，实际执行移动、旋转和还原，并比较其他对象的求值状态。失败项通过 `item_id` 指向对应物品。完整清单定义见 [物品清单](manifest.md)。

## 独立物品视图

现有视口或近景不足以判断对应物品时使用；共享形体和材质的重复物品选代表件。

```sh
"$BLENDER_EXECUTABLE" --background --factory-startup --python-exit-code 1 --python "$SKILL_DIR/scripts/render_item_views.py" -- --input "$TASK_DIR/场景.blend" --item-id item-001 --output-dir "$TASK_DIR/evidence/item-001" --resolution 256
```

脚本依据 `photo_scene_id` 提取唯一网格的求值形体与材质，在隔离场景生成透视、前后左右、顶和底视图及接触表。先打开接触表，发现问题时再看对应单图或提高分辨率。原工程文件保持不变；整室照片匹配使用原场景的照片相机预览。

## 导出与新进程回读

```sh
"$BLENDER_EXECUTABLE" --background --factory-startup --python-exit-code 1 --python "$SKILL_DIR/scripts/export_scene.py" -- --input "$TASK_DIR/场景.blend" --manifest "$TASK_DIR/scene-manifest.json" --output "$TASK_DIR/场景.glb" --report "$TASK_DIR/evidence/authored.json"
"$BLENDER_EXECUTABLE" --background --factory-startup --python-exit-code 1 --python "$SKILL_DIR/scripts/verify_scene.py" -- --input "$TASK_DIR/场景.glb" --manifest "$TASK_DIR/scene-manifest.json" --baseline "$TASK_DIR/evidence/authored.json" --output "$TASK_DIR/evidence/roundtrip.json"
```

导出器先验证作者场景，将通过或失败的验证结果写入必需的 `--report` 路径；通过后仅导出已登记的网格并保存自定义 ID。GLB 回读直接以这份报告为基准，按 ID 比较尺寸、变换、材质槽，以及各材质覆盖的表面积和面积加权质心，再执行独立移动。这些几何量允许导出器三角化和拆分顶点，能发现材质换到不同表面的情况。查看回读总览，纹理或透明效果有疑点时补充近景。

## 包与工具自测

本节用于技能维护。修改技能文档时运行一次包结构检查；修改脚本时运行对应测试；用户要求完整工具验证时运行整套测试。照片制作任务按上文场景验收交付。

```sh
python "$SKILL_DIR/scripts/check_package.py"
python -m unittest discover -s "$SKILL_DIR/tests" -p 'test_*.py' -v
```

通过 `BLENDER_EXECUTABLE` 向集成测试提供实际 Blender 路径。测试只创建临时合成场景与图片；照片场景还原的精细度由真实任务中的视觉验收确认。测试结果应区分实际通过、失败和因缺失 Blender 而跳过的测试。
