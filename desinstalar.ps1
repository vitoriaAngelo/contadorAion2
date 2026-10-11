# Desinstalador do Contador de Almas
# Chamado por Configurações > Aplicativos > Contador de Almas > Desinstalar

param([switch]$Silencioso)

$ErrorActionPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Windows.Forms

$Nome    = "Contador de Almas"
$Destino = Join-Path $env:LOCALAPPDATA "ContadorDeAlmas"
$ChaveDesinstalar = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ContadorDeAlmas"
$Sim = [System.Windows.Forms.DialogResult]::Yes

if (-not $Silencioso) {
    $r = [System.Windows.Forms.MessageBox]::Show(
        "Desinstalar o $Nome?", $Nome,
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($r -ne $Sim) { exit 0 }

    $progresso = Join-Path $Destino "progresso.json"
    if (Test-Path $progresso) {
        $r = [System.Windows.Forms.MessageBox]::Show(
            "Quer guardar uma cópia do seu progresso (as almas contadas) na Área de Trabalho?`n`n" +
            "Se reinstalar depois, é só colocar o arquivo de volta na pasta do programa.",
            $Nome, [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($r -eq $Sim) {
            $desk = [Environment]::GetFolderPath("Desktop")
            Copy-Item $progresso (Join-Path $desk "ContadorDeAlmas-progresso.json") -Force
        }
    }
}

# fecha o contador se estiver aberto
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*contador_almas.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# atalhos
$desk = [Environment]::GetFolderPath("Desktop")
$menu = [Environment]::GetFolderPath("Programs")
Remove-Item (Join-Path $desk "$Nome.lnk") -Force
Remove-Item (Join-Path $menu "$Nome.lnk") -Force

# registro em Aplicativos
Remove-Item $ChaveDesinstalar -Recurse -Force

# a pasta do programa (inclusive este arquivo) e apagada logo depois que
# este script termina, para nao dar "arquivo em uso"
Start-Process -FilePath "cmd.exe" -WindowStyle Hidden -ArgumentList @(
    "/c", "timeout /t 2 /nobreak >nul & rmdir /s /q `"$Destino`"")

if (-not $Silencioso) {
    [System.Windows.Forms.MessageBox]::Show(
        "O $Nome foi desinstalado.`n`n" +
        "O Python e o Tesseract continuam instalados, porque outros programas podem usar. " +
        "Se quiser tirar também, é por Configurações > Aplicativos.",
        $Nome, [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
}
