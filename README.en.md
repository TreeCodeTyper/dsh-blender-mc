<div align="center">

<img src="icon.svg" width="112" alt="dsh-blender-mc">

# dsh-blender-mc

**Turn a Minecraft save into Blender covers and animations.**
Give it a save folder and Blender — everything else is automatic,
and **it checks its own output before showing it to you**.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Node](https://img.shields.io/badge/node-%3E%3D20-brightgreen.svg)](package.json)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](py/)

English · [中文](README.md)

</div>

---

## What it does

A **DSH plugin** (also usable as a plain CLI).

```bash
python py/cli.py shoot "D:/saves/MyWorld" --area -2100,-1600,-1400,-900 --out ./out
```

```
save .mca  →  find a good camera spot  →  export geometry  →  build the scene in Blender
           →  render  →  ★ OpenCV self-review  →  auto-tune & re-render  →  image / video
```

You don't need to know Blender. You don't need Mineways.
You don't hand-place cameras, lights, or materials.

---

## Why it's not "just a script"

The hardest part of rendering isn't rendering — it's **not being able to tell
that the render is wrong**.

Real bugs hit while building this:

| Symptom | Actual cause | Time to spot by eye |
|---|---|---|
| "The angle looks wrong" | Character was **facing away** (yaw off by 180°) | several camera tweaks before suspecting the character |
| "Why is he floating?" | Feet **0.019** above ground — MC block edges are razor-sharp | 3 rounds of digging + 3 numeric measurements |
| "What's with the background? There's no light" | `Standard` view transform has no highlight rolloff → physical sky clips to white, dynamic range is consumed | 2 comparison experiments |
| "The sword isn't in his hand" | Wrong grip point (bone tail vs bottom of arm mesh) | per-part measurement |

**All four are detectable by computer vision but invisible in a thumbnail.**

So there's an **OpenCV review pass** after rendering:

| Check | Criterion | Auto-fix |
|---|---|---|
| **Facing** | skin-tone ratio in the head region (front ~30% / back ~9%) | yaw +180° |
| **Ground contact** | colour delta between below-feet and beside-feet ground | increase sink |
| **Exposure** | blown/crushed pixel ratio, dynamic range | lower sky strength |
| **Lighting** | grayscale variance of ground around the subject | raise light multiplier |
| **Composition** | subject height ratio, horizontal position, headroom | adjust camera distance |

### Verified behaviour

Deliberately pass a **wrong facing** (`--yaw -125`, back view):

```
[4/5] render #1 (yaw-125.0°)
[5/5] review: WARN 62
      ⚠️  [1] seeing the BACK (head skin ratio only 8.9%)
      🔧 facing -125.0° → 55.0° (turn to front)
[4/5] render #2 (yaw55.0°)
[5/5] review: PASS 92
      ✅ passed (92 ≥ 85), stop
```

**It found and fixed it on its own.**

---

## Quick start

### Requirements

| | Required | Notes |
|---|---|---|
| ✅ | **Blender** | 4.2 LTS+. [Download](https://www.blender.org/download/) |
| ✅ | **Python 3.10+** | needs `pip` |
| ✅ | **Minecraft Java save** | the folder with `.mca` region files |
| ⭕ | MCprep / BPS rig / item rig / ffmpeg | optional — it tells you exactly what's missing |

> **Bedrock is not supported** — it uses LevelDB; this reads Anvil only.

### Install

```bash
pip install opencv-python numpy
```

Then either install from the DSH plugin marketplace, or:

```bash
git clone https://github.com/TreeCodeTyper/dsh-blender-mc.git
cd dsh-blender-mc
python py/cli.py doctor            # check what's missing
python py/cli.py setup --dry-run   # see what assets would be assembled
python py/cli.py setup             # assemble them
```

Third-party assets (MCprep, rigs, textures) are **not redistributed** — `setup`
copies them from resource folders you already have.

### Commands

```bash
python py/cli.py doctor                              # check requirements
python py/cli.py setup [dirs...] [--dry-run]         # assemble assets
python py/cli.py scan   <save> --area x0,x1,z0,z1    # find camera spots
python py/cli.py shoot  <save> --area ... --out DIR  # render a cover
python py/cli.py anim   <cover.blend> --out DIR      # 10s animation
python py/cli.py check  <images...> [--json]         # review only
```

---

## Contributing

**Only maintainers can push directly. Everyone else: fork → PR → review → merge.**

See **[CONTRIBUTING.md](CONTRIBUTING.md)** (currently Chinese; an English
translation is a very welcome contribution).

The short version:

```bash
git clone https://github.com/<your-github-username>/dsh-blender-mc.git   # your fork
git checkout -b feat/your-feature
# ... edit ...
python py/cli.py doctor && node scripts/smoke.mjs
git commit -m "feat: what you did"
git push origin feat/your-feature
# then open a PR against the upstream repo
```

---

## License

[MIT](LICENSE) — covers this repo's own code and docs.

Runtime third-party assets are **not distributed** by this project;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

> Not affiliated with or endorsed by Mojang Studios or Microsoft.
> "Minecraft" is a trademark of Mojang Studios.
