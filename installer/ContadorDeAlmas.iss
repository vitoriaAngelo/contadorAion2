#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "AionTrack"
#define MyAppPublisher "@misttylol"
#define MyAppURL "https://github.com/vitoriaAngelo/contadorAion2"

[Setup]
AppId={{8E6C0EE9-090F-4CA9-9247-6C8542603B4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases/latest
DefaultDirName={localappdata}\AionTrack
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
Uninstallable=no
OutputDir=..\release
OutputBaseFilename=AionTrack-Setup
SetupIconFile=..\icone.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
CloseApplications=yes
SetupLogging=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\contador_almas.py"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\pet_protocol.py"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\pet_nomes_npcap.json"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\boss_nomes.json"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\material_icons.png"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\class_icons.zip"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\radar_mapa_abyss.jpg"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\radar_mapa_altgard.jpg"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\radar_mapa_verteron.jpg"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\radar_mascotes.json"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\radar_pontos.json"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\aviso_12.wav"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\aviso_50.wav"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\icone.ico"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\instalar.ps1"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\desinstalar.ps1"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\requirements.txt"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\version.txt"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\README.md"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\LICENSE"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\LICENSE-PET-PROTOCOL.txt"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\LICENSE-BOSS-CATALOG.txt"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall
Source: "..\LICENSE-RADAR.txt"; DestDir: "{tmp}\ContadorDeAlmas"; Flags: deleteafterinstall

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{tmp}\ContadorDeAlmas\instalar.ps1"" -Silencioso -AbrirAoFinal"; StatusMsg: "Instalando o AionTrack e os componentes necessários..."; Flags: waituntilterminated runhidden
