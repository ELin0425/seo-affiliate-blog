$log = "$PSScriptRoot\pins_run_log.txt"
"$(Get-Date): Pin pipeline starting" | Out-File -Append -FilePath $log

Set-Location $PSScriptRoot
$python = "C:\Users\linse\AppData\Local\Python\pythoncore-3.14-64\python.exe"

& $python pins.py 2>&1 | Tee-Object -Append -FilePath $log
& $python post_pins.py 2>&1 | Tee-Object -Append -FilePath $log

"$(Get-Date): Pin pipeline finished (exit code: $LASTEXITCODE)" | Out-File -Append -FilePath $log
