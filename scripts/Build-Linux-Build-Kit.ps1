param(
    [string]$OutputDirectory = "",
    [switch]$ReplaceExisting
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Version = (& $Python -c "from vsg import __version__; print(__version__)").Trim()
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $ProjectRoot 'release' }
$ReleaseRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$KitName = "Vibe-Service-Guardian-Linux-build-kit-$Version"
$KitRoot = Join-Path $ReleaseRoot $KitName
$ResolvedKit = [System.IO.Path]::GetFullPath($KitRoot)
if (-not $ResolvedKit.StartsWith($ReleaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a build-kit path outside the release directory: $ResolvedKit"
}
if (Test-Path -LiteralPath $KitRoot) {
    if (-not $ReplaceExisting) { throw "Refusing to overwrite preserved build kit: $KitRoot" }
    Remove-Item -LiteralPath $KitRoot -Recurse -Force
}

$null = New-Item -ItemType Directory -Path $KitRoot
foreach ($directory in @('vsg','tests','scripts','docs','research','.github','requirements-lock')) {
    Copy-Item -Recurse -LiteralPath (Join-Path $ProjectRoot $directory) -Destination $KitRoot
}
foreach ($file in @(
    'pyproject.toml','requirements.txt','requirements-bootstrap.txt','requirements-build-linux.txt','VibeServiceGuardian.spec',
    'Start-VSG.sh','Stop-VSG.sh','Open-VSG.sh','Setup-Linux.sh','Vibe-Service-Guardian.desktop.in',
    'README.md','README.en.md','SECURITY.md','PRIVACY.md','THIRD_PARTY_NOTICES.md','LICENSE',
    'CHANGELOG.md','SUPPORT.md','LINUX-VALIDATION.md','IMPACT.md','MAINTAINERS.md','ROADMAP.md','GOVERNANCE.md'
)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $file) -Destination $KitRoot
}
Get-ChildItem -LiteralPath $KitRoot -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $KitRoot -Recurse -File -Filter '*.pyc' | Remove-Item -Force
& $Python (Join-Path $ProjectRoot 'scripts\Audit-Public-Tree.py') --root $KitRoot
$ZipPath = Join-Path $ReleaseRoot "$KitName.zip"
if (Test-Path -LiteralPath $ZipPath) {
    if (-not $ReplaceExisting) { throw "Refusing to overwrite preserved archive: $ZipPath" }
    Remove-Item -LiteralPath $ZipPath -Force
}
if (Test-Path -LiteralPath "$ZipPath.sha256") {
    if (-not $ReplaceExisting) { throw "Refusing to overwrite preserved checksum: $ZipPath.sha256" }
    Remove-Item -LiteralPath "$ZipPath.sha256" -Force
}
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
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash
Set-Content -LiteralPath "$ZipPath.sha256" -Encoding ascii -Value "$($Hash.ToLower())  $([IO.Path]::GetFileName($ZipPath))"
& $Python (Join-Path $ProjectRoot 'scripts\Validate-Archive.py') `
    --zip $ZipPath `
    --checksum "$ZipPath.sha256" `
    --expected-root $KitName `
    --version $Version `
    --platform linux `
    --kind build-kit
if ($LASTEXITCODE -ne 0) { throw "Linux build-kit archive validation failed: $LASTEXITCODE" }
Write-Host "Linux build kit: $ZipPath"
Write-Host "SHA256: $Hash"
