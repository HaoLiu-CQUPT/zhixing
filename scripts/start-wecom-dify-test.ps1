[CmdletBinding()]
param([switch]$SkipFirewallCheck)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')

$config = Get-ZhixingConfig -RequireEnv
$kernel = $script:ZhixingKernelPath
$proxy = $script:ZhixingProxyPath
$wecom = Resolve-ZhixingConfiguredPath -Value $config.WECOM_EXE_PATH
$python = Get-ZhixingPython

if (-not (Test-Path -LiteralPath $kernel)) {
    throw "Kernel not found: $kernel. Run scripts\setup-runtime.ps1 -AcceptRisk first."
}
if ((Get-FileHash -LiteralPath $kernel -Algorithm SHA256).Hash -ne $script:ZhixingKernelSha256) {
    throw 'Kernel hash mismatch. Startup refused.'
}
if (-not (Test-Path -LiteralPath $wecom)) {
    throw "WeCom not found: $wecom"
}

$actualWeComVersion = (Get-Item -LiteralPath $wecom).VersionInfo.FileVersion
if ($actualWeComVersion -ne $config.WECOM_VERSION) {
    throw "WeCom version mismatch. Actual: $actualWeComVersion; expected: $($config.WECOM_VERSION)."
}

$difyKey = [string]$config.DIFY_API_KEY
if ([string]::IsNullOrWhiteSpace($difyKey) -or $difyKey -eq 'app-replace_me') {
    throw 'DIFY_API_KEY is missing. Copy .env.example to .env and use the Dify application API Key.'
}
if ($difyKey -match '^sk-') {
    throw 'DIFY_API_KEY looks like a model-provider key. Use the Dify application key (normally app-...), and configure model keys inside Dify.'
}
if ($config.DIFY_API_MODE -ne 'chatbot') {
    throw "This tested package requires DIFY_API_MODE=chatbot. Actual: $($config.DIFY_API_MODE)"
}

$adapterPort = 0
if (-not [int]::TryParse([string]$config.DIFY_ADAPTER_PORT, [ref]$adapterPort) -or $adapterPort -lt 1024 -or $adapterPort -gt 65535) {
    throw 'DIFY_ADAPTER_PORT must be an integer from 1024 through 65535.'
}

if (-not $SkipFirewallCheck -and -not (Get-ZhixingFirewallProtection -KernelPath $kernel)) {
    throw 'No verified inbound block rule protects newqi24.exe. Run 配置内核防火墙.cmd first.'
}

Stop-ZhixingProxy
Start-Sleep -Milliseconds 300
$existingAdapterListener = @(Get-NetTCPConnection -LocalPort $adapterPort -State Listen -ErrorAction SilentlyContinue)
if ($existingAdapterListener.Count -gt 0) {
    throw "Port $adapterPort is already in use. Stop the old test instance before starting this package."
}

Get-Process -Name 'newqi24', 'WXWork' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

if (-not (Test-Path -LiteralPath $script:ZhixingRuntimeDirectory)) {
    New-Item -ItemType Directory -Path $script:ZhixingRuntimeDirectory -Force | Out-Null
}

$registryPath = 'HKCU:\Software\Tencent\WXWork'
$registryExists = Test-Path -Path $registryPath
$registryBackup = [ordered]@{
    RegistryPathExisted = $registryExists
    ExecutableExisted = $false
    Executable = $null
    VersionExisted = $false
    Version = $null
}
if ($registryExists) {
    $registryValues = Get-ItemProperty -Path $registryPath
    $executableProperty = $registryValues.PSObject.Properties['Executable']
    $versionProperty = $registryValues.PSObject.Properties['Version']
    if ($executableProperty) {
        $registryBackup.ExecutableExisted = $true
        $registryBackup.Executable = [string]$executableProperty.Value
    }
    if ($versionProperty) {
        $registryBackup.VersionExisted = $true
        $registryBackup.Version = [string]$versionProperty.Value
    }
} else {
    New-Item -Path $registryPath -Force | Out-Null
}
$registryBackup | ConvertTo-Json | Set-Content -LiteralPath $script:ZhixingRegistryBackup -Encoding UTF8
Set-ItemProperty -Path $registryPath -Name Executable -Value $wecom
Set-ItemProperty -Path $registryPath -Name Version -Value $config.WECOM_VERSION

$env:DIFY_API_KEY = $difyKey
$env:DIFY_API_MODE = $config.DIFY_API_MODE
$env:AI_ENGINE = 'difyapi'
$env:AI_MODE = $config.DIFY_API_MODE
$env:DIFY_API_BASE = "http://127.0.0.1:$adapterPort/v1"
$env:DIFY_ADAPTER_PORT = [string]$adapterPort
$env:USE_LOCAL_SERVER = 'false'
$env:USE_LOCAL_API = 'false'
$env:LOCAL_SERVER_PORT = '3000'
$env:ENV_FILE = Join-Path $script:ZhixingRepoRoot '.env'
$env:BOT_TRIGGER_WORD = $config.BOT_TRIGGER_WORD
$env:GROUP_CHAT_WHITELIST = $config.GROUP_CHAT_WHITELIST
$env:PRIVATE_CHAT_WHITELIST = $config.PRIVATE_CHAT_WHITELIST
$env:DEFAULT_GROUP_LIMIT = $config.DEFAULT_GROUP_LIMIT
$env:DEFAULT_PRIVATE_LIMIT = $config.DEFAULT_PRIVATE_LIMIT
$env:WHITELIST_GROUP_LIMIT = $config.WHITELIST_GROUP_LIMIT
$env:WHITELIST_PRIVATE_LIMIT = $config.WHITELIST_PRIVATE_LIMIT
$env:ADVERTISEMENT = $config.ADVERTISEMENT
$env:PRIVATE_ADVERTISEMENT = $config.PRIVATE_ADVERTISEMENT

$proxyArguments = @('-u', ('"{0}"' -f $proxy))
Start-Process -FilePath $python -ArgumentList $proxyArguments -WorkingDirectory (Split-Path -Parent $proxy) -WindowStyle Hidden | Out-Null

$proxyReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $proxyHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$adapterPort/health" -TimeoutSec 2
        if ($proxyHealth.service -eq 'wecom-dify-adapter' -and $proxyHealth.status -eq 'ok') {
            $proxyReady = $true
            break
        }
    } catch {
    }
}
if (-not $proxyReady) {
    Stop-ZhixingProxy
    throw 'The loopback Dify adapter did not become healthy.'
}

Start-Process -FilePath $kernel -WorkingDirectory (Split-Path -Parent $kernel) -WindowStyle Normal | Out-Null
Write-Host 'Loopback Dify adapter and reviewed WeCom test kernel started.'
Write-Host "Adapter health: http://127.0.0.1:$adapterPort/health"
Write-Warning 'Keep this as a test environment. The kernel is unsigned and uses non-official client automation.'
