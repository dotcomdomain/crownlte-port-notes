param([int]$Seconds = 330, [string]$Out = 'C:\Users\Patrick\tools\trial-romram-20261104\serial-romram.bin', [string]$Port = '')
# ROM-in-RAM trial capture: READY once, then PROBE + ROMRAM-START once the
# "probe remains held" line has arrived on the host. Never sends anything else.
# Port: -Port COMn forces one; otherwise the present 1d6b:0104 ACM port is used
# (single-function gadget = COM4 here; ADB=1 composite = its MI_00 child, which
# Windows numbers separately), falling back to COM4 if none is found.
$ErrorActionPreference = 'Stop'
$lastPort = ''
function Get-DiagPort {
  if ($Port) { return $Port }
  try {
    $p = @(Get-CimInstance Win32_PnPEntity -Filter "PNPClass='Ports'" |
           Where-Object { $_.PNPDeviceID -like 'USB\VID_1D6B&PID_0104*' -and $_.Name -match '\((COM\d+)\)' } |
           Sort-Object { if ($_.PNPDeviceID -like '*&MI_00\*') { 0 } else { 1 } })
    if ($p -and $p[0].Name -match '\((COM\d+)\)') { return $Matches[1] }
  } catch { }
  return 'COM4'
}
$fs = [System.IO.File]::Open($Out, 'Append', 'Write', 'Read')
$buf = New-Object byte[] 65536
$seen = New-Object System.Text.StringBuilder
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$readySent = $false; $probeSent = $false; $doneSent = $false; $doneAt = 0
# Early end: when <trial dir>\done.flag appears (after START), send the DONE frame
# once; PID1 builds with ROM_DONE restart at once (older builds ignore it and the
# deadline restarts as before). Capture stops 25 s after DONE.
$doneFlag = Join-Path (Split-Path -Parent $Out) 'done.flag'
if (Test-Path $doneFlag) { Remove-Item $doneFlag -Force }
function Send($p, [string]$s) { $b = [System.Text.Encoding]::ASCII.GetBytes($s); $p.BaseStream.Write($b, 0, $b.Length); $p.BaseStream.Flush() }
function Stop-Now { return ($doneSent -and ($sw.Elapsed.TotalSeconds -gt $doneAt + 25)) }
while ($sw.Elapsed.TotalSeconds -lt $Seconds -and -not (Stop-Now)) {
  try {
    $com = Get-DiagPort
    if ($com -ne $lastPort) { Write-Output ("port {0} at {1:N1}s" -f $com, $sw.Elapsed.TotalSeconds); $lastPort = $com }
    $p = New-Object System.IO.Ports.SerialPort $com, 115200
    $p.ReadTimeout = 250; $p.DtrEnable = $true; $p.RtsEnable = $false
    $p.Open()
    if (-not $readySent) { Send $p "CROWNLTE-UFS-READY-1`n"; $readySent = $true; Write-Output ("READY sent at {0:N1}s" -f $sw.Elapsed.TotalSeconds) }
    while ($sw.Elapsed.TotalSeconds -lt $Seconds -and -not (Stop-Now)) {
      try {
        if ($probeSent -and -not $doneSent -and (Test-Path $doneFlag)) {
          Send $p "CROWNLTE-ROMRAM-DONE-1`n"; $doneSent = $true; $doneAt = $sw.Elapsed.TotalSeconds
          Write-Output ("DONE sent at {0:N1}s" -f $doneAt)
        }
        $n = $p.BaseStream.Read($buf, 0, $buf.Length)
        if ($n -gt 0) {
          $fs.Write($buf, 0, $n); $fs.Flush()
          if (-not $probeSent) {
            [void]$seen.Append([System.Text.Encoding]::ASCII.GetString($buf, 0, $n))
            if ($seen.ToString().Contains('probe remains held')) {
              Send $p "CROWNLTE-UFS-PROBE-1`n"
              Start-Sleep -Milliseconds 200
              Send $p "CROWNLTE-ROMRAM-START-1`n"
              $probeSent = $true
              Write-Output ("PROBE + ROMRAM-START sent at {0:N1}s" -f $sw.Elapsed.TotalSeconds)
            } elseif ($seen.Length -gt 200000) { [void]$seen.Remove(0, 100000) }
          }
        }
      } catch [System.TimeoutException] { }
    }
    $p.Close()
  } catch {
    Write-Output ("port: " + $_.Exception.Message)
    Start-Sleep -Milliseconds 300
  }
}
$fs.Close()
Write-Output ("done bytes=" + (Get-Item $Out).Length + " probeSent=" + $probeSent)
