$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')

$failures = [System.Collections.Generic.List[string]]::new()

$powershellFiles = Get-ChildItem -LiteralPath $script:ZhixingRepoRoot -Recurse -File -Filter '*.ps1' |
    Where-Object { $_.FullName -notlike '*\.git\*' }
foreach ($file in $powershellFiles) {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
    foreach ($parseError in @($parseErrors)) {
        $failures.Add("PowerShell parse error in $($file.FullName): $($parseError.Message)")
    }
}

foreach ($jsonFile in Get-ChildItem -LiteralPath $script:ZhixingRepoRoot -Recurse -File -Filter '*.json' | Where-Object { $_.FullName -notlike '*\.git\*' }) {
    try {
        Get-Content -LiteralPath $jsonFile.FullName -Raw | ConvertFrom-Json | Out-Null
    } catch {
        $failures.Add("Invalid JSON: $($jsonFile.FullName)")
    }
}

$python = Get-ZhixingPython
& $python -m py_compile $script:ZhixingProxyPath
if ($LASTEXITCODE -ne 0) {
    $failures.Add('Python compilation failed for adapter/dify_proxy.py.')
}

$trackedFiles = @(& git -C $script:ZhixingRepoRoot -c 'core.quotepath=false' ls-files)
if ($LASTEXITCODE -ne 0) {
    $failures.Add('git ls-files failed.')
}

$forbiddenPathPattern = '(?i)(^|/)(\.env|[^/]+\.(exe|dll|zip|db|sqlite|sqlite3|log|csv|jsonl|pyc))$'
$textExtensions = @('.md', '.txt', '.py', '.ps1', '.cmd', '.json', '.example', '.gitignore')
$secretPatterns = @(
    'app-[A-Za-z0-9_-]{20,}',
    'sk-[A-Za-z0-9_-]{20,}',
    'gh[opsu]_[A-Za-z0-9]{20,}',
    'Bearer\s+[A-Za-z0-9._-]{20,}'
)

foreach ($relativePath in $trackedFiles) {
    $normalized = $relativePath -replace '\\', '/'
    if ($normalized -match $forbiddenPathPattern) {
        $failures.Add("Forbidden tracked runtime or binary file: $relativePath")
    }

    $fullPath = Join-Path $script:ZhixingRepoRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath)) {
        continue
    }
    $fileInfo = Get-Item -LiteralPath $fullPath
    if ($fileInfo.Length -gt 10MB) {
        $failures.Add("Tracked file exceeds 10 MB: $relativePath")
    }

    if ($textExtensions -contains $fileInfo.Extension -or $fileInfo.Name -eq '.gitignore') {
        $content = Get-Content -LiteralPath $fullPath -Raw -ErrorAction SilentlyContinue
        foreach ($pattern in $secretPatterns) {
            if ($content -match $pattern) {
                $failures.Add("Possible credential in tracked file $relativePath (pattern: $pattern)")
            }
        }
    }
}

& git -C $script:ZhixingRepoRoot diff --check
if ($LASTEXITCODE -ne 0) {
    $failures.Add('git diff --check failed for unstaged changes.')
}
& git -C $script:ZhixingRepoRoot diff --cached --check
if ($LASTEXITCODE -ne 0) {
    $failures.Add('git diff --cached --check failed for staged changes.')
}

if ($failures.Count -gt 0) {
    throw ($failures -join [Environment]::NewLine)
}

& (Join-Path $PSScriptRoot 'test-adapter.ps1')
Write-Host "Release verification passed for $($trackedFiles.Count) tracked files."
