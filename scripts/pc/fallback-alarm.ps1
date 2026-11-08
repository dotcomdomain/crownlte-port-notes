# One-shot fallback alarm (2026-11-06 08:00): only if the Note9 is NOT back on
# Lineage (adb state 'device'). The phone hung in a test kernel overnight; its
# own alarm cannot ring. Plays the Windows alarm sound until the phone returns
# or 20 minutes pass. Log: C:\Users\Patrick\tools\fallback-alarm.log
$adb = 'C:\Users\Patrick\tools\platform-tools\adb.exe'
$log = 'C:\Users\Patrick\tools\fallback-alarm.log'
function PhoneBack { try { (& $adb get-state 2>$null) -match 'device' } catch { $false } }
Add-Content $log ("{0} check" -f (Get-Date -Format s))
if (PhoneBack) { Add-Content $log 'phone is back on Lineage: no alarm'; exit 0 }
Add-Content $log 'phone NOT back: sounding fallback alarm'
$wsh = New-Object -ComObject WScript.Shell
for ($i = 0; $i -lt 50; $i++) { $wsh.SendKeys([char]175) }   # volume up
$player = New-Object System.Media.SoundPlayer 'C:\Windows\Media\Alarm01.wav'
$sw = [Diagnostics.Stopwatch]::StartNew()
$player.PlayLooping()
while ($sw.Elapsed.TotalMinutes -lt 20) {
  Start-Sleep -Seconds 15
  if (PhoneBack) { break }
}
$player.Stop()
Add-Content $log ("{0} alarm stopped (phone back: {1})" -f (Get-Date -Format s), (PhoneBack))
