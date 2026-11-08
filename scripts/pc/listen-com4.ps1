param([int]$Seconds = 20, [string]$Out)
$fs = [System.IO.File]::Open($Out, 'Append', 'Write', 'Read')
$buf = New-Object byte[] 65536
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
  $p = New-Object System.IO.Ports.SerialPort 'COM4', 115200
  $p.ReadTimeout = 250; $p.DtrEnable = $true; $p.RtsEnable = $false
  $p.Open()
  while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
    try { $n = $p.BaseStream.Read($buf, 0, $buf.Length); if ($n -gt 0) { $fs.Write($buf, 0, $n); $fs.Flush() } } catch [System.TimeoutException] { }
  }
  $p.Close()
} catch { Write-Output ("port: " + $_.Exception.Message) }
$fs.Close()
Write-Output ("bytes=" + (Get-Item $Out).Length)
