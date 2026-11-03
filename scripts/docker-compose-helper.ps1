function Invoke-DesignRagDockerCommand {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Operation,
        [int]$TimeoutSeconds = 30
    )

    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        throw 'Docker CLI was not found. Start Docker Desktop, then run the project startup script again.'
    }

    $tempDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("design-rag-docker-" + [guid]::NewGuid().ToString('N'))
    $stdoutPath = Join-Path $tempDirectory 'stdout.log'
    $stderrPath = Join-Path $tempDirectory 'stderr.log'
    New-Item -ItemType Directory -Path $tempDirectory -Force | Out-Null
    try {
        $process = Start-Process -FilePath $docker.Source -ArgumentList $Arguments -NoNewWindow -PassThru `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "$Operation did not respond within $TimeoutSeconds seconds. Docker Desktop may still be starting or its Linux engine is unavailable."
        }

        $output = @()
        if (Test-Path -LiteralPath $stdoutPath) { $output += Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath $stderrPath) { $output += Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue }
        if ($process.ExitCode -ne 0) {
            $details = (($output | Where-Object { $_ }) -join [Environment]::NewLine).Trim()
            if (-not $details) { $details = "Docker exited with code $($process.ExitCode)." }
            throw "$Operation failed. $details"
        }
        return $output
    } finally {
        if (Test-Path -LiteralPath $tempDirectory) {
            Remove-Item -LiteralPath $tempDirectory -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-DesignRagDockerReady {
    param([int]$TimeoutSeconds = 90)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $attempt = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        try {
            $version = Invoke-DesignRagDockerCommand -Arguments @('version', '--format', '{{.Server.Version}}') `
                -Operation 'Docker Desktop health check' -TimeoutSeconds 10
            $serverVersion = ($version | Where-Object { $_ } | Select-Object -Last 1).Trim()
            if ($serverVersion) {
                Write-Host "Docker Desktop Linux engine is ready ($serverVersion)."
                return
            }
        } catch {
            Write-Host "Waiting for Docker Desktop Linux engine (attempt $attempt)..."
        }
        Start-Sleep -Seconds 3
    }
    throw "Docker Desktop Linux engine was not ready within $TimeoutSeconds seconds. Open Docker Desktop, wait until it reports Engine running, then retry start-project.bat."
}

function Invoke-DesignRagCompose {
    param(
        [Parameter(Mandatory)][string[]]$ComposeArguments,
        [Parameter(Mandatory)][string]$Operation,
        [int]$TimeoutSeconds = 120
    )

    Wait-DesignRagDockerReady
    Invoke-DesignRagDockerCommand -Arguments (@('compose', '--project-name', 'design-rag') + $ComposeArguments) `
        -Operation $Operation -TimeoutSeconds $TimeoutSeconds
}
