# Instalador do Contador de Almas (Aion 2)
# Instala só para o seu usuário (não precisa ser administrador),
# cria atalhos e registra em Configurações > Aplicativos para poder desinstalar.

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Nome    = "Contador de Almas"
$Versao  = "1.6.2"
$Origem  = $PSScriptRoot
$Destino = Join-Path $env:LOCALAPPDATA "ContadorDeAlmas"
$ChaveDesinstalar = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ContadorDeAlmas"
$UrlPortugues = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/por.traineddata"

function Passo($n, $total, $texto) {
    Write-Host ""
    Write-Host "[$n/$total] $texto" -ForegroundColor Cyan
}
function Ok($texto)    { Write-Host "    OK  $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "    !!  $texto" -ForegroundColor Yellow }
function Erro($texto)  { Write-Host "    XX  $texto" -ForegroundColor Red }

function Rodar {
    # roda um programa externo sem tratar avisos (stderr) como erro fatal
    param([string]$exe, [string[]]$parametros)
    $antes = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @parametros 2>&1 | ForEach-Object { "    $_" } | Out-Host }
    finally { $ErrorActionPreference = $antes }
    return $LASTEXITCODE
}

function Tem-Winget {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Testar-Python($exe) {
    # Devolve o caminho real do python se ele funcionar e tiver tkinter
    $ErrorActionPreference = "Continue"
    try {
        $saida = & $exe -c "import sys, tkinter; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $saida) {
            $caminho = ($saida | Select-Object -Last 1).Trim()
            if ($caminho -and (Test-Path $caminho) -and ($caminho -notmatch "WindowsApps")) {
                return $caminho
            }
        }
    } catch {}
    return $null
}

function Achar-Python {
    $ErrorActionPreference = "Continue"
    $tentativas = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $tentativas += ,@("py", "-3") }
    foreach ($t in $tentativas) {
        try {
            $saida = & $t[0] $t[1] -c "import sys, tkinter; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $saida) {
                $c = ($saida | Select-Object -Last 1).Trim()
                if ($c -and (Test-Path $c)) { return $c }
            }
        } catch {}
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $r = Testar-Python $cmd.Source
        if ($r) { return $r }
    }
    $pastas = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python") -Directory -ErrorAction SilentlyContinue |
              Sort-Object Name -Descending
    foreach ($p in $pastas) {
        $exe = Join-Path $p.FullName "python.exe"
        if (Test-Path $exe) {
            $r = Testar-Python $exe
            if ($r) { return $r }
        }
    }
    return $null
}

function Achar-Tesseract {
    $lista = @(
        "C:\Program Files\Tesseract-OCR\tesseract.exe",
        "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Tesseract-OCR\tesseract.exe")
    )
    foreach ($c in $lista) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Fechar-Contador {
    try {
        Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*contador_almas.py*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch {}
}

function Criar-Atalho($arquivo, $alvo, $parametros, $pasta, $icone) {
    $sh = New-Object -ComObject WScript.Shell
    $a = $sh.CreateShortcut($arquivo)
    $a.TargetPath = $alvo
    $a.Arguments = $parametros
    $a.WorkingDirectory = $pasta
    $a.IconLocation = "$icone,0"
    $a.Description = "Contador de almas para Aion 2"
    $a.Save()
}

Write-Host "==============================================" -ForegroundColor DarkCyan
Write-Host "   Instalando o $Nome $Versao"                  -ForegroundColor White
Write-Host "==============================================" -ForegroundColor DarkCyan

try {
    $total = 6

    # 1) Python ---------------------------------------------------------------
    Passo 1 $total "Procurando o Python..."
    $py = Achar-Python
    if (-not $py) {
        if (Tem-Winget) {
            Aviso "Python não encontrado. Instalando o Python 3.12 (pode demorar alguns minutos)..."
            Rodar "winget" @("install", "--id", "Python.Python.3.12", "-e", "--scope", "user", "--silent", "--accept-package-agreements", "--accept-source-agreements") | Out-Null
            $py = Achar-Python
        }
        if (-not $py) {
            Erro "Não consegui instalar o Python sozinho."
            Write-Host "    Instale pelo site (marque 'Add python.exe to PATH') e rode o INSTALAR.bat de novo:"
            Write-Host "    https://www.python.org/downloads/"
            Start-Process "https://www.python.org/downloads/"
            throw "Python ausente"
        }
    }
    Ok "Python: $py"
    $pyw = Join-Path (Split-Path $py) "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $py }

    # 2) Bibliotecas ----------------------------------------------------------
    Passo 2 $total "Instalando as bibliotecas (mss, Pillow, pytesseract)..."
    $cod = Rodar $py @("-m", "pip", "install", "--user", "--upgrade", "--disable-pip-version-check", "-q", "-r", (Join-Path $Origem "requirements.txt"))
    if ($cod -ne 0) { throw "Falha ao instalar as bibliotecas do Python" }
    Ok "Bibliotecas instaladas"

    # 3) Tesseract (leitor de texto da tela) -----------------------------------
    Passo 3 $total "Procurando o Tesseract (leitor de texto da tela)..."
    $tess = Achar-Tesseract
    if (-not $tess -and (Tem-Winget)) {
        Aviso "Tesseract não encontrado. Instalando... (o Windows pode pedir permissão de administrador)"
        Rodar "winget" @("install", "--id", "UB-Mannheim.TesseractOCR", "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements") | Out-Null
        $tess = Achar-Tesseract
    }
    if ($tess) {
        Ok "Tesseract: $tess"
    } else {
        Aviso "Não consegui instalar o Tesseract sozinho. Instale por este site e pronto (não precisa reinstalar o contador):"
        Write-Host "    https://github.com/UB-Mannheim/tesseract/wiki"
        Start-Process "https://github.com/UB-Mannheim/tesseract/wiki"
    }

    # 4) Copiar o programa ------------------------------------------------------
    Passo 4 $total "Copiando o programa para $Destino ..."
    Fechar-Contador
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
    foreach ($arq in @("contador_almas.py", "icone.ico", "desinstalar.ps1", "requirements.txt", "LICENSE", "README.md")) {
        $de = Join-Path $Origem $arq
        if (Test-Path $de) { Copy-Item $de -Destination $Destino -Force }
    }
    Get-ChildItem $Destino -File | Unblock-File -ErrorAction SilentlyContinue
    Ok "Programa copiado (seu progresso, se já existia, foi mantido)"

    # 5) Português para o leitor de texto ----------------------------------------
    Passo 5 $total "Baixando o pacote de português do leitor de texto..."
    $td = Join-Path $Destino "tessdata"
    New-Item -ItemType Directory -Force -Path $td | Out-Null
    $por = Join-Path $td "por.traineddata"
    if (-not (Test-Path $por) -or (Get-Item $por).Length -lt 100000) {
        try {
            Invoke-WebRequest -Uri $UrlPortugues -OutFile $por -UseBasicParsing
            Ok "Português baixado"
        } catch {
            Remove-Item $por -ErrorAction SilentlyContinue
            Aviso "Não consegui baixar o português; o programa vai usar o idioma que vier com o Tesseract."
        }
    } else {
        Ok "Português já estava baixado"
    }

    # 6) Atalhos e registro ---------------------------------------------------------
    Passo 6 $total "Criando os atalhos e registrando em Aplicativos..."
    $script = Join-Path $Destino "contador_almas.py"
    $icone  = Join-Path $Destino "icone.ico"
    $argumentos = "`"$script`""
    $desk = [Environment]::GetFolderPath("Desktop")
    $menu = [Environment]::GetFolderPath("Programs")
    Criar-Atalho (Join-Path $desk "$Nome.lnk") $pyw $argumentos $Destino $icone
    Criar-Atalho (Join-Path $menu "$Nome.lnk") $pyw $argumentos $Destino $icone
    Ok "Atalho na Área de Trabalho e no Menu Iniciar"

    $desinstalar = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Destino\desinstalar.ps1`""
    New-Item -Path $ChaveDesinstalar -Force | Out-Null
    $tamanhoKb = [int]((Get-ChildItem $Destino -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
    $props = @{
        "DisplayName"     = $Nome
        "DisplayVersion"  = $Versao
        "Publisher"       = "Contador de Almas"
        "DisplayIcon"     = $icone
        "InstallLocation" = $Destino
        "UninstallString" = $desinstalar
        "QuietUninstallString" = "$desinstalar -Silencioso"
        "InstallDate"     = (Get-Date -Format "yyyyMMdd")
    }
    foreach ($k in $props.Keys) {
        New-ItemProperty -Path $ChaveDesinstalar -Name $k -Value $props[$k] -PropertyType String -Force | Out-Null
    }
    New-ItemProperty -Path $ChaveDesinstalar -Name "NoModify" -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $ChaveDesinstalar -Name "NoRepair" -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $ChaveDesinstalar -Name "EstimatedSize" -Value $tamanhoKb -PropertyType DWord -Force | Out-Null
    Ok "Registrado em Configurações > Aplicativos (para desinstalar)"

    Write-Host ""
    Write-Host "==============================================" -ForegroundColor DarkGreen
    Write-Host "   Pronto! O $Nome foi instalado."               -ForegroundColor Green
    Write-Host "   Use o ícone '$Nome' na Área de Trabalho."     -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor DarkGreen
    Write-Host ""
    $r = Read-Host "Abrir o contador agora? (S/N)"
    if ($r -match "^[sSyY]") {
        Start-Process -FilePath $pyw -ArgumentList $argumentos -WorkingDirectory $Destino
    }
}
catch {
    Write-Host ""
    Erro "A instalação não terminou: $($_.Exception.Message)"
    Write-Host "    Se precisar de ajuda, mande um print desta janela."
    exit 1
}
