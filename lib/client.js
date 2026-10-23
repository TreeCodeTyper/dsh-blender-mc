/**
 * dsh-blender-mc · 背景地图管理（客户端半边）
 * ===============================================
 *
 * 装载协议：手写的 lazy-CJS bundle（`window.__ModuleLoader__.load` + factory 返回
 * cordis 插件导出），不经过构建、不 import 任何 dsh client 包 —— 和 @liustack/modlens、
 * dsh-bash-terminal-ts 同一套写法。改完存盘 → 刷新页面即生效。
 *
 * 挂载点：`settings.section`（顶层设置页的一个独立分节）。
 *   · settings.section 是按 id 注册的，不依赖插件管理器里的行 key，包改名也不会掉；
 *   · 地图库是「缩略图卡片网格 + 上传区」，需要整页宽度，比 settings.plugin.item 里
 *     那一行窄卡片合适得多；
 *   · 用 `ctx.slots.inject(...)` 等槽位声明，因此不把 ui-settings* 包写进
 *     dsh.client.inject，换 profile 也不会因为少装某个 UI 包而整块不加载。
 *   想要挪到插件配置卡片里，把下面 registerSection() 里那三行换成 WIRING.md 末尾
 *   给的 settings.plugin.item / plugins.bundle.config 片段即可，组件本身不用动。
 *
 * 数据来源：宿主半边 lib/map-routes.js 暴露的 /api/blender-mc/maps 路由族。
 *   客户端全部用同源 fetch / XHR，不用 typert remote —— 上传要字节级进度，
 *   XHR 的 upload.onprogress 是唯一干净的来源；也省掉一份编解码契约。
 *   接口表见 ../README.md「契约」一节。
 *
 * 主题：只用 --dsw-alias-* / --dsw-radius-* / --dsw-focus-ring-* 主题 token，
 *   明暗主题由 token 自己切换，没有一处写死的颜色字面量。
 */

window.__ModuleLoader__.load({
  id: 'dsh-blender-mc',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports

    /** i18n 命名空间：同时用作 slots.register 的 locale 与 settings.section 的 id 前缀 */
    var NS = 'dsh-blender-mc.maps'
    /** 宿主路由根；lib/map-routes.js 必须注册同一前缀 */
    var API = '/api/blender-mc/maps'
    var SKIN_API = '/api/blender-mc/skins'
    /** 所有写操作都带这个头：跨站表单/简单请求带不了自定义头，等价于一道 CSRF 闸 */
    var HEADER = 'x-dsh-blender-mc'
    var STYLE_ID = 'dsh-blender-mc-maps-style'
    var MAX_NAME = 80
    // 只在拿不到服务端 limits 时的兜底，必须和宿主默认 maxUploadMb=2048 对齐：
    // 写成 200 MB 会让 900 MB+ 的整合包在客户端就被挡下（且根本不发请求），
    // 用户只会看到「超过上限」，像是插件没工作。
    var DEFAULT_MAX_UPLOAD = 2048 * 1024 * 1024

    // <locale:begin> 由 tools/embed-locale.mjs 从 ../locale/*.json 生成，勿手改
    // 皮肤用的文案单独一张小表：localeTables 是 tools/embed-locale.mjs 从
    // locale/*.json 生成并替换 <locale:begin> 区块的，手改会被覆盖。
    var skinTables = {
      zh: {
        'skins.title': '人物皮肤',
        'skins.subtitle': '上传 64×64（或 128 / 256 / 512）的 Minecraft 皮肤 PNG。设成默认后，出封面/出动画时角色直接用它；不留皮肤就是原版 Steve。',
        'skins.drop': '把皮肤 PNG 拖到这里',
        'skins.dragging': '松开即可上传',
        'skins.button': '选择皮肤',
        'skins.hint': '正方形 PNG，最大 2 MB；经典（宽手臂）皮肤效果最好',
        'skins.empty': '还没有上传皮肤',
        'skins.emptyBody': '上传一张 PNG 就能换掉 Steve 的皮肤',
        'skins.default': '默认',
        'skins.setDefault': '设为默认',
        'skins.delete': '删除',
        'skins.count': '{count} 张',
        'skins.uploading': '上传中 {percent}%',
        'skins.done': '已上传「{name}」',
        'skins.deleted': '已删除皮肤',
        'skins.useIt': '之后 mc_shoot({ skin: "{name}" }) 就能用它',
        'errors.skin_not_png': '不是 PNG 文件',
        'errors.skin_bad_size': '皮肤必须是正方形，边长 64 / 128 / 256 / 512',
        'errors.skin_too_large': '皮肤文件太大'
      },
      en: {
        'skins.title': 'Player skin',
        'skins.subtitle': 'Upload a Minecraft skin PNG (64x64, or 128 / 256 / 512). Set it as default and covers/animations use it; upload nothing and you keep vanilla Steve.',
        'skins.drop': 'Drop a skin PNG here',
        'skins.dragging': 'Release to upload',
        'skins.button': 'Choose skin',
        'skins.hint': 'Square PNG, max 2 MB; classic (wide-arm) skins look best',
        'skins.empty': 'No skin uploaded yet',
        'skins.emptyBody': 'Upload a PNG to replace Steve',
        'skins.default': 'default',
        'skins.setDefault': 'Set default',
        'skins.delete': 'Delete',
        'skins.count': '{count} skin(s)',
        'skins.uploading': 'Uploading {percent}%',
        'skins.done': 'Uploaded "{name}"',
        'skins.deleted': 'Skin deleted',
        'skins.useIt': 'Then mc_shoot({ skin: "{name}" }) uses it',
        'errors.skin_not_png': 'Not a PNG file',
        'errors.skin_bad_size': 'Skin must be square, 64 / 128 / 256 / 512',
        'errors.skin_too_large': 'Skin file too large'
      }
    }

    var localeTables = {
      "zh": {
        "section.title": "背景地图",
        "section.subtitle": "上传 Minecraft 存档 zip（整合包地图也行）。插件会解压、识别存档、生成缩略图；之后出封面时用 map 参数指定这个名字即可。地图只存在本机插件数据目录里。",
        "actions.refresh": "刷新",
        "actions.retry": "重试",
        "actions.dismiss": "关闭",
        "upload.drop": "把 .zip 地图包拖到这里",
        "upload.dragging": "松开即可上传",
        "upload.hint": "仅支持 .zip，单个最大 {max}",
        "upload.button": "选择文件",
        "upload.cancel": "取消上传",
        "upload.progress": "上传中 {percent}%（{loaded} / {total}）",
        "upload.done": "已上传「{name}」",
        "list.empty.title": "还没有地图",
        "list.empty.body": "先上传一个地图 zip，之后就能在这里改名、设默认、删除。",
        "list.count": "共 {count} 个地图",
        "list.loading": "正在读取地图列表…",
        "card.default": "默认",
        "card.setDefault": "设为默认",
        "card.rename": "重命名",
        "card.delete": "删除",
        "card.confirmDelete": "确认删除？",
        "card.confirmYes": "删除",
        "card.confirmNo": "取消",
        "card.bounds": "坐标 X {xMin}~{xMax} · Z {zMin}~{zMax}",
        "card.boundsUnknown": "坐标范围未声明",
        "card.dimension": "维度 {dimension}",
        "card.uploadedAt": "上传于 {when}",
        "card.noPreview": "无预览图",
        "card.renamingHint": "Enter 保存，Esc 取消",
        "card.working": "处理中…",
        "errors.network": "连不上插件后台。确认 DSH Web 正在运行，然后重试。",
        "errors.host_route_missing": "插件后台还没有上传接口。把 dsh-blender-mc 更新到包含 lib/map-routes.js 的版本后重载 DSH。",
        "errors.bad_ext": "只接受 .zip 文件。",
        "errors.empty_file": "这个文件是空的。",
        "errors.too_large": "文件超过上限（{max}）。",
        "errors.not_zip": "不是有效的 zip 包。",
        "errors.zip_unreadable": "压缩包已损坏，或包含了不支持的压缩方式。",
        "errors.zip_too_many_entries": "zip 里的文件数量过多，已拒绝解析。",
        "errors.manifest_invalid": "map.json 格式不对：坐标必须是数字，且最小值要小于最大值。",
        "errors.duplicate": "这个地图已经上传过了。",
        "errors.name_required": "名称不能为空。",
        "errors.name_too_long": "名称不能超过 {max} 个字。",
        "errors.not_found": "地图不存在，可能已经被删掉了。",
        "errors.busy": "后台正在写入，稍后再试。",
        "errors.internal": "插件后台出错了。",
        "errors.forbidden": "请求被拒绝（同源校验未通过）。",
        "errors.unknown": "出错了：{message}"
      },
      "en": {
        "section.title": "Background maps",
        "section.subtitle": "Upload a Minecraft save zip (modpack maps are fine). The plugin unpacks it, detects the save and renders a thumbnail; afterwards just pass its name as the map argument when shooting. Maps stay in this machine's plugin data directory.",
        "actions.refresh": "Refresh",
        "actions.retry": "Retry",
        "actions.dismiss": "Dismiss",
        "upload.drop": "Drop a .zip map package here",
        "upload.dragging": "Release to upload",
        "upload.hint": ".zip only, up to {max} each",
        "upload.button": "Choose file",
        "upload.cancel": "Cancel upload",
        "upload.progress": "Uploading {percent}% ({loaded} / {total})",
        "upload.done": "Uploaded “{name}”",
        "list.empty.title": "No maps yet",
        "list.empty.body": "Upload a map zip first; after that you can rename it, make it the default, or delete it here.",
        "list.count": "{count} maps",
        "list.loading": "Loading maps…",
        "card.default": "Default",
        "card.setDefault": "Set as default",
        "card.rename": "Rename",
        "card.delete": "Delete",
        "card.confirmDelete": "Delete this map?",
        "card.confirmYes": "Delete",
        "card.confirmNo": "Cancel",
        "card.bounds": "X {xMin}~{xMax} · Z {zMin}~{zMax}",
        "card.boundsUnknown": "No coordinate range declared",
        "card.dimension": "Dimension {dimension}",
        "card.uploadedAt": "Uploaded {when}",
        "card.noPreview": "No preview image",
        "card.renamingHint": "Enter to save, Esc to cancel",
        "card.working": "Working…",
        "errors.network": "Cannot reach the plugin host. Make sure DSH Web is running, then retry.",
        "errors.host_route_missing": "The plugin host has no upload route yet. Update dsh-blender-mc to a version containing lib/map-routes.js, then reload DSH.",
        "errors.bad_ext": "Only .zip files are accepted.",
        "errors.empty_file": "That file is empty.",
        "errors.too_large": "The file exceeds the limit ({max}).",
        "errors.not_zip": "That is not a valid zip archive.",
        "errors.zip_unreadable": "The zip is corrupt or contains an unsupported compression method.",
        "errors.zip_too_many_entries": "The zip contains too many entries and was refused.",
        "errors.manifest_invalid": "map.json is invalid: bounds must be numbers with min < max.",
        "errors.duplicate": "This map was already uploaded.",
        "errors.name_required": "The name cannot be empty.",
        "errors.name_too_long": "The name cannot exceed {max} characters.",
        "errors.not_found": "That map no longer exists.",
        "errors.busy": "The host is busy writing; try again shortly.",
        "errors.internal": "The plugin host failed.",
        "errors.forbidden": "Request refused (same-origin check failed).",
        "errors.unknown": "Something went wrong: {message}"
      }
    }
    // <locale:end>

    /** React 由 apply() 里 require('react') 赋值；组件在渲染时才用到它 */
    var React = null
    var boundTranslate = null

    // ── 极简 h()：不用 JSX，也不引入 jsx-runtime ────────────────────────────
    function h(type, props) {
      var args = [type, props]
      for (var i = 2; i < arguments.length; i++) args.push(arguments[i])
      return React.createElement.apply(null, args)
    }

    // ── 语言 / 翻译 ────────────────────────────────────────────────────────
    function activeLang() {
      var lang = ''
      try {
        var el = typeof document !== 'undefined' ? document.documentElement : null
        lang = (el && (el.getAttribute('lang') || el.getAttribute('data-lang'))) || ''
        if (!lang && typeof navigator !== 'undefined') lang = navigator.language || ''
      } catch (error) {
        lang = ''
      }
      return /^en/i.test(lang) ? 'en' : 'zh'
    }

    function interpolate(text, params) {
      if (!params) return text
      return String(text).replace(/\{(\w+)\}/g, function (whole, key) {
        return params[key] === undefined || params[key] === null ? whole : String(params[key])
      })
    }

    function tableTranslate(key, params) {
      var lang = activeLang()
      var table = localeTables[lang] || localeTables.zh || {}
      var text = table[key]
      if (text === undefined) text = (localeTables.zh || {})[key]
      return text === undefined ? key : interpolate(text, params)
    }

    /**
     * 宿主绑定的翻译优先（settings 页会按 slot 的 locale 绑好），
     * 但它绑错命名空间时会原样返回 key —— 那种情况回落到自带词表，
     * 不至于在界面上看见 `card.rename` 这种字样。
     */
    function tr(key, params) {
      if (typeof boundTranslate === 'function') {
        var text = ''
        try {
          text = boundTranslate(key, params)
        } catch (error) {
          text = ''
        }
        if (typeof text === 'string' && text !== '' && text !== key) return text
      }
      return tableTranslate(key, params)
    }

    /** 组件里取翻译：优先用槽位传下来的 props.t，同样带回落 */
    /** 皮肤词条：先查 skinTables，查不到退回地图词表 */
    function ts(key, params) {
      var lang = activeLang()
      var tbl = skinTables[lang] || skinTables.zh
      var text = tbl[key]
      if (text === undefined) return tr(key, params)
      return interpolate(text, params)
    }

    function translatorOf(props) {
      var external = props && typeof props.t === 'function' ? props.t : null
      return function (key, params) {
        if (external) {
          var text = ''
          try {
            text = external(key, params)
          } catch (error) {
            text = ''
          }
          if (typeof text === 'string' && text !== '' && text !== key) return text
        }
        return tr(key, params)
      }
    }

    function setTranslator(fn) {
      boundTranslate = fn
    }

    // ── 展示格式化 ──────────────────────────────────────────────────────────
    function formatBytes(bytes) {
      if (typeof bytes !== 'number' || !isFinite(bytes) || bytes < 0) return '—'
      if (bytes < 1024) return bytes + ' B'
      var units = ['KB', 'MB', 'GB']
      var value = bytes
      var index = -1
      do {
        value = value / 1024
        index += 1
      } while (value >= 1024 && index < units.length - 1)
      var rounded = value >= 100 ? Math.round(value) : Math.round(value * 10) / 10
      return rounded + ' ' + units[index]
    }

    function formatWhen(iso) {
      if (!iso) return '—'
      var date = new Date(iso)
      if (isNaN(date.getTime())) return '—'
      try {
        return date.toLocaleString(activeLang() === 'en' ? 'en-US' : 'zh-CN', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit'
        })
      } catch (error) {
        return date.toISOString().slice(0, 16).replace('T', ' ')
      }
    }

    // ── 与宿主通信 ──────────────────────────────────────────────────────────
    /** 把任意失败归一成 { code, message, params } */
    function normalizeError(json, status) {
      if (json && json.error && typeof json.error.code === 'string') {
        return { code: json.error.code, message: json.error.message, params: json.error.params }
      }
      if (status === 403) return { code: 'forbidden' }
      if (status === 404) return { code: 'host_route_missing' }
      if (status === 405) return { code: 'internal', message: 'HTTP 405' }
      return { code: 'internal', message: 'HTTP ' + (status || '?') }
    }

    function requestJson(method, path, body) {
      var headers = { accept: 'application/json' }
      headers[HEADER] = '1'
      var init = { method: method, credentials: 'same-origin', headers: headers }
      if (body !== undefined) {
        headers['content-type'] = 'application/json'
        init.body = JSON.stringify(body)
      }
      return fetch(path, init).then(
        function (response) {
          return response.text().then(function (text) {
            var json = null
            try {
              json = text ? JSON.parse(text) : null
            } catch (error) {
              json = null
            }
            if (response.ok && json && json.ok) return json.data || {}
            throw normalizeError(json, response.status)
          })
        },
        function () {
          throw { code: 'network' }
        }
      )
    }

    /**
     * 上传：裸字节 PUT 进请求体，用 XHR 拿真实的字节级进度。
     * 不用 multipart —— 宿主可以 pipeline 直接落盘，不必为解析边界把整包读进内存。
     */
    function uploadMap(file, onProgress) {
      var xhr = new XMLHttpRequest()
      var query = '?filename=' + encodeURIComponent(file.name) + '&size=' + encodeURIComponent(String(file.size))
      var promise = new Promise(function (resolve, reject) {
        xhr.open('POST', API + query, true)
        xhr.setRequestHeader('content-type', 'application/zip')
        xhr.setRequestHeader(HEADER, '1')
        xhr.responseType = 'text'
        xhr.upload.onprogress = function (event) {
          if (event.lengthComputable) onProgress({ loaded: event.loaded, total: event.total })
        }
        xhr.onload = function () {
          var json = null
          try {
            json = xhr.responseText ? JSON.parse(xhr.responseText) : null
          } catch (error) {
            json = null
          }
          if (xhr.status >= 200 && xhr.status < 300 && json && json.ok) resolve(json.data || {})
          else reject(normalizeError(json, xhr.status))
        }
        xhr.onerror = function () {
          reject({ code: 'network' })
        }
        xhr.onabort = function () {
          reject({ code: 'cancelled' })
        }
        xhr.ontimeout = function () {
          reject({ code: 'network' })
        }
        xhr.send(file)
      })
      return {
        promise: promise,
        abort: function () {
          try {
            xhr.abort()
          } catch (error) {
            /* 已经结束 */
          }
        }
      }
    }

    /** 皮肤上传：同 uploadMap，只是 content-type 换成 image/png */
    function uploadSkin(file, onProgress) {
      var xhr = new XMLHttpRequest()
      var query = '?filename=' + encodeURIComponent(file.name) + '&size=' + encodeURIComponent(String(file.size))
      var promise = new Promise(function (resolve, reject) {
        xhr.open('POST', SKIN_API + query, true)
        xhr.setRequestHeader('content-type', 'image/png')
        xhr.setRequestHeader(HEADER, '1')
        xhr.responseType = 'text'
        if (xhr.upload && onProgress) {
          xhr.upload.onprogress = function (event) {
            if (event.lengthComputable) onProgress(event.loaded / event.total)
          }
        }
        xhr.onload = function () {
          var json = null
          try {
            json = xhr.responseText ? JSON.parse(xhr.responseText) : null
          } catch (error) {
            json = null
          }
          if (xhr.status >= 200 && xhr.status < 300 && json && json.ok) resolve(json.data || {})
          else reject(normalizeError(json, xhr.status))
        }
        xhr.onerror = function () {
          reject({ code: 'network' })
        }
        xhr.send(file)
      })
      return {
        promise: promise,
        abort: function () {
          try {
            xhr.abort()
          } catch (error) {
            /* 已经结束了 */
          }
        }
      }
    }

    function validateFile(file, limits) {
      var name = String((file && file.name) || '')
      if (!/\.zip$/i.test(name)) return { code: 'bad_ext' }
      if (!file || !(file.size > 0)) return { code: 'empty_file' }
      var max = limits && typeof limits.maxUploadBytes === 'number' ? limits.maxUploadBytes : DEFAULT_MAX_UPLOAD
      if (file.size > max) return { code: 'too_large', params: { max: formatBytes(max) } }
      return null
    }

    function errorText(t, error) {
      if (!error) return ''
      var key = 'errors.' + (error.code || 'unknown')
      var text = t(key, error.params)
      if (text === key) return t('errors.unknown', { message: error.message || error.code || '' })
      return text
    }

    // ── 样式：一次性注入，全部走主题 token ──────────────────────────────────
    var CSS = [
      '.mcbm-root{display:flex;flex-direction:column;gap:16px;color:var(--dsw-alias-label-primary);font-family:var(--dsw-font-family,inherit);font-size:13px;line-height:1.5}',
      '.mcbm-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}',
      '.mcbm-title{margin:0;font-size:15px;font-weight:600;line-height:1.35}',
      '.mcbm-sub{margin:4px 0 0;font-size:12px;line-height:1.6;color:var(--dsw-alias-label-secondary);max-width:62ch}',
      '.mcbm-btn{appearance:none;display:inline-flex;align-items:center;gap:6px;border:1px solid var(--dsw-alias-border-l2);border-radius:var(--dsw-radius-md,8px);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);padding:6px 12px;font:inherit;font-size:12px;cursor:pointer;transition:background-color .15s ease,border-color .15s ease}',
      '.mcbm-btn:hover:not([disabled]){background:var(--dsw-alias-interactive-bg-hover)}',
      '.mcbm-btn:active:not([disabled]){background:var(--dsw-alias-interactive-bg-active)}',
      '.mcbm-btn:focus-visible{outline:var(--dsw-focus-ring-width,2px) solid var(--dsw-focus-ring-color,var(--dsw-alias-brand-primary));outline-offset:2px}',
      '.mcbm-btn[disabled]{opacity:.55;cursor:not-allowed}',
      '.mcbm-btn--primary{background:var(--dsw-alias-button-primary-fill);border-color:transparent;color:var(--dsw-alias-label-primary-foreground)}',
      '.mcbm-btn--primary:hover:not([disabled]){background:var(--dsw-alias-button-primary-fill);opacity:.9}',
      '.mcbm-btn--danger{color:var(--dsw-alias-label-error)}',
      '.mcbm-drop{position:relative;border:1.5px dashed var(--dsw-alias-border-l3);border-radius:var(--dsw-radius-lg,12px);background:var(--dsw-alias-bg-layer-2);padding:20px 16px;text-align:center;cursor:pointer;transition:border-color .15s ease,background-color .15s ease}',
      '.mcbm-drop:hover{border-color:var(--dsw-alias-border-l4,var(--dsw-alias-border-l3))}',
      '.mcbm-drop:focus-visible{outline:var(--dsw-focus-ring-width,2px) solid var(--dsw-focus-ring-color,var(--dsw-alias-brand-primary));outline-offset:2px}',
      '.mcbm-drop--over{border-color:var(--dsw-alias-brand-primary);background:var(--dsw-alias-interactive-bg-hover)}',
      '.mcbm-drop__title{font-size:13px;font-weight:500}',
      '.mcbm-drop__hint{margin-top:4px;font-size:11px;color:var(--dsw-alias-label-tertiary)}',
      '.mcbm-drop__actions{margin-top:10px;display:flex;justify-content:center;gap:8px;flex-wrap:wrap}',
      '.mcbm-progress{height:6px;border-radius:999px;background:var(--dsw-alias-fill-secondary);overflow:hidden}',
      '.mcbm-progress>i{display:block;height:100%;background:var(--dsw-alias-brand-primary);transition:width .18s ease}',
      '.mcbm-uploading{display:flex;flex-direction:column;gap:6px}',
      '.mcbm-uploading__row{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:11px;color:var(--dsw-alias-label-secondary)}',
      '.mcbm-uploading__name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%}',
      '.mcbm-alert{border:1px solid var(--dsw-alias-state-error-tertiary,var(--dsw-alias-border-l2));border-radius:var(--dsw-radius-md,8px);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-error);padding:8px 10px;font-size:12px;display:flex;gap:10px;align-items:flex-start;justify-content:space-between}',
      '.mcbm-note{border:1px solid var(--dsw-alias-border-l2);border-radius:var(--dsw-radius-md,8px);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);padding:8px 10px;font-size:12px}',
      '.mcbm-list{display:flex;flex-direction:column;gap:10px}',
      '.mcbm-list__head{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12px;color:var(--dsw-alias-label-secondary)}',
      '.mcbm-grid{list-style:none;margin:0;padding:0;display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(228px,1fr))}',
      '.mcbm-card{display:flex;flex-direction:column;min-width:0;border:1px solid var(--dsw-alias-border-l2);border-radius:var(--dsw-radius-lg,12px);background:var(--dsw-alias-bg-layer-2);overflow:hidden}',
      '.mcbm-card--default{border-color:var(--dsw-alias-brand-primary)}',
      '.mcbm-thumb{position:relative;aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;background:var(--dsw-alias-bg-layer-3);overflow:hidden}',
      '.mcbm-thumb img{display:block;width:100%;height:100%;object-fit:cover}',
      '.mcbm-thumb__empty{padding:0 12px;text-align:center;font-size:11px;color:var(--dsw-alias-label-tertiary)}',
      '.mcbm-badge{position:absolute;top:8px;left:8px;padding:2px 8px;border-radius:999px;background:var(--dsw-alias-brand-primary);color:var(--dsw-alias-label-primary-foreground);font-size:11px;font-weight:600}',
      '.mcbm-body{display:flex;flex-direction:column;gap:6px;padding:10px 12px 12px;min-width:0}',
      '.mcbm-name{font-size:13px;font-weight:600;overflow-wrap:anywhere}',
      '.mcbm-meta{display:flex;flex-wrap:wrap;gap:2px 10px;font-size:11px;color:var(--dsw-alias-label-tertiary)}',
      '.mcbm-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}',
      '.mcbm-actions .mcbm-btn{padding:4px 9px;font-size:11px}',
      '.mcbm-input{box-sizing:border-box;width:100%;padding:5px 8px;border:1px solid var(--dsw-alias-border-l2);border-radius:var(--dsw-radius-sm,6px);background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font:inherit;font-size:13px}',
      '.mcbm-input:focus-visible{outline:var(--dsw-focus-ring-width,2px) solid var(--dsw-focus-ring-color,var(--dsw-alias-brand-primary));outline-offset:1px}',
      '.mcbm-hint{font-size:11px;color:var(--dsw-alias-label-tertiary)}',
      '.mcbm-empty{border:1px dashed var(--dsw-alias-border-l3);border-radius:var(--dsw-radius-lg,12px);padding:24px 16px;text-align:center}',
      '.mcbm-empty__title{font-size:13px;font-weight:600}',
      '.mcbm-empty__body{margin-top:4px;font-size:12px;color:var(--dsw-alias-label-tertiary)}',
      '.mcbm-sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}',
      '@media (max-width:520px){.mcbm-grid{grid-template-columns:1fr}}',
      '@media (prefers-reduced-motion:reduce){.mcbm-root,.mcbm-root *{transition:none!important;animation:none!important}}',
      '.mcbm-skins{display:flex;flex-direction:column;gap:10px;border-top:1px solid var(--dsw-alias-border-l2);padding-top:14px}',
      '.mcbm-skin-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:10px}',
      '.mcbm-skin-card{display:flex;flex-direction:column;gap:6px;border:1px solid var(--dsw-alias-border-l2);border-radius:var(--dsw-radius-lg,12px);background:var(--dsw-alias-bg-layer-2);padding:8px;min-width:0}',
      '.mcbm-skin-card--on{border-color:var(--dsw-alias-brand-primary)}',
      '.mcbm-skin-thumb{width:100%;aspect-ratio:1/1;object-fit:contain;image-rendering:pixelated;background:var(--dsw-alias-bg-base);border-radius:var(--dsw-radius-sm,6px)}',
      '.mcbm-skin-name{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;overflow-wrap:anywhere}',
      '.mcbm-badge{border-radius:999px;background:var(--dsw-alias-brand-primary);color:var(--dsw-alias-label-primary-foreground);font-size:10px;font-weight:600;padding:1px 7px;line-height:1.6}'
    ].join('\n')

    function ensureStyles() {
      if (typeof document === 'undefined') return
      if (document.getElementById(STYLE_ID)) return
      var style = document.createElement('style')
      style.id = STYLE_ID
      style.textContent = CSS
      ;(document.head || document.documentElement).appendChild(style)
    }

    // ── 数据 ────────────────────────────────────────────────────────────────
    function useMaps() {
      var state = React.useState({
        phase: 'loading',
        maps: [],
        defaultId: null,
        limits: null,
        error: null
      })
      var snapshot = state[0]
      var setSnapshot = state[1]

      var load = React.useCallback(function () {
        setSnapshot(function (prev) {
          return {
            phase: prev.maps.length > 0 ? 'refreshing' : 'loading',
            maps: prev.maps,
            defaultId: prev.defaultId,
            limits: prev.limits,
            error: null
          }
        })
        return requestJson('GET', API).then(
          function (data) {
            setSnapshot({
              phase: 'ready',
              maps: Array.isArray(data.maps) ? data.maps : [],
              defaultId: typeof data.defaultId === 'string' ? data.defaultId : null,
              limits: data.limits || null,
              error: null
            })
          },
          function (error) {
            setSnapshot(function (prev) {
              return {
                phase: 'failed',
                maps: prev.maps,
                defaultId: prev.defaultId,
                limits: prev.limits,
                error: error
              }
            })
          }
        )
      }, [])

      React.useEffect(function () {
        load()
      }, [load])

      return { state: snapshot, load: load }
    }

    function useSkins() {
      var state = React.useState({ phase: 'loading', skins: [], defaultId: null, error: null })
      var snapshot = state[0]
      var setSnapshot = state[1]

      var load = React.useCallback(function () {
        return requestJson('GET', SKIN_API).then(
          function (data) {
            setSnapshot({
              phase: 'ready',
              skins: Array.isArray(data.skins) ? data.skins : [],
              defaultId: typeof data.defaultId === 'string' ? data.defaultId : null,
              error: null
            })
          },
          function (error) {
            setSnapshot(function (prev) {
              return { phase: 'failed', skins: prev.skins, defaultId: prev.defaultId, error: error }
            })
          }
        )
      }, [])

      React.useEffect(function () {
        load()
      }, [load])

      return { state: snapshot, load: load }
    }

    // ── 组件 ────────────────────────────────────────────────────────────────
    function Thumb(props) {
      var broken = React.useState(false)
      var isBroken = broken[0]
      var setBroken = broken[1]
      if (!props.src || isBroken) {
        return h('div', { className: 'mcbm-thumb__empty' }, props.fallback)
      }
      return h('img', {
        src: props.src,
        alt: props.alt,
        loading: 'lazy',
        decoding: 'async',
        onError: function () {
          setBroken(true)
        }
      })
    }

    function MapCard(props) {
      var t = props.t
      var map = props.map
      var busy = props.busy
      var editing = React.useState(false)
      var isEditing = editing[0]
      var setIsEditing = editing[1]
      var draft = React.useState(map.name)
      var draftValue = draft[0]
      var setDraftValue = draft[1]
      var confirming = React.useState(false)
      var isConfirming = confirming[0]
      var setIsConfirming = confirming[1]
      var inputRef = React.useRef(null)

      React.useEffect(
        function () {
          if (isEditing && inputRef.current) {
            inputRef.current.focus()
            inputRef.current.select()
          }
        },
        [isEditing]
      )

      function startEdit() {
        setDraftValue(map.name)
        setIsEditing(true)
      }

      function commitEdit() {
        setIsEditing(false)
        props.onRename(map.id, draftValue)
      }

      function onInputKeyDown(event) {
        if (event.key === 'Enter') {
          event.preventDefault()
          commitEdit()
        } else if (event.key === 'Escape') {
          event.preventDefault()
          setIsEditing(false)
        }
      }

      var boundsText = map.bounds
        ? t('card.bounds', {
            xMin: map.bounds.xMin,
            xMax: map.bounds.xMax,
            zMin: map.bounds.zMin,
            zMax: map.bounds.zMax
          })
        : t('card.boundsUnknown')

      var meta = []
      meta.push(h('span', { key: 'size' }, formatBytes(map.sizeBytes)))
      meta.push(h('span', { key: 'bounds' }, boundsText))
      if (map.dimension) meta.push(h('span', { key: 'dimension' }, t('card.dimension', { dimension: map.dimension })))
      meta.push(h('span', { key: 'when' }, t('card.uploadedAt', { when: formatWhen(map.updatedAt || map.createdAt) })))

      var actions = []
      if (isEditing) {
        actions.push(
          h('button', { key: 'save', type: 'button', className: 'mcbm-btn mcbm-btn--primary', onClick: commitEdit, disabled: busy }, t('card.renamingHint'))
        )
      } else {
        if (!map.isDefault) {
          actions.push(
            h(
              'button',
              {
                key: 'default',
                type: 'button',
                className: 'mcbm-btn',
                disabled: busy,
                onClick: function () {
                  props.onSetDefault(map.id)
                }
              },
              t('card.setDefault')
            )
          )
        }
        actions.push(
          h('button', { key: 'rename', type: 'button', className: 'mcbm-btn', disabled: busy, onClick: startEdit }, t('card.rename'))
        )
        if (isConfirming) {
          actions.push(
            h(
              'button',
              {
                key: 'confirm',
                type: 'button',
                className: 'mcbm-btn mcbm-btn--danger',
                disabled: busy,
                onClick: function () {
                  setIsConfirming(false)
                  props.onDelete(map.id)
                }
              },
              t('card.confirmYes')
            )
          )
          actions.push(
            h(
              'button',
              {
                key: 'cancel',
                type: 'button',
                className: 'mcbm-btn',
                onClick: function () {
                  setIsConfirming(false)
                }
              },
              t('card.confirmNo')
            )
          )
        } else {
          actions.push(
            h(
              'button',
              {
                key: 'delete',
                type: 'button',
                className: 'mcbm-btn mcbm-btn--danger',
                disabled: busy,
                onClick: function () {
                  setIsConfirming(true)
                }
              },
              t('card.delete')
            )
          )
        }
      }

      return h(
        'li',
        { className: 'mcbm-card' + (map.isDefault ? ' mcbm-card--default' : '') },
        h(
          'div',
          { className: 'mcbm-thumb' },
          map.isDefault ? h('span', { className: 'mcbm-badge' }, t('card.default')) : null,
          h(Thumb, { src: map.thumbUrl, alt: map.name, fallback: t('card.noPreview') })
        ),
        h(
          'div',
          { className: 'mcbm-body' },
          busy ? h('div', { className: 'mcbm-hint', role: 'status' }, t('card.working')) : null,
          isEditing
            ? h('input', {
                ref: inputRef,
                className: 'mcbm-input',
                value: draftValue,
                maxLength: MAX_NAME,
                'aria-label': t('card.rename'),
                onChange: function (event) {
                  setDraftValue(event.target.value)
                },
                onKeyDown: onInputKeyDown,
                onBlur: function () {
                  setIsEditing(false)
                }
              })
            : h('div', { className: 'mcbm-name' }, map.name),
          h('div', { className: 'mcbm-meta' }, meta),
          isConfirming ? h('div', { className: 'mcbm-hint', role: 'alert' }, t('card.confirmDelete')) : null,
          h('div', { className: 'mcbm-actions' }, actions)
        )
      )
    }


    // ── 人物皮肤卡片 ────────────────────────────────────────────────────────
    // 和地图共用同一个设置页：上面是地图，下面就是皮肤。
    function SkinsCard() {
      var skins = useSkins()
      var snapshot = skins.state
      var noticePair = React.useState(null)
      var notice = noticePair[0]
      var setNotice = noticePair[1]
      var errPair = React.useState(null)
      var err = errPair[0]
      var setErr = errPair[1]
      var progPair = React.useState(null)
      var progress = progPair[0]
      var setProgress = progPair[1]
      var dragPair = React.useState(false)
      var isDragging = dragPair[0]
      var setIsDragging = dragPair[1]
      var inputRef = React.useRef(null)

      function beginUpload(file) {
        if (!file) return
        if (!/\.png$/i.test(file.name || '')) {
          setErr({ code: 'skin_not_png' })
          return
        }
        if (file.size > 2 * 1024 * 1024) {
          setErr({ code: 'skin_too_large' })
          return
        }
        setErr(null)
        setNotice(null)
        setProgress({ percent: 0 })
        var task = uploadSkin(file, function (ratio) {
          setProgress({ percent: Math.round(ratio * 100) })
        })
        task.promise.then(
          function (data) {
            setProgress(null)
            var name = (data && data.skin && data.skin.name) || file.name
            setNotice(ts('skins.done', { name: name }))
            return skins.load()
          },
          function (error) {
            setProgress(null)
            setErr(error)
          }
        )
      }

      function pickFile() {
        if (inputRef.current && typeof inputRef.current.click === 'function') inputRef.current.click()
      }

      function onPick(event) {
        var file = event && event.target && event.target.files && event.target.files[0]
        if (file) beginUpload(file)
        if (event && event.target) event.target.value = ''
      }

      function onDrop(event) {
        if (event && typeof event.preventDefault === 'function') event.preventDefault()
        setIsDragging(false)
        var file = event && event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]
        if (file) beginUpload(file)
      }

      function mutate(path, id) {
        setErr(null)
        requestJson('POST', SKIN_API + path, { id: id }).then(
          function () {
            return skins.load()
          },
          function (error) {
            setErr(error)
          }
        )
      }

      var items = snapshot.skins || []
      var content
      if (snapshot.phase === 'loading') {
        content = h('div', { className: 'mcbm-note', role: 'status' }, ts('list.loading') || '载入中…')
      } else if (items.length === 0) {
        content = h(
          'div',
          { className: 'mcbm-empty' },
          h('div', { className: 'mcbm-empty__title' }, ts('skins.empty')),
          h('div', { className: 'mcbm-empty__body' }, ts('skins.emptyBody'))
        )
      } else {
        content = h(
          'div',
          { className: 'mcbm-skin-grid' },
          items.map(function (skin) {
            return h(
              'div',
              { className: 'mcbm-skin-card' + (skin.isDefault ? ' mcbm-skin-card--on' : ''), key: skin.id },
              h('img', {
                className: 'mcbm-skin-thumb',
                src: skin.thumbUrl,
                alt: skin.name,
                loading: 'lazy',
                draggable: false
              }),
              h(
                'div',
                { className: 'mcbm-skin-name' },
                skin.name,
                skin.isDefault ? h('span', { className: 'mcbm-badge' }, ts('skins.default')) : null
              ),
              h(
                'div',
                { className: 'mcbm-meta' },
                h('span', null, skin.w + '×' + skin.h),
                h('span', null, formatBytes(skin.sizeBytes))
              ),
              h(
                'div',
                { className: 'mcbm-actions' },
                skin.isDefault
                  ? null
                  : h(
                      'button',
                      { type: 'button', className: 'mcbm-btn', onClick: function () { mutate('/default', skin.id) } },
                      ts('skins.setDefault')
                    ),
                h(
                  'button',
                  { type: 'button', className: 'mcbm-btn', onClick: function () { mutate('/delete', skin.id) } },
                  ts('skins.delete')
                )
              )
            )
          })
        )
      }

      return h(
        'section',
        { className: 'mcbm-skins', 'aria-labelledby': 'mcbm-skins-title' },
        h(
          'div',
          { className: 'mcbm-list__head' },
          h('span', { id: 'mcbm-skins-title' }, ts('skins.title')),
          h('span', { className: 'mcbm-hint' }, ts('skins.count', { count: items.length }))
        ),
        h('p', { className: 'mcbm-sub' }, ts('skins.subtitle')),
        h(
          'div',
          {
            className: 'mcbm-drop' + (isDragging ? ' mcbm-drop--over' : ''),
            onClick: pickFile,
            onDragEnter: function (event) {
              if (event && typeof event.preventDefault === 'function') event.preventDefault()
              setIsDragging(true)
            },
            onDragOver: function (event) {
              if (event && typeof event.preventDefault === 'function') event.preventDefault()
            },
            onDragLeave: function () {
              setIsDragging(false)
            },
            onDrop: onDrop,
            role: 'button',
            tabIndex: 0
          },
          h('div', { className: 'mcbm-drop__title' }, isDragging ? ts('skins.dragging') : ts('skins.drop')),
          h('div', { className: 'mcbm-drop__hint' }, ts('skins.hint')),
          progress
            ? h('div', { className: 'mcbm-note', role: 'status' }, ts('skins.uploading', { percent: progress.percent }))
            : null
        ),
        h('input', {
          ref: inputRef,
          type: 'file',
          accept: '.png,image/png',
          className: 'mcbm-sr',
          onChange: onPick,
          tabIndex: -1
        }),
        notice ? h('div', { className: 'mcbm-note', role: 'status' }, notice) : null,
        err
          ? h(
              'div',
              { className: 'mcbm-note', role: 'alert' },
              errorText(ts, err) || (err && err.message) || ts('errors.unknown')
            )
          : null,
        content
      )
    }

    function MapsSection(props) {
      var t = translatorOf(props)
      var maps = useMaps()
      var snapshot = maps.state

      var uploadState = React.useState({ active: false, loaded: 0, total: 0, name: '' })
      var upload = uploadState[0]
      var setUpload = uploadState[1]
      var errorState = React.useState(null)
      var error = errorState[0]
      var setError = errorState[1]
      var noticeState = React.useState('')
      var notice = noticeState[0]
      var setNotice = noticeState[1]
      var busyState = React.useState('')
      var busyId = busyState[0]
      var setBusyId = busyState[1]
      var dragging = React.useState(false)
      var isDragging = dragging[0]
      var setIsDragging = dragging[1]
      var xhrRef = React.useRef(null)
      var inputRef = React.useRef(null)
      var dragCounter = React.useRef(0)

      React.useEffect(function () {
        ensureStyles()
      }, [])

      React.useEffect(function () {
        return function () {
          if (xhrRef.current) xhrRef.current.abort()
        }
      }, [])

      function beginUpload(file) {
        if (!file) return
        setNotice('')
        var invalid = validateFile(file, snapshot.limits)
        if (invalid) {
          setError(invalid)
          return
        }
        setError(null)
        setUpload({ active: true, loaded: 0, total: file.size, name: file.name })
        var job = uploadMap(file, function (progress) {
          setUpload(function (prev) {
            return {
              active: true,
              loaded: progress.loaded,
              total: progress.total || prev.total || file.size,
              name: prev.name || file.name
            }
          })
        })
        xhrRef.current = job
        job.promise.then(
          function (data) {
            xhrRef.current = null
            setUpload({ active: false, loaded: 0, total: 0, name: '' })
            var uploaded = data && data.map
            setNotice(t('upload.done', { name: (uploaded && uploaded.name) || file.name }))
            maps.load()
          },
          function (failure) {
            xhrRef.current = null
            setUpload({ active: false, loaded: 0, total: 0, name: '' })
            if (failure && failure.code === 'cancelled') return
            setError(failure)
            if (failure && failure.code === 'duplicate') maps.load()
          }
        )
      }

      function pickFile() {
        if (inputRef.current) inputRef.current.click()
      }

      function mutate(path, body, id) {
        setBusyId(id)
        setError(null)
        return requestJson('POST', API + path, body).then(
          function (data) {
            setBusyId('')
            return data
          },
          function (failure) {
            setBusyId('')
            setError(failure)
            return null
          }
        )
      }

      function onRename(id, name) {
        var trimmed = String(name || '').trim()
        if (!trimmed) {
          setError({ code: 'name_required' })
          return
        }
        if (trimmed.length > MAX_NAME) {
          setError({ code: 'name_too_long', params: { max: MAX_NAME } })
          return
        }
        setNotice('')
        mutate('/rename', { id: id, name: trimmed }, id).then(function (data) {
          if (data) maps.load()
        })
      }

      function onDelete(id) {
        setNotice('')
        mutate('/delete', { id: id }, id).then(function (data) {
          if (data) maps.load()
        })
      }

      function onSetDefault(id) {
        setNotice('')
        mutate('/default', { id: id }, id).then(function (data) {
          if (data) maps.load()
        })
      }

      function onDrop(event) {
        event.preventDefault()
        dragCounter.current = 0
        setIsDragging(false)
        var files = event.dataTransfer && event.dataTransfer.files
        if (files && files.length > 0) beginUpload(files[0])
      }

      function onDragEnter(event) {
        event.preventDefault()
        dragCounter.current += 1
        setIsDragging(true)
      }

      function onDragOver(event) {
        event.preventDefault()
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
      }

      function onDragLeave(event) {
        event.preventDefault()
        dragCounter.current -= 1
        if (dragCounter.current <= 0) {
          dragCounter.current = 0
          setIsDragging(false)
        }
      }

      var maxBytes =
        snapshot.limits && typeof snapshot.limits.maxUploadBytes === 'number' ? snapshot.limits.maxUploadBytes : DEFAULT_MAX_UPLOAD
      var percent = upload.active && upload.total > 0 ? Math.min(100, Math.round((upload.loaded / upload.total) * 100)) : 0

      var uploadingRow = upload.active
        ? h(
            'div',
            { className: 'mcbm-uploading' },
            h(
              'div',
              { className: 'mcbm-uploading__row' },
              h('span', { className: 'mcbm-uploading__name', title: upload.name }, upload.name),
              h(
                'span',
                null,
                t('upload.progress', {
                  percent: percent,
                  loaded: formatBytes(upload.loaded),
                  total: formatBytes(upload.total)
                })
              ),
              h(
                'button',
                {
                  type: 'button',
                  className: 'mcbm-btn',
                  onClick: function () {
                    if (xhrRef.current) xhrRef.current.abort()
                  }
                },
                t('upload.cancel')
              )
            ),
            h(
              'div',
              {
                className: 'mcbm-progress',
                role: 'progressbar',
                'aria-label': t('upload.progress', { percent: percent, loaded: formatBytes(upload.loaded), total: formatBytes(upload.total) }),
                'aria-valuemin': 0,
                'aria-valuemax': 100,
                'aria-valuenow': percent
              },
              h('i', { style: { width: percent + '%' } })
            )
          )
        : null

      var content = null
      if (snapshot.phase === 'loading') {
        content = h('div', { className: 'mcbm-note', role: 'status' }, t('list.loading'))
      } else if (snapshot.maps.length === 0) {
        content = h(
          'div',
          { className: 'mcbm-empty' },
          h('div', { className: 'mcbm-empty__title' }, t('list.empty.title')),
          h('div', { className: 'mcbm-empty__body' }, t('list.empty.body'))
        )
      } else {
        content = h(
          'ul',
          { className: 'mcbm-grid' },
          snapshot.maps.map(function (map) {
            return h(MapCard, {
              key: map.id,
              map: map,
              t: t,
              busy: busyId === map.id,
              onRename: onRename,
              onDelete: onDelete,
              onSetDefault: onSetDefault
            })
          })
        )
      }

      var listError = snapshot.error
        ? h(
            'div',
            { className: 'mcbm-alert', role: 'alert' },
            h('span', null, errorText(t, snapshot.error)),
            h(
              'button',
              {
                type: 'button',
                className: 'mcbm-btn',
                onClick: function () {
                  maps.load()
                }
              },
              t('actions.retry')
            )
          )
        : null

      return h(
        'div',
        { className: 'mcbm-root' },
        h(
          'div',
          { className: 'mcbm-head' },
          h(
            'div',
            null,
            h('h3', { className: 'mcbm-title', id: 'mcbm-title' }, t('section.title')),
            h('p', { className: 'mcbm-sub', id: 'mcbm-sub' }, t('section.subtitle'))
          ),
          h(
            'button',
            {
              type: 'button',
              className: 'mcbm-btn',
              disabled: snapshot.phase === 'loading' || snapshot.phase === 'refreshing',
              onClick: function () {
                maps.load()
              }
            },
            t('actions.refresh')
          )
        ),
        h(
          'section',
          { 'aria-labelledby': 'mcbm-title' },
          h(
            'div',
            {
              className: 'mcbm-drop' + (isDragging ? ' mcbm-drop--over' : ''),
              onClick: pickFile,
              onDragEnter: onDragEnter,
              onDragOver: onDragOver,
              onDragLeave: onDragLeave,
              onDrop: onDrop,
              'aria-busy': upload.active
            },
            h('div', { className: 'mcbm-drop__title' }, isDragging ? t('upload.dragging') : t('upload.drop')),
            h('div', { className: 'mcbm-drop__hint', id: 'mcbm-upload-hint' }, t('upload.hint', { max: formatBytes(maxBytes) })),
            h(
              'div',
              { className: 'mcbm-drop__actions' },
              h(
                'button',
                {
                  type: 'button',
                  className: 'mcbm-btn mcbm-btn--primary',
                  disabled: upload.active,
                  'aria-describedby': 'mcbm-upload-hint'
                },
                t('upload.button')
              )
            ),
            h('input', {
              ref: inputRef,
              type: 'file',
              accept: '.zip,application/zip',
              className: 'mcbm-sr',
              tabIndex: -1,
              'aria-hidden': 'true',
              onChange: function (event) {
                var files = event.target.files
                if (files && files.length > 0) beginUpload(files[0])
                event.target.value = ''
              }
            })
          ),
          uploadingRow ? h('div', { style: { marginTop: '10px' } }, uploadingRow) : null
        ),
        error
          ? h(
              'div',
              { className: 'mcbm-alert', role: 'alert' },
              h('span', null, errorText(t, error)),
              h(
                'button',
                {
                  type: 'button',
                  className: 'mcbm-btn',
                  onClick: function () {
                    setError(null)
                  }
                },
                t('actions.dismiss')
              )
            )
          : null,
        notice ? h('div', { className: 'mcbm-note', role: 'status' }, notice) : null,
        listError,
        h(
          'section',
          { className: 'mcbm-list', 'aria-labelledby': 'mcbm-list-head' },
          h(
            'div',
            { className: 'mcbm-list__head' },
            h('span', { id: 'mcbm-list-head', 'aria-live': 'polite' }, t('list.count', { count: snapshot.maps.length })),
            snapshot.phase === 'refreshing' ? h('span', { role: 'status' }, t('list.loading')) : null
          ),
          h('div', { 'aria-busy': snapshot.phase === 'loading' || snapshot.phase === 'refreshing' }, content)
        ),
        h(SkinsCard, { key: 'skins' })
      )
    }

    // ── 插件导出 ────────────────────────────────────────────────────────────
    function registerSection(ctx) {
      // slots.inject 会等槽位被声明再挂；槽位由 ui-settings* 包声明，
      // 因此不必把那个包写进 dsh.client.inject。
      ctx.slots.inject(
        'settings.section',
        function* () {
          yield ctx.slots.register(
            {
              name: 'settings.section',
              id: 'blender-mc-maps',
              order: 60,
              label: function () {
                return tr('section.title')
              },
              locale: NS,
              inject: function () {
                return {}
              }
            },
            MapsSection
          )
        },
        'blender-mc: background maps section'
      )
    }

    function apply(ctx) {
      try {
        React = require('react')
      } catch (error) {
        console.error('[dsh-blender-mc] maps UI skipped: react unavailable', error)
        return
      }
      ensureStyles()

      // 语言服务可选：有就绑，没有就用自己的词表兜底（不硬依赖 locale）
      if (typeof ctx.inject === 'function') {
        ctx.inject(['locale'], function (scope) {
          var locale = scope.locale
          if (!locale || typeof locale.register !== 'function') return
          var register = function () {
            return locale.register(NS, localeTables)
          }
          if (typeof scope.effect === 'function') scope.effect(register, 'blender-mc: maps dictionaries')
          else register()
          if (typeof locale.bind === 'function') {
            var bound = locale.bind(NS)
            if (typeof bound === 'function') setTranslator(bound)
          }
        })
      }

      registerSection(ctx)
    }

    exports.name = 'dsh-blender-mc'
    // slots 是硬依赖；locale 走上面那层可选 inject
    exports.inject = ['slots']
    exports.apply = apply
    // 给仓库内测试用，不属于插件契约
    exports.__internals = {
      validateFile: validateFile,
      formatBytes: formatBytes,
      formatWhen: formatWhen,
      normalizeError: normalizeError,
      localeTables: localeTables,
      MapsSection: MapsSection
    }

    return module.exports
  }
})
