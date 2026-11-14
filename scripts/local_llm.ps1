function Get-OwnedLocalLlm([string]$Root) {
    $manifest = Join-Path $Root '.local/llm-runtime/process.json'
    if (-not (Test-Path -LiteralPath $manifest)) { return $null }
    $saved = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
    $child = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)" -ErrorAction SilentlyContinue
    if (-not $child) { return $null }
    $expectedPath = Join-Path $Root '.local/llm-runtime/bin/llama-server.exe'
    if ($child.ExecutablePath -ne $expectedPath -or -not $child.CommandLine.Contains('--alias kiber102-local')) { return $null }
    if ($child.CreationDate.ToUniversalTime().Ticks -ne ([DateTimeOffset]$saved.started).UtcTicks) { return $null }
    return $child
}

function Stop-LocalLlm([string]$Root) {
    $child = Get-OwnedLocalLlm $Root
    if ($child) { Stop-Process -Id $child.ProcessId -ErrorAction Stop }
    $manifest = Join-Path $Root '.local/llm-runtime/process.json'
    if (Test-Path -LiteralPath $manifest) { Remove-Item -LiteralPath $manifest }
}

function Start-LocalLlm([string]$Root, [string]$PythonPath, [bool]$Install) {
    if ($env:LLM_PROVIDER -ne 'local') { return }
    if (([string]$env:LLM_BASE_URL).TrimEnd('/') -ne 'http://127.0.0.1:18080/v1' -or $env:LLM_MODEL -ne 'kiber102-local') {
        Write-Warning 'Для автоматического запуска локальной модели используйте http://127.0.0.1:18080/v1 и LLM_MODEL=kiber102-local. Иной локальный сервер запускается отдельно.'
        return
    }
    $device = if ($env:LLM_LOCAL_DEVICE) { $env:LLM_LOCAL_DEVICE } else { 'none' }
    if ($device -notmatch '^(none|Vulkan[0-9]+)$') { throw 'Некорректный LLM_LOCAL_DEVICE.' }
    $profile = "qwen3-4b:b12360:8192:1:reasoning-off:cors-localhost:$device"
    $owned = Get-OwnedLocalLlm $Root
    if ($owned) {
        $savedProfile = (Get-Content -Raw -LiteralPath (Join-Path $Root '.local/llm-runtime/process.json') | ConvertFrom-Json).profile
        if ($Install -or $savedProfile -ne $profile) { Stop-LocalLlm $Root }
    }
    if ($Install) {
        if (Get-NetTCPConnection -LocalPort 18080 -State Listen -ErrorAction SilentlyContinue) { throw 'Порт 18080 занят другим процессом.' }
        & $PythonPath -X utf8 (Join-Path $Root 'scripts/install_local_llm.py')
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить проверенную локальную модель' }
    }
    $runtime = Join-Path $Root '.local/llm-runtime'
    $executable = Join-Path $runtime 'bin/llama-server.exe'
    $model = Join-Path $runtime 'Qwen3-4B-Q4_K_M.gguf'
    if (-not (Test-Path -LiteralPath $executable) -or -not (Test-Path -LiteralPath $model)) {
        Write-Warning 'Локальная модель не установлена. Поиск работает в резервном режиме. Для установки выполните ./run.ps1 -Install.'
        return
    }
    $newProcess = $false
    if (-not (Get-OwnedLocalLlm $Root)) {
        if (Get-NetTCPConnection -LocalPort 18080 -State Listen -ErrorAction SilentlyContinue) { throw 'Порт 18080 занят другим процессом.' }
        if ((Get-FileHash -LiteralPath $model -Algorithm SHA256).Hash.ToLowerInvariant() -ne '7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5') { throw 'Контрольная сумма локальной модели не совпадает.' }
        $layers = if ($device -eq 'none') { '0' } else { '99' }
        $child = Start-Process -FilePath $executable -ArgumentList @('-m',"`"$model`"",'--host','127.0.0.1','--port','18080','--alias','kiber102-local','--ctx-size','8192','--parallel','1','--device',$device,'--gpu-layers',$layers,'--reasoning','off','--reasoning-budget','0','--no-ui','--cors-origins','localhost','--no-slots','--no-agent') -WindowStyle Hidden -WorkingDirectory $Root -PassThru -RedirectStandardOutput (Join-Path $runtime 'server.out.log') -RedirectStandardError (Join-Path $runtime 'server.err.log')
        $info = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.Id)"
        @{pid=$child.Id;started=$info.CreationDate.ToUniversalTime().ToString('o');profile=$profile} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'process.json') -Encoding utf8
        $newProcess = $true
    }
    for ($attempt=0; $attempt -lt 120; $attempt++) {
        if (-not (Get-OwnedLocalLlm $Root)) { throw 'Локальная модель завершилась. См. .local/llm-runtime/server.err.log' }
        try {
            if ((Invoke-WebRequest 'http://127.0.0.1:18080/health' -TimeoutSec 2).StatusCode -eq 200) {
                if ($newProcess) {
                    & $PythonPath -X utf8 (Join-Path $Root 'scripts/warm_local_llm.py')
                    if ($LASTEXITCODE -ne 0) { Write-Warning 'Прогрев модели не завершён. До её готовности доступен резервный поиск.' }
                }
                Write-Host 'Локальная модель готова на 127.0.0.1:18080.'
                return
            }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    Write-Warning 'Модель ещё загружается. Сайт продолжит работать в резервном поиске.'
}
