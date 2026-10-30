/**
 * mc_shoot 的场景参数：双手物品 / 树林 / 时刻 / 雾 / 云
 *
 * 为什么单独一个文件：
 *   这几个参数要把「人话」翻译成 py/cli.py 的开关（R=…,L=… 简写、morning/noon
 *   预设、空手用空字符串），规矩全在 docs/NEW_PARAMS.md 里。塞回 index.js 只会
 *   让那个 900 行的文件更难读，也没法脱离宿主单测。
 *
 * 约定：
 *   · 成功返回 { argv, env, scene }，失败返回 { error }（不 throw）——
 *     工具侧把 error 原样 report 给用户，用户就知道哪写错了、还能怎么写。
 *   · 往 py 传参同时走 CLI 开关和 DSH_* 环境变量：契约里两套都定义了，双保险；
 *     显式空字符串（空手）必须原样传，不能被当成「没给」。
 */

/** 左右手别名。中文「右手/左手」也认，模型经常直接这么说。 */
const SIDE_ALIAS = {
  r: 'right',
  right: 'right',
  右手: 'right',
  righthand: 'right',
  l: 'left',
  left: 'left',
  左手: 'left',
  lefthand: 'left'
}

/** hour 预设 → 真正的小时。morning 取契约默认的 8 点；night 用 0 点（太阳最低）。 */
export const HOUR_PRESETS = { morning: 8, noon: 12, dusk: 18, night: 0 }

export const ITEM_USAGE = [
  '物品参数可以这么写：',
  '  · item="anvil"                       只给右手',
  '  · item="R=anvil,L=crafting_table"    简写，可以只写一边',
  '  · items={ right: "anvil", left: "crafting_table" }',
  '  · items: ["anvil", "crafting_table"] 按 [右手, 左手] 排',
  '物品可以写物品库里的名字（如 "Sword: Diamond"）、MC 方块 id（crafting_table）、物品 id（anvil / furnace），',
  '或者直接贴游戏里的物品 id：item="minecraft:diamond_sword"、item="R=minecraft:anvil,L=minecraft:crafting_table"；',
  '空字符串表示空手，例如 item="R=,L=anvil" 是右手空着、左手拿铁砧。'
].join('\n')

class ShootParamError extends Error {}

function normalizeText(value) {
  return String(value ?? '')
    .normalize('NFKC')
    .replace(/[＝]/g, '=')
    .replace(/[，、]/g, ',')
    .trim()
}

/** 单个物品值：允许空串（空手），拒绝明显是文件路径的输入并说明为什么。 */
function cleanItemValue(raw, where) {
  const value = normalizeText(raw)
  if (value === '') return ''
  if (value.length > 160) {
    throw new ShootParamError(`${where} 太长了（${value.length} 字）：物品名或方块 id 不会这么长`)
  }
  if (/[\\/]/.test(value) || /\.(blend|png|jpe?g|zip)$/i.test(value)) {
    throw new ShootParamError(
      `${where} 看起来是文件路径（${value}）。物品参数只收物品名或方块 id；`
        + '要换物品库/骨骼，请改插件设置里的 item_rig / bps_rig'
    )
  }
  return value
}

/** 解析 "R=anvil,L=crafting_table" 这类简写。 */
function parseShorthand(text, into, where) {
  const segments = text.split(',').map((s) => s.trim()).filter(Boolean)
  if (!segments.length) throw new ShootParamError(`${where} 是空的。\n${ITEM_USAGE}`)
  for (const segment of segments) {
    const eq = segment.indexOf('=')
    if (eq < 0) {
      throw new ShootParamError(`${where} 里的「${segment}」缺等号：简写要写成 R=物品,L=物品。\n${ITEM_USAGE}`)
    }
    const rawKey = segment.slice(0, eq).trim().toLowerCase()
    const side = SIDE_ALIAS[rawKey]
    if (!side) {
      throw new ShootParamError(`${where} 里的「${rawKey}」不认识：只认 R / L（也接受 right / left / 右手 / 左手）。\n${ITEM_USAGE}`)
    }
    into[side] = cleanItemValue(segment.slice(eq + 1), `${where} 的 ${rawKey.toUpperCase()}`)
  }
}

/** items={ right: …, left: … } */
function applyItemsObject(value, into, where) {
  for (const [rawKey, rawValue] of Object.entries(value)) {
    const side = SIDE_ALIAS[String(rawKey).trim().toLowerCase()]
    if (!side) {
      throw new ShootParamError(`${where} 里的键「${rawKey}」不认识：只认 right / left。\n${ITEM_USAGE}`)
    }
    if (rawValue === undefined || rawValue === null) continue
    into[side] = cleanItemValue(rawValue, `${where}.${side}`)
  }
}

/** items: ["anvil", "crafting_table"] 或 ["R=anvil"] */
function applyItemsArray(value, into, where) {
  if (!value.length) {
    throw new ShootParamError(`${where} 是空数组：给 [右手物品] 或 [右手物品, 左手物品]，想空手就传空字符串。\n${ITEM_USAGE}`)
  }
  if (value.length > 2) {
    throw new ShootParamError(`${where} 最多两项（右手、左手），收到 ${value.length} 项。\n${ITEM_USAGE}`)
  }
  const parts = value.map((v) => normalizeText(v))
  const shorthandCount = parts.filter((p) => p.includes('=')).length
  if (shorthandCount) {
    if (shorthandCount !== parts.length) {
      throw new ShootParamError(`${where} 混用了两种写法：要么全是 "R=…"/"L=…"，要么全是物品名。\n${ITEM_USAGE}`)
    }
    for (const part of parts) parseShorthand(part, into, where)
    return
  }
  into.right = cleanItemValue(parts[0], `${where}[0]（右手）`)
  if (parts.length > 1) into.left = cleanItemValue(parts[1], `${where}[1]（左手）`)
}

/**
 * item / items → { right?, left? }（undefined = 没提到，保留 Python 默认）。
 * items 逐字段覆盖 item，所以 item 只给右手、items 只给左手也能叠加。
 */
export function resolveItemSpec(args = {}) {
  const into = {}
  try {
    if (args.item !== undefined && args.item !== null) {
      const text = normalizeText(args.item)
      if (text === '') into.right = ''
      else if (text.includes('=')) parseShorthand(text, into, 'item')
      else into.right = cleanItemValue(text, 'item')
    }
    if (args.items !== undefined && args.items !== null) {
      const value = args.items
      if (typeof value === 'string') {
        const text = normalizeText(value)
        if (text.includes('=')) parseShorthand(text, into, 'items')
        else into.right = cleanItemValue(text, 'items')
      } else if (Array.isArray(value)) {
        applyItemsArray(value, into, 'items')
      } else if (typeof value === 'object') {
        applyItemsObject(value, into, 'items')
      } else {
        throw new ShootParamError(`items 要数组或 {right,left} 对象，收到 ${typeof value}。\n${ITEM_USAGE}`)
      }
    }
  } catch (error) {
    if (error instanceof ShootParamError) return { error: error.message }
    throw error
  }
  return { right: into.right, left: into.left }
}

/** hour：0–24 的数字，或 morning / noon / dusk / night。 */
export function parseHour(value) {
  if (typeof value === 'boolean') {
    return { error: 'hour 要给 0–24 的数字，或 morning / noon / dusk / night，不是 true/false' }
  }
  const text = normalizeText(value).toLowerCase()
  if (Object.prototype.hasOwnProperty.call(HOUR_PRESETS, text)) {
    return { value: HOUR_PRESETS[text], preset: text }
  }
  const hour = typeof value === 'number' ? value : Number(text)
  if (!Number.isFinite(hour)) {
    return { error: `hour 只认 0–24 的数字或预设名 morning / noon / dusk / night，收到「${String(value)}」` }
  }
  if (hour < 0 || hour > 24) return { error: `hour 要在 0–24 之间（0 = 午夜，12 = 正午），收到 ${hour}` }
  return { value: hour }
}

function parseInteger(value, name, min, max, hint) {
  if (typeof value === 'boolean') return { error: `${name} 要给数字（${min}–${max}），不是 true/false` }
  const number = typeof value === 'number' ? value : Number(normalizeText(value))
  if (!Number.isFinite(number)) return { error: `${name} 要给数字（${min}–${max}），收到「${String(value)}」${hint}` }
  if (!Number.isInteger(number)) return { error: `${name} 要给整数（${min}–${max}），收到 ${number}${hint}` }
  if (number < min || number > max) return { error: `${name} 要在 ${min}–${max} 之间，收到 ${number}${hint}` }
  return { value: number }
}

function parseFog(value) {
  if (typeof value === 'boolean') return { error: 'fog 要给数字：1 = 基准浓度，0 = 关雾，越大越朦胧' }
  const number = typeof value === 'number' ? value : Number(normalizeText(value))
  if (!Number.isFinite(number)) return { error: `fog 要给数字（0 = 关雾，1 = 基准），收到「${String(value)}」` }
  if (number < 0) return { error: `fog 不能是负数（0 = 关雾，1 = 基准），收到 ${number}` }
  if (number > 100) return { error: `fog 太大了（${number}）：1 就是基准浓度，一般 0.5–3 之间调` }
  return { value: number }
}

/** clouds 宽容一点：true/false、1/0、yes/no 都认（模型三种写法都会用）。 */
function parseClouds(value) {
  if (typeof value === 'boolean') return { value: value ? 1 : 0 }
  if (typeof value === 'number' && (value === 0 || value === 1)) return { value }
  const text = normalizeText(value).toLowerCase()
  if (['1', 'true', 'yes', 'on', '显示', '有'].includes(text)) return { value: 1 }
  if (['0', 'false', 'no', 'off', '隐藏', '无'].includes(text)) return { value: 0 }
  return { error: `clouds 只认 true / false（也接受 1 / 0），收到「${String(value)}」` }
}

function given(value) {
  return value !== undefined && value !== null && value !== ''
}

/**
 * 把 mc_shoot 的场景参数翻译成 py/cli.py 的开关 + 环境变量。
 * @returns {{ argv: string[], env: Record<string,string>, scene: Record<string, unknown> } | { error: string }}
 */
export function buildShootExtras(args = {}) {
  const argv = []
  const env = {}
  const scene = {}

  const items = resolveItemSpec(args)
  if (items.error) return { error: items.error }
  if (items.right !== undefined) {
    argv.push('--item-right', items.right)
    env.DSH_ITEM_R = items.right
    scene.item_right = items.right === '' ? '（空手）' : items.right
  }
  if (items.left !== undefined) {
    argv.push('--item-left', items.left)
    env.DSH_ITEM_L = items.left
    scene.item_left = items.left === '' ? '（空手）' : items.left
  }

  if (given(args.armorColor)) {
    const c = String(args.armorColor).trim()
    if (!/^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(c)) {
      return { error: `armorColor 要写成 #RRGGBB（也可以 #RGB），收到的是「${c}」。` }
    }
    argv.push('--armor-color', c)
    env.DSH_ARMOR_COLOR = c
    scene.armor_color = c
  }

  if (given(args.armor)) {
    const armor = String(args.armor).trim()
    if (!armor) return { error: 'armor 是空字符串：想不穿就传 "none"。' }
    argv.push('--armor', armor)
    env.DSH_ARMOR = armor
    scene.armor = armor.toLowerCase() === 'none' ? '（不穿）' : armor
  }

  if (given(args.trees)) {
    const trees = parseInteger(args.trees, 'trees', 0, 100, '（0 = 不种树；种树会按 MC 原版形状长在人物身后）')
    if (trees.error) return { error: trees.error }
    argv.push('--trees', String(trees.value))
    env.DSH_TREES = String(trees.value)
    scene.trees = trees.value
  }

  if (given(args.hour)) {
    const hour = parseHour(args.hour)
    if (hour.error) return { error: hour.error }
    argv.push('--hour', String(hour.value))
    env.DSH_HOUR = String(hour.value)
    scene.hour = hour.preset ? `${hour.preset}（${hour.value} 点）` : `${hour.value} 点`
  }

  if (given(args.fog)) {
    const fog = parseFog(args.fog)
    if (fog.error) return { error: fog.error }
    argv.push('--fog', String(fog.value))
    env.DSH_FOG = String(fog.value)
    scene.fog = fog.value
  }

  if (given(args.clouds)) {
    const clouds = parseClouds(args.clouds)
    if (clouds.error) return { error: clouds.error }
    argv.push('--clouds', String(clouds.value))
    env.DSH_CLOUDS = String(clouds.value)
    scene.clouds = clouds.value === 1 ? '显示' : '隐藏'
  }

  return { argv, env, scene }
}
