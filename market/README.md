# 市场卡片条目（投给 awesome-dsh-plugin 的 PR 内容）

这个目录**不属于 npm 包**（`files` 白名单里没有它），只是把要提交的内容存在仓库里，方便直接复制粘贴。

## 背景：描述是被维护者把关的

查 `data/plugins/TreeCodeTyper__dsh-blender-mc.yml` 的提交历史：

| 提交 | 时间 | 说明 |
|---|---|---|
| `49af1db` | 10-20 20:32 | 作者自己的 PR #7656 |
| `37e12fb` | 10-20 20:32 | 维护者 fkysly 合并，顺手「加前置条件、删没实现的功能」 |
| `45da6a6` | 10-22 18:59 | 维护者再次收录（#8310），描述被改成更保守的一版 |
| `a08a505` | 10-22 19:02 | **三分钟后被 revert**，恢复成长描述 |

结论：**改描述可以提，但必须「只说已经做到的」**。维护者拒绝的是夸大与不实（当时写了
items/armor 自定义但参数没接进管线），不是「不能更新」。所以这次 0.5.x 重写描述的前提是：
**每一条都能在 0.5.3 的默认出图上复现**。

## 现在要提交的内容

路径：`data/plugins/TreeCodeTyper__dsh-blender-mc.yml`（替换整段 `description`，`icon` 已在）

与旧描述相比，只多了三类**事实性**补充：

1. 机位不是随便挑 —— 有「地形仰角 > 8° 出局、太阳方向惩罚」的方位打分；
2. 布光与后期是固定链路 —— 三点布光按相机方向重摆、AgX + 合成器后期；
3. 审图是 8 项（旧描述只写 5 项），并说明同一套场景还能出动画与分镜片。

以本目录的 [`TreeCodeTyper__dsh-blender-mc.yml`](TreeCodeTyper__dsh-blender-mc.yml) 为准，提交时整段复制。

## PR 标题与正文（可直接粘贴）

```text
Update dsh-blender-mc description to match 0.5.3

Refresh the description so it matches what the plugin actually does as of
0.5.3. Every claim is reproducible with the default render path:

- the camera picker scores azimuths by terrain elevation and sun direction
  (an azimuth staring into a cliff is rejected), not just "flat ground";
- lighting is three-point, re-aimed from the camera, with AgX color
  management and a compositor grade;
- the OpenCV review covers 8 checks (facing, ground contact, exposure,
  lighting, depth of field, framing, rim light, sky gradient);
- the same scene build also renders 10-second animations and multi-shot
  storyboard films from JSON motion recipes;
- prerequisites unchanged: Blender 4.2 LTS+, Python 3.10+ with
  opencv-python and numpy, one-time `setup` assembles the assets.

`url`, `category` and `icon` are unchanged.
```

## 提交步骤（全程网页，不用 clone）

1. 打开 https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/blob/main/data/plugins/TreeCodeTyper__dsh-blender-mc.yml
2. 右上角铅笔（Edit this file）
3. 用本目录 `TreeCodeTyper__dsh-blender-mc.yml` 的 `description:` 两行整体替换；`url` / `name` / `category` / `icon` 保持不动
4. 「Commit changes」→ 选 “Create a new branch for this commit and start a pull request”
5. 标题正文粘上面那段 → Create pull request

## 提交前自检

- [ ] 描述里没有「还没实现」的功能（自定义 items/armor、Bedrock 导出、Cycles 高质模式都别写）
- [ ] 前置条件仍在（Blender 4.2 LTS+ / Python 3.10+ / opencv-python / numpy / 首次 setup）
- [ ] `icon` 那行没动（指向 `main` 分支的 `assets/icon-180.png`）
- [ ] 版本号不用在这里改 —— 市场目录自己从 npm 抓，npm 上是 0.5.3
