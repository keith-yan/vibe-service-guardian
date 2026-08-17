param(
    [string]$Executable = '',
    [switch]$Source,
    [string]$PythonPath = '',
    [string]$ExpectedVersion = '',
    [switch]$KeepArtifacts
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $IsWindows -and $PSVersionTable.PSEdition -eq 'Core') {
    throw 'This validation script must run on Windows.'
}

if (-not $PythonPath) {
    $BundledPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $BundledPython) {
        $PythonPath = $BundledPython
    }
}

if (-not $Source -and -not $Executable) {
    $Executable = Join-Path $ProjectRoot 'dist\VibeServiceGuardian.exe'
}
if ($Source) {
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
        throw 'Source validation requires a local Python interpreter.'
    }
    $Runner = $PythonPath
    $RunnerArguments = '-m vsg'
    $RunnerLabel = 'source'
}
else {
    $Executable = [IO.Path]::GetFullPath($Executable)
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Executable not found: $Executable"
    }
    $Runner = $Executable
    $RunnerArguments = ''
    $RunnerLabel = 'portable-exe'
}

if (-not $ExpectedVersion) {
    $Pyproject = Join-Path $ProjectRoot 'pyproject.toml'
    if (Test-Path -LiteralPath $Pyproject) {
        $VersionMatch = Select-String -LiteralPath $Pyproject -Pattern '^version = "([^"]+)"$'
        if ($VersionMatch) {
            $ExpectedVersion = $VersionMatch.Matches[0].Groups[1].Value
        }
    }
}

$TempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$ValidationRoot = Join-Path $TempBase ('vsg-windows-validation-' + [guid]::NewGuid().ToString('N'))
$DataDir = Join-Path $ValidationRoot 'data'
New-Item -ItemType Directory -Path $ValidationRoot | Out-Null
$StdoutPath = Join-Path $ValidationRoot 'stdout.txt'
$StderrPath = Join-Path $ValidationRoot 'stderr.txt'
$RuntimePath = Join-Path $DataDir 'runtime.json'
$StartedProcess = $null
$BaseUrl = $null

function Get-HttpStatus {
    param(
        [string]$Uri,
        [string]$Method = 'GET',
        [hashtable]$Headers = @{},
        [string]$Body = ''
    )
    try {
        $RequestParameters = @{
            UseBasicParsing = $true
            Uri = $Uri
            Method = $Method
            Headers = $Headers
            TimeoutSec = 3
        }
        if ($Method -ne 'GET') {
            $RequestParameters.Body = $Body
            $RequestParameters.ContentType = 'application/json'
        }
        $Response = Invoke-WebRequest @RequestParameters
        return [int]$Response.StatusCode
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            return [int]$_.Exception.Response.StatusCode
        }
        throw
    }
}

function Stop-VsgInstance {
    if ($Source) {
        & $PythonPath -m vsg --data-dir $DataDir --stop
        return [int]$LASTEXITCODE
    }
    $StopArguments = '--data-dir "' + $DataDir + '" --stop'
    $StopProcess = Start-Process -FilePath $Executable -ArgumentList $StopArguments `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -Wait
    return [int]$StopProcess.ExitCode
}

try {
    $QuotedDataDir = '"' + $DataDir + '"'
    $StartArguments = ($RunnerArguments + ' --port 0 --data-dir ' + $QuotedDataDir).Trim()
    $StartedProcess = Start-Process -FilePath $Runner -ArgumentList $StartArguments `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath

    for ($Attempt = 0; $Attempt -lt 80; $Attempt++) {
        if (Test-Path -LiteralPath $RuntimePath) { break }
        Start-Sleep -Milliseconds 125
    }
    if (-not (Test-Path -LiteralPath $RuntimePath)) {
        throw 'runtime.json was not created within 10 seconds.'
    }

    $Runtime = Get-Content -Raw -Encoding utf8 -LiteralPath $RuntimePath | ConvertFrom-Json
    $RuntimePid = [int]$Runtime.pid
    $RuntimePort = [int]$Runtime.port
    if ($RuntimePid -le 0 -or $RuntimePort -lt 1024 -or $RuntimePort -gt 65535) {
        throw 'runtime.json contains an invalid PID or port.'
    }
    $BaseUrl = "http://127.0.0.1:$RuntimePort"

    $Health = Invoke-RestMethod -Uri ($BaseUrl + '/healthz') -TimeoutSec 3
    if (
        $Health.ok -ne $true -or
        $Health.version -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$' -or
        -not $Health.instance_id
    ) {
        throw 'Health response is not a versioned VSG response.'
    }
    if ($ExpectedVersion -and $Health.version -ne $ExpectedVersion) {
        throw "Version mismatch: expected $ExpectedVersion, received $($Health.version)."
    }

    $Listeners = @(Get-NetTCPConnection -State Listen -OwningProcess $RuntimePid -ErrorAction Stop)
    $ExpectedListener = @($Listeners | Where-Object {
        $_.LocalAddress -eq '127.0.0.1' -and $_.LocalPort -eq $RuntimePort
    })
    $NonLoopback = @($Listeners | Where-Object {
        $_.LocalPort -eq $RuntimePort -and $_.LocalAddress -ne '127.0.0.1'
    })
    if ($ExpectedListener.Count -ne 1 -or $NonLoopback.Count -ne 0) {
        throw 'The control port is not bound exclusively to 127.0.0.1.'
    }

    if ((Get-HttpStatus -Uri ($BaseUrl + '/healthz') -Headers @{ Host = 'example.invalid' }) -ne 421) {
        throw 'Invalid Host was not rejected with HTTP 421.'
    }
    if ((Get-HttpStatus -Uri ($BaseUrl + '/healthz') -Headers @{ Origin = 'https://example.invalid' }) -ne 421) {
        throw 'Cross-origin GET was not rejected with HTTP 421.'
    }
    if ((Get-HttpStatus -Uri ($BaseUrl + '/api/refresh') -Method POST -Headers @{ 'X-VSG-Token' = 'invalid' } -Body '{}') -ne 403) {
        throw 'Invalid control token was not rejected with HTTP 403.'
    }

    $Bootstrap = Invoke-RestMethod -Uri ($BaseUrl + '/api/bootstrap') -TimeoutSec 3
    if (
        $Bootstrap.version -ne $Health.version -or
        $Bootstrap.instance_id -ne $Health.instance_id -or
        -not $Bootstrap.token
    ) {
        throw 'Bootstrap response is inconsistent with health response.'
    }

    $Status = $null
    for ($Attempt = 0; $Attempt -lt 120; $Attempt++) {
        $Status = Invoke-RestMethod -Uri ($BaseUrl + '/api/status') -TimeoutSec 3
        if ($Status.snapshot.generated_at) { break }
        Start-Sleep -Milliseconds 125
    }
    if (-not $Status.snapshot.generated_at) {
        throw 'The first collector snapshot did not complete within 15 seconds.'
    }
    if (
        $Status.platform.key -ne 'windows' -or
        $Status.snapshot.schema_version -ne '2.0' -or
        $Status.snapshot.collectors.host.method -ne 'psutil' -or
        $Status.snapshot.collectors.host.status -ne 'ok'
    ) {
        throw 'The Windows host collector did not return the expected snapshot contract.'
    }
    if (
        $null -eq $Status.snapshot.telemetry.cpu.percent -or
        $null -eq $Status.snapshot.telemetry.memory.used_percent -or
        @($Status.snapshot.telemetry.disks).Count -lt 1 -or
        -not $Status.snapshot.posture.overall.state -or
        $null -eq $Status.snapshot.posture.overall.unknown_domain_count -or
        $null -eq $Status.snapshot.runtime_probes -or
        $null -eq $Status.snapshot.trusted_nodes -or
        $Status.snapshot.service_relationships.schema_version -ne '1.1'
    ) {
        throw 'The AI runtime health snapshot is incomplete.'
    }
    $ServiceBenchmarks = Invoke-RestMethod -Uri ($BaseUrl + '/api/service-benchmarks') -TimeoutSec 3
    $Snapshots = Invoke-RestMethod -Uri ($BaseUrl + '/api/snapshots') -TimeoutSec 3
    $Relationships = Invoke-RestMethod -Uri ($BaseUrl + '/api/service-relationships') -TimeoutSec 3
    $StopVerifications = Invoke-RestMethod -Uri ($BaseUrl + '/api/stop-verifications') -TimeoutSec 3
    $StopObservations = Invoke-RestMethod -Uri ($BaseUrl + '/api/stop-observations') -TimeoutSec 3
    $MatrixStatus = Invoke-RestMethod -Uri ($BaseUrl + '/api/benchmark-matrix/status') -TimeoutSec 3
    $AssessmentCount = @($Relationships.relationships.assessments.PSObject.Properties).Count
    if (
        $ServiceBenchmarks.ok -ne $true -or
        $Snapshots.ok -ne $true -or
        $Relationships.ok -ne $true -or
        $StopVerifications.ok -ne $true -or
        $StopObservations.ok -ne $true -or
        $MatrixStatus.ok -ne $true
    ) {
        throw 'A history, relationship, or workload-matrix read endpoint failed.'
    }
    if (
        $Relationships.relationships.schema_version -ne '1.1' -or
        [int]$Relationships.relationships.summary.services -ne [int]$Status.snapshot.summary.services -or
        $AssessmentCount -ne [int]$Status.snapshot.summary.services
    ) {
        throw 'The service relationship model is inconsistent with the service snapshot.'
    }
    if (
        @($StopVerifications.items).Count -ne 0 -or
        @($StopObservations.items).Count -ne 0 -or
        @($StopObservations.active).Count -ne 0 -or
        $null -ne $MatrixStatus.active_job_id -or
        $null -ne $MatrixStatus.job
    ) {
        throw 'A fresh validation profile unexpectedly contains stop or active benchmark state.'
    }
    if ((Get-HttpStatus -Uri ($BaseUrl + '/api/snapshots/create') -Method POST `
        -Headers @{ 'X-VSG-Token' = [string]$Bootstrap.token } -Body '{"paths":[],"confirmation":""}') -ne 409) {
        throw 'An unconfirmed snapshot request was not rejected with HTTP 409.'
    }
    if ((Get-HttpStatus -Uri ($BaseUrl + '/api/refresh') -Method POST `
        -Headers @{ 'X-VSG-Token' = [string]$Bootstrap.token } -Body '{}') -ne 202) {
        throw 'Authorized refresh did not return HTTP 202.'
    }

    $PlannerStatus = Invoke-RestMethod -Uri ($BaseUrl + '/api/model-planner/status') -TimeoutSec 10
    if (
        $PlannerStatus.ok -ne $true -or
        $PlannerStatus.hardware.platform.key -ne 'windows' -or
        [int]$PlannerStatus.catalog.model_count -lt 10 -or
        $PlannerStatus.catalog.offline -ne $true -or
        $PlannerStatus.privacy.telemetry -ne $false -or
        $null -eq $PlannerStatus.measured_profiles.items -or
        $null -eq $PlannerStatus.measured_profiles.summary.valid -or
        $null -eq $PlannerStatus.current_resource_margin.guard_percent
    ) {
        throw 'Model planner status did not return the expected offline Windows contract.'
    }
    $EstimateBody = @{
        total_users = 25
        concurrency = 4
        prompt_tokens = 1024
        context_tokens = 8192
        output_tokens = 512
        target_tps_per_user = 8
        target_ttft_seconds = 5
        preference = 'balanced'
        runtime = 'auto'
        kv_cache_bits = 16
    } | ConvertTo-Json -Compress
    $Estimate = Invoke-RestMethod -Uri ($BaseUrl + '/api/model-planner/estimate') `
        -Method POST -Headers @{ 'X-VSG-Token' = [string]$Bootstrap.token } `
        -ContentType 'application/json' -Body $EstimateBody -TimeoutSec 10
    if (
        $Estimate.ok -ne $true -or
        $Estimate.estimate.schema_version -ne '1.1' -or
        @($Estimate.estimate.candidates).Count -ne [int]$PlannerStatus.catalog.model_count -or
        $null -eq $Estimate.estimate.calibration_summary.available_samples -or
        $null -eq $Estimate.estimate.calibration_summary.calibrated_candidates -or
        -not $Estimate.estimate.ceilings.physical -or
        -not $Estimate.estimate.ceilings.usable -or
        -not $Estimate.estimate.ceilings.sla -or
        [string]$Estimate.estimate.runtime_plan.binding -notmatch '^127\.0\.0\.1:' -or
        $Estimate.estimate.runtime_plan.will_execute -ne $false
    ) {
        throw 'Model planner estimate did not return the expected three-level safe plan.'
    }
    $UnsafeBenchmarkBody = @{
        model_id = 'gpt-oss-20b'
        quantization = 'Q4_K_M'
        model_path = 'relative.gguf'
        confirmation = ''
    } | ConvertTo-Json -Compress
    if ((Get-HttpStatus -Uri ($BaseUrl + '/api/model-planner/benchmark') -Method POST `
        -Headers @{ 'X-VSG-Token' = [string]$Bootstrap.token } -Body $UnsafeBenchmarkBody) -ne 409) {
        throw 'Unsafe benchmark request was not rejected with HTTP 409.'
    }

    $IndexResponse = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUrl + '/') -TimeoutSec 3
    $Csp = [string]$IndexResponse.Headers['Content-Security-Policy']
    if ($Csp -notmatch "default-src 'self'" -or $Csp -notmatch "frame-ancestors 'none'") {
        throw 'Expected Content-Security-Policy was not returned.'
    }

    $StopExitCode = Stop-VsgInstance
    if ($StopExitCode -ne 0) {
        throw "Graceful stop returned exit code $StopExitCode."
    }
    $StartedProcess.WaitForExit(15000) | Out-Null
    if (-not $StartedProcess.HasExited) {
        throw 'The validated process did not exit after graceful shutdown.'
    }
    if (Test-Path -LiteralPath $RuntimePath) {
        throw 'runtime.json was not removed after graceful shutdown.'
    }

    [ordered]@{
        ok = $true
        runner = $RunnerLabel
        version = [string]$Health.version
        pid = $RuntimePid
        port = $RuntimePort
        loopback_only = $true
        invalid_host_status = 421
        invalid_origin_status = 421
        invalid_token_status = 403
        authorized_refresh_status = 202
        model_catalog_count = [int]$PlannerStatus.catalog.model_count
        model_estimate_candidates = @($Estimate.estimate.candidates).Count
        model_planner_offline = $true
        unsafe_benchmark_status = 409
        unconfirmed_snapshot_status = 409
        relationship_nodes = @($Relationships.relationships.nodes).Count
        relationship_edges = @($Relationships.relationships.edges).Count
        relationship_dependencies = [int]$Relationships.relationships.summary.local_dependencies
        stop_assessments = $AssessmentCount
        stop_verification_history = @($StopVerifications.items).Count
        stop_observation_history = @($StopObservations.items).Count
        benchmark_matrix_idle = ($null -eq $MatrixStatus.active_job_id -and $null -eq $MatrixStatus.job)
        measured_profile_count = @($PlannerStatus.measured_profiles.items).Count
        calibration_samples = [int]$Estimate.estimate.calibration_summary.available_samples
        calibrated_candidates = [int]$Estimate.estimate.calibration_summary.calibrated_candidates
        telemetry_gpu_count = @($Status.snapshot.telemetry.gpus).Count
        health_overall_state = [string]$Status.snapshot.posture.overall.state
        health_unknown_domains = [int]$Status.snapshot.posture.overall.unknown_domain_count
        csp = $true
        service_count = [int]$Status.snapshot.summary.services
        collector_error_count = @($Status.snapshot.errors).Count
        graceful_shutdown = $true
        artifacts = if ($KeepArtifacts) { $ValidationRoot } else { $null }
    } | ConvertTo-Json
}
finally {
    if ($BaseUrl -and (Test-Path -LiteralPath $RuntimePath)) {
        try {
            Stop-VsgInstance | Out-Null
        }
        catch {}
    }
    if ($StartedProcess -and -not $StartedProcess.HasExited) {
        Stop-Process -Id $StartedProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if (-not $KeepArtifacts) {
        $ResolvedValidation = [IO.Path]::GetFullPath($ValidationRoot)
        $SafePrefix = $TempBase.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if (
            $ResolvedValidation.StartsWith($SafePrefix, [StringComparison]::OrdinalIgnoreCase) -and
            [IO.Path]::GetFileName($ResolvedValidation).StartsWith('vsg-windows-validation-', [StringComparison]::Ordinal)
        ) {
            Remove-Item -LiteralPath $ResolvedValidation -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
