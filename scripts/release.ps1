# release.ps1 —— dsh-blender-mc 一键发版
#
# 用法（在仓库目录下）：
#     powershell -ExecutionPolicy Bypass -File scripts\release.ps1
#
# 参数：
#     -SkipPush        只打包发布，不推 git
#     -SkipPublish     只推 git，不发 npm（先干跑一次看看）
#     -DryRun          什么都不做，只检查
#
# 它做这些事：
#     1. 检查工作区是否干净、分支是否是 main
#     2. 跑本地冒烟测试 + 仓库卫生检查
#     3. 推送到 GitHub，等 CI（可选）
#     4. 检查 npm 登录状态
#     5. npm pack --dry-run 列出会发布的文件（确认没有 vendor/）
#     6. npm publish

[CmdletBinding()]
param(
    [switch]$SkipPush,
    [switch]$SkipPublish,
    [switch]$DryRun,
    [switch]$Yes,
    # npm 账号开了 2FA 时，发布要现场给一次性验证码：
    #     -File scripts\release.ps1 -Otp 123456
    # 或者换一个 Automation / 勾了 bypass 2FA 的 token，就不用每次给了。
    [string]$Otp = ''
)

$ErrorActionPreference = 'Stop'
$sep = '=' * 66

function Say($t)  { Write-Host "`n$sep`n$t`n$sep" -ForegroundColor Cyan }
function OK($t)   { Write-Host "  [OK]   $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [WARN] $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host "  [FAIL] $t" -ForegroundColor Red }
function Info($t) { Write-Host "  $t" }

# 定位仓库根（脚本在 scripts/ 下）
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Say "dsh-blender-mc 一键发版"
Info "仓库: $root"
if ($DryRun)     { Warn "DryRun 模式：只检查，不改任何东西" }
if ($SkipPush)   { Warn "跳过 git 推送" }
if ($SkipPublish){ Warn "跳过 npm 发布" }

# ────────────────────────────────────────────────
Say "1/6  检查仓库状态"

if (-not (Test-Path '.git')) { Fail "这里不是 git 仓库"; exit 1 }
if (-not (Test-Path 'package.json')) { Fail "找不到 package.json"; exit 1 }

$branch = (git branch --show-current).Trim()
if ($branch -ne 'main') { Fail "当前分支是 '$branch'，发版必须在 main 上"; exit 1 }
OK "分支: main"

$dirty = git status --short
if ($dirty) {
    Fail "工作区有未提交的改动，先提交："
    $dirty | ForEach-Object { Info $_ }
    exit 1
}
OK "工作区干净"

$pkg = Get-Content 'package.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $pkg.version
$name = $pkg.name
OK "包: $name@$ver"

# 检查市场必需字段
$need = @('name','version','description','type','main','exports','license',
          'repository','publishConfig','engines','dsh','files')
$miss = @($need | Where-Object { -not $pkg.PSObject.Properties.Name.Contains($_) })
if ($miss.Count) { Fail "package.json 缺市场字段: $($miss -join ', ')"; exit 1 }
if (-not $pkg.dsh.bundle.patch) { Fail "dsh.bundle.patch 缺失 —— 插件不会被加载"; exit 1 }
if ($pkg.dsh.manifestVersion -ne 1) { Fail "dsh.manifestVersion 应为 1"; exit 1 }
if (@($pkg.files | Where-Object { $_ -match 'vendor' }).Count) {
    Fail "package.json 的 files 里有 vendor/ —— 第三方资产不该进 npm 包"; exit 1
}
OK "市场字段齐全，files 里没有 vendor/"

# ────────────────────────────────────────────────
Say "2/6  本地冒烟测试"

if (Test-Path 'scripts\smoke.mjs') {
    node scripts\smoke.mjs
    if ($LASTEXITCODE -ne 0) { Fail "冒烟测试没过"; exit 1 }
    OK "冒烟测试通过"
} else {
    Warn "没有 scripts\smoke.mjs，跳过"
}

# 仓库卫生
$bad = git ls-files | Select-String -Pattern 'vendor/|\.blend$|\.blend1$|\.zip$|\.mp4$|\.gif$|^frame_\d+\.png$'
if ($bad) {
    Fail "仓库里有不该提交的文件："
    $bad | ForEach-Object { Info $_ }
    exit 1
}
OK "仓库卫生通过（无第三方资产 / 渲染产物）"

# ────────────────────────────────────────────────
Say "3/6  推送到 GitHub"

if ($SkipPush) {
    Warn "按参数要求跳过"
} else {
    $ahead = git rev-list --count 'origin/main..HEAD' 2>$null
    if (-not $ahead) { $ahead = git rev-list --count HEAD }
    Info "待推送提交: $ahead 个"

    if ($DryRun) {
        Warn "DryRun：不实际推送"
    } else {
        git push
        if ($LASTEXITCODE -ne 0) { Fail "推送失败（可能需要登录 GitHub）"; exit 1 }
        OK "推送完成"
    }
}

# ────────────────────────────────────────────────
Say "4/6  检查 npm 登录"

if ($SkipPublish) {
    Warn "按参数要求跳过发布"
} else {
    $who = npm whoami 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $who) {
        Fail "还没登录 npm"
        Info ""
        Info "先跑这个（会开浏览器授权）："
        Info "    npm login"
        Info ""
        Info "或者用 token 免交互："
        Info "    npm config set //registry.npmjs.org/:_authToken=你的token"
        Info ""
        Info "token 在 https://www.npmjs.com/settings/~/tokens 生成（选 Automation 类型）"
        exit 1
    }
    OK "已登录为: $who"
}

# ────────────────────────────────────────────────
Say "5/6  打包预览（会发布哪些文件）"

$packOut = npm pack --dry-run --json 2>&1 | Out-String
try {
    $packInfo = $packOut | ConvertFrom-Json
    $files = $packInfo[0].files
    $totalKB = [math]::Round($packInfo[0].size / 1024, 1)
    Info "共 $($files.Count) 个文件，压缩后 $totalKB KB"
    Info ""
    $files | ForEach-Object { Info ("  {0,-46} {1,7} KB" -f $_.path, [math]::Round($_.size/1024,1)) }
    Info ""

    # ⚠️ 坑 67：光拦 vendor/ 不够 —— 实测 __pycache__/*.pyc 也会被打进包
    #   （13 个文件共 95 KB，是 Python 3.14 编的字节码，别人装上去毫无用处）。
    #   加 .npmignore **没用**：npm 文档明确写了，files 白名单里的文件，
    #   .npmignore 和 .gitignore 都排除不掉。
    #   正确做法是 files 里写 glob（"py/**/*.py"）而不是目录名（"py"）。
    $leak = @($files | Where-Object {
        $_.path -match 'vendor|__pycache__|\.pyc$|\.pyo$|\.blend1?$|\.zip$|\.tgz$'
    })
    if ($leak.Count) {
        Fail "包里混进了不该有的东西！"
        $leak | ForEach-Object { Info "  $($_.path)" }
        Info ""
        Info "如果看到 __pycache__/.pyc：检查 package.json 的 files"
        Info "  必须写 glob（py/**/*.py），不能写目录名（py）——"
        Info "  因为 files 白名单里的路径，.npmignore 排除不掉。"
        exit 1
    }
    OK "包里只有该有的东西"
} catch {
    Warn "解析 npm pack 输出失败，手动跑 'npm pack --dry-run' 看一眼"
}

# ────────────────────────────────────────────────
Say "6/6  发布"

if ($SkipPublish) {
    Warn "按参数要求跳过"
    Info "想发布的话去掉 -SkipPublish 再跑一次"
} elseif ($DryRun) {
    Warn "DryRun：不实际发布"
} else {
    if (-not $Yes) {
        Write-Host ""
        $ans = Read-Host "  确认发布 $name@$ver 到 npm？(y/N)"
        if ($ans -ne 'y' -and $ans -ne 'Y') { Warn "已取消"; exit 0 }
    }
    $pubArgs = @('publish', '--access', 'public')
    if ($Otp) {
        $pubArgs += "--otp=$Otp"
        Info "带一次性验证码发布（--otp=$Otp）"
    }
    $out = & npm @pubArgs 2>&1 | Out-String
    $out.Trim() | ForEach-Object { Info $_ }

    if ($LASTEXITCODE -ne 0) {
        Fail "发布失败"

        # ⚠️ 坑 68：npm 账号开了 2FA 时，Classic Token 里的 "Publish" 类型
        #   不绕过 2FA，必须现场给 OTP；只有 "Automation" 类型（或勾了
        #   "bypass 2FA" 的 Granular Token）才能免验证码发布。
        #   报错原文：
        #     403 Forbidden - Two-factor authentication or granular access
        #     token with bypass 2fa enabled is required to publish packages.
        if ($out -match 'Two-factor|2fa|E403|403 Forbidden') {
            Info ""
            Info "看起来是 npm 的 2FA 拦截。两种解法："
            Info ""
            Info "  【A】现场给一次性验证码（最快，手机上打开验证器看 6 位数字）"
            Info "        powershell -ExecutionPolicy Bypass -File scripts\release.ps1 -Otp 123456"
            Info ""
            Info "  【B】换一个能绕过 2FA 的 token（一劳永逸）"
            Info "        1. 打开 https://www.npmjs.com/settings/~/tokens"
            Info "        2. Generate New Token → Classic Token → 类型选 【Automation】"
            Info "           （选 Publish 不行 —— 那个仍然要求验证码）"
            Info "        3. 复制 token，配到本地："
            Info "             npm config set //registry.npmjs.org/:_authToken=你的token"
            Info "        4. 重新跑本脚本，不用再带 -Otp"
        }
        exit 1
    }
    OK "发布成功"
    Info ""
    Info "包地址: https://www.npmjs.com/package/$name"
    Info "打 tag: git tag -a v$ver -m `"v$ver`"; git push origin v$ver"
    Info ""
    Info "接下来："
    Info "  1. 去 https://github.com/$($pkg.repository.url -replace '.*github\.com/','' -replace '\.git$','')/releases"
    Info "     建个 Release，把 CHANGELOG 里这段贴进去"
    Info "  2. DSH → 设置 → 插件市场 → 搜 $name"
    Info "     （npm 索引有延迟，等几分钟；搜不到就在市场仓库开 Issue 请求收录）"
}

Write-Host ""
Write-Host "完成。" -ForegroundColor Green
