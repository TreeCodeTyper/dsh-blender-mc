# -*- coding: utf-8 -*-
"""模型提取链路自测（不需要 Blender，也不需要任何第三方素材）

造一份合成 Bedrock 几何 + 纯色贴图 → 跑 extract → 校验 OBJ / MTL / 清单。
顺带校验 DSH_MODEL* / DSH_POKE* 入参解析。

CI 里跑:  python scripts/test_extract.py
本地跑:  python scripts/test_extract.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'py'))

import bedrock          # noqa: E402
import extract          # noqa: E402
import model_spec       # noqa: E402

GEO = {
    "format_version": "1.12.0",
    "minecraft:geometry": [{
        "description": {
            "identifier": "geometry.demo_crate",
            "texture_width": 64,
            "texture_height": 64,
        },
        "bones": [
            {"name": "root", "pivot": [0, 0, 0], "cubes": [
                {"origin": [-4, 0, -4], "size": [8, 8, 8], "uv": [0, 0]},
                {"origin": [-6, 0, -1], "size": [12, 2, 2], "uv": [0, 32]},
            ]},
        ],
    }],
}


RGB_BASE = (160, 90, 40)     # 木箱底色
RGB_STRIPE = (60, 180, 200)  # 横向条纹（用来肉眼确认 UV 对不对）


def make_texture(path, size=64):
    """纯标准库写一张带条纹的 PNG（不依赖 cv2/PIL，也不怕中文路径）。"""
    import struct
    import zlib

    rows = []
    for y in range(size):
        c = RGB_STRIPE if (y % 16) < 6 else RGB_BASE
        rows.append(b'\x00' + bytes(c) * size)      # 每行前面是 filter 字节
    raw = b''.join(rows)

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 9))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)
    return True


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def test_extract(tmp):
    assets = os.path.join(tmp, 'assets')
    ent = os.path.join(assets, 'entity')
    os.makedirs(ent)
    geo = os.path.join(ent, 'demo_crate.geo.json')
    with open(geo, 'w', encoding='utf-8') as f:
        json.dump(GEO, f)
    make_texture(os.path.join(ent, 'demo_crate.png'))

    # ---- 只列不转 ----
    dry_out = os.path.join(tmp, 'out_dry')
    dry = extract.extract(assets, dry_out, list_only=True, progress=lambda *_: None)
    check(dry.get('dry_run') is True, '--list 是干跑')
    check(not os.path.exists(dry_out), '--list 不创建输出目录')
    check(dry['list'][0]['texture'] == 'entity/demo_crate.png',
          '猜到的贴图正确')

    # ---- 真转 ----
    out = os.path.join(tmp, 'out')
    m = extract.extract(assets, out, progress=lambda *_: None)
    check(m['count'] == 1, '转了 1 个模型')
    check(not m['errors'], f"没有失败项: {m['errors']}")

    name = m['models'][0]['name']
    check(name == 'demo_crate', f'模型名取 identifier 尾段: {name}')
    obj = os.path.join(out, name, name + '.obj')
    mtl = os.path.join(out, name, name + '.mtl')
    check(os.path.isfile(obj), 'OBJ 已生成')
    check(os.path.isfile(mtl), 'MTL 已生成')
    check(os.path.isfile(os.path.join(out, name, 'demo_crate.png')), '贴图已复制')
    check(os.path.isfile(os.path.join(out, name, 'model.json')), '单模型元信息已写')
    check(os.path.isfile(os.path.join(out, extract.MANIFEST)), '清单已写')

    txt = open(obj, encoding='utf-8').read()
    nv = sum(1 for line in txt.splitlines() if line.startswith('v '))
    nf = sum(1 for line in txt.splitlines() if line.startswith('f '))
    check(nv == m['models'][0]['verts'] and nv > 0, f'顶点数一致: {nv}')
    check(nf == 2 * 6, f'两个 cube 共 12 个面: {nf}')
    check('usemtl model' in txt, 'OBJ 用的是通用材质名 model')
    mtl_txt = open(mtl, encoding='utf-8').read()
    check('map_Kd demo_crate.png' in mtl_txt, 'MTL 指向贴图')

    man = json.load(open(os.path.join(out, extract.MANIFEST), encoding='utf-8'))
    check(man['count'] == 1 and 'note' in man, '清单里有授权提醒')

    # ---- 没贴图也能转 ----
    geo2 = os.path.join(ent, 'no_tex.geo.json')
    with open(geo2, 'w', encoding='utf-8') as f:
        json.dump(GEO, f)
    out2 = os.path.join(tmp, 'out2')
    r = bedrock.convert(geo2, None, os.path.join(tmp, 'no_tex.obj'), debug=False)
    check(r['faces'] == 12, '无贴图时仍导出几何')
    check('map_Kd' not in open(os.path.join(tmp, 'no_tex.mtl'),
                               encoding='utf-8').read(),
          '无贴图时 MTL 不写 map_Kd')


def test_specs(tmp):
    obj = os.path.join(tmp, 'assets', 'entity', 'demo_crate.geo.json')
    tex = os.path.join(tmp, 'assets', 'entity', 'demo_crate.png')

    m, notes = model_spec.collect({'DSH_MODEL1_OBJ': obj, 'DSH_MODEL1_TEX': tex,
                                   'DSH_MODEL1_SCALE': '2'})
    check(len(m) == 1 and m[0]['scale'] == 2.0, 'DSH_MODEL1_* 能解析')

    m, notes = model_spec.collect({'DSH_POKE_OBJ': obj})
    check(len(m) == 1, '旧名 DSH_POKE_* 仍能用')
    check(any('旧' in n for n in notes), '旧前缀会提醒迁移')

    spec = os.path.join(tmp, 'models.json')
    with open(spec, 'w', encoding='utf-8') as f:
        json.dump([{"name": "板条箱", "obj": "assets/entity/demo_crate.geo.json",
                    "tex": "assets/entity/demo_crate.png", "back": 3}], f)
    m, notes = model_spec.collect({'DSH_MODELS': spec})
    check(len(m) == 1 and m[0]['name'] == '板条箱', 'DSH_MODELS 描述文件能解析')
    check(m[0]['back'] == 3.0, '描述文件里的摆放参数生效')
    check(os.path.isabs(m[0]['obj']) and os.path.isfile(m[0]['obj']),
          '描述文件里的相对路径按文件位置解析')

    m, notes = model_spec.collect({'DSH_MODELS': '[{"obj": "x.obj"}]'})
    check(len(m) == 1, 'DSH_MODELS 内联 JSON 也能解析')

    m, notes = model_spec.collect({})
    check(m == [] and notes == [], '没有任何配置时不报错、不加载模型')


def _tmpdir():
    """优先系统临时目录；被沙箱挡住就退到仓库里的临时目录。"""
    try:
        d = tempfile.mkdtemp(prefix='dsh_mc_extract_')
        probe = os.path.join(d, '.write_probe')
        with open(probe, 'w') as f:
            f.write('ok')
        os.remove(probe)
        return d, True
    except Exception:
        d = os.path.join(HERE, '.tmp_extract_test')
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        return d, False


def main():
    tmp, external = _tmpdir()
    print(f"\n模型提取自测（临时目录 {tmp}）\n" + '─' * 60)
    try:
        test_extract(tmp)
        print()
        test_specs(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('─' * 60)
    print('全部通过\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
