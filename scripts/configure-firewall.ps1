$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdministrator) {
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $PSCommandPath)
    )
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

if (-not (Test-Path -LiteralPath $script:ZhixingKernelPath)) {
    throw "Kernel not found: $script:ZhixingKernelPath. Run setup-runtime.ps1 first."
}

$kernelHash = (Get-FileHash -LiteralPath $script:ZhixingKernelPath -Algorithm SHA256).Hash
if ($kernelHash -ne $script:ZhixingKernelSha256) {
    throw 'Kernel hash mismatch. Firewall configuration stopped.'
}

$existingRules = @(Get-NetFirewallRule -DisplayName $script:ZhixingFirewallRuleName -ErrorAction SilentlyContinue)
if ($existingRules.Count -eq 0) {
    New-NetFirewallRule `
        -DisplayName $script:ZhixingFirewallRuleName `
        -Direction Inbound `
        -Program $script:ZhixingKernelPath `
        -Action Block `
        -Profile Any `
        -Enabled True | Out-Null
} else {
    foreach ($rule in $existingRules) {
        $rule | Set-NetFirewallRule -Direction Inbound -Action Block -Profile Any -Enabled True | Out-Null
        $rule | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program $script:ZhixingKernelPath | Out-Null
    }
}

$protection = Get-ZhixingFirewallProtection -KernelPath $script:ZhixingKernelPath
if (-not $protection) {
    throw 'The firewall rule could not be verified.'
}
Write-Host "Inbound block rule is active for: $script:ZhixingKernelPath"
