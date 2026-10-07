# Voice Dictation for Windows: per-user install, no administrator rights needed.
# Safe to re-run: it updates the program and keeps your settings, choices and downloaded models.
#
#   Double-click install.cmd, or:  powershell -ExecutionPolicy Bypass -File install.ps1
#   $env:DICTATE_NO_AUTOSTART = "1"   the same, without start-at-login (e.g. for testing)
#
# It asks a few questions (language, speech model, key, large text), each with a suggestion for this
# computer that Enter accepts. These answer them without asking: DICTATE_LANGUAGE=en|sk|auto,
# DICTATE_MODEL=tiny|base|small|medium|large-v3-turbo, DICTATE_KEY=KP_Delete|Control_R|"Ctrl+Alt+D",
# DICTATE_LARGE_UI=off|panel|both, DICTATE_KEEP=keep.
#
# Everything goes to %LOCALAPPDATA%\dictate (program, private Python, models, settings, log), plus
# Start menu shortcuts "Dictate" and "Dictate Settings" and one in the Startup folder.
# (This file is ASCII on purpose: Windows PowerShell 5 reads files without a BOM as the ANSI code page.)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is many times slower with its progress bar
Set-StrictMode -Version 3

$Src = $PSScriptRoot
$D = Join-Path $env:LOCALAPPDATA "dictate"
$UvVersion = "0.12.21"
$UvZip = "uv-x86_64-pc-windows-msvc.zip"
# AMD GPUs: CTranslate2's ROCm build for Windows (update the checksum together with ctranslate2 in
# requirements.txt) and AMD's ROCm 7 runtime wheels.
$Ct2RocmZipSha256 = "43da4baa5feaee49f77e176277a9647f99c493173c81a0bc60f491cac97532c2"
$RocmVersion = "7.14.1"
$RocmWheels = "https://repo.amd.com/rocm/whl-multi-arch"
$Python = "3.14"

function Say($Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Invoke-Quiet([scriptblock]$Command) {
    # Windows PowerShell 5 turns a program's redirected error output into errors that stop this script.
    $Saved = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Command *> $null } finally { $ErrorActionPreference = $Saved }
    return $LASTEXITCODE
}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "Sorry, only 64-bit Intel/AMD Windows is supported (this is $env:PROCESSOR_ARCHITECTURE)."
}

# Keep every cache and download inside $D.
$env:UV_CACHE_DIR = "$D\cache\uv"; $env:UV_PYTHON_INSTALL_DIR = "$D\python"
$env:UV_PYTHON_CACHE_DIR = "$D\cache\uv-python"; $env:UV_PYTHON_BIN_DIR = "$D\python\bin"
$env:UV_PYTHON_INSTALL_BIN = "0"; $env:UV_NO_CONFIG = "1"; $env:UV_MANAGED_PYTHON = "1"
$env:HF_HOME = "$D\cache\hf"; $env:HF_HUB_DISABLE_TELEMETRY = "1"; $env:PYTHONUTF8 = "1"

function Get-AmdTarget([string]$Name) {
    # The GPU code in CTranslate2's ROCm build: gfx1030, gfx1100-1102, gfx1150/1151, gfx1200/1201.
    # Windows has no HSA_OVERRIDE_GFX_VERSION, so other AMD GPUs (RX 6700/6600/6500, Radeon 780M) use the CPU.
    switch -Regex ($Name) {
        'RX 9070|AI PRO R9700' { return "gfx1201" }
        'RX 9060' { return "gfx1200" }
        'RX 7900' { return "gfx1100" }
        'PRO W7900|PRO W7800' { return "gfx1100" }
        'RX 7800|RX 7700(?!S)|PRO W7700' { return "gfx1101" }
        'RX 7600|RX 7650|RX 7700S|PRO W7600|PRO W7500' { return "gfx1102" }
        'RX 6950|RX 6900|RX 6800(?! ?[MS])|PRO W6800' { return "gfx1030" }
        'Radeon\(TM\) 8060S|Radeon 8060S|8050S|8040S' { return "gfx1151" }
        'Radeon\(TM\) 890M|Radeon 890M|Radeon\(TM\) 880M|Radeon 880M' { return "gfx1150" }
    }
    return $null
}

$Gpu = "none"; $AmdTarget = $null
$Cards = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
if ((Get-Command nvidia-smi -ErrorAction SilentlyContinue) -and (Invoke-Quiet { nvidia-smi -L }) -eq 0) {
    $Gpu = "nvidia"
}
if ($Gpu -eq "none") {
    foreach ($Card in $Cards) {
        $Target = Get-AmdTarget $Card
        if ($Target) { $Gpu = "amd"; $AmdTarget = $Target; break }
        if ($Card -match "AMD|Radeon") {
            Write-Host "Note: $Card has no code in CTranslate2's Windows GPU build, so dictation runs on the processor."
        }
    }
}
Write-Host "Graphics: $($Cards -join ', ')  ->  $(if ($Gpu -eq 'none') { 'the processor (CPU)' } else { "$Gpu GPU" })"

Say "Stopping a running copy (Windows keeps its files in use)"
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like "*dictate.py*" -or $_.CommandLine -like "*bigui.py*") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

Say "Program files"
New-Item -ItemType Directory -Force -Path "$D\cache", "$D\models", "$D\bin" | Out-Null
Copy-Item -Force -Path "$Src\*.py", "$Src\requirements.txt", "$Src\requirements-cuda.txt", "$Src\config.example.toml",
    "$Src\README.md" -Destination $D

$Uv = "$D\bin\uv.exe"
if (-not (Test-Path $Uv)) {
    Say "Downloading uv $UvVersion (Python package manager, kept inside $D)"
    $Url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$UvZip"
    $Zip = "$D\cache\$UvZip"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Zip
    Invoke-WebRequest -UseBasicParsing -Uri "$Url.sha256" -OutFile "$Zip.sha256"
    $Expected = ((Get-Content -Raw "$Zip.sha256").Trim() -split "\s+")[0]
    if ((Get-FileHash -Algorithm SHA256 $Zip).Hash -ne $Expected.ToUpper()) { throw "uv download: checksum mismatch" }
    Expand-Archive -Force -Path $Zip -DestinationPath "$D\cache\uv-zip"
    Copy-Item -Force "$D\cache\uv-zip\uv.exe" $Uv
    Remove-Item -Recurse -Force $Zip, "$Zip.sha256", "$D\cache\uv-zip"
}

$Py = "$D\venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Say "Private Python $Python (independent of any other Python on this PC)"
    & $Uv python install $Python
    if ($LASTEXITCODE -ne 0) { throw "uv python install failed" }
    & $Uv venv --python $Python "$D\venv"
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
}

Say "Python packages"
$Packages = @("-r", "$D\requirements.txt")
if ($Gpu -eq "nvidia") {
    $Packages += @("-r", "$D\requirements-cuda.txt")
} elseif ($Gpu -eq "amd") {
    $Ct2 = (Select-String -Path "$D\requirements.txt" -Pattern '^ctranslate2==([0-9.]+)').Matches[0].Groups[1].Value
    $Tag = & $Py -c "import sys; print('cp%d%d' % sys.version_info[:2])"
    $Wheel = Get-ChildItem "$D\cache\rocm\ctranslate2-$Ct2-$Tag-$Tag-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $Wheel) {
        Write-Host "    CTranslate2 $Ct2 for ROCm (downloads about 140 MB)"
        New-Item -ItemType Directory -Force -Path "$D\cache\rocm" | Out-Null
        $Zip = "$D\cache\rocm\ctranslate2-$Ct2-rocm.zip"
        Invoke-WebRequest -UseBasicParsing -OutFile $Zip `
            -Uri "https://github.com/OpenNMT/CTranslate2/releases/download/v$Ct2/rocm-python-wheels-Windows.zip"
        if ((Get-FileHash -Algorithm SHA256 $Zip).Hash -ne $Ct2RocmZipSha256.ToUpper()) { throw "CTranslate2 ROCm zip: checksum mismatch" }
        Expand-Archive -Force -Path $Zip -DestinationPath "$D\cache\rocm\zip"
        $Wheel = Get-ChildItem -Recurse "$D\cache\rocm\zip\ctranslate2-$Ct2-$Tag-$Tag-win_amd64.whl" | Select-Object -First 1
        Move-Item -Force $Wheel.FullName "$D\cache\rocm\"
        $Wheel = Get-Item "$D\cache\rocm\$($Wheel.Name)"
        Remove-Item -Recurse -Force $Zip, "$D\cache\rocm\zip"
    }
    Write-Host "    ROCm $RocmVersion runtime with GPU code for $AmdTarget (downloads about 1 GB the first time)"
    $Packages += @($Wheel.FullName)
    foreach ($Name in @("rocm-sdk-core", "rocm-sdk-libraries", "rocm-sdk-device-$AmdTarget")) {
        $File = ($Name -replace "-", "_") + "-$RocmVersion-py3-none-win_amd64.whl"
        $Packages += @("$Name @ $RocmWheels/$File")
    }
}
& $Uv pip install --only-binary av --python $Py @Packages
if ($LASTEXITCODE -ne 0) { throw "installing the Python packages failed" }

# CTranslate2 needs the Visual C++ runtime (msvcp140.dll), which a fresh Windows may lack: onnxruntime
# brings a copy, which CTranslate2 loads from its own folder. (Its GPU libraries are loaded first, as
# dictation does: CTranslate2's ROCm build can't find AMD's by itself.)
$Import = "import sys, pathlib; sys.path.insert(0, str(pathlib.Path(sys.prefix).parent)); " +
          "import windows; windows.preload_gpu_libraries(); import ctranslate2"
if ((Invoke-Quiet { & $Py -c $Import }) -ne 0) {
    $Site = "$D\venv\Lib\site-packages"
    Copy-Item -Force "$Site\onnxruntime\capi\msvcp140*.dll" "$Site\ctranslate2\" -ErrorAction SilentlyContinue
    & $Py -c $Import
    if ($LASTEXITCODE -ne 0) {
        throw ("CTranslate2 can't load. Install the Microsoft Visual C++ Redistributable " +
               "(https://aka.ms/vs/17/release/vc_redist.x64.exe), then run this installer again.")
    }
}

Say "Language, speech model and key (Enter takes the suggestion)"
& $Py -X utf8 "$D\dictate.py" --setup "--gpu=$Gpu"
if ($LASTEXITCODE -ne 0) { throw "setup failed" }
Invoke-Quiet { & $Uv cache clean } | Out-Null

Say "Start menu shortcuts"
$Icon = "$D\dictate.ico"
& $Py -c ("import sys, pathlib; sys.path.insert(0, str(pathlib.Path(sys.prefix).parent)); from tray_pystray import " +
          "draw_icon; draw_icon('ready', 'D', 256).save(sys.argv[1], sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])") $Icon
$Pyw = "$D\venv\Scripts\pythonw.exe"
$Shell = New-Object -ComObject WScript.Shell
function New-Shortcut([string]$Path, [string]$Arguments, [string]$Description) {
    $Link = $Shell.CreateShortcut($Path)
    $Link.TargetPath = $Pyw
    $Link.Arguments = $Arguments
    $Link.WorkingDirectory = $D
    $Link.Description = $Description
    if (Test-Path $Icon) { $Link.IconLocation = $Icon }
    $Link.Save()
}
$Programs = [Environment]::GetFolderPath("Programs")
New-Shortcut "$Programs\Dictate.lnk" "-X utf8 `"$D\dictate.py`"" "Start push-to-talk dictation"
New-Shortcut "$Programs\Dictate Settings.lnk" "-X utf8 `"$D\bigui.py`" settings" "Dictation settings in large text"
# A copy of the Start menu's Dictate shortcut in the Startup folder, as last switched in the menu (on at first)
Say "Start at login: a shortcut in the Startup folder"
$Login = if ($env:DICTATE_NO_AUTOSTART -eq "1") { "off" } else { "saved" }
& $Py -X utf8 "$D\dictate.py" "--start-at-login=$Login"

Say "Starting dictation"
Start-Process -FilePath $Pyw -ArgumentList @("-X", "utf8", "`"$D\dictate.py`"") -WorkingDirectory $D

Say "Checking the installation"
& $Py -X utf8 "$D\dictate.py" --check
Write-Host ""
Write-Host "Done. Hold the dictation key, speak, release: the text is typed where the cursor is."
Write-Host "The microphone icon in the taskbar's notification area has the menu (you may need to drag it out of the ^ overflow)."
