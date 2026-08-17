[CmdletBinding()]
param(
    [switch]$AcceptRisk,
    [string]$ArchivePath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')

if (-not $AcceptRisk) {
    throw 'Read SECURITY.md first, then rerun with -AcceptRisk to download the unsigned third-party kernel.'
}

Write-Warning 'This downloads an unsigned, non-official WeCom client automation kernel. Use only in an isolated test environment.'

$runtimeDirectory = Split-Path -Parent $script:ZhixingKernelPath
if (-not (Test-Path -LiteralPath $runtimeDirectory)) {
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
}

if (Test-Path -LiteralPath $script:ZhixingKernelPath) {
    $existingHash = (Get-FileHash -LiteralPath $script:ZhixingKernelPath -Algorithm SHA256).Hash
    if ($existingHash -eq $script:ZhixingKernelSha256) {
        Write-Host 'Reviewed kernel is already present and its SHA-256 matches.'
        exit 0
    }
    throw "An unexpected kernel already exists at $script:ZhixingKernelPath. It was not overwritten."
}

$downloadedArchive = $false
if ([string]::IsNullOrWhiteSpace($ArchivePath)) {
    $archive = Join-Path $runtimeDirectory 'Enterprise-WeChat-GPTbot-V1.0.08-beta.1.download.zip'
    Write-Host 'Downloading the pinned upstream release (about 314 MB)...'
    Invoke-WebRequest -Uri $script:ZhixingKernelArchiveUrl -OutFile $archive -UseBasicParsing
    $downloadedArchive = $true
} else {
    $candidate = [Environment]::ExpandEnvironmentVariables($ArchivePath)
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        $candidate = Join-Path (Get-Location) $candidate
    }
    $archive = [System.IO.Path]::GetFullPath($candidate)
    if (-not (Test-Path -LiteralPath $archive)) {
        throw "Archive not found: $archive"
    }
}

$archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
if ($archiveHash -ne $script:ZhixingKernelArchiveSha256) {
    throw "Archive SHA-256 mismatch. Expected $script:ZhixingKernelArchiveSha256 but received $archiveHash. Nothing was executed."
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$partialPath = $script:ZhixingKernelPath + '.partial'
if (Test-Path -LiteralPath $partialPath) {
    Remove-Item -LiteralPath $partialPath -Force
}

$zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
    $entries = @($zip.Entries | Where-Object { $_.FullName -eq 'newqi24.exe' })
    if ($entries.Count -ne 1) {
        throw 'The pinned archive does not contain exactly one root-level newqi24.exe.'
    }

    $inputStream = $entries[0].Open()
    $outputStream = [System.IO.File]::Create($partialPath)
    try {
        $inputStream.CopyTo($outputStream)
    } finally {
        $outputStream.Dispose()
        $inputStream.Dispose()
    }
} finally {
    $zip.Dispose()
}

$kernelHash = (Get-FileHash -LiteralPath $partialPath -Algorithm SHA256).Hash
if ($kernelHash -ne $script:ZhixingKernelSha256) {
    Remove-Item -LiteralPath $partialPath -Force
    throw "Extracted kernel SHA-256 mismatch. Expected $script:ZhixingKernelSha256 but received $kernelHash."
}

Move-Item -LiteralPath $partialPath -Destination $script:ZhixingKernelPath
if ($downloadedArchive) {
    Remove-Item -LiteralPath $archive -Force
}

$signature = Get-AuthenticodeSignature -LiteralPath $script:ZhixingKernelPath
Write-Host "Kernel installed: $script:ZhixingKernelPath"
Write-Host "SHA-256: $kernelHash"
Write-Warning "Authenticode status: $($signature.Status). Configure the inbound firewall before starting it."
