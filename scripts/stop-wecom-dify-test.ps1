$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')

Get-Process -Name 'newqi24', 'WXWork' -ErrorAction SilentlyContinue | Stop-Process -Force
Stop-ZhixingProxy

$registryPath = 'HKCU:\Software\Tencent\WXWork'
if (Test-Path -LiteralPath $script:ZhixingRegistryBackup) {
    $backup = Get-Content -LiteralPath $script:ZhixingRegistryBackup -Raw | ConvertFrom-Json
    if (-not (Test-Path -Path $registryPath)) {
        New-Item -Path $registryPath -Force | Out-Null
    }

    if ($backup.ExecutableExisted) {
        Set-ItemProperty -Path $registryPath -Name Executable -Value ([string]$backup.Executable)
    } else {
        Remove-ItemProperty -Path $registryPath -Name Executable -ErrorAction SilentlyContinue
    }
    if ($backup.VersionExisted) {
        Set-ItemProperty -Path $registryPath -Name Version -Value ([string]$backup.Version)
    } else {
        Remove-ItemProperty -Path $registryPath -Name Version -ErrorAction SilentlyContinue
    }
}

Write-Host 'Test processes stopped. The saved WeCom registry values were restored when available.'
