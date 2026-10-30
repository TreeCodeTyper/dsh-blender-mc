#!/usr/bin/env node
/**
 * smoke.mjs —— 插件外壳冒烟测试
 *
 * 不启动 DSH，用一个假的 ctx 调 apply()，验证：
 *   ① 模块能 import
 *   ② 三个必需导出都在（name / inject / apply）
 *   ③ apply() 不抛异常
 *   ④ 该注册的工具都注册上了
 *   ⑤ 每个工具的 definition 形状合法（name/description/parameters/execute）
 *   ⑥ 卸载时 disposer 被调用（不会留下重复注册）
 *
 * 用法: node scripts/smoke.mjs
 * 退出码 0 = 通过，1 = 失败
 */
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ENTRY = resolve(HERE, '..', 'lib', 'index.js')

/** 期望注册的工具名 —— 少一个就是坏了，多一个说明更新了这里 */
const EXPECTED = ['mc_doctor', 'mc_setup', 'mc_extract_models', 'mc_scan',
  'mc_shoot', 'mc_anim', 'mc_film', 'mc_check', 'mc_mobs']

let failed = 0
const ok = (m) => console.log(`  \x1b[32m✓\x1b[0m ${m}`)
const bad = (m) => { failed++; console.log(`  \x1b[31m✗\x1b[0m ${m}`) }

console.log(`\n冒烟测试: ${ENTRY}\n${'─'.repeat(60)}`)

// ── 1. import ──
let mod
try {
  mod = await import(pathToFileURL(ENTRY).href)
  ok('模块 import 成功')
} catch (e) {
  bad(`import 失败: ${e.message}`)
  process.exit(1)
}

// ── 2. 导出 ──
typeof mod.name === 'string' && mod.name
  ? ok(`name = ${mod.name}`)
  : bad('缺少 export const name')

Array.isArray(mod.inject)
  ? ok(`inject = [${mod.inject.join(', ')}]`)
  : bad('缺少 export const inject')

typeof mod.apply === 'function'
  ? ok('apply 是函数')
  : bad('缺少 export function apply')

if (failed) { console.log('\n导出不全，后面的测试没意义\n'); process.exit(1) }

// ── 3~6. 用假 ctx 跑 apply ──
const registered = []
const disposeCalls = []
const disposeHandlers = []

const fakeCtx = {
  tools: {
    register(def) {
      registered.push(def)
      return () => disposeCalls.push(def.name)
    }
  },
  on(event, handler) {
    if (event === 'dispose') disposeHandlers.push(handler)
  },
  logger: { info() {}, warn() {}, error() {} },
  // ctx.subprocess 故意不给 —— 当前实现不依赖它；
  // 将来若改用 ctx.subprocess，这里要补上假的
}

try {
  mod.apply(fakeCtx, {})
  ok('apply() 执行无异常')
} catch (e) {
  bad(`apply() 抛异常: ${e.message}`)
  console.log(e.stack)
  process.exit(1)
}

// ── 注册数量与名字 ──
const got = registered.map((d) => d.name)
const missing = EXPECTED.filter((n) => !got.includes(n))
const extra = got.filter((n) => !EXPECTED.includes(n))

missing.length === 0
  ? ok(`注册了全部 ${EXPECTED.length} 个工具`)
  : bad(`少了这些工具: ${missing.join(', ')}`)

if (extra.length) {
  console.log(`  \x1b[33m!\x1b[0m 多出未在 EXPECTED 里的工具: ${extra.join(', ')}`)
  console.log('    （如果是有意新增，请更新 scripts/smoke.mjs 的 EXPECTED）')
}

// ── definition 形状 ──
let shapeBad = 0
for (const d of registered) {
  const problems = []
  if (typeof d.name !== 'string' || !d.name) problems.push('name')
  if (typeof d.description !== 'string' || d.description.length < 20) {
    problems.push('description(太短 —— 模型靠它判断何时调用)')
  }
  if (typeof d.execute !== 'function') problems.push('execute')
  if (d.parameters && d.parameters.type !== 'object') problems.push('parameters.type')
  if (problems.length) {
    bad(`${d.name}: 缺 ${problems.join(', ')}`)
    shapeBad++
  }
}
if (!shapeBad) ok('所有工具的 definition 形状合法')

// ── description 质量（能被模型正确选中）──
// 经验值：<80 字符的描述往往说不清"什么时候该用它"
const thin = registered.filter((d) => (d.description || '').length < 80)
if (thin.length) {
  console.log(`  \x1b[33m!\x1b[0m 这些工具的 description 偏短，模型可能选不准:`)
  for (const d of thin) console.log(`      ${d.name} (${d.description.length} 字符)`)
} else {
  ok('所有工具的 description 长度合理')
}

// ── dispose ──
if (disposeHandlers.length === 0) {
  bad('没有注册 ctx.on("dispose") —— 热重载会留下重复注册的工具')
} else {
  for (const h of disposeHandlers) h()
  disposeCalls.length === registered.length
    ? ok(`dispose 释放了全部 ${disposeCalls.length} 个注册`)
    : bad(`dispose 只释放了 ${disposeCalls.length}/${registered.length} 个`)
}

// ── 汇总 ──
console.log('─'.repeat(60))
if (failed) {
  console.log(`\x1b[31m失败 ${failed} 项\x1b[0m\n`)
  process.exit(1)
}
console.log('\x1b[32m全部通过\x1b[0m\n')
