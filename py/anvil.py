# -*- coding: utf-8 -*-
"""mc_anvil —— 纯标准库的 Minecraft Anvil 世界读取器

为什么自己写：Mineways 是 GUI 程序（非交互会话里起不来），
而 .mca 区域文件本质就是「zlib 压缩的 NBT」——标准库直接能读。

好处（相比 Mineways → .obj 方案）：
  · 无 GUI 依赖，完全无头
  · 没有 .obj/.mtl 往返，贴图不会丢（坑 16 的根因）
  · 能按方块名精确筛选，想导哪块导哪块

用法：
    from mc_anvil import World
    w = World(r"...\\saves\\parkour")
    blocks = w.iter_blocks(x0=-48, x1=48, z0=-48, z1=48, y0=40, y1=160)
    for (x, y, z, name) in blocks: ...
"""
import os
import struct
import zlib

# ----------------------------------------------------------------- NBT

TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG = 0, 1, 2, 3, 4
TAG_FLOAT, TAG_DOUBLE, TAG_BYTE_ARRAY, TAG_STRING = 5, 6, 7, 8
TAG_LIST, TAG_COMPOUND, TAG_INT_ARRAY, TAG_LONG_ARRAY = 9, 10, 11, 12


class NBTReader:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def u1(self):
        v = self.d[self.i]; self.i += 1; return v

    def i1(self):
        v = struct.unpack_from('>b', self.d, self.i)[0]; self.i += 1; return v

    def i2(self):
        v = struct.unpack_from('>h', self.d, self.i)[0]; self.i += 2; return v

    def u2(self):
        v = struct.unpack_from('>H', self.d, self.i)[0]; self.i += 2; return v

    def i4(self):
        v = struct.unpack_from('>i', self.d, self.i)[0]; self.i += 4; return v

    def i8(self):
        v = struct.unpack_from('>q', self.d, self.i)[0]; self.i += 8; return v

    def f4(self):
        v = struct.unpack_from('>f', self.d, self.i)[0]; self.i += 4; return v

    def f8(self):
        v = struct.unpack_from('>d', self.d, self.i)[0]; self.i += 8; return v

    def s(self):
        n = self.u2()
        v = self.d[self.i:self.i + n].decode('utf-8', 'replace')
        self.i += n
        return v

    def payload(self, t):
        if t == TAG_BYTE:
            return self.i1()
        if t == TAG_SHORT:
            return self.i2()
        if t == TAG_INT:
            return self.i4()
        if t == TAG_LONG:
            return self.i8()
        if t == TAG_FLOAT:
            return self.f4()
        if t == TAG_DOUBLE:
            return self.f8()
        if t == TAG_BYTE_ARRAY:
            n = self.i4()
            v = self.d[self.i:self.i + n]
            self.i += n
            return v
        if t == TAG_STRING:
            return self.s()
        if t == TAG_LIST:
            et = self.u1()
            n = self.i4()
            return [self.payload(et) for _ in range(n)]
        if t == TAG_COMPOUND:
            out = {}
            while True:
                t2 = self.u1()
                if t2 == TAG_END:
                    return out
                name = self.s()
                out[name] = self.payload(t2)
        if t == TAG_INT_ARRAY:
            n = self.i4()
            v = list(struct.unpack_from('>%di' % n, self.d, self.i))
            self.i += 4 * n
            return v
        if t == TAG_LONG_ARRAY:
            n = self.i4()
            v = list(struct.unpack_from('>%dq' % n, self.d, self.i))
            self.i += 8 * n
            return v
        raise ValueError('未知 NBT 类型 %d' % t)

    def root(self):
        t = self.u1()
        if t != TAG_COMPOUND:
            raise ValueError('根标签不是 Compound')
        self.s()                       # 根名（通常为空）
        return self.payload(TAG_COMPOUND)


def parse_nbt(data):
    return NBTReader(data).root()


# ----------------------------------------------------------------- 区域文件

class Region:
    """一个 .mca 文件 = 32×32 个区块"""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.raw = f.read()

    def chunk_offsets(self):
        """返回 [(cx, cz, 字节偏移, 扇区数)]，只含已生成的区块"""
        if len(self.raw) < 8192:
            # ⚠️ 坑 81：区域文件头是 8 KiB（1024 条 4 字节索引 + 4 KiB 时间戳）。
            #   截断/空文件以前直接 IndexError，把 mc_scan / mc_shoot 整条链路带崩；
            #   现在当「这个区域没区块」，让调用方继续扫别的区域。
            return []
        out = []
        for i in range(1024):
            b = i * 4
            off = (self.raw[b] << 16) | (self.raw[b + 1] << 8) | self.raw[b + 2]
            cnt = self.raw[b + 3]
            if off == 0 or cnt == 0:
                continue
            out.append((i % 32, i // 32, off * 4096, cnt))
        return out

    def chunk_nbt(self, offset):
        """解压并解析一个区块。1.21 的压缩类型在偏移+4 处。"""
        length = struct.unpack_from('>I', self.raw, offset)[0]
        comp = self.raw[offset + 4]
        payload = self.raw[offset + 5:offset + 4 + length]
        if comp == 1:                       # gzip
            import gzip
            payload = gzip.decompress(payload)
        elif comp == 2:                     # zlib
            payload = zlib.decompress(payload)
        elif comp == 3:                     # 未压缩
            pass
        else:
            raise ValueError('未知压缩类型 %d' % comp)
        return parse_nbt(payload)


def _pal_name(entry):
    """方块调色板条目的方块名。

    ≤1.21.x 的条目是 {'Name': 'minecraft:stone', 'Properties': {...}}；
    26.x 起变成两种形式：直接是字符串 'minecraft:stone'，或者
    {'id': 'minecraft:stone', 'properties': {...}}（老存档混进来时还可能
    是 {'': 'minecraft:stone'} 这种无名键），三种都得认。
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for k in ('Name', 'id', 'name', ''):
            v = entry.get(k)
            if isinstance(v, str) and v:
                return v
    return 'minecraft:air'


def _pal_props(entry):
    """方块状态（半砖 type=top、楼梯 facing 之类）。没有就返回空字典。"""
    if isinstance(entry, dict):
        for k in ('Properties', 'properties'):
            v = entry.get(k)
            if isinstance(v, dict):
                return v
    return {}


def read_player(save_dir):
    """读取玩家数据（位置/朝向/维度/背包）。

    playerdata/<uuid>.dat 是 gzip 压缩的 NBT（26.x 起挪到了
    players/data/<uuid>.dat）。这里能拿到玩家【上次下线的
    真实坐标与朝向】—— 把角色放回世界地图里时就该站在这个位置。

    返回值: dict(pos, yaw, pitch, dimension, health, food, gamemode, inventory)
            没有玩家数据时返回 None
    """
    import gzip
    pd = None
    for cand in (os.path.join(save_dir, 'playerdata'),
                 os.path.join(save_dir, 'players', 'data')):
        if os.path.isdir(cand):
            pd = cand
            break
    if not pd:
        return None
    files = [f for f in os.listdir(pd) if f.endswith('.dat')]
    if not files:
        return None
    # 多个玩家时取【最后修改】的那个（通常是最近玩的人）
    files.sort(key=lambda f: os.path.getmtime(os.path.join(pd, f)), reverse=True)
    path = os.path.join(pd, files[0])
    with open(path, 'rb') as fh:
        raw = fh.read()
    try:
        data = gzip.decompress(raw)
    except Exception:
        try:
            data = zlib.decompress(raw)
        except Exception:
            data = raw
    nbt = parse_nbt(data)
    # 26.x 的字段名是小写的，两种都试
    pos = nbt.get('Pos') or nbt.get('pos') or [0, 64, 0]
    rot = nbt.get('Rotation') or nbt.get('rotation') or [0, 0]
    inv = []
    for it in (nbt.get('Inventory') or nbt.get('inventory') or []):
        if isinstance(it, dict):
            inv.append({'slot': it.get('Slot', it.get('slot')),
                        'id': it.get('id', it.get('Id')),
                        'count': it.get('count', it.get('Count', 1))})
    return {
        'file': files[0],
        'pos': (float(pos[0]), float(pos[1]), float(pos[2])),
        'yaw': float(rot[0]),
        'pitch': float(rot[1]) if len(rot) > 1 else 0.0,
        'dimension': nbt.get('Dimension', nbt.get('dimension', 'minecraft:overworld')),
        'health': nbt.get('Health', nbt.get('health')),
        'food': nbt.get('foodLevel', nbt.get('food_level')),
        'gamemode': nbt.get('playerGameType', nbt.get('player_game_type')),
        'inventory': inv,
    }


def mc_to_blender(x, y, z):
    """MC 坐标 → Blender 坐标。

    MC 是 Y 向上，Blender 是 Z 向上。
    方块 (X, Y, Z) 占 [X,X+1]×[Y,Y+1]×[Z,Z+1]，
    映射后占 [X,X+1]×[-Z-1,-Z]×[Y,Y+1]。
    """
    return (x, -z, y)


# ----------------------------------------------------------------- 世界

def _unpack_blocks(longs, palette_size, count=4096):
    """把打包的 long 数组还原成方块索引。

    1.16+ 的「紧凑」打包：一个方块索引【不跨 long】。
    bits = max(4, ceil(log2(palette_size)))，每个 long 放 64//bits 个。
    """
    if palette_size <= 1:
        return [0] * count
    bits = max(4, (palette_size - 1).bit_length())
    per_long = 64 // bits
    mask = (1 << bits) - 1
    out = []
    for v in longs:
        uv = v & 0xFFFFFFFFFFFFFFFF
        for k in range(per_long):
            if len(out) >= count:
                break
            out.append((uv >> (k * bits)) & mask)
        if len(out) >= count:
            break
    while len(out) < count:
        out.append(0)
    return out


def _region_rel_candidates(save_dir):
    """候选 region 相对路径：经典 <save>/region 优先，其次 26.x 的
    <save>/dimensions/<ns>/<dim>/region（这时顶层没有 region/）。"""
    yield 'region'
    dims_root = os.path.join(save_dir, 'dimensions')
    if not os.path.isdir(dims_root):
        return
    try:
        namespaces = sorted(os.listdir(dims_root))
    except OSError:
        return
    namespaces = (['minecraft'] if 'minecraft' in namespaces else []) + \
                 [n for n in namespaces if n != 'minecraft']
    order = ('overworld', 'the_nether', 'the_end')
    for ns in namespaces:
        ns_dir = os.path.join(dims_root, ns)
        if not os.path.isdir(ns_dir):
            continue
        try:
            dims = sorted(os.listdir(ns_dir))
        except OSError:
            continue
        dims = [d for d in order if d in dims] + [d for d in dims if d not in order]
        for dm in dims:
            yield 'dimensions/%s/%s/region' % (ns, dm)


def _find_region_rel(save_dir):
    """按布局判断主世界的 region 相对路径（与 maplib.find_region_dir 同一套规则）。

    1. `<save>/region` 有 .mca → 经典布局的主世界（数据包维度再多也不是它）
    2. `<save>/dimensions/minecraft/overworld/region` → 26.x 新布局
    3. 其余 dimensions 里 .mca 最多的（只带自定义维度的存档兜底）
    """
    def count(rel):
        p = os.path.join(save_dir, *rel.split('/'))
        try:
            return sum(1 for f in os.listdir(p) if f.lower().endswith('.mca'))
        except OSError:
            return 0

    if count('region'):
        return 'region'
    primary = 'dimensions/minecraft/overworld/region'
    if count(primary):
        return primary
    best = None
    for rel in _region_rel_candidates(save_dir):
        if rel in ('region', primary):
            continue
        n = count(rel)
        if n and (best is None or n > best[1]):
            best = (rel, n)
    return best[0] if best else None


class World:
    def __init__(self, save_dir, region_rel=None):
        self.dir = save_dir
        rel = region_rel or _find_region_rel(save_dir)
        if not rel:
            raise FileNotFoundError(
                '找不到 region 目录（经典 region/ 与 dimensions/*/*/region 都不存在）: %s'
                % save_dir)
        self.region_rel = rel
        self.region_dir = os.path.join(save_dir, *rel.split('/'))

    def region_files(self, dim='overworld'):
        if dim in ('overworld', '', None):
            d = self.region_dir
            if not os.path.isdir(d):
                return []
            return [os.path.join(d, f) for f in sorted(os.listdir(d))
                    if f.endswith('.mca')]
        # 其它维度：26.x 在 dimensions/minecraft/<dim>/region，老布局在 <大洲码>/region
        alias = {'the_nether': 'DIM-1', 'the_end': 'DIM1',
                 'DIM-1': 'the_nether', 'DIM1': 'the_end'}
        names = [dim] + ([alias[dim]] if dim in alias else [])
        for nm in names:
            for d in (os.path.join(self.dir, 'dimensions', 'minecraft', nm, 'region'),
                      os.path.join(self.dir, nm, 'region')):
                if os.path.isdir(d):
                    return [os.path.join(d, f) for f in sorted(os.listdir(d))
                            if f.endswith('.mca')]
        return []

    def iter_blocks(self, x0=-64, x1=64, z0=-64, z1=64, y0=0, y1=320,
                    skip_air=True, dim='overworld', limit=None, on_progress=None,
                    with_props=False):
        """产出 (x, y, z, block_name)。坐标是【世界坐标】。
        with_props=True 时产出 (x, y, z, name, props)，props 是方块状态字典
        （半砖的 type=top/bottom、楼梯的 facing 等都在这里）。

        只读与给定范围相交的区块，不会全图扫描。
        """
        n = 0
        files = self.region_files(dim)
        for fi, rf in enumerate(files):
            reg = Region(rf)
            # 区域文件自身坐标（r.X.Z.mca）
            base = os.path.basename(rf)[2:-4].split('.')
            try:
                rx, rz = int(base[0]), int(base[1])
            except Exception:
                rx = rz = 0
            # 该区域覆盖的世界范围
            if (rx + 1) * 512 <= x0 or rx * 512 >= x1:
                continue
            if (rz + 1) * 512 <= z0 or rz * 512 >= z1:
                continue
            for (cx, cz, off, cnt) in reg.chunk_offsets():
                wx, wz = rx * 32 + cx, rz * 32 + cz
                if wx * 16 + 15 < x0 or wx * 16 > x1:
                    continue
                if wz * 16 + 15 < z0 or wz * 16 > z1:
                    continue
                try:
                    nbt = reg.chunk_nbt(off)
                except Exception:
                    continue
                for sec in nbt.get('sections', []):
                    sy = sec.get('Y')
                    if sy is None:
                        continue
                    sy = sy if isinstance(sy, int) else 0
                    bs = sec.get('block_states')
                    if not bs:
                        continue
                    pal = [_pal_name(p) for p in bs.get('palette', [])]
                    props = [_pal_props(p) for p in bs.get('palette', [])]
                    if not pal:
                        continue
                    data = bs.get('data')
                    idxs = _unpack_blocks(data or [], len(pal))
                    for li, bi in enumerate(idxs):
                        if bi >= len(pal):
                            continue
                        name = pal[bi]
                        if skip_air and name in ('minecraft:air', 'minecraft:cave_air',
                                                 'minecraft:void_air'):
                            continue
                        # 段内索引 → 段内局部坐标（YZX 顺序）
                        ly, lz, lx = li // 256, (li // 16) % 16, li % 16
                        X, Y, Z = wx * 16 + lx, sy * 16 + ly, wz * 16 + lz
                        if not (x0 <= X <= x1 and z0 <= Z <= z1 and y0 <= Y <= y1):
                            continue
                        if with_props:
                            yield (X, Y, Z, name, props[bi])
                        else:
                            yield (X, Y, Z, name)
                        n += 1
                        if limit and n >= limit:
                            return
                if on_progress:
                    on_progress(fi, len(files), wx, wz, n)
