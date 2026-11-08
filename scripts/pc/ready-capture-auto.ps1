param([int]$Seconds = 150, [string]$Out = 'C:\Users\Patrick\tools\serial-ready.bin')
# Delayed-UFS capture: READY once, never PROBE. The ACM port number is autodetected
# (1d6b:0104 gadget; it has been COM4 and COM7), falling back to COM4.
$ErrorActionPreference = 'Stop'
function Get-DiagPort {
  try {
    $p = @(Get-CimInstance Win32_PnPEntity -Filter "PNPClass='Ports'" |
           Where-Object { $_.PNPDeviceID -like 'USB\VID_1D6B&PID_0104*' -and $_.Name -match '\((COM\d+)\)' })
    if ($p -and $p[0].Name -match '\((COM\d+)\)') { return $Matches[1] }
  } catch { }
  return 'COM4'
}
Start-Sleep -Milliseconds 500
$fs = [System.IO.File]::Open($Out, 'Append', 'Write', 'Read')
$buf = New-Object byte[] 65536
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$sent = $false; $last = ''
while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
  try {
    $com = Get-DiagPort
    if ($com -ne $last) { Write-Output ("port {0} at {1:N1}s" -f $com, $sw.Elapsed.TotalSeconds); $last = $com }
    $p = New-Object System.IO.Ports.SerialPort $com, 115200
    $p.ReadTimeout = 250; $p.DtrEnable = $true; $p.RtsEnable = $false
    $p.Open()
    if (-not $sent) {
      $b = [System.Text.Encoding]::ASCII.GetBytes("CROWNLTE-UFS-READY-1`n")
      $p.BaseStream.Write($b, 0, $b.Length); $p.BaseStream.Flush(); $sent = $true
      Write-Output ("READY sent at {0:N1}s" -f $sw.Elapsed.TotalSeconds)
    }
    while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
      try { $n = $p.BaseStream.Read($buf, 0, $buf.Length); if ($n -gt 0) { $fs.Write($buf, 0, $n); $fs.Flush() } }
      catch [System.TimeoutException] { }
    }
    $p.Close()
  } catch {
    Write-Output ("port: " + $_.Exception.Message)
    Start-Sleep -Milliseconds 300
  }
}
$fs.Close()
Write-Output ("done bytes=" + (Get-Item $Out).Length)
