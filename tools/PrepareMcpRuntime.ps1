param(
    [Parameter(Mandatory = $true)]
    [string]$CoreDir,
    [ValidateSet('Debug', 'RelWithDebInfo')]
    [string]$Configuration = 'Debug',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$corePath = (Resolve-Path -LiteralPath $CoreDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $corePath 'MaaCore.dll'))) {
    throw "MaaCore.dll is missing from $corePath"
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) {
    throw 'Install Visual Studio 2026 or Build Tools with the C++ desktop workload and Windows SDK.'
}
$vsPath = & $vswhere -latest -products '*' -version '[18.0,19.0)' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or -not $vsPath) {
    throw 'Visual Studio 2026 x64 C++ tools were not found.'
}
$crtPattern = 'x64/Microsoft.VC*.CRT'
if ($Configuration -eq 'Debug') {
    $crtPattern = 'debug_nonredist/x64/Microsoft.VC*.DebugCRT'
} elseif ($Remove) {
    throw 'Only the non-redistributable Debug CRT needs removal.'
}
$crtDir = Get-ChildItem -Path "$vsPath/VC/Redist/MSVC/*/$crtPattern" -Directory |
    Sort-Object FullName -Descending |
    Select-Object -First 1
if (-not $crtDir) {
    throw "The x64 MSVC $Configuration CRT is missing."
}
$libraries = @(Get-ChildItem -LiteralPath $crtDir.FullName -Filter '*.dll' -File)
if ($libraries.Count -eq 0) {
    throw 'No MSVC CRT DLLs were found.'
}
if ($Configuration -eq 'Debug') {
    $ucrt = Get-ChildItem -Path "${env:ProgramFiles(x86)}/Windows Kits/10/bin/*/x64/ucrt/ucrtbased.dll" -File |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if (-not $ucrt) {
        throw 'The Windows SDK x64 Debug UCRT is missing.'
    }
    $libraries += $ucrt
}

# Debug CRT DLLs are for local development only and must not be redistributed.
foreach ($library in $libraries) {
    $destination = Join-Path $corePath $library.Name
    if ($Remove) {
        Remove-Item -LiteralPath $destination
    } else {
        Copy-Item -LiteralPath $library.FullName -Destination $destination -Force
    }
}
if (-not $Remove -and $Configuration -eq 'Debug') {
    Write-Host 'Local Debug CRT installed. Do not redistribute this modified core directory.'
}
