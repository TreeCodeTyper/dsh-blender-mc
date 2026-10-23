/**
 * dsh-blender-mc · 人物皮肤库（Node 侧）
 *
 * 为什么放在 Node 而不是 python：皮肤就是一个 PNG，校验只有「签名 + IHDR 尺寸」两件事，
 * 用 Buffer 直接读就行，不必为它再起一个 python 子进程。渲染时由 python 侧用
 * DSH_SKIN=<png 路径> 接管（见 py/blender/build_cover.py 的 [4.5] 换皮肤）。
 *
 * 目录布局（和地图库分开，重装插件不会丢）：
 *   <DSH_HOME>/blender-mc/skins/index.json   { version, defaultId, skins: [...] }
 *   <DSH_HOME>/blender-mc/skins/<id>.png
 */
import { mkdirSync, readFileSync, writeFileSync, renameSync, rmSync, existsSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { createHash, randomUUID } from 'node:crypto'

export const MAX_SKIN_BYTES = 2 * 1024 * 1024
/** 正方形皮肤边长白名单：原版 64、HD 128 / 256 / 512（BPS 的 UV 按比例缩放） */
export const SKIN_SIDES = new Set([64, 128, 256, 512])
const ID_RE = /^s_[0-9a-f]{12}$/
const PNG_SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

export class SkinError extends Error {
  constructor(code, message, status = 400, params) {
    super(message)
    this.name = 'SkinError'
    this.code = code
    this.status = status
    this.params = params || {}
  }
}

function expandHome(p) {
  if (typeof p !== 'string') return ''
  if (p.startsWith('~')) return join(homedir(), p.slice(1))
  return p
}

export function defaultSkinsDir(env = process.env) {
  const home = env.DSH_HOME ? expandHome(env.DSH_HOME) : join(homedir(), '.dsh')
  return join(home, 'blender-mc', 'skins')
}

export function isSkinId(v) {
  return typeof v === 'string' && ID_RE.test(v)
}

/** 读 PNG 头：签名 + IHDR 的宽高（不做完整解码，够判断「是不是一张能用的皮肤」） */
export function pngSize(buf) {
  if (!Buffer.isBuffer(buf) || buf.length < 33) throw new SkinError('skin.not-png', '文件太小，不是 PNG')
  if (!buf.subarray(0, 8).equals(PNG_SIG)) throw new SkinError('skin.not-png', '不是 PNG 文件（皮肤必须是 PNG）')
  if (buf.toString('ascii', 12, 16) !== 'IHDR') throw new SkinError('skin.not-png', 'PNG 结构异常（缺 IHDR）')
  return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) }
}

function ensureDir(dir) {
  mkdirSync(dir, { recursive: true })
}

function indexPath(dir) {
  return join(dir, 'index.json')
}

export function readIndex(dir) {
  try {
    const raw = readFileSync(indexPath(dir), 'utf8')
    const j = JSON.parse(raw)
    if (!j || typeof j !== 'object') throw new Error('bad')
    return {
      version: 1,
      defaultId: isSkinId(j.defaultId) ? j.defaultId : null,
      skins: Array.isArray(j.skins) ? j.skins.filter((s) => s && isSkinId(s.id)) : []
    }
  } catch {
    return { version: 1, defaultId: null, skins: [] }
  }
}

function writeIndex(dir, index) {
  ensureDir(dir)
  const tmp = `${indexPath(dir)}.tmp-${process.pid}-${randomUUID().slice(0, 6)}`
  writeFileSync(tmp, JSON.stringify(index, null, 2), 'utf8')
  renameSync(tmp, indexPath(dir))
}

function sanitizeName(name, fallback) {
  const s = String(name || '').replace(/[\u0000-\u001f\\/:*?"<>|]/g, '').trim()
  return (s || fallback || '我的皮肤').slice(0, 40)
}

export function dto(skin) {
  return {
    id: skin.id,
    name: skin.name,
    w: skin.w,
    h: skin.h,
    sizeBytes: skin.sizeBytes,
    createdAt: skin.createdAt,
    isDefault: Boolean(skin.isDefault),
    thumbUrl: `/api/blender-mc/skins/${skin.id}/thumb`
  }
}

export function listSkins(dir) {
  const index = readIndex(dir)
  const skins = index.skins
    .filter((s) => {
      try {
        return statSync(join(dir, `${s.id}.png`)).size > 0
      } catch {
        return false
      }
    })
    .map((s) => dto({ ...s, isDefault: s.id === index.defaultId }))
  return { skins, defaultId: index.defaultId, limits: { maxUploadBytes: MAX_SKIN_BYTES } }
}

export function addSkin(dir, buf, opts = {}) {
  if (!Buffer.isBuffer(buf) || buf.length === 0) throw new SkinError('skin.empty', '文件是空的')
  if (buf.length > MAX_SKIN_BYTES) {
    throw new SkinError('skin.too-large',
      `皮肤最大 ${(MAX_SKIN_BYTES / 1024 / 1024).toFixed(0)} MB（收到 ${(buf.length / 1024).toFixed(0)} KB）`, 413)
  }
  const { w, h } = pngSize(buf)
  if (w !== h) {
    throw new SkinError('skin.bad-size',
      `皮肤必须是正方形（收到 ${w}×${h}）；64×32 的 1.7 老皮肤不支持`, 422, { w, h })
  }
  if (!SKIN_SIDES.has(w)) {
    throw new SkinError('skin.bad-size',
      `皮肤边长要是 ${[...SKIN_SIDES].join(' / ')}（收到 ${w}×${h}）`, 422, { w, h })
  }

  ensureDir(dir)
  const index = readIndex(dir)
  const sha = createHash('sha256').update(buf).digest('hex')
  const id = `s_${sha.slice(0, 12)}`
  const base = String(opts.filename || '').split(/[\\/]/).pop() || ''
  const name = sanitizeName(opts.name, base.replace(/\.[^.]+$/, ''))

  const file = join(dir, `${id}.png`)
  const fileOk = () => {
    try {
      return statSync(file).size > 0
    } catch {
      return false
    }
  }

  const exist = index.skins.find((s) => s.id === id)
  if (exist && fileOk()) {
    // 同一张图重传：只更新名字，不当错误（皮肤不像地图，重复上传很常见）
    exist.name = name
    exist.createdAt = exist.createdAt || new Date().toISOString()
    writeIndex(dir, index)
    return dto({ ...exist, isDefault: exist.id === index.defaultId })
  }
  if (exist) {
    // 索引里有、文件却没了（用户手动清过目录 / 上次写文件失败）：
    // 直接按这份新传的内容把文件补回去，不算重复。
    const keepName = opts.name || exist.name || name
    const tmp = `${file}.tmp-${randomUUID().slice(0, 6)}`
    writeFileSync(tmp, buf)
    renameSync(tmp, file)
    exist.name = sanitizeName(keepName, name)
    exist.w = w
    exist.h = h
    exist.sizeBytes = buf.length
    if (!isSkinId(index.defaultId)) index.defaultId = id
    writeIndex(dir, index)
    return dto({ ...exist, isDefault: exist.id === index.defaultId })
  }

  const tmp = `${file}.tmp-${randomUUID().slice(0, 6)}`
  writeFileSync(tmp, buf)
  renameSync(tmp, file)

  const meta = {
    id,
    name,
    w,
    h,
    sizeBytes: buf.length,
    createdAt: new Date().toISOString()
  }
  index.skins.unshift(meta)
  if (!isSkinId(index.defaultId)) index.defaultId = id // 第一个上传的自动成默认
  writeIndex(dir, index)
  return dto({ ...meta, isDefault: meta.id === index.defaultId })
}

export function setDefaultSkin(dir, id) {
  const index = readIndex(dir)
  const hit = index.skins.find((s) => s.id === id)
  if (!hit) throw new SkinError('not_found', '皮肤不在库里（可能已被删除）', 404)
  index.defaultId = id
  writeIndex(dir, index)
  return { defaultId: id }
}

export function clearDefaultSkin(dir) {
  const index = readIndex(dir)
  index.defaultId = null
  writeIndex(dir, index)
  return { defaultId: null }
}

export function deleteSkin(dir, id) {
  const index = readIndex(dir)
  const hit = index.skins.find((s) => s.id === id)
  if (!hit) throw new SkinError('not_found', '皮肤不在库里（可能已被删除）', 404)
  index.skins = index.skins.filter((s) => s.id !== id)
  if (index.defaultId === id) index.defaultId = index.skins[0]?.id || null
  writeIndex(dir, index)
  try {
    rmSync(join(dir, `${id}.png`), { force: true })
  } catch {
    /* 文件删不掉不影响索引 */
  }
  return { defaultId: index.defaultId }
}

export function readSkinFile(dir, id) {
  if (!isSkinId(id)) throw new SkinError('bad_id', '皮肤 id 不合法', 400)
  const p = join(dir, `${id}.png`)
  if (!existsSync(p)) throw new SkinError('not_found', '这张皮肤不在了', 404)
  return readFileSync(p)
}

/**
 * 把「id / 名字 / 绝对路径」解析成可以直接喂给 Blender 的 png 路径。
 * 找不到回 null（不抛错：没设皮肤是正常状态，保持原版 Steve）。
 */
export function resolveSkinPath(dir, key) {
  const k = String(key || '').trim()
  if (!k) return null
  if (isSkinId(k)) {
    const p = join(dir, `${k}.png`)
    return existsSync(p) ? p : null
  }
  // 绝对 / 相对路径直接用（方便脚本传本地皮肤）
  if (/[\\/]/.test(k) && existsSync(k)) return k
  const index = readIndex(dir)
  const low = k.toLowerCase()
  const byName = index.skins.find((s) => String(s.name || '').toLowerCase() === low) ||
    index.skins.find((s) => String(s.name || '').toLowerCase().startsWith(low))
  if (byName && existsSync(join(dir, `${byName.id}.png`))) return join(dir, `${byName.id}.png`)
  return null
}

/** 没指定皮肤时用的默认皮肤路径（没有就 null） */
export function defaultSkinPath(dir) {
  const index = readIndex(dir)
  if (!isSkinId(index.defaultId)) return null
  const p = join(dir, `${index.defaultId}.png`)
  return existsSync(p) ? p : null
}
