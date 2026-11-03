<#
.SYNOPSIS
  在 HP（Windows + Docker Desktop）上一键部署内容生产工作台 WebUI。

.DESCRIPTION
  做的事：拉代码 → 生成页面口令与 .env → 构建并启动容器 → 等健康检查 → 打印访问地址。
  重复执行是安全的（幂等）：已有 .env 不会被覆盖，代码会更新到最新。

.PARAMETER Repo
  仓库地址，默认本项目的分支。

.PARAMETER Dir
  部署目录，默认 $HOME\workbench。

.PARAMETER VaultUrl
  Obsidian Local REST API 地址。Obsidian 与容器在同一台机器时用默认值即可。

.PARAMETER VaultToken
  Obsidian Local REST API 的 token（在插件设置里生成）。

.PARAMETER ReviewVaultPath
  Windows 上"复盘库"目录的路径（可留空，留空则暂不开写回）。

.EXAMPLE
  .\deploy-windows.ps1 -VaultToken "abcdef123456"
#>
[CmdletBinding()]
param(
  [string]$Repo = "https://github.com/FJ-double/dsh-superagent.git",
  [string]$Branch = "agent/extract-article-design-903b08e5cb03",
  [string]$Dir = "$HOME\workbench",
  [string]$VaultUrl = "https://host.docker.internal:27124",
  [string]$VaultToken = "",
  [string]$VaultVerifyTls = "false",
  [string]$ReviewVaultPath = "",
  [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !  $m" -ForegroundColor Yellow }

Info "检查 Docker"
docker version --format '{{.Server.Version}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker 没跑起来。先启动 Docker Desktop，等托盘图标变绿再试。" }
Ok "Docker 可用"

Info "取代码到 $Dir"
if (Test-Path (Join-Path $Dir ".git")) {
  git -C $Dir fetch --all --quiet
  git -C $Dir checkout $Branch --quiet
  git -C $Dir reset --hard "origin/$Branch" --quiet
} else {
  git clone --branch $Branch $Repo $Dir
}
Ok "代码就位：$(git -C $Dir rev-parse --short HEAD)"

$deployDir = Join-Path $Dir "deploy"
$envFile = Join-Path $deployDir ".env"

Info "生成 .env（已有则保留）"
if (-not (Test-Path $envFile)) {
  $bytes = New-Object byte[] 24
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $webToken = [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
  $lines = @(
    "WB_WEB_TOKEN=$webToken",
    "WB_VAULT_BASE_URL=$VaultUrl",
    "WB_VAULT_TOKEN=$VaultToken",
    "WB_VAULT_VERIFY_TLS=$VaultVerifyTls",
    "WB_VAULT_REVIEW_ROOT=/vault-review"
  )
  Set-Content -Path $envFile -Value $lines -Encoding utf8
  Ok "已生成 $envFile"
} else {
  Warn "已存在 $envFile，保持原样（要改口令就编辑它，然后重新运行本脚本）"
}

Info "准备 config.toml"
$cfg = Join-Path $deployDir "config.toml"
if (-not (Test-Path $cfg)) {
  Copy-Item (Join-Path $Dir "config.example.toml") $cfg
  Ok "已从模板生成 config.toml（仓库地址与口令走 .env，这里可留空）"
}
if ($ReviewVaultPath -ne "") {
  $composePath = Join-Path $deployDir "docker-compose.yml"
  $compose = Get-Content $composePath -Raw
  if ($compose -notmatch "vault-review") {
    Warn "需要在 docker-compose.yml 的 volumes 里加一行：- `"$($ReviewVaultPath):/vault-review`""
  }
}

Info "构建并启动容器"
Push-Location $deployDir
try {
  docker compose up -d --build
  if ($LASTEXITCODE -ne 0) { throw "docker compose 启动失败，看上面的报错" }
} finally { Pop-Location }
Ok "容器已启动"

Info "等健康检查"
$deadline = (Get-Date).AddSeconds(90)
$healthy = $false
while ((Get-Date) -lt $deadline) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $healthy = $true; break }
  } catch { Start-Sleep -Seconds 3 }
}
if (-not $healthy) {
  Warn "90 秒内没等到 200。看日志：docker compose -f $deployDir\docker-compose.yml logs --tail=50"
} else { Ok "服务已响应" }

Info "验证接口确实要口令"
try {
  Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/status" -UseBasicParsing -TimeoutSec 3 | Out-Null
  Warn "接口没要口令 —— 检查 .env 里的 WB_WEB_TOKEN 是否为空"
} catch {
  if ($_.Exception.Response.StatusCode.value__ -eq 401) { Ok "未带口令返回 401，符合预期" }
}

# 找出局域网地址，方便手机直接打开
$lanIps = Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
  Select-Object -ExpandProperty IPAddress
$webToken = (Get-Content $envFile | Where-Object { $_ -like "WB_WEB_TOKEN=*" }) -replace "WB_WEB_TOKEN=", ""

Write-Host ""
Write-Host "部署完成。" -ForegroundColor Green
Write-Host "  页面口令：$webToken"
Write-Host "  本机访问：http://127.0.0.1:$Port/"
foreach ($ip in $lanIps) { Write-Host "  局域网/手机：http://${ip}:$Port/" }
Write-Host ""
Write-Host "常用命令："
Write-Host "  docker compose -f $deployDir\docker-compose.yml logs -f     # 看日志"
Write-Host "  docker compose -f $deployDir\docker-compose.yml restart     # 重启"
Write-Host "  docker compose -f $deployDir\docker-compose.yml down        # 停掉"
