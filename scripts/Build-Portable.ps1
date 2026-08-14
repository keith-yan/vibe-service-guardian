$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $SystemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($SystemPython) {
        & $SystemPython.Source -m venv (Join-Path $ProjectRoot '.venv')
    } else {
        & py.exe -3 -m venv (Join-Path $ProjectRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed: $LASTEXITCODE" }
}

$PythonTag = (& $VenvPython -c 'import sys; print(chr(112)+chr(121)+str(sys.version_info.major)+str(sys.version_info.minor))').Trim()
if ($PythonTag -notin @('py310', 'py311', 'py312')) {
    throw "Unsupported Python version for locked build: $PythonTag"
}
$BootstrapLock = Join-Path $ProjectRoot 'requirements-lock\bootstrap-py3.txt'
$BuildLock = Join-Path $ProjectRoot "requirements-lock\build-windows-$PythonTag.txt"
& $VenvPython -m pip install --disable-pip-version-check --only-binary=:all: --no-deps --require-hashes -r $BootstrapLock
if ($LASTEXITCODE -ne 0) { throw "Secure pip bootstrap failed: $LASTEXITCODE" }
& $VenvPython -m pip install --disable-pip-version-check --only-binary=:all: --no-deps --require-hashes -r $BuildLock
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed: $LASTEXITCODE" }
& $VenvPython (Join-Path $ProjectRoot 'scripts\Requirement-Locks.py') --verify
if ($LASTEXITCODE -ne 0) { throw "Requirement lock verification failed: $LASTEXITCODE" }
& $VenvPython (Join-Path $ProjectRoot 'scripts\Audit-Public-Tree.py') --root $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw "Public tree audit failed: $LASTEXITCODE" }
& $VenvPython -m unittest discover -s (Join-Path $ProjectRoot 'tests') -v
if ($LASTEXITCODE -ne 0) { throw "Test suite failed: $LASTEXITCODE" }
& $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $ProjectRoot 'VibeServiceGuardian.spec')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed: $LASTEXITCODE" }
$Version = (& $VenvPython -c 'from vsg import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0) { throw "Version lookup failed: $LASTEXITCODE" }
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Invalid application version: $Version"
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $ProjectRoot 'scripts\Validate-Windows.ps1') `
    -Executable (Join-Path $ProjectRoot 'dist\VibeServiceGuardian.exe') `
    -ExpectedVersion $Version
if ($LASTEXITCODE -ne 0) { throw "Portable executable validation failed: $LASTEXITCODE" }

$ReleaseRoot = Join-Path $ProjectRoot 'release'
$PortableRoot = Join-Path $ReleaseRoot "Vibe-Service-Guardian-Windows-x64-$Version"
if (Test-Path -LiteralPath $PortableRoot) {
    $ResolvedRelease = [IO.Path]::GetFullPath($ReleaseRoot)
    $ResolvedPortable = [IO.Path]::GetFullPath($PortableRoot)
    if (-not $ResolvedPortable.StartsWith($ResolvedRelease + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to clean a path outside the release directory.'
    }
    Remove-Item -LiteralPath $PortableRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $PortableRoot -Force | Out-Null

$Files = @(
    'Start-VSG.cmd', 'Stop-VSG.cmd', 'Open-VSG.cmd', 'README.md', 'README.en.md',
    'SECURITY.md', 'PRIVACY.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE',
    'CHANGELOG.md', 'SUPPORT.md', 'MACOS-VALIDATION.md', 'LINUX-VALIDATION.md',
    'IMPACT.md', 'MAINTAINERS.md', 'ROADMAP.md', 'GOVERNANCE.md'
)
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'dist\VibeServiceGuardian.exe') -Destination $PortableRoot
foreach ($File in $Files) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $File) -Destination $PortableRoot
}
$AsciiEncoding = New-Object System.Text.ASCIIEncoding
Get-ChildItem -LiteralPath $PortableRoot -Filter '*.cmd' -File | ForEach-Object {
    $Content = [IO.File]::ReadAllText($_.FullName)
    $Content = $Content -replace "`r?`n", "`r`n"
    [IO.File]::WriteAllText($_.FullName, $Content, $AsciiEncoding)
}
$PortableResearch = Join-Path $PortableRoot 'research'
New-Item -ItemType Directory -Path $PortableResearch -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'research\GITHUB_RESEARCH.md') -Destination $PortableResearch
$PortableDocs = Join-Path $PortableRoot 'docs'
New-Item -ItemType Directory -Path $PortableDocs -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\AGENT-SUPPORT.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\ARCHITECTURE.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\MODEL-CAPACITY.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\V0.8-FEATURES.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\V0.8.1-FEATURES.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\V0.8.2-HARDENING.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\PRODUCTION-READINESS-0.8.2.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\V0.8.3-CONVERGENCE.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\PRODUCTION-READINESS-0.8.3.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\V0.8.4-P0-CLOSURE.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\PRODUCTION-READINESS-0.8.4.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\VALIDATION.md') -Destination $PortableDocs
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\EVIDENCE-REGISTER.md') -Destination $PortableDocs
$PortableCaseStudies = Join-Path $PortableDocs 'case-studies'
New-Item -ItemType Directory -Path $PortableCaseStudies -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\case-studies\README.md') -Destination $PortableCaseStudies
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\case-studies\maintainer-validation.md') -Destination $PortableCaseStudies
$PortableAssets = Join-Path $PortableDocs 'assets'
New-Item -ItemType Directory -Path $PortableAssets -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\assets\vsg-overview.svg') -Destination $PortableAssets
$PortableScripts = Join-Path $PortableRoot 'scripts'
New-Item -ItemType Directory -Path $PortableScripts -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Validate-Windows.ps1') -Destination $PortableScripts

& $VenvPython (Join-Path $ProjectRoot 'scripts\Collect-ThirdPartyLicenses.py') `
    --output (Join-Path $PortableRoot 'THIRD_PARTY_LICENSES') `
    --sbom (Join-Path $PortableRoot 'SBOM.spdx.json') `
    --app-version $Version
if ($LASTEXITCODE -ne 0) { throw "License/SBOM generation failed: $LASTEXITCODE" }

$ZipPath = Join-Path $ReleaseRoot "Vibe-Service-Guardian-Windows-x64-$Version.zip"
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
Compress-Archive -LiteralPath $PortableRoot -DestinationPath $ZipPath -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
Set-Content -LiteralPath ($ZipPath + '.sha256') -Value ($Hash + '  ' + [IO.Path]::GetFileName($ZipPath)) -Encoding ascii
& $VenvPython (Join-Path $ProjectRoot 'scripts\Validate-Archive.py') `
    --zip $ZipPath `
    --checksum ($ZipPath + '.sha256') `
    --expected-root ([IO.Path]::GetFileName($PortableRoot)) `
    --version $Version `
    --platform windows
if ($LASTEXITCODE -ne 0) { throw "Portable archive validation failed: $LASTEXITCODE" }
Write-Host "Portable package: $ZipPath"
Write-Host "SHA256: $Hash"
