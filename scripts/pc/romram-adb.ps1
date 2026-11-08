param(
  [int]$TimeoutSec = 540,
  [string]$Adb = 'C:\Users\Patrick\tools\platform-tools\adb.exe',
  [string]$HostMac = '02-43-52-4E-39-48',   # NCM host_addr set by the ROM-RAM PID1 (ADB=1 builds)
  [int]$Port = 5555,
  [switch]$Root,          # also run `adb root` (works only with an ADB_ROOT=1 build)
  [switch]$FindOnly       # only locate the adapter / probe TCP; never run adb
)
# ROM-in-RAM ADB=1 helper. Finds the phone's USB NCM adapter (in-box UsbNcm driver),
# probes adbd at [fe80::1%<ifIndex>]:5555 (IPv6 link-local, available at once) and
# 169.254.42.1:5555 (needs Windows' APIPA address, ~1 min), then `adb connect`s the
# first one that answers and runs read-only checks with an explicit -s serial.
# Needs no admin rights. Never opens a COM port; romram-capture.ps1 keeps the
# serial frames. Does not change any Windows network setting.
$ErrorActionPreference = 'Stop'
$sw = [System.Diagnostics.Stopwatch]::StartNew()
function Log([string]$s) { Write-Output ("[{0,6:N1}s] {1}" -f $sw.Elapsed.TotalSeconds, $s) }

function Find-Adapter {
  $a = @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object { $_.MacAddress -eq $HostMac })
  if (-not $a) {
    $a = @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -like '*UsbNcm*' })
  }
  if ($a.Count -gt 1) { Log ("WARN {0} candidate adapters; using ifIndex {1}" -f $a.Count, $a[0].ifIndex) }
  if ($a) { return $a[0] } else { return $null }
}

function Test-Tcp([string]$ip, [int]$port) {
  $c = $null
  try {
    $addr = [System.Net.IPAddress]::Parse($ip)
    $c = New-Object System.Net.Sockets.TcpClient($addr.AddressFamily)
    $t = $c.ConnectAsync($addr, $port)
    return ($t.Wait(2000) -and $c.Connected)
  } catch { return $false } finally { if ($c) { $c.Close() } }
}

$lastState = ''; $target = $null
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec -and -not $target) {
  $ad = Find-Adapter
  if (-not $ad) { $state = 'no NCM adapter yet (phone not in an ADB=1 ROM-RAM boot, or still enumerating)' }
  else {
    $v4 = @(Get-NetIPAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object IPAddress) -join ','
    $state = "adapter ifIndex=$($ad.ifIndex) '$($ad.InterfaceDescription)' mac=$($ad.MacAddress) status=$($ad.Status) ipv4=$v4"
    if ($ad.Status -eq 'Up') {
      $cands = @("fe80::1%$($ad.ifIndex)")
      if ($v4 -like '169.254.*') { $cands += '169.254.42.1' }
      foreach ($ip in $cands) {
        if (Test-Tcp $ip $Port) { $target = $ip; break }
      }
      if (-not $target) { $state += " (tcp $Port not answering yet: adbd starts ~1-2 min after Lineage init)" }
    }
  }
  if ($state -ne $lastState) { Log $state; $lastState = $state }
  if (-not $target) { Start-Sleep -Seconds 3 }
}
if (-not $target) { Log "FAIL no adbd reachable within ${TimeoutSec}s"; exit 1 }
$serial = if ($target.Contains(':')) { "[$target]:$Port" } else { "${target}:$Port" }
Log "PASS tcp $serial answers"
if ($FindOnly) { exit 0 }

# PS 5.1: native stderr under 'Stop' would throw NativeCommandError; collect it instead.
function A([string[]]$a) { $ErrorActionPreference = 'Continue'; $o = & $Adb @a 2>&1 | Out-String; return $o.Trim() }
$o = A @('connect', $serial); Log "adb connect: $o"
for ($i = 0; $i -lt 10; $i++) {
  $st = A @('-s', $serial, 'get-state')
  if ($st -eq 'device') { break }
  Log "state=$st (unauthorized means adb_keys did not take: check P; 'ADB PASS key installed')"; Start-Sleep -Seconds 2
}
if ($st -ne 'device') { Log "FAIL adb state '$st'"; exit 2 }
Log "PASS adb device $serial"
Log ("id: " + (A @('-s', $serial, 'shell', 'id')))
Log ("kernel: " + (A @('-s', $serial, 'shell', 'uname -a')))
Log ("props: " + (A @('-s', $serial, 'shell', 'getprop ro.serialno; getprop ro.boot.crownlte_adb; getprop sys.boot_completed; getprop ro.debuggable')) -replace "`r?`n", ' ')
if ($Root) {
  Log ("adb root: " + (A @('-s', $serial, 'root')))
  Start-Sleep -Seconds 4
  Log ("reconnect: " + (A @('connect', $serial)))
  Start-Sleep -Seconds 2
  Log ("id after root: " + (A @('-s', $serial, 'shell', 'id')))
}
Log "use: & '$Adb' -s '$serial' shell"
