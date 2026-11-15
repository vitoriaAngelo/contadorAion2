param([string]$Iscc)

$ErrorActionPreference = "Stop"
$Raiz = $PSScriptRoot
$Versao = (Get-Content (Join-Path $Raiz "version.txt") -Raw).Trim()

if (-not $Iscc) {
    $candidatos = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $candidatos | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    throw "Inno Setup 6 não encontrado. Instale em https://jrsoftware.org/isdl.php"
}

$Saida = Join-Path $Raiz "release"
New-Item -ItemType Directory -Force -Path $Saida | Out-Null
Remove-Item (Join-Path $Saida "ContadorDeAlmas-Setup.exe") -Force -ErrorAction SilentlyContinue

& $Iscc "/DMyAppVersion=$Versao" (Join-Path $Raiz "installer\ContadorDeAlmas.iss")
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o instalador." }

$Instalador = Join-Path $Saida "ContadorDeAlmas-Setup.exe"
if (-not (Test-Path $Instalador)) { throw "O instalador não foi gerado." }
Write-Host "Instalador pronto: $Instalador"
