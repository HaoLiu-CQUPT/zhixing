Set-StrictMode -Version Latest

$script:ZhixingRepoRoot = Split-Path -Parent $PSScriptRoot
$script:ZhixingKernelPath = Join-Path $script:ZhixingRepoRoot 'new-kernel-runtime\newqi24.exe'
$script:ZhixingProxyPath = Join-Path $script:ZhixingRepoRoot 'adapter\dify_proxy.py'
$script:ZhixingRuntimeDirectory = Join-Path $script:ZhixingRepoRoot 'adapter\runtime'
$script:ZhixingRegistryBackup = Join-Path $script:ZhixingRuntimeDirectory 'wecom-registry-backup.json'
$script:ZhixingKernelSha256 = 'D96B74EDA95BB24B0640D42D30B2CFD3756E3D26A8B70780AB93DB62F13E38E1'
$script:ZhixingKernelArchiveSha256 = 'DAF79014AEBE681E5AE6B8CB5D372D7B99FCDD2A268F7848C0D1D381BF43102B'
$script:ZhixingKernelArchiveUrl = 'https://github.com/luolin-ai/Enterprise-WeChat-GPTbot/releases/download/V1.0.08-beta.1/Enterprise-WeChat-GPTbot.zip'
$script:ZhixingFirewallRuleName = 'Zhixing WeCom Test - Block newqi24 inbound'

function Read-ZhixingDotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    $values = @{}
    $encoding = [System.Text.UTF8Encoding]::new($false, $true)
    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path, $encoding)) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
            continue
        }

        $separator = $line.IndexOf('=')
        if ($separator -lt 1) {
            continue
        }

        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $values[$name] = $value
    }
    return $values
}

function Get-ZhixingConfig {
    param([switch]$RequireEnv)

    $config = @{
        DIFY_API_KEY = ''
        DIFY_API_MODE = 'chatbot'
        DIFY_ADAPTER_PORT = '8765'
        WECOM_EXE_PATH = 'D:\WXWork\WXWork.exe'
        WECOM_VERSION = '4.1.33.6009'
        BOT_TRIGGER_WORD = '@AI答疑助手'
        DEFAULT_GROUP_LIMIT = '20'
        DEFAULT_PRIVATE_LIMIT = '0'
        WHITELIST_GROUP_LIMIT = '20'
        WHITELIST_PRIVATE_LIMIT = '0'
        GROUP_CHAT_WHITELIST = ''
        PRIVATE_CHAT_WHITELIST = ''
        ADVERTISEMENT = ''
        PRIVATE_ADVERTISEMENT = ''
    }

    $envPath = Join-Path $script:ZhixingRepoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        if ($RequireEnv) {
            throw "Configuration not found: $envPath. Copy .env.example to .env first."
        }
        return $config
    }

    $localValues = Read-ZhixingDotEnv -Path $envPath
    foreach ($name in $localValues.Keys) {
        $config[$name] = $localValues[$name]
    }
    return $config
}

function Resolve-ZhixingConfiguredPath {
    param([Parameter(Mandatory = $true)][string]$Value)

    $expanded = [Environment]::ExpandEnvironmentVariables($Value)
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $script:ZhixingRepoRoot $expanded))
}

function Get-ZhixingPython {
    $command = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command 'python' -ErrorAction SilentlyContinue
    }
    if (-not $command) {
        throw 'Python was not found in PATH. Install Python 3.10 or newer.'
    }
    return $command.Source
}

function Get-ZhixingProxyProcesses {
    $proxyPath = [System.IO.Path]::GetFullPath($script:ZhixingProxyPath)
    return @(
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and $_.CommandLine.IndexOf($proxyPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            }
    )
}

function Stop-ZhixingProxy {
    foreach ($process in @(Get-ZhixingProxyProcesses)) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Get-ZhixingFirewallProtection {
    param([Parameter(Mandatory = $true)][string]$KernelPath)

    $expectedPath = [System.IO.Path]::GetFullPath($KernelPath)
    $knownNames = @(
        $script:ZhixingFirewallRuleName,
        'WeCom Dify Test - Block newqi24 inbound'
    )
    foreach ($name in $knownNames) {
        foreach ($rule in @(Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
            $filters = @($rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue)
            foreach ($filter in $filters) {
                if ($filter.Program -and
                    [System.IO.Path]::GetFullPath($filter.Program).Equals($expectedPath, [System.StringComparison]::OrdinalIgnoreCase) -and
                    $rule.Enabled -eq 'True' -and
                    $rule.Direction -eq 'Inbound' -and
                    $rule.Action -eq 'Block') {
                    return $rule
                }
            }
        }
    }
    return $null
}
