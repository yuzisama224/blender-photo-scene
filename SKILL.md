---
name: blender-photo-scene
description: 从照片在 Blender 中重建可编辑的写实场景，精细制作并组织可独立选中移动的小物品。用于照片场景还原、场景内道具细化、逐件移动验收和 Blender/GLB 交付。
---

# Blender 照片场景

把照片中的空间、固定设施和小物品制作成可编辑场景。照片决定内容与摆放；物品清单贯穿制作、独立移动和导出检查。默认支持近距离查看，每件小物品可在 Blender 对象模式直接选中、整体移动和旋转。

## 开始

1. 阅读 [执行约定](references/modules/execution.md)，用 [工具入口](references/scripts.md) 中的 `preflight.py` 检查运行环境。缺少 Blender 或当前步骤所需依赖时，先提示缺失项并询问是否安装，得到明确同意后安装。环境可用后，通过工具语义发现能力并实际调用 Blender MCP 只读场景查询。
2. 实际查看用户照片，确认目标文件和当前场景。默认交付 Blender 工程与 GLB；允许自行建模及免费、授权清楚的公开模型和材质。按需查询素材并保留来源及署名。
3. 阅读 [制作流程](references/workflow.md) 和 [物品清单](references/manifest.md)，建立当前任务的场景说明、物品清单和证据目录。此入口统一调度下面的技术模块，按到达的阶段读取。

## 按阶段读取

| 当前工作 | 模块 |
|---|---|
| 照片透视、空间结构与灰模 | [相机](references/modules/camera.md)、[建筑与空间](references/modules/archviz.md)、[通用建模](references/modules/modeling.md) |
| 小物品与完整移动单位 | [道具](references/modules/props.md)、[场景组织](references/modules/scene-assembly.md) |
| 材质与表面细节 | [材质](references/modules/materials.md)、[UV](references/modules/uv.md)、[贴图](references/modules/texture.md) |
| 照片外观与观察证据 | [灯光](references/modules/lighting.md)、[渲染](references/modules/rendering.md) |
| 明确需要重复结构或生成器 | [几何节点](references/modules/geometry-nodes.md)、[程序化建模](references/modules/procedural.md) |
| 交付检查 | [验收](references/modules/qa.md)、[导出](references/modules/export.md)、[工具入口](references/scripts.md) |

## 制作与完成

- 先相机、空间比例和物品位置，再形体、接合、UV、材质和近景细节。阶段检查采用 [验收](references/modules/qa.md) 的默认范围，复用有效图像，只复查受修改影响的内容。
- 每件清单物品对应一个带稳定 ID 的网格对象。固定设施也登记；重复小物品各自有对象变换。完整物品的多材质、网格岛和有意义的可编辑结构保留。
- 可移动物品具有合理的侧面、背面和底部，移开后暴露的承托面完整。照片无法证实的尺寸与隐藏结构记为推估。
- 默认自主推进。能合理推估且可逆的选择记入场景说明；缺失信息会显著改变结果时提出一个具体问题。
- 验收保留照片视角、清单覆盖、近景质量、完整移动和 GLB 回读。默认执行一次导出内验证和一次回读验证；视觉证据按代表性选取，额外诊断按需运行。机器报告与实际看图结论分别记录。
- 按 [制作流程](references/workflow.md) 保存阶段检查点。输入或执行条件阻碍继续改善时，交付可继续编辑的工程与明确的未完成项。

## 默认产物

以场景本身命名的 `.blend`、`.glb`、构建脚本、照片视角和物品细节图、物品与素材清单、中文验收记录。工程内打包必要图像，GLB 保留物品 ID 与独立变换。交付状态如实区分结构检查、视觉检查和未完成要求。

本包的来源、固定提交和适配文件见 [来源清单](UPSTREAM.json)，许可全文位于该清单链接的本地文件。包结构与工具自测见 [工具入口](references/scripts.md#包与工具自测)。
