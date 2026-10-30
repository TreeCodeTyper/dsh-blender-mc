/**
 * dsh-blender-mc / 背景地图库的「按名字找地图」逻辑
 *
 * 为什么单独一个文件：
 *   py/maplib.py 只认 id（map resolve --id m_xxx），而玩家/模型张口就是名字
 *   （「26.3 新世界」「方可梦世界」）。名字 → id 这段匹配全在这里，纯函数、
 *   不碰 Python 也不碰网络，所以能脱离宿主用 node 直接测。
 *
 * 历史包袱（用户投诉「背景地图库调用不上」）：
 *   ① 索引里曾经同时存在两张都叫「新的世界」的地图（其中一张已移进 trash/），
 *      旧实现 find() 命中第一张就返回 —— 选中的可能是已被删掉的那张；
 *   ② 所有存档的 levelName 都是「新的世界」，用户按这个名字喊之前直接报
 *      「找不到」，且报错只列名字不列 id，人根本没法自我纠正；
 *   ③ 缩略图/推荐区域缺失的老记录会让上层报一句干巴巴的「缺少区域」。
 *   所以这里的规则是：强匹配（id / 全名 / zip 文件名）命中多张时【自动挑一张
 *   并说明】，弱匹配（版本号 / levelName / 子串）命中多张时给出【带 id 的歧义
 *   提示】；任何失败都要把「当前可用地图」连名字带 id 列出来。
 */
import { existsSync } from 'node:fs'

export const MAP_ID_RE = /^m_[0-9a-f]{12}$/

/**
 * 名字比对用的归一化：全半角、大小写、空白和常见标点都不敏感。
 *   「26.3 新世界」=「26.3新世界」=「２６。３ 新世界」
 * 注意只删分隔符，不删字母/数字 —— 删了会把 26.3 和 263 混成同一个版本。
 */
export function normName(value) {
  return String(value ?? '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[\s_\-–—·・.。,，:：;；/\\|()（）[\]【】<>《》"'“”‘’!！?？~～]+/g, '')
}

/** 一张地图参与匹配的所有键（同一条记录可能被玩家用名字/版本/zip 名喊）。 */
export function mapKeys(meta) {
  const m = meta || {}
  const keys = []
  const push = (kind, value) => {
    const key = normName(value)
    if (key) keys.push({ kind, key })
  }
  push('name', m.name)
  push('level', m.levelName)
  push('version', m.mcVersion)
  push('file', String(m.sourceFilename || '').replace(/\.zip$/i, ''))
  // 「版本 + 名字」组合键：截图/面板上两个字段是并排显示的，玩家常连起来喊
  if (m.mcVersion && m.name) push('combo', `${m.mcVersion} ${m.name}`)
  if (m.mcVersion && m.levelName) push('combo', `${m.mcVersion} ${m.levelName}`)
  return keys
}

/** 一行人类可读的地图描述：名字 + id（+ 版本），缺存档目录时标记出来。 */
export function describeMap(meta) {
  const m = meta || {}
  const name = String(m.name || m.levelName || '未命名地图').trim()
  const version = m.mcVersion ? `，MC ${m.mcVersion}` : ''
  let text = `${name}（id ${m.id || '?'}${version}）`
  if (m.savePath && !existsSync(m.savePath)) text += ' ⚠ 存档目录缺失'
  return text
}

/** 错误提示里那段可选的清单。 */
export function catalogText(maps) {
  const list = Array.isArray(maps) ? maps : []
  if (!list.length) return '  （库里暂时没有地图）'
  return list.map((m) => `  · ${describeMap(m)}`).join('\n')
}

/** 「可用地图 + 怎么填」的固定尾注，所有解析失败都带上。 */
export function mapUsageHint(maps) {
  return `可用地图：\n${catalogText(maps)}\n`
    + '用法：map 可以填名字（只写一部分也行），也可以填 id（最稳）；'
    + '也可以不写 map，直接给 save（存档目录）。'
}

/** 匹配层级：越靠前越可信。rank 0–2 是「强匹配」，重名时允许自动挑一张。 */
const TIERS = [
  { rank: 0, kind: 'combo', mode: 'exact' },
  { rank: 1, kind: 'name', mode: 'exact' },
  { rank: 2, kind: 'file', mode: 'exact' },
  { rank: 3, kind: 'version', mode: 'exact' },
  { rank: 4, kind: 'name', mode: 'prefix' },
  { rank: 5, kind: 'level', mode: 'exact' },
  { rank: 6, kind: 'name', mode: 'contains' },
  { rank: 7, kind: 'level', mode: 'contains' },
  { rank: 8, kind: 'combo', mode: 'contains' }
]

/** 强匹配：命中多张时自动挑「存档目录还在 + 最近更新」的那张，不直接失败。 */
const STRONG_RANKS = new Set([0, 1, 2])

function worldUsable(meta) {
  return meta && meta.savePath && existsSync(meta.savePath) ? 1 : 0
}

function pickBest(hits) {
  return [...hits].sort(
    (a, b) => worldUsable(b) - worldUsable(a) || (Number(b.updatedAt) || 0) - (Number(a.updatedAt) || 0)
  )[0]
}

function matches(meta, tier, query) {
  for (const key of mapKeys(meta)) {
    if (key.kind !== tier.kind) continue
    if (tier.mode === 'exact' ? key.key === query : key.key.includes(query)) return true
  }
  return false
}

function fullTitle(meta) {
  const m = meta || {}
  return `${m.mcVersion ? `${m.mcVersion} ` : ''}${m.name || m.levelName || m.id || '未命名地图'}`
}

/**
 * 名字/版本 → 一张地图记录。
 * @returns {{ map: object, note?: string } | { error: { code: string, message: string } }}
 */
export function pickMapByQuery(maps, wanted) {
  const list = (Array.isArray(maps) ? maps : []).filter((m) => m && typeof m === 'object')
  const query = normName(wanted)
  const raw = String(wanted ?? '').trim()
  if (!query) {
    return {
      error: {
        code: 'map.not-found',
        message: `地图名「${raw}」里没有能用来匹配的字符。\n${mapUsageHint(list)}`
      }
    }
  }

  // 模型/玩家有时会丢掉 m_ 前缀，直接给 12 位哈希 —— 也认
  if (/^[0-9a-f]{12}$/.test(query)) {
    const byTail = list.find((m) => String(m.id || '').toLowerCase().endsWith(query))
    if (byTail) return { map: byTail, note: `按 id 后 12 位匹配到「${byTail.name || byTail.id}」（${byTail.id}）` }
  }

  // 玩家也常直接把上传时的文件名报上来（26.3.zip）—— 去掉 .zip 再比一次
  const queries = query.endsWith('zip') && query.length > 3
    ? [query, query.slice(0, -3)]
    : [query]

  for (const tier of TIERS) {
    const hits = list.filter((m) => queries.some((q) => matches(m, tier, q)))
    if (!hits.length) continue
    if (hits.length === 1) return { map: hits[0] }
    if (STRONG_RANKS.has(tier.rank)) {
      const best = pickBest(hits)
      return {
        map: best,
        note: `地图库里有 ${hits.length} 张匹配「${raw}」的地图，已选最近更新的`
          + `「${best.name || best.id}」（${best.id}）；要指定别的请直接传 id。`
      }
    }
    return {
      error: {
        code: 'map.ambiguous',
        message: `「${raw}」对应 ${hits.length} 张地图，没法确定是哪一张：\n${catalogText(hits)}\n`
          + `把 map 写成 id（如 ${hits[0].id}）就能确定；或者写「版本 + 名字」（如「${fullTitle(hits[0])}」）。`
      }
    }
  }

  return {
    error: {
      code: 'map.not-found',
      message: `地图库里没有「${raw}」。\n${mapUsageHint(list)}`
    }
  }
}

/**
 * 兜底推荐区域：老记录 / 手改过的 index.json 可能没有 area（Python 的 dto 只在
 * 有 bounds 时才补算）。这里按 py/maplib.py suggest_area 的同一套规则算一次 ——
 * 以出生点为中心 ±half、再钳进 bounds —— 免得 mc_shoot 因为一条旧记录直接
 * 报「缺少区域」。（half 与 Python 侧保持一致：384）
 */
export function areaFromMeta(meta, half = 384) {
  const bounds = meta && meta.bounds
  if (!bounds || typeof bounds !== 'object') return undefined
  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : undefined)
  const xMin = num(bounds.xMin)
  const xMax = num(bounds.xMax)
  const zMin = num(bounds.zMin)
  const zMax = num(bounds.zMax)
  if ([xMin, xMax, zMin, zMax].some((v) => v === undefined)) return undefined
  if (!(xMax > xMin) || !(zMax > zMin)) return undefined

  const spawn = meta.spawn && typeof meta.spawn === 'object' ? meta.spawn : null
  const sx = spawn ? num(spawn.x) : undefined
  const sz = spawn ? num(spawn.z) : undefined
  let cx
  let cz
  if (sx !== undefined && sz !== undefined) {
    cx = Math.trunc(sx)
    cz = Math.trunc(sz)
  } else {
    cx = Math.trunc((xMin + xMax) / 2)
    cz = Math.trunc((zMin + zMax) / 2)
  }

  let x0 = Math.max(xMin, cx - half)
  let x1 = Math.min(xMax, cx + half)
  let z0 = Math.max(zMin, cz - half)
  let z1 = Math.min(zMax, cz + half)
  if (x1 - x0 < 32 || z1 - z0 < 32) {
    // 出生点跑到世界边界外了：退回包围盒中心（和 Python 侧一致）
    cx = Math.trunc((xMin + xMax) / 2)
    cz = Math.trunc((zMin + zMax) / 2)
    x0 = cx - half
    x1 = cx + half
    z0 = cz - half
    z1 = cz + half
  }
  return `${x0},${x1},${z0},${z1}`
}
