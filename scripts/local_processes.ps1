# Helpers contain no startup side effects and can be tested without stopping services.
function Get-ProjectFingerprint([string]$Root) {
    $files = [Collections.Generic.List[string]]::new()
    foreach ($relative in @('apps/api/app','apps/api/migrations','apps/worker','apps/bot','apps/web/src','apps/web/public','scripts')) {
        $directory = Join-Path $Root $relative
        if (Test-Path -LiteralPath $directory) {
            foreach ($file in (Get-ChildItem -LiteralPath $directory -File -Recurse)) {
                if ($file.FullName -notmatch '[\\/]__pycache__[\\/]' -and $file.Extension -ne '.pyc') { $files.Add($file.FullName) }
            }
        }
    }
    foreach ($relative in @('.env','run.ps1','requirements.txt','requirements.lock.txt','pyproject.toml','alembic.ini','apps/web/package.json','apps/web/package-lock.json','apps/web/next.config.ts','apps/web/tsconfig.json')) {
        $file = Join-Path $Root $relative
        if (Test-Path -LiteralPath $file) { $files.Add($file) }
    }
    $hasher = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA256)
    try {
        foreach ($file in ($files | Sort-Object -Unique)) {
            $relative = $file.Substring($Root.Length).TrimStart('\', '/').Replace('\', '/')
            $hasher.AppendData([Text.Encoding]::UTF8.GetBytes($relative + "`n"))
            $hasher.AppendData([Text.Encoding]::UTF8.GetBytes((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash))
        }
        return ([BitConverter]::ToString($hasher.GetHashAndReset())).Replace('-', '').ToLowerInvariant()
    } finally { $hasher.Dispose() }
}

function Test-ServiceCommand([string]$Service, $ProcessInfo, [string]$Root, $ExpectedStarted) {
    if (-not $ProcessInfo -or -not $ProcessInfo.CommandLine) { return $false }
    $command = $ProcessInfo.CommandLine.Replace('\','/').ToLowerInvariant()
    $rootName = $Root.Replace('\','/').TrimEnd('/').ToLowerInvariant()
    if (-not $command.Contains($rootName + '/')) { return $false }
    $marker = switch ($Service) {
        'api' { 'apps.api.app.main:app' }
        'web' { 'apps/web/' }
        'worker' { 'apps.worker.main' }
        'bot' { 'apps.bot.main' }
        default { return $false }
    }
    if (-not $command.Contains($marker)) { return $false }
    if ($ExpectedStarted) {
        if (-not $ProcessInfo.CreationDate) { return $false }
        try {
            $actualInstant = [DateTimeOffset]$ProcessInfo.CreationDate
            $expectedInstant = [DateTimeOffset]$ExpectedStarted
            if ($actualInstant.UtcDateTime.Ticks -ne $expectedInstant.UtcDateTime.Ticks) { return $false }
        } catch { return $false }
    }
    return $true
}

function Get-OwnedService([string]$Service, $Manifest, [string]$Root) {
    $savedNumber = $Manifest.$Service
    if (-not $savedNumber -or [string]$savedNumber -notmatch '^\d{1,10}$') { return $null }
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$savedNumber" -ErrorAction SilentlyContinue
    $expected = if ($Manifest.started -and $Manifest.started.PSObject.Properties[$Service]) { $Manifest.started.$Service } else { $null }
    if (Test-ServiceCommand $Service $processInfo $Root $expected) { return $processInfo }
    return $null
}

function Save-ProcessManifest($Manifest, [string]$Path) {
    $temporary = $Path + '.tmp'
    $Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Stop-OwnedService([string]$Service, $Manifest, [string]$Root, [string]$ManifestPath) {
    $owned = Get-OwnedService $Service $Manifest $Root
    if ($owned) {
        $queue = [Collections.Generic.List[object]]::new()
        $queue.Add($owned)
        for ($index = 0; $index -lt $queue.Count; $index++) {
            foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($queue[$index].ProcessId)" -ErrorAction SilentlyContinue)) {
                if ($child -and -not ($queue | Where-Object ProcessId -eq $child.ProcessId)) { $queue.Add($child) }
            }
        }
        for ($index = $queue.Count - 1; $index -ge 0; $index--) {
            $candidate = $queue[$index]
            $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($candidate.ProcessId)" -ErrorAction SilentlyContinue
            # A PID may be recycled between enumeration and termination.
            if ($current -and $current.CreationDate -eq $candidate.CreationDate) {
                Stop-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
            }
        }
    }
    $Manifest.$Service = $null
    $Manifest.started.$Service = $null
    Save-ProcessManifest $Manifest $ManifestPath
}

function Test-PortOwned([int]$Port, [string]$Service, $Manifest, [string]$Root) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if (-not $listeners) { return $true }
    $owned = Get-OwnedService $Service $Manifest $Root
    if (-not $owned) { return $false }
    foreach ($listener in $listeners) {
        $number = $listener.OwningProcess
        $matched = $false
        for ($depth = 0; $depth -lt 8 -and $number; $depth++) {
            if ($number -eq $owned.ProcessId) { $matched = $true; break }
            $ancestor = Get-CimInstance Win32_Process -Filter "ProcessId=$number" -ErrorAction SilentlyContinue
            if (-not $ancestor) { break }
            $number = $ancestor.ParentProcessId
        }
        if (-not $matched) { return $false }
    }
    return $true
}

function Start-LocalServices([string]$Root, [string]$PythonPath, [string]$LocalDir, [string]$ManifestPath, [bool]$Development) {
    $saved = if (Test-Path -LiteralPath $ManifestPath) { Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json } else { [pscustomobject]@{} }
    foreach ($name in @('api','web','worker','bot','fingerprint','mode')) {
        if (-not $saved.PSObject.Properties[$name]) { $saved | Add-Member -NotePropertyName $name -NotePropertyValue $null }
    }
    if (-not $saved.PSObject.Properties['started']) { $saved | Add-Member -NotePropertyName started -NotePropertyValue ([pscustomobject]@{}) }
    foreach ($name in @('api','web','worker','bot')) {
        if (-not $saved.started.PSObject.Properties[$name]) { $saved.started | Add-Member -NotePropertyName $name -NotePropertyValue $null }
    }
    foreach ($pair in @(@('api',8000),@('web',3000))) {
        if (-not (Test-PortOwned $pair[1] $pair[0] $saved $Root)) {
            throw "Порт $($pair[1]) занят процессом, который не принадлежит этому запуску. Освободите порт самостоятельно."
        }
    }
    $fingerprint = Get-ProjectFingerprint $Root
    $mode = if ($Development) { 'dev' } else { 'production' }
    if ($saved.fingerprint -ne $fingerprint -or $saved.mode -ne $mode) {
        foreach ($name in @('bot','worker','web','api')) { Stop-OwnedService $name $saved $Root $ManifestPath }
        $saved.fingerprint = $fingerprint
        $saved.mode = $mode
        Save-ProcessManifest $saved $ManifestPath
    }
    function Save-Started([string]$Name, $Child) {
        $saved.$Name = $Child.Id
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($Child.Id)" -ErrorAction SilentlyContinue
        $saved.started.$Name = if ($processInfo) { $processInfo.CreationDate.ToUniversalTime().ToString('o') } else { $null }
        Save-ProcessManifest $saved $ManifestPath
    }
    function Wait-Ready([string]$Name, [string]$Url) {
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            if (-not (Get-OwnedService $Name $saved $Root)) { throw "Процесс $Name завершился. См. .local/$Name.err.log" }
            try { if ((Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { return } } catch { }
            Start-Sleep -Milliseconds 500
        }
        throw "Сервис $Name не прошёл проверку готовности. См. .local/$Name.err.log"
    }
    if (-not (Get-OwnedService api $saved $Root)) {
        & $PythonPath -X utf8 (Join-Path $Root 'scripts/migrate.py')
        if ($LASTEXITCODE -ne 0) { throw 'Миграция базы не выполнена. Проверьте журнал.' }
        $child = Start-Process -FilePath $PythonPath -ArgumentList @('-X','utf8','-m','uvicorn','apps.api.app.main:app','--host','127.0.0.1','--port','8000','--env-file','.env','--no-access-log') -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LocalDir 'api.out.log') -RedirectStandardError (Join-Path $LocalDir 'api.err.log')
        Save-Started api $child
    }
    Wait-Ready api 'http://127.0.0.1:8000/api/v1/health'
    $nodePath = (Get-Command node).Source
    $nextPath = Join-Path $Root 'apps/web/node_modules/next/dist/bin/next'
    if (-not (Get-OwnedService web $saved $Root)) {
        if (-not $Development) {
            Push-Location (Join-Path $Root 'apps/web')
            try { npm run build; $buildExitCode = $LASTEXITCODE } finally { Pop-Location }
            if ($buildExitCode -ne 0) { throw 'Не удалось собрать web приложение' }
        }
        $standalonePath = Join-Path $Root 'apps/web/.next/standalone/server.js'
        if (-not $Development -and (Test-Path -LiteralPath $standalonePath)) {
            # Next standalone does not copy public or compiled static assets.
            $standaloneRoot = Split-Path -Parent $standalonePath
            Copy-Item -LiteralPath (Join-Path $Root 'apps/web/public') -Destination $standaloneRoot -Recurse -Force
            Copy-Item -LiteralPath (Join-Path $Root 'apps/web/.next/static') -Destination (Join-Path $standaloneRoot '.next') -Recurse -Force
            $child = Start-Process -FilePath $nodePath -ArgumentList @("`"$standalonePath`"") -WorkingDirectory (Join-Path $Root 'apps/web') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LocalDir 'web.out.log') -RedirectStandardError (Join-Path $LocalDir 'web.err.log')
        } else {
            $webMode = if ($Development) { 'dev' } else { 'start' }
            $child = Start-Process -FilePath $nodePath -ArgumentList @("`"$nextPath`"",$webMode,'--hostname','127.0.0.1','--port','3000') -WorkingDirectory (Join-Path $Root 'apps/web') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LocalDir 'web.out.log') -RedirectStandardError (Join-Path $LocalDir 'web.err.log')
        }
        Save-Started web $child
    }
    Wait-Ready web 'http://127.0.0.1:3000'
    foreach ($name in @('worker','bot')) {
        if ($name -eq 'bot' -and -not $env:TELEGRAM_BOT_TOKEN) { Stop-OwnedService bot $saved $Root $ManifestPath; continue }
        if (-not (Get-OwnedService $name $saved $Root)) {
            $child = Start-Process -FilePath $PythonPath -ArgumentList @('-X','utf8','-m',"apps.$name.main") -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LocalDir "$name.out.log") -RedirectStandardError (Join-Path $LocalDir "$name.err.log")
            Save-Started $name $child
        }
    }
    Start-Sleep -Milliseconds 1000
    foreach ($name in @('api','web','worker','bot')) {
        if ($name -eq 'bot' -and -not $env:TELEGRAM_BOT_TOKEN) { continue }
        if (-not (Get-OwnedService $name $saved $Root)) { throw "Процесс $name завершился. См. .local/$name.err.log" }
    }
    if ((Get-ProjectFingerprint $Root) -ne $fingerprint) { throw 'Исходники изменились во время запуска. Повторите ./run.ps1.' }
    Write-Host 'Запуск: http://localhost:3000 | API: http://localhost:8000/docs'
    Write-Host 'Пароль администратора хранится в .env, журналы в .local.'
    Write-Host 'API, сайт и сборщик прошли проверку запуска. Telegram-бот запускается при наличии токена в .env.'
}
