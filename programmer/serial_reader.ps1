$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$port = New-Object System.IO.Ports.SerialPort $env:UTSM_PICO_COM,115200,None,8,One
$port.DtrEnable = $true
$buffer = ''
try {
    $port.Open()
    [Console]::WriteLine('__MONITOR_READY__')
    while ($true) {
        $buffer += $port.ReadExisting()
        while ($buffer.Contains("`n")) {
            $end = $buffer.IndexOf("`n")
            [Console]::WriteLine($buffer.Substring(0, $end).TrimEnd("`r"))
            $buffer = $buffer.Substring($end + 1)
        }
        if ($buffer.Length -gt 8192) { [Console]::WriteLine($buffer); $buffer = '' }
        [Console]::Out.Flush()
        Start-Sleep -Milliseconds 30
    }
} catch {
    [Console]::WriteLine('__MONITOR_ERROR__' + $_.Exception.Message)
    exit 1
} finally {
    if ($port.IsOpen) { $port.Close() }
    $port.Dispose()
}
