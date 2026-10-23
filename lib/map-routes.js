/**
 * dsh-blender-mc / 背景地图库的 HTTP 半边
 *
 * 职责边界（很重要）：
 *   这里只是一层「薄壳」——收字节、落临时文件、spawn 一次 Python、把 JSON 转给前端。
 *   **所有磁盘写入都在 py/maplib.py 里**：解压、存档识别、索引 index.json 的读写
 *   只有一个写入者，避免 Node 和 Python 两边同时改索引打架。
 *
 * 为什么走同源 HTTP 而不是工具调用：
 *   浏览器上传 1 GB 级 zip 需要字节级进度条（xhr.upload.onprogress），
 *   工具调用没有这种通道；而且玩家在设置页点上传时不该触发一次模型回合。
 *
 * 为什么不用 multipart：
 *   裸字节直接 pipeline 落盘，服务端不必为了解析边界把整包读进内存。
 *
 * 路由（前缀挂在 webServer 上，供前端 fetch/XHR 调用）：:
 *
 *   GET  /api/blender-mc/maps                   地图列表
 *   POST /api/blender-mc/maps?filename=a.zip    上传并入库（202/201）
 *   POST /api/blender-mc/maps/rename            { id, name }
 *   POST /api/blender-mc/maps/delete            { id }
 *   POST /api/blender-mc/maps/default           { id }
 *   GET  /api/blender-mc/maps/<id>/thumb        缩略图（<img> 直连，不能带自定义头）
 *   GET  /api/blender-mc/status                 自检（排障用）
 */
import { spawn } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { createWriteStream, existsSync, promises as fsp } from 'node:fs'
import {
  SkinError, addSkin, defaultSkinsDir, defaultSkinPath, deleteSkin, listSkins,
  readSkinFile, resolveSkinPath, setDefaultSkin, clearDefaultSkin, MAX_SKIN_BYTES
} from './skin-store.js'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = '/api/blender-mc'
const HEADER = 'x-dsh-blender-mc'
const MAX_JSON = 64 * 1024
const ZIP_TYPES = ['application/zip', 'application/x-zip-compressed', 'application/octet-stream']

/** 插件内部错误码 → 前端文案表的短码（对不上的交给 errors.unknown 兜底） */
const CODE_ALIAS = {
  'map.duplicate': 'duplicate',
  'map.too-large': 'too_large',
  'zip.too-large': 'too_large',
  'zip.too-many-entries': 'zip_too_many_entries',
  'zip.unreadable': 'zip_unreadable',
  'zip.corrupt': 'zip_unreadable',
  'map.name-required': 'name_required',
  'map.name-too-long': 'name_too_long',
  'map.not-found': 'not_found',
  'map.bad-id': 'not_found',
  'zip.encrypted': 'zip_unreadable',
  'zip.slip-entry': 'not_zip',
  'zip.unsafe-entry': 'not_zip',
  'zip.ratio-suspicious': 'not_zip',
  'zip.entry-too-large': 'too_large',
  'map.empty': 'empty_file'
}

function alias(code) {
  return CODE_ALIAS[code] || code || 'unknown'
}

export function defaultMapsDir(env = process.env) {
  const home = env.DSH_HOME || join(env.USERPROFILE || homedir(), '.dsh')
  // 库根 = <DSH_HOME>/blender-mc。每个存档在它下面的 maps/<id>/world，
  // 所以这里不能再带 /maps（否则 python 侧会拼成 maps/maps/<id>，0.3.1 的 bug）。
  return join(home, 'blender-mc')
}

function sendJson(res, status, body) {
  const payload = Buffer.from(JSON.stringify(body), 'utf8')
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': String(payload.length),
    'cache-control': 'no-store'
  })
  res.end(payload)
}

function ok(res, status, data) {
  sendJson(res, status, { ok: true, data })
}

function fail(res, status, code, message, params) {
  sendJson(res, status, { ok: false, error: { code: alias(code), message, params: params || {} } })
}

async function readJsonBody(req) {
  let size = 0
  const chunks = []
  for await (const chunk of req) {
    size += chunk.length
    if (size > MAX_JSON) throw new Error('body too large')
    chunks.push(chunk)
  }
  if (!chunks.length) return {}
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    return null
  }
}

/**
 * 跑一次 python py/cli.py，取 stdout 里最后一坨 JSON。
 * 约定见 py/maplib.py：{"ok":true,"data":...} 或 {"ok":false,"error":{...}}
 *
 * ⚠️ 为什么把 stdio 重定向到文件而不是用管道：
 *   沙箱里（以及部分受限宿主）Node 用 pipe 抓子进程输出会直接 EPERM。
 *   写进临时文件再读回来，既不依赖管道，也顺便把 Python 的报错留全了。
 */
async function runPy(deps, args, timeoutMs) {
  const logPath = join(tmpdir(), `dsh-blender-mc-${randomUUID()}.log`)
  let fh = null
  try {
    fh = await fsp.open(logPath, 'w')
  } catch {
    fh = null
  }
  const child = spawn(deps.python, [deps.cli, ...args], {
    cwd: deps.pyDir,
    env: {
      ...process.env,
      ...deps.pyEnv,
      PYTHONIOENCODING: 'utf-8',
      PYTHONUNBUFFERED: '1'
    },
    stdio: fh ? ['ignore', fh.fd, fh.fd] : 'ignore',
    windowsHide: true
  })

  const result = await new Promise((resolveP) => {
    let settled = false
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL') } catch { /* 已经结束 */ }
      settle({ ok: false, error: { code: 'map.timeout', message: '处理超时，已中止' } })
    }, timeoutMs || 20 * 60 * 1000)
    const settle = (value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolveP(value)
    }
    child.on('error', (e) => settle({
      ok: false,
      error: { code: 'map.python-missing', message: `启动 Python 失败：${e.message}` }
    }))
    child.on('close', () => settle(null))
  })

  let text = ''
  try {
    text = await fsp.readFile(logPath, 'utf8')
  } catch { /* 没写出来就当空 */ }
  if (fh) await fh.close().catch(() => {})
  await fsp.rm(logPath, { force: true }).catch(() => {})

  if (result) return result

  const line = text.trim().split(/\r?\n/).filter((l) => l.startsWith('{')).pop()
  if (line) {
    try {
      const parsed = JSON.parse(line)
      if (parsed && typeof parsed.ok === 'boolean') return parsed
    } catch { /* 落到下面 */ }
  }
  return {
    ok: false,
    error: {
      code: 'map.internal',
      message: (text || 'Python 没有输出').trim().slice(-1500)
    }
  }
}

/** 给前端补上 thumbUrl / boundsKnown（前端只认这两个派生字段） */
function decorate(meta) {
  if (!meta || typeof meta !== 'object') return meta
  const out = { ...meta }
  out.boundsKnown = Boolean(meta.bounds)
  out.thumbUrl = meta.thumb ? `${BASE}/maps/${meta.id}/thumb` : null
  return out
}

export function registerMapRoutes(ctx, deps) {
  const ws = ctx?.webServer ?? (typeof ctx?.get === 'function' ? ctx.get('webServer') : undefined)
  if (!ws || typeof ws.register !== 'function') {
    ctx?.logger?.warn?.('dsh-blender-mc: 宿主没有 webServer 服务，背景地图上传入口不可用')
    return () => {}
  }

  const mapsDir = deps.mapsDir || defaultMapsDir()
  const maxUploadBytes = deps.maxUploadBytes || 2048 * 1024 * 1024
  const skinsDir = deps.skinsDir || defaultSkinsDir()
  const full = { ...deps, mapsDir, maxUploadBytes, skinsDir }
  const log = (...a) => ctx?.logger?.info?.(...a)

  /** 同源 + 自定义头校验（浏览器跨站表单发不出自定义头；服务端再兜一道 Origin） */
  function trusted(req, { needHeader = true } = {}) {
    if (needHeader && req.headers[HEADER] !== '1') return false
    const site = String(req.headers['sec-fetch-site'] || '')
    if (site === 'cross-site') return false
    const origin = req.headers.origin
    if (origin) {
      let expected = null
      try { expected = typeof ws.getOrigin === 'function' ? ws.getOrigin() : null } catch { expected = null }
      if (expected && origin !== expected) return false
    }
    return true
  }

  async function handleList(res) {
    const r = await runPy(full, ['map', 'list', '--root', mapsDir, '--json'], 60000)
    if (!r.ok) return fail(res, 500, r.error?.code, r.error?.message)
    const data = r.data || {}
    ok(res, 200, {
      maps: (data.maps || []).map(decorate),
      defaultId: data.defaultId || null,
      limits: { maxUploadBytes }
    })
  }

  async function handleUpload(req, res, url) {
    const filename = url.searchParams.get('filename') || ''
    if (!/\.zip$/i.test(filename)) {
      return fail(res, 400, 'map.bad-ext', '只接受 .zip 文件')
    }
    const ctype = String(req.headers['content-type'] || '').toLowerCase()
    if (ctype && !ZIP_TYPES.some((t) => ctype.startsWith(t))) {
      return fail(res, 415, 'map.bad-type', `不认识的 content-type：${ctype.slice(0, 60)}`)
    }
    const declared = Number(req.headers['content-length'] || 0)
    if (!Number.isFinite(declared) || declared <= 0) {
      return fail(res, 411, 'map.length-required', '缺少 Content-Length')
    }
    if (declared > maxUploadBytes) {
      return fail(res, 413, 'map.too-large',
        `文件有 ${(declared / 1024 ** 3).toFixed(2)} GiB，超过上限 ${(maxUploadBytes / 1024 ** 3).toFixed(0)} GiB`,
        { max: maxUploadBytes })
    }

    const tmpDir = join(mapsDir, '.tmp')
    await fsp.mkdir(tmpDir, { recursive: true })
    const tmpZip = join(tmpDir, `upload-${Date.now()}-${randomUUID().slice(0, 8)}.zip`)
    const hash = createHash('sha256')
    const started = Date.now()

    // ── 收字节：边收边算 sha256，超过上限立刻掐断 ────────────────────────
    try {
      await new Promise((resolveP, rejectP) => {
        const writer = createWriteStream(tmpZip)
        let received = 0
        let failed = false
        const boom = (err) => {
          if (failed) return
          failed = true
          try { writer.destroy() } catch { /* 忽略 */ }
          rejectP(err)
        }
        req.on('data', (chunk) => {
          received += chunk.length
          if (received > maxUploadBytes) {
            req.destroy()
            return boom(Object.assign(new Error('too large'), { code: 'map.too-large' }))
          }
          hash.update(chunk)
          if (!writer.write(chunk)) {
            req.pause()
            writer.once('drain', () => req.resume())
          }
        })
        req.on('error', boom)
        req.on('aborted', () => boom(Object.assign(new Error('aborted'), { code: 'map.cancelled' })))
        req.on('end', () => {
          if (failed) return
          writer.end(() => resolveP())
        })
        writer.on('error', boom)
      })
    } catch (error) {
      await fsp.rm(tmpZip, { force: true }).catch(() => {})
      if (error?.code === 'map.too-large') {
        return fail(res, 413, 'map.too-large', '文件超过上限，已中止')
      }
      if (error?.code === 'map.cancelled') return fail(res, 400, 'map.cancelled', '上传被取消')
      return fail(res, 500, 'map.io-error', `写临时文件失败：${error?.message || error}`)
    }

    const sha256 = hash.digest('hex')
    const receivedBytes = (await fsp.stat(tmpZip).catch(() => ({ size: 0 }))).size
    log(`dsh-blender-mc: 收到 ${filename}（${(receivedBytes / 1048576).toFixed(1)} MiB，${Date.now() - started} ms），开始解压识别`)

    // ── 交给 Python：解压 → 识别存档 → 入库 ────────────────────────────
    const jobPath = join(tmpDir, `job-${randomUUID()}.json`)
    await fsp.writeFile(jobPath, JSON.stringify({
      root: mapsDir,
      zip: tmpZip,
      name: url.searchParams.get('name') || undefined,
      sha256,
      sourceFilename: filename
    }), 'utf8')

    let result
    try {
      result = await runPy(full, ['map', 'import', '--job', jobPath, '--json'], 30 * 60 * 1000)
    } finally {
      await fsp.rm(jobPath, { force: true }).catch(() => {})
      await fsp.rm(tmpZip, { force: true }).catch(() => {})
    }

    if (!result.ok) {
      const code = result.error?.code || 'map.internal'
      const status = code === 'map.duplicate' ? 409
        : code === 'map.too-large' ? 413
          : code === 'map.not-found' ? 404
            : code === 'map.internal' ? 500 : 422
      log(`dsh-blender-mc: 导入失败 ${code} —— ${result.error?.message || ''}`)
      return fail(res, status, code, result.error?.message, result.error?.params)
    }
    const map = decorate(result.data)
    log(`dsh-blender-mc: 已入库 ${map.id}（${map.name}）${map.mcVersion || ''} ${map.area || ''}`)
    ok(res, 201, { map })
  }

  async function handleThumb(res, mapId) {
    if (!/^m_[0-9a-f]{12}$/.test(mapId)) return fail(res, 400, 'map.bad-id', '地图 id 不合法')
    const file = join(mapsDir, 'maps', mapId, 'thumb.png')
    if (!existsSync(file)) return fail(res, 404, 'map.no-thumb', '这张地图没有缩略图')
    const buf = await fsp.readFile(file)
    res.writeHead(200, {
      'content-type': 'image/png',
      'content-length': String(buf.length),
      'cache-control': 'private, max-age=300'
    })
    res.end(buf)
  }

  // ── 人物皮肤 ────────────────────────────────────────────────────────────
  async function handleSkinList(res) {
    const r = listSkins(skinsDir)
    return ok(res, 200, r)
  }

  async function handleSkinUpload(req, res, url) {
    const filename = url.searchParams.get('filename') || ''
    if (!/\.png$/i.test(filename)) {
      return fail(res, 400, 'skin.bad-ext', '皮肤只接受 .png 文件')
    }
    const ctype = String(req.headers['content-type'] || '').toLowerCase()
    if (ctype && !ctype.startsWith('image/png') && !ctype.startsWith('application/octet-stream')) {
      return fail(res, 415, 'skin.bad-type', `不认识的 content-type：${ctype.slice(0, 60)}`)
    }
    const declared = Number(req.headers['content-length'] || 0)
    if (!Number.isFinite(declared) || declared <= 0) {
      return fail(res, 411, 'skin.length-required', '缺少 Content-Length')
    }
    if (declared > MAX_SKIN_BYTES) {
      return fail(res, 413, 'skin.too-large',
        `皮肤有 ${(declared / 1024).toFixed(0)} KB，上限 ${(MAX_SKIN_BYTES / 1024).toFixed(0)} KB`)
    }
    const chunks = []
    let got = 0
    for await (const chunk of req) {
      got += chunk.length
      if (got > MAX_SKIN_BYTES) return fail(res, 413, 'skin.too-large', '皮肤文件太大')
      chunks.push(chunk)
    }
    const buf = Buffer.concat(chunks)
    try {
      const name = url.searchParams.get('name') || filename.replace(/\.png$/i, '')
      const skin = addSkin(skinsDir, buf, { name, filename })
      log(`dsh-blender-mc: 皮肤已入库 ${skin.id}（${skin.name}）${skin.w}×${skin.h}`)
      return ok(res, 201, { skin })
    } catch (error) {
      if (error instanceof SkinError) return fail(res, error.status, error.code, error.message, error.params)
      throw error
    }
  }

  async function handleSkinThumb(res, id) {
    try {
      const buf = readSkinFile(skinsDir, id)
      res.statusCode = 200
      res.setHeader('content-type', 'image/png')
      res.setHeader('content-length', String(buf.length))
      res.setHeader('cache-control', 'private, max-age=0, must-revalidate')
      res.setHeader('etag', `"${id}"`)
      res.end(buf)
    } catch (error) {
      if (error instanceof SkinError) return fail(res, error.status, error.code, error.message)
      throw error
    }
  }

  async function handler(req, res) {
    let url
    try {
      url = new URL(req.url || '/', 'http://localhost')
    } catch {
      return fail(res, 400, 'route.bad-url', 'URL 解析失败')
    }
    const pathname = url.pathname.replace(/\/+$/, '') || '/'
    const method = String(req.method || 'GET').toUpperCase()

    try {
      // 缩略图：<img> 直连发不出自定义头，只做 Origin/同源校验
      const thumb = /^\/api\/blender-mc\/maps\/([^/]+)\/thumb$/.exec(pathname)
      if (thumb) {
        if (method !== 'GET') return fail(res, 405, 'route.method', '只支持 GET')
        if (!trusted(req, { needHeader: false })) return fail(res, 403, 'forbidden', '跨站请求被拒绝')
        return await handleThumb(res, thumb[1])
      }

      const skinThumb = /^\/api\/blender-mc\/skins\/([^/]+)\/thumb$/.exec(pathname)
      if (skinThumb) {
        if (method !== 'GET') return fail(res, 405, 'route.method', '只支持 GET')
        if (!trusted(req, { needHeader: false })) return fail(res, 403, 'forbidden', '跨站请求被拒绝')
        return await handleSkinThumb(res, skinThumb[1])
      }

      if (!trusted(req)) return fail(res, 403, 'forbidden', '跨站请求被拒绝')

      if (pathname === `${BASE}/skins`) {
        if (method === 'GET') return await handleSkinList(res)
        if (method === 'POST') return await handleSkinUpload(req, res, url)
        return fail(res, 405, 'route.method', '只支持 GET / POST')
      }
      const skinMut = /^\/api\/blender-mc\/skins\/(default|delete|clear)$/.exec(pathname)
      if (skinMut) {
        if (method !== 'POST') return fail(res, 405, 'route.method', '只支持 POST')
        const body = await readJsonBody(req)
        if (!body || typeof body !== 'object') return fail(res, 400, 'route.bad-json', '请求体不是合法 JSON')
        try {
          if (skinMut[1] === 'clear') return ok(res, 200, clearDefaultSkin(skinsDir))
          const id = String(body.id || '')
          if (!/^s_[0-9a-f]{12}$/.test(id)) return fail(res, 400, 'not_found', '皮肤 id 不合法')
          if (skinMut[1] === 'default') return ok(res, 200, setDefaultSkin(skinsDir, id))
          return ok(res, 200, deleteSkin(skinsDir, id))
        } catch (error) {
          if (error instanceof SkinError) return fail(res, error.status, error.code, error.message)
          throw error
        }
      }

      if (pathname === `${BASE}/maps`) {
        if (method === 'GET') return await handleList(res)
        if (method === 'POST') return await handleUpload(req, res, url)
        return fail(res, 405, 'route.method', '只支持 GET / POST')
      }
      if (pathname === `${BASE}/health` || pathname === `${BASE}/status`) {
        if (method !== 'GET') return fail(res, 405, 'route.method', '只支持 GET')
        const r = await runPy(full, ['map', 'doctor', '--root', mapsDir, '--json'], 60000)
        return r.ok ? ok(res, 200, {
          mapsDir, maxUploadBytes, python: full.python,
          skinsDir, skinCount: listSkins(skinsDir).skins.length,
          ...r.data
        })
          : fail(res, 500, r.error?.code, r.error?.message)
      }
      const mut = /^\/api\/blender-mc\/maps\/(rename|delete|default)$/.exec(pathname)
      if (mut) {
        if (method !== 'POST') return fail(res, 405, 'route.method', '只支持 POST')
        const body = await readJsonBody(req)
        if (!body || typeof body !== 'object') return fail(res, 400, 'route.bad-json', '请求体不是合法 JSON')
        const id = String(body.id || '')
        if (!/^m_[0-9a-f]{12}$/.test(id)) return fail(res, 400, 'not_found', '地图 id 不合法')
        if (mut[1] === 'rename' && typeof body.name !== 'string') {
          return fail(res, 400, 'name_required', '名字不能为空')
        }
        const args = ['map', mut[1], '--root', mapsDir, '--id', id, '--json']
        if (mut[1] === 'rename') args.push('--name', String(body.name).slice(0, 200))
        const r = await runPy(full, args, 60000)
        return r.ok ? ok(res, 200, r.data) : fail(res, 422, r.error?.code, r.error?.message)
      }
      return fail(res, 404, 'route.not-found', `没有这个接口：${method} ${pathname}`)
    } catch (error) {
      const msg = error?.stack || String(error)
      ctx?.logger?.warn?.(`dsh-blender-mc: ${BASE} 处理失败 ${msg}`)
      if (!res.headersSent) fail(res, 500, 'internal', '插件后台出错了')
      else res.end()
    }
  }

  const dispose = ws.register({ kind: 'prefix', path: BASE, handler })
  log(`dsh-blender-mc: 背景地图接口已挂载 ${BASE}（地图 ${mapsDir} / 皮肤 ${skinsDir}）`)
  return () => {
    try { if (typeof dispose === 'function') dispose() } catch { /* 忽略 */ }
  }
}

/* ──────────────────────────────────────────────────────────────
 * 给 mc_shoot / mc_scan 用的解析器：地图库里的 id 或名字 → 真实存档目录
 * ────────────────────────────────────────────────────────────── */

const MAP_ID_RE = /^m_[0-9a-f]{12}$/

/**
 * 把「地图 id 或名字」解析成 { ok, savePath, map }。
 * 名字允许不精确：先全名，再忽略大小写，最后前缀匹配 —— 玩家在设置页看到什么就能填什么。
 */
export async function resolveMapWorld(deps, mapper) {
  const wanted = String(mapper ?? '').trim()
  if (!wanted) {
    return { ok: false, error: { code: 'map.missing', message: '没有给地图 id 或名字' } }
  }

  let id = wanted
  if (!MAP_ID_RE.test(wanted)) {
    const listed = await runPy(deps, ['map', 'list', '--root', deps.mapsDir, '--json'], 120000)
    if (!listed.ok) return listed
    const payload = listed.data || {}
    const maps = Array.isArray(payload) ? payload : (Array.isArray(payload.maps) ? payload.maps : [])
    if (!maps.length) {
      return { ok: false, error: { code: 'map.empty', message: '地图库还是空的，先去「设置 → 背景地图」上传一个存档 zip' } }
    }
    const low = wanted.toLowerCase()
    const hit =
      maps.find((m) => m.name === wanted) ||
      maps.find((m) => String(m.name || '').toLowerCase() === low) ||
      maps.find((m) => String(m.name || '').toLowerCase().startsWith(low)) ||
      maps.find((m) => String(m.id || '') === wanted)
    if (!hit) {
      const names = maps.map((m) => m.name).filter(Boolean).join('、')
      return {
        ok: false,
        error: { code: 'map.not-found', message: `地图库里没有「${wanted}」。可选：${names || '（无）'}` }
      }
    }
    id = hit.id
  }

  const res = await runPy(deps, ['map', 'resolve', '--root', deps.mapsDir, '--id', id, '--json'], 120000)
  if (!res.ok) return res
  const data = res.data || {}
  if (!data.savePath) {
    return { ok: false, error: { code: 'map.missing-world', message: '这条地图记录没有对应的存档目录' } }
  }
  return { ok: true, savePath: data.savePath, map: data.meta || {} }
}

/** 给工具侧列可选地图（没找到时的提示用）。 */
export async function listLibraryMaps(deps) {
  const res = await runPy(deps, ['map', 'list', '--root', deps.mapsDir, '--json'], 120000)
  if (!res.ok) return []
  const payload = res.data || {}
  const maps = Array.isArray(payload) ? payload : (Array.isArray(payload.maps) ? payload.maps : [])
  return maps.map((m) => ({ id: m.id, name: m.name }))
}
