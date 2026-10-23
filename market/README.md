# 市场卡片条目（投给 awesome-dsh-plugin 的 PR 内容）

这个目录不属于 npm 包（`files` 白名单里没有它），只是把要提交的内容存在仓库里，方便直接复制。

## 先说结论：只加图标，不动描述

查了 `data/plugins/TreeCodeTyper__dsh-blender-mc.yml` 的提交历史：

| 提交 | 时间 | 说明 |
|---|---|---|
| `49af1db` | 10-20 20:32 | 你自己的 PR #7656 |
| `37e12fb` | 10-20 20:32 | 维护者 fkysly 合并，并顺手「加前置条件、删没实现的功能」 |
| `45da6a6` | 10-22 18:59 | 维护者再次收录（#8310），描述被改成更保守的一版 |
| `a08a505` | 10-22 19:02 | **三分钟后被 revert**，恢复成原来那段长描述 |

也就是说：**这个条目的描述是维护者在手工把关的，改描述大概率会被打回**。
所以这次 PR 只做一件事 —— 加 `icon`（当前文件没写，卡片回退成作者头像，在 854 个 tools 里很难被认出来）。

## 要提交的文件

路径：`data/plugins/TreeCodeTyper__dsh-blender-mc.yml`（**已存在，做修改**，只多一行 `icon`）

```yaml
url: https://github.com/TreeCodeTyper/dsh-blender-mc
name: TreeCodeTyper/dsh-blender-mc
category: tools
description:
  en: 'Turns a Minecraft Java save into a Blender cover or animation: scans the .mca region files for flat open ground, exports the terrain to OBJ, and drives Blender headless with MCprep materials, the BPS character rig and three-point lighting. After each render it reviews the frame with OpenCV for facing, ground contact, exposure, lighting and composition, then re-renders with adjusted parameters when a check fails. Requires Blender 4.2 LTS or newer and Python 3.10+ with opencv-python and numpy; the first run assembles the Minecraft assets with the bundled setup command.'
  zh: '把 Minecraft Java 存档变成 Blender 封面或动画：在 .mca 区域文件里找大片平地，导出地形为 OBJ，无头驱动 Blender（MCprep 材质、BPS 角色骨架、三点布光）。每次渲染后用 OpenCV 检查朝向、接地、曝光、布光和构图，不合格就调整参数重渲。需 Blender 4.2 LTS 以上、Python 3.10+（opencv-python、numpy），首次使用运行 setup 装配 Minecraft 素材。'
icon: https://raw.githubusercontent.com/TreeCodeTyper/dsh-blender-mc/main/assets/icon-180.png
```

（`description` 两行保持原样，一个字都不动。）

## PR 说明（可直接粘贴）

```text
Add storefront icon for dsh-blender-mc

Only adds the optional `icon` field so the card uses a rendered cover
instead of falling back to the owner avatar. `url`, `category` and both
description lines are unchanged.
```

## 步骤（全程网页，不用 clone）

1. 打开 https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/blob/main/data/plugins/TreeCodeTyper__dsh-blender-mc.yml
2. 右上角铅笔（Edit this file）→ 在最后一行下面加一行 `icon: ...`（内容见上）
3. 「Commit changes」→ 选 “Create a new branch for this commit and start a pull request”
4. 标题填 `Add storefront icon for dsh-blender-mc`，正文粘上面那段 → Create pull request

## 注意

- 版本号（`version`）**不用**在这里改 —— 市场目录会自己从 npm 抓，npm 已经是 `0.3.1`。
- `icon` 指向 `main` 分支的 `assets/icon-180.png`，主仓库已推送，图已验证可访问（200 / 28.6 kB）。
- 如果维护者这次愿意听，可以顺带在 PR 评论里提一句：描述里「Requires Blender 4.2 LTS or newer」是准确的，但 0.3.x 起还多了「可在设置页上传地图 zip 与人物皮肤」。**不要直接改描述**，让他决定。
