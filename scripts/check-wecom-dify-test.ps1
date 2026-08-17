$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')

$config = Get-ZhixingConfig
$adapterPort = 8765
[void][int]::TryParse([string]$config.DIFY_ADAPTER_PORT, [ref]$adapterPort)
$kernel = $script:ZhixingKernelPath
$allKernelProcesses = @(Get-Process -Name 'newqi24' -ErrorAction SilentlyContinue)
$kernelProcesses = @(
    $allKernelProcesses | Where-Object {
        $_.Path -and $_.Path.Equals($kernel, [System.StringComparison]::OrdinalIgnoreCase)
    }
)
$packageProxyProcesses = @(Get-ZhixingProxyProcesses)
$wecomProcesses = @(Get-CimInstance Win32_Process -Filter "Name='WXWork.exe'" -ErrorAction SilentlyContinue)
$apiListeners = @(Get-NetTCPConnection -LocalPort 8002 -State Listen -ErrorAction SilentlyContinue)
$adapterListeners = @(Get-NetTCPConnection -LocalPort $adapterPort -State Listen -ErrorAction SilentlyContinue)

$wecomPaths = @(
    $wecomProcesses |
        ForEach-Object { $_.ExecutablePath } |
        Where-Object { $_ } |
        Sort-Object -Unique
)
$wecomVersions = @(
    $wecomPaths | ForEach-Object { (Get-Item -LiteralPath $_).VersionInfo.FileVersion }
)

$kernelHashMatches = $false
if (Test-Path -LiteralPath $kernel) {
    $kernelHashMatches = (Get-FileHash -LiteralPath $kernel -Algorithm SHA256).Hash -eq $script:ZhixingKernelSha256
}

$heartbeat = $null
try {
    $heartbeat = Invoke-RestMethod -Uri 'http://127.0.0.1:8002/wechat-heartbeat' -TimeoutSec 5
} catch {
    $heartbeat = [pscustomobject]@{ error = $_.Exception.Message }
}

$adapterHealth = $null
try {
    $adapterHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$adapterPort/health" -TimeoutSec 5
} catch {
    $adapterHealth = [pscustomobject]@{ error = $_.Exception.Message }
}

$firewallProtection = Get-ZhixingFirewallProtection -KernelPath $kernel

[pscustomobject]@{
    KernelPresent = (Test-Path -LiteralPath $kernel)
    KernelHashMatches = $kernelHashMatches
    KernelProcessCount = $kernelProcesses.Count
    OtherKernelProcessCount = $allKernelProcesses.Count - $kernelProcesses.Count
    WeComProcessCount = $wecomProcesses.Count
    WeComPaths = ($wecomPaths -join '; ')
    WeComVersions = ($wecomVersions -join '; ')
    WeComVersionMatches = ($wecomVersions -contains $config.WECOM_VERSION)
    KernelApiListening = ($apiListeners.Count -gt 0)
    KernelApiLocalAddress = (($apiListeners | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique) -join '; ')
    KernelInboundBlocked = ($null -ne $firewallProtection)
    AdapterListening = ($adapterListeners.Count -gt 0)
    AdapterOwnedByPackage = ($packageProxyProcesses.Count -gt 0)
    Heartbeat = ($heartbeat | ConvertTo-Json -Compress -Depth 5)
    AdapterHealth = ($adapterHealth | ConvertTo-Json -Compress -Depth 5)
} | Format-List
