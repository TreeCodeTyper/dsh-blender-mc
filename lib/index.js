/**
 * dsh-blender-mc —— Minecraft 存档 → Blender 封面 / 动画
 *
 * 用户只需要提供两样东西：
 *   ① Minecraft 存档目录（.mca 区域文件所在的那层）
 *   ② Blender 可执行文件（没装的话插件会告诉你去哪下）
 *
 * 其余全部由插件的 Python 侧负责：
 *   读存档 → 找机位 → 导出几何 → 调 Blender 搭场景 → 渲染
 *   → ★ OpenCV 自动审图 → 不合格自动调参重渲
 *
 * 设计要点：JS 侧只做「工具注册 + 参数校验 + 调 Python + 汇报」，
 * 真正的活全在 py/ 里。这样用户能在命令行直接跑同一套逻辑，
 * 不必依赖 DSH 才能用。
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'dsh-blender-mc'
export const inject = ['subprocess', 'tools']

const HERE = dirname(fileURLToPath(import.meta.url))
const PY_DIR = resolve(HERE, '..', 'py')
const CLI = join(PY_DIR, 'cli.py')

/** 找 Python：优先插件配置，其次常见位置 */
function findPython(config) {
  const cands = [
    config?.python,
    process.env.DSH_MC_PYTHON,
    process.env.PYTHON,
    'C:\\Python314\\python.exe',
    'C:\\Python313\\python.exe',
    'C:\\Python312\\python.exe',
    'python',
    'python3'
  ].filter(Boolean)
  for (const c of cands) {
    if (c === 'python' || c === 'python3' || existsSync(c)) return c
  }
  return 'python'
}

/** 跑一次 python cli.py，把输出按行回调 */
function runCli(ctx, config, args, onLine) {
  const py = findPython(config)
  const argv = [CLI, ...args]
  if (config?.blender) argv.unshift('--blender', config.blender)
  return new Promise((resolveP) => {
    let out = ''
    const child = spawn(py, argv, {
      cwd: PY_DIR,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
      windowsHide: true
    })
    const feed = (buf) => {
      const s = buf.toString('utf8')
      out += s
      if (onLine) for (const ln of s.split(/\r?\n/)) if (ln.trim()) onLine(ln)
    }
    child.stdout.on('data', feed)
    child.stderr.on('data', feed)
    child.on('error', (e) => { out += `\n[启动失败] ${e.message}\n`; resolveP({ code: -1, out }) })
    child.on('close', (code) => resolveP({ code, out }))
  })
}

/** 统一的工具返回格式 */
function report(title, code, out, extra = {}) {
  const tail = out.length > 6000 ? '…\n' + out.slice(-6000) : out
  return {
    ok: code === 0,
    title,
    exit_code: code,
    summary: code === 0 ? `${title} 完成` : `${title} 失败（exit ${code}）`,
    output: tail,
    ...extra
  }
}

export function apply(ctx, config = {}) {
  // cordis 约定：register 返回一个 dispose 句柄，要收好并在卸载时释放，
  // 否则热重载会留下重复注册的工具。dsh-ffmpeg 就是这么做的。
  const disposers = []
  const register = (def) => disposers.push(ctx.tools.register(def))
  // ─────────────────────────────────────────────────────
  // 1. 自检
  // ─────────────────────────────────────────────────────
  register({
    name: 'mc_doctor',
    description:
      '检查 dsh-blender-mc 的运行条件：Python 依赖（opencv/numpy）、Blender 路径、' +
      'MCprep 附加组件、角色骨架与物品库。任何 mc_* 工具报错时先跑这个。',
    parameters: { type: 'object', properties: {} },
    output: { schema: { type: 'object' } },
    timeoutMs: 600000,
    async execute() {
      const { code, out } = await runCli(ctx, config, ['doctor'])
      return report('自检', code, out)
    }
  })

  // ─────────────────────────────────────────────────────
  // 1.5 装配外部资产
  // ─────────────────────────────────────────────────────
  register({
    name: 'mc_setup',
    description:
      '装配插件需要的外部资产。这些资产（BPS 角色骨架、Minecraft 物品库、MCprep、ffmpeg）' +
      '都是第三方作品，不随插件分发，改为从用户本地已有的资源目录扫描并搬到 vendor/，' +
      '并把 MCprep 解压安装到 Blender 的 addons 目录。会自动跳过用不上的大件' +
      '（Mineways 251MB、MCSkin3D、ffmpeg 整包 96MB —— 本插件直接用 Python 读 .mca，' +
      '不需要 Mineways 那套 GUI 流程）。支持 --dry-run 先看会搬什么。',
    parameters: {
      type: 'object',
      properties: {
        sources: { type: 'array', items: { type: 'string' },
                   description: '资源目录，如 pack/。留空则默认用工作区里的 pack/' },
        dry_run: { type: 'boolean', description: '只看会搬什么，不实际搬' },
        link: { type: 'boolean', description: '用硬链接代替复制（省空间，同一磁盘才行）' }
      }
    },
    output: { schema: { type: 'object' } },
    timeoutMs: 1800000,
    async execute(args) {
      const a = ['setup', ...(args.sources || [])]
      if (args.dry_run) a.push('--dry-run')
      if (args.link) a.push('--link')
      const { code, out } = await runCli(ctx, config, a)
      return report('装配资产', code, out)
    }
  })

  // ─────────────────────────────────────────────────────
  // 2. 找机位
  // ─────────────────────────────────────────────────────
  register({
    name: 'mc_scan',
    description:
      '在 Minecraft 存档里找适合拍摄的位置：要求成片的同高度平地，' +
      '且视线方向上没有建筑遮挡。返回按「开阔度」排序的候选机位（坐标 + 视线方位角）。' +
      '扫描范围外的区域一律按「有遮挡」处理，避免选到村落广场那种被房子包围的点。',
    parameters: {
      type: 'object',
      properties: {
        save: { type: 'string', description: '存档目录（含 region/ 的那层）' },
        area: { type: 'string', description: '扫描范围 x0,x1,z0,z1' },
        y: { type: 'string', description: '纵向范围 y0,y1，默认 50,200' },
        radius: { type: 'number', description: '要求的平坦半径（格），默认 15' },
        top: { type: 'number', description: '返回几个候选，默认 12' },
        json: { type: 'string', description: '可选：把结果写到这个 json 文件' }
      },
      required: ['save', 'area']
    },
    output: { schema: { type: 'object' } },
    timeoutMs: 3600000,
    async execute(args) {
      const a = ['scan', args.save, '--area', String(args.area)]
      if (args.y) a.push('--y', String(args.y))
      if (args.radius) a.push('--radius', String(args.radius))
      if (args.top) a.push('--top', String(args.top))
      if (args.json) a.push('--json', args.json)
      const { code, out } = await runCli(ctx, config, a)
      return report('找机位', code, out)
    }
  })

  // ─────────────────────────────────────────────────────
  // 3. 出封面（含审图闭环）
  // ─────────────────────────────────────────────────────
  register({
    name: 'mc_shoot',
    description:
      '从 Minecraft 存档出一张 Blender 封面：自动找机位 → 导出几何 → 搭场景（MCprep 材质染色 + ' +
      '角色骨架 + 手持物品 + 物理天空 + 三点布光）→ 渲染 → OpenCV 自动审图。' +
      '审出问题（背影 / 脚没踩地 / 过曝 / 看不出布光 / 主体太小）会【自动调参重渲】，最多三轮。' +
      '看 issues 字段就知道这张图还有哪些毛病。',
    parameters: {
      type: 'object',
      properties: {
        save: { type: 'string', description: '存档目录' },
        area: { type: 'string', description: '可用区域 x0,x1,z0,z1' },
        out: { type: 'string', description: '输出目录' },
        center: { type: 'string', description: '可选：指定机位 x,z（不给就自动找）' },
        yaw: { type: 'number', description: '角色朝向（度）。相机在 -Y，yaw≈25 是 3/4 侧脸面向镜头' },
        cam_az: { type: 'number', description: '视线方位角（MC 角度）' },
        dist: { type: 'number', description: '相机距离' },
        sky: { type: 'number', description: '天空强度。用「标准」视图转换时必须 ≤0.05，否则削顶成白' },
        sink: { type: 'number', description: '脚陷入地面的深度，默认 0.06（防止看起来悬浮）' },
        view_transform: { type: 'string', description: 'Standard / Filmic / AgX' },
        attempts: { type: 'number', description: '自动重渲轮数上限，默认 3' },
        target: { type: 'number', description: '目标分，默认 85' },
        radius: { type: 'number', description: '导出半径（格），默认 44' }
      },
      required: ['save', 'area', 'out']
    },
    output: { schema: { type: 'object' } },
    timeoutMs: 7200000,
    async execute(args) {
      const a = ['shoot', args.save, '--area', String(args.area), '--out', args.out]
      for (const k of ['center', 'view_transform']) if (args[k]) a.push('--' + k.replace('_', '-'), String(args[k]))
      for (const k of ['yaw', 'cam_az', 'dist', 'sky', 'sink', 'attempts', 'target', 'radius'])
        if (args[k] !== undefined && args[k] !== null) a.push('--' + k.replace('_', '-'), String(args[k]))
      const { code, out } = await runCli(ctx, config, a)
      return report('出封面', code, out)
    }
  })

  // ─────────────────────────────────────────────────────
  // 4. 出动画
  // ─────────────────────────────────────────────────────
  register({
    name: 'mc_anim',
    description:
      '在封面工程上做动画并渲染帧序列。默认「杀戮光环」：角色挥剑两刀 + 周围旋转的发光能量环 + ' +
      '斩击弧光，10 秒 / 240 帧 / 24fps。渲完用 ffmpeg 转 MP4（命令会一并返回）。' +
      '先用 preview 只渲几帧验证动作，再跑全片。',
    parameters: {
      type: 'object',
      properties: {
        blend: { type: 'string', description: '封面工程 .blend（由 mc_shoot 产出）' },
        out: { type: 'string', description: '输出目录' },
        frames: { type: 'number', description: '总帧数，默认 240（24fps = 10 秒）' },
        fps: { type: 'number', description: '帧率，默认 24' },
        preview: { type: 'string', description: '只渲这些帧，如 "1,40,60,120"。强烈建议先跑一遍' }
      },
      required: ['blend', 'out']
    },
    output: { schema: { type: 'object' } },
    timeoutMs: 14400000,
    async execute(args) {
      const a = ['anim', args.blend, '--out', args.out]
      if (args.frames) a.push('--frames', String(args.frames))
      if (args.fps) a.push('--fps', String(args.fps))
      if (args.preview) a.push('--preview', String(args.preview))
      const { code, out } = await runCli(ctx, config, a)
      return report('出动画', code, out)
    }
  })

  // ─────────────────────────────────────────────────────
  // 5. 只审图
  // ─────────────────────────────────────────────────────
  register({
    name: 'mc_check',
    description:
      '用 OpenCV 审渲染结果，不需要跑 Blender。检查：朝向（正面/背面，靠头部区域的皮肤色占比）、' +
      '接地（脚底下方与旁边地面的色差）、曝光（死白/死黑/动态范围）、布光（地面灰阶方差）、' +
      '构图（主体占比/位置/留白）。返回 verdict(PASS/WARN/FAIL) + 分数 + 具体问题列表。',
    parameters: {
      type: 'object',
      properties: {
        images: { type: 'array', items: { type: 'string' }, description: '图片路径' },
        json: { type: 'boolean', description: '输出 json' }
      },
      required: ['images']
    },
    output: { schema: { type: 'object' } },
    timeoutMs: 600000,
    async execute(args) {
      const a = ['check', ...args.images]
      if (args.json) a.push('--json')
      const { code, out } = await runCli(ctx, config, a)
      return report('审图', code, out)
    }
  })

  ctx.logger?.info?.('dsh-blender-mc 已加载：mc_doctor / mc_setup / mc_scan / mc_shoot / mc_anim / mc_check')

  if (typeof ctx.on === 'function') {
    ctx.on('dispose', () => {
      for (const dispose of disposers) dispose()
    })
  }
}
