# 市场卡片条目（投给 awesome-dsh-plugin 的 PR 内容）

这个目录不属于 npm 包（`files` 白名单里没有它），只是把要提交的内容存在仓库里，方便直接复制。

## 要提交的文件

路径：`data/plugins/TreeCodeTyper__dsh-blender-mc.yml`（该文件在 awesome-dsh-plugin 里已存在，做**修改**而不是新增）

```yaml
url: https://github.com/TreeCodeTyper/dsh-blender-mc
name: TreeCodeTyper/dsh-blender-mc
category: tools
description:
  en: 'Turns a Minecraft Java save into a Blender cover or a 10-second animation: picks the camera spot, exports the terrain, sets up lighting and materials, renders, then reviews the frame with OpenCV and re-renders until it passes. Upload maps and skins from the DSH settings page. Requires Blender 4.2 LTS and Python 3.10+.'
  zh: '把 Minecraft 存档变成 Blender 封面或 10 秒动画：自动挑机位、导出地形、布光配材质、渲染，再用 OpenCV 审图，不合格自动调参重渲。地图和人物皮肤都能在 DSH 设置页直接上传。需 Blender 4.2 LTS 与 Python 3.10+。'
icon: https://raw.githubusercontent.com/TreeCodeTyper/dsh-blender-mc/main/assets/icon-180.png
```

改了什么：

- **描述从 300+ 字压到一句**，第一句就说「存档 → 封面/动画」，把「审图自检」留在句中，装环境的要求挪到句尾。
- **加 `icon`**：原来没写，卡片回退成作者头像；现在用封面裁出的 180×180 方块图（`assets/icon-180.png`），市场里更容易被认出来。

## PR 说明（可直接粘贴）

```text
Update entry: shorter one-line description + storefront icon

- Trim the description to one sentence that leads with what it does
  (save → Blender cover / 10s animation) instead of the pipeline internals.
- Add `icon` (180×180 crop of a rendered cover) so the card no longer
  falls back to the owner avatar.

No url/category change.
```

## 步骤（全程网页，不用 clone）

1. 打开 https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/blob/main/data/plugins/TreeCodeTyper__dsh-blender-mc.yml
2. 右上角铅笔（Edit this file）→ 全选删除 → 粘贴上面那段 YAML
3. 「Commit changes」→ 选 “Create a new branch for this commit and start a pull request”
4. 标题填 `Update dsh-blender-mc: one-line description + icon`，正文粘上面那段 → Create pull request

## 注意

- 版本号（`version`）**不用**在这里改 —— 市场目录会自己从 npm 抓，npm 已经是 `0.3.1`。
- `icon` 指向 `main` 分支的 `assets/icon-180.png`，所以**先把主仓库 push 上去**，否则图挂。
