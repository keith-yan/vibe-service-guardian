$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ReleaseRoot = Join-Path $ProjectRoot 'release'
$VersionMatch = Select-String -LiteralPath (Join-Path $ProjectRoot 'pyproject.toml') -Pattern '^version = "([^"]+)"$'
if (-not $VersionMatch) { throw 'Unable to read version from pyproject.toml.' }
$Version = $VersionMatch.Matches[0].Groups[1].Value
$KitRevision = 'r9'
$KitName = "Vibe-Service-Guardian-macOS-build-kit-$Version-$KitRevision"
$KitRoot = Join-Path $ReleaseRoot $KitName

if (Test-Path -LiteralPath $KitRoot) {
    $ResolvedRelease = [IO.Path]::GetFullPath($ReleaseRoot)
    $ResolvedKit = [IO.Path]::GetFullPath($KitRoot)
    if (-not $ResolvedKit.StartsWith($ResolvedRelease + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to clean a path outside the release directory.'
    }
    Remove-Item -LiteralPath $KitRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $KitRoot -Force | Out-Null

$TopFiles = @(
    '.gitattributes',
    'Start-VSG.command', 'Stop-VSG.command', 'Open-VSG.command', 'Setup-macOS.command',
    'Run-macOS-VM-Auto-Test.command', 'Start-macOS-Manual-Test.command',
    'Finish-macOS-Manual-Test.command', 'MACOS-VM-QUICKSTART.md',
    'README.md', 'README.en.md', 'MACOS-VALIDATION.md', 'LINUX-VALIDATION.md', 'SECURITY.md', 'PRIVACY.md',
    'THIRD_PARTY_NOTICES.md', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md',
    'CODE_OF_CONDUCT.md', 'SUPPORT.md', 'IMPACT.md', 'MAINTAINERS.md',
    'ROADMAP.md', 'GOVERNANCE.md',
    'requirements.txt', 'requirements-audit.txt', 'requirements-bootstrap.txt',
    'requirements-build.txt', 'requirements-build-linux.txt', 'requirements-build-macos.txt',
    'pyproject.toml', 'VibeServiceGuardian.spec'
)
foreach ($File in $TopFiles) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $File) -Destination $KitRoot
}
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'vsg') -Destination $KitRoot -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'tests') -Destination $KitRoot -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'requirements-lock') -Destination $KitRoot -Recurse
New-Item -ItemType Directory -Path (Join-Path $KitRoot 'scripts') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Build-Portable-macOS.sh') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Build-Portable-Linux.sh') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Build-Portable.ps1') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Validate-macOS.sh') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Validate-Windows.ps1') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Collect-ThirdPartyLicenses.py') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Audit-Public-Tree.py') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Validate-Archive.py') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'scripts\Requirement-Locks.py') -Destination (Join-Path $KitRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs') -Destination $KitRoot -Recurse
New-Item -ItemType Directory -Path (Join-Path $KitRoot 'research') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'research\GITHUB_RESEARCH.md') -Destination (Join-Path $KitRoot 'research')
Get-ChildItem -LiteralPath $KitRoot -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $KitRoot -Recurse -File -Filter '*.pyc' | Remove-Item -Force
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') `
    (Join-Path $KitRoot 'scripts\Requirement-Locks.py') --verify
if ($LASTEXITCODE -ne 0) { throw "Build-kit requirement-lock verification failed: $LASTEXITCODE" }
Push-Location $KitRoot
try {
    & (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw "Build-kit self-contained test suite failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}
Get-ChildItem -LiteralPath $KitRoot -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $KitRoot -Recurse -File -Filter '*.pyc' | Remove-Item -Force
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') `
    (Join-Path $ProjectRoot 'scripts\Audit-Public-Tree.py') --root $KitRoot
if ($LASTEXITCODE -ne 0) { throw "Public tree audit failed: $LASTEXITCODE" }

$ZipPath = Join-Path $ReleaseRoot ($KitName + '.zip')
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Archive = [IO.Compression.ZipFile]::Open($ZipPath, [IO.Compression.ZipArchiveMode]::Create)
try {
    $ArchiveBase = Split-Path -Parent $KitRoot
    Get-ChildItem -LiteralPath $KitRoot -Recurse -File | ForEach-Object {
        $EntryName = $_.FullName.Substring($ArchiveBase.Length + 1).Replace('\', '/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $Archive,
            $_.FullName,
            $EntryName,
            [IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}
finally {
    $Archive.Dispose()
}
$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
Set-Content -LiteralPath ($ZipPath + '.sha256') -Value ($Hash + '  ' + [IO.Path]::GetFileName($ZipPath)) -Encoding ascii
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') `
    (Join-Path $ProjectRoot 'scripts\Validate-Archive.py') `
    --zip $ZipPath `
    --checksum ($ZipPath + '.sha256') `
    --expected-root $KitName `
    --version $Version `
    --platform macos `
    --kind build-kit
if ($LASTEXITCODE -ne 0) { throw "macOS build-kit archive validation failed: $LASTEXITCODE" }

$TarPath = Join-Path $ReleaseRoot ($KitName + '.tar.gz')
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') `
    (Join-Path $ProjectRoot 'scripts\Create-macOS-Build-Kit-Tar.py') `
    --root $KitRoot `
    --output $TarPath
if ($LASTEXITCODE -ne 0) { throw "macOS executable-mode tar archive failed: $LASTEXITCODE" }

Write-Host "macOS build kit: $ZipPath"
Write-Host "SHA256: $Hash"
Write-Host "macOS executable-mode build kit: $TarPath"
