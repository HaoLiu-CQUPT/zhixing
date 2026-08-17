$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')

$python = Get-ZhixingPython
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$testPort = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$previousPort = $env:DIFY_ADAPTER_PORT
$process = $null
try {
    $env:DIFY_ADAPTER_PORT = [string]$testPort
    $arguments = @('-u', ('"{0}"' -f $script:ZhixingProxyPath))
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory (Split-Path -Parent $script:ZhixingProxyPath) -WindowStyle Hidden -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 200
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$testPort/health" -TimeoutSec 2
            if ($health.status -eq 'ok' -and $health.listen -eq "127.0.0.1:$testPort") {
                $ready = $true
                break
            }
        } catch {
        }
    }
    if (-not $ready) {
        throw 'Adapter health test failed.'
    }

    Add-Type -AssemblyName System.Net.Http
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(2)
    try {
        $unknownResponse = $client.GetAsync("http://127.0.0.1:$testPort/not-allowed").GetAwaiter().GetResult()
        $unknownStatus = [int]$unknownResponse.StatusCode
        $unknownResponse.Dispose()
        if ($unknownStatus -ne 404) {
            throw "Unexpected status for an unknown path: $unknownStatus"
        }

        $body = [System.Net.Http.StringContent]::new('{}', [System.Text.Encoding]::UTF8, 'application/json')
        $unauthorizedResponse = $client.PostAsync("http://127.0.0.1:$testPort/v1/chat-messages", $body).GetAwaiter().GetResult()
        $unauthorizedStatus = [int]$unauthorizedResponse.StatusCode
        $unauthorizedResponse.Dispose()
        $body.Dispose()
        if ($unauthorizedStatus -ne 401) {
            throw "Unexpected status for a request without a Bearer token: $unauthorizedStatus"
        }
    } finally {
        $client.Dispose()
    }

    Write-Host "Adapter test passed on loopback port $testPort (health=200, unknown=404, missing token=401)."
} finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $previousPort) {
        Remove-Item Env:DIFY_ADAPTER_PORT -ErrorAction SilentlyContinue
    } else {
        $env:DIFY_ADAPTER_PORT = $previousPort
    }
}
