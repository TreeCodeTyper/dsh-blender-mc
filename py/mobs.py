"""MCprep 生物 / 玩家 rig 库：列出来，或者 append 进一个工程。

课程依据（这套课程里点名讲过的两集）：
- T52 MCprep 的「生成器（Spawning）→ 生物生成器」能直接生成已绑定好的生物，
  首次必须点「重新加载资源」，且必须在物体模式；
- T55/T56 BPS / Woodplank 的人物 rig 同理。

但 `blender -b` 无界面模式里 bpy.ops.mcprep 是空的（那些算子只在 GUI 注册），
所以插件走另一条路：直接 append MCprep 自带的 rig .blend。
"""
import os
import subprocess
import sys
import tempfile

RIG_GROUPS = ("custom", "friendly", "hostile", "passive", "player")


def _blender_roots():
    """Blender 用户配置目录下的所有版本（5.2 / 5.0 / 4.5 …）。"""
    base = os.path.join(os.environ.get("APPDATA", ""), "Blender Foundation", "Blender")
    if not os.path.isdir(base):
        return []
    return [os.path.join(base, d) for d in sorted(os.listdir(base), reverse=True)]


def find_rig_dirs():
    """所有版本的 MCprep_resources/rigs 目录（新版优先）。"""
    out = []
    for root in _blender_roots():
        d = os.path.join(root, "scripts", "addons", "MCprep_addon", "MCprep_resources", "rigs")
        if os.path.isdir(d):
            out.append(d)
    return out


def list_rigs(group=None):
    """[(分组, 名字, 路径, 大小KB)]"""
    rows = []
    seen = set()
    for base in find_rig_dirs():
        groups = [group] if group else [g for g in RIG_GROUPS
                                        if os.path.isdir(os.path.join(base, g))]
        for g in groups:
            d = os.path.join(base, g)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if not name.lower().endswith(".blend"):
                    continue
                key = (g, name)
                if key in seen:
                    continue
                seen.add(key)
                p = os.path.join(d, name)
                rows.append((g, name[:-6], p, round(os.path.getsize(p) / 1024.0, 1)))
    return rows


def match_rigs(query):
    """按名字模糊匹配（大小写不敏感，子串即可）。"""
    q = (query or "").strip().lower()
    rows = list_rigs()
    if not q:
        return rows
    return [r for r in rows if q in r[1].lower() or q == r[0].lower()]


SPAWN_PY = r'''
import bpy, sys, os
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
rig_path, keyword, out_path = argv[0], argv[1], argv[2]
pos = tuple(float(v) for v in (argv[3].split(",") if len(argv) > 3 and argv[3] else [0, 0, 0]))
scale = float(argv[4]) if len(argv) > 4 and argv[4] else 1.0

sc = bpy.context.scene
with bpy.data.libraries.load(rig_path, link=False) as (src, dst):
    dst.objects = list(src.objects)
loaded = [o for o in dst.objects if o is not None]

# 一个 rig 文件里常常有好几个物体（相机、灯光、字体、几套角色绑在一起）。
# 一套角色的边界 = 骨架 + 它的对象子级 + 蒙皮到它的网格。
# 文件里有多个骨架时（Fox Panda 里同时有 fox.arma / panda.arma），按名字挑。
arms = [o for o in loaded if o.type == "ARMATURE"]
target_arm = None
if arms:
    words = [w for w in (keyword or "").replace("-", " ").split() if w]
    best = None
    for cand in arms:
        low = cand.name.lower()
        for w in words:
            if w.lower() in low and (best is None or len(w) > best[0]):
                best = (len(w), cand)
    target_arm = best[1] if best else arms[0]

kept = []
if target_arm is not None:
    kept.append(target_arm)
    for ob in loaded:
        if ob is target_arm or ob.type in ("CAMERA", "LIGHT", "FONT"):
            continue
        if ob.parent is target_arm:
            kept.append(ob)
        elif any(m.type == "ARMATURE" and m.object is target_arm for m in ob.modifiers):
            kept.append(ob)
else:
    kept = [o for o in loaded if o.type == "MESH"]
dropped = len(loaded) - len(kept)
keep_prefix = target_arm.name if target_arm is not None else None

for ob in kept:
    if ob.name not in sc.collection.objects:
        try:
            sc.collection.objects.link(ob)
        except RuntimeError:
            pass
bpy.context.view_layer.update()

# 水平居中 + 脚落地 + 缩放 + 平移
arm = next((o for o in kept if o.type == "ARMATURE"), None)
targets = [o for o in kept if o.type == "MESH"]
if arm is not None and arm.scale[0] != scale:
    arm.scale = (scale, scale, scale)
    bpy.context.view_layer.update()
xs, ys, zs = [], [], []
for ob in targets:
    for c in ob.bound_box:
        w = ob.matrix_world @ Vector(c)
        xs.append(w.x); ys.append(w.y); zs.append(w.z)
if xs:
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    mz = min(zs)
    if arm is not None:
        arm.location.x += pos[0] - cx
        arm.location.y += pos[1] - cy
        arm.location.z += pos[2] - mz
    else:
        for ob in targets:
            ob.location.x += pos[0] - cx
            ob.location.y += pos[1] - cy
            ob.location.z += pos[2] - mz
bpy.context.view_layer.update()

h = 0.0
if zs:
    h = max(zs) - min(zs)
print("[mobs] 保留 %d 个对象（丢弃 %d），高 %.2f，前缀 %s" % (len(kept), dropped, h, keep_prefix))
bpy.ops.wm.save_as_mainfile(filepath=out_path)
print("[mobs] 已保存 %s" % out_path)
'''


def spawn(name, blend, out, blender_exe, pos=(0, 0, 0), scale=1.0, keyword=None):
    """把 name 匹配到的 rig append 进 blend，另存为 out。返回 (ok, log)。"""
    hits = match_rigs(name)
    if not hits:
        return False, "没有匹配的生物 rig：%s" % name
    group, rig_name, rig_path, _kb = hits[0]
    out_dir = os.path.dirname(os.path.abspath(out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    script = os.path.join(tempfile.gettempdir(), "dsh_mobs_spawn.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(SPAWN_PY)
    # 文件名形如 "Fox Panda - Boxscape"，取作者名之前那一段当关键词：
    # 里面可能有两个角色词（Fox / Panda），由 SPAWN_PY 选命中最长的那个骨架。
    kw = keyword or rig_name.split(" - ")[0].strip()
    cmd = [blender_exe, "-b", blend, "--python", script, "--",
           rig_path, kw, out, "%g,%g,%g" % tuple(pos), str(scale)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode in (0, 1) and os.path.isfile(out), log


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        group = argv[1] if len(argv) > 1 else None
        rows = list_rigs(group)
        if not rows:
            print("[mobs] 没找到 MCprep rig 库（确认 MCprep 已装）")
            return 2
        cur = None
        for g, n, _p, kb in rows:
            if g != cur:
                print("\n[%s]" % g)
                cur = g
            print("  %-42s %7.1fMB" % (n, kb / 1024.0))
        print("\n[mobs] 共 %d 个 rig" % len(rows))
        return 0
    if cmd == "find":
        rows = match_rigs(argv[1] if len(argv) > 1 else "")
        for g, n, p, _kb in rows:
            print("%-9s %-40s %s" % (g, n, p))
        print("[mobs] 命中 %d" % len(rows))
        return 0
    print("[mobs] 用法：mobs list [分组] | mobs find <关键词>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
