param([string]$DataRoot = 'E:\yaohe-dev')

# Dot-source this file before installation or development. Changes affect only
# this PowerShell session and its children, never system-wide user settings.
if (-not [IO.Path]::IsPathRooted($DataRoot)) {
    throw 'DataRoot must be an absolute path on the selected data drive.'
}
$developmentRoot = [IO.Path]::GetFullPath($DataRoot)
if (-not (Test-Path -LiteralPath ([IO.Path]::GetPathRoot($developmentRoot)))) {
    throw "The selected data drive is not available: $developmentRoot"
}
$developmentPaths = @{
    TEMP = 'temp'; TMP = 'temp'; TMPDIR = 'temp'
    UV_CACHE_DIR = 'cache\uv'; UV_PYTHON_INSTALL_DIR = 'runtimes\python'
    PIP_CACHE_DIR = 'cache\pip'; HF_HOME = 'cache\huggingface'
    HF_HUB_CACHE = 'cache\huggingface\hub'; TORCH_HOME = 'cache\torch'
    XDG_CACHE_HOME = 'cache'; npm_config_cache = 'cache\npm'
    ELECTRON_CACHE = 'cache\electron'; ELECTRON_BUILDER_CACHE = 'cache\electron-builder'
    PYINSTALLER_CONFIG_DIR = 'cache\pyinstaller'
    PLAYWRIGHT_BROWSERS_PATH = 'cache\playwright'; COREPACK_HOME = 'cache\corepack'
}
foreach ($entry in $developmentPaths.GetEnumerator()) {
    $cacheDirectory = Join-Path $developmentRoot $entry.Value
    New-Item -ItemType Directory -Force -Path $cacheDirectory | Out-Null
    Set-Item -LiteralPath "Env:$($entry.Key)" -Value $cacheDirectory
}
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$developmentTools = @('tools\uv-package\bin', 'tools\ffmpeg\bin') |
    ForEach-Object { Join-Path $developmentRoot $_ }
$env:PATH = ($developmentTools + $env:PATH) -join ';'
